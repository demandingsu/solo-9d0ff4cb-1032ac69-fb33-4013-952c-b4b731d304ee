"""离线样本站：模拟公路施工封闭公告的同站分页列表与详情页。

数据全部内置，无外部依赖，供采集 API 离线联调。
页面结构与真实公告站点同构：
- <prefix>/announcements?page=N  列表页（含“下一页”同站分页）
- <prefix>/announcement/<id>     详情页（路段/方向/生效日期/封闭日/起止钟点）

内置三份目录：
- /traffic  交管部门公告目录（采集完整，作为双源对账的来源 A）
- /carrier  承运商公告目录（含一个失效链接，且与交管目录存在刊载差异，来源 B）
- /announcements（无前缀）旧版单目录，保留给 /check 冒烟测试，含一个失效链接
"""
from __future__ import annotations

from datetime import date

from flask import Flask, abort, render_template_string, request

app = Flask(__name__)

LIST_TEMPLATE = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>施工封闭公告列表</title></head>
<body>
<h1>{{ source_name }}施工封闭公告</h1>
<table class="announcement-table">
  <thead><tr><th>公告标题</th><th>发布日期</th></tr></thead>
  <tbody>
  {% for item in items %}
    <tr>
      <td><a href="{{ item.href }}">{{ item.title }}</a></td>
      <td>{{ item.published }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
<div class="pagination">
  {% if page > 1 %}<a rel="prev" href="{{ prefix }}/announcements?page={{ page - 1 }}">上一页</a>{% endif %}
  {% if has_next %}<a rel="next" href="{{ prefix }}/announcements?page={{ page + 1 }}">下一页</a>{% endif %}
</div>
</body></html>
"""

DETAIL_TEMPLATE = """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{{ ann.title }}</title></head>
<body>
<article class="content">
  <h1>{{ ann.title }}</h1>
  <p>路段编号：{{ ann.road_id }}</p>
  <p>方向：{{ ann.direction }}</p>
  <p>生效日期：{{ ann.effective_text }}</p>
  <p>封闭日：{{ ann.weekday_text }}</p>
  <p>封闭时间：{{ ann.clock_text }}</p>
  <p>管制措施：施工期间该路段禁止车辆通行，请提前绕行。</p>
</article>
</body></html>
"""

# ---- 交管部门目录（来源 A，完整无失效链接） -------------------------------
TRAFFIC_ANNOUNCEMENTS = [
    {
        "id": "gg-traffic-001",
        "title": "G15沈海高速K123段下行夜间施工封闭公告",
        "published": "2026-11-01",
        "road_id": "G15-K123",
        "direction": "下行",
        "effective_start": date(2026, 11, 1),
        "effective_end": date(2026, 11, 30),
        "effective_text": "2026年11月1日至2026年11月30日",
        "weekday_text": "每周一、周三",
        "clock_text": "22:00 至 次日 05:00（跨午夜，归属当日封闭）",
    },
    {
        "id": "gg-traffic-002",
        "title": "G15沈海高速K123段上行白天施工封闭公告",
        "published": "2026-11-02",
        "road_id": "G15-K123",
        "direction": "上行",
        "effective_start": date(2026, 11, 5),
        "effective_end": date(2026, 11, 25),
        "effective_text": "2026年11月5日至2026年11月25日",
        "weekday_text": "每周二、周四",
        "clock_text": "09:00 至 16:30",
    },
    {
        "id": "gg-traffic-003",
        "title": "G25长深高速K88段双向周末施工封闭公告",
        "published": "2026-11-03",
        "road_id": "G25-K88",
        "direction": "双向",
        "effective_start": date(2026, 10, 15),
        "effective_end": None,
        "effective_text": "2026年10月15日起长期有效",
        "weekday_text": "每周六、周日",
        "clock_text": "00:00 至 24:00（全天封闭）",
    },
]

# ---- 承运商目录（来源 B，含失效链接与刊载差异） ---------------------------
CARRIER_ANNOUNCEMENTS = [
    {
        # 与交管 gg-traffic-001 相比漏载周三夜间（仅保留周一）
        "id": "gg-carrier-001",
        "title": "G15沈海高速K123段下行周一夜间施工封闭提示",
        "published": "2026-11-04",
        "road_id": "G15-K123",
        "direction": "下行",
        "effective_start": date(2026, 11, 1),
        "effective_end": date(2026, 11, 30),
        "effective_text": "2026年11月1日至2026年11月30日",
        "weekday_text": "每周一",
        "clock_text": "22:00 至 次日 05:00（跨午夜，归属当日封闭）",
    },
    {
        # 与交管 gg-traffic-002 完全一致
        "id": "gg-carrier-002",
        "title": "G15沈海高速K123段上行白天施工封闭提示",
        "published": "2026-11-04",
        "road_id": "G15-K123",
        "direction": "上行",
        "effective_start": date(2026, 11, 5),
        "effective_end": date(2026, 11, 25),
        "effective_text": "2026年11月5日至2026年11月25日",
        "weekday_text": "每周二、周四",
        "clock_text": "09:00 至 16:30",
    },
    {
        # 与交管 gg-traffic-003 相比漏载周六（仅保留周日）
        "id": "gg-carrier-003",
        "title": "G25长深高速K88段双向周日施工封闭提示",
        "published": "2026-11-05",
        "road_id": "G25-K88",
        "direction": "双向",
        "effective_start": date(2026, 10, 15),
        "effective_end": None,
        "effective_text": "2026年10月15日起长期有效",
        "weekday_text": "每周日",
        "clock_text": "00:00 至 24:00（全天封闭）",
    },
    {
        # 交管目录没有的承运商独有公告（B-only 差异）
        "id": "gg-carrier-004",
        "title": "G60沪昆高速K45段上行周五上午施工封闭提示",
        "published": "2026-11-06",
        "road_id": "G60-K45",
        "direction": "上行",
        "effective_start": date(2026, 11, 1),
        "effective_end": date(2026, 12, 31),
        "effective_text": "2026年11月1日至2026年12月31日",
        "weekday_text": "每周五",
        "clock_text": "08:00 至 12:00",
    },
]

# 旧版单目录数据（/check 冒烟测试使用）
LEGACY_ANNOUNCEMENTS = [
    {
        "id": "gg-2026-001",
        "title": "G15沈海高速K123段下行夜间施工封闭公告",
        "published": "2026-11-01",
        "road_id": "G15-K123",
        "direction": "下行",
        "effective_start": date(2026, 11, 1),
        "effective_end": date(2026, 11, 30),
        "effective_text": "2026年11月1日至2026年11月30日",
        "weekday_text": "每周一、周三",
        "clock_text": "22:00 至 次日 05:00（跨午夜，归属当日封闭）",
    },
    {
        "id": "gg-2026-002",
        "title": "G15沈海高速K123段上行白天施工封闭公告",
        "published": "2026-11-02",
        "road_id": "G15-K123",
        "direction": "上行",
        "effective_start": date(2026, 11, 5),
        "effective_end": date(2026, 11, 25),
        "effective_text": "2026年11月5日至2026年11月25日",
        "weekday_text": "每周二、周四",
        "clock_text": "09:00 至 16:30",
    },
    {
        "id": "gg-2026-003",
        "title": "G25长深高速K88段双向周末施工封闭公告",
        "published": "2026-11-03",
        "road_id": "G25-K88",
        "direction": "双向",
        "effective_start": date(2026, 10, 15),
        "effective_end": None,
        "effective_text": "2026年10月15日起长期有效",
        "weekday_text": "每周六、周日",
        "clock_text": "00:00 至 24:00（全天封闭）",
    },
]

PAGE_SIZE = 2

# 各目录故意放置的失效链接（href 中的前缀在 _all_items 里按目录替换）
BROKEN_ITEMS = {
    "/carrier": {
        "href": "/carrier/announcement/gg-carrier-deleted",
        "title": "【已撤销】承运商临时管制提示",
        "published": "2026-11-07",
    },
    "": {
        "href": "/announcement/gg-2026-deleted",
        "title": "【已撤销】临时管制公告",
        "published": "2026-11-04",
    },
}

CATALOGS = {
    "/traffic": {
        "name": "交管部门",
        "announcements": TRAFFIC_ANNOUNCEMENTS,
        "broken": None,
    },
    "/carrier": {
        "name": "承运商",
        "announcements": CARRIER_ANNOUNCEMENTS,
        "broken": BROKEN_ITEMS["/carrier"],
    },
    # 承运商目录的完整镜像（无失效链接），用于验证两侧完整采集时的 different
    "/carrier-full": {
        "name": "承运商（完整镜像）",
        "announcements": CARRIER_ANNOUNCEMENTS,
        "broken": None,
    },
    "": {
        "name": "公路",
        "announcements": LEGACY_ANNOUNCEMENTS,
        "broken": BROKEN_ITEMS[""],
    },
}


def _all_items(prefix: str) -> list[dict]:
    catalog = CATALOGS[prefix]
    items = [
        {
            "href": f"{prefix}/announcement/{a['id']}",
            "title": a["title"],
            "published": a["published"],
        }
        for a in catalog["announcements"]
    ]
    if catalog["broken"] is not None:
        # 放在第二条，验证跨分页时失效链接的失败记录；
        # 镜像目录复用同一条失效数据但替换为自身前缀
        broken = dict(catalog["broken"])
        broken["href"] = (
            f"{prefix}/announcement/" + broken["href"].rsplit("/", 1)[-1]
        )
        items.insert(1, broken)
    return items


def _find_announcement(prefix: str, ann_id: str) -> dict | None:
    return next(
        (a for a in CATALOGS[prefix]["announcements"] if a["id"] == ann_id),
        None,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _listing(prefix: str):
    page = max(1, request.args.get("page", default=1, type=int))
    items = _all_items(prefix)
    start = (page - 1) * PAGE_SIZE
    page_items = items[start : start + PAGE_SIZE]
    if not page_items and page != 1:
        abort(404)
    has_next = start + PAGE_SIZE < len(items)
    return render_template_string(
        LIST_TEMPLATE,
        items=page_items,
        page=page,
        has_next=has_next,
        prefix=prefix,
        source_name=CATALOGS[prefix]["name"],
    )


def _detail(prefix: str, ann_id: str):
    ann = _find_announcement(prefix, ann_id)
    if ann is None:
        abort(404)
    return render_template_string(DETAIL_TEMPLATE, ann=ann)


@app.get("/traffic/announcements")
def traffic_listing():
    return _listing("/traffic")


@app.get("/traffic/announcement/<ann_id>")
def traffic_detail(ann_id: str):
    return _detail("/traffic", ann_id)


@app.get("/carrier/announcements")
def carrier_listing():
    return _listing("/carrier")


@app.get("/carrier/announcement/<ann_id>")
def carrier_detail(ann_id: str):
    return _detail("/carrier", ann_id)


@app.get("/carrier-full/announcements")
def carrier_full_listing():
    return _listing("/carrier-full")


@app.get("/carrier-full/announcement/<ann_id>")
def carrier_full_detail(ann_id: str):
    return _detail("/carrier-full", ann_id)


@app.get("/announcements")
def legacy_listing():
    return _listing("")


@app.get("/announcement/<ann_id>")
def legacy_detail(ann_id: str):
    return _detail("", ann_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
