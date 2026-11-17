"""Label each site as existing, newly built or pipeline, and flag crypto mines."""

from __future__ import annotations

import re

import pandas as pd

CRYPTO = re.compile(
    r"bitcoin|crypto|\bmining\b|\bminer|\briot\b|marathon|bitdeer|cipher mining|hut ?8|core scientific|iris energy|"
    r"terawulf|cleanspark|bitfarms|greenidge|hive digital|stronghold",
    re.IGNORECASE,
)

# A site counts as built when at least this share of its footprint is the built class.
BUILT_SHARE = 0.25
MIN_CHANGE_HA = 1.0


def is_crypto(*texts) -> bool:
    return any(isinstance(t, str) and CRYPTO.search(t) for t in texts)


def site_status(built_2017: float, built_2023: float, conv_ha: float, area_ha: float) -> str:
    """existing: already built in 2017. new: built between 2017 and 2023.
    pipeline: mapped in OSM but its footprint was still mostly unbuilt in 2023.
    """
    if pd.isna(built_2017) or pd.isna(built_2023):
        return "no data"
    if built_2017 >= BUILT_SHARE:
        return "existing"
    if built_2023 >= BUILT_SHARE and conv_ha >= MIN_CHANGE_HA:
        return "new"
    if built_2023 < BUILT_SHARE and area_ha >= MIN_CHANGE_HA:
        return "pipeline"
    return "small or unclear"


def label(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["status"] = [
        site_status(a, b, c, d)
        for a, b, c, d in zip(out.fp_built_2017, out.fp_built_2023, out.fp_conv_total_ha.fillna(0), out.area_ha, strict=True)
    ]
    out["crypto"] = [is_crypto(n, o) for n, o in zip(out.name, out.operator, strict=True)]
    return out
