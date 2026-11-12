"""区间运算与双源对账规则的离线单元测试（仅依赖项目自身代码）。

运行：PYTHONUSERBASE=/tmp/pyuser python3 -m unittest tests.test_reconcile -v
"""
from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from app.intervals import merge_intervals, subtract_intervals
from app.parser import ClosureAnnouncement
from app.reconciler import reconcile
from app.scraper import FetchFailureRecord, ScrapeResult
from app.timeutils import ClockRange

BJ = timezone(timedelta(hours=8))


def dt(day: int, h: int, m: int = 0, *, month: int = 11) -> datetime:
    return datetime(2026, month, day, h, m, tzinfo=BJ)


def make_ann(
    url: str,
    road_id: str,
    direction: str,
    weekdays: list[int],
    clock: ClockRange,
    *,
    start: date | None = None,
    end: date | None = None,
) -> ClosureAnnouncement:
    return ClosureAnnouncement(
        url=url,
        title=url,
        road_id=road_id,
        direction=direction,
        effective_start=start or date(2026, 11, 1),
        effective_end=end,
        weekdays=weekdays,
        clock=clock,
    )


NIGHT = ClockRange(start=datetime(2000, 1, 1, 22, 0).time(),
                   end=datetime(2000, 1, 1, 5, 0).time(), cross_midnight=True)
DAY = ClockRange(start=datetime(2000, 1, 1, 9, 0).time(),
                 end=datetime(2000, 1, 1, 16, 30).time(), cross_midnight=False)


class IntervalTests(unittest.TestCase):
    def test_merge_overlapping_and_touching(self):
        merged = merge_intervals([
            (dt(16, 22), dt(17, 5)),
            (dt(17, 5), dt(17, 8)),   # 首尾相接 -> 合并
            (dt(18, 0), dt(18, 6)),
            (dt(18, 3), dt(18, 9)),   # 重叠 -> 合并
        ])
        self.assertEqual(merged, [
            (dt(16, 22), dt(17, 8)),
            (dt(18, 0), dt(18, 9)),
        ])

    def test_subtract_keeps_pieces(self):
        base = [(dt(16, 0), dt(17, 0))]
        cover = [(dt(16, 5), dt(16, 8)), (dt(16, 20), dt(17, 1))]
        self.assertEqual(subtract_intervals(base, cover), [
            (dt(16, 0), dt(16, 5)),
            (dt(16, 8), dt(16, 20)),
        ])

    def test_subtract_empty_cover_returns_merged_base(self):
        base = [(dt(16, 22), dt(17, 5)), (dt(18, 22), dt(19, 5))]
        self.assertEqual(subtract_intervals(base, []), merge_intervals(base))


def _result(anns: list[ClosureAnnouncement], failures: list | None = None) -> ScrapeResult:
    return ScrapeResult(announcements=anns, failures=failures or [])


class ReconcileTests(unittest.TestCase):
    URL_A = "http://example.test/traffic/announcements?page=1"
    URL_B = "http://example.test/carrier/announcements?page=1"
    START = date(2026, 11, 16)  # 周一
    END = date(2026, 11, 22)    # 周日，共 7 天

    def _call(self, ra: ScrapeResult, rb: ScrapeResult):
        return reconcile(ra, rb, self.URL_A, self.URL_B, self.START, self.END)

    def test_consistent_when_identical_and_complete(self):
        anns = [make_ann("u1", "G15-K123", "下行", [0, 2], NIGHT)]
        resp = self._call(_result(list(anns)), _result(list(anns)))
        self.assertEqual(resp.status, "consistent")
        self.assertEqual(resp.confirmed_differences, [])
        self.assertEqual(resp.pending_differences, [])
        self.assertTrue(resp.source_a.complete and resp.source_b.complete)

    def test_confirmed_a_only_when_b_complete(self):
        # A：周一、周三夜间；B：仅周一夜间 -> 周三(11-18)夜间为 A-only
        a = _result([make_ann("a1", "G15-K123", "下行", [0, 2], NIGHT)])
        b = _result([make_ann("b1", "G15-K123", "下行", [0], NIGHT)])
        resp = self._call(a, b)
        self.assertEqual(resp.status, "different")
        self.assertEqual(len(resp.confirmed_differences), 1)
        diff = resp.confirmed_differences[0]
        self.assertEqual((diff.road_id, diff.direction, diff.only_in, diff.verdict),
                         ("G15-K123", "下行", "A", "confirmed"))
        self.assertEqual(
            [(i.start, i.end) for i in diff.intervals],
            [(dt(18, 22), dt(19, 5))],
        )
        self.assertEqual(diff.announcements, ["a1"])
        self.assertEqual(resp.pending_differences, [])

    def test_bidirectional_expands_both_directions(self):
        # A：双向周末全天；B：仅周日 -> 周六(11-21)上行/下行各一条 A-only
        all_day = ClockRange(
            start=datetime(2000, 1, 1, 0, 0).time(),
            end=datetime(2000, 1, 1, 0, 0).time(),
            cross_midnight=True,
        )
        a = _result([make_ann("a2", "G25-K88", "双向", [5, 6], all_day)])
        b = _result([make_ann("b2", "G25-K88", "双向", [6], all_day)])
        resp = self._call(a, b)
        self.assertEqual(resp.status, "different")
        keys = sorted((d.road_id, d.direction) for d in resp.confirmed_differences)
        self.assertEqual(keys, [("G25-K88", "上行"), ("G25-K88", "下行")])
        for d in resp.confirmed_differences:
            self.assertEqual(
                [(i.start, i.end) for i in d.intervals],
                [(dt(21, 0), dt(22, 0))],
            )

    def test_b_only_difference_with_links(self):
        a = _result([])
        b = _result([make_ann("b3", "G60-K45", "上行", [4], DAY)])
        resp = self._call(a, b)
        self.assertEqual(resp.status, "different")
        diff = resp.confirmed_differences[0]
        self.assertEqual((diff.only_in, diff.verdict), ("B", "confirmed"))
        self.assertEqual(
            [(i.start, i.end) for i in diff.intervals],
            [(dt(20, 9), dt(20, 16, 30))],
        )
        self.assertEqual(diff.announcements, ["b3"])

    def test_incomplete_side_makes_opposite_differences_pending(self):
        # B 采集不完整：A 多出的周三夜间只能待核实，绝不能确认 B 漏载
        a = _result([make_ann("a1", "G15-K123", "下行", [0, 2], NIGHT)])
        b = _result(
            [make_ann("b1", "G15-K123", "下行", [0], NIGHT)],
            [FetchFailureRecord(url="http://x/dead", stage="detail", reason="HTTP 404")],
        )
        resp = self._call(a, b)
        self.assertEqual(resp.status, "partial")
        self.assertEqual(resp.confirmed_differences, [])
        self.assertEqual(len(resp.pending_differences), 1)
        pend = resp.pending_differences[0]
        self.assertEqual((pend.only_in, pend.verdict), ("A", "pending"))
        self.assertFalse(resp.source_b.complete)
        self.assertEqual(resp.source_b.failures[0].url, "http://x/dead")

    def test_incomplete_side_pushes_its_missing_side_differences_to_pending(self):
        # A 不完整、B 完整：B-only 差异声称“A 漏载”，但 A 没采全，只能待核实
        a = _result(
            [],
            [FetchFailureRecord(url="http://x/list", stage="list", reason="连接失败")],
        )
        b = _result([make_ann("b3", "G60-K45", "上行", [4], DAY)])
        resp = self._call(a, b)
        self.assertEqual(resp.status, "partial")
        self.assertEqual(resp.confirmed_differences, [])
        self.assertEqual(len(resp.pending_differences), 1)
        self.assertEqual(
            (resp.pending_differences[0].only_in,
             resp.pending_differences[0].verdict),
            ("B", "pending"),
        )

    def test_incomplete_side_still_allows_confirmed_difference_on_other_side(self):
        # B 不完整、A 完整：A-only 差异声称“B 漏载”，B 没采全 -> 待核实；
        # 而 A 完整意味着 B 实际采到的 B-only 时段仍可确认 A 漏载。
        a = _result([make_ann("a9", "G99-K9", "下行", [0], NIGHT)])
        b = _result(
            [make_ann("b9", "G99-K8", "下行", [0], NIGHT)],
            [FetchFailureRecord(url="http://x/dead", stage="detail", reason="HTTP 404")],
        )
        resp = self._call(a, b)
        self.assertEqual(resp.status, "partial")
        self.assertEqual(
            [(d.only_in, d.verdict) for d in resp.confirmed_differences],
            [("B", "confirmed")],
        )
        self.assertEqual(
            [(d.only_in, d.verdict) for d in resp.pending_differences],
            [("A", "pending")],
        )

    def test_no_consistent_when_incomplete_even_without_observed_diff(self):
        # 两侧窗口恰好一致，但 B 采集失败：必须 partial，不得报告 consistent
        anns = [make_ann("a1", "G15-K123", "下行", [0], NIGHT)]
        a = _result(list(anns))
        b = _result(
            [make_ann("b1", "G15-K123", "下行", [0], NIGHT)],
            [FetchFailureRecord(url="http://x/dead", stage="detail", reason="HTTP 404")],
        )
        resp = self._call(a, b)
        self.assertEqual(resp.status, "partial")
        self.assertEqual(resp.confirmed_differences, [])

    def test_effective_range_limits_windows(self):
        # 生效期 11-17 截止：周一(11-16)、周三(11-18)两个封闭日中，
        # 仅开始日 11-16 落在生效期内；跨午夜窗口尾部延伸到 11-17 不影响归属
        a = _result([
            make_ann("a1", "G15-K123", "下行", [0, 2], NIGHT,
                     start=date(2026, 11, 1), end=date(2026, 11, 17))
        ])
        b = _result([])
        resp = self._call(a, b)
        diff = resp.confirmed_differences[0]
        self.assertEqual(
            [(i.start, i.end) for i in diff.intervals],
            [(dt(16, 22), dt(17, 5))],
        )

    def test_overlapping_announcements_merge_within_source(self):
        # 同来源两条公告时段重叠，合并后只产生一条连续差异，链接两条都保留
        clock1 = ClockRange(dt(16, 0).timetz(), dt(16, 10).timetz(), False)
        clock2 = ClockRange(dt(16, 5).timetz(), dt(16, 12).timetz(), False)
        # 构造单日（周一=0）两条重叠公告
        a = _result([
            make_ann("a1", "G99-K1", "下行", [0], clock1),
            make_ann("a2", "G99-K1", "下行", [0], clock2),
        ])
        b = _result([])
        resp = self._call(a, b)
        diff = resp.confirmed_differences[0]
        self.assertEqual(
            [(i.start, i.end) for i in diff.intervals],
            [(dt(16, 0), dt(16, 12))],
        )
        self.assertEqual(sorted(diff.announcements), ["a1", "a2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
