"""
Campground catalog for the dashboard's autocomplete.

Rather than route through camply's campground-listing code (which is brittle
across GoingToCamp jurisdictions), this queries GoingToCamp's documented,
unauthenticated endpoints directly:

    GET /api/resourceLocation  -> campgrounds (id, category ids, localized name)
    GET /api/maps              -> resourceLocationId -> mapId

Results carry campground_id / resource_location_id / map_id so the UI fills
them in and the user never looks up a numeric id by hand. The full list for a
host is fetched once and cached (with a TTL); autocomplete filters locally so
typing is instant and we don't hammer the provider.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, List, Optional

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 600  # re-fetch a host's campground list at most this often
FETCH_TIMEOUT = 25.0

# GoingToCamp resource-category ids that denote a reservable campground
# (from camply's provider constants).
CAMP_SITE = -2147483648
OVERFLOW_SITE = -2147483647
GROUP_SITE = -2147483643
CAMPGROUND_CATEGORIES = {CAMP_SITE, OVERFLOW_SITE, GROUP_SITE}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json",
}


class CampgroundOption(BaseModel):
    campground_id: int
    resource_location_id: int
    map_id: Optional[int]
    name: str


class CatalogError(Exception):
    """Campground catalog could not be fetched from the provider."""


def _dig(node: Any, *path: Any) -> Any:
    for key in path:
        if isinstance(node, list):
            if not isinstance(key, int) or key >= len(node):
                return None
            node = node[key]
        elif isinstance(node, dict):
            node = node.get(key)
        else:
            return None
    return node


class CampgroundCatalog:
    def __init__(self, ttl_seconds: int = CATALOG_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, List[CampgroundOption]]] = {}
        self._lock = threading.Lock()

    def _get_json(self, url: str) -> Any:
        resp = httpx.get(url, headers=_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()

    def _map_ids(self, base_url: str) -> dict:
        """resourceLocationId -> mapId, best-effort (never fatal)."""
        try:
            maps = self._get_json(f"{base_url}/api/maps")
        except Exception as exc:
            logger.warning("could not fetch /api/maps for map ids: %s", exc)
            return {}
        out = {}
        if isinstance(maps, list):
            for m in maps:
                rid, mid = _dig(m, "resourceLocationId"), _dig(m, "mapId")
                if rid is not None and mid is not None:
                    out[rid] = mid
        return out

    def _fetch(self, base_url: str) -> List[CampgroundOption]:
        try:
            facilities = self._get_json(f"{base_url}/api/resourceLocation")
        except Exception as exc:
            raise CatalogError(f"failed to fetch campgrounds: {exc}") from exc
        if not isinstance(facilities, list):
            raise CatalogError("unexpected response from /api/resourceLocation")

        map_ids = self._map_ids(base_url)
        options: List[CampgroundOption] = []
        for facil in facilities:
            rid = _dig(facil, "resourceLocationId")
            name = _dig(facil, "localizedValues", 0, "fullName")
            categories = _dig(facil, "resourceCategoryIds") or []
            if rid is None or not name:
                continue
            # keep only reservable campgrounds (skip day-use / non-camp facilities)
            if categories and not (set(categories) & CAMPGROUND_CATEGORIES):
                continue
            try:
                rid_int = int(rid)
            except (TypeError, ValueError):
                continue
            options.append(
                CampgroundOption(
                    campground_id=rid_int,
                    resource_location_id=rid_int,
                    map_id=map_ids.get(rid),
                    name=str(name),
                )
            )
        options.sort(key=lambda o: o.name.lower())
        return options

    def all(self, base_url: str, force: bool = False) -> List[CampgroundOption]:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(base_url)
            if cached and not force and now - cached[0] < self._ttl:
                return cached[1]
        options = self._fetch(base_url)  # network call outside the lock
        with self._lock:
            self._cache[base_url] = (time.monotonic(), options)
        return options

    def search(self, base_url: str, query: str, limit: int = 25) -> List[CampgroundOption]:
        options = self.all(base_url)
        q = (query or "").strip().lower()
        matches = options if not q else [o for o in options if q in o.name.lower()]
        return sorted(matches, key=lambda o: o.name.lower())[:limit]
