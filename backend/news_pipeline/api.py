from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status

from .service import NewsPipelineActiveError, get_service


router = APIRouter(prefix="/api", tags=["news-pipeline"])


def _start_pipeline() -> dict[str, object]:
    try:
        run = get_service().start()
    except NewsPipelineActiveError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "资讯刷新正在运行") from error
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "资讯刷新暂时不可用") from error
    return {"data": {
        "run_id": run.run_id,
        "raw_snapshot_id": run.raw_snapshot_id,
        "phase": run.phase.value,
    }}


@router.post("/market-news/refresh", status_code=status.HTTP_202_ACCEPTED)
def market_news_refresh(
    mode: Literal["my_focus", "my_holdings", "global_tech", "domestic_policy"] = "my_focus",
    tag_id: list[str] = Query(default=[]),
    category: Literal["all", "policy", "industry", "company", "fund_notice", "deep_content"] = "all",
    days: int = 7,
    sort: Literal["importance", "latest", "holding_relevance"] = "importance",
):
    if days not in {1, 3, 7, 30}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid market-news filters")
    # Query values remain accepted for compatibility; filtering happens on the
    # existing read endpoint after the shared snapshot is published.
    _ = mode, tag_id, category, sort
    return _start_pipeline()


@router.post("/evidence/refresh", status_code=status.HTTP_202_ACCEPTED)
def evidence_refresh():
    return _start_pipeline()


@router.post("/radar/refresh", status_code=status.HTTP_202_ACCEPTED)
def radar_refresh():
    return _start_pipeline()


@router.get("/news/pipeline-status")
def pipeline_status(run_id: str | None = Query(default=None, min_length=1, max_length=128)):
    try:
        return {"data": get_service().get_status(run_id)}
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "资讯刷新运行不存在") from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "无效的资讯刷新运行 ID") from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "资讯刷新状态暂时不可用") from error
