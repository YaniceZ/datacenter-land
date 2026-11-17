"""Check the pre-construction land class against national reference maps.

For pixels that the global product says changed from not built (2017) to
built (2023), look up what an independent national map called the same
ground before construction:

* United States: USDA Cropland Data Layer 2017 (30 m)
* Canada: NRCan Land Cover of Canada 2015 (30 m)

Both are resampled (nearest neighbour) onto the 10 m grid of the global product.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import Resampling, reproject

from . import landcover as lc

GROUPS = ["crops", "trees", "rangeland", "wetland", "water", "bare", "built"]

# Global product code -> comparison group.
ESRI_GROUP = {1: "water", 2: "trees", 4: "wetland", 5: "crops", 7: "built", 8: "bare", 11: "rangeland"}


def cdl_group(code: int) -> str | None:
    """USDA Cropland Data Layer code -> comparison group."""
    if code == 0:
        return None
    if code in (111,):
        return "water"
    if code in (121, 122, 123, 124):
        return "built"
    if code == 131:
        return "bare"
    if code in (141, 142, 143, 63):
        return "trees"
    if code in (152, 176, 64):
        return "rangeland"
    if code in (190, 195, 87):
        return "wetland"
    if code in (112, 92):
        return None
    return "crops"  # field crops, hay, fallow, orchards


def nrcan_group(code: int) -> str | None:
    """NRCan Land Cover of Canada code -> comparison group."""
    if code in (1, 2, 5, 6):
        return "trees"
    if code in (8, 10, 11, 12, 13):
        return "rangeland"
    if code == 14:
        return "wetland"
    if code == 15:
        return "crops"
    if code == 16:
        return "bare"
    if code == 17:
        return "built"
    if code == 18:
        return "water"
    return None


REFERENCES = {
    "USA": {"collection": "usda-cdl", "asset": "cropland", "datetime": "2017-01-01/2017-12-31", "group": cdl_group},
    "CAN": {"collection": "nrcan-landcover", "asset": "landcover", "datetime": "2015-01-01/2015-12-31", "group": nrcan_group},
}


def crosstab(esri_2017: np.ndarray, reference: np.ndarray, converted: np.ndarray, group_fn) -> pd.DataFrame:
    """Hectares of converted land by global-product group (rows) and reference group (columns)."""
    table = pd.DataFrame(0.0, index=GROUPS, columns=GROUPS)
    e = esri_2017[converted]
    r = reference[converted]
    for e_code, r_code in zip(*np.unique(np.stack([e, r]), axis=1), strict=False):
        e_group, r_group = ESRI_GROUP.get(int(e_code)), group_fn(int(r_code))
        if e_group is None or r_group is None:
            continue
        n = np.count_nonzero((e == e_code) & (r == r_code))
        table.loc[e_group, r_group] += n * lc.PIXEL_HA
    return table


def _reference_window(ref_cfg: dict, geom_wgs84, shape, transform, crs) -> np.ndarray | None:
    catalog = pystac_client.Client.open(lc.STAC_URL)
    centroid = geom_wgs84.representative_point()
    items = list(
        catalog.search(
            collections=[ref_cfg["collection"]],
            intersects={"type": "Point", "coordinates": [centroid.x, centroid.y]},
            datetime=ref_cfg["datetime"],
        ).items()
    )
    items = [i for i in items if ref_cfg["asset"] in i.assets]
    if not items:
        return None
    out = np.zeros(shape, dtype="uint8")
    with rasterio.open(pc.sign(items[0].assets[ref_cfg["asset"]].href)) as src:
        reproject(
            rasterio.band(src, 1),
            out,
            dst_transform=transform,
            dst_crs=crs,
            resampling=Resampling.nearest,
        )
    return out


def validate_site(row, iso3: str) -> pd.DataFrame | None:
    ref_cfg = REFERENCES[iso3]
    stack, transform, crs, geom = lc._read_stack(row.geometry, row.lon, row.lat, pad_m=40)
    first, last = stack[2017], stack[2023]
    mask = geometry_mask([geom], out_shape=first.shape, transform=transform, invert=True)
    converted = mask & (first != lc.BUILT) & (first != 0) & (last == lc.BUILT)
    if not converted.any():
        return None
    reference = _reference_window(ref_cfg, row.geometry, first.shape, transform, crs)
    if reference is None:
        return None
    return crosstab(first, reference, converted, ref_cfg["group"])


def validate_country(sites: gpd.GeoDataFrame, iso3: str, workers: int = 8) -> tuple[pd.DataFrame, int]:
    """Sum the cross-tabulation over all sites of one country. Returns the table and the number of sites used."""
    rows = list(sites[sites.iso3 == iso3].itertuples())

    def safe(row):
        try:
            return validate_site(row, iso3)
        except Exception:  # noqa: BLE001 - a missing window should not stop the run
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        tables = [t for t in pool.map(safe, rows) if t is not None]
    total = pd.DataFrame(0.0, index=GROUPS, columns=GROUPS)
    for t in tables:
        total += t
    return total, len(tables)


def agreement(table: pd.DataFrame) -> dict:
    """Overall agreement and the reference-based share of each group."""
    total = table.to_numpy().sum()
    if total == 0:
        return {"hectares": 0.0}
    return {
        "hectares": round(float(total), 1),
        "overall_agreement": round(float(np.trace(table.to_numpy()) / total), 3),
        "global_product_share": (table.sum(axis=1) / total).round(3).to_dict(),
        "reference_share": (table.sum(axis=0) / total).round(3).to_dict(),
    }
