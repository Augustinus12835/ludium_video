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
    color_scheme          --pipeline-dir DIR (LECTURE-level semantic colour scheme, ONCE in
                          Phase A after segment and BEFORE scripting; result → color_scheme.json.
                          Injected as background into every script prompt and inherited by
                          every video's color_plan)
    color_plan            --video-dir DIR   (video-level semantic color plan, AFTER all
                          frames are verified; result goes top-level in math_verification.json.
                          Inherits the lecture colour scheme when one is staged)
    manim                 --video-dir DIR --frame N --duration SEC --word-transcript FILE
                          [--prior-context FILE]
                          (omit --frame for a manifest of animatable frames;
                           --duration/--word-transcript auto-derive after tts)

All STEP subcommands also accept --pretty to indent the JSON for inspection, and
--system-only / --user-only to split the output: the system half of a per-frame
prompt (manim, verify_math) is identical for every frame of a video, so agents render
it ONCE with --system-only and ask for the frame-specific {user, notes} with --user-only.
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
        "aloud. The visual field is animation guidance only. Be concise. "
        "Output ONLY valid JSON."
    ),
    "technical": (
        "You are an expert educational script writer for technical subjects. "
        "Create clear, precise narration that follows the teaching flow. "
        "The narration must be COMPLETE — every concept and calculation spoken "
        "aloud. The visual field is animation guidance for Manim. Be concise. "
        "Output ONLY valid JSON."
    ),
}

VERIFY_MATH_USER_TEMPLATE = """Analyze this educational content and extract its sequential structure for animation, following the TASK in your instructions.

FRAME NUMBER: {frame_number}

ORIGINAL NARRATION:
{narration}

VISUAL CONTEXT:
{visual_context}
{prior_context_section}"""

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


# Output-shape control, set by main() from --user-only / --system-only. The system
# half of a per-frame codegen prompt is identical for every frame of a video (~90 KB
# for Manim), so agents render it ONCE with --system-only and then request only the
# frame-specific half with --user-only.
_EMIT_OPTS = {"user_only": False, "system_only": False}


def emit(system: str, user: str, notes: str = "", pretty: bool = False) -> None:
    if _EMIT_OPTS["system_only"]:
        payload = {"system": system}
    elif _EMIT_OPTS["user_only"]:
        payload = {"user": user, "notes": notes}
    else:
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
# Reference lecture notes (optional grounding for recorded lectures)
# -----------------------------------------------------------------------------
# Drop the lecturer's own notes / handout for the lecture, as Markdown, at
# pipeline/<L>/source_lecture_notes.md BEFORE the clean step and they are
# injected automatically into the clean and script prompts — no flag. An ASR
# transcript of a chalkboard lecture never sees the board, so without them the
# agents RECONSTRUCT the equations and worked examples, inventing values at
# every ambiguity.

REFERENCE_NOTES_FILENAME = "source_lecture_notes.md"

REFERENCE_NOTES_CLEAN_BLOCK = """

---

OFFICIAL LECTURE NOTES FOR THIS LECTURE (the lecturer's own notes — GROUND TRUTH):

{notes}

---

HOW TO USE THE OFFICIAL NOTES ABOVE. The transcript is machine speech-to-text of a
chalkboard lecture: it never sees the board, so equations arrive spoken-only ("x squared
over two") or garbled, numbers and symbols are mangled, and worked examples lose their
exact values. The notes fix that:
1. Reconstruct every definition, theorem statement, formula, derivation step and worked
   example EXACTLY as the notes write it — values, signs, limits of integration, variable
   names and notation. Write the math as LaTeX.
2. Resolve any transcript ambiguity or self-contradiction in favour of the notes.
3. Recover a formula or example the lecturer wrote on the board but never fully spoke.
4. Do NOT import material the lecture does not teach: the notes may cover more (or a
   different order) than this lecture — keep the lecturer's own order, examples and emphasis,
   and add nothing from the notes that the lecture never reaches.
5. If the lecture and the notes genuinely disagree on a value, keep the lecturer's value and
   flag it with a bracketed note (e.g. "[notes give 3/2]").
"""

REFERENCE_NOTES_SCRIPT_BLOCK = """

---

OFFICIAL LECTURE NOTES (the lecturer's own notes for this lecture — GROUND TRUTH for
notation, formulas and worked-example values). The SOURCE CONTENT above was cleaned against
these notes; use them to (a) put formulas on screen in the notes' exact form and notation,
(b) check every stated value, sign and limit before it is narrated, and (c) resolve any
ambiguity in the source content. Do NOT add material beyond what the SOURCE CONTENT for
this video covers — the notes may span more than this video.

{notes}
"""


def load_reference_notes(pipeline_dir: Path) -> str:
    """Return the staged reference notes for a lecture, or '' when none exist."""
    path = Path(pipeline_dir) / REFERENCE_NOTES_FILENAME
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


COLOR_SCHEME_FILENAME = "color_scheme.json"

COLOR_SCHEME_SCRIPT_BLOCK = """

---

LECTURE COLOUR SCHEME (background — already decided for the whole lecture; you do not
choose colours and you do not need to restate them):

{scheme}

This is context, not a task. Every video in this lecture paints these quantities in these
colours, and the frame-authoring agent applies them automatically from the plan — so:
- Write `visual` descriptions in terms of the QUANTITY ("the slope", "the step size"),
  not a colour word. The colour follows the quantity by itself.
- A boxed final result and a mark on an error use the lecture ACCENTS listed above — say
  "box the result" or "mark the wrong form", not a colour.
- Name a colour only where the frame's meaning genuinely depends on one ("the two curves
  must be visually distinct") — and then use a colour this scheme has not already
  committed to another quantity.
- If your video introduces a recurring quantity the scheme does not list, describe it and
  leave the colour unstated; the plan step will assign one.
"""

COLOR_SCHEME_PLAN_BLOCK = """

---

LECTURE COLOUR SCHEME — ALREADY DECIDED, INHERIT IT. This lecture's scheme was authored
once for all its videos and is binding:

{scheme}

Your job is NOT to invent a plan. Copy every scheme entry whose quantity actually appears
in THIS video's steps, keeping its colour EXACTLY as given, and extending its `tex` list
with any additional LaTeX forms this video happens to use. Carry each copied entry's
`scope` into your entry as well, cut to the sentences that apply to THIS video: its keying
cautions (a tex form that is a substring of another quantity's, a letter that also matches
inside a macro such as `c` in `\cos`) reach the frame authors only through your plan.
Then, and only then, add plan
entries for quantities that are genuinely local to this video, choosing colours the scheme
has not already used. Never re-assign a scheme colour to a different quantity, and never
give a scheme quantity a different colour.
"""


def _read_scheme_file(path: Path) -> dict:
    """The raw scheme mapping, reserved `_` keys included; {} when absent or unreadable."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Tolerate a wrapper: {"color_scheme": {...}, "_note": "..."}.
    return data.get("color_scheme") if isinstance(data.get("color_scheme"), dict) else data


# Accent colours: the final-answer box and the error/warning mark. They are FIXED at
# GREEN and RED_C and do NOT move when the scheme gives a quantity the same colour —
# a box is a box, a strike is a strike, and sharing a colour with a quantity costs the
# viewer nothing. What did cost something was leaving it unsaid: one lecture's scheme
# spent GREEN on a recurring constant, each of its five producers concluded the box
# therefore needed a colour of its own, and the lecture shipped two different golds
# (#F0AC5F in three videos, #EAB308 in two). A lecture that genuinely wants other
# accents says so in a top-level `_accents` entry in color_scheme.json.
ACCENT_DEFAULTS = {"final_answer": "GREEN", "warning": "RED_C"}
_ACCENT_LABELS = {"final_answer": "final-answer box", "warning": "error/warning mark"}


def resolve_color_accents(explicit=None) -> dict:
    """The lecture's accents: the fixed defaults unless `_accents` overrides a role."""
    explicit = explicit if isinstance(explicit, dict) else {}
    return {role: explicit.get(role) or default
            for role, default in ACCENT_DEFAULTS.items()}


def load_color_accents(pipeline_dir: Path) -> dict:
    """Accents for the staged lecture scheme, or {} when none is staged."""
    path = Path(pipeline_dir) / COLOR_SCHEME_FILENAME
    inner = _read_scheme_file(path)
    quantities = {k: v for k, v in inner.items()
                  if not k.startswith("_") and isinstance(v, dict)}
    if not quantities:
        return {}
    accents = resolve_color_accents(inner.get("_accents"))
    _warn_bad_accents(accents, path)
    return accents


def _warn_bad_accents(accents: dict, path: Path) -> None:
    """Warn when an `_accents` override is not a colour name Manim will resolve.

    An accent that duplicates a quantity's colour is NOT a warning: the accent is
    fixed on purpose, and a boxed result sharing green with a green quantity reads
    fine — the box, not the colour, is what marks it.
    """
    try:
        import manim  # noqa: PLC0415 — optional at prompt-render time
    except Exception:
        return
    for role, color in accents.items():
        if not isinstance(color, str) or not hasattr(manim, color):
            print(f"  [color_scheme] WARNING: accent {role} → {color!r} is not a Manim "
                  f"colour constant; the render will fail ({path})", file=sys.stderr)


def _codegen_accents(video_dir: Path) -> dict | None:
    """The lecture's accents when it overrides them, else None.

    None means the system prompt's own defaults stand (green box, red error mark),
    which is the case for every lecture that does not set `_accents`.
    """
    accents = load_color_accents(video_dir.parent)
    return accents if accents and accents != ACCENT_DEFAULTS else None


def load_color_scheme(pipeline_dir: Path) -> dict:
    """Return the lecture-level colour scheme dict, or {} when none is staged."""
    path = Path(pipeline_dir) / COLOR_SCHEME_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Tolerate a wrapper: {"color_scheme": {...}, "_note": "..."}.
    scheme = {k: v for k, v in _read_scheme_file(path).items() if not k.startswith("_")}
    if scheme:
        _warn_bad_scheme_colors(scheme, path)
    return scheme


def _warn_bad_scheme_colors(scheme: dict, path: Path) -> None:
    """Warn on stderr about colour names Manim will not resolve, and on reuse.

    A typo'd constant does not fail here — it fails deep in a 4K render, long
    after the scheme has been injected into every script prompt in the lecture.
    Checked against the real Manim namespace, so any genuine constant passes.
    """
    try:
        import manim  # noqa: PLC0415 — optional at prompt-render time
    except Exception:
        return
    seen: dict = {}
    for name, spec in scheme.items():
        color = (spec or {}).get("color")
        if not color:
            print(f"  [color_scheme] WARNING: {name!r} has no `color` ({path})", file=sys.stderr)
            continue
        if not isinstance(color, str) or not hasattr(manim, color):
            print(f"  [color_scheme] WARNING: {name!r} → {color!r} is not a Manim colour "
                  f"constant; the render will fail ({path})", file=sys.stderr)
        elif color in ("WHITE", "YELLOW"):
            print(f"  [color_scheme] WARNING: {name!r} → {color} is reserved for default "
                  f"step/note text ({path})", file=sys.stderr)
        if color in seen:
            print(f"  [color_scheme] WARNING: {color} assigned to both {seen[color]!r} and "
                  f"{name!r} — one colour must mean one quantity ({path})", file=sys.stderr)
        else:
            seen[color] = name


def format_color_scheme(scheme: dict, accents: dict | None = None) -> str:
    """Render the scheme as the human-readable lines both prompt blocks embed.

    `scope` goes in verbatim because scheme authors put the keying cautions there
    (T inside T_e, a bare c matching inside \cos); dropping it left those cautions
    reaching no agent at all.
    """
    lines = []
    for name, spec in scheme.items():
        spec = spec or {}
        tex = ", ".join(f"`{t}`" for t in spec.get("tex", []))
        words = ", ".join(f'"{w}"' for w in spec.get("note_words", []))
        lines.append(f"- {name} → {spec.get('color', '?')} — tex forms: {tex or '(none)'};"
                     f" note words: {words or '(none)'}")
        if spec.get("scope"):
            lines.append(f"  scope: {spec['scope']}")
    if accents:
        lines.append("ACCENTS (not quantities; the same in every video): "
                     + "; ".join(f"{_ACCENT_LABELS[role]} → {accents[role]}"
                                 for role in ACCENT_DEFAULTS if role in accents))
    return "\n".join(lines)



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
    # Reference notes, when staged: inserted between the transcript and the
    # output cue so every chunk's cleaner sees the same ground truth.
    ref = load_reference_notes(path.parent)
    if ref:
        block = REFERENCE_NOTES_CLEAN_BLOCK.format(notes=ref)
        cue = "\n---\n\nEDUCATIONAL CONTENT:"
        user = user.replace(cue, block + cue, 1) if cue in user else user + block
        notes += (f" reference_notes={path.parent / REFERENCE_NOTES_FILENAME} injected "
                  f"({len(ref.split()):,} words) — ground every equation/example in them.")
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
    # Reference notes, when staged (see load_reference_notes above).
    ref = load_reference_notes(pipeline_dir)
    if ref:
        user += REFERENCE_NOTES_SCRIPT_BLOCK.format(notes=ref)
    # Lecture-level colour scheme, when staged (Phase A `color_scheme` step).
    # Background only — the script author writes in quantities, not colour words.
    scheme = load_color_scheme(pipeline_dir)
    if scheme:
        user += COLOR_SCHEME_SCRIPT_BLOCK.format(
            scheme=format_color_scheme(scheme, load_color_accents(pipeline_dir)))

    system = SCRIPT_SYSTEMS[mode]
    notes = (
        f"mode={mode}. Response is a bare JSON object (no markdown fences). "
        f"Save verbatim to {video_dir}/script.json. "
        "Then regenerate script.md for human review via: "
        f"python -c \"from scripts.utils.script_parser import load_script, save_script; "
        f"sd = load_script('{video_dir}'); save_script(sd, '{video_dir}', write_json=False, write_md=True)\""
    )
    if ref:
        notes += (f" reference_notes={pipeline_dir / REFERENCE_NOTES_FILENAME} injected "
                  "(official notes — on-screen formulas must match their form).")
    if scheme:
        notes += (f" color_scheme={pipeline_dir / COLOR_SCHEME_FILENAME} injected as BACKGROUND "
                  f"({len(scheme)} quantities) — write `visual` in quantities, not colour words.")
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

    # The script declares each frame's class, and the class decides the shape of the
    # verification entry. A `visual` frame skips verification entirely (minimal
    # {"frame_type": "visual"}); a `code` frame is NOT verified either, but it still
    # needs a real entry — codegen only passes `code_steps` and selects the technical
    # Code Block system prompt when frame_type == "code" (see step_manim below). A code
    # frame written as {"frame_type": "visual"} therefore silently ships with the
    # wrong layout. Surface both shapes in the prompt rather than leaving them buried
    # in `notes`. `frame_class` is not carried on the parsed Frame, so read it from
    # the raw script.json.
    frame_class = ""
    try:
        _raw = json.loads((video_dir / "script.json").read_text())
        for _f in _raw.get("frames", []):
            if _f.get("number") == args.frame:
                frame_class = (_f.get("frame_class") or "").strip().lower()
                break
    except (OSError, ValueError):
        pass
    if frame_class == "code":
        narration = (
            "NOTE: script.json declares this frame's frame_class as \"code\". A code frame "
            "is NOT verified — it gets no math_steps and no final_answer — but it DOES need "
            "a full entry, not the minimal visual one. "
            "Emit exactly:\n"
            '  {"frame_type": "code",\n'
            '   "code_steps": [{"step": 1, "expression": "<one source line, verbatim>", '
            '"operation": "<what it does / how to reveal it>", '
            '"note": "<optional provenance, e.g. source file + line>", '
            '"highlight_when": "<optional verbatim phrase from the narration below>"}, ...],\n'
            '   "original_narration": "<the narration below, copied VERBATIM>"}\n'
            "and stop. Three requirements, each of which breaks the frame silently if missed: "
            "(1) frame_type MUST be \"code\" — codegen only passes code_steps and only selects "
            "the Code Block layout for \"code\"; (2) the step key is \"operation\", NOT "
            "\"description\"; (3) original_narration is REQUIRED — without it the codegen "
            "prompt's NARRATION block is EMPTY. Each highlight_when must occur exactly once in "
            "the narration. Only override this if the narration below genuinely works a "
            "calculation.\n\n"
            f"{narration}"
        )
    elif frame_class and frame_class != "math":
        narration = (
            f"NOTE: script.json declares this frame's frame_class as \"{frame_class}\", "
            f"NOT \"math\". A non-math, non-code frame is NOT verified and gets no math_steps "
            f"— emit exactly {{\"frame_type\": \"visual\"}} for it and "
            f"stop. Only override this if the narration below genuinely works a calculation.\n\n"
            f"{narration}"
        )

    user = VERIFY_MATH_USER_TEMPLATE.format(
        frame_number=args.frame,
        narration=narration,
        visual_context=frame.visual.reference if frame.visual else "",
        prior_context_section=prior_context_section,
    )
    notes = (
        "Write ONLY valid JSON. "
        "The subagent runs SymPy ITSELF to confirm every step and the final answer (write a "
        "temp .py and execute it with venv/bin/python); that execution IS the verification — "
        "there is no separate sympy_gen step and no sympy_verified gate. "
        f"Store the result for this frame under 'frames.{args.frame}' in "
        f"{video_dir}/math_verification.json, whose shape is: top level "
        '{"success": true, "video_title": str, "requires_math": true, "frames": {...}} ; '
        'a math frame is {"frame_type": "math", "math_steps": [...], "final_answer": str, '
        '"math_context": str, '
        '"verification_status": "correct"|"corrected", "issues_found": [...], '
        '"confidence": str} ; a CODE frame is {"frame_type": "code", "code_steps": '
        '[{"step": int, "expression": <one source line verbatim>, "operation": str, '
        '"note": str (optional), "highlight_when": str (optional, verbatim from the '
        'narration, occurring exactly once)}], "original_narration": <the script narration, '
        'VERBATIM>} — frame_type must be "code" (codegen gates code_steps and the Code Block '
        'layout on it), the step key is "operation" not "description", and '
        'original_narration is required or the codegen NARRATION block is empty ; '
        'a visual frame is the minimal {"frame_type": "visual"}. '
        "SPOKEN TEXT: do NOT write a `natural_narration` field (retired 2026-08-30). TTS, "
        "subtitles and codegen read script.json's narration VERBATIM for every frame class; "
        "it is already TTS-safe and reviewed. If the verification changes what must be SAID "
        "(a wrong value), fix script.json frames[N].narration itself — a spoken-text edit "
        "belongs to the scripting model (route it like a review fix) — and record the "
        "wrong→right in issues_found. Once ALL frames are verified, run the "
        "`color_plan` step to add the video-wide semantic color plan (top-level key)."
    )
    emit(VERIFY_MATH_SYSTEM, user, notes, pretty=args.pretty)


COLOR_SCHEME_SYSTEM = """You design the LECTURE-WIDE semantic colour scheme for a course \
lecture that has been split into several Manim-rendered videos.

Downstream, every frame colours recurring quantities so a student can match a mark on a graph \
to the symbol in the algebra and to the word in a margin note without reading letters. Your \
scheme is what keeps those colours consistent across the WHOLE LECTURE — one quantity = one \
colour, in every video, for hours of viewing. It is decided ONCE, before any script is \
written, and every later stage inherits it.

You receive the lecture title and, per video, its title and the concepts/examples it covers. \
You are choosing from the lecture's SUBJECT MATTER, before any frame exists — so pick the \
quantities the lecture keeps coming back to, not incidental ones.

Selection rules:
- Pick 3-7 quantities that RECUR ACROSS VIDEOS. A quantity confined to one video does not \
belong here — that video's own colour_plan step will handle it. Prefer the lecture's running \
symbols (the ones in its central recurrence, equation, or worked example), quantities that get \
DRAWN (a curve, a strut, an axis, a region), and confusable pairs that must stay separable.
- Colours come from exactly this palette (Manim constant names): BLUE, ORANGE, TEAL, PURPLE, \
PINK, RED_C, GREEN — in roughly that order of preference. GREEN doubles as the final-answer \
accent and RED_C as the error/warning accent, so reach for them last, or when the meaning \
genuinely matches (RED_C for a divergence/warning, GREEN for a result). Spending one is fine: \
a boxed final answer stays the final-answer green and an error mark stays RED_C even when a \
quantity wears that colour — a box is a box, and the collision costs the viewer nothing. A \
lecture that genuinely needs different accents sets a top-level "_accents": {"final_answer": \
"<COLOUR>", "warning": "<COLOUR>"} entry beside the quantities.
- One quantity per colour, and never re-use a colour. Never WHITE or YELLOW (reserved for \
default step and note text). GREEN and RED_C may be spent like any other colour — the accents \
do not move, and a boxed result simply shares its green.
- Beware near-duplicates that differ only by case or a subscript (an error constant C vs a \
constant of integration c): if the lecture uses both, they MUST get different colours, and \
say so in the entry's note_words.
- "tex" lists the EXACT LaTeX forms of the quantity, including every variant the lecture uses \
(e.g. "A_n", "A_0", "A_1"). These become substring keys for tex_to_color_map, so each must be \
a free-standing symbol, not a fragment. A quantity that only ever occurs brace-nested (inside \
\\\\frac{}{}, \\\\sqrt{}, ^{} or _{}) cannot be linked anywhere — do not plan a colour for it.
- "note_words" lists the plain-English words the narration and notes use to name it \
("the slope", "step size"). Lowercase, 1-3 words; prefer phrases over bare single letters.
- "scope" says where it appears (e.g. "videos 1-5, the Euler recurrence") plus any keying \
caution the frame authors need: a tex form that is a substring of another quantity's (T inside \
T_e), a letter that also matches inside a macro (c inside \\cos), a case collision. It is shown to \
every script, plan and codegen agent in the lecture, so keep it to what they must act on.

Respond with ONLY valid JSON, no other text:
{
    "<short-quantity-name>": {
        "color": "TEAL",
        "tex": ["A_n", "A_0"],
        "note_words": ["the slope", "euler slope"],
        "scope": "videos 1-6: the slope each step is taken along"
    }
}"""


def step_color_scheme(args: argparse.Namespace) -> None:
    """Lecture-level semantic colour scheme prompt (math/technical).

    Run ONCE per lecture in Phase A, after segment and BEFORE any script is
    authored — the script agent receives the result as background so its
    `visual` descriptions are written in quantities rather than colour words,
    and every video's colour_plan step inherits it verbatim.
    """
    pipeline_dir = Path(args.pipeline_dir)
    seg_path = pipeline_dir / "segments.json"
    if not seg_path.exists():
        raise SystemExit(f"{seg_path} missing — run the segment step first")
    seg = json.loads(seg_path.read_text(encoding="utf-8"))
    videos = seg.get("videos", [])
    lines = [f"LECTURE: {pipeline_dir.name}", f"{len(videos)} videos:", ""]
    for i, v in enumerate(videos, 1):
        lines.append(f"Video {i}: {v.get('title', '(untitled)')}")
        if v.get("core_concept"):
            lines.append(f"  core: {v['core_concept']}")
        for key in ("key_concepts", "examples", "key_takeaways"):
            for item in (v.get(key) or [])[:4]:
                lines.append(f"  - {item}")
        lines.append("")
    user = ("Design the lecture-wide semantic colour scheme for the lecture below.\n\n"
            + "\n".join(lines))
    out = pipeline_dir / COLOR_SCHEME_FILENAME
    notes = (
        "Math/technical only, ONCE PER LECTURE, in Phase A after segment and BEFORE any "
        "script is authored. Subagent responds with ONLY the JSON scheme "
        "({name: {color, tex, note_words, scope}}). Save it verbatim to "
        f"{out}. From then on `render_step_prompt.py script` injects it as BACKGROUND for "
        "the script author (so `visual` text names quantities, not colours) and "
        "`render_step_prompt.py color_plan` injects it as a BINDING inheritance for every "
        "video's plan."
    )
    emit(COLOR_SCHEME_SYSTEM, user, notes, pretty=args.pretty)


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
    # A lecture-level scheme, when staged, is BINDING: this step inherits it and
    # only extends it, so a quantity keeps one colour across every video.
    scheme = load_color_scheme(video_dir.parent)
    if scheme:
        user += COLOR_SCHEME_PLAN_BLOCK.format(
            scheme=format_color_scheme(scheme, load_color_accents(video_dir.parent)))
    notes = (
        "Math/technical only. Subagent responds with ONLY the JSON plan "
        "({name: {color, tex, note_words, scope?}}, or {} if nothing recurs). Insert the result "
        f"as the TOP-LEVEL `color_plan` key of {mv_path} (preserve everything else). "
        "Every subsequent manim-step prompt injects it as the mandatory VIDEO COLOR PLAN "
        "block; after authoring/rendering frames, lint with: venv/bin/python -c "
        "\"from scripts.generate_math_animation import check_color_links; "
        f"check_color_links('{video_dir}')\"."
    )
    if scheme:
        notes += (f" LECTURE COLOUR SCHEME {video_dir.parent / COLOR_SCHEME_FILENAME} injected "
                  f"({len(scheme)} quantities) — INHERIT those colours verbatim; add only "
                  "video-local quantities, in colours the scheme has not used; carry each "
                  "inherited entry's `scope` (its keying cautions) into the plan, trimmed "
                  "to this video.")
    emit(COLOR_PLAN_SYSTEM, user, notes, pretty=args.pretty)


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
    math_steps = frame_info.get("math_steps", [])
    script_frame = script_data.get_frame(frame_num)

    # Spoken text, in the precedence TTS uses (generate_tts_elevenlabs.get_natural_narration):
    # a LEGACY `natural_narration` (pre-2026-08-30 files only) > a code frame's verbatim
    # `original_narration` > script.json narration — the norm for EVERY frame class now
    # that the verify_math rewrite is retired. Previously a math frame with neither field
    # shipped an EMPTY narration block (plus a word transcript matching nothing).
    narration = (
        frame_info.get("natural_narration")
        or frame_info.get("original_narration")
        or (script_frame.narration if script_frame else "")
    )
    visual_desc = frame_info.get("original_narration", narration)
    if script_frame and script_frame.visual and script_frame.visual.reference:
        visual_desc = script_frame.visual.reference

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
        color_accents=_codegen_accents(video_dir),
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
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--system-only", action="store_true",
                       help="Emit only {system} — identical for every frame of a video; "
                            "render it once")
        g.add_argument("--user-only", action="store_true",
                       help="Emit only {user, notes} — the frame-specific half; pair with "
                            "one --system-only render per video")

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

    sp = sub.add_parser("color_scheme", help="LECTURE-level semantic colour scheme prompt "
                        "(run ONCE in Phase A after segment, before scripting)")
    sp.add_argument("--pipeline-dir", required=True)
    add_common(sp)
    sp.set_defaults(func=step_color_scheme)

    sp = sub.add_parser("color_plan", help="Video-level semantic color plan prompt "
                        "(run after all frames are verified; inherits the lecture "
                        "colour scheme when one is staged)")
    sp.add_argument("--video-dir", required=True)
    add_common(sp)
    sp.set_defaults(func=step_color_plan)

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
    _EMIT_OPTS["user_only"] = bool(getattr(args, "user_only", False))
    _EMIT_OPTS["system_only"] = bool(getattr(args, "system_only", False))
    # Send any stray prints from imported helpers to stderr; JSON goes to
    # _REAL_STDOUT (kept above). One-shot CLI — no need to restore.
    sys.stdout = sys.stderr
    args.func(args)


if __name__ == "__main__":
    main()
