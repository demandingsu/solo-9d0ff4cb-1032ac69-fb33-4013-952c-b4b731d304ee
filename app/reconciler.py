"""双源对账：展开封闭时段 -> 来源内合并 -> 按路段+方向求两侧差异。

处理规则（需求约定）：
- 封闭时段按原有封闭日、跨午夜（归属开始当天）、生效期与双向通行规则展开，
  仅取“开始日”落在请求日期范围内的窗口；
- 双向公告同时对“上行/下行”两个方向生效；
- 每个来源内部先把重叠/首尾相接的区间合并，再与对侧做差；
- 判定某一来源漏载前必须先完整采集该来源：对侧完整时差异记为 confirmed，
  对侧不完整时同样的单侧时段只能记为 pending（待核实）；
- 任一侧采集不完整时绝不报告“两侧一致”。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .intervals import Interval, merge_intervals, subtract_intervals
from .models import (
    FetchFailure,
    ReconcileDifference,
    ReconcileInterval,
    ReconcileResponse,
    ReconcileSourceSummary,
)
from .parser import ClosureAnnouncement
from .scraper import ScrapeResult
from .timeutils import (
    iter_date_range_windows,
    normalize_direction,
    normalize_road_id,
)

# 双向公告展开到的两个具体方向
BOTH_DIRECTIONS = ("上行", "下行")


@dataclass
class _SourceTimeline:
    """单侧来源按 (路段, 方向) 归并后的封闭时间线。"""

    complete: bool
    list_url: str
    announcements: list[ClosureAnnouncement]
    failures: list
    # key=(规范化路段, 规范化方向) -> 合并后的区间
    intervals: dict[tuple[str, str], list[Interval]] = field(default_factory=dict)
    # 输出展示用的原始路段/方向写法
    display: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)


def _direction_keys(ann: ClosureAnnouncement) -> list[str]:
    direction = normalize_direction(ann.direction)
    if direction == "双向":
        return list(BOTH_DIRECTIONS)
    return [direction]


def build_timeline(
    scrape_result: ScrapeResult, list_url: str, start: date, end: date
) -> _SourceTimeline:
    """采集结果 -> 按路段+方向合并后的来源时间线（仅含日期范围内窗口）。"""
    timeline = _SourceTimeline(
        complete=scrape_result.ok,
        list_url=list_url,
        announcements=scrape_result.announcements,
        failures=scrape_result.failures,
    )

    for ann in scrape_result.announcements:
        road_key = normalize_road_id(ann.road_id)
        windows = list(
            iter_date_range_windows(
                effective_start=ann.effective_start,
                effective_end=ann.effective_end,
                weekdays=ann.weekdays,
                clock=ann.clock,
                range_start=start,
                range_end=end,
            )
        )
        if not windows:
            continue
        for direction_key in _direction_keys(ann):
            key = (road_key, direction_key)
            timeline.display.setdefault(key, (ann.road_id.strip(), direction_key))
            timeline.intervals.setdefault(key, []).extend(windows)

    timeline.intervals = {
        key: merge_intervals(values) for key, values in timeline.intervals.items()
    }
    return timeline


def _covering_urls(
    timeline: _SourceTimeline, key: tuple[str, str], pieces: list[Interval]
) -> list[str]:
    """返回实际覆盖给定差异时段的公告链接（逐段核对，而非整组公告全列）。"""
    # 重新从该来源的原始公告中找出与 pieces 真正相交的公告
    road_key, direction_key = key
    result: list[str] = []
    for ann in timeline.announcements:
        if normalize_road_id(ann.road_id) != road_key:
            continue
        if direction_key not in _direction_keys(ann):
            continue
        windows = iter_date_range_windows(
            effective_start=ann.effective_start,
            effective_end=ann.effective_end,
            weekdays=ann.weekdays,
            clock=ann.clock,
            range_start=min(p[0].date() for p in pieces),
            range_end=max(p[0].date() for p in pieces),
        )
        if any(
            w_start < p_end and p_start < w_end
            for (w_start, w_end) in windows
            for (p_start, p_end) in pieces
        ):
            if ann.url not in result:
                result.append(ann.url)
    return result


def _make_difference(
    side: str,
    timeline: _SourceTimeline,
    key: tuple[str, str],
    pieces: list[Interval],
    verdict: str,
) -> ReconcileDifference:
    road_display, direction_display = timeline.display[key]
    urls = _covering_urls(timeline, key, pieces)
    return ReconcileDifference(
        road_id=road_display,
        direction=direction_display,
        only_in=side,  # type: ignore[arg-type]
        verdict=verdict,  # type: ignore[arg-type]
        intervals=[ReconcileInterval(start=s, end=e) for s, e in pieces],
        announcements=urls,
    )


def reconcile(
    result_a: ScrapeResult,
    result_b: ScrapeResult,
    url_a: str,
    url_b: str,
    start: date,
    end: date,
) -> ReconcileResponse:
    """双源对账主入口。"""
    tl_a = build_timeline(result_a, url_a, start, end)
    tl_b = build_timeline(result_b, url_b, start, end)

    confirmed: list[ReconcileDifference] = []
    pending: list[ReconcileDifference] = []

    all_keys = sorted(set(tl_a.intervals) | set(tl_b.intervals))
    for key in all_keys:
        a_intervals = tl_a.intervals.get(key, [])
        b_intervals = tl_b.intervals.get(key, [])

        # A 有、B 没有的部分；B 完整才能确认 B 漏载，否则待核实
        a_only = subtract_intervals(a_intervals, b_intervals)
        if a_only:
            verdict = "confirmed" if tl_b.complete else "pending"
            diff = _make_difference("A", tl_a, key, a_only, verdict)
            (confirmed if tl_b.complete else pending).append(diff)

        # B 有、A 没有的部分；A 完整才能确认 A 漏载，否则待核实
        b_only = subtract_intervals(b_intervals, a_intervals)
        if b_only:
            verdict = "confirmed" if tl_a.complete else "pending"
            diff = _make_difference("B", tl_b, key, b_only, verdict)
            (confirmed if tl_a.complete else pending).append(diff)
    if not tl_a.complete or not tl_b.complete:
        status = "partial"
        failed_sides = [
            name
            for name, tl in (("A", tl_a), ("B", tl_b))
            if not tl.complete
        ]
        message = (
            f"来源 {'、'.join(failed_sides)} 采集不完整：已确认差异 "
            f"{len(confirmed)} 条，待核实 {len(pending)} 条；"
            "信息不完整，不报告两侧一致。"
        )
    elif confirmed:
        status = "different"
        message = f"两侧均完整采集，发现 {len(confirmed)} 条单侧刊载的封闭时段差异。"
    else:
        status = "consistent"
        message = "两侧均完整采集，日期范围内各路段双向封闭时段完全一致。"

    return ReconcileResponse(
        status=status,
        start_date=start,
        end_date=end,
        source_a=_summary(tl_a),
        source_b=_summary(tl_b),
        confirmed_differences=confirmed,
        pending_differences=pending,
        message=message,
    )


def _summary(timeline: _SourceTimeline) -> ReconcileSourceSummary:
    return ReconcileSourceSummary(
        list_url=timeline.list_url,
        complete=timeline.complete,
        fetched_announcements=len(timeline.announcements),
        failures=[
            FetchFailure(url=f.url, stage=f.stage, reason=f.reason)
            for f in timeline.failures
        ],
    )
