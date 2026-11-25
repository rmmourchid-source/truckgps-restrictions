"""Generate TruckGPS day/night map styles (OpenMapTiles schema, OpenFreeMap tiles)."""
import json, sys, os

OUT = sys.argv[1]

def ip(stops, base=1.4):
    e = ["interpolate", ["exponential", base], ["zoom"]]
    for z, v in stops:
        e += [z, v]
    return e

def lin(stops):
    e = ["interpolate", ["linear"], ["zoom"]]
    for z, v in stops:
        e += [z, v]
    return e

NAME = ["coalesce", ["get", "name:latin"], ["get", "name"]]

# class: (minzoom, fill widths, casing widths)
ROADS = [
    ("service",   14, [(14, 0.6), (15, 1.6), (18, 7)],               [(14, 1.2), (15, 2.6), (18, 9)]),
    ("minor",     12, [(12, 0.6), (13, 1.0), (15, 3.6), (18, 14)],   [(12, 1.0), (13, 1.8), (15, 5.2), (18, 17)]),
    ("tertiary",  11, [(11, 0.8), (13, 1.8), (15, 5), (18, 17)],     [(11, 1.2), (13, 3.0), (15, 7), (18, 21)]),
    ("secondary",  9, [(9, 0.7), (12, 1.8), (15, 6), (18, 19)],      [(9, 1.1), (12, 3.0), (15, 8), (18, 23)]),
    ("primary",    7, [(7, 0.7), (12, 2.4), (15, 7), (18, 22)],      [(7, 1.1), (12, 3.8), (15, 9.5), (18, 27)]),
    ("trunk",      5, [(5, 0.6), (8, 1.3), (12, 3), (15, 8), (18, 24)], [(5, 1.0), (8, 2.0), (12, 4.5), (15, 11), (18, 30)]),
    ("motorway",   4, [(4, 0.6), (8, 1.6), (12, 3.5), (15, 9), (18, 26)], [(4, 1.0), (8, 2.4), (12, 5), (15, 12), (18, 32)]),
]

PAL = {
    "day": dict(
        bg="#F3F2EE", residential="#ECEAE4", industrial="#EBE8EE", wood="#D9E8CF", grass="#E2EED8",
        park="#D2E6C3", water="#A8D2F1", building="#E3E0D9", building_line="#D2CEC5",
        runway="#DAD8D2", boundary="#A29DB2", rail="#B3AFA6", ferry="#7FB3D9",
        road={"motorway": ("#F5A04B", "#C47129"), "trunk": ("#FAC66A", "#C4963C"),
              "primary": ("#FFE288", "#C9AA55"), "secondary": ("#FFFFFF", "#BDB7AB"),
              "tertiary": ("#FFFFFF", "#C6C1B6"), "minor": ("#FFFFFF", "#D0CBC1"),
              "service": ("#FFFFFF", "#D6D2C9")},
        tunnel_opacity=0.55, path="#B9B4AA",
        text="#2E2E2E", text_soft="#5B5B5B", halo="#FFFFFF", water_text="#3E78A8",
        ref_text="#FFFFFF", ref_motorway="#1E5BB8", ref_other="#C2402D", house="#8A8A8A",
    ),
    "night": dict(
        bg="#18212E", residential="#1C2533", industrial="#1E2433", wood="#1B2C27", grass="#1C2E28",
        park="#1C3229", water="#0E2A45", building="#232D3B", building_line="#2D3848",
        runway="#2A3342", boundary="#5E6A80", rail="#3E4757", ferry="#2E5F86",
        road={"motorway": ("#C27A33", "#5E3916"), "trunk": ("#A0803F", "#4D3D1D"),
              "primary": ("#7F7448", "#3E3824"), "secondary": ("#3B4759", "#232C39"),
              "tertiary": ("#364254", "#222B38"), "minor": ("#313C4D", "#212A36"),
              "service": ("#2C3646", "#202833")},
        tunnel_opacity=0.5, path="#3A4454",
        text="#E1E6EC", text_soft="#AEB7C2", halo="#18212E", water_text="#6FA3CF",
        ref_text="#FFFFFF", ref_motorway="#2A64C4", ref_other="#B03A2A", house="#7D8794",
    ),
}

def road_filter(cls, brunnel):
    f = ["all", ["==", ["get", "class"], cls]]
    if brunnel == "tunnel":
        f.append(["==", ["get", "brunnel"], "tunnel"])
    elif brunnel == "bridge":
        f.append(["==", ["get", "brunnel"], "bridge"])
    else:
        f.append(["!", ["in", ["get", "brunnel"], ["literal", ["tunnel", "bridge"]]]])
    return f

def build(mode):
    p = PAL[mode]
    L = []
    add = L.append
    add({"id": "background", "type": "background", "paint": {"background-color": p["bg"]}})
    add({"id": "landuse-residential", "type": "fill", "source": "openmaptiles", "source-layer": "landuse",
         "minzoom": 9, "filter": ["in", ["get", "class"], ["literal", ["residential", "suburb", "neighbourhood"]]],
         "paint": {"fill-color": p["residential"]}})
    add({"id": "landuse-industrial", "type": "fill", "source": "openmaptiles", "source-layer": "landuse",
         "minzoom": 11, "filter": ["in", ["get", "class"], ["literal", ["industrial", "commercial", "retail", "railway"]]],
         "paint": {"fill-color": p["industrial"]}})
    add({"id": "landcover-wood", "type": "fill", "source": "openmaptiles", "source-layer": "landcover",
         "filter": ["==", ["get", "class"], "wood"], "paint": {"fill-color": p["wood"], "fill-opacity": 0.7}})
    add({"id": "landcover-grass", "type": "fill", "source": "openmaptiles", "source-layer": "landcover",
         "filter": ["in", ["get", "class"], ["literal", ["grass", "farmland"]]],
         "paint": {"fill-color": p["grass"], "fill-opacity": 0.5}})
    add({"id": "park", "type": "fill", "source": "openmaptiles", "source-layer": "park",
         "paint": {"fill-color": p["park"], "fill-opacity": 0.8}})
    add({"id": "water", "type": "fill", "source": "openmaptiles", "source-layer": "water",
         "paint": {"fill-color": p["water"]}})
    add({"id": "waterway", "type": "line", "source": "openmaptiles", "source-layer": "waterway", "minzoom": 9,
         "paint": {"line-color": p["water"], "line-width": ip([(9, 0.6), (14, 2), (18, 6)])}})
    add({"id": "aeroway-runway", "type": "line", "source": "openmaptiles", "source-layer": "aeroway", "minzoom": 11,
         "filter": ["in", ["get", "class"], ["literal", ["runway", "taxiway"]]],
         "paint": {"line-color": p["runway"], "line-width": ip([(11, 2), (14, 12), (18, 60)])}})
    add({"id": "building", "type": "fill", "source": "openmaptiles", "source-layer": "building", "minzoom": 14,
         "paint": {"fill-color": p["building"], "fill-outline-color": p["building_line"],
                   "fill-opacity": lin([(14, 0.0), (15, 1.0)])}})
    add({"id": "boundary-state", "type": "line", "source": "openmaptiles", "source-layer": "boundary", "minzoom": 5,
         "filter": ["all", ["==", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
         "paint": {"line-color": p["boundary"], "line-width": 0.8, "line-dasharray": [3, 2], "line-opacity": 0.6}})
    add({"id": "boundary-country", "type": "line", "source": "openmaptiles", "source-layer": "boundary",
         "filter": ["all", ["==", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1]],
         "paint": {"line-color": p["boundary"], "line-width": ip([(3, 1), (10, 2.2)]), "line-dasharray": [4, 2]}})

    # Roads: tunnels, then ground level, then bridges; casings below fills.
    for brunnel in ("tunnel", "ground", "bridge"):
        tun = brunnel == "tunnel"
        for cls, minz, fill_w, case_w in ROADS:
            fill, case = p["road"][cls]
            add({"id": f"road-{brunnel}-{cls}-casing", "type": "line", "source": "openmaptiles",
                 "source-layer": "transportation", "minzoom": minz, "filter": road_filter(cls, brunnel),
                 "layout": {"line-cap": "butt" if tun else "round", "line-join": "round"},
                 "paint": {"line-color": case, "line-width": ip(case_w),
                           **({"line-opacity": p["tunnel_opacity"], "line-dasharray": [2, 1]} if tun else {})}})
        for cls, minz, fill_w, case_w in ROADS:
            fill, case = p["road"][cls]
            add({"id": f"road-{brunnel}-{cls}", "type": "line", "source": "openmaptiles",
                 "source-layer": "transportation", "minzoom": minz, "filter": road_filter(cls, brunnel),
                 "layout": {"line-cap": "round", "line-join": "round"},
                 "paint": {"line-color": fill, "line-width": ip(fill_w),
                           **({"line-opacity": p["tunnel_opacity"]} if tun else {})}})
        if brunnel == "ground":
            add({"id": "road-path", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
                 "minzoom": 15, "filter": ["in", ["get", "class"], ["literal", ["track", "path"]]],
                 "paint": {"line-color": p["path"], "line-width": ip([(15, 1), (18, 2.5)]), "line-dasharray": [2, 2]}})
            add({"id": "rail", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
                 "minzoom": 10, "filter": ["in", ["get", "class"], ["literal", ["rail", "transit"]]],
                 "paint": {"line-color": p["rail"], "line-width": ip([(10, 0.6), (18, 3)]), "line-dasharray": [3, 3]}})
            add({"id": "ferry", "type": "line", "source": "openmaptiles", "source-layer": "transportation",
                 "minzoom": 7, "filter": ["==", ["get", "class"], "ferry"],
                 "paint": {"line-color": p["ferry"], "line-width": 1.2, "line-dasharray": [4, 3]}})

    halo = {"text-halo-color": p["halo"], "text-halo-width": 2, "text-halo-blur": 0.5}
    add({"id": "water-name", "type": "symbol", "source": "openmaptiles", "source-layer": "water_name", "minzoom": 8,
         "layout": {"text-field": NAME, "text-font": ["Noto Sans Italic"], "text-size": 13, "text-max-width": 8},
         "paint": {"text-color": p["water_text"], **halo}})
    add({"id": "house-number", "type": "symbol", "source": "openmaptiles", "source-layer": "housenumber", "minzoom": 17,
         "layout": {"text-field": ["get", "housenumber"], "text-font": ["Noto Sans Regular"], "text-size": 11},
         "paint": {"text-color": p["house"], **halo}})
    add({"id": "road-name", "type": "symbol", "source": "openmaptiles", "source-layer": "transportation_name",
         "minzoom": 12,
         "filter": ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary", "secondary", "tertiary", "minor", "service"]]],
         "layout": {"symbol-placement": "line", "text-field": NAME, "text-font": ["Noto Sans Regular"],
                    "text-size": ip([(12, 10.5), (15, 13.5), (18, 17)], 1.2), "text-max-angle": 30,
                    "text-padding": 2, "symbol-spacing": 350},
         "paint": {"text-color": p["text"], **halo}})
    add({"id": "road-ref", "type": "symbol", "source": "openmaptiles", "source-layer": "transportation_name",
         "minzoom": 7,
         "filter": ["all", ["has", "ref"], ["<=", ["coalesce", ["get", "ref_length"], 0], 7],
                    ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]]],
         "layout": {"symbol-placement": "line", "symbol-spacing": 450, "text-field": ["get", "ref"],
                    "text-font": ["Noto Sans Bold"], "text-size": 12, "text-rotation-alignment": "viewport",
                    "text-padding": 4},
         "paint": {"text-color": p["ref_text"],
                   "text-halo-color": ["match", ["get", "class"], "motorway", p["ref_motorway"], p["ref_other"]],
                   "text-halo-width": 3}})
    places = [
        ("place-neighbourhood", ["suburb", "neighbourhood", "quarter"], 13, None, [(13, 11), (16, 13)], "Noto Sans Regular", p["text_soft"]),
        ("place-village", ["village", "hamlet"], 11, None, [(11, 11), (15, 14)], "Noto Sans Regular", p["text"]),
        ("place-town", ["town"], 7, None, [(7, 11), (12, 16)], "Noto Sans Bold", p["text"]),
        ("place-city", ["city"], 4, 15, [(4, 11), (10, 18)], "Noto Sans Bold", p["text"]),
    ]
    for pid, classes, minz, maxz, size, font, color in places:
        layer = {"id": pid, "type": "symbol", "source": "openmaptiles", "source-layer": "place", "minzoom": minz,
                 "filter": ["in", ["get", "class"], ["literal", classes]],
                 "layout": {"text-field": NAME, "text-font": [font], "text-size": lin(size), "text-max-width": 8},
                 "paint": {"text-color": color, **halo}}
        if maxz:
            layer["maxzoom"] = maxz
        add(layer)
    add({"id": "place-country", "type": "symbol", "source": "openmaptiles", "source-layer": "place", "maxzoom": 7,
         "filter": ["==", ["get", "class"], "country"],
         "layout": {"text-field": NAME, "text-font": ["Noto Sans Bold"], "text-size": lin([(2, 11), (6, 16)]),
                    "text-transform": "uppercase", "text-max-width": 7},
         "paint": {"text-color": p["text_soft"], **halo}})

    return {
        "version": 8,
        "name": f"TruckGPS {mode}",
        "metadata": {"truckgps:style": f"{mode}-1"},
        "sources": {"openmaptiles": {"type": "vector", "url": "https://tiles.openfreemap.org/planet",
                                     "attribution": "© OpenMapTiles © OpenStreetMap contributors"}},
        "glyphs": "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
        "layers": L,
    }

os.makedirs(OUT, exist_ok=True)
for mode in ("day", "night"):
    style = build(mode)
    ids = [l["id"] for l in style["layers"]]
    assert len(ids) == len(set(ids)), "duplicate layer id"
    with open(os.path.join(OUT, f"truckgps-{mode}.json"), "w") as f:
        json.dump(style, f, indent=1)
    print(mode, len(ids), "layers")
