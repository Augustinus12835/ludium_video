#!/usr/bin/env python
"""LaTeX dry-run preflight for authored Manim frames.

Runs `manim -ql -n 99999`, which walks the whole `construct()` and compiles every
Tex/MathTex but renders no video. That turns a class of failures that would cost a
full 4K render into a few seconds:

  * LaTeX/dvi errors (unbalanced groups from `t2c` surgery, missing packages)
  * Python errors anywhere in the scene body
  * a scene clock that does not land on the audio duration -- reported as the
    final `self._t`, so a mismatch is caught BEFORE rendering. `wait_to()` is
    monotonic, so a target already passed silently no-ops and the scene overshoots.

Each run gets its OWN media dir. Concurrent manim jobs sharing one `--media_dir`
race the Tex cache and die with a misleading "does not support converting .dvi to
SVG" (see MEMORY.md manim_parallel_media_dir_race).

usage:
    preflight_manim.py pipeline/<L>/Video-N              # every frame, vs its audio
    preflight_manim.py frames/frame_3_manim.py [12.5]    # one frame, optional duration

Exit code is non-zero if any frame fails to compile or overshoots its audio.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON = REPO / "venv/bin/python"
SCENE = "MathAnimation"
TOLERANCE = 0.35  # seconds; beyond this the scene and audio visibly disagree

# Appended to a copy of the frame so the scene reports its final clock value.
EPILOGUE = """

_PREFLIGHT_ORIG_CONSTRUCT = {scene}.construct


def _preflight_construct(self):
    _PREFLIGHT_ORIG_CONSTRUCT(self)
    print("PREFLIGHT_FINAL_T", round(getattr(self, "_t", -1.0), 3), flush=True)


{scene}.construct = _preflight_construct
"""


def audio_duration(video_dir, frame_no):
    """Decoded duration of the frame's narration mp3, or None."""
    mp3 = Path(video_dir) / "audio" / f"frame_{frame_no}.mp3"
    if not mp3.exists():
        return None
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(mp3)],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def preflight(src, expected=None, keep_workdir=False):
    """Compile-only run of one frame. Returns (ok, final_t, message)."""
    src = Path(src).resolve()
    work = Path(tempfile.mkdtemp(prefix=f"preflight_{src.stem}_"))
    try:
        tmp_py = work / f"{src.stem}_preflight.py"
        tmp_py.write_text(src.read_text(encoding="utf-8")
                          + EPILOGUE.format(scene=SCENE), encoding="utf-8")

        proc = subprocess.run(
            [str(PYTHON), "-m", "manim", "render", "-ql", "-n", "99999",
             "--media_dir", str(work / "media"), str(tmp_py), SCENE],
            capture_output=True, text=True, cwd=str(REPO),
            env=dict(os.environ), timeout=1800)
        output = proc.stdout + proc.stderr
        match = re.search(r"PREFLIGHT_FINAL_T ([\d.\-]+)", output)

        if proc.returncode != 0 or match is None:
            return False, None, output[-4000:]

        final_t = float(match.group(1))
        if expected is None:
            return True, final_t, f"final self._t = {final_t:.3f}s (no audio to compare)"

        drift = final_t - expected
        if abs(drift) <= TOLERANCE:
            return True, final_t, (
                f"final self._t = {final_t:.3f}s vs audio {expected:.3f}s "
                f"(drift {drift:+.3f}s)")
        hint = ("scene OVERSHOOTS -- a wait_to target was already passed "
                "(wait_to is monotonic)" if drift > 0
                else "scene ENDS EARLY -- beats are packed too tightly")
        return False, final_t, (
            f"final self._t = {final_t:.3f}s vs audio {expected:.3f}s "
            f"(drift {drift:+.3f}s) *** {hint}")
    finally:
        if not keep_workdir:
            shutil.rmtree(work, ignore_errors=True)


def main(argv):
    if not argv:
        print(__doc__)
        return 2

    target = Path(argv[0])
    if target.is_dir():
        frames_dir = target / "frames"
        sources = sorted(
            frames_dir.glob("frame_*_manim.py"),
            key=lambda p: int(re.search(r"frame_(\d+)_", p.name).group(1)))
        jobs = [(p, audio_duration(target, re.search(r"frame_(\d+)_", p.name).group(1)))
                for p in sources]
    else:
        expected = float(argv[1]) if len(argv) > 1 else None
        jobs = [(target, expected)]

    if not jobs:
        print(f"no frame_*_manim.py found under {target}")
        return 2

    failures = 0
    for src, expected in jobs:
        ok, _final_t, message = preflight(src, expected)
        print(f"{'OK  ' if ok else 'FAIL'} {src.name}")
        for line in message.splitlines():
            print(f"       {line}")
        if not ok:
            failures += 1

    print()
    print(f"PREFLIGHT: {len(jobs) - failures}/{len(jobs)} passed"
          + ("" if not failures else f" -- {failures} need fixing before rendering"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
