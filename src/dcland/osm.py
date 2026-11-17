"""Download data center features from OpenStreetMap and group them into sites."""

from __future__ import annotations

import json
import time
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Tags that mark a data center in OSM. telecom=data_center is the documented tag;
# building=data_center is the building-typology equivalent.
QUERY = """
[out:json][timeout:600];
(
  way["telecom"="data_center"];
  relation["telecom"="data_center"];
  way["building"="data_center"];
  relation["building"="data_center"];
  way["industrial"="data_center"];
);
out tags geom;
"""

KEEP_TAGS = ["name", "operator", "start_date", "telecom", "building", "landuse"]

# Equal-area projection used for all area and buffer work.
EQUAL_AREA = "ESRI:54034"  # World Cylindrical Equal Area


def fetch(out_path: Path, retries: int = 3) -> Path:
    """Run the Overpass query and save the raw JSON response."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(retries):
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": QUERY}, timeout=900, headers={"User-Agent": "dcland-research/0.1"})
                r.raise_for_status()
                payload = r.json()
                if not payload.get("elements"):
                    raise RuntimeError("Overpass returned no elements")
                out_path.write_text(json.dumps(payload), encoding="utf-8")
                return out_path
            except Exception as exc:  # noqa: BLE001 - try the next mirror
                last_error = exc
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"Overpass download failed: {last_error}")


def _ring(geometry: list[dict]) -> list[tuple[float, float]]:
    return [(p["lon"], p["lat"]) for p in geometry]


def _way_polygon(element: dict) -> Polygon | None:
    coords = _ring(element.get("geometry", []))
    if len(coords) < 4 or coords[0] != coords[-1]:
        return None
    poly = Polygon(coords)
    return poly if poly.is_valid else poly.buffer(0)


def _relation_polygon(element: dict) -> Polygon | MultiPolygon | None:
    outers, inners = [], []
    for member in element.get("members", []):
        if member.get("type") != "way" or "geometry" not in member:
            continue
        coords = _ring(member["geometry"])
        if len(coords) < 4 or coords[0] != coords[-1]:
            continue  # multi-way rings are rare for these tags; skip rather than guess
        poly = Polygon(coords)
        poly = poly if poly.is_valid else poly.buffer(0)
        (inners if member.get("role") == "inner" else outers).append(poly)
    if not outers:
        return None
    shape = unary_union(outers)
    if inners:
        shape = shape.difference(unary_union(inners))
    return shape if not shape.is_empty else None


def to_features(raw_path: Path) -> gpd.GeoDataFrame:
    """Turn the raw Overpass JSON into one polygon per OSM feature."""
    elements = json.loads(Path(raw_path).read_text(encoding="utf-8"))["elements"]
    rows = []
    for el in elements:
        geom = _way_polygon(el) if el["type"] == "way" else _relation_polygon(el)
        if geom is None or geom.is_empty:
            continue
        tags = el.get("tags", {})
        row = {k: tags.get(k) for k in KEEP_TAGS}
        row["osm_id"] = f"{el['type']}/{el['id']}"
        row["geometry"] = geom
        rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def to_sites(features: gpd.GeoDataFrame, merge_distance_m: float = 150.0) -> gpd.GeoDataFrame:
    """Group features closer than ``merge_distance_m`` into one site.

    A campus is often mapped as several buildings, or as a building inside a
    campus outline. Grouping avoids counting the same campus more than once.
    The site geometry is the union of the mapped polygons, not the buffer.
    """
    ea = features.to_crs(EQUAL_AREA)
    blobs = gpd.GeoDataFrame(geometry=list(ea.buffer(merge_distance_m / 2).union_all().geoms), crs=EQUAL_AREA)
    blobs["site_id"] = range(len(blobs))
    points = ea.copy()
    points["geometry"] = ea.representative_point()
    joined = gpd.sjoin(points, blobs, predicate="within", how="left")
    ea["site_id"] = joined["site_id"].to_numpy()

    def first(values):
        vals = [v for v in values if isinstance(v, str) and v.strip()]
        return vals[0] if vals else None

    rows = []
    for site_id, grp in ea.groupby("site_id"):
        rows.append(
            {
                "site_id": int(site_id),
                "n_features": len(grp),
                "name": first(grp["name"]),
                "operator": first(grp["operator"]),
                "start_date": first(grp["start_date"]),
                "osm_ids": ";".join(grp["osm_id"]),
                "geometry": grp.geometry.union_all(),
            }
        )
    sites = gpd.GeoDataFrame(rows, geometry="geometry", crs=EQUAL_AREA)
    sites["area_ha"] = sites.area / 1e4
    sites = sites.to_crs("EPSG:4326")
    centroids = sites.representative_point()
    sites["lon"] = centroids.x.round(5)
    sites["lat"] = centroids.y.round(5)
    return sites.sort_values("site_id").reset_index(drop=True)
