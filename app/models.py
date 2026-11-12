"""Pydantic 模型：请求路段与响应结构。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# 整体判定状态：
# - clear          全部公告均成功采集，且所有路段均无相交封闭
# - blocked        存在受阻路段（不代表全部路段受阻）
# - undetermined   存在采集失败，无法给出完整判定（绝不会降级为“畅通”）
CheckStatus = Literal["clear", "blocked", "undetermined"]


class RoadSegment(BaseModel):
    """调度计划中的一个路段通行区间。"""

    road_id: str = Field(..., description="路段编号，如 G15-K123")
    direction: str = Field(..., description="通行方向，如 上行/下行；与公告方向做归一化匹配")
    enter_at: datetime = Field(..., description="带时区的驶入时间（ISO 8601）")
    exit_at: datetime = Field(..., description="带时区的驶出时间（ISO 8601）")

    @field_validator("enter_at", "exit_at")
    @classmethod
    def _must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("时间必须带时区，例如 2026-11-16T21:00:00+08:00")
        return v

    @field_validator("exit_at")
    @classmethod
    def _exit_after_enter(cls, v: datetime, info) -> datetime:
        enter = info.data.get("enter_at")
        if enter is not None and v <= enter:
            raise ValueError("驶出时间必须晚于驶入时间")
        return v


class CheckRequest(BaseModel):
    """采集与核对请求。"""

    announcement_list_url: str = Field(..., description="公告列表页 URL")
    segments: list[RoadSegment] = Field(..., min_length=1)


class BlockingInterval(BaseModel):
    """与通行区间相交的一段封闭区间（北京时间，带 +08:00 偏移）。"""

    start: datetime
    end: datetime


class BlockedResult(BaseModel):
    """单个受阻路段的命中明细。"""

    road_id: str
    direction: str
    enter_at: datetime
    exit_at: datetime
    blocking: list[BlockingInterval]
    announcements: list[str] = Field(
        default_factory=list, description="命中的公告详情页链接（去重）"
    )


class ClearResult(BaseModel):
    """未命中封闭的路段（仅在全部采集成功时给出）。"""

    road_id: str
    direction: str
    enter_at: datetime
    exit_at: datetime


class FetchFailure(BaseModel):
    """采集失败记录：标记无法判定并给出失败地址。"""

    url: str
    stage: Literal["list", "detail"]
    reason: str


class CheckResponse(BaseModel):
    status: CheckStatus
    timezone_note: str = "公告时间均按北京时间(UTC+8)解释"
    fetched_announcements: int
    blocked: list[BlockedResult] = Field(default_factory=list)
    clear: list[ClearResult] = Field(default_factory=list)
    failures: list[FetchFailure] = Field(default_factory=list)
    message: Optional[str] = None


# ---- 双源对账 -------------------------------------------------------------

# 对账整体判定：
# - consistent       两侧均完整采集，且两侧封闭时段完全一致（无差异、无待核实）
# - different        两侧均完整采集，但存在仅一侧刊载的封闭时段
# - partial          至少一侧采集不完整：仅给出可确认差异，其余待核实，绝不报告一致
ReconcileStatus = Literal["consistent", "different", "partial"]

# 单条差异的可确认程度：
# - confirmed   对侧来源已完整采集，差异可确认
# - pending     对侧来源采集不完整，差异待核实
DifferenceVerdict = Literal["confirmed", "pending"]


class ReconcileRequest(BaseModel):
    """双源采集与对账请求。"""

    source_a_list_url: str = Field(..., description="来源 A（如交管部门）公告列表页 URL")
    source_b_list_url: str = Field(..., description="来源 B（如承运商）公告列表页 URL")
    start_date: date = Field(..., description="对账起始日（北京时间，含当日）")
    end_date: date = Field(..., description="对账截止日（北京时间，含当日，跨度最多 7 天）")

    @model_validator(mode="after")
    def _validate_range(self) -> "ReconcileRequest":
        if self.end_date < self.start_date:
            raise ValueError("end_date 不能早于 start_date")
        if (self.end_date - self.start_date).days > 6:
            raise ValueError("日期范围最多 7 天（含首尾）")
        if self.source_a_list_url == self.source_b_list_url:
            raise ValueError("两个公告目录 URL 不能相同")
        return self


class ReconcileInterval(BaseModel):
    """一段封闭时段（北京时间，带 +08:00 偏移）。"""

    start: datetime
    end: datetime


class ReconcileDifference(BaseModel):
    """按路段+方向归并出的单侧封闭差异。"""

    road_id: str
    direction: str
    only_in: Literal["A", "B"] = Field(..., description="该封闭时段仅出现的来源侧")
    verdict: DifferenceVerdict
    intervals: list[ReconcileInterval]
    announcements: list[str] = Field(
        default_factory=list,
        description="实际覆盖这些封闭时段的公告详情页链接（去重）",
    )


class ReconcileSourceSummary(BaseModel):
    """单侧来源的采集概况。"""

    list_url: str
    complete: bool = Field(..., description="该来源是否完整采集（无任何列表/详情失败）")
    fetched_announcements: int
    failures: list[FetchFailure] = Field(default_factory=list)


class ReconcileResponse(BaseModel):
    status: ReconcileStatus
    timezone_note: str = "公告时间均按北京时间(UTC+8)解释；跨午夜封闭归属开始当天"
    start_date: date
    end_date: date
    source_a: ReconcileSourceSummary
    source_b: ReconcileSourceSummary
    confirmed_differences: list[ReconcileDifference] = Field(default_factory=list)
    pending_differences: list[ReconcileDifference] = Field(default_factory=list)
    message: Optional[str] = None
