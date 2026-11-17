"""Command line entry point: ``dcland <step>``."""

from __future__ import annotations

import argparse
from pathlib import Path

DATA = Path("data")
RESULTS = Path("results")
DOCS = Path("docs")
GHSL = DATA / "raw" / "ghsl"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dcland", description="Land taken by data centers, from open data.")
    parser.add_argument("step", choices=["sites", "context", "landcover", "surroundings", "validate", "sentinel",
                                         "summary", "map", "all"])
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args(argv)
    steps = ["sites", "context", "landcover", "surroundings", "validate", "sentinel", "summary", "map"]
    for step in steps if args.step == "all" else [args.step]:
        print(f"== {step}", flush=True)
        globals()[f"_{step}"](args.workers)


def _sites(workers):
    from . import osm
    raw = osm.fetch(DATA / "raw" / "osm_datacenters.json")
    osm.to_sites(osm.to_features(raw)).to_file(DATA / "sites_all.gpkg", driver="GPKG")


def _context(workers):
    import geopandas as gpd

    from . import context
    s = gpd.read_file(DATA / "sites_all.gpkg")
    s = context.add_country(s, DATA / "raw" / "ne" / "ne_50m_admin_0_countries.shp")
    s = context.add_settlement(s, GHSL / "GHS_SMOD_E2020_GLOBE_R2023A_54009_1000_V2_0.tif",
                               GHSL / "GHS_POP_E2020_GLOBE_R2023A_54009_1000_V1_0.tif")
    s.to_file(DATA / "sites_context.gpkg", driver="GPKG")


def _landcover(workers):
    import geopandas as gpd

    from . import classify, landcover
    s = gpd.read_file(DATA / "sites_context.gpkg")
    lc = landcover.analyse_all(s, workers=workers)
    lc.to_csv(DATA / "landcover_by_site.csv", index=False)
    classify.label(s.merge(lc, on="site_id")).to_file(DATA / "sites_labelled.gpkg", driver="GPKG")


def _surroundings(workers):
    import geopandas as gpd
    import pandas as pd

    from . import landcover
    m = gpd.read_file(DATA / "sites_labelled.gpkg")
    a = landcover.surroundings_all(m[m.status == "new"], 2017, workers=workers)
    b = landcover.surroundings_all(m[m.status == "pipeline"], 2023, workers=workers)
    pd.concat([a, b]).to_csv(DATA / "surroundings.csv", index=False)


def _validate(workers):
    import json

    import geopandas as gpd

    from . import validate
    s = gpd.read_file(DATA / "sites_labelled.gpkg")
    s = s[s.fp_conv_total_ha >= 0.5]
    out = {}
    for iso in ["USA", "CAN"]:
        table, n = validate.validate_country(s, iso, workers=min(workers, 8))
        table.to_csv(RESULTS / f"validation_{iso}.csv")
        out[iso] = {"sites": n, **validate.agreement(table)}
    (RESULTS / "validation.json").write_text(json.dumps(out, indent=2))


def _sentinel(workers):
    import geopandas as gpd
    import pandas as pd

    from . import sentinel
    m = gpd.read_file(DATA / "sites_labelled.gpkg")
    control = m[(m.status == "existing") & (m.area_ha >= 1)].sample(60, random_state=7)
    p = sentinel.check_all(m[m.status == "pipeline"], workers=min(workers, 8)).assign(group="pipeline")
    c = sentinel.check_all(control, workers=min(workers, 8)).assign(group="existing control")
    pd.concat([p, c]).to_csv(DATA / "sentinel_check.csv", index=False)


def _summary(workers):
    from . import analysis
    analysis.summarise(DATA, RESULTS)
    analysis.make_figures(DATA, RESULTS, DOCS)
    analysis.export_sites(DATA, RESULTS)


def _map(workers):
    from . import webmap
    webmap.build(RESULTS / "datacenter_sites.geojson", DOCS / "map.html")


if __name__ == "__main__":
    main()
