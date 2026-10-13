#!/usr/bin/env python3
"""Voice several videos in ONE Drama 3 GPU session (TTS_PROVIDER=drama3, the cheap way).

A Drama 3 session costs ~3-4 min of Modal H100 start-up (~US$0.25) and then ~15 s (~US$0.02) per
10 min of narration, so voicing a lecture's videos together — or two small lectures together
(run-pipeline: a lecture of <= 4 videos pairs with the next one) — is what makes it cheap; Modal's
US$30/month of free credit then covers hundreds of videos. Every pending frame of every video
goes out in one batch; each video is then written exactly as
`generate_tts_elevenlabs.py --profile drama3` would write it (audio/frame_N.mp3,
frame_N_timestamps.json, tts_meta.json), so `pipeline.py` sees tts COMPLETE and moves on.

    venv/bin/python scripts/tts_drama3_batch.py pipeline/<L1> pipeline/<L2>       # whole lectures
    venv/bin/python scripts/tts_drama3_batch.py pipeline/<L>/Video-2 pipeline/<L>/Video-3

Each video passes the pre-TTS narration gate first (SKIP_NARRATION_CHECK=1 bypasses a confirmed
false positive). A video already part-voiced in another narrator is skipped (one narrator per
video). Frames whose speaking rate looks wrong (a skipped or doubled sentence) are flagged.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scripts.generate_tts_elevenlabs as tts
from scripts.utils.narration_check import scan_video_narration, format_report

RATE_RANGE = (1.5, 3.8)   # words per second outside this = listen to the frame


def video_dirs(paths):
    out = []
    for p in map(Path, paths):
        vids = [p] if p.name.startswith("Video-") else sorted(
            (v for v in p.glob("Video-*") if v.is_dir()), key=lambda v: int(v.name.split("-")[1]))
        out += [v for v in vids if (v / "script.md").exists()]
    return out


def main():
    paths = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not paths:
        print(__doc__); sys.exit(1)
    tts.activate_provider("drama3")
    tts.EXPLICIT_PROVIDER = True
    tts.check_provider_config()

    plan, blocked = [], []
    for vd in video_dirs(paths):
        audio = vd / "audio"
        meta = tts.read_tts_meta(audio)
        have_audio = audio.exists() and any(audio.glob("frame_*.mp3"))
        if have_audio and (not meta or meta.get("provider") != "drama3"):
            print(f"  skip {vd}: already voiced in {(meta or {}).get('provider', 'elevenlabs (legacy)')} "
                  f"— one narrator per video")
            continue
        if not os.getenv("SKIP_NARRATION_CHECK"):
            offenders = scan_video_narration(vd)
            if offenders:
                print(format_report(offenders, vd)); blocked.append(vd); continue
        script = vd / "script.md"
        frames = tts.parse_script(str(script))
        math_data = tts.load_math_verification(str(vd))
        pending = [f for f in frames if not (audio / f"frame_{f.number}.mp3").exists()]
        if pending:
            plan.append((vd, frames, math_data, pending))

    if not plan:
        print("Nothing to voice." + (f" {len(blocked)} video(s) blocked by the narration gate." if blocked else ""))
        sys.exit(1 if blocked else 0)
    texts = [tts.clean_narration_text(tts.get_natural_narration(f.number, f.text, md))
             for _, _, md, pend in plan for f in pend]
    words = sum(len(t.split()) for t in texts)
    print(f"Drama 3 batch: {len(plan)} video(s), {len(texts)} frames, {words:,} words "
          f"(~{words / 150:.0f} min of narration)")

    failed = 0
    with tts._drama3_module().Session() as sess:
        tts._DRAMA3['session'] = sess
        for t, e in tts.drama3_prefetch(texts).items():
            print(f"  ✗ Drama 3 failed: {e} — {t[:60]}...")
        for vd, frames, math_data, _ in plan:
            print(f"\n=== {vd}")
            results = tts.generate_audio_for_frames(frames, str(vd / "audio"), math_data)
            tts.print_report(results)
            failed += sum(r["status"] == "failed" for r in results)
        tts._DRAMA3['session'] = None

    print("\nSpeaking-rate check (a skipped or doubled sentence shows up here):")
    flags = 0
    for vd, frames, _, pend in plan:
        for f in pend:
            ts = vd / "audio" / f"frame_{f.number}_timestamps.json"
            if not ts.exists():
                continue
            w = json.loads(ts.read_text())["words"]
            dur = tts.get_audio_duration(str(vd / "audio" / f"frame_{f.number}.mp3"))
            rate = len(w) / dur if dur else 0
            if not RATE_RANGE[0] <= rate <= RATE_RANGE[1]:
                flags += 1
                print(f"  ⚠ {vd.parent.name}/{vd.name} frame {f.number}: {rate:.2f} words/s — listen to it")
    print(f"  {flags} frame(s) flagged" if flags else "  all frames in range")
    if blocked:
        print(f"\n✗ {len(blocked)} video(s) blocked by the narration gate: " + ", ".join(map(str, blocked)))
    sys.exit(1 if failed or blocked else 0)


if __name__ == "__main__":
    main()
