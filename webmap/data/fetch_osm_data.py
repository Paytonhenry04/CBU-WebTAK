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
CBU_RELATION_ID = 13218766
# CBU's Health Science Campus (3532 Monroe St, Lot 25 + its ~6 buildings) is a
# separate OSM parcel, way 143181604 "College of Health Science" - outside
# the main campus relation, so it has to be pulled in explicitly.
# (Overpass area id = 2400000000 + way id)
HSC_WAY_ID = 143181604
HSC_AREA_ID = 2400000000 + HSC_WAY_ID
INFO_PATH = Path(__file__).parent / "building_info.json"
# Riverside Municipal Airport (KRAL) aerodrome boundary
KRAL_WAY_ID = 127837011
# Search origin, matching KRAL_LAT/KRAL_LON/SEARCH_RADIUS_NM in adsb_tak.py
KRAL_LAT = 33.9519
KRAL_LON = -117.4459

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


def ignored_ids():
    """osm_ids in building_info.json's _ignore list - footprints inside the
    campus boundary that aren't CBU buildings (e.g. private houses)."""
    try:
        return {str(i) for i in json.loads(INFO_PATH.read_text()).get("_ignore", [])}
    except (OSError, json.JSONDecodeError):
        return set()


def fetch_cbu_buildings():
    query = (
        "[out:json][timeout:80];"
        f"area({CBU_AREA_ID})->.a;"
        f"area({HSC_AREA_ID})->.h;"
        '(way["building"](area.a);relation["building"](area.a);'
        'way["building"](area.h);relation["building"](area.h););'
        "out geom;"
    )
    data = overpass(query)
    skip = ignored_ids()
    features = [
        f
        for el in data["elements"]
        if (f := building_feature(el)) and str(f["properties"]["osm_id"]) not in skip
    ]
    fc = {"type": "FeatureCollection", "features": features}
    out_path = DATA_DIR / "cbu_buildings.geojson"
    out_path.write_text(json.dumps(fc))
    named = sum(1 for f in features if f["properties"]["name"])
    print(f"cbu_buildings.geojson: {len(features)} features ({named} named) -> {out_path}")


def fetch_campus_boundary():
    """The real CBU campus outline (OSM amenity=university relation), not a
    synthetic circle. It's a multipolygon - the campus is several parcels."""
    query = (
        f"[out:json][timeout:50];relation(id:{CBU_RELATION_ID});out geom;"
    )
    data = overpass(query)
    els = data["elements"]
    if not els:
        raise RuntimeError(f"CBU relation {CBU_RELATION_ID} returned no elements")
    el = els[0]

    rings = [
        [[nd["lon"], nd["lat"]] for nd in m["geometry"]]
        for m in el.get("members", [])
        if m.get("role") == "outer" and "geometry" in m
    ]
    if not rings:
        raise RuntimeError("CBU relation had no outer rings with geometry")

    hsc = overpass(f"[out:json][timeout:50];way(id:{HSC_WAY_ID});out geom;")["elements"]
    if hsc and hsc[0].get("geometry"):
        rings.append(way_ring(hsc[0]))

    feature = {
        "type": "Feature",
        "geometry": {
            "type": "MultiPolygon",
            "coordinates": [[ring] for ring in rings],
        },
        "properties": {
            "osm_id": el["id"],
            "name": el.get("tags", {}).get("name", "California Baptist University"),
        },
    }
    fc = {"type": "FeatureCollection", "features": [feature]}
    out_path = DATA_DIR / "cbu_campus.geojson"
    out_path.write_text(json.dumps(fc))
    print(f"cbu_campus.geojson: {len(rings)} outer rings -> {out_path}")


def fetch_airports():
    """Every aerodrome within the same radius adsb_tak.py searches, so
    takeoffs and landings can be matched to a field by position."""
    radius_m = int(100 * 1852)  # SEARCH_RADIUS_NM in adsb_tak.py
    query = (
        "[out:json][timeout:50];"
        f'(node["aeroway"="aerodrome"](around:{radius_m},{KRAL_LAT},{KRAL_LON});'
        f'way["aeroway"="aerodrome"](around:{radius_m},{KRAL_LAT},{KRAL_LON}););'
        "out center tags;"
    )
    data = overpass(query)

    features = []
    for el in data["elements"]:
        tags = el.get("tags", {})
        name = tags.get("name")
        icao = tags.get("icao")
        if not name and not icao:
            continue  # unnamed dirt strips would only produce bogus matches
        if el["type"] == "node":
            lon, lat = el.get("lon"), el.get("lat")
        else:
            center = el.get("center") or {}
            lon, lat = center.get("lon"), center.get("lat")
        if lon is None or lat is None:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "osm_id": el["id"],
                    "name": name,
                    "icao": icao,
                    "iata": tags.get("iata"),
                },
            }
        )

    fc = {"type": "FeatureCollection", "features": features}
    out_path = DATA_DIR / "airports.geojson"
    out_path.write_text(json.dumps(fc))
    with_icao = sum(1 for f in features if f["properties"]["icao"])
    print(f"airports.geojson: {len(features)} aerodromes ({with_icao} with ICAO) -> {out_path}")


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
    fetch_campus_boundary()
    fetch_airports()
    fetch_kral_boundary()
