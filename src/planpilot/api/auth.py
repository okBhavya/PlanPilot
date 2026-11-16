"""API key authentication dependency for PlanPilot REST API."""

from __future__ import annotations

import os

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(API_KEY_HEADER)) -> str:
    """Validate X-API-Key header against configured PLANPILOT_API_KEY.

    If PLANPILOT_API_KEY is not configured or REQUIRE_API_KEY is false,
    access is permitted.
    """
    configured_key = os.getenv("PLANPILOT_API_KEY")
    require_key = os.getenv("REQUIRE_API_KEY", "false").lower() in {
        "true",
        "1",
        "yes",
    }

    if not require_key:
        return api_key or "anonymous"

    if not configured_key:
        return "anonymous"

    if not api_key or api_key != configured_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key in X-API-Key header",
        )

    return api_key
