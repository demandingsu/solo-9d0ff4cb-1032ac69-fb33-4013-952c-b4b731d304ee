"""FastAPI 入口：冷链配送施工封闭核对 API。"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI

from .models import (
    CheckRequest,
    CheckResponse,
    ReconcileRequest,
    ReconcileResponse,
)
from .reconciler import reconcile
from .scheduler import build_response
from .scraper import scrape

app = FastAPI(
    title="冷链配送施工封闭核对 API",
    description=(
        "传入公告列表页 URL 与计划路段（编号、方向、带时区驶入/驶出时间），"
        "采集同站分页与详情并返回受阻路段及公告链接；"
        "采集失败时标记无法判定并给出失败地址。"
        "另提供 /reconcile 双源对账：比对交管部门与承运商两份公告目录，"
        "按路段与方向找出仅一侧刊载的封闭时段。"
    ),
    version="1.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/check", response_model=CheckResponse)
async def check(request: CheckRequest) -> CheckResponse:
    scrape_result = await scrape(request.announcement_list_url)
    return build_response(request.segments, scrape_result)


@app.post("/reconcile", response_model=ReconcileResponse)
async def reconcile_sources(request: ReconcileRequest) -> ReconcileResponse:
    # 两个来源各自完整采集（分页+详情全部尝试），互不短路
    result_a, result_b = await asyncio.gather(
        scrape(request.source_a_list_url),
        scrape(request.source_b_list_url),
    )
    return reconcile(
        result_a,
        result_b,
        request.source_a_list_url,
        request.source_b_list_url,
        request.start_date,
        request.end_date,
    )
