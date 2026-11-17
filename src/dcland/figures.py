"""Publication figures (183 mm double-column, Arial 7 pt, vector PDF and 600 dpi PNG)."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

MM = 1 / 25.4
FULL = 183 * MM

INK = "#1a1a19"
INK_2 = "#52514e"
RULE = "#d9d7d0"
LAND = "#ecebe7"

# Cohorts.
COHORT = {"existing": "#9a9893", "new": "#eb6834", "pipeline": "#2a78d6"}
COHORT_LABEL = {"existing": "Built before 2017", "new": "Built 2018–2023", "pipeline": "Unbuilt in 2023"}
# Previous land class (validated: adjacent normal-vision dE >= 15, CVD dE >= 7.7 with direct labels).
CLASS = {"trees": "#008300", "crops": "#eda100", "rangeland": "#1baf7a", "bare": "#8a7a66"}
CLASS_LABEL = {"trees": "Trees", "crops": "Cropland", "rangeland": "Rangeland", "bare": "Bare"}
OTHER = "#cfcdc6"
# Settlement type is ordinal: one hue, dark (urban) to light (rural).
SETTLEMENT = {"urban centre": "#0d3d73", "dense or semi-dense town": "#2a78d6",
              "suburban or peri-urban": "#7fb0ea", "rural": "#cfe1f7"}
SETTLEMENT_LABEL = {"urban centre": "Urban centre", "dense or semi-dense town": "Town",
                    "suburban or peri-urban": "Suburban", "rural": "Rural"}
UNDEVELOPED = ["crops", "trees", "rangeland", "bare", "flooded_vegetation"]


def style() -> None:
    mpl.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"],
        "font.size": 7,
        "axes.titlesize": 7,
        "axes.labelsize": 7,
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "legend.fontsize": 6.5,
        "axes.linewidth": 0.5,
        "xtick.major.width": 0.5,
        "ytick.major.width": 0.5,
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "axes.edgecolor": INK_2,
        "axes.labelcolor": INK,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "text.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "legend.frameon": False,
        "pdf.fonttype": 42,
        "svg.fonttype": "none",
        "savefig.dpi": 600,
    })


def panel(ax, letter: str, x: float = -0.12, y: float = 1.06) -> None:
    ax.text(x, y, letter, transform=ax.transAxes, fontsize=8, fontweight="bold", va="bottom", ha="left")


def save(fig, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(out.with_suffix(".png"), bbox_inches="tight", dpi=300)
    plt.close(fig)


def pct(ax, axis="x"):
    fmt = mpl.ticker.FuncFormatter(lambda v, _: f"{v:.0%}")
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(fmt)


# ---------------------------------------------------------------- figure 1


def figure1(df: gpd.GeoDataFrame, countries: gpd.GeoDataFrame, out: Path) -> None:
    """Where data center campuses are, and how much land the new ones converted."""
    crs = "EPSG:8857"  # Equal Earth
    world = countries[countries.CONTINENT != "Antarctica"].to_crs(crs)
    pts = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326").to_crs(crs)

    fig = plt.figure(figsize=(FULL, 140 * MM))
    gs = GridSpec(2, 2, figure=fig, height_ratios=[2.05, 1], width_ratios=[1.25, 1], hspace=0.22, wspace=0.42)
    ax = fig.add_subplot(gs[0, :])
    world.plot(ax=ax, color=LAND, edgecolor="white", linewidth=0.3)
    for status in ["existing", "new", "pipeline"]:
        p = pts[pts.status == status]
        ax.scatter(p.geometry.x, p.geometry.y, s=np.clip(1.2 + 1.6 * np.sqrt(p.area_ha), 1.2, 60),
                   color=COHORT[status], alpha=0.55 if status == "existing" else 0.9,
                   edgecolor="white", linewidth=0.25, zorder=3 if status != "existing" else 2)
    ax.set_axis_off()
    ax.set_xlim(world.total_bounds[0], world.total_bounds[2])
    ax.set_ylim(-5.6e6, world.total_bounds[3])
    handles = [Line2D([], [], marker="o", ls="", color=COHORT[s], markersize=4,
                      label=f"{COHORT_LABEL[s]} (n = {(df.status == s).sum():,})") for s in ["existing", "new", "pipeline"]]
    sizes = [Line2D([], [], marker="o", ls="", color=INK_2, markerfacecolor="none", markeredgewidth=0.5,
                    markersize=np.sqrt(1.2 + 1.6 * np.sqrt(a)), label=f"{a} ha") for a in (1, 10, 100)]
    leg = ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 0.02), title="Campus status",
                    title_fontsize=6.5, alignment="left", handletextpad=0.2)
    ax.add_artist(leg)
    ax.legend(handles=sizes, loc="lower left", bbox_to_anchor=(0.255, 0.02), title="Footprint", title_fontsize=6.5,
              alignment="left", handletextpad=0.2)
    ax.text(0.0, 1.0, "a", transform=ax.transAxes, fontsize=8, fontweight="bold", va="top")

    new = df[df.status == "new"].copy()
    classes = ["trees", "crops", "rangeland", "bare"]
    new["other"] = new.fp_conv_total_ha - new[[f"fp_conv_{c}_ha" for c in classes]].sum(axis=1)

    ax2 = fig.add_subplot(gs[1, 0])
    by_year = new.groupby("fp_first_built_year")[[f"fp_conv_{c}_ha" for c in classes] + ["other"]].sum()
    years = by_year.index.astype(int)
    bottom = np.zeros(len(years))
    for c in classes + ["other"]:
        v = by_year[f"fp_conv_{c}_ha" if c != "other" else "other"].to_numpy()
        ax2.bar(years, v, bottom=bottom, width=0.72, color=CLASS.get(c, OTHER), edgecolor="white", linewidth=0.6,
                label=CLASS_LABEL.get(c, "Other"))
        bottom += v
    for x, total, n in zip(years, bottom, new.groupby("fp_first_built_year").size(), strict=True):
        ax2.text(x, total + 5, f"n = {n}", ha="center", va="bottom", fontsize=6, color=INK_2)
    ax2.set_ylabel("Land converted (ha)")
    ax2.set_xlabel("First year the footprint was mostly built")
    ax2.set_xticks(years)
    ax2.set_ylim(0, bottom.max() * 1.15)
    ax2.legend(ncol=5, loc="lower left", bbox_to_anchor=(0, 1.0), handlelength=1, columnspacing=0.8, handletextpad=0.3)
    panel(ax2, "b", x=-0.16, y=1.1)

    ax3 = fig.add_subplot(gs[1, 1])
    by_cont = new.groupby("continent")[[f"fp_conv_{c}_ha" for c in classes] + ["other"]].sum()
    by_cont = by_cont.loc[by_cont.sum(axis=1).sort_values().index]
    left = np.zeros(len(by_cont))
    y = np.arange(len(by_cont))
    for c in classes + ["other"]:
        v = by_cont[f"fp_conv_{c}_ha" if c != "other" else "other"].to_numpy()
        ax3.barh(y, v, left=left, height=0.66, color=CLASS.get(c, OTHER), edgecolor="white", linewidth=0.6)
        left += v
    counts = new.groupby("continent").size()
    for yi, cont, total in zip(y, by_cont.index, left, strict=True):
        ax3.text(total + 12, yi, f"{total:,.0f} ha · {counts[cont]} sites", va="center", fontsize=6, color=INK_2)
    ax3.set_yticks(y, by_cont.index)
    ax3.tick_params(axis="y", length=0)
    ax3.set_xlabel("Land converted, 2017–2023 (ha)")
    ax3.set_xlim(0, left.max() * 1.55)
    panel(ax3, "c", x=-0.3, y=1.1)
    save(fig, out)


# ---------------------------------------------------------------- figure 2


def figure2(stats: dict, val: pd.DataFrame, out: Path) -> None:
    """Previous land class with uncertainty, and agreement with the USDA Cropland Data Layer."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(FULL, 76 * MM), gridspec_kw={"width_ratios": [1, 1.05], "wspace": 0.45})
    groups = [("conversion_new_global", "All new campuses", "o", INK),
              ("conversion_new_usa", "United States", "s", INK_2),
              ("conversion_new_outside_usa", "Outside the United States", "D", "#8d8b85")]
    classes = ["crops", "rangeland", "trees", "bare"]
    for k, (key, _label, marker, color) in enumerate(groups):
        g = stats[key]
        for i, c in enumerate(classes):
            yv = i + (k - 1) * 0.22
            lo, hi = g[c]["ci95"]
            a.plot([lo, hi], [yv, yv], color=color, lw=0.9, solid_capstyle="butt")
            a.plot(g[c]["share"], yv, marker=marker, color=color, ms=3.6, mec="white", mew=0.4, ls="")
    for i, c in enumerate(classes):
        a.axhspan(i - 0.42, i + 0.42, color=CLASS[c], alpha=0.10, lw=0, zorder=0)
    a.set_yticks(range(len(classes)), [CLASS_LABEL[c] for c in classes])
    a.invert_yaxis()
    a.tick_params(axis="y", length=0)
    a.set_xlim(0, 0.75)
    pct(a)
    a.set_xlabel("Share of converted land (95% site-bootstrap interval)")
    a.legend([Line2D([], [], color=c, marker=m, ms=3.6, mec="white", mew=0.4, lw=0.9) for _, _, m, c in groups],
             [f"{lab} ({stats[k]['sites']} sites, {stats[k]['hectares']:,.0f} ha)" for k, lab, _, _ in groups],
             loc="lower left", bbox_to_anchor=(-0.02, 1.0), handlelength=1.6, handletextpad=0.4)
    a.grid(axis="x", color=RULE, lw=0.4)
    a.set_axisbelow(True)
    panel(a, "a", x=-0.2, y=1.2)

    rows = ["crops", "rangeland", "trees", "bare"]
    cols = ["crops", "rangeland", "trees", "wetland", "bare", "built"]
    m = val.loc[rows, cols]
    norm = m.div(m.sum(axis=1), axis=0)
    im = b.imshow(norm.to_numpy(), cmap=mpl.colors.LinearSegmentedColormap.from_list("b", ["#f4f8fd", "#0d3d73"]),
                  vmin=0, vmax=1, aspect="auto")
    for i in range(len(rows)):
        for j in range(len(cols)):
            v, ha = norm.iat[i, j], m.iat[i, j]
            if ha < 0.05:
                continue
            b.text(j, i, f"{v:.0%}\n{ha:,.0f} ha", ha="center", va="center", fontsize=5.6,
                   color="white" if v > 0.5 else INK)
    b.set_xticks(range(len(cols)), ["Cropland", "Rangeland", "Trees", "Wetland", "Bare", "Developed"], rotation=30, ha="right")
    b.set_yticks(range(len(rows)), [CLASS_LABEL[r] for r in rows])
    b.set_xlabel("USDA Cropland Data Layer 2017")
    b.set_ylabel("Global 10 m product, 2017")
    b.tick_params(length=0)
    for s in b.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=b, fraction=0.04, pad=0.03)
    cb.outline.set_linewidth(0.4)
    cb.ax.tick_params(width=0.4, length=2)
    cb.set_label("Share of row")
    cb.ax.yaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: f"{v:.0%}"))
    panel(b, "b", x=-0.3, y=1.2)
    save(fig, out)


# ---------------------------------------------------------------- figure 3


def _ecdf(ax, values, color, label):
    v = np.sort(np.asarray(values, dtype=float))
    y = np.arange(1, len(v) + 1) / len(v)
    ax.step(v, y, where="post", color=color, lw=1.1, label=label)
    med = np.median(v)
    ax.plot([med, med], [0, 0.5], color=color, lw=0.6, ls=(0, (2, 1.5)))


def figure3(df: pd.DataFrame, stats: dict, out: Path) -> None:
    """The move out of cities: settlement type, neighbourhood population and campus size by cohort."""
    fig = plt.figure(figsize=(FULL, 62 * MM))
    gs = GridSpec(1, 3, figure=fig, width_ratios=[1.25, 1, 1], wspace=0.42)
    a = fig.add_subplot(gs[0])
    cohorts = ["existing", "new", "pipeline"]
    for i, c in enumerate(cohorts):
        shares = df[df.status == c].settlement.value_counts(normalize=True)
        left = 0.0
        for s, col in SETTLEMENT.items():
            w = float(shares.get(s, 0))
            a.barh(i, w, left=left, height=0.62, color=col, edgecolor="white", linewidth=0.6)
            if w >= 0.08:
                a.text(left + w / 2, i, f"{w:.0%}", ha="center", va="center", fontsize=6,
                       color="white" if s in ("urban centre", "dense or semi-dense town") else INK)
            left += w
    a.set_yticks(range(3), [f"{COHORT_LABEL[c]}\nn = {(df.status == c).sum():,}" for c in cohorts])
    a.invert_yaxis()
    a.tick_params(axis="y", length=0)
    a.set_xlim(0, 1)
    pct(a)
    a.set_xlabel("Share of campuses")
    a.legend([Patch(color=c) for c in SETTLEMENT.values()], SETTLEMENT_LABEL.values(), ncol=4, loc="lower left",
             bbox_to_anchor=(-0.02, 1.0), handlelength=1, columnspacing=0.7, handletextpad=0.3)
    panel(a, "a", x=-0.36, y=1.12)

    b = fig.add_subplot(gs[1])
    for c in cohorts:
        _ecdf(b, df[df.status == c].pop_5km.clip(lower=10), COHORT[c], COHORT_LABEL[c])
    b.set_xscale("log")
    b.set_xlim(1e2, 3e6)
    b.set_ylim(0, 1.01)
    pct(b, "y")
    b.set_xlabel("Residents within 5 km (GHSL 2020)")
    b.set_ylabel("Cumulative share of campuses")
    b.legend(ncol=3, loc="lower left", bbox_to_anchor=(-0.02, 1.0), handlelength=1.2, columnspacing=1.0)
    panel(b, "b", x=-0.3, y=1.12)

    c_ax = fig.add_subplot(gs[2], sharey=b)
    for c in cohorts:
        _ecdf(c_ax, df[df.status == c].area_ha.clip(lower=0.01), COHORT[c], COHORT_LABEL[c])
    c_ax.set_xscale("log")
    c_ax.set_xlim(0.01, 1000)
    c_ax.set_xlabel("Campus footprint (ha)")
    c_ax.tick_params(labelleft=False)
    panel(c_ax, "c", x=-0.12, y=1.12)
    save(fig, out)


# ---------------------------------------------------------------- figure 4


def figure4(surr: pd.DataFrame, stats: dict, out: Path) -> None:
    """Footprint versus the undeveloped land around it."""
    fig = plt.figure(figsize=(FULL, 62 * MM))
    gs = GridSpec(1, 4, figure=fig, width_ratios=[1, 1, 1, 1.25], wspace=0.35)
    x = surr[surr.status == "new"]
    fp_u = x[[f"fp_{c}_ha" for c in UNDEVELOPED]].sum(axis=1)
    ring_u = x[[f"ring_{c}_ha" for c in UNDEVELOPED]].sum(axis=1)
    ok = fp_u > 0.5
    classes = ["crops", "rangeland", "trees"]
    for k, cls in enumerate(classes):
        ax = fig.add_subplot(gs[k])
        fpv = (x[f"fp_{cls}_ha"] / fp_u)[ok]
        rv = (x[f"ring_{cls}_ha"] / ring_u)[ok]
        ax.plot([0, 1], [0, 1], color=RULE, lw=0.8, zorder=1)
        ax.scatter(rv, fpv, s=7, color=CLASS[cls], edgecolor="white", linewidth=0.3, alpha=0.9, zorder=2)
        ax.set_xlim(-0.03, 1.03)
        ax.set_ylim(-0.03, 1.03)
        ax.set_aspect("equal")
        pct(ax)
        pct(ax, "y")
        ax.set_title(CLASS_LABEL[cls], loc="center", fontsize=7, color=INK)
        ax.set_xlabel("5 km ring")
        if k == 0:
            ax.set_ylabel("Footprint")
        else:
            ax.tick_params(labelleft=False)
        panel(ax, "abc"[k], x=-0.42 if k == 0 else -0.16, y=1.02)

    f = fig.add_subplot(gs[3])
    rows = []
    for cls in classes:
        for key, label in (("selection_new", "Built 2018–2023 (2017 cover)"), ("selection_pipeline", "Unbuilt in 2023 (2023 cover)")):
            s = stats[key][cls]
            rows.append((cls, label, s["mean_difference"], *s["mean_difference_ci95"], s["wilcoxon_p"], stats[key]["sites"]))
    ys = []
    for i, (_cls, label, d, lo, hi, p, _n) in enumerate(rows):
        y = i + (i // 2) * 0.6
        ys.append(y)
        color = COHORT["new"] if label.startswith("Built") else COHORT["pipeline"]
        f.plot([lo, hi], [y, y], color=color, lw=1.1, solid_capstyle="butt")
        f.plot(d, y, "o", color=color, ms=3.6, mec="white", mew=0.4)
        f.text(0.205, y, f"p = {p:.2g}" if p >= 0.001 else "p < 0.001", va="center", fontsize=6, color=INK_2)
    f.axvline(0, color=INK_2, lw=0.5)
    f.set_yticks([(ys[2 * k] + ys[2 * k + 1]) / 2 for k in range(3)], [CLASS_LABEL[c] for c in classes])
    f.invert_yaxis()
    f.tick_params(axis="y", length=0)
    f.set_xlim(-0.2, 0.27)
    f.xaxis.set_major_formatter(mpl.ticker.FuncFormatter(lambda v, _: f"{v * 100:+.0f}" if v else "0"))
    f.set_xlabel("Footprint minus ring (percentage points)")
    f.legend([Line2D([], [], color=COHORT["new"], marker="o", ms=3.6, lw=1.1),
              Line2D([], [], color=COHORT["pipeline"], marker="o", ms=3.6, lw=1.1)],
             [f"Built 2018–2023 (n = {stats['selection_new']['sites']})",
              f"Unbuilt in 2023 (n = {stats['selection_pipeline']['sites']})"],
             loc="lower left", bbox_to_anchor=(-0.02, 1.0), handlelength=1.4)
    panel(f, "d", x=-0.22, y=1.08)
    save(fig, out)


# ---------------------------------------------------------------- figure 5


def _stretch(a: np.ndarray, b: np.ndarray):
    both = np.concatenate([a[a.sum(axis=2) > 0], b[b.sum(axis=2) > 0]])
    lo, hi = np.percentile(both, [1, 99], axis=0)
    return [np.clip((x - lo) / (hi - lo), 0, 1) ** 0.85 for x in (a, b)]


def figure5(s2: pd.DataFrame, sites: gpd.GeoDataFrame, chips: Path, stats: dict, out: Path) -> None:
    """Construction since 2023 on campuses that were unbuilt in 2023."""
    meta = json.loads((chips / "meta.json").read_text())
    examples = [(634, "New Carlisle, Indiana, USA"), (1396, "Waltham Cross, England, UK"), (1906, "Cyberjaya, Malaysia")]
    fig = plt.figure(figsize=(FULL, 112 * MM))
    gs = GridSpec(2, 4, figure=fig, width_ratios=[1.35, 1, 1, 1], height_ratios=[1, 1], wspace=0.08, hspace=0.18)

    a = fig.add_subplot(gs[:, 0])
    ok = s2[s2.changed_share.notna()]
    thr = next(r for r in stats["pipeline_thresholds"] if r["control_quantile"] == 0.9)
    rng = np.random.default_rng(1)
    for i, (grp, label, color) in enumerate([("existing control", "Built before 2017\n(control)", COHORT["existing"]),
                                             ("pipeline", "Unbuilt in 2023", COHORT["pipeline"])]):
        v = ok[ok.group == grp].changed_share.to_numpy()
        a.scatter(i + rng.uniform(-0.18, 0.18, len(v)), v, s=6, color=color, edgecolor="white", linewidth=0.3, alpha=0.9)
        q1, q2, q3 = np.percentile(v, [25, 50, 75])
        a.plot([i + 0.27, i + 0.27], [q1, q3], color=INK, lw=1.2)
        a.plot(i + 0.27, q2, "o", color=INK, ms=3)
        a.text(i, -0.03, f"{label}\nn = {len(v)}", ha="center", va="top", fontsize=6.5)
    a.axhline(thr["threshold"], color=INK_2, lw=0.6, ls=(0, (3, 2)))
    a.text(-0.45, thr["threshold"] + 0.015, "90th percentile of controls", ha="left", va="bottom", fontsize=6, color=INK_2)
    a.set_xlim(-0.5, 1.5)
    a.set_ylim(0, 1.02)
    a.set_xticks([])
    a.spines["bottom"].set_visible(False)
    pct(a, "y")
    a.set_ylabel("Footprint changed between summer 2023 and 2026")
    a.text(0.5, 1.03, f"{thr['started_sites']} of {thr['of']} unbuilt campuses changed", transform=a.transAxes,
           ha="center", fontsize=6.5, color=INK)
    panel(a, "a", x=-0.32, y=1.0)

    letters = iter("bcdefg")
    for col, (sid, place) in enumerate(examples):
        site = sites[sites.site_id == sid].iloc[0]
        before, after = (np.load(chips / f"{sid}_{y}.npy") for y in (2023, 2026))
        before, after = _stretch(before, before)[0], _stretch(after, after)[0]
        for row, (img, year) in enumerate([(before, 2023), (after, 2026)]):
            m = meta[f"{sid}_{year}"]
            x0, y0, x1, y1 = m["bounds"]
            ax = fig.add_subplot(gs[row, col + 1])
            ax.imshow(img, extent=(x0, x1, y0, y1), interpolation="nearest")
            outline = gpd.GeoSeries([site.geometry], crs="EPSG:4326").to_crs(m["epsg"])
            outline.boundary.plot(ax=ax, color="white", lw=0.8)
            ax.set_xlabel("")
            ax.set_ylabel("")
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            ax.set_xticks([])
            ax.set_yticks([])
            for s in ax.spines.values():
                s.set_visible(False)
            date = m["scene"].split("_")[2][:8]
            ax.text(0.03, 0.97, f"{date[:4]}-{date[4:6]}-{date[6:]}", transform=ax.transAxes, va="top", fontsize=6,
                    color="white", fontweight="bold")
            if row == 0:
                ax.set_title(place, fontsize=6.5, loc="left", color=INK)
            if row == 1:
                ax.plot([x1 - 150 - 1000, x1 - 150], [y0 + 170, y0 + 170], color="white", lw=1.6, solid_capstyle="butt")
                ax.text(x1 - 150 - 500, y0 + 260, "1 km", ha="center", va="bottom", fontsize=6, color="white", fontweight="bold")
            ax.text(-0.02, 1.0, next(letters), transform=ax.transAxes, fontsize=8, fontweight="bold", ha="right", va="top")
    save(fig, out)


# ---------------------------------------------------------------- entry point


def make_all(data: Path, results: Path, figdir: Path, countries_shp: Path) -> None:
    style()
    sites = gpd.read_file(data / "sites_labelled.gpkg")
    df = pd.DataFrame(sites.drop(columns="geometry"))
    stats = json.loads((results / "stats.json").read_text())
    surr = pd.read_csv(data / "surroundings.csv").merge(df[["site_id", "status"]], on="site_id")
    s2 = pd.read_csv(data / "sentinel_check.csv")
    figure1(df, gpd.read_file(countries_shp), figdir / "fig1_global")
    figure2(stats, pd.read_csv(results / "validation_USA.csv", index_col=0), figdir / "fig2_land_before")
    figure3(df, stats, figdir / "fig3_siting")
    figure4(surr, stats, figdir / "fig4_selection")
    figure5(s2, sites, data / "chips", stats, figdir / "fig5_pipeline")
