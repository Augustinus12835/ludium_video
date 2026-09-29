#!/usr/bin/env python3
"""
FOLIO mode — "a book of plates": free-form Remotion scenes over one narration.

A humanities video in folio mode is narration-first (argument map → script →
TTS → word timeline), then:

  folio.json (director subagent)  → scenes laid over the narration by verbatim
                                    anchors + the asset list (engraved plates,
                                    portraits, isolated objects, grounded maps,
                                    and "then"-states of the same composition)
  folio.py place                  → anchors → frame-exact scene spans
  folio.py assets                 → generated images (OpenAI gpt-image; grounded
                                    maps on Gemini), normalised to one sepia
                                    duotone; objects become ink cutouts (RGBA)
  frames/scene_NN.tsx (scene authors, after LOOKING at the assets)
  folio.py still --auto           → look-fix-look stills at every cue
  folio.py lint                   → cues resolve, assets exist, house rules
  folio.py render                 → one bundle, scenes rendered in parallel
  folio.py compile                → segment-wise join + narration
  folio.py sheet                  → review contact sheet

Scene TSX imports only 'react', 'remotion' and '../../folio'
(remotion/src/folio.tsx — stage, chrome, karaoke captions and primitives).

Usage:
    folio.py place   pipeline/<L>/Video-1
    folio.py assets  pipeline/<L>/Video-1 [--only a,b] [--force] [--jobs 6]
    folio.py still   pipeline/<L>/Video-1 --scene 3 [--at 2.5,7] [--auto]
    folio.py lint    pipeline/<L>/Video-1
    folio.py render  pipeline/<L>/Video-1 [--only 3,4] [--jobs 3] [--force]
    folio.py compile pipeline/<L>/Video-1
    folio.py sheet   pipeline/<L>/Video-1
    folio.py contact pipeline/<L>/Video-1            (asset contact sheets for QA)
    folio.py audit   pipeline/<L>/Video-1            (director gate on folio.json)
    folio.py prompt  direct|scenes pipeline/<L>/Video-1 [--scenes 0-12] [--out F]
    folio.py grid    pipeline/<L>/Video-1 <asset>    (coordinate grid for overlays)
    folio.py refinish pipeline/<L>/Video-1           (re-post-process raw images, no API)
    folio.py status  pipeline/<L>/Video-1
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from scripts.utils.subtitle_compact import compact_words  # noqa: E402
from scripts.utils.ffmpeg_compile import (  # noqa: E402
    build_audio_track, clip_segment_cmd, concat_mux, encode_segments)

try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except Exception:  # noqa: BLE001
    pass

REMOTION = REPO / "remotion"
FPS = 30
FLASH = "gemini-3.1-flash-lite-image"
PRO = "gemini-3-pro-image"
# Escalation ladder for stubborn assets: the default model, one reroll on it, then a
# stronger model. Maps stay on grounded Gemini Pro.
TIERS = {"lite": FLASH, "flash": "gemini-3.1-flash-image", "pro": PRO,
         "gpt": os.getenv("FOLIO_GPT_MODEL", "gpt-image-2")}
LADDER = ["lite", "flash", "gpt"]
# Default rung for folio assets: OpenAI gpt-image (in an A/B against Gemini flash-lite it
# gave far more detail and instruction-following — correct counts, recognisable objects —
# at ≈$0.04 a plate vs ≈$0.034). OpenAI rate-limits images per minute by account tier and a
# call takes ~35–45 s; at 20 images/min `assets --jobs 12` runs at the ceiling (429s back
# off automatically). FOLIO_DEFAULT_TIER=lite switches the default to Gemini flash-lite
# (then only GOOGLE_CLOUD_API_KEY is needed).
DEFAULT_TIER = os.getenv("FOLIO_DEFAULT_TIER", "gpt")
GPT_SIZE = {"16:9": "1536x1024", "3:4": "1024x1536", "1:1": "1024x1024"}
GEN_URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

# Duotone every image is normalised to — the same ink and paper as folio.tsx PAL.
INK_RGB = np.array([35, 31, 27], np.float32)
PAPER_RGB = np.array([232, 225, 211], np.float32)

# ─────────────────────────────────────────────────────────────── prompts
# Positive wording only: a negative in an image prompt paints its token, and
# ALL-CAPS prompt text gets lettered onto the picture.
HOUSE_STYLE = (
    "A nineteenth-century steel engraving in sepia-black ink on warm cream paper, in the "
    "manner of the engravings in illustrated Victorian histories of Greece and of Flaxman and "
    "Stuart and Revett: fine parallel hatching, cross-hatching and stipple, crisp confident "
    "linework, even daylight with gentle modelling, one ink colour only. A clean unlettered "
    "picture: every surface is drawing or plain paper."
)

KIND_CLAUSE = {
    "plate": ("Composition: a full-bleed wide landscape picture whose hatching runs out to all four "
              "edges of the sheet, with a clear focal subject and a readable depth of field."),
    "portrait": ("Composition: a vignette head-and-shoulders portrait bust in three-quarter view, "
                 "the head in the upper half of a tall sheet, shoulders cut by the lower edge, set "
                 "against open cream paper with a light hatched halo fading into the sheet."),
    "object": ("Composition: a vignette engraving of one single isolated object floating in the "
               "centre of an open sheet of plain cream paper, its hatching fading softly into the "
               "blank paper, wide open paper on every side, only a faint hatched contact shadow beneath it."),
    "map": ("Composition: an antique engraved map that fills the whole sheet: coastlines with "
            "hatched shading and fine water-lining, mountains as hachures, rivers as fine "
            "lines, sea with delicate horizontal ruling, a small compass rose in open sea."),
}
ASPECT = {"plate": "16:9", "portrait": "3:4", "object": "1:1", "map": "16:9"}
FULL_BLEED_RETRY = ("\n\nThe picture is cut by the edges of the sheet like a detail of a larger "
                    "engraving: its hatching and forms run straight off all four edges.")
MAP_PREAMBLE = (
    "Before drawing, use Google Search to verify the real geography of the places named "
    "below: their true relative positions, the real shapes of the coastlines, islands and "
    "straits. Then draw the map so the geography is factually correct.\n\n")


def build_prompt(asset: dict, folio: dict, kind: str) -> str:
    parts = [HOUSE_STYLE, KIND_CLAUSE[kind]]
    world = (folio.get("world") or "").strip()
    if world and kind != "map" and asset.get("world", True):
        parts.append(f"Period and material culture: {world}")
    cast = {c["id"]: c for c in folio.get("cast", []) if isinstance(c, dict) and c.get("id")}
    looks = [f"{cast[c]['name']}: {cast[c].get('look', '')}" for c in asset.get("cast", []) if c in cast]
    if looks:
        parts.append("People in this picture, drawn exactly as described (and as in the reference "
                     "portraits provided): " + " ".join(looks))
    if kind == "map":
        places = asset.get("places") or []
        if places:
            parts.append("Letter ONLY these place names, in small engraved serif italic, each at "
                         "its true location: " + "; ".join(places) + ".")
        else:
            parts.append("A clean unlettered map.")
    if asset.get("of"):
        parts.append("Redraw the supplied engraving keeping exactly the same composition, framing, "
                     "viewpoint, scale and lighting, and change only this: " + asset["prompt"].strip())
    else:
        parts.append("Subject: " + asset["prompt"].strip())
    text = "\n\n".join(parts)
    if kind == "map":
        text = MAP_PREAMBLE + text
    return text


# ─────────────────────────────────────────────────────────────── io helpers

def vdir(p) -> Path:
    d = Path(p).resolve()
    if not (d / "folio.json").exists():
        sys.exit(f"no folio.json in {d}")
    return d


def load_folio(vd: Path) -> dict:
    return json.loads((vd / "folio.json").read_text(encoding="utf-8"))


def save_folio(vd: Path, f: dict) -> None:
    (vd / "folio.json").write_text(json.dumps(f, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_timeline(vd: Path) -> dict:
    p = vd / "narration_timeline.json"
    if not p.exists():
        sys.exit(f"missing {p} — run scripts/build_narration_timeline.py {vd}")
    return json.loads(p.read_text(encoding="utf-8"))


def norm_tok(s: str) -> list:
    return re.sub(r"[^a-z0-9]+", " ", s.lower().replace("’", "").replace("'", "")).split()


def flat_tokens(words):
    """[(token, word_index)] — a hyphenated word yields several tokens (same as folio.tsx)."""
    out = []
    for i, w in enumerate(words):
        for t in norm_tok(w["word"]):
            out.append((t, i))
    return out


def find_phrase(words, flat, phrase, t_lo=-1e9, t_hi=1e9):
    """All start-word indices where `phrase` matches, restricted to [t_lo, t_hi] starts."""
    want = norm_tok(phrase)
    hits = []
    if not want:
        return hits
    n = len(want)
    for k in range(len(flat) - n + 1):
        if [flat[k + j][0] for j in range(n)] == want:
            wi = flat[k][1]
            # must start at a word boundary (first token of that word)
            if k > 0 and flat[k - 1][1] == wi:
                continue
            s = words[wi]["start"]
            if t_lo - 0.06 <= s <= t_hi:
                hits.append((wi, flat[k + n - 1][1]))
    return hits


def end_time(folio: dict, tl: dict) -> float:
    return float(folio.get("end_at") or tl["total_duration"])


def tail_secs(folio: dict) -> float:
    """Silent hold after the last word (folio.json "tail", seconds): the final scene is
    extended by it and compile pads the narration with silence, so the ending breathes
    before the video ends. Ignored for end_at previews."""
    return 0.0 if folio.get("end_at") else max(0.0, float(folio.get("tail", 0) or 0))


# ─────────────────────────────────────────────────────────────── place

CUT_LEAD = 0.25  # a scene begins this long before its anchor word, so its dissolve lands on it


def cmd_place(a) -> int:
    vd = vdir(a.video)
    f = load_folio(vd)
    tl = load_timeline(vd)
    words = tl["words"]
    flat = flat_tokens(words)
    total = end_time(f, tl)
    scenes = sorted(f["scenes"], key=lambda s: s["index"])
    errs = 0
    cursor = 0.0
    for i, s in enumerate(scenes):
        if s["index"] != i:
            print(f"  ERROR scene indices must be contiguous from 0 (got {s['index']} at {i})")
            errs += 1
        if i == 0:
            s["t_start"] = 0.0
            continue
        hits = [h for h in find_phrase(words, flat, s.get("anchor", ""), cursor - 0.01) if True]
        if not hits:
            print(f"  ERROR scene {s['index']}: anchor not found after {cursor:.1f}s: \"{s.get('anchor')}\"")
            errs += 1
            continue
        ws = words[hits[0][0]]["start"]
        s["t_start"] = round(max(cursor + 1.0, ws - CUT_LEAD), 3)
        cursor = ws
    if errs:
        return 1
    tf = int(round((total + tail_secs(f)) * FPS))
    for i, s in enumerate(scenes):
        s["f0"] = int(round(s["t_start"] * FPS))
    for i, s in enumerate(scenes):
        s["f1"] = scenes[i + 1]["f0"] if i + 1 < len(scenes) else tf
        s["t_end"] = round(s["f1"] / FPS, 3)
        d = (s["f1"] - s["f0"]) / FPS
        s["dur"] = round(d, 2)
        flag = "  <- short" if d < 4 else "  <- long" if d > 45 else ""
        print(f"  scene {s['index']:>3}  {s['t_start']:8.2f}–{s['t_end']:8.2f}  {d:5.1f}s  pl.{s.get('plate', '')}{flag}")
    if scenes and scenes[-1]["f1"] <= scenes[-1]["f0"]:
        print("  ERROR last scene starts after the end of the narration")
        return 1
    f["scenes"] = scenes
    save_folio(vd, f)
    print(f"placed {len(scenes)} scenes over {total:.1f}s ({len(scenes) / (total / 60):.1f} scenes/min)")
    return 0


# ─────────────────────────────────────────────────────────────── captions

MAX_CHARS = 112


def _glen(ws):
    return sum(len(w["word"]) for w in ws) + max(0, len(ws) - 1)


def _split_group(ws):
    if _glen(ws) <= MAX_CHARS or len(ws) < 4:
        return [ws]
    mid = _glen(ws) / 2
    best, best_d = None, 1e9
    for i in range(2, len(ws) - 1):  # split AFTER word i-1
        if re.search(r"[,;:—–)]$", ws[i - 1]["word"]):
            d = abs(_glen(ws[:i]) - mid)
            if d < best_d:
                best, best_d = i, d
    if best is None or best_d > mid * 0.6:
        acc, best = 0, len(ws) // 2
        for i, w in enumerate(ws):
            acc += len(w["word"]) + 1
            if acc >= mid:
                best = max(2, min(len(ws) - 2, i + 1))
                break
    return _split_group(ws[:best]) + _split_group(ws[best:])


def build_captions(words, t_end):
    disp = compact_words([{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words])
    disp = [w for w in disp if w["start"] < t_end]
    groups, cur = [], []
    for w in disp:
        cur.append(w)
        if re.search(r"[.?!][\"'”’)]*$", w["word"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        for part in _split_group(g):
            out.append({"t0": round(part[0]["start"], 3), "t1": round(part[-1]["end"], 3),
                        "words": [{"w": x["word"], "s": round(x["start"], 3), "e": round(x["end"], 3)} for x in part]})
    return out


# ─────────────────────────────────────────────────────────────── assets

ASSET_DIR = "assets/folio"


def asset_kind(folio: dict, a: dict) -> str:
    if a.get("of"):
        base = next((x for x in folio["assets"] if x["id"] == a["of"]), None)
        if base is None:
            raise ValueError(f"asset {a['id']}: base '{a['of']}' not declared")
        return a.get("kind") or asset_kind(folio, base)
    return a.get("kind", "plate")


def asset_file(vd: Path, folio: dict, a: dict) -> Path:
    k = asset_kind(folio, a)
    return vd / ASSET_DIR / (f"{a['id']}.png" if k == "object" else f"{a['id']}.jpg")


def sha(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12] if path.exists() else "-"


def _api_key() -> str:
    k = os.getenv("GOOGLE_CLOUD_API_KEY")
    if not k:
        raise RuntimeError("GOOGLE_CLOUD_API_KEY not set")
    return k


def gpt_image(prompt: str, aspect: str, model: str, refs=(), retries=8) -> bytes:
    """OpenAI gpt-image generation; with reference images it uses images.edit (same
    composition for a then-state, likeness for a cast member)."""
    from openai import OpenAI
    client = OpenAI()
    last = ""
    for attempt in range(retries):
        try:
            if refs:
                files = [open(r, "rb") for r in refs]
                try:
                    r = client.images.edit(model=model, image=files, prompt=prompt,
                                           size=GPT_SIZE.get(aspect, "1536x1024"), quality="high")
                finally:
                    for fh in files:
                        fh.close()
            else:
                r = client.images.generate(model=model, prompt=prompt, n=1,
                                           size=GPT_SIZE.get(aspect, "1536x1024"), quality="high")
            b64 = r.data[0].b64_json
            if b64:
                u = getattr(r, "usage", None)
                if u is not None:
                    try:
                        with open(REPO / "pipeline" / "_folio_library" / "gpt_usage.log", "a") as fh:
                            fh.write(f"{time.strftime('%F %T')} {model} {aspect} refs={len(refs)} {u}\n")
                    except OSError:
                        pass
                return base64.b64decode(b64)
            last = "no image in response"
        except Exception as e:  # noqa: BLE001
            last = str(e)
            if "429" in last or "rate" in last.lower():
                time.sleep(20 + 15 * attempt)   # per-minute image rate limit: wait the window out
                continue
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(last)


def make_image(prompt: str, aspect: str, tier: str, refs=(), grounding=False) -> bytes:
    model = TIERS[tier]
    if tier == "gpt":
        return gpt_image(prompt, aspect, model, refs)
    return gemini_image(prompt, aspect, model, refs, grounding)


def asset_tier(a: dict, kind: str) -> str:
    if kind == "map":
        return "pro"
    return a.get("model", DEFAULT_TIER)


def next_tier(tier: str):
    i = LADDER.index(tier) if tier in LADDER else 0
    return LADDER[i + 1] if i + 1 < len(LADDER) else None


def gemini_image(prompt: str, aspect: str, model: str, refs=(), grounding=False, retries=4) -> bytes:
    parts = []
    for r in refs:
        mime = "image/png" if str(r).endswith(".png") else "image/jpeg"
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(Path(r).read_bytes()).decode()}})
    parts.append({"text": prompt})
    cfg = {"responseModalities": ["IMAGE", "TEXT"], "imageConfig": {"aspectRatio": aspect}}
    if model == PRO:
        cfg["imageConfig"]["imageSize"] = "2K"
    payload = {"contents": [{"parts": parts}], "generationConfig": cfg}
    if grounding:
        payload["tools"] = [{"google_search": {}}]
    last = ""
    for attempt in range(retries):
        try:
            r = requests.post(GEN_URL.format(m=model), params={"key": _api_key()}, json=payload, timeout=240)
            if r.status_code == 200:
                for p in r.json()["candidates"][0]["content"]["parts"]:
                    d = p.get("inlineData") or p.get("inline_data")
                    if d:
                        return base64.b64decode(d["data"])
                last = "no image part in response"
            else:
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code == 400 and "imageSize" in r.text and "imageSize" in cfg["imageConfig"]:
                    cfg["imageConfig"].pop("imageSize")
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(4 * (attempt + 1) ** 2)
    raise RuntimeError(last)


def duotone(img: Image.Image) -> np.ndarray:
    """Luminance → levels-stretched → ink/paper duotone. Returns L in [0,1] (1 = paper)."""
    g = np.asarray(img.convert("L"), np.float32) / 255.0
    lo, hi = np.percentile(g, 0.4), np.percentile(g, 99.6)
    hi = max(hi, lo + 0.2)
    return np.clip((g - lo) / (hi - lo), 0, 1)


def to_rgb(L: np.ndarray) -> Image.Image:
    rgb = INK_RGB[None, None, :] * (1 - L[..., None]) + PAPER_RGB[None, None, :] * L[..., None]
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    s = max(w / img.width, h / img.height)
    im = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def _smooth(v: np.ndarray, k: int) -> np.ndarray:
    return np.convolve(v, np.ones(k) / k, mode="same")


def erase_rules(L: np.ndarray, thr: float = 0.35) -> np.ndarray:
    """Blank thin straight rules (platemarks, border lines) that sit against empty paper."""
    ink = (1 - L) > thr
    L = L.copy()
    for axis in (1, 0):
        frac = ink.mean(axis=axis)
        n = len(frac)
        edge = int(n * 0.15)
        for i in np.where(frac > 0.4)[0]:
            # platemarks and border rules live near the sheet's edges; a long straight line
            # further in is part of the drawing (a stele's side, a column) — erasing
            # those cut transparent slits through a tall cutout.
            if edge <= i < n - edge:
                continue
            lo, hi = max(0, i - 14), min(n, i + 15)
            side_a = frac[lo:max(lo, i - 5)].mean() if i - 5 > lo else 0
            side_b = frac[min(hi, i + 6):hi].mean() if i + 6 < hi else 0
            if min(side_a, side_b) < 0.12:
                if axis == 1:
                    L[max(0, i - 2):i + 3, :] = 1.0
                else:
                    L[:, max(0, i - 2):i + 3] = 1.0
    return L


def margin_box(L: np.ndarray, cap: float = 0.25) -> list:
    """Crop box [x0, y0, x1, y1] that drops ONLY a true paper margin and platemark.

    Walks in from each edge over rows/columns that are STRICTLY blank paper (no inked
    pixel at all) or a thin rule (a run of ≤ 6 dense rows), stopping at the first row
    that carries any ink. Margins measure exactly 0; even a pale engraved sky carries
    0.1–4 % — a density threshold cropped away skies, mountains and hilltop buildings."""
    ink = (1 - L) > 0.25

    def walk(frac):
        n, k, lim = len(frac), 0, int(len(frac) * cap)
        while k < lim:
            if frac[k] < 0.0008:
                k += 1
                continue
            run = 0
            while k + run < n and frac[k + run] > 0.35 and run <= 6:
                run += 1
            if 0 < run <= 6 and k + run < lim:
                k += run
                continue
            break
        return k

    rows, cols = ink.mean(axis=1), ink.mean(axis=0)
    h, w = L.shape
    y0, x0 = walk(rows), walk(cols)
    y1, x1 = h - walk(rows[::-1]), w - walk(cols[::-1])
    return [int(x0), int(y0), int(x1), int(y1)]


def framed_edges(L: np.ndarray, box: list) -> dict:
    """Sides of a cropped plate where a framed picture starts: within 12 % of the edge,
    a run of ≥ 10 near-blank rows followed abruptly by dense picture (the picture's own
    border). Returns {side: offset from that edge} — a plate the model drew inside a
    frame with a paper gap."""
    x0, y0, x1, y1 = box
    ink = ((1 - L[y0:y1, x0:x1]) > 0.25)
    out = {}
    for side, frac in (("top", ink.mean(axis=1)), ("bottom", ink.mean(axis=1)[::-1]),
                       ("left", ink.mean(axis=0)), ("right", ink.mean(axis=0)[::-1])):
        n = len(frac)
        lim = min(n - 25, int(n * 0.07))
        quiet = 0
        for j in range(lim):
            if frac[j] > 0.3 and frac[j:j + 20].mean() > 0.2:
                if quiet >= 10:
                    out[side] = j + 3
                break
            # the gap inside a frame is near-blank (< 1.2 %); a pale sky is not
            quiet = quiet + 1 if frac[j] < 0.012 else (quiet if frac[j] < 0.4 else 0)
    return out


FRAMED: set = set()  # plates whose model output was drawn inside a frame (reroll candidates)


def finish_asset(raw: bytes, kind: str, out: Path, crop=None, trim=None):
    """Normalise a model output into the house duotone. Objects become RGBA ink
    cutouts cropped to their ink (or to `crop` — a base object's box, so a
    then-state lines up with its base exactly). Returns the crop box used."""
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if trim:  # manual override from folio.json: [left, top, right, bottom] fractions to cut
        l, t, r, b = trim
        img = img.crop((round(img.width * l), round(img.height * t),
                        round(img.width * (1 - r)), round(img.height * (1 - b))))
    L = duotone(img)
    if kind == "object":
        L = erase_rules(L, 0.1)
        ink = 1 - L
        floor = float(np.percentile(ink, 60)) + 0.09  # plate tint / paper tone → fully transparent
        a = np.clip((ink - floor) / (1 - floor), 0, 1) ** 0.9
        ys, xs = np.where(a > 0.12)
        if crop and crop[2] <= img.width and crop[3] <= img.height:
            x0, y0, x1, y1 = crop
        elif len(xs):
            m = int(0.03 * max(img.width, img.height))
            x0, x1 = max(0, xs.min() - m), min(img.width, xs.max() + m)
            y0, y1 = max(0, ys.min() - m), min(img.height, ys.max() + m)
        else:
            x0, y0, x1, y1 = 0, 0, img.width, img.height
        crop = [int(x0), int(y0), int(x1), int(y1)]
        a = a[y0:y1, x0:x1]
        rgba = np.zeros(a.shape + (4,), np.uint8)
        rgba[..., :3] = INK_RGB.astype(np.uint8)
        rgba[..., 3] = (a * 255).astype(np.uint8)
        im = Image.fromarray(rgba, "RGBA")
        if max(im.size) > 1600:
            s = 1600 / max(im.size)
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        im.save(out)
        return crop
    if kind in ("plate", "map"):
        h, w = L.shape
        if crop and crop[2] <= w and crop[3] <= h:
            box = crop
        else:
            box = margin_box(L)
            fr = framed_edges(L, box)
            box = [box[0] + fr.get("left", 0), box[1] + fr.get("top", 0),
                   box[2] - fr.get("right", 0), box[3] - fr.get("bottom", 0)]
            if fr:
                FRAMED.add(out.stem)
        crop = box
        # No rule-erasing on plates/maps: margin_box already drops a real platemark, and on
        # full-bleed renders (gpt-image) the eraser mistook dense edge hatching for rules and
        # blanked ~8 px paper strips down both sides.
        L = L[box[1]:box[3], box[0]:box[2]]
    else:
        L = erase_rules(L, 0.1)
    im = to_rgb(L)
    if kind == "portrait":
        im = cover(im, 900, 1200)
    else:
        im = cover(im, 1920, 1080)
    im.save(out, quality=92)
    return crop if kind in ("plate", "map") else None


def base_crop(vd: Path, folio: dict, a: dict):
    if not a.get("of"):
        return None
    base = next((x for x in folio["assets"] if x["id"] == a["of"]), None)
    f = vd / ASSET_DIR / f"{base['id']}.crop.json" if base else None
    return json.loads(f.read_text()) if f and f.exists() else None


def finish_and_record(vd: Path, folio: dict, a: dict, raw: bytes) -> None:
    kind = asset_kind(folio, a)
    crop = finish_asset(raw, kind, asset_file(vd, folio, a), base_crop(vd, folio, a), a.get("trim"))
    cf = vd / ASSET_DIR / f"{a['id']}.crop.json"
    if crop:
        cf.write_text(json.dumps(crop))


def gen_one(vd: Path, folio: dict, a: dict, force: bool) -> tuple:
    kind = asset_kind(folio, a)
    out = asset_file(vd, folio, a)
    raw_dir = vd / ASSET_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    refs = []
    if a.get("of"):
        base = next(x for x in folio["assets"] if x["id"] == a["of"])
        refs.append(raw_dir / f"{base['id']}.png")
    cast = {c["id"]: c for c in folio.get("cast", []) if c.get("id")}
    for cid in a.get("cast", []):
        pid = cast.get(cid, {}).get("portrait")
        if pid and pid != a["id"]:
            refs.append(raw_dir / f"{pid}.png")
    for r in refs:
        if not r.exists():
            return a["id"], f"WAIT (reference {r.name} not generated yet)"
    prompt = build_prompt(a, folio, kind)
    tier = asset_tier(a, kind)
    model = TIERS[tier]
    grounding = kind == "map"
    key = json.dumps({"p": prompt, "m": model, "k": kind, "refs": [sha(r) for r in refs], "seed": a.get("seed", 0)}, sort_keys=True)
    keyf = out.with_suffix(out.suffix + ".key")
    if out.exists() and keyf.exists() and keyf.read_text() == key and not force:
        return a["id"], "cached"
    lib = library_dir(vd) / a["id"] if a.get("shared") else None
    if lib is not None and not force and (lib / "key").exists() and (lib / "raw.png").exists():
        # Series library: the SAME image in every episode (likeness never drifts). The
        # library copy wins even if this episode's prompt text differs — pass --force
        # to regenerate it for the whole series.
        raw = (lib / "raw.png").read_bytes()
        (raw_dir / f"{a['id']}.png").write_bytes(raw)
        finish_and_record(vd, folio, a, raw)
        keyf.write_text(key)
        return a["id"], "library"
    t0 = time.time()
    note = ""
    # Framed plates: reroll once on the same model with a full-bleed nudge, then
    # climb the ladder (lite → flash → gpt) — never more than one retry per model.
    plan = [(tier, prompt), (tier, prompt + FULL_BLEED_RETRY)]
    up = next_tier(tier) if kind == "plate" and not a.get("of") else None
    while up:
        plan.append((up, prompt + FULL_BLEED_RETRY))
        up = next_tier(up)
    used = tier
    for attempt, (t_, p) in enumerate(plan):
        try:
            raw = make_image(p, a.get("aspect") or ASPECT[kind], t_, refs, grounding)
        except Exception as e:  # noqa: BLE001
            if attempt == 0:
                return a["id"], f"FAIL {e}"
            note += f" ({t_} failed: {str(e)[:80]})"
            continue
        used = t_
        (raw_dir / f"{a['id']}.png").write_bytes(raw)
        FRAMED.discard(a["id"])
        finish_and_record(vd, folio, a, raw)
        if a["id"] not in FRAMED or kind != "plate" or a.get("of"):
            break
        note = f" (framed → retry {attempt + 1})"
    (vd / ASSET_DIR / f"{a['id']}.model").write_text(used)
    if used != tier:
        note += f" [escalated to {used}]"
    (vd / ASSET_DIR / f"{a['id']}.prompt.txt").write_text(prompt, encoding="utf-8")
    keyf.write_text(key)
    if lib is not None:
        lib.mkdir(parents=True, exist_ok=True)
        (lib / "raw.png").write_bytes(raw)
        (lib / "key").write_text(key)
        (lib / "prompt.txt").write_text(prompt, encoding="utf-8")
    return a["id"], f"ok {kind} {used} {time.time() - t0:.0f}s{note}"


def library_dir(vd: Path) -> Path:
    """Series-wide shared assets: pipeline/_folio_library/<SERIES PREFIX>/<asset id>/."""
    m = re.match(r"^([A-Za-z]+)_", vd.parent.name)
    return REPO / "pipeline" / "_folio_library" / (m.group(1) if m else vd.parent.name)


def cmd_refinish(a) -> int:
    """Re-run post-processing on the stored raw model outputs (no API calls)."""
    vd = vdir(a.video)
    folio = load_folio(vd)
    ids = set(x.strip() for x in a.only.split(",")) if a.only else None
    for x in folio["assets"]:
        raw = vd / ASSET_DIR / "raw" / f"{x['id']}.png"
        if (ids is None or x["id"] in ids) and raw.exists():
            finish_and_record(vd, folio, x, raw.read_bytes())
            print(f"  refinished {x['id']}" + ("  [FRAMED — consider `assets --only … --force`]" if x["id"] in FRAMED else ""))
    return 0


def cmd_grid(a) -> int:
    """Asset with a labelled pixel grid (every 100 px) → folio_build/grid_<id>.png, for
    reading overlay coordinates off a plate/map (image space = Plate children space)."""
    from PIL import ImageDraw, ImageFont
    vd = vdir(a.video)
    folio = load_folio(vd)
    x = next((y for y in folio["assets"] if y["id"] == a.asset), None)
    if x is None:
        sys.exit(f"unknown asset {a.asset}")
    im = Image.open(asset_file(vd, folio, x)).convert("RGBA")
    if im.getchannel("A").getextrema()[0] < 255:
        bg = Image.new("RGBA", im.size, tuple(int(v) for v in PAPER_RGB) + (255,))
        im = Image.alpha_composite(bg, im)
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype(str(REMOTION / "public/fonts/Inter.ttf"), 18)
    except Exception:  # noqa: BLE001
        font = None
    for gx in range(0, im.width, 100):
        d.line([(gx, 0), (gx, im.height)], fill=(200, 30, 30, 150 if gx % 500 else 255), width=1)
        d.text((gx + 3, 3), str(gx), fill=(200, 30, 30, 255), font=font)
    for gy in range(0, im.height, 100):
        d.line([(0, gy), (im.width, gy)], fill=(30, 60, 200, 150 if gy % 500 else 255), width=1)
        d.text((3, gy + 3), str(gy), fill=(30, 60, 200, 255), font=font)
    out = vd / "folio_build" / f"grid_{a.asset}.png"
    out.parent.mkdir(exist_ok=True)
    im.convert("RGB").save(out)
    print(f"grid {out}  ({im.width}×{im.height})")
    return 0


NEG_RE = re.compile(r"\b(no|not|never|without|avoid|don't|nothing)\b", re.I)
CAPS_RE = re.compile(r"\b[A-Z]{4,}\b")


def cmd_audit(a) -> int:
    """Director-level gate on folio.json (run after `place`, before spending on images)."""
    vd = vdir(a.video)
    f = load_folio(vd)
    tl = load_timeline(vd)
    total = end_time(f, tl)
    mins = total / 60
    errs, warns = [], []
    scenes = sorted(f.get("scenes", []), key=lambda x: x["index"])
    assets = f.get("assets", [])
    ids = [x.get("id") for x in assets]
    if len(set(ids)) != len(ids):
        errs.append("duplicate asset ids")
    idset = set(ids)
    for x in assets:
        if not x.get("prompt"):
            errs.append(f"asset {x.get('id')}: no prompt")
        if x.get("of") and x["of"] not in idset:
            errs.append(f"asset {x['id']}: base '{x['of']}' not declared")
        k = asset_kind(f, x) if x.get("id") else "?"
        if k not in KIND_CLAUSE:
            errs.append(f"asset {x.get('id')}: unknown kind '{k}'")
        p = x.get("prompt", "")
        if NEG_RE.search(p):
            warns.append(f"asset {x['id']}: negative wording in prompt ({NEG_RE.search(p).group(0)!r}) — a negative paints its token")
        if CAPS_RE.search(p):
            warns.append(f"asset {x['id']}: ALL-CAPS word {CAPS_RE.search(p).group(0)!r} in prompt — it will be lettered")
        if k == "map" and len(x.get("places", [])) > 8:
            warns.append(f"asset {x['id']}: {len(x['places'])} places — maps letter ≤ 8 legibly")
    cast_ids = {c.get("id") for c in f.get("cast", [])}
    for c in f.get("cast", []):
        if c.get("portrait") and c["portrait"] not in idset:
            errs.append(f"cast {c.get('id')}: portrait asset '{c['portrait']}' not declared")
    for x in assets:
        for cid in x.get("cast", []):
            if cid not in cast_ids:
                errs.append(f"asset {x['id']}: cast id '{cid}' not declared")
    if not all("f0" in s_ for s_ in scenes):
        errs.append("scenes not placed — run folio.py place")
    # usage: scene TSX when written, else the brief
    used, with_asset = set(), 0
    for s_ in scenes:
        p = scene_tsx(vd, s_["index"])
        text = p.read_text(encoding="utf-8") if p.exists() else s_.get("brief", "") + " " + " ".join(s_.get("assets", []))
        u = used_assets(text, f) if p.exists() else set(brief_asset_ids(s_, {i: 1 for i in idset}))
        used |= u
        with_asset += bool(u)
        if "f0" in s_:
            d = (s_["f1"] - s_["f0"]) / FPS
            if d < 3.5:
                warns.append(f"scene {s_['index']}: {d:.1f}s — too short to read")
            if d > 45:
                warns.append(f"scene {s_['index']}: {d:.1f}s — split it or make sure it keeps building")
        if not s_.get("brief"):
            errs.append(f"scene {s_['index']}: no brief")
    bases = {x["of"] for x in assets if x.get("of")}
    for i in idset - used - bases:
        warns.append(f"asset {i} is declared but no scene uses it")
    if "off_screen" not in f:
        warns.append("no off_screen list — name what the narration only points to or disputes (use [] if nothing)")
    for o in f.get("off_screen", []):
        if not o.get("what"):
            errs.append("off_screen entry without 'what'")
    nights = [s_["index"] for s_ in scenes if s_.get("tone") == "night"]
    for i, j in zip(nights, nights[1:]):
        if j == i + 1:
            warns.append(f"night scenes {i} and {j} are consecutive")
    if len(nights) > max(3, round(mins / 6)):
        warns.append(f"{len(nights)} night scenes — keep them rare (2–3 per video)")
    n_img = len(assets)
    kinds = {}
    for x in assets:
        kinds[asset_kind(f, x)] = kinds.get(asset_kind(f, x), 0) + 1
    rate = len(scenes) / mins if mins else 0
    img_rate = n_img / mins if mins else 0
    share = with_asset / len(scenes) if scenes else 0
    if not 2.5 <= rate <= 6:
        warns.append(f"{rate:.1f} scenes/min — aim 3–5")
    if img_rate < 5:
        warns.append(f"{img_rate:.1f} images/min — generation is cheap; aim 6–10")
    if share < 0.8:
        warns.append(f"only {share:.0%} of scenes use a generated image — aim ≥ 85%")
    for e in errs:
        print(f"  ERROR {e}")
    for w in warns:
        print(f"  warn  {w}")
    print(f"audit: {len(scenes)} scenes ({rate:.1f}/min) · {n_img} assets ({img_rate:.1f}/min) {kinds} · "
          f"{share:.0%} scenes with an image · night {nights} · {len(errs)} error(s), {len(warns)} warning(s)")
    return 1 if errs else 0


def brief_asset_ids(scene: dict, ids) -> list:
    """Asset ids a brief names: backticked ids (house convention) + the `assets` list;
    briefs with no backticks fall back to whole-word matching."""
    brief = scene.get("brief", "")
    named = set(re.findall(r"`([A-Za-z0-9_\-]+)`", brief)) | set(scene.get("assets", []))
    if not re.search(r"`", brief):
        named |= {x for x in ids if re.search(rf"(?<![A-Za-z0-9_]){re.escape(x)}(?![A-Za-z0-9_])", brief)}
    return [x for x in ids if x in named]


def _scene_words(tl, s_):
    t0, t1 = s_["f0"] / FPS, s_["f1"] / FPS
    return " ".join(w["word"] for w in tl["words"] if t0 - 0.05 <= w["start"] < t1)


def cmd_prompt(a) -> int:
    """Render the director or scene-author prompt for this video (stdout or --out)."""
    vd = Path(a.video).resolve()
    tpl = REPO / "templates" / ("folio_director_prompt.md" if a.step == "direct" else "folio_scene_prompt.md")
    text = tpl.read_text(encoding="utf-8")
    rel = vd.relative_to(REPO) if vd.is_relative_to(REPO) else vd
    if a.step == "direct":
        tl = load_timeline(vd)
        total = tl["total_duration"]
        prev = sorted(p for p in (REPO / "pipeline").glob(f"{vd.parent.name.split('_')[0]}_*/Video-*/folio.json")
                      if p.parent != vd)
        text += (f"\n\n## This video\n\n- Video dir: `{rel}`\n- Narration: {total / 60:.1f} min "
                 f"({tl['word_count']} words) → aim {round(total / 60 * 3)}–{round(total / 60 * 5)} scenes, "
                 f"{round(total / 60 * 6)}–{round(total / 60 * 10)} assets.\n"
                 f"- Read: `{rel}/narration_timeline.txt`, `{rel}/script.json`, `{rel}/content.txt`"
                 + (f", `{rel}/argument.json`" if (vd / "argument.json").exists() else "") + "\n"
                 + (f"- Series precedent (carry world/cast/chrome/shared portraits forward): `{prev[-1].relative_to(REPO)}`\n" if prev else "- First folio episode of this series: establish `world`, `cast`, `chrome.running`.\n")
                 + f"- Write `{rel}/folio.json`, then `folio.py place` and `folio.py audit`.\n")
    else:
        f = load_folio(vd)
        tl = load_timeline(vd)
        want = set()
        for part in a.scenes.split(","):
            if "-" in part:
                lo, hi = part.split("-")
                want |= set(range(int(lo), int(hi) + 1))
            else:
                want.add(int(part))
        byid = {x["id"]: x for x in f["assets"]}
        sc = {s_["index"]: s_ for s_ in f["scenes"]}
        text += f"\n\n## Your scenes — video `{rel}`\n\nChrome: running '{f.get('chrome', {}).get('running', '')}', subtitle '{f.get('chrome', {}).get('subtitle', '')}'.\n"
        off = f.get("off_screen", [])
        if off:
            text += ("\n### Off screen — applies to EVERY scene, whatever its brief\n\n"
                     "Never show, name, count or imply these; `folio.py lint` fails on a listed term.\n\n"
                     + "".join(f"- {o.get('what', '')}" + (f" — {o['why']}" if o.get("why") else "")
                               + (f" (terms: {', '.join(o['terms'])})" if o.get("terms") else "") + "\n"
                               for o in off))
        for i in sorted(want & set(sc)):
            s_ = sc[i]
            nxt = sc.get(i + 1)
            prv = sc.get(i - 1)
            d = (s_["f1"] - s_["f0"]) / FPS
            text += (f"\n### Scene {i} — `frames/scene_{i:02d}.tsx` · {d:.1f}s · PL. {s_.get('plate', '')} · tone {s_.get('tone', 'paper')}"
                     f" · enters by {s_.get('enter', 'dissolve')}"
                     + (f" · NEXT scene enters by {nxt.get('enter', 'dissolve')}" if nxt else " · last scene") + "\n\n"
                     f"Brief: {s_.get('brief', '')}\n\nSpoken in this scene: \"{_scene_words(tl, s_)}\"\n")
            ids = brief_asset_ids(s_, byid)
            for x in ids:
                p = asset_file(vd, f, byid[x])
                text += f"- asset `{x}` ({asset_kind(f, byid[x])}): `{p.relative_to(REPO) if p.is_relative_to(REPO) else p}`\n"
            if s_.get("enter") == "cut" and prv:
                text += f"- continues scene {i - 1}: read `frames/scene_{i - 1:02d}.tsx` for carried-over elements.\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"prompt → {a.out} ({len(text.split())} words)")
    else:
        print(text)
    return 0


def cmd_assets(a) -> int:
    vd = vdir(a.video)
    folio = load_folio(vd)
    ids = set(x.strip() for x in a.only.split(",")) if a.only else None
    if a.escalate:
        # QA rule: the first fix is the SAME model with a corrected prompt; if the defect
        # survives that reroll, --escalate moves the asset one rung up the ladder.
        if not ids:
            sys.exit("--escalate needs --only <ids>")
        for x in folio["assets"]:
            if x["id"] in ids:
                k = asset_kind(folio, x)
                if k == "map":
                    print(f"  {x['id']}: maps stay on grounded Pro — fix the prompt/places instead")
                    continue
                mf = vd / ASSET_DIR / f"{x['id']}.model"
                cur = mf.read_text().strip() if mf.exists() else asset_tier(x, k)
                nxt = next_tier(cur)
                if nxt is None:
                    print(f"  {x['id']}: already on the top rung ({cur}) — rewrite the prompt or drop the asset")
                    continue
                x["model"] = nxt
                print(f"  {x['id']}: {cur} → {nxt}")
        save_folio(vd, folio)
        a.force = True
    todo = [x for x in folio["assets"] if ids is None or x["id"] in ids]
    (vd / ASSET_DIR).mkdir(parents=True, exist_ok=True)
    # Waves: portraits and bases first, then anything that references them.
    fails = 0
    for wave in range(4):
        pending = []
        with ThreadPoolExecutor(a.jobs) as ex:
            for aid, msg in ex.map(lambda x: gen_one(vd, folio, x, a.force), todo):
                print(f"  {aid:<28} {msg}", flush=True)
                if msg.startswith("WAIT"):
                    pending.append(aid)
                elif msg.startswith("FAIL"):
                    fails += 1
        if not pending:
            break
        todo = [x for x in todo if x["id"] in pending]
        a.force = False
    n = len(folio["assets"])
    have = sum(asset_file(vd, folio, x).exists() for x in folio["assets"])
    print(f"assets: {have}/{n} present, {fails} failed")
    return 1 if fails else 0


# ─────────────────────────────────────────────────────────────── data / bundle

def scene_tsx(vd: Path, i: int) -> Path:
    return vd / "frames" / f"scene_{i:02d}.tsx"


def build_data(vd: Path, folio: dict, tl: dict) -> dict:
    total = end_time(folio, tl)
    words = [w for w in tl["words"] if w["start"] < total]
    assets = {}
    for x in folio["assets"]:
        p = asset_file(vd, folio, x)
        if p.exists():
            assets[x["id"]] = f"a/{p.name}"
    scenes = {}
    for s in folio["scenes"]:
        if "f0" not in s:
            sys.exit("scenes not placed — run folio.py place first")
        scenes[str(s["index"])] = {"index": s["index"], "f0": s["f0"], "f1": s["f1"], "plate": s.get("plate", ""),
                                   "tone": s.get("tone", "paper"), "enter": s.get("enter", "dissolve")}
    ch = folio.get("chrome", {})
    return {
        "chrome": {"running": ch.get("running", folio.get("title", "")), "subtitle": ch.get("subtitle", "")},
        "total": total,
        "words": [{"w": w["word"], "s": round(w["start"], 3), "e": round(w["end"], 3)} for w in words],
        "captions": build_captions(words, total),
        "assets": assets,
        "scenes": scenes,
    }


ENTRY = """import React from 'react';
import {{registerRoot, Composition}} from 'remotion';
import {{FolioStage, FolioData}} from '../../folio';
import dataJson from './data.json';
{imports}
const data = dataJson as unknown as FolioData;
const mk = (n: number, C: React.FC) => () => <FolioStage data={{data}} scene={{n}}><C /></FolioStage>;
const Root: React.FC = () => (<>
{comps}
</>);
registerRoot(Root);
"""


def used_assets(tsx: str, folio: dict) -> set:
    ids = {x["id"] for x in folio["assets"]}
    found = set(re.findall(r"""['"]([A-Za-z0-9_\-]+)['"]""", tsx))
    return found & ids


def bundle(vd: Path, folio: dict, tl: dict, idx: list, tag: str) -> Path:
    """One Remotion bundle holding scenes `idx`; returns the bundle dir."""
    build = vd / "folio_build"
    build.mkdir(exist_ok=True)
    data = build_data(vd, folio, tl)
    slug = re.sub(r"[^a-z0-9]+", "_", vd.parent.name.lower())[:40] + "_" + vd.name.lower().replace("-", "")
    job = f"folio_{slug}_{tag}_{os.getpid()}"
    src = REMOTION / "src" / "generated" / job
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    pub = build / f"public_{tag}"
    if pub.exists():
        shutil.rmtree(pub)
    (pub / "a").mkdir(parents=True)
    shutil.copytree(REMOTION / "public" / "fonts", pub / "fonts")
    shutil.copytree(REMOTION / "public" / "folio", pub / "folio")
    need = set()
    imports, comps = [], []
    for i in idx:
        tsx = scene_tsx(vd, i).read_text(encoding="utf-8")
        (src / f"scene_{i:02d}.tsx").write_text(tsx, encoding="utf-8")
        need |= used_assets(tsx, folio)
        imports.append(f"import S{i} from './scene_{i:02d}';")
        sc = data["scenes"][str(i)]
        comps.append(f'  <Composition id="S{i}" component={{mk({i}, S{i})}} durationInFrames={{{sc["f1"] - sc["f0"]}}} '
                     f'fps={{{FPS}}} width={{1920}} height={{1080}} />')
    for aid in need:
        rel = data["assets"].get(aid)
        if rel:
            p = vd / ASSET_DIR / Path(rel).name
            try:
                os.link(p, pub / rel)
            except OSError:
                shutil.copy2(p, pub / rel)
    (src / "data.json").write_text(json.dumps(data), encoding="utf-8")
    (src / "entry.tsx").write_text(ENTRY.format(imports="\n".join(imports), comps="\n".join(comps)), encoding="utf-8")
    out = build / f"bundle_{tag}"
    if out.exists():
        shutil.rmtree(out)
    r = subprocess.run(["npx", "remotion", "bundle", f"src/generated/{job}/entry.tsx", f"--public-dir={pub}",
                        f"--out-dir={out}", "--log", "error"], cwd=REMOTION, capture_output=True, text=True)
    shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(pub, ignore_errors=True)
    if r.returncode:
        raise RuntimeError("bundle failed:\n" + (r.stderr + r.stdout)[-3000:])
    return out


# ─────────────────────────────────────────────────────────────── lint (static)

CUE_RE = re.compile(r"""\b(?:at|until|fillAt|strike)\s*[=:]\s*\{?\s*(['"])(.+?)\1""")
CUEFN_RE = re.compile(r"""\bcue\(\s*(['"])(.+?)\1(\s*,\s*\{[^}]*n\s*:\s*(\d+))?""")


def scene_cues(tsx: str) -> list:
    out = [(m.group(2), None) for m in CUE_RE.finditer(tsx)]
    out += [(m.group(2), int(m.group(4)) if m.group(4) else None) for m in CUEFN_RE.finditer(tsx)]
    return out


def resolve_cue(words, flat, s: dict, phrase: str, n=None):
    hits = find_phrase(words, flat, phrase, s["f0"] / FPS, s["f1"] / FPS)
    if not hits:
        return None, 0
    k = (n or 1) - 1
    if k >= len(hits):
        return None, len(hits)
    return words[hits[k][0]]["start"] - s["f0"] / FPS, len(hits)


def lint_scene(vd, folio, tl, s) -> tuple:
    errs, warns = [], []
    p = scene_tsx(vd, s["index"])
    if not p.exists():
        return [f"missing {p.name}"], []
    tsx = p.read_text(encoding="utf-8")
    words, flat = tl["words"], flat_tokens(tl["words"])
    for imp in re.findall(r"""from\s+['"]([^'"]+)['"]""", tsx):
        if imp not in ("react", "remotion", "../../folio"):
            errs.append(f"import from '{imp}' (only react, remotion, ../../folio)")
    if "Math.random" in tsx:
        errs.append("Math.random — use seeded(n)")
    if re.search(r"transition\s*:|@keyframes|animation\s*:", tsx):
        errs.append("CSS transition/animation — animate from useT()")
    if "export default" not in tsx:
        errs.append("no default export")
    # A rubber stamp reads as cliché/tabloid — folio has none.
    if re.search(r"<Stamp\b", tsx):
        errs.append("<Stamp> is not part of folio (tabloid) — let the picture + narration carry it, "
                    "or a quiet Statement/Tag/Kicker in ink or blue, upright, fading in")
    ids = {x["id"] for x in folio["assets"]}
    for m in re.finditer(r"""\b(?:src|to)\s*[=:]\s*\{?\s*['"]([A-Za-z0-9_\-]+)['"]""", tsx):
        if m.group(1) not in ids and not m.group(1).startswith("#"):
            errs.append(f"asset '{m.group(1)}' not declared in folio.json")
    for phrase, n in scene_cues(tsx):
        t, cnt = resolve_cue(words, flat, s, phrase, n)
        if t is None:
            errs.append(f"cue not spoken in this scene: \"{phrase}\"" + (f" (n={n}, {cnt} found)" if n else ""))
        elif cnt > 1 and n is None:
            warns.append(f"cue \"{phrase}\" occurs {cnt}× in this scene — first used; pass cue(..., {{n}}) or a longer phrase")
    for m in re.finditer(r"\b(?:y|top)\s*[=:]\s*\{?\s*(\d{3,4})", tsx):
        if int(m.group(1)) > 872:
            warns.append(f"y={m.group(1)} is inside the caption band (872–1006)")
    shown = visible_text(tsx)
    for o in folio.get("off_screen", []):
        for term in o.get("terms", []):
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", shown, re.I):
                errs.append(f"off-screen term on screen: \"{term}\" ({o.get('what', '')})")
    return errs, warns


def visible_text(tsx: str) -> str:
    """The text a scene can put on screen: string literals and JSX text, comments dropped.
    Over-inclusive (asset ids and cue phrases are literals too) — off_screen terms are
    names/numbers/phrases, so a hit in a cue means the narration says it and the entry
    is wrong."""
    code = re.sub(r"/\*.*?\*/", " ", tsx, flags=re.S)
    code = re.sub(r"(?m)(^|[^:])//.*$", r"\1", code)
    lits = re.findall(r'"([^"\n]*)"|\'([^\'\n]*)\'|`([^`]*)`', code)
    jsx = re.findall(r">([^<>{}]+)<", code)
    return "\n".join([x for t in lits for x in t if x] + jsx)


def cmd_lint(a) -> int:
    vd = vdir(a.video)
    folio, tl = load_folio(vd), load_timeline(vd)
    bad = 0
    for s in folio["scenes"]:
        if "f0" not in s:
            print("scenes not placed — run folio.py place")
            return 1
        errs, warns = lint_scene(vd, folio, tl, s)
        for e in errs:
            print(f"  ERROR scene {s['index']}: {e}")
        for w in warns:
            print(f"  warn  scene {s['index']}: {w}")
        bad += bool(errs)
    missing = [x["id"] for x in folio["assets"] if not asset_file(vd, folio, x).exists()]
    if missing:
        print(f"  ERROR assets not generated: {', '.join(missing)}")
        bad += 1
    print("lint: " + ("clean" if not bad else f"{bad} scene(s)/asset issues"))
    return 1 if bad else 0


# ─────────────────────────────────────────────────────────────── still / render

def auto_times(vd, folio, tl, s) -> list:
    tsx = scene_tsx(vd, s["index"]).read_text(encoding="utf-8")
    words, flat = tl["words"], flat_tokens(tl["words"])
    dur = (s["f1"] - s["f0"]) / FPS
    ts = []
    for phrase, n in scene_cues(tsx):
        t, _ = resolve_cue(words, flat, s, phrase, n)
        if t is not None:
            ts.append(min(dur - 0.1, t + 1.0))
    ts.append(max(0.5, dur - 0.6))
    ts = sorted(set(round(t, 1) for t in ts))
    keep = []
    for t in ts:
        if not keep or t - keep[-1] >= 1.2:
            keep.append(t)
    return keep[:10]


def cmd_still(a) -> int:
    vd = vdir(a.video)
    folio, tl = load_folio(vd), load_timeline(vd)
    sc = {s["index"]: s for s in folio["scenes"]}
    idx = [int(x) for x in a.scene.split(",")]
    for i in idx:
        errs, warns = lint_scene(vd, folio, tl, sc[i])
        for e in errs:
            print(f"  ERROR scene {i}: {e}")
        for w in warns:
            print(f"  warn  scene {i}: {w}")
        if errs:
            return 1
    tag = f"still{'_'.join(map(str, idx))}"
    b = bundle(vd, folio, tl, idx, tag)
    outdir = vd / "folio_build" / "stills"
    outdir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i in idx:
        s = sc[i]
        nfr = s["f1"] - s["f0"]
        times = auto_times(vd, folio, tl, s) if a.auto or not a.at else [float(x) for x in a.at.split(",")]
        for t in times:
            fr = max(0, min(nfr - 1, int(round(t * FPS))))
            png = outdir / f"scene_{i:02d}_t{t:05.1f}.png"
            jobs.append((i, fr, png))

    def one(j):
        i, fr, png = j
        r = subprocess.run(["npx", "remotion", "still", str(b), f"S{i}", str(png), f"--frame={fr}", "--log", "error"],
                           cwd=REMOTION, capture_output=True, text=True)
        return png, (None if r.returncode == 0 else (r.stderr + r.stdout)[-1500:])

    fails = 0
    with ThreadPoolExecutor(4) as ex:
        for png, err in ex.map(one, jobs):
            if err:
                fails += 1
                print(f"  FAIL {png.name}\n{err}")
            else:
                print(f"  still {png}")
    shutil.rmtree(b, ignore_errors=True)
    return 1 if fails else 0


def render_key(vd, folio, s, data) -> str:
    tsx = scene_tsx(vd, s["index"]).read_text(encoding="utf-8")
    t0, t1 = s["f0"] / FPS, s["f1"] / FPS
    caps = [g for g in data["captions"] if g["t1"] + 2 >= t0 and g["t0"] - 1 <= t1]
    h = hashlib.sha1()
    for part in (tsx, json.dumps(data["scenes"][str(s["index"])], sort_keys=True), json.dumps(caps),
                 json.dumps(data["chrome"]), (REMOTION / "src" / "folio.tsx").read_text(encoding="utf-8"),
                 json.dumps(data["scenes"].get(str(s["index"] + 1)), sort_keys=True)):
        h.update(part.encode())
    for aid in sorted(used_assets(tsx, folio)):
        h.update(sha(vd / ASSET_DIR / Path(data["assets"].get(aid, "x")).name).encode())
    return h.hexdigest()


def cmd_render(a) -> int:
    vd = vdir(a.video)
    folio, tl = load_folio(vd), load_timeline(vd)
    data = build_data(vd, folio, tl)
    scenes = [s for s in folio["scenes"] if not a.only or s["index"] in {int(x) for x in a.only.split(",")}]
    todo = []
    for s in scenes:
        out = vd / "frames" / f"scene_{s['index']:02d}.mp4"
        key = render_key(vd, folio, s, data)
        kf = out.with_suffix(".mp4.key")
        if out.exists() and kf.exists() and kf.read_text() == key and not a.force:
            continue
        errs, _ = lint_scene(vd, folio, tl, s)
        if errs:
            print(f"  ERROR scene {s['index']}: " + "; ".join(errs))
            return 1
        todo.append((s, out, kf, key))
    if not todo:
        print("render: all scenes up to date")
        return 0
    print(f"render: {len(todo)} scene(s), bundling ...", flush=True)
    b = bundle(vd, folio, tl, [s["index"] for s, *_ in todo], "render")
    conc = max(2, (os.cpu_count() or 8) // max(1, a.jobs) - 1)

    def one(item):
        s, out, kf, key = item
        t0 = time.time()
        tmp = out.with_suffix(".tmp.mp4")
        r = subprocess.run(["npx", "remotion", "render", str(b), f"S{s['index']}", str(tmp), "--codec", "h264",
                            "--crf", "16", f"--concurrency={conc}", "--timeout=120000", "--log", "error"],
                           cwd=REMOTION, capture_output=True, text=True, timeout=int(os.getenv("REMOTION_RENDER_TIMEOUT", "1800")))
        if r.returncode or not tmp.exists():
            return s["index"], "FAIL " + (r.stderr + r.stdout)[-1500:], time.time() - t0
        tmp.replace(out)
        kf.write_text(key)
        return s["index"], "ok", time.time() - t0

    fails = 0
    with ThreadPoolExecutor(a.jobs) as ex:
        for i, msg, dt in ex.map(one, todo):
            print(f"  scene {i:>3}: {msg} ({dt:.0f}s)", flush=True)
            fails += msg != "ok"
    shutil.rmtree(b, ignore_errors=True)
    return 1 if fails else 0


# ─────────────────────────────────────────────────────────────── compile

def decoded_duration(p: Path) -> float:
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
                         capture_output=True)
    return len(pcm.stdout) / (2 * 48000)


def cmd_compile(a) -> int:
    vd = vdir(a.video)
    folio, tl = load_folio(vd), load_timeline(vd)
    scenes = sorted(folio["scenes"], key=lambda s: s["index"])
    clips = []
    for s in scenes:
        p = vd / "frames" / f"scene_{s['index']:02d}.mp4"
        if not p.exists():
            print(f"  ERROR missing {p.name} — run folio.py render")
            return 1
        clips.append((p, s["f1"] - s["f0"]))
    script = json.loads((vd / "script.json").read_text(encoding="utf-8"))
    audio = [vd / "audio" / f"frame_{fr['number']}.mp3" for fr in script["frames"]]
    for p in audio:
        if not p.exists():
            print(f"  ERROR missing audio {p}")
            return 1
    seg = vd / ".compile_segments"
    if seg.exists():
        shutil.rmtree(seg)
    seg.mkdir()
    cmds, segs = [], []
    for j, (clip, n) in enumerate(clips):
        out = seg / f"seg_{j:04d}.mp4"
        cmds.append(clip_segment_cmd(clip, n, out, crf="18"))
        segs.append(out)
    marker = vd / "tail_info.json"
    print(f"  encoding {len(cmds)} segments ...", flush=True)
    ok, err = encode_segments(cmds, label="scene")
    if not ok:
        print(f"  SEGMENT ENCODE FAILED: {err}")
        return 1
    track = seg / "audio.m4a"
    tail = tail_secs(folio)
    silence = []
    if tail > 0:
        sil = seg / "tail_silence.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                        "-t", f"{tail:.3f}", str(sil)], check=True)
        silence = [sil]
    ok, err = build_audio_track(audio + silence, track)
    if not ok:
        print(f"  AUDIO FAILED: {err}")
        return 1
    if folio.get("end_at"):  # pilot/preview: cut narration at end_at
        cut = seg / "audio_cut.m4a"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(track), "-t", f"{float(folio['end_at']):.3f}",
                        "-c:a", "aac", "-b:a", "192k", str(cut)], check=True)
        track = cut
    out = vd / ("final_video.mp4" if not folio.get("end_at") else "preview_video.mp4")
    ok, err = concat_mux(segs, track, out, seg / "segments.txt")
    if not ok:
        print(f"  CONCAT FAILED: {err}")
        return 1
    if tail > 0:
        # A silent tail after the last word: the marker tells generate_subtitles.py about
        # the trailing silence so its drift guard holds.
        marker.write_text(json.dumps({"lead_silence": tail}, indent=2))
    elif marker.exists():
        marker.unlink()
    shutil.rmtree(seg, ignore_errors=True)
    print(f"  ✓ {out.name} {decoded_duration(out):.1f}s ({out.stat().st_size // 2**20} MB)")
    return 0


# ─────────────────────────────────────────────────────────────── review sheet / status

def cmd_sheet(a) -> int:
    """Contact sheet of rendered scenes: 3 frames each (after entry, middle, end)."""
    vd = vdir(a.video)
    folio = load_folio(vd)
    tmp = vd / "folio_build" / "sheet"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    tiles = []
    for s in folio["scenes"]:
        p = vd / "frames" / f"scene_{s['index']:02d}.mp4"
        if not p.exists():
            continue
        d = (s["f1"] - s["f0"]) / FPS
        for k, t in enumerate((min(1.2, d / 3), d / 2, max(0, d - 0.4))):
            png = tmp / f"{s['index']:03d}_{k}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1",
                            "-vf", f"scale=640:-1,drawtext=text='S{s['index']} {t:.1f}s':x=8:y=8:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.6",
                            str(png)], check=False)
            if png.exists():
                tiles.append(png)
    per = 24
    outs = []
    for n in range(0, len(tiles), per):
        lst = tmp / f"list_{n}.txt"
        lst.write_text("".join(f"file '{t.resolve()}'\n" for t in tiles[n:n + per]))
        out = vd / "folio_build" / f"sheet_{n // per:02d}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", "tile=6x4:padding=4", "-frames:v", "1", str(out)], check=False)
        outs.append(out)
    for o in outs:
        print(f"  sheet {o}")
    return 0


def cmd_contact(a) -> int:
    """Asset contact sheets (id-labelled, objects shown on paper) → folio_build/assets_NN.jpg."""
    from PIL import ImageDraw, ImageFont
    vd = vdir(a.video)
    folio = load_folio(vd)
    cw, ch, cols, rows = 480, 300, 4, 4
    try:
        font = ImageFont.truetype(str(REMOTION / "public/fonts/Inter.ttf"), 20)
    except Exception:  # noqa: BLE001
        font = None
    tiles = []
    for x in folio["assets"]:
        p = asset_file(vd, folio, x)
        if not p.exists():
            continue
        im = Image.open(p).convert("RGBA")
        bg = Image.new("RGBA", im.size, tuple(int(v) for v in PAPER_RGB) + (255,))
        im = Image.alpha_composite(bg, im).convert("RGB")
        im.thumbnail((cw, ch - 30))
        tile = Image.new("RGB", (cw, ch), (40, 40, 40))
        tile.paste(im, ((cw - im.width) // 2, 30 + (ch - 30 - im.height) // 2))
        label = f"{x['id']} · {asset_kind(folio, x)}" + (f" (of {x['of']})" if x.get("of") else "")
        ImageDraw.Draw(tile).text((6, 4), label, fill=(255, 220, 120), font=font)
        tiles.append(tile)
    per = cols * rows
    for n in range(0, len(tiles), per):
        sheet = Image.new("RGB", (cols * cw, rows * ch), (20, 20, 20))
        for k, t in enumerate(tiles[n:n + per]):
            sheet.paste(t, ((k % cols) * cw, (k // cols) * ch))
        out = vd / "folio_build" / f"assets_{n // per:02d}.jpg"
        out.parent.mkdir(exist_ok=True)
        sheet.save(out, quality=88)
        print(f"  contact {out}")
    return 0


def cmd_status(a) -> int:
    vd = vdir(a.video)
    folio = load_folio(vd)
    n = len(folio["scenes"])
    placed = all("f0" in s for s in folio["scenes"])
    tsx = sum(scene_tsx(vd, s["index"]).exists() for s in folio["scenes"])
    mp4 = sum((vd / "frames" / f"scene_{s['index']:02d}.mp4").exists() for s in folio["scenes"])
    have = sum(asset_file(vd, folio, x).exists() for x in folio["assets"])
    kinds = {}
    for x in folio["assets"]:
        kinds[asset_kind(folio, x)] = kinds.get(asset_kind(folio, x), 0) + 1
    print(f"scenes {n} (placed: {placed}) · tsx {tsx}/{n} · rendered {mp4}/{n} · assets {have}/{len(folio['assets'])} {kinds}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("place", "lint", "compile", "sheet", "status", "contact"):
        s = sub.add_parser(name)
        s.add_argument("video")
    s = sub.add_parser("assets")
    s.add_argument("video")
    s.add_argument("--only")
    s.add_argument("--force", action="store_true")
    s.add_argument("--jobs", type=int, default=6)
    s.add_argument("--escalate", action="store_true",
                   help="move --only assets one model up the ladder (lite → flash → gpt) and regenerate")
    s = sub.add_parser("audit")
    s.add_argument("video")
    s = sub.add_parser("prompt")
    s.add_argument("step", choices=["direct", "scenes"])
    s.add_argument("video")
    s.add_argument("--scenes", default="0-9999")
    s.add_argument("--out")
    s = sub.add_parser("refinish")
    s.add_argument("video")
    s.add_argument("--only")
    s = sub.add_parser("grid")
    s.add_argument("video")
    s.add_argument("asset")
    s = sub.add_parser("still")
    s.add_argument("video")
    s.add_argument("--scene", required=True, help="scene index or comma list")
    s.add_argument("--at", help="comma list of scene-local seconds")
    s.add_argument("--auto", action="store_true", help="one still ~1 s after every cue + the final frame")
    s = sub.add_parser("render")
    s.add_argument("video")
    s.add_argument("--only")
    s.add_argument("--jobs", type=int, default=3)
    s.add_argument("--force", action="store_true")
    a = ap.parse_args()
    return {"place": cmd_place, "assets": cmd_assets, "lint": cmd_lint, "still": cmd_still, "render": cmd_render,
            "compile": cmd_compile, "sheet": cmd_sheet, "status": cmd_status, "refinish": cmd_refinish,
            "grid": cmd_grid, "audit": cmd_audit, "prompt": cmd_prompt, "contact": cmd_contact}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
