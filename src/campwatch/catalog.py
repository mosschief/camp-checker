"""
Campground catalog: wraps camply's GoingToCamp provider to search campgrounds
by name for the dashboard's autocomplete. Results carry everything a watch
needs (campground_id, resource_location_id, map_id), so the user never looks
up a numeric id by hand.

The full campground list for a recreation area is fetched once and cached
(with a TTL); autocomplete then filters locally, so typing is instant and we
don't hammer GoingToCamp on every keystroke.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 600  # re-fetch a rec area's campground list at most this often


class CampgroundOption(BaseModel):
    campground_id: int
    resource_location_id: int
    map_id: Optional[int]
    name: str


class CatalogError(Exception):
    """Campground catalog could not be fetched from the provider."""


class CampgroundCatalog:
    def __init__(self, ttl_seconds: int = CATALOG_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._cache: dict[int, tuple[float, List[CampgroundOption]]] = {}
        self._lock = threading.Lock()

    def _fetch(self, rec_area_id: int) -> List[CampgroundOption]:
        # Imported lazily so a camply import problem can't break the whole app.
        from camply.providers.going_to_camp.going_to_camp_provider import GoingToCamp

        provider = GoingToCamp()
        try:
            facilities = provider.find_campgrounds(rec_area_id=[rec_area_id])
        except SystemExit as exc:  # camply calls sys.exit on bad rec-area
            raise CatalogError(
                f"provider rejected rec_area_id {rec_area_id} (code {exc.code})"
            ) from exc
        except Exception as exc:
            raise CatalogError(f"failed to fetch campgrounds: {exc}") from exc

        options = []
        for f in facilities:
            try:
                options.append(
                    CampgroundOption(
                        campground_id=int(f.facility_id),
                        resource_location_id=int(f.facility_id),
                        map_id=f.map_id,
                        name=f.facility_name,
                    )
                )
            except (TypeError, ValueError):
                continue
        options.sort(key=lambda o: o.name.lower())
        return options

    def all(self, rec_area_id: int, force: bool = False) -> List[CampgroundOption]:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(rec_area_id)
            if cached and not force and now - cached[0] < self._ttl:
                return cached[1]
        # fetch outside the lock (network call)
        options = self._fetch(rec_area_id)
        with self._lock:
            self._cache[rec_area_id] = (time.monotonic(), options)
        return options

    def search(self, rec_area_id: int, query: str, limit: int = 25) -> List[CampgroundOption]:
        options = self.all(rec_area_id)
        q = (query or "").strip().lower()
        matches = options if not q else [o for o in options if q in o.name.lower()]
        # sort here so ordering is guaranteed for the UI regardless of source order
        return sorted(matches, key=lambda o: o.name.lower())[:limit]
