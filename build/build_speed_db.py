#!/usr/bin/env python3
"""
Builds the TruckGPS offline speed-limit database.

Input  (work dir): <region>.speedseq   osmium geojsonseq export of roads
                                        (motorway … unclassified + links)
                                        that carry a maxspeed tag
Output: one database per country, so the app downloads only the countries
        a route passes through:
          speedlimits-<region>.db.gz   (gzip of the SQLite file)
          speed-manifest.json          (version, and per country: size,
                                        sha256, bounding box and a simplified
                                        border from <region>.poly)

Usage: build_speed_db.py <work_dir> <out_dir>

Tables
  meta(key, value)
  seg(tile INTEGER, kmh INTEGER, hgv INTEGER, oneway INTEGER, geom TEXT)
      kmh    = general limit (km/h)
      hgv    = maxspeed:hgv (km/h) or 0
      oneway = 1 forward only, -1 backward only, 0 both
      geom   = encoded polyline (precision 5), simplified to ~4 m

Tile = floor((lat+90)*20)*10000 + floor((lon+180)*20)   (0.05 degree grid)

Data (c) OpenStreetMap contributors, ODbL 1.0.
"""
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_db import encode_polyline, read_features, tiles_for  # noqa: E402

SIMPLIFY_METERS = 4.0

# Implicit limits ("FR:urban" …). Only values that are clear national rules.
IMPLICIT = {
    "urban": 50, "zone30": 30, "zone:30": 30, "zone20": 20, "zone:20": 20,
    "living_street": 20, "walk": 6,
}
IMPLICIT_COUNTRY = {
    ("FR", "rural"): 80, ("FR", "motorway"): 130, ("FR", "trunk"): 110,
    ("ES", "rural"): 90, ("ES", "motorway"): 120, ("ES", "trunk"): 100,
    ("PT", "rural"): 90, ("PT", "motorway"): 120, ("PT", "trunk"): 100,
    ("BE", "rural"): 90, ("BE-VLG", "rural"): 70, ("BE-WAL", "rural"): 90,
    ("BE-BRU", "rural"): 70, ("BE", "motorway"): 120, ("BE", "trunk"): 120,
    ("NL", "rural"): 80, ("NL", "motorway"): 130, ("NL", "trunk"): 100,
    ("MA", "rural"): 100, ("MA", "motorway"): 120, ("MA", "urban"): 60,
    ("DE", "rural"): 100, ("DE", "urban"): 50, ("IT", "rural"): 90,
    ("IT", "motorway"): 130, ("IT", "trunk"): 110, ("CH", "rural"): 80,
    ("CH", "motorway"): 120, ("AT", "rural"): 100, ("AT", "motorway"): 130,
    ("LU", "rural"): 90, ("LU", "motorway"): 130,
}

NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/h|kmh|kph)?\s*$", re.I)


def parse_speed(value):
    """km/h from a maxspeed value, or None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("none", "signals", "variable", "no", "unknown"):
        return None
    if ";" in text:  # several values: take the lowest (safer)
        speeds = [parse_speed(part) for part in text.split(";")]
        speeds = [s for s in speeds if s]
        return min(speeds) if speeds else None
    m = NUMBER.match(text)
    if m:
        speed = float(m.group(1))
        if m.group(2) and m.group(2).lower() == "mph":
            speed *= 1.609344
        speed = int(round(speed))
        return speed if 5 <= speed <= 150 else None
    if ":" in text:
        country, kind = text.split(":", 1)
        kind = kind.lower()
        if (country.upper(), kind) in IMPLICIT_COUNTRY:
            return IMPLICIT_COUNTRY[(country.upper(), kind)]
        if kind in IMPLICIT:
            return IMPLICIT[kind]
    if text.lower() == "walk":
        return 6
    return None


def simplify(points, tolerance):
    """Douglas-Peucker on (lat, lon) using a local metric projection."""
    if len(points) <= 2:
        return points
    lat0 = math.radians(points[0][0])
    kx = 111320.0 * math.cos(lat0)
    ky = 110540.0
    xy = [(p[1] * kx, p[0] * ky) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tolerance * tolerance
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        best, best_d = -1, -1.0
        for i in range(a + 1, b):
            px, py = xy[i]
            if length2 == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
                cx, cy = ax + t * dx, ay + t * dy
                d = (px - cx) ** 2 + (py - cy) ** 2
            if d > best_d:
                best, best_d = i, d
        if best_d > tol2:
            keep[best] = True
            stack.append((a, best))
            stack.append((best, b))
    return [p for p, k in zip(points, keep) if k]


def line_points(feature):
    geometry = feature.get("geometry") or {}
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "LineString":
        return [[(c[1], c[0]) for c in coords]]
    if kind == "MultiLineString":
        return [[(c[1], c[0]) for c in line] for line in coords]
    return []


def parse_poly(path, max_points=160):
    """Geofabrik .poly → list of outer rings [[lat, lon], …] (holes skipped),
    each thinned to at most max_points points."""
    if not os.path.exists(path):
        return []
    rings, ring, hole = [], None, False
    with open(path, encoding="utf-8", errors="replace") as handle:
        lines = [line.strip() for line in handle]
    for line in lines[1:]:
        if not line:
            continue
        if line == "END":
            if ring is not None:
                if not hole and len(ring) >= 3:
                    rings.append(ring)
                ring = None
            continue
        if ring is None:
            hole = line.startswith("!")
            ring = []
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                ring.append([round(float(parts[1]), 4), round(float(parts[0]), 4)])
            except ValueError:
                pass
    thinned = []
    for r in rings:
        step = max(1, math.ceil(len(r) / max_points))
        thinned.append(r[::step])
    return thinned


def build_region(work_dir, region, output_path):
    """Builds one country's SQLite file. Returns (ways, rows, bbox)."""
    if os.path.exists(output_path):
        os.remove(output_path)
    db = sqlite3.connect(output_path)
    db.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE seg(tile INTEGER, kmh INTEGER, hgv INTEGER, oneway INTEGER, geom TEXT);
        """
    )
    seen = set()
    ways = 0
    rows = 0
    south, west, north, east = 90.0, 180.0, -90.0, -180.0
    for feature in read_features(os.path.join(work_dir, region + ".speedseq")):
        props = feature.get("properties") or {}
        osm_id = props.get("@id")
        if osm_id is None or osm_id in seen:
            continue
        seen.add(osm_id)

        kmh = parse_speed(props.get("maxspeed"))
        if kmh is None:
            forward = parse_speed(props.get("maxspeed:forward"))
            backward = parse_speed(props.get("maxspeed:backward"))
            both = [s for s in (forward, backward) if s]
            kmh = min(both) if both else None
        hgv = parse_speed(props.get("maxspeed:hgv")) or 0
        if kmh is None and not hgv:
            continue
        if kmh is None:
            kmh = hgv

        oneway_tag = str(props.get("oneway", "")).strip().lower()
        highway = str(props.get("highway", ""))
        if oneway_tag in ("yes", "true", "1") or highway in ("motorway", "motorway_link"):
            oneway = 1
        elif oneway_tag == "-1":
            oneway = -1
        else:
            oneway = 0
        if oneway_tag == "no":
            oneway = 0

        for points in line_points(feature):
            if len(points) < 2:
                continue
            simple = simplify(points, SIMPLIFY_METERS)
            for lat, lon in simple:
                south, north = min(south, lat), max(north, lat)
                west, east = min(west, lon), max(east, lon)
            geom = encode_polyline(simple)
            for tile in tiles_for(simple):
                db.execute("INSERT INTO seg VALUES (?, ?, ?, ?, ?)", (tile, kmh, hgv, oneway, geom))
                rows += 1
        ways += 1

    db.execute("CREATE INDEX seg_tile ON seg(tile)")
    built = datetime.now(timezone.utc)
    meta = {
        "version": built.strftime("%Y-%m-%d"),
        "built_at": built.isoformat(timespec="seconds"),
        "regions": region,
        "ways": str(ways),
        "attribution": "(c) OpenStreetMap contributors, ODbL 1.0",
    }
    db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    db.commit()
    db.execute("VACUUM")
    db.close()
    bbox = [round(south, 4), round(west, 4), round(north, 4), round(east, 4)] if ways else []
    return ways, rows, bbox


def main(work_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    regions = sorted(f[:-len(".speedseq")] for f in os.listdir(work_dir) if f.endswith(".speedseq"))
    if not regions:
        sys.exit("no .speedseq files found")

    built = datetime.now(timezone.utc)
    countries = []
    for region in regions:
        db_path = os.path.join(out_dir, f"speedlimits-{region}.db")
        gz_path = db_path + ".gz"
        ways, rows, bbox = build_region(work_dir, region, db_path)
        if not ways:
            print(f"{region}: no roads with a speed limit, skipped")
            os.remove(db_path)
            continue
        with open(db_path, "rb") as src, gzip.open(gz_path, "wb", compresslevel=9) as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
        os.remove(db_path)
        digest = hashlib.sha256()
        with open(gz_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        size = os.path.getsize(gz_path)
        countries.append({
            "name": region,
            "bytes": size,
            "sha256": digest.hexdigest(),
            "ways": ways,
            "bbox": bbox,
            "poly": parse_poly(os.path.join(work_dir, region + ".poly")),
        })
        print(f"{region}: ways={ways} rows={rows} gz={size / 1048576:.1f} MB bbox={bbox}")

    manifest = {
        "format": 2,
        "version": built.strftime("%Y-%m-%d"),
        "built_at": built.isoformat(timespec="seconds"),
        "attribution": "(c) OpenStreetMap contributors, ODbL 1.0",
        "countries": countries,
    }
    with open(os.path.join(out_dir, "speed-manifest.json"), "w") as handle:
        json.dump(manifest, handle, separators=(",", ":"))
    print(f"manifest: {len(countries)} countries, "
          f"{sum(c['bytes'] for c in countries) / 1048576:.1f} MB in total")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
