#!/usr/bin/env python3
"""
Math Verification helpers

Math/technical verification is authored by a Claude Code subagent: render each
frame's prompt with `render_step_prompt.py verify_math --video-dir DIR --frame N`
(then `color_plan`), and write the results to math_verification.json. This
module keeps the shared pieces that flow imports: SYMPY_HELPERS, the
script.json readers, the SymPy check runner, and the running-context builder.

Pipeline position:
    script.json → (Claude Code subagent via render_step_prompt.py) → math_verification.json
"""

import sys
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Add parent to path for utils
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.utils.script_parser import load_script


SYMPY_HELPERS = '''\
import math as _eq_math
import sympy as sp


def equiv(a, b):
    """Robust mathematical equivalence: symbolic + numerical sampling fallback.

    `sp.simplify(a - b) == 0` is fragile in the presence of `Abs(x)`, factored
    radicals, and piecewise expressions: SymPy may return surface-different but
    numerically identical forms. This helper falls back to sampling at multiple
    real points, respecting `positive=True` assumptions on symbols."""
    a, b = sp.sympify(a), sp.sympify(b)
    diff = sp.simplify(a - b)
    if diff == 0:
        return True
    try:
        if sp.simplify(sp.radsimp(sp.expand(diff))) == 0:
            return True
    except Exception:
        pass
    free = (a - b).free_symbols
    if not free:
        return False
    base_pts = [sp.Rational(7, 5), sp.Rational(11, 7), sp.Rational(23, 9),
                sp.Rational(31, 11), sp.Rational(53, 17)]
    pts = base_pts + [-p for p in base_pts]
    syms_sorted = sorted(free, key=lambda s: s.name)
    matched = 0
    tried = 0
    for i in range(len(pts)):
        subs = {}
        for j, s in enumerate(syms_sorted):
            v = pts[(i + j) % len(pts)]
            if getattr(s, "is_positive", False) and v < 0:
                v = -v
            subs[s] = v
        try:
            ea = complex(a.evalf(subs=subs, n=25))
            eb = complex(b.evalf(subs=subs, n=25))
        except Exception:
            continue
        if not (_eq_math.isfinite(ea.real) and _eq_math.isfinite(ea.imag)
                and _eq_math.isfinite(eb.real) and _eq_math.isfinite(eb.imag)):
            continue
        tried += 1
        scale = max(1.0, abs(ea), abs(eb))
        if abs(ea - eb) > 1e-8 * scale:
            return False
        matched += 1
    return tried >= 3 and matched == tried


def is_antiderivative(F, f, var):
    """Verify F is an antiderivative of f w.r.t. var by differentiating F.

    More reliable than `sp.integrate(f, var) == F`: the heuristic integrator
    often returns RootSum, unevaluated, or structurally different (but equal)
    antiderivatives."""
    return equiv(sp.diff(F, var), f)


'''


def parse_script_from_dir(video_dir: Path) -> Tuple[str, List[Dict]]:
    """
    Parse script file (JSON or MD) to extract frame information.

    Uses the shared script_parser utility for consistent parsing.

    Returns:
        (title, frames_list)
    """
    script_data = load_script(video_dir)

    frames = []
    for frame in script_data.frames:
        frames.append({
            "number": frame.number,
            "timing": frame.timing_str,
            "word_count": frame.word_count,
            "narration": frame.narration,
            "visual_ref": frame.visual.reference if frame.visual else ""
        })

    return script_data.title, frames


def load_raw_script_json(video_dir: Path) -> Dict:
    """Load script.json raw (fields like frame_class/metadata that the parsed
    ScriptData doesn't carry). Empty dict if absent/unreadable."""
    json_path = video_dir / "script.json"
    if not json_path.exists():
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def get_requires_math(video_dir: Path) -> bool:
    """Resolve the requires_math flag.

    Priority: script.json metadata (script generation declares it) →
    visual_specs.json (legacy brief-step artifact) → True (old videos).
    """
    raw = load_raw_script_json(video_dir)
    meta_flag = raw.get("metadata", {}).get("requires_math")
    if isinstance(meta_flag, bool):
        return meta_flag

    specs_path = video_dir / "visual_specs.json"
    if specs_path.exists():
        try:
            with open(specs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("requires_math"), bool):
                return data["requires_math"]
        except (json.JSONDecodeError, IOError):
            pass

    return True  # Default to True for old videos


def get_script_frame_classes(video_dir: Path) -> Dict[int, str]:
    """frame_class declared at script-generation time (technical/math modes).

    Returns {frame_number: 'math'|'code'|'visual'}. Frames present here skip
    the per-frame Claude classification call; legacy scripts without the field
    fall back to detect_frame_type()."""
    classes = {}
    raw = load_raw_script_json(video_dir)
    for fr in raw.get("frames", []):
        fc = fr.get("frame_class")
        if fc in ("math", "code", "visual") and fr.get("number") is not None:
            classes[fr["number"]] = fc
    return classes


def execute_sympy_check(code: str, verbose: bool = False) -> Tuple[bool, str]:
    """
    Execute SymPy verification code in a subprocess.

    Args:
        code: Python code string with SymPy assertions
        verbose: Show the code being executed

    Returns:
        (passed, error_message) — True if all assertions pass
    """
    if verbose:
        print(f"    SymPy code:\n{'='*40}")
        for i, line in enumerate(code.split('\n'), 1):
            print(f"      {i:3d} | {line}")
        print(f"{'='*40}")

    full_code = SYMPY_HELPERS + code
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(full_code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            if verbose and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    print(f"      {line}")
            return True, ""
        else:
            error = result.stderr.strip()
            # Extract the most useful part of the traceback
            lines = error.split('\n')
            # Find AssertionError or last meaningful line
            for line in reversed(lines):
                if 'AssertionError' in line or 'Error' in line:
                    return False, line.strip()
            return False, lines[-1] if lines else "Unknown error"

    except subprocess.TimeoutExpired:
        return False, "SymPy verification timed out (30s)"
    except Exception as e:
        return False, f"Execution error: {e}"
    finally:
        os.unlink(tmp_path)


def _build_context_from_verification(
    prior_context: str,
    math_steps: List[Dict],
    final_answer: Optional[str],
    frame_num: int,
) -> str:
    """
    Auto-build context summary from verification results when Claude doesn't
    provide an explicit math_context_update.

    Appends the last 2-3 steps from the current frame to prior context,
    truncating at ~500 chars.
    """
    parts = []
    if prior_context:
        parts.append(prior_context.rstrip(". ") + ".")

    # Take last 2-3 steps as the most relevant results
    recent_steps = math_steps[-3:] if len(math_steps) > 3 else math_steps
    step_summaries = []
    for step in recent_steps:
        expr = step.get("expression", "")
        op = step.get("operation", "")
        if expr and op:
            step_summaries.append(f"{op}: {expr}")
        elif op:
            step_summaries.append(op)

    if step_summaries:
        parts.append(f"Frame {frame_num}: " + "; ".join(step_summaries) + ".")

    if final_answer:
        parts.append(f"Result: {final_answer}.")

    context = " ".join(parts)
    # Truncate to ~500 chars to keep prompts manageable
    if len(context) > 500:
        context = context[:497] + "..."
    return context


def main():
    sys.exit(
        "Math/technical verification is authored by a Claude Code subagent.\n"
        "Render its prompts per frame: python scripts/render_step_prompt.py "
        "verify_math --video-dir pipeline/<L>/Video-N --frame N\n"
        "then color_plan --video-dir <dir> once all frames are written to "
        "math_verification.json."
    )


if __name__ == "__main__":
    main()
