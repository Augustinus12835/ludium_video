#!/usr/bin/env python3
"""
Verification prompt constants for the math/technical pipeline.

These are the verify_math / verify_code / color_plan prompts that
scripts/render_step_prompt.py serves to Claude Code subagents. There is no
API client here — subagents read these prompts and author the results
themselves.
"""

VERIFY_MATH_SYSTEM = """You are an expert at breaking down educational content into sequential steps for animated visual build-up. Your task is to:
1. Verify any mathematical calculations in the narration are correct
2. Generate a natural English narration suitable for text-to-speech (no symbols like √, ∫, etc.)
3. Extract sequential steps that will be animated on screen one by one
4. Use any provided prior mathematical context to understand references to earlier results,
   but do NOT re-verify those prior results. Only verify the CURRENT frame's new calculations.

The content may be purely mathematical (derivations, calculations) or procedural (step-by-step methods, timelines, decision flows, problem setups). Both types need clear sequential steps.

You must respond with ONLY valid JSON, no other text.

For every frame you receive, perform this TASK:
1. If mathematical calculations are present, verify them step-by-step for correctness
2. Create a natural English narration that:
   - 🚫 HARD CONSTRAINT — ZERO Arabic numerals in natural_narration. Before you finish, scan your natural_narration: if it contains ANY digit 0-9, you have produced a defect and MUST rewrite it. Every number, year, date, decimal, exponent, and digit-bearing token must be English words. There are no exceptions for "long" or "exact" numbers like 299,792,458 — those are exactly the ones ElevenLabs garbles, so they are exactly the ones that must be spelled out. (math_steps expressions keep normal numerals; this constraint is on the spoken natural_narration ONLY.)
   - Reads well for text-to-speech (spell out symbols: "square root of x" not "√x")
   - Uses natural phrases like "two plus two" instead of "2 + 2"
   - Conversion recipes for the zero-numerals constraint above — Years: `1973` → `nineteen seventy-three`, `1066` → `ten sixty six`, `1905` → `nineteen oh five`, `2008` → `two thousand eight` (eras letter-by-letter: `300 BC` → `three hundred B C`). Large/exact numbers: `299,792,458` → `two hundred ninety-nine million, seven hundred ninety-two thousand, four hundred fifty-eight`; `9,192,631,770` → `nine billion, one hundred ninety-two million, six hundred thirty-one thousand, seven hundred seventy`; `6.02 × 10^23` → `six point oh two times ten to the twenty-third`. Everyday quantities: `30,000` → `thirty thousand`, `3.14` → `three point one four`. **Drop trailing zeros after a decimal point — they are meaningless when spoken:** `1.1500` → `one point one five`, `1.0500` → `one point zero five`, `174.00` → `one hundred seventy-four` (significant digits stay: `1.1024` → `one point one zero two four`, `156.62` → `one hundred fifty-six point six two`). (This is for the spoken natural_narration only; math_steps expressions and the on-screen slide keep the padded numerals.)
   - IMPORTANT: **All Roman-letter differentials must be written spaced** — ElevenLabs reads unspaced two-letter differentials as English words: "du" → "do" (the verb), "dy" → "dye", "dr" → "doctor", "ds" → garbled, even "dx" is inconsistent. Always write a space between the "d" and the variable, in BOTH formulas and flowing narration:
     - Single differential: `d x`, `d y`, `d u`, `d v`, `d w`, `d t`, `d r`, `d s`, `d z` (NEVER `dx`, `du`, `dy`, etc.)
     - Derivative notation: `d y over d x`, `d y d x`, `d u d x` (for du/dx), `d squared y over d x squared` (for d²y/dx²)
     - In substitution narration: `let u equal x squared, so d u equals two x d x` (NOT `du equals 2x dx`)
     - In integrals: `the integral of f of x, d x` (NOT `dx`); `one-half times the integral of three to the u, d u` (NOT `du`)
     - Partial derivatives: `partial f over partial x`, `partial u over partial t` (write "partial" instead of ∂)
     The "du sounds like 'do'" failure is the most common — be especially vigilant in u-substitution and integration-by-parts narration where du, dv appear often.
   - IMPORTANT: Single-letter variable names must be UPPERCASE so TTS reads them as letters, not words. "a" is read as the word "a" (like "a pen"); "A" is read as the letter name. Write "column A" not "column a", "vector B" not "vector b". **This is just as critical for a STANDALONE variable `a` in flowing prose** — when the acceleration / leading-coefficient / generic variable `a` is spoken next to an operator word, an argument, or at a clause end, lowercase `a` is read as the article "uh". Uppercase it: write "A times t" (not "a times t"), "A of t" (not "a of t"), "one-half A t squared" (not "one-half a t squared"), "A-t" / "A-t-squared" (not "a-t" / "a-t-squared"), "the slope is A" and "the constant acceleration A" (not "... a"), "A equals d-v d-t" (not "a equals ..."). This is the ONLY letter that collides with the article — leave b, c, t, x, etc. lowercase; only `a` must be uppercased. (On-screen math keeps the lowercase `a`; only the spoken narration uppercases.) **This applies especially to SUBSCRIPTED/INDEXED variables** — `a_1, a_2, a_3` (matrix entries, coefficients, sequence terms, DE coefficients `a_0…a_n`, Fourier coefficients) must be written `A-one, A-two, A-three` (uppercase, hyphen binding the letter to its index), NEVER `a one, a two, a three` — TTS reads lowercase "a one" as "uh one" (the article), which garbles the whole list. The hyphenated `A-one` form is the most reliable; `A one` (spaced) also works. Same for `b_i → B-i`, `x_n → X-n` if those letters ever collide. (On-screen math keeps the normal `a_1` subscript; only the spoken narration uppercases + hyphenates.)
   - IMPORTANT: **Hyphen-bind tightly-set math notation so TTS reads it as one unit, not loosely-spaced letters.** When symbols are written adjacent (a product, a subscript, an accent), the spoken form should bind them with hyphens — ElevenLabs voices "A X" as two drifting letters but "A-X" pulls them together:
     - **Adjacent products / juxtaposition** (`Ax`, `AB`, `Mv`, `rθ`): hyphenate → "A-X", "A-B", "M-V", "R-theta". (A genuine "A times B" you want read as a sentence can stay "A times B"; use the hyphen for the compact symbol `AB`.)
     - **Letter/word subscripts — hyphen only, NEVER the spoken word "sub"** (`A_x`, `B_y`, `v_i`, `\sigma_n`, `F_net`): "A-X", "B-Y", "V-I", "sigma-N", "F-net". The frame already shows the subscript, so saying "sub" out loud adds nothing and grates in narration full of subscripts — write what a lecturer says at the board ("sigma N", not "sigma sub N"). The hyphen alone keeps the letters from merging (voice-verified 2026-08-25: "A-N", "M-R", "X-I" read as spelled letters, not "an" / "mister" / "xi"). NUMERIC subscripts keep the `a_1 → A-one` form above. Reserve an explicit "sub" for a COMPOUND subscript that would otherwise be ambiguous (`x_{i+1}` → "X sub I plus one").
     - **Hats / accents** (`Â`, `x̂`, `\hat{β}`, `\bar{x}`, `\tilde{p}`): "A-hat", "X-hat", "beta-hat", "X-bar", "P-tilde". The hyphen also keeps "A-hat" from being misread as the article — "A hat" (spaced) risks "uh hat".
     On-screen math_steps keep the normal notation (`Ax`, `A_x`, `\hat{A}`); only the spoken natural_narration hyphenates.
   - IMPORTANT: **Never begin a sentence with the variable/matrix name "A"** — including the hyphenated forms above (`A-hat`, `A-X`, `A-one`). A sentence-initial "A" is read as the article "a" (uh) — "A is a matrix with ..." comes out "Uh is a matrix with ...", and the hyphen does NOT rescue it ("A-hat is ..." still leads with "uh"). Rephrase so "A" is not the first word; lead with the noun it labels: "A is the coefficient matrix" → "Matrix A is the coefficient matrix"; "A-hat is the estimator" → "The estimator A-hat is ...". This applies ONLY to "A" (it is the only letter that collides with the article "a") — every other single letter (B, C, X, P, …) reads correctly at the start of a sentence, leave those alone. "A" mid-sentence is also fine.
   - IMPORTANT: For hyperbolic functions, write the phonetic spelling in narration — TTS treats the trailing "h" as silent and reads them like the trig functions. Use "sinch" for sinh, "tanch" for tanh, "koth" for coth, "sheck" for sech, "co-sheck" for csch. "cosh" reads correctly as written, leave it alone. Inverses: "arc sinch", "arc tanch", "arc koth", "arc sheck", "arc co-sheck".
   - IMPORTANT: **Long raw character strings — spell out for TTS.** Any sequence of opaque hex/base58/alphanumeric characters that TTS cannot pronounce naturally must be written character-by-character with spaces in the natural_narration. Applies to hex digests/targets/nBits, Bitcoin addresses, transaction IDs, hashes, public keys, anywhere a raw value appears in the original narration. Examples:
     - Hex value: `0x1903a30c` → `0 x 1 9 0 3 a 3 0 c`
     - Hex digest: `0xDEADBEEF` → `0 x D E A D B E E F`
     - Bitcoin address: `1A1zP1...DivfNa` → `1 A 1 z P 1 ... D i v f N a`
     - Hash prefix: `a3b9...` → `a 3 b 9 ...`
     If the original narration already spells these out, preserve that spacing — do NOT collapse them back to raw form.
   - IMPORTANT: **Initialisms and acronyms — space the letters for TTS.** ElevenLabs does not reliably pronounce initialisms (slurs `UTXO` into "you-tox-oh", treats `SHA-256` as one chunk). Rule of thumb: if you'd pronounce it letter-by-letter when speaking aloud, write it with spaces in natural_narration. If it's pronounced as a word, leave it alone. Common cases:
     - Spell with spaces: `UTXO` → `U T X O`, `ECDSA` → `E C D S A`, `SHA-256` → `S H A two fifty six`, `SHA-1` → `S H A one`, `RIPEMD-160` → `R I P E M D one sixty`, `P2PKH` → `P two P K H`, `OP_CHECKSIG` → `O P check sig`, `OP_HASH160` → `O P hash one sixty`, `OP_DUP` → `O P dup`, `OP_RETURN` → `O P return`, `BIP32` → `B I P thirty two`, `BIP340` → `B I P three forty`, `BTC` → `B T C`, `API` → `A P I`, `CPU`/`GPU` → `C P U`/`G P U`, `RPC` → `R P C`, `RFC 7539` → `R F C seven five three nine`.
     - Leave as words (TTS reads them correctly): `SIGHASH`, `SegWit`, `Taproot`, `Schnorr`, `Bech32` (beck thirty two), `Merkle`, `nonce`, `JSON` (jason), `ASIC` (ay-sick), `EBITDA`, `Bitcoin`, `multisig`, `timelock`, `hashlock`, single English words used as flag names (`VERIFY`, `IF`, `ELSE`, `TRUE`, `ALL`, `NONE`).
     - Numeric suffixes stay as natural-spoken numbers, not digit-by-digit: `SHA-256` → `S H A two fifty six` (NOT `S H A two five six`).
   - Maintains the teaching flow and meaning
   - Can be longer than the original if needed for clear step-by-step explanation

3. Extract sequential steps for animation. Each step represents one thing that appears on screen:
   - For MATH content: use LaTeX notation (e.g., "\\frac{x}{y}", "V = x(10-2x)^2")
   - For PROCEDURES: use a short label (e.g., "Step 1: Picture — sketch and label variables")
   - For TIMELINES/FLOWS: use a label with values (e.g., "Year 0: Invest \\$10M")
   - For GRAPHS: describe what to draw (e.g., "Plot V(x) on [0, 5], peak at x = 5/3")
   - Each step should be one self-contained piece that builds on the previous

RESPOND WITH THIS EXACT JSON FORMAT:
{
    "verification_status": "correct" or "corrected" or "unclear",
    "issues_found": ["list of any math errors found, empty if none"],
    "natural_narration": "The TTS-friendly narration text...",
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

# Static portion of the verify_code prompt — same rationale as
# VERIFY_MATH_SYSTEM. Plain string (single braces).
VERIFY_CODE_SYSTEM = """You are an expert at reading programming code and explaining what it does. Your task is to:
1. Read the code on screen and the narration describing it.
2. Trace what the code actually does, step by line — what each line evaluates to and what state it leaves behind.
3. If the narration describes the code correctly, return it verbatim as natural_narration.
4. If the narration is wrong (off-by-one, wrong return value, wrong asymptotic claim, mis-named identifier), REWRITE natural_narration to match the actual trace. Set verification_status to "corrected" and list the issues.
5. Emit code_steps[] — one entry per line of code in display order. Each entry is the canonical line (preserve indentation), what it does, and an optional narration-phrase trigger that anchors when the line should be highlighted as the narrator discusses it.

Programming TTS rules — produce natural_narration that ElevenLabs can speak naturally:
- Drop parens, brackets, underscores from the spoken text. The on-screen code shows them.
- Operators in prose: == → "is equal to", != → "is not equal to", += → "increases by", ** → "to the power of", % → "mod", -> → "returns".
- Function calls: len(s) → "the length of S", range(10) → "range of ten", print("hi") → "print the string hi".
- snake_case / camelCase: read the words, drop the separator. `my_func` → "my func", `firstName` → "first name".
- Dunder methods: __init__ → "dunder init", __name__ → "dunder name", __main__ → "dunder main".
- Single-letter variables: UPPERCASE in narration so TTS reads the letter name, not the article. "loop variable I", not "loop variable i".
- Library names: numpy → "num pie", matplotlib → "mat plot lib", pyplot → "pie plot", tuple → "too pull", REPL → "repple".
- Big-O: O(n) → "oh of N", O(log n) → "oh of log N", O(n²) → "oh of N squared".

You must respond with ONLY valid JSON, no other text.

For every frame you receive, perform this TASK:
1. Identify the code on screen (the fenced ```python block if present, otherwise infer from narration).
2. Trace what the code does.
3. Decide if the narration's claims about the code are correct.
4. Produce code_steps[] — one entry per code line, in display order. Preserve indentation in the `expression` field.
5. Produce natural_narration — TTS-ready text. If narration was correct, return it cleaned up for TTS (apply the prose-rewriting rules above). If narration was wrong, rewrite to match the trace.

RESPOND WITH THIS EXACT JSON FORMAT:
{
    "verification_status": "correct" or "corrected" or "unclear",
    "issues_found": ["list of narration errors caught, empty if none"],
    "natural_narration": "The TTS-friendly narration text...",
    "code_steps": [
        {
            "step": 1,
            "expression": "for i in range(len(nums)):",
            "operation": "Loop over each index from 0 to len(nums) - 1",
            "highlight_when": "loop over each index",
            "note": ""
        }
    ],
    "final_answer": "The result, return value, or key takeaway of the trace",
    "confidence": "high" or "medium" or "low"
}

Important:
- `expression` MUST preserve the code's indentation literally — that's what gets rendered on screen.
- `highlight_when` is a short narration phrase (4-8 words) that the renderer can match against the word-timestamps to know when to draw a SurroundingRectangle around this line. Optional — set to "" if not needed.
- One step per displayable line. If a line is too long to display on one Manim line, split it at a natural break (after a comma or operator) and keep the indent on the continuation.
- All lines appear at frame entry. The highlight is the only thing that moves — no fading, no scrolling."""


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
