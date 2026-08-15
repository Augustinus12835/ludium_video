#!/usr/bin/env python3
"""
Render the exact (system, user) prompt for a pipeline step.

Every LLM step in this pipeline is authored by a Claude Code subagent; this
renderer feeds the subagent the exact prompt for the step. Single source of
truth: it imports prompt constants from the pipeline scripts themselves, so
the rendered prompt cannot drift from what the scripts expect. If the Python
changes, this renderer changes too.

Output is a single JSON object on stdout:
    {"system": "...", "user": "...", "notes": "..."}

Usage:
    python scripts/render_step_prompt.py STEP [options]

Steps:
    clean                 --transcript FILE [--chunk-index N]
    segment               --content FILE
    script                --video-dir DIR --mode math|technical
    verify_math           --video-dir DIR --frame N [--prior-context FILE] [--sympy-error TEXT]
    color_plan            --video-dir DIR   (video-level semantic color plan, AFTER all
                          frames are verified; result goes top-level in math_verification.json)
    sympy_gen             --video-dir DIR --frame N
    manim                 --video-dir DIR --frame N --duration SEC --word-transcript FILE
                          [--prior-context FILE]
                          (omit --frame for a manifest of animatable frames;
                           --duration/--word-transcript auto-derive after tts)

All STEP subcommands also accept --pretty to indent the JSON for inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Make `from scripts.*` imports work when invoked as a module or file.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Prompt constants — imported directly so the renderer cannot drift from
# what the pipeline scripts expect.
from scripts.clean_transcript import CLEANING_PROMPT as CLEAN_PIPELINE_PROMPT
from scripts.segment_concepts import SEGMENTATION_PROMPT
from scripts.generate_scripts import (
    MATH_SCRIPT_GENERATION_PROMPT,
    TECHNICAL_SCRIPT_GENERATION_PROMPT,
    PLANNING_BLOCK,
    build_source_block,
    compute_duration_and_frame_hints,
    load_segments,
    load_style_guide,
    load_video_source,
)
from scripts.generate_math_animation import (
    build_claude_prompt as build_manim_user_prompt,
    load_system_prompt as load_manim_system_prompt,
    get_audio_duration_ffprobe,
    load_math_verification,
    select_frames_for_animation,
    load_stored_word_timestamps,
    align_words_to_script,
    format_word_transcript,
)
from scripts.utils.script_parser import load_script
from scripts.utils.verify_prompts import (
    VERIFY_MATH_SYSTEM,
    COLOR_PLAN_SYSTEM,
    build_color_plan_user,
)


# -----------------------------------------------------------------------------
# System prompts that the scripts use inline (not module constants)
# -----------------------------------------------------------------------------

CLEAN_PIPELINE_SYSTEM = "Extract educational content. Be thorough but concise."

SEGMENT_SYSTEM = (
    "You are segmenting educational content into concept videos. "
    "Output valid JSON only. Ensure all JSON strings are properly escaped."
)

SCRIPT_SYSTEMS = {
    "math": (
        "You are an expert educational script writer for math videos. "
        "Create clear, precise narration that follows the teaching flow. "
        "The narration must be COMPLETE — every calculation and result spoken "
        "aloud. The visual field is animation guidance only. Be concise - no "
        "filler words. Output ONLY valid JSON."
    ),
    "technical": (
        "You are an expert educational script writer for technical subjects. "
        "Create clear, precise narration that follows the teaching flow. "
        "The narration must be COMPLETE — every concept and calculation spoken "
        "aloud. The visual field is animation guidance for Manim. Be concise - "
        "no filler words. Output ONLY valid JSON."
    ),
}

VERIFY_MATH_USER_TEMPLATE = """Analyze this educational content and extract its sequential structure for animation, following the TASK in your instructions.

FRAME NUMBER: {frame_number}

ORIGINAL NARRATION:
{narration}

VISUAL CONTEXT:
{visual_context}
{prior_context_section}"""

# SymPy verification code-gen prompt (this renderer is its single source).
SYMPY_SYSTEM = """You write SymPy verification code for mathematical claims in educational content.

Given a list of math steps from a calculus/linear algebra lecture, write a standalone Python script that:
1. Imports sympy (and numpy only if needed for numerical checks)
2. Encodes each verifiable math claim as an assertion
3. Prints "PASS: [description]" for each assertion that holds
4. Raises AssertionError with a clear message if any check fails

WHAT TO VERIFY:
- Arithmetic: evaluate expressions, check equalities (e.g., 16*8 == 128)
- Algebra: simplifications, factoring, expanding (e.g., expand(expr) == expected)
- Derivatives: diff(f, x) == expected_derivative
- Integrals: integrate(f, x) == expected_antiderivative
- Evaluations: expr.subs(x, val) == expected_value
- Matrix operations: determinants, eigenvalues, inverses, rank
- Equation solving: solve(eq, var) == expected_solutions
- Point verification: substituting a point into an equation equals expected value

WHAT TO SKIP (return None for these):
- Purely procedural/conceptual steps with no math to verify
- Steps that are just labels or descriptions
- Steps where the "expression" is plain text (starts with \\text{})

RULES:
- Use sympy.Rational for fractions to avoid floating-point issues
- Use sympy.symbols() for variables
- Use sympy.Function('y')(x) for implicit functions when needed
- For implicit differentiation: use idiff() or manually differentiate with y as Function
- ALWAYS use symbolic equality: assert sp.simplify(a - b) == 0, not str(a) == str(b)
  NEVER compare string representations — SymPy formatting varies (e.g., "3*(x - 1)*(x + 1)" vs "3(x-1)(x+1)")
- For factoring checks: assert sp.simplify(sp.factor(expr) - expected) == 0
- For absolute value equations, declare variables as real: symbols('x', real=True).
  SymPy raises NotImplementedError on solve(Abs(x) - k, x) unless x is real.
  Example: x = sp.symbols('x', real=True); assert set(sp.solve(sp.Abs(x) - 5, x)) == {-5, 5}
- For log/exp cancellation identities (ln(e^u) = u, e^(ln u) = u, log(x*y) = log(x)+log(y)),
  declare variables as real (and positive when inside ln): symbols('x', real=True) or
  symbols('x', positive=True). Without real/positive assumptions, simplify(log(exp(x)) - x)
  returns -x + log(exp(x)) (not 0) because the identity only holds on the principal branch.
  Example: u = sp.symbols('u', real=True); assert sp.simplify(sp.log(sp.exp(u)) - u) == 0
  For ln(x*y) = ln(x)+ln(y) style expansions, use positive=True and sp.expand_log(expr, force=True).
- For fractional powers of negative numbers, SymPy returns complex principal roots:
  (-1)**(Rational(1,3)) is NOT -1 in SymPy. Use sp.real_root(base, n) instead.
  Example: sp.real_root(-8, 3) == -2, NOT (-8)**sp.Rational(1,3)
  For cube roots specifically: sp.cbrt(x) gives real cube root.
  When evaluating f(x) at negative x with fractional exponents, substitute THEN simplify with real_root.
- Each assertion must have a descriptive message
- Keep the code simple and focused — no classes, no fancy structure
- If NO steps have verifiable math, respond with exactly: NONE

Respond with ONLY the Python code (no markdown fences, no explanation), or the word NONE."""

SYMPY_USER_TEMPLATE = """Write SymPy verification code for these math steps.

NARRATION CONTEXT:
{narration}

MATH STEPS:
{steps_text}

Write the verification code, or respond with NONE if there's no verifiable math."""

MANIM_USER_NOTE = (
    "The user prompt below is the Manim code-generation prompt exactly as "
    "generate_math_animation.py would send it. The system prompt is the "
    "contents of templates/manim_system_prompt.md."
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

# The renderer's stdout MUST be pure JSON. Some imported helpers print
# progress, which would corrupt the JSON. main() redirects sys.stdout → stderr
# so those strays land on stderr; the JSON is written to this saved real
# stdout instead.
_REAL_STDOUT = sys.stdout


def emit(system: str, user: str, notes: str = "", pretty: bool = False) -> None:
    payload = {"system": system, "user": user, "notes": notes}
    if pretty:
        json.dump(payload, _REAL_STDOUT, indent=2, ensure_ascii=False)
    else:
        json.dump(payload, _REAL_STDOUT, ensure_ascii=False)
    _REAL_STDOUT.write("\n")


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def chunk_text(text: str, max_chars: int = 25000) -> list[str]:
    """Same chunker that clean_transcript.py uses."""
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        if current_len + len(word) + 1 > max_chars and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(word)
        current_len += len(word) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def extract_full_text(transcript_json: dict) -> str:
    """Same extractor that clean_transcript.py uses."""
    segments = transcript_json.get("segments", [])
    if segments:
        return " ".join(seg.get("text", "").strip() for seg in segments)
    return transcript_json.get("text", "")


def video_num_from_dir(video_dir: Path) -> int:
    m = re.match(r"Video-(\d+)$", video_dir.name)
    if not m:
        raise SystemExit(f"Expected a Video-N directory, got {video_dir}")
    return int(m.group(1))


# -----------------------------------------------------------------------------
# Step handlers
# -----------------------------------------------------------------------------


def step_clean(args: argparse.Namespace) -> None:
    path = Path(args.transcript)
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        text = extract_full_text(data)
    else:
        text = read_text(path)

    chunks = chunk_text(text, max_chars=25000) if len(text) >= 30000 else [text]
    notes = (
        f"chunks_total={len(chunks)}; "
        f"chunk_index={args.chunk_index}; "
        "concatenate subagent outputs with '\\n\\n' if chunks_total > 1. "
        "Save final result to <pipeline>/content_cleaned.txt."
    )
    if args.chunk_index >= len(chunks):
        raise SystemExit(f"chunk_index {args.chunk_index} out of range (have {len(chunks)})")
    user = CLEAN_PIPELINE_PROMPT.format(transcript=chunks[args.chunk_index])
    emit(CLEAN_PIPELINE_SYSTEM, user, notes, pretty=args.pretty)


def step_segment(args: argparse.Namespace) -> None:
    content = read_text(Path(args.content))
    user = SEGMENTATION_PROMPT.format(content=content)
    notes = (
        "Output is a single JSON object with start_anchor per video (NOT full content). "
        "Save the subagent's raw response to a file, then materialize deterministically: "
        "`python scripts/segment_concepts.py <pipeline_dir> --apply <response.json>` — it "
        "slices content_cleaned.txt at the anchors and writes segments.json + each "
        "Video-N/content.txt (filling missing duration_estimate from slice word count). "
        "On a non-zero exit (anchor unmatched/out of order), re-prompt with the error appended."
    )
    emit(SEGMENT_SYSTEM, user, notes, pretty=args.pretty)


def step_script(args: argparse.Namespace) -> None:
    """Script generation works directly from content.txt + segments.json
    metadata — the brief step was merged into it (see generate_scripts.py)."""
    video_dir = Path(args.video_dir)
    pipeline_dir = video_dir.parent
    video_num = video_num_from_dir(video_dir)

    segments = load_segments(pipeline_dir)
    segment_meta, content = load_video_source(video_dir, segments)
    if not content:
        raise SystemExit(f"No content found for {video_dir}")

    mode = args.mode
    source_block = build_source_block(segment_meta, content)
    style_guide = load_style_guide() or ""
    duration_hint, frame_count_hint = compute_duration_and_frame_hints(
        segment_meta.get("duration_estimate", ""), content)

    if mode == "math":
        template = MATH_SCRIPT_GENERATION_PROMPT
    else:
        template = TECHNICAL_SCRIPT_GENERATION_PROMPT

    user = template.format(
        source_block=source_block,
        style_guide=style_guide,
        planning_block=PLANNING_BLOCK,
        duration_hint=duration_hint,
        frame_count_hint=frame_count_hint,
    )

    system = SCRIPT_SYSTEMS[mode]
    notes = (
        f"mode={mode}. Response is a bare JSON object (no markdown fences). "
        f"Save verbatim to {video_dir}/script.json. "
        "Then regenerate script.md for human review via: "
        f"python -c \"from scripts.utils.script_parser import load_script, save_script; "
        f"sd = load_script('{video_dir}'); save_script(sd, '{video_dir}', write_json=False, write_md=True)\""
    )
    emit(system, user, notes, pretty=args.pretty)


def step_verify_math(args: argparse.Namespace) -> None:
    video_dir = Path(args.video_dir)
    script_data = load_script(video_dir)
    frame = script_data.get_frame(args.frame)
    if frame is None:
        raise SystemExit(f"Frame {args.frame} not found in {video_dir}/script.json")

    narration = frame.narration
    if args.sympy_error:
        narration = (
            f"{narration}\n\nIMPORTANT CORRECTION: A computational check found an error "
            f"in the previous verification attempt: {args.sympy_error}\n"
            "Please re-verify all calculations carefully and correct any mistakes."
        )

    prior_context = ""
    if args.prior_context:
        prior_context = read_text(Path(args.prior_context))

    prior_context_section = ""
    if prior_context:
        prior_context_section = (
            f"\nPRIOR MATHEMATICAL CONTEXT (reference only -- do NOT re-verify):\n"
            f"{prior_context}\n"
        )

    # The script declares each frame's class; a `visual` frame skips verification
    # entirely (minimal {"frame_type": "visual"}), so surface that in the prompt
    # rather than leaving it buried in `notes`. `frame_class` is not carried on the
    # parsed Frame, so read it from the raw script.json.
    frame_class = ""
    try:
        _raw = json.loads((video_dir / "script.json").read_text())
        for _f in _raw.get("frames", []):
            if _f.get("number") == args.frame:
                frame_class = (_f.get("frame_class") or "").strip().lower()
                break
    except (OSError, ValueError):
        pass
    if frame_class and frame_class != "math":
        narration = (
            f"NOTE: script.json declares this frame's frame_class as \"{frame_class}\", "
            f"NOT \"math\". A non-math frame is NOT verified and gets no math_steps and no "
            f"natural_narration — emit exactly {{\"frame_type\": \"visual\"}} for it and stop. "
            f"Only override this if the narration below genuinely works a calculation.\n\n"
            f"{narration}"
        )

    user = VERIFY_MATH_USER_TEMPLATE.format(
        frame_number=args.frame,
        narration=narration,
        visual_context=frame.visual.reference if frame.visual else "",
        prior_context_section=prior_context_section,
    )
    notes = (
        "Subagent MUST run with adaptive thinking in mind — respond with ONLY valid JSON. "
        "The subagent runs SymPy ITSELF to confirm every step and the final answer (write a "
        "temp .py and execute it with venv/bin/python); that execution IS the verification — "
        "there is no separate sympy_gen step and no sympy_verified gate. "
        f"Store the result for this frame under 'frames.{args.frame}' in "
        f"{video_dir}/math_verification.json, whose shape is: top level "
        '{"success": true, "video_title": str, "requires_math": true, "frames": {...}} ; '
        'a math frame is {"frame_type": "math", "math_steps": [...], "final_answer": str, '
        '"natural_narration": <TTS-safe spoken text>, "math_context": str, '
        '"verification_status": "correct"|"corrected", "issues_found": [...], '
        '"confidence": str} ; a visual frame is the minimal {"frame_type": "visual"}. '
        "NOTE generate_tts_elevenlabs.py speaks `natural_narration` for any math frame whose "
        "verification_status is correct/corrected — so that field, not script.json, is what "
        "the viewer hears. Once ALL frames are verified, run the "
        "`color_plan` step to add the video-wide semantic color plan (top-level key)."
    )
    emit(VERIFY_MATH_SYSTEM, user, notes, pretty=args.pretty)


def step_color_plan(args: argparse.Namespace) -> None:
    """Video-level semantic color plan prompt (math/technical).

    Run AFTER every frame's verification entry is written — the plan is built
    from the final math/code steps so its tex forms match the animation exactly.
    """
    video_dir = Path(args.video_dir)
    mv_path = video_dir / "math_verification.json"
    if not mv_path.exists():
        raise SystemExit(f"{mv_path} missing — write the verify_math results first")
    mv = json.loads(mv_path.read_text(encoding="utf-8"))
    user = build_color_plan_user(mv.get("video_title", video_dir.parent.name),
                                 mv.get("frames", {}))
    if user is None:
        raise SystemExit(
            "No frame carries math/code steps — nothing to plan (skip this step).")
    notes = (
        "Math/technical only. Subagent responds with ONLY the JSON plan "
        "({name: {color, tex, note_words}}, or {} if nothing recurs). Insert the result "
        f"as the TOP-LEVEL `color_plan` key of {mv_path} (preserve everything else). "
        "Every subsequent manim-step prompt injects it as the mandatory VIDEO COLOR PLAN "
        "block; after authoring/rendering frames, lint with: venv/bin/python -c "
        "\"from scripts.generate_math_animation import check_color_links; "
        f"check_color_links('{video_dir}')\"."
    )
    emit(COLOR_PLAN_SYSTEM, user, notes, pretty=args.pretty)


def step_sympy_gen(args: argparse.Namespace) -> None:
    video_dir = Path(args.video_dir)
    mv_path = video_dir / "math_verification.json"
    if not mv_path.exists():
        raise SystemExit(f"{mv_path} does not exist yet")
    mv = json.loads(mv_path.read_text(encoding="utf-8"))
    entry = mv.get("frames", {}).get(str(args.frame))
    if not entry:
        raise SystemExit(f"No frame {args.frame} in math_verification.json")

    math_steps = entry.get("math_steps", [])
    narration = entry.get("natural_narration") or entry.get("original_narration", "")

    steps_text = ""
    for step in math_steps:
        steps_text += (
            f"Step {step.get('step', '?')}: {step.get('expression', '')}\n"
            f"  Operation: {step.get('operation', '')}\n"
            f"  Note: {step.get('note', '')}\n\n"
        )

    user = SYMPY_USER_TEMPLATE.format(
        narration=narration[:2000],
        steps_text=steps_text,
    )
    notes = (
        "Subagent responds with ONLY Python code (no fences) OR the word NONE. "
        "If NONE, set sympy_verified=null and move on. "
        "Otherwise, write the code to a temp file, run with `python <tmp>.py`, "
        "capture stdout/stderr. Timeout 30s. "
        "If it passes: set sympy_verified=true. If it fails: retry verify_math with --sympy-error."
    )
    emit(SYMPY_SYSTEM, user, notes, pretty=args.pretty)


def step_manim(args: argparse.Namespace) -> None:
    video_dir = Path(args.video_dir)
    script_data = load_script(video_dir)
    audio_dir = str(video_dir / "audio")

    # Load math_verification.json (math/technical).
    mv_path = video_dir / "math_verification.json"
    mv: dict = {}
    if mv_path.exists():
        mv = json.loads(mv_path.read_text(encoding="utf-8"))

    # --- Manifest mode (no --frame): list animatable frames + which to author ---
    if args.frame is None:
        math_data = mv or load_math_verification(str(video_dir)) or {}
        qualifying, info = select_frames_for_animation(
            str(video_dir), math_data, script_data, all_frames=True)
        manifest = [{
            "frame": fn,
            "frame_type": info[fn].get("frame_type", "math"),
            "duration": round(info[fn].get("duration", 0), 2),
            "math_steps": info[fn].get("math_steps", 0),
            "py_exists": os.path.exists(video_dir / "frames" / f"frame_{fn}_manim.py"),
            "needs_authoring": True,
        } for fn in qualifying]
        payload = {
            "video_dir": str(video_dir),
            "frames": manifest,
            "notes": (
                "Author each frame: call this step with --frame N for its exact prompt, "
                "write the code to frames/frame_N_manim.py, then render with "
                "render_manim_scene() (see CLAUDE.md 'Fixing Frames'). Or resume the "
                "pipeline --from animate, which reuses existing frame_N_manim.py and "
                "only renders (no codegen)."
            ),
        }
        _REAL_STDOUT.write(json.dumps(payload, indent=2 if args.pretty else None,
                                      ensure_ascii=False) + "\n")
        return

    frame_num = args.frame
    frame_info = mv.get("frames", {}).get(str(frame_num), {})

    frame_type = frame_info.get("frame_type", "math")
    narration = (
        frame_info.get("natural_narration")
        or frame_info.get("original_narration")
        or ""
    )
    math_steps = frame_info.get("math_steps", [])

    script_frame = script_data.get_frame(frame_num)
    visual_desc = frame_info.get("original_narration", narration)
    if script_frame and script_frame.visual and script_frame.visual.reference:
        visual_desc = script_frame.visual.reference
    if frame_type == "visual" and not frame_info.get("natural_narration") and script_frame:
        narration = script_frame.narration

    # Duration: auto-derive from audio when --duration omitted.
    if args.duration is not None:
        duration = args.duration
    else:
        audio_path = os.path.join(audio_dir, f"frame_{frame_num}.mp3")
        if not os.path.exists(audio_path):
            raise SystemExit(
                f"Missing {audio_path}; pass --duration or run the tts step first.")
        duration = get_audio_duration_ffprobe(audio_path)

    # Word transcript: explicit file, else stored ElevenLabs timestamps (post-tts).
    if args.word_transcript:
        word_transcript = read_text(Path(args.word_transcript))
    else:
        stored_words = load_stored_word_timestamps(audio_dir, frame_num)
        if stored_words is not None:
            word_transcript = format_word_transcript(
                align_words_to_script(narration, stored_words))
        else:
            word_transcript = ("(word timestamps unavailable — run the tts step first; "
                               "the real codegen would Scribe-transcribe here)")

    prior_context = ""
    if args.prior_context:
        prior_context = read_text(Path(args.prior_context))

    user = build_manim_user_prompt(
        narration=narration,
        math_steps=math_steps,
        visual_desc=visual_desc,
        total_duration=duration,
        frame_number=frame_num,
        word_transcript=word_transcript,
        prior_context=prior_context,
        code_steps=frame_info.get("code_steps") if frame_type == "code" else None,
        color_plan=mv.get("color_plan"),
    )
    if frame_type == "code":
        prompt_mode = "technical"  # only technical-mode frames carry code_steps
    else:
        prompt_mode = "math"
    system = load_manim_system_prompt(str(video_dir), mode=prompt_mode)
    notes = (
        MANIM_USER_NOTE
        + " Subagent responds with Python code only. Save verbatim to "
        f"{video_dir}/frames/frame_{frame_num}_manim.py. Then render with "
        "`python -m manim render -r 1920,1080 --fps 30 --format mp4 <file> MathAnimation` "
        "(see render_manim_scene() in scripts/generate_math_animation.py for full flags)."
    )
    emit(system, user, notes, pretty=args.pretty)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="step", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")

    sp = sub.add_parser("clean", help="clean_transcript.py prompt")
    sp.add_argument("--transcript", required=True, help="Path to transcript.json or plain-text file")
    sp.add_argument("--chunk-index", type=int, default=0)
    add_common(sp)
    sp.set_defaults(func=step_clean)

    sp = sub.add_parser("segment", help="segment_concepts.py prompt")
    sp.add_argument("--content", required=True, help="Path to content_cleaned.txt")
    add_common(sp)
    sp.set_defaults(func=step_segment)

    sp = sub.add_parser("script", help="generate_scripts.py prompt for one video")
    sp.add_argument("--video-dir", required=True)
    sp.add_argument("--mode", choices=["math", "technical"], required=True)
    add_common(sp)
    sp.set_defaults(func=step_script)

    sp = sub.add_parser("verify_math", help="verify_math prompt for one frame")
    sp.add_argument("--video-dir", required=True)
    sp.add_argument("--frame", type=int, required=True)
    sp.add_argument("--prior-context", default="", help="File with prior math context (from earlier frames)")
    sp.add_argument("--sympy-error", default="", help="Include only on retry after SymPy failure")
    add_common(sp)
    sp.set_defaults(func=step_verify_math)

    sp = sub.add_parser("color_plan", help="Video-level semantic color plan prompt "
                        "(math/technical; run after all frames are verified)")
    sp.add_argument("--video-dir", required=True)
    add_common(sp)
    sp.set_defaults(func=step_color_plan)

    sp = sub.add_parser("sympy_gen", help="SymPy verification code-gen prompt for one frame")
    sp.add_argument("--video-dir", required=True)
    sp.add_argument("--frame", type=int, required=True)
    add_common(sp)
    sp.set_defaults(func=step_sympy_gen)

    sp = sub.add_parser(
        "manim",
        help="Manim code-gen prompt for one frame (omit --frame for a frame "
             "manifest). --duration/--word-transcript auto-derive from the video "
             "dir after tts if omitted.")
    sp.add_argument("--video-dir", required=True)
    sp.add_argument("--frame", type=int, default=None,
                    help="Frame number; omit to list all animatable frames")
    sp.add_argument("--duration", type=float, default=None,
                    help="Audio duration (s); auto-derived from audio if omitted")
    sp.add_argument("--word-transcript", default=None,
                    help="File with formatted word-level transcript; auto-derived "
                         "from stored ElevenLabs timestamps if omitted")
    sp.add_argument("--prior-context", default="")
    add_common(sp)
    sp.set_defaults(func=step_manim)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    # Send any stray prints from imported helpers to stderr; JSON goes to
    # _REAL_STDOUT (kept above). One-shot CLI — no need to restore.
    sys.stdout = sys.stderr
    args.func(args)


if __name__ == "__main__":
    main()
