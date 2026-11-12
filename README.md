# 冷链配送施工封闭核对 API

出车前核对计划路线是否与公路施工封闭冲突。给定公告列表页 URL 和计划路段
（编号、方向、带时区的驶入/驶出时间），服务会：

1. 抓取公告列表页并自动跟随**同站“下一页”分页**；
2. 逐页抓取公告详情（BeautifulSoup 解析），提取**路段、方向、生效日期、
   每周封闭日、起止钟点**；
3. 按**路段编号 + 方向**匹配，判断路段通行区间与封闭区间是否相交；
4. 返回受阻路段、相交的封闭时段与公告链接；
5. 任何页面抓取失败时标记 `undetermined`（无法判定）并给出失败地址，
   **绝不会在信息不完整时报“全程畅通”**。

此外提供 **`POST /reconcile` 双源对账 API**：传入交管部门与承运商两份公告
目录 URL 和**最多七天的北京时间日期范围**，分别完整采集两个来源后，按
路段与方向找出仅一侧刊载的封闭时段（含实际覆盖该时段的公告链接），避免
排车时漏看封闭。

## 时间规则（需求约定）

- 公告中的所有时间一律按**北京时间（UTC+8）**解释；请求中的时间可带任意
  时区偏移（如 `Z`、`+09:00`），服务内部统一换算为北京时间。
- **跨午夜封闭归属开始当天**：如 `22:00 至 次日 05:00` 的封闭日按 22:00
  所在日期计算，结束时刻落在次日。
- **生效日期限定封闭开始日**：只有封闭区间的开始日落入生效日期区间内，
  该封闭才成立；跨午夜窗口的尾部即使延伸到生效期外也不影响归属。
- 通行区间与封闭区间均按半开区间 `[start, end)` 判断相交，恰好首尾相接
  （如 05:00 驶入、封闭 05:00 结束）不算受阻。
- `00:00 至 24:00` 视为当天全天封闭（跨午夜到次日 00:00）。

## 目录结构

```
.
├── app/                  # FastAPI 采集与核对服务
│   ├── main.py           # 路由：GET /health、POST /check、POST /reconcile
│   ├── models.py         # 请求/响应模型（时间必须带时区，日期范围 ≤7 天）
│   ├── parser.py         # BeautifulSoup 列表分页与详情解析
│   ├── scraper.py        # httpx 异步抓取、重试、失败记录
│   ├── scheduler.py      # 路段匹配与区间相交判定（/check）
│   ├── reconciler.py     # 双源对账：展开、来源内合并、求两侧差异（/reconcile）
│   ├── intervals.py      # 半开区间合并（重叠/相邻）与区间相减
│   └── timeutils.py      # 北京时间、跨午夜、生效期、星期解析
├── sample-site/          # 离线样本站（Flask，同构分页+详情，含失效链接）
├── tests/
│   ├── smoke_test.py            # /check 端到端冒烟测试（仅标准库）
│   ├── test_reconcile.py        # 区间运算与对账规则单元测试
│   └── reconcile_smoke_test.py  # /reconcile 端到端冒烟测试（仅标准库）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 双源对账 `POST /reconcile`

### 请求

| 字段 | 含义 |
| --- | --- |
| `source_a_list_url` | 来源 A（如交管部门）公告目录列表页 URL |
| `source_b_list_url` | 来源 B（如承运商）公告目录列表页 URL（不能与 A 相同） |
| `start_date` / `end_date` | 北京时间日期范围（含首尾，**跨度最多 7 天**） |

两个来源**各自完整采集**（分页 + 全部详情逐条尝试，互不短路）。封闭时段
按原有封闭日、**跨午夜归属开始当天**、生效日期限定与**双向通行**规则展开，
仅取开始日落在日期范围内的窗口；每个来源内部先合并重叠/首尾相接的区间，
再按 `路段编号 + 方向` 做区间相减。

### 响应与判定规则

| 字段 | 含义 |
| --- | --- |
| `status` | `consistent` 两侧完整且时段完全一致；`different` 两侧完整但存在单侧封闭；`partial` 至少一侧采集不完整 |
| `confirmed_differences[]` | **可确认**的单侧封闭：对侧来源已完整采集，足以判定对侧漏载 |
| `pending_differences[]` | **待核实**的单侧封闭：对侧来源采集不完整，不能断言漏载 |
| `source_a` / `source_b` | 各来源的列表 URL、`complete`、成功解析公告数与失败地址 |
| 差异内 `announcements` | **实际覆盖该差异时段**的公告详情链接（逐段核对，去重） |

关键约束（需求约定）：

- **判定某一来源漏载前，必须先完整采集该来源**。例如 A 有某时段而 B 没有，
  只有 B 完整时才记为 `confirmed`（B 漏载）；B 采集不完整时同样的时段只能
  标 `pending`（待核实），并在 `source_b.failures` 列出失败地址。
- 任一侧采集不完整时，保留仍可确认的差异，**绝不报告两侧一致**
  （不会出现 `consistent`）。
- 公告方向为“双向”时，差异按“上行/下行”两个方向分别给出。

### 请求示例

```bash
curl -X POST http://localhost:8000/reconcile \
  -H 'Content-Type: application/json' \
  -d '{
    "source_a_list_url": "http://sample-site:8080/traffic/announcements?page=1",
    "source_b_list_url": "http://sample-site:8080/carrier/announcements?page=1",
    "start_date": "2026-11-16",
    "end_date": "2026-11-22"
  }'
```

### 离线样本目录

样本站在同一容器内提供三份同构目录，均带同站分页：

| 目录前缀 | 角色 | 特点 |
| --- | --- | --- |
| `/traffic` | 交管部门（来源 A） | 3 条公告，采集完整、无失效链接 |
| `/carrier` | 承运商（来源 B） | 4 条公告，与交管存在刊载差异，含 1 个失效详情链接（404） |
| `/carrier-full` | 承运商完整镜像 | 与 `/carrier` 同数据但无失效链接，用于验证两侧完整时的 `different` |
| `/announcements`（无前缀） | 旧版单目录 | 供 `/check` 冒烟测试，含 1 个失效链接 |

内置的刊载差异（2026-11-16 ~ 2026-11-22 窗口内）：

- `G15-K123 下行`：交管每周一、三夜间 `22:00-次日05:00`，承运商仅周一
  → 周三（11-18）夜间为交管独有；
- `G25-K88 双向`：交管周六、周日全天，承运商仅周日
  → 周六（11-21）全天为交管独有，按上行/下行各一条；
- `G60-K45 上行`：仅承运商刊载周五（11-20）`08:00-12:00`；
- `G15-K123 上行`：两侧完全一致，对账结果中不出现。

## 用 Docker Compose 启动（离线可用）

镜像构建仅需在有网络时拉取基础镜像与依赖；运行后两个容器之间走内部
Docker 网络，调用样本站不需要外网。

```bash
docker compose up --build
```

- API 文档：<http://localhost:8000/docs>
- 样本站列表：<http://localhost:8080/announcements?page=1>

## 请求示例

```bash
curl -X POST http://localhost:8000/check \
  -H 'Content-Type: application/json' \
  -d '{
    "announcement_list_url": "http://sample-site:8080/announcements?page=1",
    "segments": [
      {
        "road_id": "G15-K123",
        "direction": "下行",
        "enter_at": "2026-11-16T21:30:00+08:00",
        "exit_at":  "2026-11-17T02:00:00+08:00"
      },
      {
        "road_id": "G25-K88",
        "direction": "上行",
        "enter_at": "2026-11-14T06:00:00+08:00",
        "exit_at":  "2026-11-14T09:00:00+08:00"
      }
    ]
  }'
```

> 容器内访问样本站用 `http://sample-site:8080`；从宿主机访问用
> `http://localhost:8080`（此时把该 URL 作为 `announcement_list_url` 即可）。

## 响应说明

| 字段 | 含义 |
| --- | --- |
| `status` | `clear` 全部采集成功且无相交；`blocked` 存在受阻路段；`undetermined` 有采集失败，无法给出完整判定 |
| `blocked[]` | 受阻路段、相交封闭时段（北京时间）、命中的公告详情链接 |
| `clear[]` | 未受阻路段；**仅当零采集失败时才会出现** |
| `failures[]` | 采集失败地址、阶段（`list`/`detail`）与原因（如 `HTTP 404`、超时） |
| `fetched_announcements` | 成功解析的公告条数 |

样本站故意包含一个已撤销公告的失效链接，因此对样本站调用时 `status`
会是 `undetermined`，同时 `failures` 中给出 404 地址——用于演示失败处理。

## 本地开发（不用 Docker）

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r sample-site/requirements.txt
python sample-site/app.py &
uvicorn app.main:app --port 8000
python tests/smoke_test.py             # /check 端到端
python tests/reconcile_smoke_test.py   # /reconcile 端到端
python -m unittest tests.test_reconcile -v   # 对账规则单元测试
```

## 解析约定

- 列表页：提取同站（同 scheme + 同 host）的公告详情链接，并按“下一页/
  next/›”等文本识别分页链接，最多跟随 50 页并按 URL 去重防环。
- 详情页：从含“路段编号/方向/生效日期/封闭日/封闭时间”关键词的行中用
  正则提取字段；字段缺失或无法解析时该详情页按采集失败处理。
- 方向：公告为“双向”时匹配任一通行方向；否则需精确一致（忽略空白）。
- 请求超时 10 秒（连接 5 秒），每个地址失败后重试 1 次。
