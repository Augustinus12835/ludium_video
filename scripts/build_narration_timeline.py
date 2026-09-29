#!/usr/bin/env python3
"""
Build the global narration word-timeline for folio (humanities) mode.

After TTS, each narration frame has audio/frame_N.mp3 + frame_N_timestamps.json.
This stacks them onto one continuous timeline (offsets summed from DECODED audio
durations — container metadata drifts ~30ms/file) and writes:

  narration_timeline.json  {total_duration, word_count, words:[{word,start,end}],
                            narration_beats:[{frame,t_start,t_end,narration}]}
  narration_timeline.txt   readable per-frame lines with [t_start-t_end] markers

The folio director reads this to lay scenes over the narration by verbatim anchor
phrases, cutting at any word boundary (incl. mid-sentence); scripts/folio.py snaps
the anchors and every on-screen cue to this word clock.

Usage:
    python build_narration_timeline.py pipeline/<L>/Video-N
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.utils.script_parser import load_script


def decoded_duration(p: Path) -> float:
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(p), "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
        capture_output=True)
    return len(pcm.stdout) / (2 * 48000)


def build_timeline(video_dir: Path) -> dict:
    video_dir = Path(video_dir)
    adir = video_dir / "audio"
    sd = load_script(video_dir)

    words = []
    spans = []
    offset = 0.0
    for fr in sd.frames:
        ts = adir / f"frame_{fr.number}_timestamps.json"
        mp3 = adir / f"frame_{fr.number}.mp3"
        if not ts.exists() or not mp3.exists():
            raise FileNotFoundError(f"missing audio/timestamps for frame {fr.number}")
        data = json.loads(ts.read_text())
        start_off = offset
        for w in data.get("words") or []:
            words.append({"word": w["word"],
                          "start": round(w["start"] + offset, 3),
                          "end": round(w["end"] + offset, 3)})
        dur = decoded_duration(mp3)
        spans.append({"frame": fr.number, "t_start": round(start_off, 3),
                      "t_end": round(start_off + dur, 3), "narration": fr.narration})
        offset += dur

    out = {"total_duration": round(offset, 3), "word_count": len(words),
           "words": words, "narration_beats": spans}
    (video_dir / "narration_timeline.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))
    (video_dir / "narration_timeline.txt").write_text(
        "\n".join(f"[{b['t_start']:.1f}-{b['t_end']:.1f}] {b['narration']}" for b in spans))
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python build_narration_timeline.py pipeline/<L>/Video-N")
        sys.exit(1)
    t = build_timeline(Path(sys.argv[1]))
    print(f"narration_timeline.json: {t['word_count']} words, {t['total_duration']:.1f}s "
          f"({len(t['narration_beats'])} narration beats)")
