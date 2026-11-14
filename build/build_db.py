#!/usr/bin/env python3
"""
Builds the TruckGPS offline truck-restriction database.

Input  (work dir): <region>.geojsonseq  from `osmium export` (filtered OSM)
                   <region>.poly        Geofabrik boundary of the extract
Output: restrictions.db (SQLite)

Tables
  meta(key, value)                  version, built_at, regions, attribution
  coverage(region, ring)            outer boundary rings (encoded polyline)
  feat(tile, type, id, tags, geom)  restriction features, one row per tile
                                    they touch; geom = encoded polyline

Tile = floor((lat+90)*20)*10000 + floor((lon+180)*20)   (0.05 degree grid)

Data (c) OpenStreetMap contributors, ODbL 1.0.
"""
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone

TILE_SIZE = 0.05

PHYSICAL_KEYS = {
    "maxheight", "maxheight:physical", "maxheight:hgv",
    "maxwidth", "maxwidth:physical", "maxwidth:hgv",
    "maxlength", "maxlength:hgv",
    "maxweight", "maxweight:hgv",
    "maxweightrating", "maxweightrating:hgv",
    "maxaxleload", "maxaxleload:hgv",
}
ACCESS_NO_KEYS = {"hgv", "goods", "hgv_articulated", "trailer", "overtaking:hgv"}
CONTEXT_KEYS = {
    "highway", "bridge", "tunnel", "man_made", "layer", "name", "ref",
    "oneway", "access", "motor_vehicle", "barrier",
    "hgv:conditional", "maxweight:conditional", "mindistance:hgv",
}
KEEP_KEYS = PHYSICAL_KEYS | ACCESS_NO_KEYS | CONTEXT_KEYS
NO_VALUES = {"", "none", "no", "default", "unsigned", "below_default"}


def encode_polyline(points):
    """Google encoded polyline, precision 5, points = [(lat, lon), ...]."""
    result = []
    prev_lat = prev_lon = 0
    for lat, lon in points:
        ilat = int(round(lat * 1e5))
        ilon = int(round(lon * 1e5))
        for value in (ilat - prev_lat, ilon - prev_lon):
            value = ~(value << 1) if value < 0 else (value << 1)
            while value >= 0x20:
                result.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            result.append(chr(value + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(result)


def tile_of(lat, lon):
    return int(math.floor((lat + 90) / TILE_SIZE)) * 10000 + int(math.floor((lon + 180) / TILE_SIZE))


def tiles_for(points):
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    r0, r1 = int(math.floor((min(lats) + 90) / TILE_SIZE)), int(math.floor((max(lats) + 90) / TILE_SIZE))
    c0, c1 = int(math.floor((min(lons) + 180) / TILE_SIZE)), int(math.floor((max(lons) + 180) / TILE_SIZE))
    # Very long ways (rare): cap the fan-out, index their points instead.
    if (r1 - r0 + 1) * (c1 - c0 + 1) > 60:
        return sorted({tile_of(lat, lon) for lat, lon in points})
    return [r * 10000 + c for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]


def active(value):
    return str(value).strip().lower() not in NO_VALUES


def is_physical(tags):
    return any(k in tags and active(tags[k]) for k in PHYSICAL_KEYS)


def is_access_no(tags):
    return any(str(tags.get(k, "")).strip().lower() == "no" for k in ACCESS_NO_KEYS)


def is_bridge(tags):
    return active(tags.get("bridge", "no")) or tags.get("man_made") == "bridge"


def read_features(path):
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def feature_points(feature):
    geometry = feature.get("geometry") or {}
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Point":
        return [(coords[1], coords[0])]
    if kind == "LineString":
        return [(c[1], c[0]) for c in coords]
    if kind == "MultiLineString":
        return [(c[1], c[0]) for line in coords for c in line]
    return []


def read_poly(path):
    """Outer rings of a Geofabrik .poly file as [(lat, lon), ...] lists."""
    rings, current, hole = [], None, False
    with open(path, "r", encoding="utf-8") as handle:
        lines = [l.strip() for l in handle if l.strip()]
    for line in lines[1:]:
        if line == "END":
            if current is not None:
                if not hole and len(current) >= 3:
                    rings.append(current)
                current = None
            continue
        if current is None:
            hole = line.startswith("!")
            current = []
            continue
        parts = line.split()
        if len(parts) >= 2:
            current.append((float(parts[1]), float(parts[0])))
    simplified = []
    for ring in rings:
        step = max(1, len(ring) // 3000)
        simplified.append(ring[::step])
    return simplified


def main(work_dir, output_path):
    if os.path.exists(output_path):
        os.remove(output_path)
    db = sqlite3.connect(output_path)
    db.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE coverage(region TEXT, ring TEXT);
        CREATE TABLE feat(tile INTEGER, type TEXT, id INTEGER, tags TEXT, geom TEXT);
        """
    )

    regions = sorted(f[:-len(".geojsonseq")] for f in os.listdir(work_dir) if f.endswith(".geojsonseq"))
    if not regions:
        sys.exit("no .geojsonseq files found")

    seen = set()
    restrictions = []
    bridges = []

    for region in regions:
        poly = os.path.join(work_dir, region + ".poly")
        if not os.path.exists(poly):
            sys.exit(f"missing boundary {poly}: refusing to build without coverage")
        for ring in read_poly(poly):
            db.execute("INSERT INTO coverage VALUES (?, ?)", (region, encode_polyline(ring)))

        for feature in read_features(os.path.join(work_dir, region + ".geojsonseq")):
            props = feature.get("properties") or {}
            osm_type = {"n": "node", "w": "way", "node": "node", "way": "way"}.get(str(props.get("@type", "")))
            osm_id = props.get("@id")
            if osm_type is None or osm_id is None:
                continue
            key = (osm_type, int(osm_id))
            if key in seen:  # regions overlap at borders
                continue
            points = feature_points(feature)
            if not points:
                continue
            tags = {k: v for k, v in props.items() if k in KEEP_KEYS}

            if osm_type == "way":
                restriction = "highway" in tags and (is_physical(tags) or is_access_no(tags))
            else:
                restriction = is_physical(tags)

            if restriction:
                seen.add(key)
                restrictions.append((osm_type, int(osm_id), tags, points))
            elif osm_type == "way" and is_bridge(tags):
                bridges.append((osm_type, int(osm_id), tags, points))

    # Keep only bridges near a height restriction (the app uses them to say
    # "BRIDGE" for a low clearance under it). All bridges would be huge.
    cell = 0.002
    height_cells = set()
    for _, _, tags, points in restrictions:
        if any(k.startswith("maxheight") for k in tags):
            for lat, lon in points:
                height_cells.add((int(lat // cell), int(lon // cell)))

    def near_height(points):
        for lat, lon in points:
            r, c = int(lat // cell), int(lon // cell)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if (r + dr, c + dc) in height_cells:
                        return True
        return False

    kept_bridges = 0
    rows = 0
    for osm_type, osm_id, tags, points in restrictions + [
        b for b in bridges if (b[0], b[1]) not in seen and near_height(b[3])
    ]:
        if (osm_type, osm_id) not in seen:
            seen.add((osm_type, osm_id))
            kept_bridges += 1
        tags_json = json.dumps(tags, ensure_ascii=False, separators=(",", ":"))
        geom = encode_polyline(points)
        for tile in tiles_for(points):
            db.execute("INSERT INTO feat VALUES (?, ?, ?, ?, ?)", (tile, osm_type, osm_id, tags_json, geom))
            rows += 1

    db.execute("CREATE INDEX feat_tile ON feat(tile)")
    built = datetime.now(timezone.utc)
    meta = {
        "version": built.strftime("%Y-%m-%d"),
        "built_at": built.isoformat(timespec="seconds"),
        "regions": ",".join(regions),
        "restrictions": str(len(restrictions)),
        "bridges": str(kept_bridges),
        "tile_size": str(TILE_SIZE),
        "attribution": "(c) OpenStreetMap contributors, ODbL 1.0",
    }
    db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    db.commit()
    db.execute("VACUUM")
    db.close()
    print(f"regions={regions} restrictions={len(restrictions)} bridges={kept_bridges} rows={rows}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
