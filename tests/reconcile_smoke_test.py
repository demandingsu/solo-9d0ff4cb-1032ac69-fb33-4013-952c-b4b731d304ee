"""双源对账端到端冒烟测试（仅标准库）：对离线样本站真实采集两份目录。

前置：API 与样本站已启动（docker compose up 或本地 uvicorn/flask）。
样本目录：
- /traffic  交管部门（完整，来源 A）
- /carrier  承运商（含 1 个失效详情链接，来源 B）
覆盖：单侧漏载、双向展开、来源独有公告、跨午夜归属、采集不完整 -> 待核实、
绝不报告一致、7 天上限校验。
"""
import json
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000/reconcile"
TRAFFIC = "http://127.0.0.1:8080/traffic/announcements?page=1"
CARRIER = "http://127.0.0.1:8080/carrier/announcements?page=1"

# 2026-11-16(周一) ~ 2026-11-22(周日)，恰好 7 天
START, END = "2026-11-16", "2026-11-22"


def call(url_a=TRAFFIC, url_b=CARRIER, start=START, end=END):
    body = json.dumps({
        "source_a_list_url": url_a,
        "source_b_list_url": url_b,
        "start_date": start,
        "end_date": end,
    }).encode()
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def call_expect_422(payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        assert exc.code == 422, f"期望 422，实际 {exc.code}"
        return json.loads(exc.read().decode())
    raise AssertionError("应当返回 422")


def show(name, data):
    print(f"== {name} ==")
    print("status:", data["status"])
    print(
        "  A complete:", data["source_a"]["complete"],
        "| fetched:", data["source_a"]["fetched_announcements"],
    )
    print(
        "  B complete:", data["source_b"]["complete"],
        "| fetched:", data["source_b"]["fetched_announcements"],
    )
    for d in data["confirmed_differences"]:
        print("  confirmed", d["only_in"], d["road_id"], d["direction"],
              [(x["start"][5:16], x["end"][5:16]) for x in d["intervals"]],
              "links:", d["announcements"])
    for d in data["pending_differences"]:
        print("  pending  ", d["only_in"], d["road_id"], d["direction"],
              [(x["start"][5:16], x["end"][5:16]) for x in d["intervals"]],
              "links:", d["announcements"])
    for side in ("source_a", "source_b"):
        for f in data[side]["failures"]:
            print("  FAIL", side, f["stage"], f["url"], f["reason"])
    print()
    return data


# 1. 主场景：交管完整、承运商不完整（含失效链接），差异确认与待核实并存
d1 = show("交管 vs 承运商（7 天，承运商含失效链接）", call())
assert d1["status"] == "partial", "任一侧采集不完整时必须 partial"
assert d1["source_a"]["complete"] is True
assert d1["source_b"]["complete"] is False
assert d1["source_b"]["fetched_announcements"] == 4

# 承运商失效链接被逐条记录
b_failures = d1["source_b"]["failures"]
assert len(b_failures) == 1 and b_failures[0]["stage"] == "detail"
assert b_failures[0]["url"].endswith("/carrier/announcement/gg-carrier-deleted")
assert b_failures[0]["reason"] == "HTTP 404"
assert d1["source_a"]["failures"] == []

# 2. 可确认差异：承运商独有 G60-K45（交管已完整采集，足以确认交管漏载）
g60 = [d for d in d1["confirmed_differences"] if d["road_id"] == "G60-K45"]
assert len(g60) == 1
assert g60[0]["only_in"] == "B" and g60[0]["verdict"] == "confirmed"
assert g60[0]["direction"] == "上行"
# 周五 = 2026-11-20，08:00-12:00
assert g60[0]["intervals"] == [{
    "start": "2026-11-20T08:00:00+08:00",
    "end": "2026-11-20T12:00:00+08:00",
}]
assert g60[0]["announcements"][0].endswith("/carrier/announcement/gg-carrier-004")

# 3. 待核实差异：承运商没采全，交管多出的时段不能断言承运商漏载
pending_keys = sorted((d["road_id"], d["direction"]) for d in d1["pending_differences"])
# G15-K123 下行（周三夜间漏载）、G25-K88 双向（周六漏载，展开为上行/下行）
assert ("G15-K123", "下行") in pending_keys
assert ("G25-K88", "上行") in pending_keys
assert ("G25-K88", "下行") in pending_keys
for d in d1["pending_differences"]:
    assert d["verdict"] == "pending" and d["only_in"] == "A"

# 4. 跨午夜归属：G15-K123 下行仅周三(11-18)22:00 -> 周四(11-19)05:00 为差异；
#    周一夜间两侧都有，不得出现在差异中
g15 = next(d for d in d1["pending_differences"] if d["road_id"] == "G15-K123")
assert g15["intervals"] == [{
    "start": "2026-11-18T22:00:00+08:00",
    "end": "2026-11-19T05:00:00+08:00",
}]
assert g15["announcements"][0].endswith("/traffic/announcement/gg-traffic-001")

# 5. 双向展开：G25-K88 差异仅为周六(11-21)全天，周日两侧一致不出现
g25_diffs = [d for d in d1["pending_differences"] if d["road_id"] == "G25-K88"]
assert sorted(d["direction"] for d in g25_diffs) == ["上行", "下行"]
for d in g25_diffs:
    assert d["intervals"] == [{
        "start": "2026-11-21T00:00:00+08:00",
        "end": "2026-11-22T00:00:00+08:00",
    }]
    assert d["announcements"][0].endswith(
        "/traffic/announcement/gg-traffic-003"
    )

# 6. G15-K123 上行两侧一致（周二/周四白天），不得出现在任何差异里
all_diffs = d1["confirmed_differences"] + d1["pending_differences"]
assert not any(
    d["road_id"] == "G15-K123" and d["direction"] == "上行" for d in all_diffs
)

# 7. 两侧完整且内容一致：同一完整目录自比 -> consistent（URL 不同参数即可）
d7 = show("完整目录自比 -> consistent", call(
    url_a=TRAFFIC,
    url_b=TRAFFIC + "&_v=2",
))
assert d7["status"] == "consistent"
assert d7["confirmed_differences"] == [] and d7["pending_differences"] == []
assert d7["source_a"]["complete"] and d7["source_b"]["complete"]

# 8. 两侧均完整采集但内容不同：交管 vs 承运商完整镜像 -> different
d8 = show("交管 vs 承运商完整镜像（两侧完整，存在差异）", call(
    url_b="http://127.0.0.1:8080/carrier-full/announcements?page=1",
))
assert d8["status"] == "different"
assert d8["source_a"]["complete"] and d8["source_b"]["complete"]
assert d8["pending_differences"] == []
confirmed = {(d["road_id"], d["direction"], d["only_in"])
             for d in d8["confirmed_differences"]}
# 周三夜间 A-only、周六双向 A-only、G60 周五 B-only
assert ("G15-K123", "下行", "A") in confirmed
assert ("G25-K88", "上行", "A") in confirmed
assert ("G25-K88", "下行", "A") in confirmed
assert ("G60-K45", "上行", "B") in confirmed
for d in d8["confirmed_differences"]:
    assert d["verdict"] == "confirmed" and d["announcements"]

# 9. 参数校验：超过 7 天、相同 URL、起止倒置 -> 422
call_expect_422({
    "source_a_list_url": TRAFFIC,
    "source_b_list_url": CARRIER,
    "start_date": START,
    "end_date": "2026-11-23",  # 8 天
})
call_expect_422({
    "source_a_list_url": TRAFFIC,
    "source_b_list_url": TRAFFIC,  # 相同 URL
    "start_date": START,
    "end_date": END,
})
call_expect_422({
    "source_a_list_url": TRAFFIC,
    "source_b_list_url": CARRIER,
    "start_date": END,
    "end_date": START,  # 起止倒置
})
print("参数校验（>7 天 / 相同 URL / 起止倒置）均返回 422 ✔")

# 10. 列表页不可达：list 阶段失败，整侧不完整，差异全部待核实
d10 = show("来源 A 列表页不可达", call(
    url_a="http://127.0.0.1:8080/traffic/announcements?page=999",
))
assert d10["status"] == "partial"
assert d10["source_a"]["complete"] is False
assert any(f["stage"] == "list" for f in d10["source_a"]["failures"])
# B 有、A 空：声称 A 漏载但 A 没采全 -> 只能待核实，绝不确认
assert d10["confirmed_differences"] == []
assert len(d10["pending_differences"]) >= 1

print("全部断言通过 ✔")
