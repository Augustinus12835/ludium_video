#!/usr/bin/env python3
"""Geographically exact folio maps from free elevation data — no image-model call.

Generated maps get geography wrong: in one of our episodes grounded Gemini Pro squeezed Japan down
to the Philippines' latitude because the prompt framed Japan "just below the middle" on a map that
also had to reach Malacca. Folio maps are unlettered (names are Pin/Tag overlays), so the picture
can come straight from real data, and every overlay position is computed from lon/lat, not read
off a grid by eye.

Two sources, picked by how much detail the window needs (`source: "auto"`):
  * "ne"      — Natural Earth GRAY_HR_SR_OB_DR (public domain): 21600×10800 shaded relief with
                lakes and drainages, 60 px/degree. For wide windows (≥ ~43° of longitude).
  * "terrain" — AWS Terrain Tiles (Mapzen terrarium, SRTM/GMTED/ETOPO, public, no key), fetched at
                the zoom that gives the 4K canvas native detail (down to a battlefield), hillshaded
                here; Natural Earth 10m lakes (and rivers, on windows ≥ 3°) drawn on top. Tiles are
                cached in inputs/maps/terrain_tiles/.
                Mostly public-domain sources; a few regions' DEMs ask for a credit line in what you
                publish (e.g. EU-DEM in Europe: "Produced using Copernicus data and information funded
                by the European Union") — github.com/tilezen/joerd/blob/master/docs/attribution.md.
Both are rendered in the house engraved look: relief-shaded land on cream paper, ruled sea with a
coastal halo, an ink coastline. Projection: Mercator (true shapes at mid-latitudes); "plate"
(plate carrée) is available too.

Modern-data caveats (use a prompted Gemini Pro map instead — folio.json `prompt`, no `bbox`): a
coastline that has moved since the period and matters to the story (Lade island at Miletus, the
pass at Thermopylae, silted harbours); windows beyond ~60° latitude (Mercator stretches them).
Data (~250 MB) downloads on first use into inputs/maps/.
Modern reservoirs and canals are left out of the vector layers.

In folio.json:   {"id": "asia_sea", "kind": "map", "bbox": [68, 164, 44, -10],
                  "pins": {"Goa": [73.83, 15.50], "Malacca": [102.25, 2.20]}}
`folio.py assets` renders it ($0) and writes assets/folio/<id>.geo.json — the fitted bbox and every
pin in the 1920×1080 overlay space; `folio.py pins <video> <id> [Name=lon,lat …]` prints them.

    python scripts/geo_map.py render --bbox 68 164 44 -10 -o /tmp/x.jpg [--source terrain] [--projection plate]
    python scripts/geo_map.py pins --geo assets/folio/asia_sea.geo.json Kagoshima=130.55,31.60
"""
import argparse
import json
import math
import shutil
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
MAPS = REPO / "inputs/maps"
NE_RASTER = MAPS / "natural_earth/GRAY_HR_SR_OB_DR/GRAY_HR_SR_OB_DR.tif"
NE_RASTER_URL = "https://naciscdn.org/naturalearth/10m/raster/GRAY_HR_SR_OB_DR.zip"
NE_VECTOR = MAPS / "natural_earth/vector"
NE_VECTOR_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/{}.geojson"
TILES = MAPS / "terrain_tiles"
TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
RENDER_VERSION = 1          # bump when the look changes, so folio.py re-renders cached maps
NE_PX_PER_DEG = 60
NE_MIN_SPAN = 43            # narrower than this, the NE raster would be upscaled > 1.5× on a 4K canvas
MAX_ZOOM = 14
INK = np.array([35, 31, 27], np.float32)      # = folio.py INK_RGB / PAPER_RGB
PAPER = np.array([232, 225, 211], np.float32)
WIPE = (1496, 424)          # presenter.FOLIO_WIPE_ZONE_PX: x ≥ 1496 and y ≤ 424 in the 1920×1080 space
HIGH_LAT = 60


# ---------------------------------------------------------------- projection

def merc(lat):
    lat = max(-85.0, min(85.0, lat))
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def unmerc(y):
    return math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)


def fit_bbox(bbox, projection="mercator", aspect=16 / 9):
    """Grow the smaller side of [W, E, N, S] about its centre until the window is `aspect` in
    projected units (so nothing asked for is cut off)."""
    W, E, N, S = map(float, bbox)
    if not (W < E and N > S):
        raise ValueError(f"bbox {bbox}: want [west, east, north, south] with west < east, north > south")
    fy = merc if projection == "mercator" else (lambda v: v)
    gy = unmerc if projection == "mercator" else (lambda v: v)
    yN, yS = fy(N), fy(S)
    w, h = E - W, yN - yS
    if w / h < aspect:
        c, w = (W + E) / 2, h * aspect
        W, E = c - w / 2, c + w / 2
    else:
        c, h = (yN + yS) / 2, w / aspect
        yN, yS = c + h / 2, c - h / 2
        N, S = gy(yN), gy(yS)
    if W < -180 or E > 180:
        raise ValueError(f"bbox crosses the antimeridian ({W:.1f}..{E:.1f}) — not supported")
    return [round(W, 4), round(E, 4), round(N, 4), round(S, 4)]


def to_px(lon, lat, bbox, projection="mercator", w=1920, h=1080):
    W, E, N, S = bbox
    fy = merc if projection == "mercator" else (lambda v: v)
    return [round((lon - W) / (E - W) * w), round((fy(N) - fy(lat)) / (fy(N) - fy(S)) * h)]


def grid_lonlat(bbox, projection, w, h):
    """lon (w,) and lat (h,) at the pixel centres of the output canvas."""
    W, E, N, S = bbox
    lon = W + (np.arange(w) + 0.5) / w * (E - W)
    if projection == "mercator":
        yN, yS = merc(N), merc(S)
        ym = yN - (np.arange(h) + 0.5) / h * (yN - yS)
        lat = np.degrees(2 * np.arctan(np.exp(np.radians(ym))) - np.pi / 2)
    else:
        lat = N - (np.arange(h) + 0.5) / h * (N - S)
    return lon, lat


def choose_source(bbox):
    return "ne" if bbox[1] - bbox[0] >= NE_MIN_SPAN else "terrain"


# ---------------------------------------------------------------- data

def _fetch(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    tmp.rename(dest)


def ne_relief(bbox, projection, w, h):
    """Gray shaded relief (0–255) of the window, resampled from the NE raster."""
    import cv2
    if not NE_RASTER.exists():   # first use: 92 MB zip → 233 MB GeoTIFF, once
        import zipfile
        z = NE_RASTER.parent.with_suffix(".zip")
        print(f"  downloading Natural Earth shaded relief (92 MB, once) → {NE_RASTER.parent}", flush=True)
        _fetch(NE_RASTER_URL, z)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(NE_RASTER.parent)
        z.unlink()
    Image.MAX_IMAGE_PIXELS = None
    lon, lat = grid_lonlat(bbox, projection, w, h)
    col = (lon + 180) * NE_PX_PER_DEG - 0.5
    row = (90 - lat) * NE_PX_PER_DEG - 0.5
    c0, c1 = max(0, int(col.min()) - 2), min(21600, int(col.max()) + 3)
    r0, r1 = max(0, int(row.min()) - 2), min(10800, int(row.max()) + 3)
    src = np.asarray(Image.open(NE_RASTER).crop((c0, r0, c1, r1)).convert("L"), np.float32)
    mx = np.broadcast_to((col - c0)[None, :], (h, w)).astype(np.float32)
    my = np.broadcast_to((row - r0)[:, None], (h, w)).astype(np.float32)
    interp = cv2.INTER_AREA if (c1 - c0) > w else cv2.INTER_CUBIC
    if interp == cv2.INTER_AREA:   # remap has no area filter: pre-shrink, then remap
        s = w / (c1 - c0) * 1.25
        if s < 1:
            src = cv2.resize(src, (round(src.shape[1] * s), round(src.shape[0] * s)), interpolation=cv2.INTER_AREA)
            mx, my = mx * s, my * s
        interp = cv2.INTER_CUBIC
    return cv2.remap(src, mx, my, interp, borderMode=cv2.BORDER_REPLICATE)


def tile_zoom(bbox, w):
    need = w / (bbox[1] - bbox[0])                 # output px per degree of longitude
    return max(1, min(MAX_ZOOM, math.ceil(math.log2(need * 360 / 256))))


def terrain_dem(bbox, projection, w, h):
    """Elevation (m) of the window from AWS Terrain Tiles (terrarium), resampled to the canvas."""
    import cv2
    z = tile_zoom(bbox, w)
    n = 2 ** z
    lon, lat = grid_lonlat(bbox, projection, w, h)
    gx = (lon + 180) / 360 * n * 256
    latr = np.radians(np.clip(lat, -85.05, 85.05))
    gy = (1 - np.log(np.tan(latr) + 1 / np.cos(latr)) / np.pi) / 2 * n * 256
    tx0, tx1 = int(gx.min() // 256), int(gx.max() // 256)
    ty0, ty1 = int(gy.min() // 256), int(gy.max() // 256)
    tiles = [(x, y) for y in range(ty0, ty1 + 1) for x in range(tx0, tx1 + 1)]
    if len(tiles) > 900:
        raise RuntimeError(f"{len(tiles)} tiles at zoom {z} — window too large for terrain; use source ne")

    def get(t):
        x, y = t
        p = TILES / str(z) / str(x) / f"{y}.png"
        if not p.exists():
            _fetch(TILE_URL.format(z=z, x=x, y=y), p)
        a = np.asarray(Image.open(p).convert("RGB"), np.float32)
        return t, a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768

    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    with ThreadPoolExecutor(16) as ex:
        for (x, y), e in ex.map(get, tiles):
            mosaic[(y - ty0) * 256:(y - ty0 + 1) * 256, (x - tx0) * 256:(x - tx0 + 1) * 256] = e
    mx = np.broadcast_to((gx - tx0 * 256 - 0.5)[None, :], (h, w)).astype(np.float32)
    my = np.broadcast_to((gy - ty0 * 256 - 0.5)[:, None], (h, w)).astype(np.float32)
    s = w / mosaic.shape[1] * 1.25
    if s < 1:
        mosaic = cv2.resize(mosaic, (round(mosaic.shape[1] * s), round(mosaic.shape[0] * s)), interpolation=cv2.INTER_AREA)
        mx, my = mx * s, my * s
    return cv2.remap(mosaic, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE), z


def vector(name):
    p = NE_VECTOR / f"{name}.geojson"
    if not p.exists():
        _fetch(NE_VECTOR_URL.format(name), p)
    return json.loads(p.read_text())


MODERN = ("reservoir", "canal")


def _rings(geom):
    t, c = geom["type"], geom["coordinates"]
    if t == "Polygon":
        return [c]
    if t == "MultiPolygon":
        return c
    if t == "LineString":
        return [[c]]
    if t == "MultiLineString":
        return [[l] for l in c]
    return []


def raster_vectors(bbox, projection, w, h, rivers: bool):
    """(lake mask, river ink strength) from Natural Earth 10m vectors; reservoirs and canals left out."""
    import cv2
    W, E, N, S = bbox
    fy = merc if projection == "mercator" else (lambda v: v)
    yN, yS = fy(N), fy(S)

    def pts(line):
        a = np.asarray(line, np.float64)
        if projection == "mercator":
            la = np.radians(np.clip(a[:, 1], -85, 85))
            yy = np.degrees(np.log(np.tan(np.pi / 4 + la / 2)))
        else:
            yy = a[:, 1]
        return np.stack([(a[:, 0] - W) / (E - W) * w, (yN - yy) / (yN - yS) * h], 1)

    def inside(line):
        a = np.asarray(line)
        return not (a[:, 0].max() < W or a[:, 0].min() > E or a[:, 1].max() < S or a[:, 1].min() > N)

    lakes = np.zeros((h, w), np.uint8)
    for f in vector("ne_10m_lakes")["features"]:
        if any(m in (f["properties"].get("featurecla") or "").lower() for m in MODERN):
            continue
        for poly in _rings(f["geometry"]):
            if not inside(poly[0]):
                continue
            cv2.fillPoly(lakes, [np.round(pts(poly[0]) * 4).astype(np.int32)], 1, cv2.LINE_AA, shift=2)
            for hole in poly[1:]:
                cv2.fillPoly(lakes, [np.round(pts(hole) * 4).astype(np.int32)], 0, cv2.LINE_AA, shift=2)
    riv = np.zeros((h, w), np.float32)
    if rivers:
        k = w / 3840
        for f in vector("ne_10m_rivers_lake_centerlines")["features"]:
            pr = f["properties"]
            if any(m in (pr.get("featurecla") or "").lower() for m in MODERN) or not f["geometry"]:
                continue
            width = max(1, round((3.2 - 0.22 * (pr.get("scalerank") or 6)) * k * 1.6))
            for (line,) in _rings(f["geometry"]):
                if inside(line):
                    cv2.polylines(riv, [np.round(pts(line) * 4).astype(np.int32)], False, 1.0, width, cv2.LINE_AA, shift=2)
    return lakes.astype(bool), riv


def hillshade(dem, bbox, projection, w, h, az=315.0, alt=45.0):
    W, E, N, S = bbox
    _, lat = grid_lonlat(bbox, projection, w, h)
    m_per_px_x = (E - W) / w * 111320 * np.cos(np.radians(lat))[:, None]
    if projection == "mercator":
        m_per_px_y = m_per_px_x                       # conformal
    else:
        m_per_px_y = (N - S) / h * 110540
    span = E - W
    zf = max(1.5, 2.5 * math.sqrt(span))              # wide windows need relief exaggerated to read
    d = np.where(dem > 0, dem, 0) * zf
    dy, dx = np.gradient(d)
    dx, dy = dx / m_per_px_x, dy / m_per_px_y
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(-dx, dy)
    zen, azr = np.radians(90 - alt), np.radians(360 - az + 90)
    hs = np.cos(zen) * np.cos(slope) + np.sin(zen) * np.sin(slope) * np.cos(azr - aspect)
    return np.clip(hs, 0, 1)


# ---------------------------------------------------------------- style

def engrave(land, relief, river, lakes):
    """Compose the house look. land: bool; relief: land tone 0–1 (1 = paper); river: 0–1 ink."""
    import cv2
    from scipy import ndimage as ndi
    h, w = land.shape
    k = w / 3840
    land = land & ~lakes
    lab, n = ndi.label(~land)
    if n:
        sizes = ndi.sum(~land, lab, range(1, n + 1))
        land |= np.isin(lab, np.where(sizes < 250 * k * k)[0] + 1)       # specks inside land
    lab, n = ndi.label(land)
    if n:
        sizes = ndi.sum(land, lab, range(1, n + 1))
        land &= np.isin(lab, np.where(sizes >= max(4, 10 * k * k))[0] + 1)   # sea speckle; small islands stay
    water = ~land
    Lland = (0.62 + 0.38 * relief ** 0.8) * (1 - 0.55 * river)
    dist = ndi.distance_transform_edt(water) / k
    y = np.arange(h)[:, None] / k
    ruling = (np.sin(y * 2 * np.pi / 7.0) > 0.55).astype(np.float32)
    near = np.exp(-dist / 70.0)
    far = 0.22 + 0.10 * np.exp(-dist / 400.0)
    Lsea = 1 - ruling * (0.18 * far + 0.42 * near) - 0.10 * np.exp(-dist / 28.0)
    L = np.where(land, Lland, Lsea)
    edge = land ^ ndi.binary_erosion(land, iterations=max(1, round(2 * k)), border_value=1)
    L = np.where(edge, 0.08, L)
    L = cv2.GaussianBlur(L.astype(np.float32), (0, 0), 0.6 * k)
    rgb = INK * (1 - L[..., None]) + PAPER * L[..., None]
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


def render(bbox, out: Path, source="auto", projection="mercator", size=(3840, 2160), pins=None, fit=True):
    """Render the map to `out` (jpg) and return its geo record (also written beside it as .geo.json)."""
    import cv2
    from scipy import ndimage as ndi
    w, h = size
    fb = fit_bbox(bbox, projection, w / h) if fit else [float(v) for v in bbox]
    src = choose_source(fb) if source == "auto" else source
    zoom = None
    if src == "ne":
        g = ne_relief(fb, projection, w, h)
        land = cv2.GaussianBlur(g, (0, 0), 0.7 * w / 3840) > 118          # ocean ~80-100, land ~140-240
        land = ndi.binary_closing(np.pad(land, 4, mode="edge"), iterations=2)[4:-4, 4:-4]   # close drainages
        relief = np.clip((g - 120) / (245 - 120), 0, 1)
        bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        river = np.clip((bh - 10) / 25, 0, 1) * land
        lakes = np.zeros_like(land)
    elif src == "terrain":
        dem, zoom = terrain_dem(fb, projection, w, h)
        # the DEM is noisy right at sea level (blocky ocean fill): threshold it smoothed, then clean
        k = w / 3840
        land = cv2.GaussianBlur(dem, (0, 0), 2.0 * k) > 1.0
        it = max(1, round(2 * k))
        land = ndi.binary_closing(ndi.binary_opening(np.pad(land, 6, mode="edge"), iterations=it), iterations=it)[6:-6, 6:-6]
        relief = np.clip(hillshade(dem, fb, projection, w, h) / 0.80, 0, 1)
        lakes, river = raster_vectors(fb, projection, w, h, rivers=(fb[1] - fb[0]) >= 3)
        river = river * land
    else:
        raise ValueError(f"unknown source {source!r}")
    im = engrave(land, relief, river, lakes)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, quality=93)
    geo = {"projection": projection, "bbox": fb, "asked_bbox": [float(v) for v in bbox], "source": src,
           "zoom": zoom, "size": [1920, 1080], "version": RENDER_VERSION,
           "pins": {k: to_px(v[0], v[1], fb, projection) for k, v in (pins or {}).items()}}
    out.with_suffix(".geo.json").write_text(json.dumps(geo, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return geo


def pin_warnings(geo, presenter=True) -> list:
    """Pins off the canvas, or (presenter=True) under the presenter's top-right window."""
    out = []
    for k, (x, y) in geo.get("pins", {}).items():
        if not (0 <= x <= 1920 and 0 <= y <= 1080):
            out.append(f"{k} {[x, y]} is off the map")
        elif presenter and x >= WIPE[0] and y <= WIPE[1]:
            out.append(f"{k} {[x, y]} sits under the presenter window (x ≥ {WIPE[0]}, y ≤ {WIPE[1]})")
    N, S = geo["bbox"][2], geo["bbox"][3]
    if max(abs(N), abs(S)) > HIGH_LAT:
        out.append(f"window reaches {max(abs(N), abs(S)):.0f}° latitude — Mercator stretches it; consider a prompted map")
    return out


def parse_places(items):
    out = {}
    for s in items:
        name, ll = s.rsplit("=", 1)
        lon, lat = map(float, ll.split(","))
        out[name] = [lon, lat]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("WEST", "EAST", "NORTH", "SOUTH"))
    r.add_argument("-o", "--out", type=Path, required=True)
    r.add_argument("--source", choices=["auto", "ne", "terrain"], default="auto")
    r.add_argument("--projection", choices=["mercator", "plate"], default="mercator")
    r.add_argument("--no-fit", action="store_true", help="use the bbox as given (it must already be 16:9)")
    r.add_argument("places", nargs="*", help="Name=lon,lat")
    p = sub.add_parser("pins")
    p.add_argument("--geo", type=Path, required=True)
    p.add_argument("places", nargs="+", help="Name=lon,lat")
    a = ap.parse_args()
    if a.cmd == "render":
        geo = render(a.bbox, a.out, a.source, a.projection, pins=parse_places(a.places), fit=not a.no_fit)
        print(f"wrote {a.out}  source={geo['source']} zoom={geo['zoom']} bbox={geo['bbox']}")
    else:
        geo = json.loads(a.geo.read_text())
        geo["pins"] = {k: to_px(v[0], v[1], geo["bbox"], geo["projection"]) for k, v in parse_places(a.places).items()}
    for k, v in geo["pins"].items():
        print(f"  {k}: {v}")
    for wmsg in pin_warnings(geo):
        print(f"  ⚠ {wmsg}")


if __name__ == "__main__":
    main()
