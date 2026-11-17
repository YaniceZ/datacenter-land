"""Uncertainty and robustness: site-level bootstrap intervals and sensitivity analyses."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

from . import classify

CLASSES = ["crops", "trees", "rangeland", "bare"]
UNDEVELOPED = ["crops", "trees", "rangeland", "bare", "flooded_vegetation"]
N_BOOT = 5000
SEED = 20261117


def _ci(samples: np.ndarray) -> list[float]:
    lo, hi = np.nanpercentile(samples, [2.5, 97.5])
    return [round(float(lo), 3), round(float(hi), 3)]


def bootstrap(values: np.ndarray, stat, n: int = N_BOOT, seed: int = SEED) -> np.ndarray:
    """Resample rows (sites) with replacement and return the statistic for each replicate."""
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(values), size=(n, len(values)))
    return np.array([stat(values[i]) for i in idx])


def conversion_shares(df: pd.DataFrame) -> dict:
    """Area-weighted share of converted land by previous class, with 95% site-bootstrap intervals."""
    cols = [f"fp_conv_{c}_ha" for c in CLASSES]
    arr = df[cols + ["fp_conv_total_ha"]].to_numpy(dtype=float)
    out = {"sites": len(df), "hectares": round(float(arr[:, -1].sum()), 1)}
    for i, c in enumerate(CLASSES):
        point = arr[:, i].sum() / arr[:, -1].sum()
        boots = bootstrap(arr, lambda a, i=i: a[:, i].sum() / a[:, -1].sum())
        out[c] = {"share": round(float(point), 3), "ci95": _ci(boots)}
    return out


def proportion(mask: np.ndarray) -> dict:
    mask = np.asarray(mask, dtype=float)
    return {"share": round(float(mask.mean()), 3), "ci95": _ci(bootstrap(mask, np.mean)), "n": len(mask)}


def median_ci(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=float)
    return {"median": round(float(np.median(values)), 2), "ci95": _ci(bootstrap(values, np.median)), "n": len(values)}


def selection(surr: pd.DataFrame) -> dict:
    """Footprint minus ring share of undeveloped land, per site: mean difference, bootstrap CI, Wilcoxon p."""
    fp_u = surr[[f"fp_{c}_ha" for c in UNDEVELOPED]].sum(axis=1)
    ring_u = surr[[f"ring_{c}_ha" for c in UNDEVELOPED]].sum(axis=1)
    ok = (fp_u > 0.5).to_numpy()
    out = {"sites": int(ok.sum())}
    for c in ["crops", "trees", "rangeland"]:
        a = (surr[f"fp_{c}_ha"] / fp_u).to_numpy()[ok]
        b = (surr[f"ring_{c}_ha"] / ring_u).to_numpy()[ok]
        d = a - b
        # Area-weighted version: pooled footprint share minus pooled ring share.
        fa, ra = surr[f"fp_{c}_ha"].to_numpy()[ok], surr[f"ring_{c}_ha"].to_numpy()[ok]
        fu, ru = fp_u.to_numpy()[ok], ring_u.to_numpy()[ok]
        stack = np.column_stack([fa, fu, ra, ru])
        pooled = bootstrap(stack, lambda s: s[:, 0].sum() / s[:, 1].sum() - s[:, 2].sum() / s[:, 3].sum())
        out[c] = {
            "footprint_mean": round(float(a.mean()), 3),
            "ring_mean": round(float(b.mean()), 3),
            "mean_difference": round(float(d.mean()), 3),
            "mean_difference_ci95": _ci(bootstrap(d, np.mean)),
            "wilcoxon_p": float(f"{wilcoxon(d).pvalue:.2g}"),
            "pooled_difference": round(float(fa.sum() / fu.sum() - ra.sum() / ru.sum()), 3),
            "pooled_difference_ci95": _ci(pooled),
        }
    return out


def relabel(df: pd.DataFrame, threshold: float) -> pd.Series:
    old = classify.BUILT_SHARE
    classify.BUILT_SHARE = threshold
    try:
        return pd.Series(
            [classify.site_status(a, b, c, d) for a, b, c, d in
             zip(df.fp_built_2017, df.fp_built_2023, df.fp_conv_total_ha.fillna(0), df.area_ha, strict=True)],
            index=df.index,
        )
    finally:
        classify.BUILT_SHARE = old


def sensitivity(df: pd.DataFrame) -> list[dict]:
    """Headline numbers under alternative definitions."""
    rows = []

    def record(label, sub, status):
        new = sub[status == "new"]
        conv = new.fp_conv_total_ha.sum()
        rows.append({
            "variant": label,
            "new_sites": int(len(new)),
            "converted_ha": round(float(conv), 1),
            "cropland_share": round(float(new.fp_conv_crops_ha.sum() / conv), 3),
            "trees_share": round(float(new.fp_conv_trees_ha.sum() / conv), 3),
            "new_rural_share": round(float((new.settlement == "rural").mean()), 3),
            "pre2017_rural_share": round(float((sub[status == "existing"].settlement == "rural").mean()), 3),
        })

    record("Main (built share 25%)", df, df.status)
    for t in (0.10, 0.50):
        record(f"Built share threshold {int(t * 100)}%", df, relabel(df, t))
    keep = ~df.crypto
    record("Excluding crypto-flagged sites", df[keep], df.status[keep])
    keep = df.iso3 != "USA"
    record("Excluding the United States", df[keep], df.status[keep])
    keep = df.area_ha <= 50
    record("Excluding footprints over 50 ha", df[keep], df.status[keep])
    # Site-weighted: every new site counts once, whatever its size.
    new = df[df.status == "new"]
    share = (new.fp_conv_crops_ha / new.fp_conv_total_ha).mean()
    rows.append({"variant": "Site-weighted (mean of per-site shares)", "new_sites": int(len(new)),
                 "converted_ha": round(float(new.fp_conv_total_ha.sum()), 1), "cropland_share": round(float(share), 3),
                 "trees_share": round(float((new.fp_conv_trees_ha / new.fp_conv_total_ha).mean()), 3),
                 "new_rural_share": None, "pre2017_rural_share": None})
    return rows


def pipeline_thresholds(s2: pd.DataFrame, area: pd.Series) -> list[dict]:
    ok = s2[s2.changed_share.notna()]
    control = ok[ok.group == "existing control"].changed_share
    pipe = ok[ok.group == "pipeline"].copy()
    pipe["area_ha"] = pipe.site_id.map(area)
    rows = []
    for q in (0.80, 0.90, 0.95):
        t = float(control.quantile(q))
        started = pipe[(pipe.changed_share > t) | (pipe.cleared_share >= 0.25)]
        rows.append({"control_quantile": q, "threshold": round(t, 3), "started_sites": int(len(started)),
                     "of": int(len(pipe)), "started_area_ha": round(float(started.area_ha.sum()), 1)})
    return rows


def run(data: Path, results: Path) -> dict:
    import geopandas as gpd

    df = pd.DataFrame(gpd.read_file(data / "sites_labelled.gpkg").drop(columns="geometry"))
    surr = pd.read_csv(data / "surroundings.csv").merge(df[["site_id", "status"]], on="site_id")
    s2 = pd.read_csv(data / "sentinel_check.csv")
    new = df[df.status == "new"]
    out = {
        "bootstrap_replicates": N_BOOT,
        "conversion_new_global": conversion_shares(new),
        "conversion_new_usa": conversion_shares(new[new.iso3 == "USA"]),
        "conversion_new_outside_usa": conversion_shares(new[new.iso3 != "USA"]),
        "rural_share": {s: proportion((g.settlement == "rural").to_numpy()) for s, g in df.groupby("status")
                        if s in ("existing", "new", "pipeline")},
        "population_5km": {s: median_ci(g.pop_5km.to_numpy()) for s, g in df.groupby("status")
                           if s in ("existing", "new", "pipeline")},
        "area_ha": {s: median_ci(g.area_ha.to_numpy()) for s, g in df.groupby("status")
                    if s in ("existing", "new", "pipeline")},
        "selection_new": selection(surr[surr.status == "new"]),
        "selection_pipeline": selection(surr[surr.status == "pipeline"]),
        "sensitivity": sensitivity(df),
        "pipeline_thresholds": pipeline_thresholds(s2, df.set_index("site_id").area_ha),
    }
    (results / "stats.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
