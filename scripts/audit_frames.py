#!/usr/bin/env python3
"""
Frame visual-QA aid.

Frames are generated WITHOUT vision (Manim codegen), so they ship with
layout defects: collisions, out-of-bounds text, mis-placed highlights, cramped or
tiny text, mis-placed assets, and dead time (long spans with no on-screen change).

This builds, for each frame_N.mp4 in a video, a labeled CONTACT SHEET — several
stills tiled across the clip's timeline — plus a static-span report, so the agent
can vision-inspect a whole frame's evolution in ONE Read instead of many, then fix
the source (.py) per the usual re-render workflow.

Usage:
    python scripts/audit_frames.py pipeline/<L>/Video-N             # one video
    python scripts/audit_frames.py pipeline/<L>/Video-N --frame 5   # one frame
    python scripts/audit_frames.py pipeline/<L>/Video-N --cols 4    # grid width

Writes <video>/audit/frame_N_sheet.png + audit_manifest.json and prints a summary.
"""
import argparse
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def ffprobe_dur(p):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def extract_still(mp4, t, out):
    subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(mp4),
                    "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True)


def load_font(sz):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, sz)
            except OSError:
                pass
    return ImageFont.load_default()


def sample_times(dur):
    """~1 still every 4s, min 4 / max 12, spanning [0.4 .. dur-0.3] inclusive
    (the last sample captures the fully-composed end state)."""
    if dur <= 0:
        return [0.0]
    n = max(4, min(12, math.ceil(dur / 4)))
    lo, hi = 0.4, max(0.6, dur - 0.3)
    if n == 1:
        return [round((lo + hi) / 2, 2)]
    return [round(lo + (hi - lo) * i / (n - 1), 2) for i in range(n)]


def build_sheet(mp4, frame_num, video_label, out_dir, tile_w=760, cols=2):
    out_dir = Path(out_dir)
    out_png = out_dir / f"frame_{frame_num}_sheet.png"
    dur = ffprobe_dur(mp4)
    times = sample_times(dur)
    tile_h = int(tile_w * 9 / 16)
    thumbs, arrs, content = [], [], []
    full_paths = {}  # timestamp -> full-res still path (kept on disk)
    full_dir = out_dir / "full"
    full_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            sp = Path(td) / f"s{i}.png"
            extract_still(mp4, t, sp)            # native 1920x1080
            if sp.exists() and sp.stat().st_size > 0:
                full = Image.open(sp).convert("RGB")
            else:
                full = Image.new("RGB", (1920, 1080), (20, 20, 20))
            # keep the FULL-RES still on disk — small collisions only show here
            fp = full_dir / f"frame_{frame_num}_t{t:.1f}.png"
            full.save(fp)
            full_paths[round(t, 1)] = fp
            im = full.resize((tile_w, tile_h))
            thumbs.append(im)
            arr = np.asarray(im, dtype=np.int16)
            arrs.append(arr)
            content.append(float(np.mean(np.abs(arr - arr[0:1, 0:1]))))  # ink vs corner bg

    # the 2 busiest moments (most on-screen content) — where collisions live;
    # the skill MUST read these full-res, not just the downscaled sheet.
    order = sorted(range(len(times)), key=lambda i: content[i], reverse=True)
    detail_idx = sorted(order[:2]) if len(times) >= 2 else [0]
    detail_stills = [str(full_paths[round(times[i], 1)]) for i in detail_idx]

    # static-span detection: mean abs pixel diff between consecutive samples
    STATIC = 1.3  # below this ≈ no visible change (low, to avoid flagging small reveals)
    merged = []
    for i in range(1, len(arrs)):
        d = float(np.mean(np.abs(arrs[i] - arrs[i - 1])))
        if d < STATIC:
            s, e = times[i - 1], times[i]
            if merged and abs(merged[-1][1] - s) < 1e-6:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
    flags = [f"{s:.1f}-{e:.1f}s" for s, e in merged if (e - s) >= 6.0]

    # compose grid
    rows = math.ceil(len(thumbs) / cols)
    cap, pad, title_h, foot_h = 24, 10, 48, 42
    W = cols * tile_w + (cols + 1) * pad
    H = title_h + rows * (tile_h + cap + pad) + pad + foot_h
    sheet = Image.new("RGB", (W, H), (12, 12, 12))
    dr = ImageDraw.Draw(sheet)
    f_t, f_c = load_font(26), load_font(18)
    dr.text((pad, 12),
            f"{video_label}   frame {frame_num}   dur {dur:.1f}s   ({len(times)} samples, read left→right, top→bottom)",
            fill=(255, 255, 255), font=f_t)
    for i, im in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (tile_w + pad)
        y = title_h + r * (tile_h + cap + pad)
        dr.text((x, y), f"t = {times[i]:.1f}s", fill=(150, 210, 255), font=f_c)
        sheet.paste(im, (x, y + cap))
    foot = ("STATIC (no change ≥6s): " + "; ".join(flags)) if flags else "STATIC spans ≥6s: none"
    foot += "    |  read FULL-RES detail at t = " + ", ".join(f"{times[i]:.1f}s" for i in detail_idx)
    dr.text((pad, H - foot_h + 10), foot,
            fill=(255, 170, 90) if flags else (140, 200, 140), font=f_c)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    return {"frame": frame_num, "duration": round(dur, 1), "samples": times,
            "static_flags": flags, "sheet": str(out_png),
            "detail_stills": detail_stills}


def main():
    ap = argparse.ArgumentParser(description="Build frame contact sheets for visual QA")
    ap.add_argument("video_dir", help="pipeline/<L>/Video-N")
    ap.add_argument("--frame", type=int, help="audit a single frame only")
    ap.add_argument("--cols", type=int, default=2, help="contact-sheet grid columns")
    args = ap.parse_args()

    vd = Path(args.video_dir)
    fr_dir = vd / "frames"
    label = f"{vd.parent.name}/{vd.name}"
    mp4s = sorted(fr_dir.glob("frame_*.mp4"),
                  key=lambda p: int(p.stem.split("_")[1]))
    if args.frame is not None:
        mp4s = [p for p in mp4s if int(p.stem.split("_")[1]) == args.frame]
    if not mp4s:
        print(f"No frame_*.mp4 found in {fr_dir}")
        return

    out_dir = vd / "audit"
    results = []
    for mp4 in mp4s:
        fn = int(mp4.stem.split("_")[1])
        r = build_sheet(mp4, fn, label, out_dir, cols=args.cols)
        results.append(r)
        flag = ("  ⚠ static " + ", ".join(r["static_flags"])) if r["static_flags"] else ""
        print(f"frame {fn}: {r['duration']}s, {len(r['samples'])} samples -> {r['sheet']}{flag}")

    (out_dir / "audit_manifest.json").write_text(json.dumps(results, indent=2))
    print("\nFor EACH frame: Read the contact sheet (overview + timing), THEN Read its")
    print("full-res detail stills (small collisions show ONLY at full res):")
    for r in results:
        print(f"  frame {r['frame']}: sheet {r['sheet']}")
        for d in r.get("detail_stills", []):
            print(f"             detail {d}")
    print(f"\nManifest: {out_dir / 'audit_manifest.json'}")


if __name__ == "__main__":
    main()
