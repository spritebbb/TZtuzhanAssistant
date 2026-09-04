# -*- coding: utf-8 -*-
"""成本面板（D5）：token 用量与花费汇总，只读接口。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..core.config import config
from ..core.userdb import usage_summary

router = APIRouter(prefix="/api/usage", tags=["usage"])

_UID = "assistant-main"


def _cost(prompt: int, completion: int) -> float:
    return round(
        prompt * config.llm_price_input_per_mtok / 1_000_000
        + completion * config.llm_price_output_per_mtok / 1_000_000,
        4,
    )


@router.get("/summary")
async def api_usage_summary(days: int = Query(7, ge=1, le=90)):
    data = usage_summary(_UID, days)
    data["today"]["cost"] = _cost(data["today"]["prompt"], data["today"]["completion"])
    data["period"]["cost"] = _cost(data["period"]["prompt"], data["period"]["completion"])
    for row in data["by_channel"]:
        row["cost"] = _cost(row["prompt"], row["completion"])
    data["prices"] = {
        "input_per_mtok": config.llm_price_input_per_mtok,
        "output_per_mtok": config.llm_price_output_per_mtok,
    }
    return {"ok": True, "usage": data}
