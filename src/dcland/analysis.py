"""Turn the per-site tables into the numbers and figures reported in the README."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

# Categorical slots 1-4 of the reference palette (validated adjacent-pair order).
CLASS_COLORS = {"crops": "#2a78d6", "trees": "#eb6834", "rangeland": "#1baf7a", "bare": "#eda100"}
OTHER = "#b5b3ad"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e2dc"
UNDEVELOPED = ["crops", "trees", "rangeland", "bare", "flooded_vegetation"]

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_2,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 150,
    }
)


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


def _stacked_bar(ax, y, shares, height=0.6):
    left = 0.0
    for cls, color in CLASS_COLORS.items():
        w = shares.get(cls, 0.0)
        ax.barh(y, w, left=left, height=height, color=color, edgecolor="white", linewidth=2)
        if w >= 0.06:
            ax.text(left + w / 2, y, f"{w:.0%}", ha="center", va="center", color="white", fontsize=9, fontweight="bold")
        left += w
    rest = max(0.0, 1 - left)
    if rest > 0.001:
        ax.barh(y, rest, left=left, height=height, color=OTHER, edgecolor="white", linewidth=2)


def figure_land(summary: dict, val_us: pd.DataFrame, out: Path) -> None:
    """What was the land before? Global product, and the US check against USDA CDL."""
    fig, ax = plt.subplots(figsize=(8.5, 3.2))
    new = summary["new_sites_2018_2023"]
    us_val = summary["validation"]["USA"]
    rows = [
        (f"All new sites, global product\n{new['sites']} sites, {new['hectares']:,.0f} ha", new["share"]),
        (f"US sites, global product\n{summary['validation']['USA']['sites']} sites, {us_val['hectares']:,.0f} ha",
         us_val["global_product_share"]),
        ("US sites, USDA Cropland Data Layer\nsame pixels", us_val["reference_share"]),
    ]
    for i, (_label, shares) in enumerate(rows):
        _stacked_bar(ax, -i, shares)
    ax.set_yticks([-i for i in range(len(rows))], [r[0] for r in rows], fontsize=9, color=INK)
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.tick_params(axis="y", length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in CLASS_COLORS.values()] + [plt.Rectangle((0, 0), 1, 1, color=OTHER)]
    ax.legend(handles, ["Cropland", "Trees", "Rangeland", "Bare", "Other"], ncol=5, frameon=False,
              loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=9)
    ax.set_title("Land that became data center footprint, 2017 to 2023", loc="left", color=INK, pad=28, fontsize=11)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def figure_siting(df: pd.DataFrame, out: Path) -> None:
    """Where new sites go: settlement type and neighbourhood population."""
    groups = [("existing", "Built before 2017"), ("new", "Built 2018 to 2023"), ("pipeline", "Mapped, unbuilt in 2023")]
    order = ["urban centre", "dense or semi-dense town", "suburban or peri-urban", "rural"]
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 3.4), gridspec_kw={"width_ratios": [1.4, 1]})
    for i, (status, _label) in enumerate(groups):
        g = df[df.status == status]
        shares = g.settlement.value_counts(normalize=True)
        left = 0.0
        for cls, col in zip(order, colors, strict=True):
            w = float(shares.get(cls, 0))
            a1.barh(-i, w, left=left, height=0.6, color=col, edgecolor="white", linewidth=2)
            if w >= 0.08:
                a1.text(left + w / 2, -i, f"{w:.0%}", ha="center", va="center", color="white", fontsize=9, fontweight="bold")
            left += w
    a1.set_yticks([-i for i in range(3)], [f"{lab}\n(n={int((df.status == s).sum())})" for s, lab in groups], color=INK)
    a1.set_xlim(0, 1)
    a1.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    a1.tick_params(axis="y", length=0)
    a1.legend([plt.Rectangle((0, 0), 1, 1, color=c) for c in colors], ["Urban centre", "Town", "Suburban", "Rural"],
              ncol=4, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=9)
    a1.set_title("Settlement type at the site (GHSL 2020)", loc="left", color=INK, pad=24, fontsize=11)

    data = [df[df.status == s].pop_5km.clip(lower=1) for s, _ in groups]
    bp = a2.boxplot(data, vert=False, widths=0.5, patch_artist=True, showfliers=False,
                    medianprops={"color": INK, "linewidth": 2})
    for patch in bp["boxes"]:
        patch.set(facecolor="#d6e4f5", edgecolor=INK_2)
    a2.set_yticks([1, 2, 3], ["" for _ in groups])
    a2.invert_yaxis()
    a2.set_xscale("log")
    a2.set_xlim(1e2, 2e6)
    for i, d in enumerate(data, 1):
        a2.text(d.median(), i - 0.38, f"{d.median():,.0f}", ha="center", fontsize=9, color=INK)
    a2.set_xlabel("People within 5 km (GHSL 2020, log scale)")
    a2.set_title("Neighbourhood population", loc="left", color=INK, pad=24, fontsize=11)
    a2.grid(axis="x", color=GRID, linewidth=0.8)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def figure_selection(surr: pd.DataFrame, out: Path) -> None:
    """Footprint against the undeveloped land in a 5 km ring, per site."""
    x = surr[surr.status == "new"]
    fp_u = x[[f"fp_{c}_ha" for c in UNDEVELOPED]].sum(axis=1)
    ring_u = x[[f"ring_{c}_ha" for c in UNDEVELOPED]].sum(axis=1)
    ok = fp_u > 0.5
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.4), sharey=True)
    for ax, cls in zip(axes, ["crops", "trees", "rangeland"], strict=True):
        a = (x[f"fp_{cls}_ha"] / fp_u)[ok]
        b = (x[f"ring_{cls}_ha"] / ring_u)[ok]
        ax.plot([0, 1], [0, 1], color=GRID, linewidth=1.5, zorder=1)
        ax.scatter(b, a, s=22, color=CLASS_COLORS[cls], edgecolor="white", linewidth=0.8, alpha=0.85, zorder=2)
        ax.set_title(f"{cls.capitalize()}: footprint {a.mean():.0%}, ring {b.mean():.0%}", loc="left", fontsize=10, color=INK)
        ax.set_xlim(-0.02, 1.02)
        ax.set_ylim(-0.02, 1.02)
        ax.set_xlabel("Share of undeveloped land in 5 km ring")
        ax.set_aspect("equal")
    axes[0].set_ylabel("Share of footprint")
    fig.suptitle(f"Do new sites pick a land type? {int(ok.sum())} sites, 2017 land cover. Diagonal = no preference.",
                 x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def make_figures(data: Path, results: Path, docs: Path) -> None:
    docs.mkdir(parents=True, exist_ok=True)
    summary = json.loads((results / "summary.json").read_text())
    df = load(data)
    surr = pd.read_csv(data / "surroundings.csv").merge(df[["site_id", "status"]], on="site_id")
    figure_land(summary, pd.read_csv(results / "validation_USA.csv", index_col=0), docs / "fig1_land_before.png")
    figure_siting(df, docs / "fig2_siting.png")
    figure_selection(surr, docs / "fig3_selection.png")


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
