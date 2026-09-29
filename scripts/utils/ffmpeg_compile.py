#!/usr/bin/env python3
"""
Bounded ffmpeg compilation helpers (segment-wise encode + concat demuxer).

WHY THIS EXISTS
---------------
A compile built as ONE monolithic ffmpeg invocation — every scene an input and a
`concat` FILTER joining them at the end — does not scale. ffmpeg opens and
decodes all inputs, but the concat filter drains only the segment it is
currently on, so raw (uncompressed) frames for every other segment pile up in
the filter links. At 1080p a raw frame is ~3 MB, at 4K ~12 MB — a ~290-segment
compile reached 43-50 GB RSS and the kernel OOM-killed the whole desktop session. Memory scaled with TOTAL INPUT LENGTH, not with the
output.

The fix is structural, not a tuning knob: encode each segment in its own ffmpeg
process (ONE input each, so peak = one decoder + one encoder), then join the
finished segments with the concat DEMUXER and stream-copy (`-c copy`), which
never decodes anything. Peak memory is then O(1) in the number of frames/beats.

Audio stays a single sample-accurate `concat` filter pass (audio-only graphs
buffer PCM, which is ~3 orders of magnitude smaller than video) so narration
remains gapless and `generate_subtitles.py`'s decoded-duration stacking keeps
working.
"""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

FPS = 30
# Parallel segment encodes. Each is a single-input ffmpeg (a few hundred MB at
# most), so this is about CPU, not memory. Keep it modest: segment encoding is
# only a fraction of a compile and the box may be running other producers.
ENCODE_JOBS = max(1, int(os.getenv("COMPILE_ENCODE_JOBS", "3")))


def frame_span(start: float, end: float, fps: int = FPS) -> int:
    """Frame count for [start, end), anchored to ABSOLUTE time.

    Rounding each boundary against the absolute timeline (rather than rounding
    each duration on its own) keeps every segment boundary within half a frame
    of its true audio time — per-segment error never accumulates down the video.
    """
    return max(1, int(round(end * fps)) - int(round(start * fps)))


def run(cmd: Sequence[str]) -> Tuple[bool, str]:
    res = subprocess.run(list(cmd), capture_output=True, text=True)
    return res.returncode == 0, res.stderr[-2000:]


def encode_segments(cmds: List[Sequence[str]], label: str = "segment",
                    jobs: int = ENCODE_JOBS) -> Tuple[bool, str]:
    """Run per-segment encodes in parallel. Returns (ok, first error message)."""
    done = 0
    total = len(cmds)
    failures: List[str] = []

    def _one(idx_cmd):
        idx, cmd = idx_cmd
        ok, err = run(cmd)
        return idx, ok, err

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for idx, ok, err in pool.map(_one, list(enumerate(cmds))):
            done += 1
            if not ok:
                failures.append(f"{label} {idx}: {err}")
            if done % 25 == 0 or done == total:
                print(f"    {label}s encoded: {done}/{total}", flush=True)
    if failures:
        return False, failures[0]
    return True, ""


def build_audio_track(audio_files: Sequence[Path], out_path: Path) -> Tuple[bool, str]:
    """Concatenate narration mp3s into one AAC track, sample-accurately.

    A `concat` filter (not the demuxer) so mp3 encoder delay/padding is trimmed
    on decode and the segments butt up with no gaps — the demuxer would leave a
    ~30 ms hole per file and walk the subtitle timeline off.
    """
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p in audio_files:
        cmd += ["-i", str(p)]
    n = len(audio_files)
    parts = [f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]" for i in range(n)]
    parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[audio]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[audio]",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out_path)]
    return run(cmd)


def concat_mux(segments: Sequence[Path], audio_path: Optional[Path],
               out_path: Path, list_path: Path) -> Tuple[bool, str]:
    """Join encoded segments with the concat DEMUXER and stream-copy.

    Nothing is decoded here, so this stays flat in memory no matter how many
    segments there are. All segments must share codec/resolution/pix_fmt —
    they do, because this module encoded them all the same way.
    """
    list_path.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in segments),
        encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_path)]
    if audio_path is not None:
        cmd += ["-i", str(audio_path), "-c:a", "copy", "-shortest"]
    cmd += ["-c:v", "copy", "-movflags", "+faststart", str(out_path)]
    return run(cmd)


COVER = ("scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,"
         "crop=1920:1080,setsar=1")                       # fill the frame, crop overflow
CONTAIN = ("scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
           "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1")   # letterbox, never crop


def still_segment_cmd(image: Path, n_frames: int, out_path: Path,
                      crf: str = "21", extra_vf: str = "",
                      fit: str = "cover") -> List[str]:
    """One still image held for exactly n_frames — no zoom, no pan.

    ``fit`` picks framing: "cover" crops to fill (a 16:9 slate loses nothing)
    and "contain" letterboxes (frames of unknown aspect —
    never silently crop someone's diagram).
    """
    vf = COVER if fit == "cover" else CONTAIN
    if extra_vf:
        vf += "," + extra_vf
    return ["ffmpeg", "-y", "-v", "error",
            "-loop", "1", "-framerate", str(FPS), "-i", str(image),
            "-frames:v", str(n_frames), "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", crf,
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(out_path)]


def clip_segment_cmd(clip: Path, n_frames: int, out_path: Path,
                     crf: str = "21", extra_vf: str = "",
                     pad_style: str = "clone") -> List[str]:
    """An existing mp4 (Manim/Remotion) fitted to exactly n_frames.

    `tpad` clones the last frame so a clip that runs a hair SHORT still fills
    its span instead of ending early and dragging every later cut out of sync.
    """
    vf = f"fps={FPS},{CONTAIN},tpad=stop_mode={pad_style}:stop_duration=2"
    if extra_vf:
        vf += "," + extra_vf
    return ["ffmpeg", "-y", "-v", "error", "-i", str(clip),
            "-frames:v", str(n_frames), "-vf", vf, "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", crf,
            "-pix_fmt", "yuv420p", "-r", str(FPS), str(out_path)]
