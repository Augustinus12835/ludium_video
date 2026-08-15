"""
Pre-TTS guard: detect text that ElevenLabs will mispronounce in spoken narration.

The scripting prompts (generate_scripts.py, verify_prompts.py) tell Claude to convert anything
TTS can't read naturally into spoken English BEFORE it reaches the narration field. The model
sometimes forgets. This guard re-checks the spoken text and, if it finds an un-converted token,
halts the pipeline BEFORE the TTS step and surfaces the offenders the same way SymPy failures
are — so /run-pipeline can triage (fix the source, or bypass a false alarm with
SKIP_NARRATION_CHECK=1).

It DETECTS only — it never rewrites text. False positives are expected and fine: /run-pipeline
(or the user) verifies each one. Categories, with the conversion the prompts require:

  numeral       digits / years / decimals  -> spelled-out English ("nineteen eighty-three")
  greek         α β Δ ν π …                 -> the letter's name ("alpha", "delta", "nu")
  math_symbol   √ ∫ ∑ ∞ ≤ × ° → ² …         -> spoken form ("square root of", "times", "degrees")
  hex           0xDEADBEEF                  -> spaced chars ("0 x D E A D B E E F")
  opaque        long letter+digit blobs     -> spaced chars (hashes / addresses / txids)
  differential  dx du dy …                  -> spaced ("d x", "d u")  [the "du"->"do" failure]
  acronym       UTXO ECDSA SHA …            -> spaced letters ("U T X O")  [allowlist read-as-words]
  code_token    snake_case / -> / == …      -> spoken prose ("my func", "returns", "is equal to")
  variable_a    "a one"/"a-two" (var a_1), or a bare variable a ("a times t", "a of t",
                "a-t", "slope a.")          -> uppercase the variable ("A-one"; "A times t", "A of t", "A-t")
  sentence_a    sentence-initial "A" / "A-hat" / "A-X"  -> lead with the noun ("Matrix A times …")  [read as "uh"; hyphen doesn't rescue it]

Spoken source per frame mirrors generate_tts_elevenlabs.get_natural_narration():
  - math frame with a verified natural_narration -> math_verification.json natural_narration
  - otherwise                                    -> script.json frames[N].narration
"""

import json
import re
from pathlib import Path

# Numbers, but not when embedded in a longer alnum/identifier blob (those are caught as
# hex/opaque). Bounded so "86,400" / "3.14" / "1983" match but "1903a30c" does not.
_NUMERAL = re.compile(r"(?<![0-9A-Za-z._])\d+(?:,\d+)*(?:\.\d+)?(?![0-9A-Za-z])")

# Greek + Coptic (U+0370-03FF), Greek Extended (U+1F00-1FFF), and the micro sign µ (U+00B5).
_GREEK = re.compile("[Ͱ-Ͽἀ-῿µ]")

# Math/technical Unicode: degree, ±, super/subscripts, ×, ÷, middot, primes, arrows, and the
# math-operator blocks (√ ∫ ∑ ∏ ∞ ∂ ∇ ≤ ≥ ≠ ≈ ≡ ∈ … live in U+2200-22FF / U+2A00-2AFF).
_MATH_SYMBOL = re.compile(
    "[°±²³¹×÷·"
    "′-‴⁰-₟←-⇿∀-⋿⨀-⫿]"
)

# Hex literal (0x…) and opaque alnum blobs (8+ chars containing BOTH a letter and a digit:
# hashes, addresses, txids, nBits). Pure numbers and pure words are excluded by the lookaheads.
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_OPAQUE = re.compile(r"\b(?=[0-9A-Za-z]*[A-Za-z])(?=[0-9A-Za-z]*\d)[0-9A-Za-z]{8,}\b")

# Unspaced Roman-letter differentials: dx du dy dv dw dt dr ds dz (the "du" -> "do" failure).
_DIFFERENTIAL = re.compile(r"\bd[xyuvwtrsz]\b")

# Initialisms: 2+ capitals in a row. Allowlist below holds the ones TTS reads fine as words.
_ACRONYM = re.compile(r"\b[A-Z]{2,}\b")
_ACRONYM_OK = {
    "JSON", "REST", "CRUD", "SQL", "ASIC", "RAID", "LASER", "RADAR", "SCUBA", "NATO",
    "EBITDA", "EBIT", "COGS", "SAAS", "PAAS", "MAST", "HODL", "GAAP", "TARP",
    "ALL", "NONE", "SINGLE", "TRUE", "FALSE", "VERIFY", "IF", "ELSE", "AND", "OR", "NOT",
    "ANYONECANPAY", "OK",
}

# Code leakage: tokens with an underscore (snake_case / dunder) and multi-char ASCII operators.
_CODE_TOKEN = re.compile(r"\b\w*_\w+\b|->|=>|==|!=|>=|<=")

# Lowercase single-letter variable "a" used with an index. A subscripted variable `a_1, a_2,
# a_3` (matrix entries, DE/Fourier coefficients, sequence terms) that reaches narration as
# "a one, a two, a three" is voiced by ElevenLabs as "uh one, uh two, uh three" — the article
# "a" collides with the variable. Only lowercase "a" collides; uppercase "A" and every other
# letter read as the letter name. Fix: uppercase the variable ("A-one" / "A one").
#
# Detecting this is hard because the article legitimately precedes numbers ("a 4 by 4 matrix",
# "a 2-of-3 setup", "a one-dimensional space"). Two guards keep precision high:
#   (1) exclude article-compounds — index followed by a hyphen ("a one-dimensional") or a
#       magnitude/measure word ("a two hundred", "a 4 by 4", "a 2 of 3");
#   (2) only flag the SEQUENCE signature — a consecutive index run (a-one, a-two, …) or 3+
#       indexed hits in one frame. A lone "a four" stays unflagged; a variable LIST trips it.
# Residual false positives (e.g. "each a zero or a one") are rare and fine — the gate triages.
_VAR_A_INDEX_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_VAR_A = re.compile(
    r"\ba([ -])(\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\b(?!-)(?! (?:hundred|thousand|million|billion|by|of|to)\b)"
)


# Sentence-initial variable/matrix name "A" — TTS reads it as the article "a" (uh), so
# "A times x ..." is voiced "Uh times x ...". A bare sentence-initial "A" is usually the
# legitimate article ("A vector is ..."), so only flag when the NEXT word is one an article
# can never precede — a verb or math-operator word that forces the letter reading. Calibrated
# against the corpus (2026-06-11): of ~2,860 sentence-initial "A <word>" occurrences, this
# list matched ~220, essentially all genuine letter-A. "dot" and "prime" are deliberately
# EXCLUDED — they collide with article+noun ("a dot product", "a prime number").
# Fix: lead with the noun "A" labels ("Matrix A times ...", "Vector A equals ...").
_SENTENCE_A = re.compile(
    r"(?:^|[.!?:;]\s+|\n\s*)"
    r"(A(?: (?:is|has|equals|times|plus|minus|transpose|inverse|acts|maps|becomes|"
    r"sub|squared|cubed|cross|hat|over|to the)"
    # hyphenated variable forms (A-hat, A-sub-X, A-X, A-one) still read as "uh" at
    # sentence start — the hyphen binds the token but doesn't rescue leading "A".
    r"|-(?:hat|sub|prime|bar|tilde|one|two|three|four|five|six|seven|eight|nine|ten|[A-Z]))"
    r"\b[^.!?\n]{0,30})"
)


def _sentence_a(text):
    return [m.group(1).rstrip() for m in _SENTENCE_A.finditer(text)]


def _variable_a(text):
    hits = []
    for m in _VAR_A.finditer(text):
        sep, v = m.group(1), m.group(2)
        n = int(v) if v.isdigit() else _VAR_A_INDEX_WORDS[v]
        if n <= 10:
            # digit ("a 1") or hyphen ("a-one") forms are unambiguously a variable;
            # the spaced-word form ("a one") collides with the article and is only
            # trusted as part of a consecutive sequence (handled by `best` below).
            strong = v.isdigit() or sep == "-"
            hits.append((n, m.group(0), strong))
    if len(hits) < 2:
        return []
    distinct = sorted({n for n, _, _ in hits})
    run = best = 1
    for a, b in zip(distinct, distinct[1:]):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    # consecutive distinct-index run (a1, a2, …) -> a variable list regardless of form;
    # else require 3+ hits AND at least one unambiguous (digit/hyphen) form, so that a
    # bare "a zero"/"a one" repeated as the article ("a zero pivot", "a one-to-one map")
    # does not trip the gate.
    if best >= 2 or (len(hits) >= 3 and any(s for _, _, s in hits)):
        hits = [(n, tok) for n, tok, _ in hits]
        # de-dup tokens while preserving first-seen order
        seen, out = set(), []
        for _, tok in hits:
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
        return out
    return []


# Bare (non-indexed) standalone variable "a" in flowing prose. The acceleration /
# leading-coefficient / generic variable `a`, spoken next to an operator word, an
# argument, or at a clause end, is voiced by ElevenLabs as the article "uh" — "a times t"
# -> "uh times t", "a of t" -> "uh of t", "the slope is a" -> "... is uh". Fix: uppercase
# it ("A times t", "A of t", "the slope is A", "A-t"). Only lowercase "a" collides (every
# other letter reads as its name), so this targets "a" alone. High-precision shapes only —
# the article "a" never precedes "times/squared/cubed/of/equals/over" as an operator, never
# hyphen-binds to a lone variable letter, and never ends a clause. A handful of residual
# false positives (e.g. a Latin lesson literally discussing the article "a") are fine — the
# gate triages. Indexed forms (a_1 -> "a one") are handled separately by `_variable_a`.
_VAR_A_BARE = re.compile(
    r"\ba (?:times|squared|cubed|of|equals|over)\b"   # a times t / a squared / a of t / a equals
    r"|\ba-(?:t|x|y|z|b|c|d|n|m|k)\b"                 # a-t, a-t-squared, a-x (hyphen-bound product)
    r"|\ba t (?:squared|cubed)\b"                     # spaced "a t squared"
    r"|\ba(?=[.,;:])"                                 # lone "a" before clause punctuation
)
_VAR_A_BARE_SKIP_PREV = {"no", "letter", "article"}


def _bare_variable_a(text):
    out = []
    for m in _VAR_A_BARE.finditer(text):
        prev = text[:m.start()].rstrip().rsplit(" ", 1)[-1].strip(".,;:!?\"'").lower()
        if prev in _VAR_A_BARE_SKIP_PREV:
            continue  # "no a", "the letter a", "the article a" — discussing the symbol itself
        out.append(m.group(0))
    return out


def _acronyms(text):
    return [t for t in _ACRONYM.findall(text) if t.upper() not in _ACRONYM_OK]


# (category, finder) — each finder returns a list of offending substrings.
_DETECTORS = [
    ("numeral", _NUMERAL.findall),
    ("greek", _GREEK.findall),
    ("math_symbol", _MATH_SYMBOL.findall),
    ("hex", _HEX.findall),
    ("opaque", _OPAQUE.findall),
    ("differential", _DIFFERENTIAL.findall),
    ("acronym", _acronyms),
    ("code_token", _CODE_TOKEN.findall),
    ("variable_a", _variable_a),
    ("variable_a", _bare_variable_a),
    ("sentence_a", _sentence_a),
]


def find_tts_issues(text: str):
    """Return a list of (category, token) for every TTS-unfriendly token in `text`.
    Empty list = clean. De-duplicated per (category, token)."""
    if not text:
        return []
    seen = set()
    out = []
    for category, finder in _DETECTORS:
        for tok in finder(text):
            key = (category, tok)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _spoken_text_for_frame(frame: dict, frame_num, math_frames: dict):
    """Return (source_label, text) — the text TTS will actually speak for this frame."""
    fd = math_frames.get(str(frame_num)) if math_frames else None
    if fd:
        natural = fd.get("natural_narration")
        if natural and fd.get("verification_status") in ("correct", "corrected"):
            return ("natural_narration", natural)
    return ("script.json narration", frame.get("narration", ""))


def scan_video_narration(video_dir):
    """
    Scan a Video-N directory's spoken narration for TTS-unfriendly tokens.

    Returns a list of offender dicts: {frame, source, issues, preview}, where `issues` is a
    list of (category, token). Empty list = clean.
    """
    video_dir = Path(video_dir)
    script_path = video_dir / "script.json"
    if not script_path.exists():
        return []
    script = json.loads(script_path.read_text(encoding="utf-8"))

    mv_path = video_dir / "math_verification.json"
    math_frames = {}
    if mv_path.exists():
        try:
            math_frames = json.loads(mv_path.read_text(encoding="utf-8")).get("frames", {})
        except (json.JSONDecodeError, OSError):
            math_frames = {}

    offenders = []
    for i, frame in enumerate(script.get("frames", [])):
        num = frame.get("number", i)
        source, text = _spoken_text_for_frame(frame, num, math_frames)
        issues = find_tts_issues(text)
        if issues:
            offenders.append({
                "frame": num,
                "source": source,
                "issues": issues,
                "preview": (text or "")[:160],
            })
    return offenders


def format_report(offenders, video_dir) -> str:
    """Build a SymPy-failure-style report block for the pipeline log."""
    lines = [f"  ✗ TTS narration check failed for {len(offenders)} frame(s) in {video_dir}:"]
    for o in offenders:
        # group tokens by category for a compact line
        by_cat = {}
        for cat, tok in o["issues"]:
            by_cat.setdefault(cat, []).append(tok)
        summary = "; ".join(f"{cat}: {toks}" for cat, toks in by_cat.items())
        lines.append(f"    Frame {o['frame']} ({o['source']}): {summary}")
        lines.append(f"      \"{o['preview']}\"")
    lines.append("")
    lines.append("  Spoken narration must be fully TTS-ready — no raw numerals, Greek letters,")
    lines.append("  math symbols, hex/opaque strings, unspaced differentials (dx -> 'd x'),")
    lines.append("  bare initialisms, code tokens, lowercase variable 'a' (indexed 'a one' ->")
    lines.append("  'A-one'; bare 'a times t'/'a of t'/'slope a' -> 'A times t'/'A of t'/'slope A'),")
    lines.append("  or a sentence starting with the name 'A' (read as the article")
    lines.append("  'uh' — lead with the noun: 'Matrix A times ...'). Convert each in the spoken source")
    lines.append("  (natural_narration for math frames, script.json narration for visual frames);")
    lines.append("  on-screen text keeps its normal form. Genuine false positive? Resume with")
    lines.append("  SKIP_NARRATION_CHECK=1 to bypass.")
    return "\n".join(lines)
