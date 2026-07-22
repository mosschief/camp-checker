import pytest

from campwatch.catalog import CampgroundCatalog, CampgroundOption, CatalogError

BASE = "https://washington.goingtocamp.com"


def opts(*items):
    return [CampgroundOption(campground_id=i, resource_location_id=i, map_id=m, name=n)
            for (i, n, m) in items]


def test_search_filters_and_maps_fields(monkeypatch):
    cat = CampgroundCatalog()
    monkeypatch.setattr(cat, "_fetch", lambda base: opts(
        (111, "Kanaskat-Palmer State Park", 222),
        (333, "Deception Pass State Park", 444),
        (555, "Kanaskat Overflow", 666),
    ))
    results = cat.search(BASE, "kanaskat")
    names = [o.name for o in results]
    assert names == ["Kanaskat Overflow", "Kanaskat-Palmer State Park"]  # sorted, filtered
    palmer = next(o for o in results if o.campground_id == 111)
    assert palmer.map_id == 222


def test_empty_query_returns_all(monkeypatch):
    cat = CampgroundCatalog()
    monkeypatch.setattr(cat, "_fetch", lambda base: opts((1, "A", None), (2, "B", None)))
    assert len(cat.search(BASE, "")) == 2


def test_cache_avoids_refetch(monkeypatch):
    cat = CampgroundCatalog()
    calls = {"n": 0}

    def fake_fetch(base):
        calls["n"] += 1
        return opts((1, "A", None))

    monkeypatch.setattr(cat, "_fetch", fake_fetch)
    cat.search(BASE, "a")
    cat.search(BASE, "a")
    assert calls["n"] == 1  # second search hits the cache


def test_fetch_error_propagates(monkeypatch):
    cat = CampgroundCatalog()

    def boom(base):
        raise CatalogError("provider down")

    monkeypatch.setattr(cat, "_fetch", boom)
    with pytest.raises(CatalogError):
        cat.search(BASE, "x")


def test_fetch_parses_and_filters_real_shape(monkeypatch):
    """_fetch should parse the /api/resourceLocation shape and drop non-camp facilities."""
    cat = CampgroundCatalog()
    resource_location = [
        {"resourceLocationId": 111, "resourceCategoryIds": [-2147483648],
         "localizedValues": [{"fullName": "Kanaskat-Palmer State Park"}]},
        {"resourceLocationId": 999, "resourceCategoryIds": [-1],  # day-use → dropped
         "localizedValues": [{"fullName": "Some Day Use Area"}]},
        {"resourceLocationId": 333, "resourceCategoryIds": [-2147483643],
         "localizedValues": [{"fullName": "Deception Pass"}]},
    ]
    maps = [{"resourceLocationId": 111, "mapId": 222}]

    def fake_get(url):
        return maps if url.endswith("/api/maps") else resource_location

    monkeypatch.setattr(cat, "_get_json", fake_get)
    out = cat._fetch(BASE)
    names = [o.name for o in out]
    assert names == ["Deception Pass", "Kanaskat-Palmer State Park"]
    assert next(o for o in out if o.campground_id == 111).map_id == 222
    assert all(o.campground_id != 999 for o in out)  # day-use filtered out
