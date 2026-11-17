"""Country, settlement type and nearby population for each site."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.windows import Window

# GHSL Degree of Urbanisation (SMOD) level-2 codes, grouped for reporting.
SMOD_GROUPS = {
    30: "urban centre",
    23: "dense or semi-dense town",
    22: "dense or semi-dense town",
    21: "suburban or peri-urban",
    13: "rural",
    12: "rural",
    11: "rural",
    10: "water",
}


def add_country(sites: gpd.GeoDataFrame, countries_shp: Path) -> gpd.GeoDataFrame:
    """Attach country name, ISO code and continent using the nearest Natural Earth polygon."""
    countries = gpd.read_file(countries_shp)[["ADMIN", "ADM0_A3", "CONTINENT", "geometry"]]
    countries = countries.rename(columns={"ADMIN": "country", "ADM0_A3": "iso3", "CONTINENT": "continent"})
    points = gpd.GeoDataFrame(sites[["site_id"]], geometry=gpd.points_from_xy(sites.lon, sites.lat), crs="EPSG:4326")
    # Nearest join in an equal-area projection so coastal sites still get a country.
    joined = gpd.sjoin_nearest(points.to_crs("ESRI:54034"), countries.to_crs("ESRI:54034"), max_distance=50_000, how="left")
    joined = joined.drop_duplicates("site_id").set_index("site_id")
    out = sites.copy()
    for col in ["country", "iso3", "continent"]:
        out[col] = out["site_id"].map(joined[col])
    return out


def disc_offsets(radius_cells: int) -> list[tuple[int, int]]:
    """Row and column offsets of the cells whose centre lies within a radius, in cell units."""
    r = radius_cells
    return [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if dy * dy + dx * dx <= r * r]


def add_settlement(sites: gpd.GeoDataFrame, smod_tif: Path, pop_tif: Path, radius_km: int = 5) -> gpd.GeoDataFrame:
    """Attach the GHSL settlement class at the site and the 2020 population within ``radius_km``.

    Both grids are 1 km in Mollweide, so the population figure is a coarse
    neighbourhood total, not a count of immediate neighbours.
    """
    out = sites.copy()
    offsets = disc_offsets(radius_km)
    with rasterio.open(smod_tif) as smod, rasterio.open(pop_tif) as pop:
        pts = gpd.GeoSeries(gpd.points_from_xy(sites.lon, sites.lat), crs="EPSG:4326").to_crs(smod.crs)
        smod_codes, pops = [], []
        for pt in pts:
            row, col = smod.index(pt.x, pt.y)
            code = int(smod.read(1, window=Window(col, row, 1, 1), boundless=True, fill_value=10)[0, 0])
            smod_codes.append(code)
            prow, pcol = pop.index(pt.x, pt.y)
            r = radius_km
            block = pop.read(1, window=Window(pcol - r, prow - r, 2 * r + 1, 2 * r + 1), boundless=True, fill_value=0)
            block = np.where(block < 0, 0, block)
            pops.append(float(sum(block[dy + r, dx + r] for dy, dx in offsets)))
    out["smod_code"] = smod_codes
    out["settlement"] = [SMOD_GROUPS.get(c, "unknown") for c in smod_codes]
    out[f"pop_{radius_km}km"] = np.round(pops).astype(int)
    return out
