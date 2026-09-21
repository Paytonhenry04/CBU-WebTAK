#!/usr/bin/env python3
"""
fetch_osm_data.py - One-off script to fetch and cache OSM building/airport
geometry for the CBU webmap. NOT part of the runtime pipeline - re-run this
manually (`python3 fetch_osm_data.py`) only if the source data needs
refreshing. Writes cbu_buildings.geojson and kral_airport.geojson into this
same directory.
"""

import json
import urllib.request
from pathlib import Path

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "cbu-tak-webmap/1.0 (research)"}

# CBU campus, resolved via Nominatim to OSM relation 13218766
# (Overpass area id = 3600000000 + relation id)
CBU_AREA_ID = 3613218766
# Riverside Municipal Airport (KRAL) aerodrome boundary
KRAL_WAY_ID = 127837011

DATA_DIR = Path(__file__).parent


def overpass(query):
    req = urllib.request.Request(
        OVERPASS_URL, data=f"data={query}".encode(), headers=HEADERS
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.load(resp)


def render_height(tags):
    """Estimate an extrusion height in meters from whatever tags exist."""
    if "height" in tags:
        try:
            return float(str(tags["height"]).replace("m", "").strip())
        except ValueError:
            pass
    if "building:levels" in tags:
        try:
            return float(tags["building:levels"]) * 3.0
        except ValueError:
            pass
    return 8.0  # fallback: roughly a 2-3 story building


def way_ring(way):
    return [[nd["lon"], nd["lat"]] for nd in way.get("geometry", [])]


def building_feature(el):
    tags = el.get("tags", {})

    if el["type"] == "way":
        ring = way_ring(el)
        if len(ring) < 3:
            return None
        geometry = {"type": "Polygon", "coordinates": [ring]}
    elif el["type"] == "relation":
        outer_rings = [
            [[nd["lon"], nd["lat"]] for nd in m["geometry"]]
            for m in el.get("members", [])
            if m.get("role") == "outer" and "geometry" in m
        ]
        if not outer_rings:
            return None
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [[ring] for ring in outer_rings],
        }
    else:
        return None

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "osm_id": el["id"],
            "name": tags.get("name"),
            "building": tags.get("building"),
            "amenity": tags.get("amenity"),
            "render_height": render_height(tags),
        },
    }


def fetch_cbu_buildings():
    query = (
        "[out:json][timeout:80];"
        f"area({CBU_AREA_ID})->.a;"
        '(way["building"](area.a);relation["building"](area.a););'
        "out geom;"
    )
    data = overpass(query)
    features = [f for el in data["elements"] if (f := building_feature(el))]
    fc = {"type": "FeatureCollection", "features": features}
    out_path = DATA_DIR / "cbu_buildings.geojson"
    out_path.write_text(json.dumps(fc))
    named = sum(1 for f in features if f["properties"]["name"])
    print(f"cbu_buildings.geojson: {len(features)} features ({named} named) -> {out_path}")


def fetch_kral_boundary():
    query = f"[out:json][timeout:40];way(id:{KRAL_WAY_ID});out geom;"
    data = overpass(query)
    els = data["elements"]
    if not els:
        raise RuntimeError(f"KRAL way {KRAL_WAY_ID} returned no elements")
    feature = building_feature(els[0])
    if feature is None:
        raise RuntimeError(f"KRAL way {KRAL_WAY_ID} had no usable geometry")
    feature["properties"]["name"] = els[0].get("tags", {}).get(
        "name", "Riverside Municipal Airport"
    )
    fc = {"type": "FeatureCollection", "features": [feature]}
    out_path = DATA_DIR / "kral_airport.geojson"
    out_path.write_text(json.dumps(fc))
    print(f"kral_airport.geojson: 1 feature -> {out_path}")


if __name__ == "__main__":
    fetch_cbu_buildings()
    fetch_kral_boundary()
