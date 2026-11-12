"""半开时间区间运算：合并重叠区间、区间相减。

对账场景下区间均为北京时间的半开区间 [start, end)；
首尾相接（a.end == b.start）的两个区间也合并，
这样同一路段相邻公告拼出的连续封闭不会在对账时产生伪差异。
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence, TypeVar

T = TypeVar("T")
Interval = tuple[datetime, datetime]


def merge_intervals(intervals: Sequence[Interval]) -> list[Interval]:
    """合并互相重叠或首尾相接的区间。"""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda x: x[0])
    merged: list[list[datetime]] = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:  # 半开区间：首尾相接也合并
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def subtract_intervals(
    base: Sequence[Interval], cover: Sequence[Interval]
) -> list[Interval]:
    """返回 base 中未被 cover 覆盖的部分（结果已合并）。"""
    if not base:
        return []
    if not cover:
        return merge_intervals(base)

    cover_merged = merge_intervals(cover)
    pieces: list[Interval] = []
    for b_start, b_end in merge_intervals(base):
        cursor = b_start
        for c_start, c_end in cover_merged:
            if c_end <= cursor:
                continue  # 覆盖区间在游标之前，跳过
            if c_start >= b_end:
                break  # 覆盖区间已在 base 之后，后续无需再比
            if c_start > cursor:
                pieces.append((cursor, min(c_start, b_end)))
            cursor = max(cursor, c_end)
            if cursor >= b_end:
                break
        if cursor < b_end:
            pieces.append((cursor, b_end))
    return merge_intervals(pieces)
