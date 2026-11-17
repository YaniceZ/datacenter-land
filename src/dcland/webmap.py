"""Interactive web map of all sites, coloured by status."""

from __future__ import annotations

from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd

STATUS_COLORS = {"new": "#eb6834", "pipeline": "#2a78d6", "existing": "#8a8984"}
STATUS_LABEL = {"new": "Built 2018-2023", "pipeline": "Mapped, unbuilt in 2023", "existing": "Built before 2017"}


def _fmt(v, pattern="{:,.1f}"):
    return "n/a" if pd.isna(v) else pattern.format(v)


def build(sites_geojson: Path, out_html: Path) -> None:
    sites = gpd.read_file(sites_geojson)
    sites = sites[sites.status.isin(STATUS_COLORS)]
    m = folium.Map(location=[30, 0], zoom_start=2, tiles="OpenStreetMap", control_scale=True)
    for status in ["existing", "new", "pipeline"]:
        layer = folium.FeatureGroup(name=f"{STATUS_LABEL[status]} ({(sites.status == status).sum()})", show=True)
        for r in sites[sites.status == status].itertuples():
            lines = [
                f"<b>{r.name or 'Unnamed'}</b>",
                f"{r.operator or ''}",
                f"{r.country} &middot; {STATUS_LABEL[status]}",
                f"Footprint: {_fmt(r.area_ha)} ha",
                f"Converted 2017-23: {_fmt(r.fp_conv_total_ha)} ha (cropland {_fmt(r.fp_conv_crops_ha)}, trees {_fmt(r.fp_conv_trees_ha)})",
                f"People within 5 km: {_fmt(r.pop_5km, '{:,.0f}')}",
            ]
            if status == "pipeline" and "changed_share" in sites and not pd.isna(r.changed_share):
                lines.append(f"Surface changed 2023-26: {r.changed_share:.0%}")
            lines.append(f"<a href='https://www.openstreetmap.org/{r.osm_ids.split(';')[0]}' target='_blank'>OSM</a>")
            folium.CircleMarker(
                [r.lat, r.lon],
                radius=max(3.0, min(14.0, 2.5 + (r.area_ha or 0) ** 0.5)),
                color="white",
                weight=1,
                fill=True,
                fill_color=STATUS_COLORS[status],
                fill_opacity=0.85,
                popup=folium.Popup("<br>".join(x for x in lines if x), max_width=320),
            ).add_to(layer)
        layer.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_html))
