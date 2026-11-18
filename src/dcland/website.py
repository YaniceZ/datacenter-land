"""Small data files for the project web page (docs/index.html)."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

STATUS_CODE = {"existing": 0, "new": 1, "pipeline": 2}  # anything else -> 3 (unclassified)

# Same rule as the paper: changed more than the 90th percentile of controls, or a quarter cleared.
FIELDS = ["lon", "lat", "status", "area_ha", "name", "operator", "country", "conv_ha", "crops_ha", "trees_ha",
          "rangeland_ha", "pop_5km", "first_year", "changed", "cleared", "started", "osm"]


def _num(v, digits=1):
    return None if pd.isna(v) else round(float(v), digits)


def export(results: Path, out_dir: Path, started_threshold: float) -> dict:
    sites = gpd.read_file(results / "datacenter_sites.geojson")
    rows = []
    for r in sites.itertuples():
        changed = _num(r.changed_share, 2)
        cleared = _num(r.cleared_share, 2)
        started = None
        if r.status == "pipeline" and changed is not None:
            started = int(changed > started_threshold or cleared >= 0.25)
        rows.append([
            round(r.lon, 4), round(r.lat, 4), STATUS_CODE.get(r.status, 3), _num(r.area_ha, 2),
            r.name if isinstance(r.name, str) else None, r.operator if isinstance(r.operator, str) else None,
            r.country, _num(r.fp_conv_total_ha), _num(r.fp_conv_crops_ha), _num(r.fp_conv_trees_ha),
            _num(r.fp_conv_rangeland_ha), None if pd.isna(r.pop_5km) else int(r.pop_5km),
            None if pd.isna(r.fp_first_built_year) else int(r.fp_first_built_year), changed, cleared, started,
            r.osm_ids.split(";")[0],
        ])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sites.json").write_text(json.dumps({"fields": FIELDS, "rows": rows}, separators=(",", ":")),
                                        encoding="utf-8")

    # Footprint outlines for close zoom: simplified to about 2 m, coordinates to 5 decimals (~1 m).
    fp = sites[["site_id", "status", "geometry"]].copy()
    fp["s"] = fp.status.map(STATUS_CODE).fillna(3).astype(int)
    fp = fp.to_crs("ESRI:54034")
    fp["geometry"] = fp.geometry.simplify(2.0, preserve_topology=True)
    fp = fp.to_crs("EPSG:4326")[["s", "geometry"]]
    (out_dir / "footprints.geojson").write_text(fp.to_json(drop_id=True, to_wgs84=False, show_bbox=False)
                                                .replace(" ", ""), encoding="utf-8")
    gj = json.loads((out_dir / "footprints.geojson").read_text(encoding="utf-8"))

    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], list) else [round(c[0], 5), round(c[1], 5)]

    for f in gj["features"]:
        f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
    (out_dir / "footprints.geojson").write_text(json.dumps(gj, separators=(",", ":")), encoding="utf-8")
    return {p.name: p.stat().st_size for p in out_dir.iterdir()}
