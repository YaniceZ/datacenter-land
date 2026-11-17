"""Offline tests: no network, small synthetic inputs."""

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from dcland import classify, context, landcover, osm, validate


def test_transition_table_counts_only_new_built():
    first = np.array([[5, 5, 2, 7], [11, 8, 1, 0]])
    last = np.array([[7, 5, 7, 7], [7, 7, 7, 7]])
    mask = np.ones_like(first, dtype=bool)
    t = landcover.transition_table(first, last, mask)
    assert t["conv_crops_ha"] == 0.01
    assert t["conv_trees_ha"] == 0.01
    assert t["conv_rangeland_ha"] == 0.01
    assert t["conv_bare_ha"] == 0.01
    assert t["conv_water_ha"] == 0.01
    assert t["conv_total_ha"] == 0.05  # the already built and the no data pixel are excluded


def test_transition_table_respects_mask():
    first = np.full((2, 2), 5)
    last = np.full((2, 2), 7)
    mask = np.array([[True, False], [False, False]])
    assert landcover.transition_table(first, last, mask)["conv_total_ha"] == 0.01


def test_first_built_year_requires_staying_built():
    assert landcover.first_built_year({2017: 0.1, 2018: 0.6, 2019: 0.2, 2020: 0.7, 2021: 0.8}) == 2020
    assert landcover.first_built_year({2017: 0.1, 2018: 0.2}) is None
    assert landcover.first_built_year({2017: 0.9, 2018: 0.9}) == 2017


def test_site_status():
    assert classify.site_status(0.6, 0.9, 0.0, 3) == "existing"
    assert classify.site_status(0.0, 0.8, 5.0, 6) == "new"
    assert classify.site_status(0.0, 0.05, 0.0, 40) == "pipeline"
    assert classify.site_status(0.0, 0.05, 0.0, 0.3) == "small or unclear"
    assert classify.site_status(np.nan, 0.5, 0, 1) == "no data"


def test_crypto_flag():
    assert classify.is_crypto("Riot Corsicana", None)
    assert classify.is_crypto(None, "Core Scientific")
    assert not classify.is_crypto("Google Data Center", "Google")
    assert not classify.is_crypto(None, None)


def test_disc_offsets_radius():
    offs = context.disc_offsets(2)
    assert (0, 0) in offs and (2, 0) in offs and (2, 2) not in offs
    assert len(offs) == 13


def _square(x, y, d=0.001):
    return Polygon([(x, y), (x + d, y), (x + d, y + d), (x, y + d)])


def test_to_sites_merges_neighbours_and_keeps_far_apart():
    f = gpd.GeoDataFrame(
        {
            "name": ["A", None, "B"],
            "operator": [None, "Op", None],
            "start_date": [None, None, None],
            "osm_id": ["way/1", "way/2", "way/3"],
        },
        geometry=[_square(10, 50), _square(10.0012, 50), _square(11, 50)],
        crs="EPSG:4326",
    )
    s = osm.to_sites(f, merge_distance_m=150)
    assert len(s) == 2
    merged = s[s.n_features == 2].iloc[0]
    assert merged["name"] == "A" and merged["operator"] == "Op"


def test_way_polygon_rejects_open_ring():
    open_way = {"geometry": [{"lon": 0, "lat": 0}, {"lon": 1, "lat": 0}, {"lon": 1, "lat": 1}]}
    assert osm._way_polygon(open_way) is None


def test_reference_group_mappings():
    assert validate.cdl_group(1) == "crops"  # corn
    assert validate.cdl_group(141) == "trees"
    assert validate.cdl_group(122) == "built"
    assert validate.cdl_group(0) is None
    assert validate.nrcan_group(15) == "crops"
    assert validate.nrcan_group(17) == "built"


def test_crosstab_and_agreement():
    esri = np.array([5, 5, 2, 11])
    ref = np.array([1, 141, 141, 176])  # corn, forest, forest, grassland
    conv = np.array([True, True, True, False])
    t = validate.crosstab(esri, ref, conv, validate.cdl_group)
    assert t.loc["crops", "crops"] == 0.01
    assert t.loc["crops", "trees"] == 0.01
    assert t.loc["trees", "trees"] == 0.01
    a = validate.agreement(t)
    assert a["overall_agreement"] == round(2 / 3, 3)


def test_season_by_hemisphere():
    from dcland import sentinel
    assert sentinel.season(2026, 45).startswith("2026-06")
    assert sentinel.season(2026, -33).startswith("2025-12")
    assert sentinel.season(2026, 1) == "2026-01-01/2026-12-31"


def test_bootstrap_ci_contains_point_estimate():
    from dcland import stats
    df = pd.DataFrame({
        "fp_conv_crops_ha": [5.0, 1.0, 0.0, 3.0],
        "fp_conv_trees_ha": [0.0, 2.0, 4.0, 1.0],
        "fp_conv_rangeland_ha": [1.0, 1.0, 1.0, 1.0],
        "fp_conv_bare_ha": [0.0, 0.0, 1.0, 0.0],
    })
    df["fp_conv_total_ha"] = df.sum(axis=1)
    out = stats.conversion_shares(df)
    assert out["crops"]["share"] == round(9 / 21, 3)
    lo, hi = out["crops"]["ci95"]
    assert lo <= out["crops"]["share"] <= hi


def test_relabel_restores_threshold():
    from dcland import stats
    df = pd.DataFrame({"fp_built_2017": [0.2], "fp_built_2023": [0.9], "fp_conv_total_ha": [3.0], "area_ha": [4.0]})
    assert stats.relabel(df, 0.10).iloc[0] == "existing"
    assert stats.relabel(df, 0.25).iloc[0] == "new"
    assert classify.BUILT_SHARE == 0.25
