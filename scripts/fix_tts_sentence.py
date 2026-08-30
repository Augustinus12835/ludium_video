#!/usr/bin/env python
"""Standard TTS-fix workflow: sentence-level audio swap (zero shift).

When a frame's spoken narration has a TTS mispronunciation (e.g. lowercase variable `a`
read as the article "uh", a gobbled number, a mispronounced term), the fix used to require
regenerating the WHOLE frame's audio and then re-timing the Manim animation, because TTS
pacing drifts run-to-run. This tool avoids that: it regenerates ONLY the sentence(s) whose
text changed and splices each back into the existing audio, **time-stretched (pitch-
preserving) to the exact original sentence span**, so the total duration and every other
word's timing are unchanged — no re-timing, no Manim re-render, just recompile.

Workflow:
  1. Edit the spoken source to fix the text:
       - every frame  -> narration in script.json (the norm since 2026-08-30)
       - LEGACY math frames that still carry a verified natural_narration in
         math_verification.json -> edit it THERE (that is what the audio was voiced from)
     (This tool reads the SAME source generate_tts_elevenlabs.get_natural_narration() reads.)
  2. python scripts/fix_tts_sentence.py pipeline/<L>/Video-N [--frame F ...] [--dry-run]
     Omit --frame to auto-process every frame whose source text now differs from its audio.
  3. Recompile: python scripts/compile_video.py pipeline/<L>/Video-N
     (subtitles regenerate from the updated word timestamps).

It backs up each frame's audio + timestamps to *.prefix.bak before changing them.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.generate_tts_elevenlabs import call_elevenlabs_api, get_natural_narration  # noqa: E402

SR = 44100


# ---------- audio helpers ----------
def decode(path=None, data=None):
    cmd = ["ffmpeg", "-v", "error", "-i", str(path) if path else "pipe:0",
           "-ar", str(SR), "-ac", "1", "-f", "s16le", "pipe:1"]
    p = subprocess.run(cmd, input=data, stdout=subprocess.PIPE)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def encode(arr, path):
    pcm = (np.clip(arr, -1, 1) * 32767).astype(np.int16).tobytes()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1",
                    "-i", "pipe:0", "-codec:a", "libmp3lame", "-b:a", "192k", str(path)], input=pcm)


def atempo_fit(pcm, target_n):
    """Pitch-preserving time-stretch of pcm to exactly target_n samples."""
    if len(pcm) == 0:
        return np.zeros(target_n, np.float32)
    factor = min(max(len(pcm) / target_n, 0.5), 2.0)  # >1 => speed up (shorten)
    raw = (np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes()
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1",
                        "-i", "pipe:0", "-filter:a", f"atempo={factor:.6f}",
                        "-f", "s16le", "-ar", str(SR), "-ac", "1", "pipe:1"],
                       input=raw, stdout=subprocess.PIPE)
    out = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if len(out) >= target_n:
        return out[:target_n]
    return np.concatenate([out, np.zeros(target_n - len(out), np.float32)])


def xfade(a, b, n=int(0.008 * SR)):
    if len(a) < n or len(b) < n:
        return np.concatenate([a, b])
    w = np.linspace(0, 1, n)
    mix = a[-n:] * (1 - w) + b[:n] * w
    return np.concatenate([a[:-n], mix, b[n:]])


# ---------- sentence segmentation ----------
def word_sentences(words):
    """Yield (start_idx, end_idx_inclusive) ranges; a sentence ends on a word ending in .!?"""
    start = 0
    for i, w in enumerate(words):
        t = (w.get("word") or "").strip()
        if t and t[-1] in ".!?":
            yield (start, i)
            start = i + 1
    if start < len(words):
        yield (start, len(words) - 1)


def text_sentences(text):
    """Split text into sentence strings, preserving terminal punctuation."""
    parts = re.findall(r"[^.!?]*[.!?]+|\S[^.!?]*$", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


# ---------- core ----------
def fix_frame(video_dir: Path, frame: int, script_frames: dict, math_data, dry=False,
              voice_id=None, resay=None):
    audio = video_dir / "audio" / f"frame_{frame}.mp3"
    tsp = video_dir / "audio" / f"frame_{frame}_timestamps.json"
    if not audio.exists() or not tsp.exists():
        print(f"  frame {frame}: audio/timestamps missing, skip")
        return False

    ts = json.loads(tsp.read_text())
    old_text = ts.get("text", "")
    words = ts["words"]
    word_ranges = list(word_sentences(words))
    # Always fill the original span via gentle atempo (a little stretch is fine); a held
    # pause to fill the slack reads as awkward. To avoid a big stretch when the new wording
    # is much shorter, rewrite the sentence longer (text-edit path) so it fills naturally.
    natural = False

    if resay:
        # Force re-synthesis of specific sentence(s) in --voice-id at their ORIGINAL spans
        # (text unchanged, duration preserved). Use to repair a sentence voiced in the
        # wrong voice — e.g. an earlier edit regenerated with a different voice ID than
        # the rest of the video's audio.
        new_text = old_text
        changed = []
        for i, (s, e) in enumerate(word_ranges):
            old_sent = " ".join((words[k].get("word") or "") for k in range(s, e + 1))
            if any(_norm(old_sent) == _norm(rt) for rt in resay):
                changed.append((i, s, e, old_sent))
        if not changed:
            print(f"  frame {frame}: no sentence matched --resay, skip")
            return False
    else:
        sf = script_frames.get(frame)
        original_narration = sf.get("narration", "") if sf else ""
        new_text = get_natural_narration(frame, original_narration, math_data)

        if _norm(old_text) == _norm(new_text):
            print(f"  frame {frame}: source unchanged vs audio — nothing to swap")
            return False

        new_sents = text_sentences(new_text)
        if len(word_ranges) != len(new_sents):
            print(f"  frame {frame}: sentence count mismatch (audio {len(word_ranges)} vs "
                  f"source {len(new_sents)}) — sentence-swap unsafe; regenerate the whole frame "
                  f"(delete the mp3 + run generate_tts_elevenlabs.py) instead.")
            return False

        changed = []
        for i, (s, e) in enumerate(word_ranges):
            old_sent = " ".join((words[k].get("word") or "") for k in range(s, e + 1))
            if _norm(old_sent) != _norm(new_sents[i]):
                changed.append((i, s, e, new_sents[i]))
        if not changed:
            print(f"  frame {frame}: text differs but no per-sentence change detected, skip")
            return False

    print(f"  frame {frame}: {len(changed)} sentence(s) to swap")
    for _, _, _, ns in changed:
        print(f"      -> {ns[:80]!r}")
    if dry:
        return True

    old = decode(path=audio)
    total = len(old) / SR
    out = old.copy()
    new_words_all = words[:]  # we rebuild this in place

    # Splice last sentence first so untouched sample offsets stay valid (lengths preserved).
    for i, s, e, new_sent in sorted(changed, key=lambda x: -x[1]):
        # region = silence-midpoint before .. silence-midpoint after (seams land in silence)
        rs = (words[s - 1]["end"] + words[s]["start"]) / 2 if s > 0 else max(0.0, words[s]["start"] - 0.12)
        re_ = (words[e]["end"] + words[e + 1]["start"]) / 2 if e + 1 < len(words) else min(total, words[e]["end"] + 0.20)
        s0, s1 = int(round(rs * SR)), int(round(re_ * SR))
        region_n = s1 - s0
        # ORIGINAL speech span inside the region (the rest is lead/trail pause we must keep)
        sp_start, sp_end = words[s]["start"], words[e]["end"]
        lead_n = max(0, int(round((sp_start - rs) * SR)))
        speech_n = max(1, int(round((sp_end - sp_start) * SR)))

        regen_bytes, regen_words = call_elevenlabs_api(new_sent, voice_id=voice_id)
        npcm = decode(data=regen_bytes)
        # trim the regen's own lead/trail silence using its word timestamps -> pure speech
        if regen_words:
            r0 = max(0.0, regen_words[0]["start"])
            r1 = min(len(npcm) / SR, regen_words[-1]["end"])
        else:
            r0, r1 = 0.0, len(npcm) / SR
        regen_speech = npcm[int(r0 * SR):int(r1 * SR)]

        # Placement. In `natural` mode (resay/voice-repair) play the regen at its OWN pace
        # when it fits the region — no time-stretch — and absorb the slack as trailing pause
        # (keeps the frame duration, so downstream timing is untouched). Otherwise
        # atempo-fit the regen to the original speech span (the classic zero-shift swap).
        if natural and lead_n + len(regen_speech) <= region_n:
            fitted_speech = regen_speech
        else:
            if lead_n + speech_n > region_n:
                speech_n = max(1, region_n - lead_n)
            fitted_speech = atempo_fit(regen_speech, speech_n)     # speech-to-speech stretch (gentle)
        place_n = len(fitted_speech)
        trail_n = max(0, region_n - lead_n - place_n)
        content = np.concatenate([np.zeros(lead_n, np.float32), fitted_speech,
                                  np.zeros(trail_n, np.float32)])
        content = content[:region_n] if len(content) >= region_n else \
            np.concatenate([content, np.zeros(region_n - len(content), np.float32)])
        out = xfade(xfade(out[:s0], content), out[s1:])

        # rebuild word timestamps, mapped onto the span actually occupied by the placed speech
        sp_len = max(r1 - r0, 1e-6)
        place_dur = place_n / SR
        mapped = []
        for w in regen_words:
            mapped.append({
                "word": w.get("word"),
                "start": round(sp_start + ((w["start"] - r0) / sp_len) * place_dur, 3),
                "end": round(sp_start + ((w["end"] - r0) / sp_len) * place_dur, 3),
            })
        new_words_all[s:e + 1] = mapped
        stretch = place_n / max(len(regen_speech), 1)
        print(f"      swapped [{rs:.2f}-{re_:.2f}] region {region_n / SR:.2f}s, "
              f"speech {speech_n / SR:.2f}s/regen {len(regen_speech) / SR:.2f}s "
              f"(stretch {stretch:.2f}x{', NATURAL' if natural and place_n==len(regen_speech) else ''}), "
              f"{len(mapped)} words")

    # backups
    shutil.copy(audio, audio.with_suffix(".mp3.prefix.bak"))
    shutil.copy(tsp, tsp.with_suffix(".json.prefix.bak"))

    encode(out, audio)
    ts["text"] = new_text
    ts["words"] = new_words_all
    tsp.write_text(json.dumps(ts, indent=2, ensure_ascii=False))
    print(f"      dur {total:.3f} -> {len(out) / SR:.3f}s ({len(out) / SR - total:+.4f})  ✓ written")
    return True


def main():
    ap = argparse.ArgumentParser(description="Sentence-level zero-shift TTS fix")
    ap.add_argument("video_dir")
    ap.add_argument("--frame", type=int, action="append", help="frame number(s); omit = all changed")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--voice-id", default=None,
                    help="ElevenLabs voice for regenerated sentences "
                         "(default: ELEVENLABS_VOICE_ID from .env). Pass the video's actual "
                         "voice when it differs from the current .env default, so the spliced "
                         "sentence matches the existing audio.")
    ap.add_argument("--resay", action="append", default=None,
                    help="Force re-synthesis of the sentence whose text matches this, in --voice-id, "
                         "at its original span (text unchanged). Repeatable. For voice repairs.")
    args = ap.parse_args()

    vd = Path(args.video_dir)
    script = json.loads((vd / "script.json").read_text())
    script_frames = {int(f["number"]): f for f in script.get("frames", [])}
    mvp = vd / "math_verification.json"
    math_data = json.loads(mvp.read_text()) if mvp.exists() else None

    frames = args.frame if args.frame else sorted(script_frames)
    print(f"{vd}  ({len(frames)} frame(s) to check){'  [DRY RUN]' if args.dry_run else ''}")
    n = 0
    for f in frames:
        if fix_frame(vd, f, script_frames, math_data, dry=args.dry_run,
                     voice_id=args.voice_id, resay=args.resay):
            n += 1
    print(f"\n{'Would fix' if args.dry_run else 'Fixed'} {n} frame(s). "
          f"{'' if args.dry_run else 'Recompile: python scripts/compile_video.py ' + str(vd)}")


if __name__ == "__main__":
    main()
