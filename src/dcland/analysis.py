"""Headline numbers (results/summary.json) and the public per-campus dataset."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

UNDEVELOPED = ["crops", "trees", "rangeland", "bare", "flooded_vegetation"]


def load(data: Path) -> pd.DataFrame:
    sites = gpd.read_file(data / "sites_labelled.gpkg")
    return pd.DataFrame(sites.drop(columns="geometry"))


def converted_shares(df: pd.DataFrame) -> dict:
    cols = {c: f"fp_conv_{c}_ha" for c in ["crops", "trees", "rangeland", "bare", "water", "flooded_vegetation"]}
    totals = {k: float(df[v].sum()) for k, v in cols.items()}
    total = float(df["fp_conv_total_ha"].sum())
    return {"hectares": round(total, 1), "share": {k: round(v / total, 3) for k, v in totals.items()}}


def selection_test(surr: pd.DataFrame, status: str) -> dict:
    """Paired comparison of footprint and 5 km ring, both as shares of undeveloped land."""
    x = surr[surr.status == status]
    fp_u = x[[f"fp_{c}_ha" for c in UNDEVELOPED]].sum(axis=1)
    ring_u = x[[f"ring_{c}_ha" for c in UNDEVELOPED]].sum(axis=1)
    ok = fp_u > 0.5
    out = {"sites": int(ok.sum())}
    for c in ["crops", "trees", "rangeland"]:
        a = (x[f"fp_{c}_ha"] / fp_u)[ok]
        b = (x[f"ring_{c}_ha"] / ring_u)[ok]
        diff = a - b
        out[c] = {
            "footprint_mean": round(float(a.mean()), 3),
            "ring_mean": round(float(b.mean()), 3),
            "median_difference": round(float(diff.median()), 3),
            "wilcoxon_p": float(f"{wilcoxon(diff).pvalue:.3g}"),
        }
    return out


def summarise(data: Path, results: Path) -> dict:
    df = load(data)
    surr = pd.read_csv(data / "surroundings.csv").merge(df[["site_id", "status"]], on="site_id")
    val = json.loads((results / "validation.json").read_text())
    new = df[df.status == "new"]
    df[df.status == "existing"]
    pipe = df[df.status == "pipeline"]

    summary = {
        "osm_sites": len(df),
        "sites_by_status": df.status.value_counts().to_dict(),
        "crypto_flagged": int(df.crypto.sum()),
        "new_sites_2018_2023": {
            "sites": len(new),
            **converted_shares(new),
            "by_year": new.groupby("fp_first_built_year").agg(sites=("site_id", "size"), ha=("fp_conv_total_ha", "sum"))
            .round(1)
            .reset_index()
            .assign(fp_first_built_year=lambda d: d.fp_first_built_year.astype(int))
            .to_dict("records"),
            "by_continent": new.groupby("continent").agg(sites=("site_id", "size"), ha=("fp_conv_total_ha", "sum"))
            .round(1)
            .to_dict("index"),
            "us_share_of_hectares": round(float(new[new.iso3 == "USA"].fp_conv_total_ha.sum() / new.fp_conv_total_ha.sum()), 3),
        },
        "rural_share": {s: round(float((g.settlement == "rural").mean()), 3) for s, g in df.groupby("status")},
        "median_area_ha": {s: round(float(g.area_ha.median()), 2) for s, g in df.groupby("status")},
        "median_population_5km": {s: int(g.pop_5km.median()) for s, g in df.groupby("status")},
        "selection_vs_5km_ring": {"new (2017 land cover)": selection_test(surr, "new"),
                                  "pipeline (2023 land cover)": selection_test(surr, "pipeline")},
        "validation": val,
        "pipeline": {"sites": len(pipe), "area_ha": round(float(pipe.area_ha.sum()), 1),
                     "area_ha_without_largest": round(float(pipe.area_ha.sum() - pipe.area_ha.max()), 1),
                     "largest": pipe.sort_values("area_ha").iloc[-1][["name", "country", "area_ha"]].to_dict()},
    }

    s2_path = data / "sentinel_check.csv"
    if s2_path.exists():
        s2 = pd.read_csv(s2_path)
        ok = s2[s2.get("error").isna()] if "error" in s2 else s2
        control = ok[ok.group == "existing control"]
        threshold = float(control.changed_share.quantile(0.9))
        p = ok[ok.group == "pipeline"].merge(df[["site_id", "area_ha"]], on="site_id")
        started = p[(p.changed_share > threshold) | (p.cleared_share >= 0.25)]
        summary["pipeline"]["sentinel2_2023_vs_2026"] = {
            "checked": len(p),
            "failed": int((s2.group == "pipeline").sum() - len(p)),
            "control_sites": len(control),
            "control_changed_share_median": round(float(control.changed_share.median()), 3),
            "control_changed_share_p90": round(threshold, 3),
            "started_sites": len(started),
            "started_area_ha": round(float(started.area_ha.sum()), 1),
            "cleared_vegetation_ha": round(float((p.cleared_share * p.area_ha).sum()), 1),
        }
    (results / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def export_sites(data: Path, results: Path) -> None:
    """Public per-site table: the dataset other people can reuse."""
    sites = gpd.read_file(data / "sites_labelled.gpkg")
    keep = ["site_id", "name", "operator", "country", "iso3", "continent", "status", "crypto", "area_ha", "lon", "lat",
            "settlement", "pop_5km", "fp_first_built_year", "fp_conv_total_ha", "fp_conv_crops_ha", "fp_conv_trees_ha",
            "fp_conv_rangeland_ha", "fp_conv_bare_ha"] + [f"fp_built_{y}" for y in range(2017, 2024)] + ["osm_ids"]
    out = sites[keep + ["geometry"]].copy()
    for c in ["area_ha", "fp_conv_total_ha", "fp_conv_crops_ha", "fp_conv_trees_ha", "fp_conv_rangeland_ha", "fp_conv_bare_ha"]:
        out[c] = out[c].round(2)
    s2_path = data / "sentinel_check.csv"
    if s2_path.exists():
        s2 = pd.read_csv(s2_path)
        s2 = s2[s2.group == "pipeline"].drop(columns=["group", "error"], errors="ignore")
        out = out.merge(s2.drop(columns=["s2_tile"], errors="ignore"), on="site_id", how="left")
    out.to_file(results / "datacenter_sites.geojson", driver="GeoJSON")
    out.drop(columns="geometry").to_csv(results / "datacenter_sites.csv", index=False)


def counts_check(df: pd.DataFrame) -> bool:
    """Every site gets exactly one status."""
    return bool(np.all(df.status.notna()))
