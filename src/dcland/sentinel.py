"""Has construction started since 2023? Sentinel-2 check for pipeline sites.

For each site, build a cloud-masked median of the clearest Sentinel-2 L2A
scenes in the growing season of 2023 and of 2026, on one MGRS tile so both
years share a pixel grid. Two measures inside the footprint:

* vegetated share: NDVI >= 0.4
* changed share: |dNDVI| > 0.2 or |d red reflectance| > 0.05, which also
  catches clearing and building on desert or already bare ground
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
BAD_SCL = [0, 1, 3, 8, 9, 10, 11]  # no data, saturated, shadow, cloud, cirrus, snow
VEG_NDVI = 0.4
MAX_SCENES = 5


def season(year: int, lat: float) -> str:
    """Date range of the main growing season for a latitude."""
    if lat >= 23.5:
        return f"{year}-06-01/{year}-09-30"
    if lat <= -23.5:
        return f"{year - 1}-12-01/{year}-03-31"
    return f"{year}-01-01/{year}-12-31"


def _items(catalog, lon, lat, year, tile=None):
    query = {"eo:cloud_cover": {"lt": 20}}
    if tile:
        query["s2:mgrs_tile"] = {"eq": tile}
    items = catalog.search(
        collections=["sentinel-2-l2a"],
        intersects={"type": "Point", "coordinates": [lon, lat]},
        datetime=season(year, lat),
        query=query,
    ).items()
    return sorted(items, key=lambda i: i.properties["eo:cloud_cover"])


def _median(items, geom_utm, pad=30):
    reds, nirs, transform, shape = [], [], None, None
    for item in items[:MAX_SCENES]:
        bands = {}
        for name in ("B04", "B08", "SCL"):
            with rasterio.open(pc.sign(item.assets[name].href)) as src:
                if name == "B04":
                    minx, miny, maxx, maxy = geom_utm.bounds
                    win = from_bounds(minx - pad, miny - pad, maxx + pad, maxy + pad, src.transform)
                    win = win.round_offsets().round_lengths()
                    transform, shape = src.window_transform(win), (int(win.height), int(win.width))
                    bands[name] = src.read(1, window=win, boundless=True, fill_value=0).astype("float32")
                else:
                    # SCL is 20 m: read at the 10 m window size.
                    w20 = from_bounds(*rasterio.transform.array_bounds(*shape, transform), src.transform)
                    bands[name] = src.read(1, window=w20, out_shape=shape, boundless=True, fill_value=0, resampling=Resampling.nearest)
        bad = np.isin(bands["SCL"], BAD_SCL) | (bands["B04"] == 0)
        # L2A processing baseline >= 04.00 adds an offset of 1000 to reflectance.
        offset = 1000.0 if item.datetime.year >= 2022 else 0.0
        red = (bands["B04"] - offset) / 10000.0
        nir = (bands["B08"] - offset) / 10000.0
        red[bad] = np.nan
        nir[bad] = np.nan
        reds.append(red)
        nirs.append(nir)
    with np.errstate(all="ignore"):
        red = np.nanmedian(np.stack(reds), axis=0)
        nir = np.nanmedian(np.stack(nirs), axis=0)
        ndvi = (nir - red) / (nir + red)
    return red, ndvi, transform, shape


def check_site(row, years=(2023, 2026)) -> dict:
    catalog = pystac_client.Client.open(STAC_URL)
    first = _items(catalog, row.lon, row.lat, years[0])
    if not first:
        raise RuntimeError("no clear scenes in first year")
    tile = first[0].properties["s2:mgrs_tile"]
    second = _items(catalog, row.lon, row.lat, years[1], tile)
    if not second:
        raise RuntimeError("no clear scenes in second year")
    epsg = first[0].properties.get("proj:epsg") or int(str(first[0].properties["proj:code"]).split(":")[1])
    geom = gpd.GeoSeries([row.geometry], crs="EPSG:4326").to_crs(epsg).iloc[0]
    red0, ndvi0, transform, shape = _median([i for i in first if i.properties["s2:mgrs_tile"] == tile], geom)
    red1, ndvi1, _, _ = _median(second, geom)
    mask = geometry_mask([geom], out_shape=shape, transform=transform, invert=True)
    valid = mask & np.isfinite(ndvi0) & np.isfinite(ndvi1)
    n = int(valid.sum())
    if n == 0:
        raise RuntimeError("no clear pixels in footprint")
    veg0 = ndvi0 >= VEG_NDVI
    veg1 = ndvi1 >= VEG_NDVI
    changed = (np.abs(ndvi1 - ndvi0) > 0.2) | (np.abs(red1 - red0) > 0.05)
    return {
        "site_id": row.site_id,
        "s2_tile": tile,
        "s2_valid_share": round(n / int(mask.sum()), 3),
        f"veg_share_{years[0]}": round(float(np.count_nonzero(valid & veg0)) / n, 3),
        f"veg_share_{years[1]}": round(float(np.count_nonzero(valid & veg1)) / n, 3),
        "cleared_share": round(float(np.count_nonzero(valid & veg0 & ~veg1)) / n, 3),
        "changed_share": round(float(np.count_nonzero(valid & changed)) / n, 3),
    }


def check_all(sites: gpd.GeoDataFrame, workers: int = 8) -> pd.DataFrame:
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(check_site, r): r.site_id for r in sites.itertuples()}
        for future in as_completed(futures):
            try:
                rows.append(future.result())
            except Exception as exc:  # noqa: BLE001
                rows.append({"site_id": futures[future], "error": str(exc)[:200]})
    return pd.DataFrame(rows).sort_values("site_id").reset_index(drop=True)
