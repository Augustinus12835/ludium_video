#!/usr/bin/env python3
"""
FOLIO motion clips — turn an action-heavy folio scene into a moving engraved plate.

OPTIONAL, and off by default: Omni Flash is still unstable and costs ~$1.10 per 10 s clip, so
an episode is finished with still plates and the camera (<Plate camera>); run this only when
you want motion in a particular episode.

WHAT TO ANIMATE (lessons from the first 29 clips): WIDE shots whose motion is generic and
continuous and whose details stay obscure — a whole army or fleet moving, crowds, rowing,
climbing, work seen from afar, and camera moves through a place (a theatre crane worked very
well). The motion should run on in the background while the narration goes on. NOT close-ups,
and NOT a specific delicate action or choreographed event (building a wall up close, storming
a fort, ramming and towing ships, overrunning a post, a sneak attack): Omni glitches on those —
impossible heads, morphing hands, invented cuts. Comparisons, definitions, concepts,
portraits and maps stay still. Build clips FOR the episode (each from its own plate); note
when one can be reused (a repeated plate, a later scene) rather than making generic stock.

Per scene NN of pipeline/<L>/Video-1 (files under <video>/motion/):

  cues   — scene span, stretch factor and the narration's word cues in CLIP time (0–10 s);
           a proxy keys its motion to these
  proxy  — render motion/scene_NN.js (Three.js blocking, written against
           scripts/motion_proxy/lib.js) → motion/scene_NN.proxy.mp4 (10 s, 1280×720, 24 fps);
           --test t1,t2,… renders just those instants → motion/scene_NN.test.jpg
  ref    — a reference image made FOR this clip: gpt-image redraws a proxy frame as a finished
           plate in the style of the scene's own folio plate → motion/scene_NN.ref.jpg
  gen    — Gemini Omni Flash edit: proxy video (layout, camera, timing) + <IMAGE_REF_0> the
           clip's ref + <IMAGE_REF_1> the scene's plate, prompt motion/scene_NN.prompt.txt
           → motion/scene_NN.omni.mp4  (~$1.10, ~65 s)
  extend — for a scene longer than ~12.5 s: Omni multi-turn extension (previous_interaction_id) until
           the clip reaches scene/1.25 s, so the motion runs on instead of freezing (~$0.10/s)
  fit    — crop (default 5% all round; Omni sometimes engraves a fake signature in the bottom
           right — pass --crop W:H:X:Y to cut it), stretch to the scene (≤1.25×), 30 fps
           1920×1080, hold the last frame to the scene's length → assets/folio/m_<plate>.mp4,
           declared in folio.json as kind "motion". The scene TSX then swaps <Plate> → <Motion>.
  sheet  — 6-frame contact sheet of the omni clip (or --proxy) → motion/scene_NN.sheet.jpg

Usage:
    folio_motion.py cues  pipeline/<L>/Video-1 47
    folio_motion.py proxy pipeline/<L>/Video-1 47 [--test 0,3,6,9.9]
    folio_motion.py ref   pipeline/<L>/Video-1 47 [--at 5.0]
    folio_motion.py gen   pipeline/<L>/Video-1 47
    folio_motion.py fit   pipeline/<L>/Video-1 47 [--crop 1216:684:32:0]
    folio_motion.py sheet pipeline/<L>/Video-1 47 [--proxy]
"""
import argparse
import base64
import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "scripts" / "motion_proxy"
ASSET_DIR = Path("assets") / "folio"
CLIP = 10.0          # Omni output length
MAX_STRETCH = 1.25   # slower than this reads as slow motion; the rest of the scene holds
MIN_STRETCH = 0.9    # faster than this reads as fast-forward; a short scene uses only the clip's start
FPS = 30
load_dotenv(ROOT / ".env")


def vdir(p) -> Path:
    v = Path(p)
    return v if v.is_absolute() else ROOT / v


def load(vd: Path):
    return json.loads((vd / "folio.json").read_text()), json.loads((vd / "narration_timeline.json").read_text())


def scene_of(folio, n):
    s = next((x for x in folio["scenes"] if x["index"] == n), None)
    if s is None or "f0" not in s:
        sys.exit(f"scene {n} not found / not placed")
    return s


def span(s):
    t0, t1 = s["f0"] / FPS, s["f1"] / FPS
    return t0, t1, max(MIN_STRETCH, min((t1 - t0) / CLIP, MAX_STRETCH))


def plate_of(vd, folio, n) -> str:
    """The scene's full-bleed plate id, read from its TSX (<Plate src="…">)."""
    tsx = (vd / "frames" / f"scene_{n:02d}.tsx").read_text()
    m = re.search(r"""<(?:Plate|Motion)\s+src=["']([A-Za-z0-9_\-]+)["']""", tsx)
    if not m:
        sys.exit(f"scene {n}: no <Plate src=…> to animate")
    pid = m.group(1)
    return pid[2:] if pid.startswith("m_") else pid


def mdir(vd) -> Path:
    d = vd / "motion"
    d.mkdir(exist_ok=True)
    return d


# ─────────────────────────────────────────────────────────────── cues

def cmd_cues(a):
    vd = vdir(a.video); folio, tl = load(vd); s = scene_of(folio, a.scene)
    t0, t1, k = span(s)
    print(f"scene {a.scene}: {t0:.2f}–{t1:.2f} s ({t1 - t0:.2f} s), plate {plate_of(vd, folio, a.scene)}; "
          f"clip time = (t − {t0:.2f}) / {k:.3f}; the clip covers {CLIP * k:.2f} s"
          + (f", then holds {t1 - t0 - CLIP * k:.2f} s" if t1 - t0 > CLIP * k + 0.05 else ""))
    words = [w for w in tl["words"] if t0 <= w["start"] < t1]
    print(" ".join(f"{w['word']}@{(w['start'] - t0) / k:.2f}" for w in words))
    return 0


# ─────────────────────────────────────────────────────────────── proxy

def serve(vd: Path):
    """Serve kit + this video's motion/ dir (as scenes/) on a free localhost port."""
    root = vd / "folio_build" / f"motion_serve_{os.getpid()}"   # per process: proxies render in parallel
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for name in ("lib.js", "page.html", "node_modules"):
        (root / name).symlink_to(KIT / name)
    (root / "scenes").symlink_to(mdir(vd))
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass
    h = functools.partial(Quiet, directory=str(root))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def cmd_proxy(a):
    vd = vdir(a.video); md = mdir(vd); name = f"scene_{a.scene:02d}"
    if not (md / f"{name}.js").exists():
        sys.exit(f"write {md / (name + '.js')} first (Three.js, imports '../lib.js')")
    if not (KIT / "node_modules").exists():
        subprocess.run(["npm", "install", "--silent"], cwd=KIT, check=True)
    srv, base = serve(vd)
    frames = vd / "folio_build" / f"motion_frames_{name}"
    shutil.rmtree(frames, ignore_errors=True)
    try:
        cmd = ["node", str(KIT / "render.mjs"), base, name, str(frames), "24"] + ([a.test] if a.test else [])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        print((r.stdout + r.stderr).strip()[-1500:])
        if r.returncode:
            return 1
    finally:
        srv.shutdown()
        shutil.rmtree(vd / "folio_build" / f"motion_serve_{os.getpid()}", ignore_errors=True)
    pngs = sorted(frames.glob("f_*.png"))
    if a.test:
        n = len(pngs); cols = 3; rows = (n + cols - 1) // cols
        ins = sum([["-i", str(p)] for p in pngs], [])
        fc = "".join(f"[{i}]scale=640:360[s{i}];" for i in range(n)) + "".join(f"[s{i}]" for i in range(n)) + \
            f"xstack=inputs={n}:layout=" + "|".join(f"{(i % cols) * 640}_{(i // cols) * 360}" for i in range(n)) + ":fill=black"
        if n == 1:
            fc = "[0]scale=640:360"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc, str(md / f"{name}.test.jpg")], check=True)
        print(f"→ {md / (name + '.test.jpg')}")
    else:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "24", "-i", str(frames / "f_%04d.png"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", str(md / f"{name}.proxy.mp4")], check=True)
        print(f"→ {md / (name + '.proxy.mp4')}")
    shutil.rmtree(frames, ignore_errors=True)
    return 0


# ─────────────────────────────────────────────────────────────── ref

REF_PROMPT = """Image 1 is a rough 3D layout render; image 2 is the style, costume and setting reference — a finished plate from the same film.
Redraw image 1 as a finished sepia nineteenth-century steel-engraving plate in exactly the style of image 2: fine cross-hatched ink on cream paper, the same ink-and-cream duotone, the same kind of people, dress, ships, buildings and landscape. Keep the composition of image 1 exactly: the same camera angle and horizon, every figure, group, ship and building in the same place and at the same size, every pose as blocked. Turn the flat blocky shapes into real people and things.
{extra}
No text, lettering, signature, border or platemark anywhere."""


def cmd_ref(a):
    from openai import OpenAI
    vd = vdir(a.video); folio, _ = load(vd); md = mdir(vd); name = f"scene_{a.scene:02d}"
    proxy = md / f"{name}.proxy.mp4"
    if not proxy.exists():
        sys.exit("render the proxy first")
    plate = vd / ASSET_DIR / f"{plate_of(vd, folio, a.scene)}.jpg"
    frame = md / f"{name}.ref_src.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(a.at), "-i", str(proxy), "-frames:v", "1", str(frame)], check=True)
    extra_f = md / f"{name}.ref.txt"
    extra = extra_f.read_text().strip() if extra_f.exists() else ""
    r = OpenAI().images.edit(model="gpt-image-2.5-sunburst",
                             image=[open(frame, "rb"), open(plate, "rb")],
                             prompt=REF_PROMPT.format(extra=extra), size="1536x864", n=1)
    png = md / f"{name}.ref.png"
    png.write_bytes(base64.b64decode(r.data[0].b64_json))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(png), "-q:v", "2", str(md / f"{name}.ref.jpg")], check=True)
    print(f"→ {md / (name + '.ref.jpg')}  (proxy frame at {a.at}s, plate {plate.name})")
    return 0


# ─────────────────────────────────────────────────────────────── gen (Omni)

OMNI = "https://generativelanguage.googleapis.com"


def omni_upload(path: Path, key: str) -> str:
    data = path.read_bytes()
    r = requests.post(f"{OMNI}/upload/v1beta/files", params={"key": key}, json={"file": {"display_name": path.name}},
                      headers={"X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
                               "X-Goog-Upload-Header-Content-Length": str(len(data)),
                               "X-Goog-Upload-Header-Content-Type": "video/mp4", "Content-Type": "application/json"})
    r.raise_for_status()
    r = requests.post(r.headers["x-goog-upload-url"], data=data,
                      headers={"X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0"})
    r.raise_for_status()
    f = r.json()["file"]
    while f.get("state") == "PROCESSING":
        time.sleep(5)
        f = requests.get(f"{OMNI}/v1beta/{f['name']}", params={"key": key}).json()
    if f.get("state") != "ACTIVE":
        sys.exit(f"upload failed: {f}")
    return f["uri"]


def find_video(o):
    if isinstance(o, dict):
        if o.get("type") == "video" and ("data" in o or "uri" in o):
            return o
        o = list(o.values())
    if isinstance(o, list):
        for v in o:
            r = find_video(v)
            if r:
                return r


def cmd_gen(a):
    vd = vdir(a.video); folio, _ = load(vd); md = mdir(vd); name = f"scene_{a.scene:02d}"
    key = os.environ["GOOGLE_CLOUD_API_KEY"]
    prompt = (md / f"{name}.prompt.txt").read_text().strip()
    imgs = [md / f"{name}.ref.jpg", vd / ASSET_DIR / f"{plate_of(vd, folio, a.scene)}.jpg"]
    parts = [{"type": "video", "uri": omni_upload(md / f"{name}.proxy.mp4", key), "mime_type": "video/mp4"}]
    for p in imgs:
        if not p.exists():
            sys.exit(f"missing {p}")
        parts.append({"type": "image", "data": base64.b64encode(p.read_bytes()).decode(), "mime_type": "image/jpeg"})
    parts.append({"type": "text", "text": prompt})
    body = {"model": os.getenv("OMNI_MODEL", "gemini-omni-1.1-flash"), "input": parts,
            "response_format": {"type": "video", "resolution": "720p"},
            "generation_config": {"video_config": {"task": "edit"}}}
    t = time.time()
    r = requests.post(f"{OMNI}/v1beta/interactions", params={"key": key}, json=body, timeout=1800)
    if r.status_code != 200:
        sys.exit(f"HTTP {r.status_code}: {r.text[:2000]}")
    j = r.json()
    while j.get("status") in ("in_progress", "queued", "running") and not find_video(j):
        time.sleep(10)
        j = requests.get(f"{OMNI}/v1beta/interactions/{j['id']}", params={"key": key}, timeout=600).json()
    v = find_video(j)
    if not v:
        sys.exit(f"no video in response: {json.dumps(j)[:1500]}")
    out = md / f"{name}.omni.mp4"
    if out.exists():
        n = 1
        while (md / f"{name}.omni.v{n}.mp4").exists():
            n += 1
        out.rename(md / f"{name}.omni.v{n}.mp4")
    out.write_bytes(base64.b64decode(v["data"]) if "data" in v else
                    requests.get(v["uri"], params={"key": key, "alt": "media"}, timeout=600).content)
    (md / f"{name}.omni.json").write_text(json.dumps({"id": j.get("id"), "usage": j.get("usage")}, indent=1))
    print(f"→ {out}  ({time.time() - t:.0f}s, {j.get('usage', {}).get('total_output_tokens')} output tokens)")
    return 0


def probe_dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


EXTEND_PROMPT = ("Extend this video by about {sec} seconds: one continuous shot, no cut. Continue exactly the same "
                 "camera movement, speed and the same ongoing motion of the people, ships and water, in the same "
                 "sepia steel-engraving style. Nothing new happens; the scene simply goes on. No text, no lettering. "
                 "No dialogue, no music.")


def cmd_extend(a):
    """Lengthen the current omni clip with Omni's multi-turn extension (keeps motion going through a
    long scene instead of holding the last frame). Repeats until the clip reaches the scene length."""
    vd = vdir(a.video); folio, _ = load(vd); md = mdir(vd); name = f"scene_{a.scene:02d}"
    key = os.environ["GOOGLE_CLOUD_API_KEY"]
    s = scene_of(folio, a.scene)
    D = (s["f1"] - s["f0"]) / FPS
    need = a.to or D / MAX_STRETCH           # raw seconds needed so the stretch stays ≤ MAX_STRETCH
    meta_f = md / f"{name}.omni.json"
    out = md / f"{name}.omni.mp4"
    for _ in range(3):
        have = probe_dur(out)
        if have >= need - 0.2:
            break
        iid = json.loads(meta_f.read_text())["id"]
        sec = int(min(10, max(3, round(need - have + 0.5))))
        body = {"model": os.getenv("OMNI_MODEL", "gemini-omni-1.1-flash"), "previous_interaction_id": iid,
                "input": [{"type": "text", "text": (a.prompt or EXTEND_PROMPT).format(sec=sec)}],
                "response_format": {"type": "video", "resolution": "720p"}}   # no `task` on a follow-up turn (400)
        t = time.time()
        r = requests.post(f"{OMNI}/v1beta/interactions", params={"key": key}, json=body, timeout=1800)
        if r.status_code != 200:
            sys.exit(f"HTTP {r.status_code}: {r.text[:2000]}")
        j = r.json()
        while j.get("status") in ("in_progress", "queued", "running") and not find_video(j):
            time.sleep(10)
            j = requests.get(f"{OMNI}/v1beta/interactions/{j['id']}", params={"key": key}, timeout=600).json()
        v = find_video(j)
        if not v:
            sys.exit(f"no video in response: {json.dumps(j)[:1500]}")
        raw = base64.b64decode(v["data"]) if "data" in v else \
            requests.get(v["uri"], params={"key": key, "alt": "media"}, timeout=600).content
        part = md / f"{name}.ext.mp4"
        part.write_bytes(raw)
        got = probe_dur(part)
        prev = md / f"{name}.omni.pre_extend_{int(have)}s.mp4"
        out.rename(prev)
        if got > have + 1.0:          # the model returned the whole, longer video
            part.rename(out)
        else:                         # it returned only the continuation: join
            lst = md / f"{name}.concat.txt"
            lst.write_text(f"file '{prev.name}'\nfile '{part.name}'\n")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                            "-c:v", "libx264", "-crf", "14", "-an", str(out)], check=True, cwd=md)
            lst.unlink(); part.unlink()
        meta_f.write_text(json.dumps({"id": j.get("id"), "usage": j.get("usage")}, indent=1))
        print(f"  extended {have:.1f}s → {probe_dur(out):.1f}s ({'whole video' if got > have + 1.0 else 'continuation joined'}, "
              f"{time.time() - t:.0f}s, {j.get('usage', {}).get('total_output_tokens')} output tokens)")
    print(f"→ {out}  {probe_dur(out):.1f}s (scene {D:.1f}s, target ≥ {need:.1f}s)")
    return 0


# ─────────────────────────────────────────────────────────────── fit

def cmd_fit(a):
    vd = vdir(a.video); folio, _ = load(vd); md = mdir(vd); name = f"scene_{a.scene:02d}"
    s = scene_of(folio, a.scene)
    D = (s["f1"] - s["f0"]) / FPS
    L = probe_dur(md / f"{name}.omni.mp4")
    k = max(MIN_STRETCH, min(D / L, MAX_STRETCH))
    pid = getattr(a, "plate", None) or plate_of(vd, folio, a.scene)
    mid = f"m_{pid}"
    other = next((x for x in folio["assets"] if x["id"] == mid and x.get("scene") != a.scene), None)
    if other is not None:            # another scene already animates this plate (a reused plate) — keep both
        mid = f"m_{pid}_s{a.scene:02d}"
    out = vd / ASSET_DIR / f"{mid}.mp4"
    crop = a.crop or "iw*0.95:ih*0.95"
    vf = (f"crop={crop},setpts=PTS*{k:.5f},fps={FPS},scale=1920:1080:flags=lanczos,setsar=1,"
          f"tpad=stop_mode=clone:stop_duration={max(0.0, D - L * k) + 0.5:.3f}")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(md / f"{name}.omni.mp4"), "-vf", vf, "-an",
                    "-frames:v", str(s["f1"] - s["f0"]), "-c:v", "libx264", "-crf", "14", "-preset", "slow",
                    "-pix_fmt", "yuv420p", str(out)], check=True)
    if not any(x["id"] == mid for x in folio["assets"]):
        folio["assets"].append({"id": mid, "kind": "motion", "of_plate": pid, "scene": a.scene,
                                "note": "Omni Flash clip from motion/" + name + ".proxy.mp4 (folio_motion.py)"})
        (vd / "folio.json").write_text(json.dumps(folio, indent=2, ensure_ascii=False) + "\n")
    print(f"→ {out}  ({D:.2f} s scene; {L:.1f} s clip ×{k:.3f} covers {L * k:.2f} s)  — now swap <Plate src=\"{pid}\"> → <Motion src=\"{mid}\">")
    return 0


def cmd_sheet(a):
    vd = vdir(a.video); md = mdir(vd); name = f"scene_{a.scene:02d}"
    src = md / (f"{name}.proxy.mp4" if a.proxy else f"{name}.omni.mp4")
    out = md / f"{name}.{'proxy' if a.proxy else 'omni'}.sheet.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", "fps=6/10,scale=640:-1,tile=3x2",
                    "-frames:v", "1", str(out)], check=True)
    print(f"→ {out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("cues", "proxy", "ref", "gen", "extend", "fit", "sheet"):
        p = sub.add_parser(c)
        p.add_argument("video"); p.add_argument("scene", type=int)
        if c == "proxy":
            p.add_argument("--test", help="comma-separated clip times for a quick look")
        if c == "ref":
            p.add_argument("--at", type=float, default=5.0, help="proxy time of the frame to redraw")
        if c == "extend":
            p.add_argument("--to", type=float, help="target raw clip length (default: scene / 1.25)")
            p.add_argument("--prompt", help="custom continuation prompt ({sec} = seconds)")
        if c == "fit":
            p.add_argument("--crop", help="ffmpeg crop W:H:X:Y on the 1280×720 omni clip")
            p.add_argument("--plate", help="plate id when the scene shows several (default: its first <Plate>)")
        if c == "sheet":
            p.add_argument("--proxy", action="store_true")
    a = ap.parse_args()
    return {"cues": cmd_cues, "proxy": cmd_proxy, "ref": cmd_ref, "gen": cmd_gen, "extend": cmd_extend, "fit": cmd_fit, "sheet": cmd_sheet}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
