#!/usr/bin/env python3
"""
seed_building_info.py - Pre-populate building_info.json with an entry for
every cached CBU building, filled in with whatever OpenStreetMap already
knows (name, type) and a blank description to write yourself.

Safe to re-run: existing entries are preserved exactly as they are, so your
edits are never overwritten. Only buildings missing from the file get added.
Run it again after re-fetching OSM data to pick up any new footprints.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent
BUILDINGS = DATA_DIR / "cbu_buildings.geojson"
INFO = DATA_DIR / "building_info.json"


def pretty_type(props: dict) -> str:
    """Human-readable type from the OSM tags, e.g. 'fast_food' -> 'Fast food'."""
    raw = props.get("building")
    if not raw or raw == "yes":
        raw = props.get("amenity") or ""
    raw = (raw or "").replace("_", " ").strip()
    return raw[:1].upper() + raw[1:] if raw else ""


def main() -> None:
    buildings = json.loads(BUILDINGS.read_text())["features"]

    existing = {}
    if INFO.exists():
        try:
            existing = json.loads(INFO.read_text())
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"{INFO} is not valid JSON ({exc}). Fix or delete it first - "
                "refusing to run so your existing entries aren't lost."
            )

    # Named buildings first (alphabetically), then unnamed by id, so the file
    # is easy to work through by hand.
    def sort_key(feature):
        name = feature["properties"].get("name") or ""
        return (name == "", name.lower(), feature["properties"]["osm_id"])

    result = {k: v for k, v in existing.items() if k.startswith("_")}

    added = 0
    for feature in sorted(buildings, key=sort_key):
        props = feature["properties"]
        key = str(props["osm_id"])
        if key in existing:
            result[key] = existing[key]  # preserve edits verbatim
            continue
        result[key] = {
            "name": props.get("name") or "",
            "type": pretty_type(props),
            "description": "",
        }
        added += 1

    # Anything in the file that no longer matches a building - keep it rather
    # than silently dropping someone's work.
    for key, value in existing.items():
        if key not in result:
            result[key] = value

    INFO.write_text(json.dumps(result, indent=2) + "\n")
    total = len([k for k in result if not k.startswith("_")])
    print(f"{INFO}: {total} entries ({added} newly added, {total - added} preserved)")


if __name__ == "__main__":
    main()
