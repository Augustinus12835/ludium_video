#!/usr/bin/env python3
"""
Verification prompt constants for the math/technical pipeline.

These are the verify_math / color_plan prompts that
scripts/render_step_prompt.py serves to Claude Code subagents. There is no
API client here — subagents read these prompts and author the results
themselves.

Since 2026-08-30 verify_math verifies + extracts on-screen steps ONLY. The
spoken text is script.json's narration for every frame class (already
TTS-safe — tts_rules.py + the pre-TTS narration_check gate) and it is voiced
verbatim. The old `natural_narration` TTS rewrite was retired: measured over
a month of production it fixed nothing the gate detects yet silently mutated
~22% of verified math frames (reworded cue phrases codegen anchors to,
un-hyphenated notation, lower-cased single letters, inserted unreviewed
sentences). Consumers keep reading the field only on legacy files.
"""

VERIFY_MATH_SYSTEM = """You are an expert at verifying educational math content and breaking it down into sequential steps for animated visual build-up. Your task is to:
1. Verify any mathematical calculations in the narration are correct
2. Extract sequential steps that will be animated on screen one by one
3. Use any provided prior mathematical context to understand references to earlier results,
   but do NOT re-verify those prior results. Only verify the CURRENT frame's new calculations.

The content may be purely mathematical (derivations, calculations) or procedural (step-by-step methods, timelines, decision flows, problem setups). Both types need clear sequential steps.

You do NOT rewrite the narration. The narration you receive is the reviewed, TTS-safe script text and it is spoken to the viewer VERBATIM — there is no spoken-text output from this step. If a calculation error means the spoken words themselves must change, put the exact wrong→right wording in issues_found; the fix is applied to script.json narration upstream (and flagged), never emitted here.

You must respond with ONLY valid JSON, no other text.

For every frame you receive, perform this TASK:
1. If mathematical calculations are present, verify them step-by-step for correctness. Respect deliberate rounding: a value the narration rounds on purpose ("about one hundred seventy-eight point four") is not an error, and the on-screen steps must show the value the narration speaks — flag a genuinely wrong value, never silently re-derive a rounded one to more digits.

2. Extract sequential steps for animation. Each step represents one thing that appears on screen:
   - For MATH content: use LaTeX notation (e.g., "\\frac{x}{y}", "V = x(10-2x)^2")
   - For PROCEDURES: use a short label (e.g., "Step 1: Picture — sketch and label variables")
   - For TIMELINES/FLOWS: use a label with values (e.g., "Year 0: Invest \\$10M")
   - For GRAPHS: describe what to draw (e.g., "Plot V(x) on [0, 5], peak at x = 5/3")
   - Each step should be one self-contained piece that builds on the previous
   - Steps keep normal written notation (numerals, symbols, subscripts) — they are rendered, not spoken

RESPOND WITH THIS EXACT JSON FORMAT:
{
    "verification_status": "correct" or "corrected" or "unclear",
    "issues_found": ["list of any math errors found, empty if none"],
    "math_steps": [
        {
            "step": 1,
            "expression": "LaTeX expression OR short label for procedural steps",
            "operation": "What is being shown or done in this step",
            "note": "Optional note about this step"
        }
    ],
    "final_answer": "The final result, conclusion, or key takeaway",
    "confidence": "high" or "medium" or "low",
    "math_context_update": "Brief summary of what this frame establishes mathematically, to carry forward to subsequent frames. Include key expressions and results."
}

Important:
- verification_status: use "correct"/"corrected" for math content, "correct" for procedural content with no math to verify
- Steps should be thorough enough for a student to follow the progression visually"""

# Video-level semantic color plan (math/technical). One call per video,
# AFTER per-frame verification, from the accumulated math/code steps —
# so the tex forms it lists are the exact forms the animation steps use.
# The plan is stored top-level in math_verification.json ("color_plan")
# and injected into every frame's Manim codegen prompt.
COLOR_PLAN_SYSTEM = """You design the video-wide SEMANTIC COLOR PLAN for an educational math/technical video rendered with Manim.

Downstream, every frame's animation colors recurring quantities so a student can match a mark on a graph to the symbol in the algebra and to the word in a margin note without reading letters. Your plan is what keeps those colors CONSISTENT across the whole video: one quantity = one color, everywhere, in every frame.

You receive the video title and every frame's extracted steps (LaTeX expressions, operations, notes). Pick the quantities that deserve a persistent color and assign one each.

Selection rules:
- Pick 2-6 quantities. Prefer ones that (a) recur across several frames, (b) get DRAWN on a graph/diagram (a curve, a triangle leg, a vector, a region), (c) are NAMED in notes/operations ("the base", "the height", "momentum"), or (d) form a confusable pair that needs separating (x vs y, v vs a). A quantity that appears once, in one step, with nothing drawn and no note naming it, does not need a plan entry.
- Colors come from exactly this palette (Manim constant names): BLUE, ORANGE, TEAL, PURPLE, PINK, RED_C, GREEN — in roughly that order of preference. GREEN doubles as the final-answer accent and RED_C as the error/warning accent, so reach for them last (or when the meaning matches — RED_C for a loss/deficit, GREEN for a result).
- One quantity per color. Never WHITE or YELLOW (reserved for default step/note text).
- "tex" lists the EXACT LaTeX forms of the quantity as they appear in the provided expressions — copy them verbatim (e.g. "\\vec{F}", "F_{net}", "|B \\times C|"), including every variant form the steps actually use. These are substring keys for tex_to_color_map, so each must be a free-standing symbol, not a fragment.
- A key is only usable where it sits OUTSIDE brace groups and \\left...\\right pairs: a tex_to_color_map key inside \\frac{}{}, \\sqrt{}, \\int_{}^{}, ^{} or _{} splits the LaTeX into an unbalanced fragment and silently kills the render, so frame authors are required to leave those occurrences uncolored. If a quantity occurs ONLY brace-nested (e.g. dx/dt appearing exclusively inside a \\sqrt{}), do not plan a color for it — it cannot be linked anywhere, and planning it only produces check_color_links warnings no author can fix. Prefer quantities that appear free-standing in at least one step.
- "note_words" lists the plain-English words/short phrases the notes and operations use to name it (e.g. "force", "net force"). Lowercase, 1-3 words each; prefer multi-word phrases over bare single letters (single letters match inside other words).

Respond with ONLY valid JSON, no other text:
{
    "<short-quantity-name>": {
        "color": "ORANGE",
        "tex": ["\\\\vec{F}", "F_{net}"],
        "note_words": ["force", "net force"]
    }
}

If genuinely nothing recurs or links (rare), respond with exactly {}."""

def build_color_plan_user(video_title: str, frames: dict) -> str | None:
    """User prompt for the color-plan call (shared with render_step_prompt.py so
    the manual-mode subagent gets the byte-identical prompt).

    frames: math_verification.json "frames" dict. Returns None when no frame
    carries math/code steps (nothing to plan).
    """
    lines = []
    for fnum in sorted(frames, key=lambda k: int(k)):
        entry = frames[fnum] or {}
        steps = entry.get("math_steps") or entry.get("code_steps") or []
        if not steps:
            continue
        lines.append(f"Frame {fnum} ({entry.get('frame_type', 'math')}):")
        for s in steps:
            line = (f"  step {s.get('step', '?')}: {s.get('expression', '')}"
                    f" — {s.get('operation', '')}")
            if s.get("note"):
                line += f" (note: {s['note']})"
            lines.append(line)
    if not lines:
        return None
    return (f"VIDEO TITLE: {video_title}\n\nFRAME STEPS:\n" + "\n".join(lines)
            + "\n\nDesign the color plan.")
