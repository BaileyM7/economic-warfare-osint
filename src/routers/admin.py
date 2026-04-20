"""Admin endpoints — usage analytics for tracking logins and feature adoption."""
from __future__ import annotations

from fastapi import APIRouter, Query

from src.db import query_usage_summary

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/usage")
async def usage_summary(days: int = Query(30, ge=1, le=365)):
    """Return aggregate usage stats for the last N days.

    Response shape:
        - logins_per_day:  [{day, success, failure, unique_users}]
        - top_features:    [{feature, hits, unique_users}]
        - top_users:       [{username, events, last_seen}]
        - recent_logins:   [{timestamp, username, status_code, client_ip, detail}]
    """
    return query_usage_summary(days=days)
