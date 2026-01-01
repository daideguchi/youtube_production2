from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, Query

from factory_common.ssot_catalog import build_ssot_catalog

router = APIRouter(prefix="/api/ssot", tags=["ssot"])

_CACHE: Dict[str, Any] | None = None
_CACHE_AT: float | None = None
_CACHE_TTL_SEC = 15.0


@router.get("/catalog")
def get_ssot_catalog(
    refresh: bool = Query(False, description="When true, rebuild catalog (bypass short cache)."),
):
    global _CACHE, _CACHE_AT
    now = time.time()
    if not refresh and _CACHE is not None and _CACHE_AT is not None:
        if now - _CACHE_AT < _CACHE_TTL_SEC:
            return _CACHE
    _CACHE = build_ssot_catalog()
    _CACHE_AT = now
    return _CACHE

