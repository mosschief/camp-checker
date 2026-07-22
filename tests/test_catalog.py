import pytest

from campwatch.catalog import CampgroundCatalog, CampgroundOption, CatalogError


class FakeFacility:
    def __init__(self, facility_id, facility_name, map_id):
        self.facility_id = facility_id
        self.facility_name = facility_name
        self.map_id = map_id


def test_search_filters_and_maps_fields(monkeypatch):
    cat = CampgroundCatalog()
    facilities = [
        FakeFacility(111, "Kanaskat-Palmer State Park", 222),
        FakeFacility(333, "Deception Pass State Park", 444),
        FakeFacility(555, "Kanaskat Overflow", 666),
    ]
    monkeypatch.setattr(cat, "_fetch", lambda rec: [
        CampgroundOption(campground_id=f.facility_id, resource_location_id=f.facility_id,
                         map_id=f.map_id, name=f.facility_name)
        for f in facilities
    ])
    results = cat.search(3, "kanaskat")
    names = [o.name for o in results]
    assert names == ["Kanaskat Overflow", "Kanaskat-Palmer State Park"]  # sorted, filtered
    palmer = next(o for o in results if o.campground_id == 111)
    assert palmer.map_id == 222


def test_empty_query_returns_all(monkeypatch):
    cat = CampgroundCatalog()
    monkeypatch.setattr(cat, "_fetch", lambda rec: [
        CampgroundOption(campground_id=1, resource_location_id=1, map_id=None, name="A"),
        CampgroundOption(campground_id=2, resource_location_id=2, map_id=None, name="B"),
    ])
    assert len(cat.search(3, "")) == 2


def test_cache_avoids_refetch(monkeypatch):
    cat = CampgroundCatalog()
    calls = {"n": 0}

    def fake_fetch(rec):
        calls["n"] += 1
        return [CampgroundOption(campground_id=1, resource_location_id=1, map_id=None, name="A")]

    monkeypatch.setattr(cat, "_fetch", fake_fetch)
    cat.search(3, "a")
    cat.search(3, "a")
    assert calls["n"] == 1  # second search hits the cache


def test_fetch_error_propagates(monkeypatch):
    cat = CampgroundCatalog()

    def boom(rec):
        raise CatalogError("provider down")

    monkeypatch.setattr(cat, "_fetch", boom)
    with pytest.raises(CatalogError):
        cat.search(3, "x")
