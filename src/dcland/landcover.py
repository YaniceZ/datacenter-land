"""Annual 10 m land cover inside each data center footprint, 2017-2023.

Source: Esri / Impact Observatory 10 m Annual Land Cover (v02), read as cloud
optimised GeoTIFFs from the Microsoft Planetary Computer. Only the small
window around each site is read.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "io-lulc-annual-v02"
YEARS = list(range(2017, 2024))

# Class codes of the Impact Observatory product.
CLASSES = {
    1: "water",
    2: "trees",
    4: "flooded_vegetation",
    5: "crops",
    7: "built",
    8: "bare",
    9: "snow_ice",
    10: "clouds",
    11: "rangeland",
}
BUILT = 7
PIXEL_HA = 0.01  # 10 m x 10 m

_local = threading.local()
_tile_cache: dict[tuple[int, int], dict[int, str]] = {}
_tile_lock = threading.Lock()


def _catalog():
    if not hasattr(_local, "catalog"):
        _local.catalog = pystac_client.Client.open(STAC_URL)
    return _local.catalog


def _hrefs_for(lon: float, lat: float) -> dict[int, str]:
    """Unsigned asset URL per year for the tile that covers a point."""
    key = (int(np.floor(lon)), int(np.floor(lat)))
    with _tile_lock:
        if key in _tile_cache:
            return _tile_cache[key]
    items = _catalog().search(collections=[COLLECTION], intersects={"type": "Point", "coordinates": [lon, lat]}).items()
    hrefs: dict[int, str] = {}
    for item in items:
        year = int(item.properties["start_datetime"][:4])
        hrefs.setdefault(year, item.assets["data"].href)
    with _tile_lock:
        _tile_cache[key] = hrefs
    return hrefs


def transition_table(first: np.ndarray, last: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    """Hectares that were not built in ``first`` and are built in ``last``, by first-year class."""
    converted = mask & (first != BUILT) & (last == BUILT) & (first != 0)
    out = {}
    for code, name in CLASSES.items():
        if code == BUILT:
            continue
        out[f"conv_{name}_ha"] = float(np.count_nonzero(converted & (first == code))) * PIXEL_HA
    out["conv_total_ha"] = float(np.count_nonzero(converted)) * PIXEL_HA
    return out


def first_built_year(built_share: dict[int, float], threshold: float = 0.5) -> int | None:
    """First year in which the built share reaches the threshold and stays there."""
    years = sorted(built_share)
    for i, year in enumerate(years):
        if all(built_share[y] >= threshold for y in years[i:]):
            return year
    return None


def _read_stack(geom_wgs84, lon: float, lat: float, pad_m: float):
    """Read all years for the window around a geometry. Returns arrays, transform and crs."""
    hrefs = _hrefs_for(lon, lat)
    if not all(y in hrefs for y in YEARS):
        raise RuntimeError("land cover tile missing for some years")
    stack, transform, crs = {}, None, None
    for year in YEARS:
        with rasterio.open(pc.sign(hrefs[year])) as src:
            if crs is None:
                crs = src.crs
                geom = gpd.GeoSeries([geom_wgs84], crs="EPSG:4326").to_crs(crs).iloc[0]
                minx, miny, maxx, maxy = geom.bounds
                window = from_bounds(minx - pad_m, miny - pad_m, maxx + pad_m, maxy + pad_m, src.transform)
                window = window.round_offsets().round_lengths()
                transform = src.window_transform(window)
            stack[year] = src.read(1, window=window, boundless=True, fill_value=0)
    return stack, transform, crs, geom


def analyse_site(row, buffer_m: float = 100.0) -> dict:
    """Land cover history for one site: footprint and footprint plus a buffer."""
    stack, transform, crs, geom = _read_stack(row.geometry, row.lon, row.lat, pad_m=buffer_m + 20)
    shape = stack[YEARS[0]].shape
    masks = {
        "fp": geometry_mask([geom], out_shape=shape, transform=transform, invert=True, all_touched=False),
        "buf": geometry_mask([geom.buffer(buffer_m)], out_shape=shape, transform=transform, invert=True, all_touched=False),
    }
    out: dict = {"site_id": row.site_id}
    for label, mask in masks.items():
        n = int(mask.sum())
        out[f"{label}_pixels"] = n
        if n == 0:
            continue
        built_share = {y: float(np.count_nonzero(mask & (stack[y] == BUILT))) / n for y in YEARS}
        for y in YEARS:
            out[f"{label}_built_{y}"] = round(built_share[y], 4)
        out[f"{label}_first_built_year"] = first_built_year(built_share)
        for code, name in CLASSES.items():
            out[f"{label}_{name}_2017_ha"] = float(np.count_nonzero(mask & (stack[2017] == code))) * PIXEL_HA
        for key, value in transition_table(stack[2017], stack[2023], mask).items():
            out[f"{label}_{key}"] = value
        # Sensitivity: also count land that is bare in the last year (cleared, under construction).
        cleared = mask & np.isin(stack[2017], [1, 2, 4, 5, 11]) & np.isin(stack[2023], [BUILT, 8])
        out[f"{label}_cleared_or_built_ha"] = float(np.count_nonzero(cleared)) * PIXEL_HA
    return out


def analyse_all(sites: gpd.GeoDataFrame, workers: int = 12, buffer_m: float = 100.0) -> pd.DataFrame:
    """Run :func:`analyse_site` for every site, in threads. Failures are recorded, not dropped."""
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyse_site, row, buffer_m): row.site_id for row in sites.itertuples()}
        for i, future in enumerate(as_completed(futures), 1):
            site_id = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - keep going, report at the end
                results.append({"site_id": site_id, "error": str(exc)[:200]})
            if i % 200 == 0:
                print(f"  {i}/{len(futures)} sites", flush=True)
    return pd.DataFrame(results).sort_values("site_id").reset_index(drop=True)


def class_areas(arr: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    """Hectares of each class inside a mask."""
    return {name: float(np.count_nonzero(mask & (arr == code))) * PIXEL_HA for code, name in CLASSES.items()}


def site_vs_surroundings(row, year: int, ring_m: float = 5000.0) -> dict:
    """Land cover inside the footprint against a ring around it, for one year.

    The ring (from the footprint edge out to ``ring_m``) shows what land was
    available nearby, so a footprint that is mostly cropland in a landscape
    that is mostly forest tells a different story from one in a farm belt.
    """
    hrefs = _hrefs_for(row.lon, row.lat)
    if year not in hrefs:
        raise RuntimeError(f"no land cover for {year}")
    with rasterio.open(pc.sign(hrefs[year])) as src:
        geom = gpd.GeoSeries([row.geometry], crs="EPSG:4326").to_crs(src.crs).iloc[0]
        minx, miny, maxx, maxy = geom.buffer(ring_m).bounds
        window = from_bounds(minx, miny, maxx, maxy, src.transform).round_offsets().round_lengths()
        transform = src.window_transform(window)
        arr = src.read(1, window=window, boundless=True, fill_value=0)
    fp = geometry_mask([geom], out_shape=arr.shape, transform=transform, invert=True)
    ring = geometry_mask([geom.buffer(ring_m)], out_shape=arr.shape, transform=transform, invert=True) & ~fp
    ring &= arr != 0
    out = {"site_id": row.site_id, "year": year}
    for label, mask in (("fp", fp), ("ring", ring)):
        for name, ha in class_areas(arr, mask).items():
            out[f"{label}_{name}_ha"] = ha
    return out


def surroundings_all(sites: gpd.GeoDataFrame, year: int, workers: int = 12, ring_m: float = 5000.0) -> pd.DataFrame:
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(site_vs_surroundings, r, year, ring_m): r.site_id for r in sites.itertuples()}
        for future in as_completed(futures):
            try:
                rows.append(future.result())
            except Exception as exc:  # noqa: BLE001
                rows.append({"site_id": futures[future], "year": year, "error": str(exc)[:200]})
    return pd.DataFrame(rows).sort_values("site_id").reset_index(drop=True)
