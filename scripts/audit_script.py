#!/usr/bin/env python3
"""
Mechanical script-level audit for a Video-N/script.json (math / technical / folio).

WHY THIS EXISTS. The Phase-B script review used to be one subagent per video that
re-authored the same probes every time: a cue-uniqueness checker, a final-cue margin
calculator, a beat-gap table, a metadata consistency check, a scope-gate regex sweep, an
n-gram overlap tool. Measured on one five-video lecture, the reviewers spent ~1.08M tokens,
roughly half of it on checks that are pure string arithmetic. This script does that half in
about two seconds so the reviewer spends its tokens on the parts that need judgement:
the mathematics (SymPy), the fit simulation (see utils/manim_probe.py), pedagogy, source
fidelity and mannered prose.

Run it BEFORE spawning the reviewer, and hand the reviewer the output.

    python scripts/audit_script.py pipeline/<L>/Video-N
    python scripts/audit_script.py pipeline/<L>/Video-N --siblings     # + cross-video n-gram
    python scripts/audit_script.py pipeline/<L>/Video-N --json
    python scripts/audit_script.py --self-test                         # prove every check FIRES

Exit code 0 = clean, 1 = findings, 2 = usage/IO error.

TWO CONVENTIONS THIS ENCODES, both of which cost a review round when they were rediscovered
by hand the hard way:

  * Cue phrases appear as BOTH `On "..."` and lowercase `on "..."`. On one script, 48 of 128
    cues (37%) were lowercase; a case-sensitive probe invents dead spans that do not exist.
    Matching here is case-insensitive AND punctuation-free, because downstream resolution is
    word-level — that is how a cue with a trailing period was caught matching three places
    and firing 12 s early.

  * The scope gate's absolute `len(reference) > 1200` trigger is NOT used. Measured against
    a shipped-good 119-frame corpus the per-frame median is 1,186 chars and
    44% of known-good frames exceed 1,200, so it fires on ordinary frames. The calibrated
    REGEXES (measured widths, scale directives, canvas coords, Manim API, rules blocks) are
    the real signal; length is reported as chars-per-narration-word (corpus median 10.3,
    p90 15.4) purely as context.

NARRATION-ONLY SCRIPTS (2026-09-15). Only math / technical frames schedule their visuals by cue
phrase and declare a `frame_class`. A script authored for a separately-built visual track (an
illustration stream or per-frame plan laid over the narration afterwards) does neither, and its
`visual.reference` is a placeholder. The audit used to report one "no-cues" BLOCK per frame on
every such script and, because that path returned early, never ran the TTS checks on them at
all. A script in which NO frame declares a `frame_class` is now audited as narration-only: the
reference checks are skipped and the spoken-text TTS checks still run. A math script still
BLOCKs on a frame with no cues.

HUMANITIES DOSSIER CHECKS. Folio (humanities) narration is written from Video-N/content.txt
treated as a research DOSSIER, to a reviewed argument map (argument.json), at ~75% of the
dossier's length. A script written straight from its source tracks the lecture instead (one
early episode: 20% of 10-grams verbatim, an identical run of 89 words). These checks compare
the narration with the dossier and gate that shape:

  dossier-verbatim   BLOCK  an identical run of >= 10 words outside quotation marks (numbers
                            normalised through subtitle_compact, so "four hundred fifty-eight"
                            matches "458"; a run that is mostly names/numbers is exempt)
  dossier-overlap    CHECK  > 8% of the narration's 6-grams also occur in the dossier
  dossier-ratio      CHECK  narration words / dossier words > 0.80 (raised only when the
                            15-minute floor itself demands more)
  unsourced-figure   CHECK  a number or capitalised name absent from the dossier, in a frame
                            whose `sources` does not carry "common"
  sources            CHECK  a frame with no `sources`, or a P-id the dossier does not have
  lecture-filler     CHECK  "So,"/"Now," openers, "as we'll see", "of course", "let us",
                            "in this video", a question mark outside quotes, …
  sentence-cadence   CHECK  mean sentence > 15 words, or > 12% of sentences over 22
  movement-shape     CHECK  < 3 or > 6 movements, a movement < 90 s or > 6 min, a missing
                            or decreasing `movement`, a cold open over ~60 s

Mode: `--mode auto` (default) treats a narration-only script as folio (humanities); pass
`--mode` to force it. `--argument` checks
Video-N/argument.json alone (the mechanical half of review round 0) — no script needed.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WPS = 2.5                    # 150 wpm, the pipeline's standard narration pace
GAP_LIMIT = 6.0              # seconds with nothing scheduled before it is a finding
FIRST_BEAT_LIMIT = 5.0       # every frame must put something on screen within ~5s
MARGIN_FLOOR = 5.0           # a reference asking the board to be HELD needs >= 5s

# Calibrated against a known-bad script (fires) vs shipped-good ones (silent).
# Do not add a pattern without checking it fires on a known-bad script AND stays silent
# on a known-good one — a scope gate that has never fired is untested, not reassuring.
SCOPE_PATS = {
    "measured_width":  r"\d+\.\d+\s*u\b",
    "scale_directive": r"at scale \d",
    "canvas_coord":    r"\(-?\d\.\d, -?\d\.\d\)",
    "manim_api":       r"\b(?:Tex|MathTex|Ellipse|VGroup|set_stroke|Indicate|scale_to_fit_width)\(",
    "rules_block":     r"CLASS RULES",
}
COLOUR_WORDS = re.compile(
    r"\b(RED_C|BLUE_D|GREEN|RED|BLUE|TEAL|PINK|PURPLE|ORANGE|GOLD|YELLOW|MAROON|GREY|GRAY)\b", re.I)
# U+22EF crashes LaTeX outright; U+2212 crashes inside MathTex; U+2192 silently vanishes.
RAW_MATH_GLYPHS = "⋯−→×·≤≥≠"
CUE_RE = re.compile(r'[Oo]n "([^"]+)"')
# A cue written as a list — `On "a", "b" and "c"` — is only HALF SEEN: CUE_RE needs `on `
# before every quote, so "b" and "c" are never scheduled. The symptom is not a missing cue
# but a reported DEAD SPAN over the words they cover, which reads as a staging bug in the
# author's `visual` rather than a parsing artifact here, so the author "fixes" beats that
# were never broken (one script had 10 quoted phrases, 8 matched, and one phantom 9.2 s
# span). Leaving CUE_RE alone is deliberate — a quoted ON-SCREEN LABEL is also legitimately
# not a cue — so this warns instead of widening the parse.
CUE_LIST_RE = re.compile(r'[Oo]n "[^"]+"((?:\s*(?:,|and|then|or|&)\s*"[^"]+")+)')
QUOTED_RE = re.compile(r'"([^"\n]+)"')

# Severity. BLOCK = the frame cannot render as directed, or a number is provably wrong.
# CHECK = a real finding the reviewer must judge (it may be correct as authored).
# Everything else is context. Defaults to CHECK.
SEVERITY = {
    "dossier-verbatim": "BLOCK",
    "cue-not-found": "BLOCK", "cue-ambiguous": "BLOCK", "cue-substring": "BLOCK",
    "empty-cue": "BLOCK", "no-cues": "BLOCK", "margin-zero": "BLOCK",
    "meta-frame-count": "BLOCK", "meta-gapless": "BLOCK", "meta-word-count": "BLOCK",
    "tex-unsafe": "BLOCK",                      # this one crashes the render outright
    "tts-digits": "BLOCK", "tts-numeral": "BLOCK",
}
# A quoted on-screen note string, distinguished from the two other uses of "'" in these
# references: an English apostrophe, and a PRIME mark (u', v', f'). The naive r"'([^']*)'"
# matched from one prime to the next and reported whole sentences of prose as on-screen text.
# So: the opening quote must follow whitespace or a bracket (a prime always follows a letter),
# the body must be short and carry no double quote or sentence break, and the closing quote
# must be followed by whitespace or punctuation.
NOTE_RE = re.compile(r"(?:(?<=\s)|(?<=\()|^)'([^'\"\n]{1,60})'(?=[\s.,;:)\]]|$)")


def norm(s: str) -> str:
    """Word-level, punctuation-free normalisation — what downstream cue resolution does."""
    return " ".join(re.sub(r"[^\w\s-]", " ", s.lower()).split())


def _find_span(hay_words, needle_words):
    """Return (start, end_inclusive) word indices of the LAST occurrence, or None."""
    n = len(needle_words)
    for i in range(len(hay_words) - n, -1, -1):
        if hay_words[i:i + n] == needle_words:
            return i, i + n - 1
    return None


def audit_frame(frame, idx, cue_schedule=True):
    """All per-frame mechanical checks. Returns (findings, stats).

    cue_schedule=False audits a narration-only frame: its `visual.reference` is a placeholder,
    so only the spoken text is checked (see the module docstring)."""
    num = frame.get("number", idx)
    narration = frame.get("narration", "") or ""
    visual = frame.get("visual") or {}
    ref = visual.get("reference", "") if isinstance(visual, dict) else str(visual)
    f, st = [], {"frame": num, "frame_class": frame.get("frame_class"),
                 "narr_words": len(narration.split()), "ref_chars": len(ref)}
    nw = norm(narration).split()
    dur = len(nw) / WPS
    st["duration_est"] = round(dur, 1)

    if not cue_schedule:
        st["cues"] = 0
        _audit_tts(f, num, narration, cue_schedule)
        return f, st

    cues = CUE_RE.findall(ref)
    st["cues"] = len(cues)
    if not cues:
        f.append((num, "no-cues", "reference schedules nothing"))
        _audit_tts(f, num, narration, cue_schedule)
        return f, st

    # Continuation phrases in a listed cue (see CUE_LIST_RE). Only flag one that occurs
    # VERBATIM IN THE NARRATION and runs to 2+ words: a one-word or unspoken quote after a
    # cue is an on-screen label, which is the common and legitimate case. Calibrated over
    # 15,388 references: 14 hits in 3 lectures (~1 per 1,100), most of them real — e.g.
    # 'write it down the second column'. A multi-word label the narration also
    # happens to speak is genuinely ambiguous and is surfaced for the reviewer to judge.
    for m in CUE_LIST_RE.finditer(ref):
        for q in QUOTED_RE.findall(m.group(1)):
            qn = norm(q).split()
            if len(qn) < 2:
                continue
            if any(nw[i:i + len(qn)] == qn for i in range(len(nw) - len(qn) + 1)):
                f.append((num, "cue-list-unscheduled",
                          f'"{q[:45]}" follows a cue in a list, so nothing schedules it '
                          f'(any dead span here is this, not your staging)'))

    spans = []
    for c in cues:
        cn = norm(c).split()
        if not cn:
            f.append((num, "empty-cue", repr(c)[:60])); continue
        hits = sum(1 for i in range(len(nw) - len(cn) + 1) if nw[i:i + len(cn)] == cn)
        if hits == 0:
            f.append((num, "cue-not-found", f'"{c[:60]}"'))
        elif hits > 1:
            f.append((num, "cue-ambiguous", f'"{c[:60]}" matches {hits}x — fires at the FIRST'))
        else:
            s = _find_span(nw, cn)
            spans.append((s[0], s[1], c))

    # containment: a later cue holding an earlier one as a substring misfires silently
    for i, (_, _, a) in enumerate(spans):
        for j, (_, _, b) in enumerate(spans):
            if i != j and norm(a) and norm(a) in norm(b) and norm(a) != norm(b):
                f.append((num, "cue-substring", f'"{a[:40]}" is contained in "{b[:40]}"'))

    spans.sort()
    for (a0, a1, at), (b0, b1, bt) in zip(spans, spans[1:]):
        if b0 <= a1:
            f.append((num, "cue-span-overlap",
                      f'"{at[:35]}" ends at word {a1}, "{bt[:35]}" starts at {b0}'))

    if spans:
        st["first_beat"] = round(spans[0][0] / WPS, 1)
        if st["first_beat"] > FIRST_BEAT_LIMIT:
            f.append((num, "dead-open", f'first beat at {st["first_beat"]}s (limit {FIRST_BEAT_LIMIT}s)'))
        gaps = []
        prev = 0
        for s0, s1, txt in spans:
            g = (s0 - prev) / WPS
            if g > GAP_LIMIT:
                gaps.append((round(prev / WPS, 1), round(s0 / WPS, 1), round(g, 1)))
            prev = s1 + 1
        st["max_gap"] = round(max([g[2] for g in gaps], default=0.0), 1)
        for a, b, g in gaps:
            f.append((num, "dead-span", f"{a}s -> {b}s ({g}s with nothing scheduled)"))

        # Final-cue margin, measured from where the phrase ENDS (measuring from its START
        # overstates by 1-4s and is the most common way this ships wrong).
        last_end = max(s1 for _, s1, _ in spans)
        margin = (len(nw) - (last_end + 1)) / WPS
        st["final_margin"] = round(margin, 1)
        if last_end == len(nw) - 1:
            f.append((num, "margin-zero", "final cue IS the closing phrase — margin 0, frame is broken"))
        elif margin < MARGIN_FLOOR:
            f.append((num, "margin-thin", f"{margin:.1f}s (< {MARGIN_FLOOR}s needed for a held board)"))

    # scope gate: regexes only; length reported as a ratio, never as a threshold
    for k, pat in SCOPE_PATS.items():
        n = len(re.findall(pat, ref))
        if n:
            f.append((num, f"scope-{k}", f"{n} occurrence(s) — belongs in Stage 2, not the script"))
    st["ref_per_word"] = round(len(ref) / max(st["narr_words"], 1), 1)

    for cw in set(COLOUR_WORDS.findall(ref)):
        if cw.upper() not in ("GREY", "GRAY"):      # scenery grey is allowed
            f.append((num, "colour-word", f'reference names "{cw}" — write the QUANTITY instead'))

    # on-screen note strings that would crash Tex()
    for s in NOTE_RE.findall(ref):
        if ". " in s or s.strip().startswith(("On ", "and on ")):
            continue                                   # prose that slipped the quote heuristic
        outside = re.sub(r"\$[^$]*\$", "", s)
        if re.search(r"[\^_]", outside):
            f.append((num, "tex-unsafe", f"bare ^ or _ outside $...$: '{s[:50]}'"))
        elif re.search(r"\\[a-zA-Z]{2,}", outside):
            f.append((num, "tex-unsafe", f"bare macro outside $...$: '{s[:50]}'"))
        for ch in set(s) & set(RAW_MATH_GLYPHS):
            f.append((num, "raw-glyph", f"U+{ord(ch):04X} in quoted string: '{s[:40]}'"))

    _audit_tts(f, num, narration, cue_schedule)
    return f, st


def _audit_tts(f, num, narration, math=True):
    """TTS safety of the SPOKEN text: applies to every mode, narration-only included."""
    issues = []
    try:
        from scripts.utils.narration_check import find_tts_issues
        issues = find_tts_issues(narration, math)
    except Exception as e:                                    # noqa: BLE001
        f.append((num, "tts-check-unavailable", str(e)[:60]))
    for cat, tok in issues:
        f.append((num, f"tts-{cat}", repr(tok)[:50]))
    digits = re.findall(r"\d", narration)
    if digits:
        f.append((num, "tts-digits", f"{len(digits)} Arabic numeral(s) in spoken text"))


# ---------------------------------------------------------------------------
# Humanities: narration vs dossier (see the module docstring)
# ---------------------------------------------------------------------------
HUMANITIES_MODES = ("folio",)
VERBATIM_RUN = 10            # BLOCK at an identical run this long (words) …
VERBATIM_ORDINARY = 8        # … of which at least this many are ordinary words, so a
                             # run that is mostly names, dates and numbers never BLOCKs
OVERLAP_N, OVERLAP_MAX = 6, 0.08
RATIO_MAX = 0.80
CADENCE_MEAN, CADENCE_LONG, CADENCE_LONG_SHARE = 15.0, 22, 0.12
MOVE_MIN_S, MOVE_MAX_S, COLD_OPEN_MAX_S = 90.0, 360.0, 60.0
QUOTE_SPAN_RE = re.compile(r'"[^"]*"|“[^”]*”')
FILLER_PATS = [
    (r"^(?:So|Now),", "sentence-initial So,/Now,"),
    (r"\bas we(?:['’]ll| will| shall) see\b", "as we'll see"),
    (r"\bas we(?: have)? (?:saw|seen)\b", "as we saw"),
    (r"\bit(?: is|['’]s) worth noting\b", "it is worth noting"),
    (r"\bof course\b", "of course"),
    (r"\blet(?: us|['’]s)\b", "let us"),
    (r"\bremember that\b", "remember that"),
    (r"\bin this video\b", "in this video"),
]
# Capitalised words that are not names: pronoun, era letters, sentence furniture.
_NAME_STOP = {"I", "B", "C", "A", "D", "BC", "AD", "Mr", "Mrs", "OK"}


def _mask_quotes(text: str) -> str:
    """Replace each quoted span with a unique non-matching token."""
    n = [0]

    def sub(_m):
        n[0] += 1
        return f" qqmask{n[0]}qq "
    return QUOTE_SPAN_RE.sub(sub, text)


def _canon_tokens(text: str):
    """[(token, is_name_or_number)] after spoken numbers are compacted to digits.
    Both the narration and the dossier go through this, so 'four hundred fifty-eight'
    and '458' meet as '458'."""
    try:
        from scripts.utils.subtitle_compact import compact_words
        words = [w["word"] for w in compact_words(
            [{"word": w, "start": 0.0, "end": 0.0} for w in text.split()])]
    except Exception:                                          # noqa: BLE001
        words = text.split()
    out, prev = [], ""
    for w in words:
        initial = not prev or prev.rstrip('"”’\')').endswith((".", "!", "?", ":"))
        flag = bool(w) and ((w[0].isupper() and not initial) or w[0].isdigit())
        for piece in norm(w).split():
            out.append((piece, flag))
        prev = w
    return out


def _grams(tokens, n):
    return [tuple(t for t, _ in tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _sentences(text: str):
    return [x for x in re.split(r'(?<=[.!?])["”]?\s+', text.strip()) if x.strip()]


def load_dossier(video_dir: Path) -> str:
    p = video_dir / "content.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def humanities_checks(frames, dossier: str, mode: str = "folio"):
    """(findings, stats) for the narration-vs-dossier and shape checks."""
    from scripts.generate_scripts import dossier_paragraph_map, humanities_targets
    f, st = [], {}
    paras = dossier_paragraph_map(dossier)
    if not dossier.strip():
        return [(None, "no-dossier", "Video-N/content.txt missing — dossier checks skipped")], st

    # dossier n-gram indexes, with the paragraph each 10-gram first occurs in
    run_index, six = {}, set()
    for pid, text in paras.items():
        toks = _canon_tokens(text)
        for g in _grams(toks, VERBATIM_RUN):
            run_index.setdefault(g, pid)
        six.update(_grams(toks, OVERLAP_N))

    all_six, total_words = [], 0
    for idx, fr in enumerate(frames):
        num = fr.get("number", idx)
        narration = fr.get("narration") or ""
        total_words += len(narration.split())
        toks = _canon_tokens(_mask_quotes(narration))
        all_six += _grams(toks, OVERLAP_N)
        hits = [g in run_index for g in _grams(toks, VERBATIM_RUN)]
        i = 0
        while i < len(hits):
            if not hits[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(hits) and hits[j + 1]:
                j += 1
            run = toks[i:j + VERBATIM_RUN]
            ordinary = sum(1 for _, fl in run if not fl)
            if ordinary >= VERBATIM_ORDINARY:                # name / date lists are exempt
                pid = run_index[tuple(t for t, _ in toks[i:i + VERBATIM_RUN])]
                text = " ".join(t for t, _ in run)
                f.append((num, "dossier-verbatim",
                          f"{len(run)}-word run identical to {pid}: \"{text[:90]}"
                          f"{'…' if len(text) > 90 else ''}\" — rewrite, or quote it (keep_verbatim)"))
            i = j + 1

    ov = (sum(1 for g in all_six if g in six) / len(all_six)) if all_six else 0.0
    st["dossier_overlap"] = round(ov, 3)
    if ov > OVERLAP_MAX:
        f.append((None, "dossier-overlap",
                  f"{100 * ov:.1f}% of the narration's {OVERLAP_N}-grams occur in the dossier "
                  f"(limit {100 * OVERLAP_MAX:.0f}%) — the script is paraphrasing, not writing"))

    dossier_words = len(dossier.split())
    ratio = total_words / max(dossier_words, 1)
    _, target = humanities_targets(dossier, mode)
    limit = max(RATIO_MAX, target / max(dossier_words, 1) + 0.05)
    st["dossier_ratio"] = round(ratio, 2)
    st["target_words"] = target
    if ratio > limit:
        f.append((None, "dossier-ratio",
                  f"narration is {ratio:.2f}x the dossier ({total_words:,} / {dossier_words:,} "
                  f"words; limit {limit:.2f}, target ~{target:,}) — select, don't transcribe"))

    # provenance: sources present and real; unsourced numbers and names
    known = set(paras)
    dnums = {t for t, _ in _canon_tokens(dossier) if any(c.isdigit() for c in t)}
    dwords = {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]+", dossier)}
    missing_src, common_frames = [], []
    for idx, fr in enumerate(frames):
        num = fr.get("number", idx)
        src = fr.get("sources")
        if not src:
            missing_src.append(num)
            src = []
        src = [src] if isinstance(src, str) else src
        bad = [s for s in src if s != "common" and s not in known]
        if bad:
            f.append((num, "sources", f"unknown paragraph ID(s) {bad} (dossier has P1…P{len(paras)})"))
        if "common" in src:
            common_frames.append(num)
            continue
        narration = fr.get("narration") or ""
        odd = []
        for t, _ in _canon_tokens(narration):
            if any(c.isdigit() for c in t) and t not in dnums and t not in odd:
                odd.append(t)
        # an opening quotation mark starts a sentence too ('asks, "Who wishes…')
        for sent in _sentences(re.sub(r'\s["“]', ". ", narration)):
            words = re.findall(r"[A-Za-zÀ-ÿ'’-]+", sent)
            for w in words[1:]:                               # skip the sentence-initial word
                for part in w.split("-"):
                    part = re.sub(r"['’]s?$", "", part)
                    if (len(part) < 3 or not part[0].isupper() or part in _NAME_STOP
                            or part in odd):
                        continue
                    lo = part.lower()
                    stem = lo[:max(4, len(lo) - 2)]
                    if lo in dwords or lo.rstrip("s") in dwords or any(
                            d.startswith(stem) for d in dwords):
                        continue
                    odd.append(part)
        if odd:
            f.append((num, "unsourced-figure",
                      f"not in the dossier: {', '.join(odd[:8])}{' …' if len(odd) > 8 else ''} "
                      "— verify, or tag the frame's sources \"common\""))
    if missing_src:
        f.append((None, "sources", f"{len(missing_src)} frame(s) carry no `sources`: "
                  f"{missing_src[:15]}{' …' if len(missing_src) > 15 else ''}"))
    st["common_frames"] = common_frames

    # lecture filler + cadence
    lengths = []
    for idx, fr in enumerate(frames):
        num = fr.get("number", idx)
        narration = fr.get("narration") or ""
        masked = _mask_quotes(narration)
        found = []
        for sent in _sentences(masked):
            s = sent.strip().strip('"“”')
            for pat, label in FILLER_PATS:
                if re.search(pat, s, re.I if not pat.startswith("^") else 0):
                    found.append(label)
            if s.rstrip('"”').endswith("?"):
                found.append("question")
        if found:
            f.append((num, "lecture-filler", ", ".join(sorted(set(found)))))
        lengths += [len(x.split()) for x in _sentences(narration)]
    if lengths:
        mean = sum(lengths) / len(lengths)
        long_share = sum(1 for n in lengths if n > CADENCE_LONG) / len(lengths)
        st["sentence_mean"] = round(mean, 1)
        st["sentence_long_share"] = round(long_share, 3)
        if mean > CADENCE_MEAN or long_share > CADENCE_LONG_SHARE:
            f.append((None, "sentence-cadence",
                      f"mean {mean:.1f} words/sentence (limit {CADENCE_MEAN:.0f}), "
                      f"{100 * long_share:.0f}% over {CADENCE_LONG} words (limit "
                      f"{100 * CADENCE_LONG_SHARE:.0f}%)"))

    # movement shape
    moves = [fr.get("movement") for fr in frames]
    if all(m is None for m in moves):
        f.append((None, "movement-shape", "no frame carries `movement` — script predates the "
                  "argument-map process, or the field was dropped"))
    else:
        if any(m is None for m in moves):
            f.append((None, "movement-shape",
                      f"frames without `movement`: {[fr.get('number', i) for i, fr in enumerate(frames) if fr.get('movement') is None][:15]}"))
        seq = [m for m in moves if isinstance(m, int)]
        if any(b < a for a, b in zip(seq, seq[1:])):
            f.append((None, "movement-shape", f"`movement` decreases somewhere: {seq}"))
        dur = {}
        for fr in frames:
            m = fr.get("movement")
            if isinstance(m, int):
                dur[m] = dur.get(m, 0.0) + len((fr.get("narration") or "").split()) / WPS
        body = {m: d for m, d in dur.items() if m != 0}
        st["movements"] = {m: round(d) for m, d in sorted(dur.items())}
        if not 3 <= len(body) <= 6:
            f.append((None, "movement-shape", f"{len(body)} movements (need 3–6)"))
        for m, d in sorted(body.items()):
            if d < MOVE_MIN_S or d > MOVE_MAX_S:
                f.append((None, "movement-shape",
                          f"movement {m} runs {d:.0f}s (90 s – 6 min)"))
        if dur.get(0, 0) > COLD_OPEN_MAX_S:
            f.append((None, "movement-shape", f"cold open (movement 0) runs {dur[0]:.0f}s "
                      f"(~30 s intended, limit {COLD_OPEN_MAX_S:.0f}s)"))
        if 0 not in dur:
            f.append((None, "movement-shape", "no cold-open frame (movement 0)"))
    return f, st


def detect_mode(video_dir: Path, script: dict, cli_mode: str = "auto") -> str:
    """Script mode for gating: cli override, else inferred from the script's shape."""
    if cli_mode != "auto":
        return cli_mode
    frames = script.get("frames", [])
    if any(fr.get("frame_class") for fr in frames):
        return "manim"                          # math / technical
    return "folio"                              # narration-only = humanities


def audit_argument(video_dir: Path, mode: str = "folio"):
    """Mechanical checks on Video-N/argument.json (review round 0). Returns problems."""
    from scripts.generate_scripts import load_argument_map, validate_argument_map
    arg, err = load_argument_map(video_dir)
    if err:
        return [err]
    if arg is None:
        return [f"no {video_dir}/argument.json"]
    return validate_argument_map(arg, load_dossier(video_dir), "folio")


def ngram_overlap(a: str, b: str, n=8):
    """Fraction of a's n-grams also present in b."""
    aw, bw = norm(a).split(), norm(b).split()
    if len(aw) < n:
        return 0.0
    A = {tuple(aw[i:i + n]) for i in range(len(aw) - n + 1)}
    B = {tuple(bw[i:i + n]) for i in range(len(bw) - n + 1)}
    return len(A & B) / len(A) if A else 0.0


def audit(video_dir: Path, siblings=False, mode="auto"):
    sp = video_dir / "script.json"
    if not sp.exists():
        raise SystemExit(f"no script.json in {video_dir}")
    script = json.loads(sp.read_text(encoding="utf-8"))
    frames = script.get("frames", [])
    meta = script.get("metadata", {}) or {}
    findings, stats = [], []

    declared = meta.get("frame_count")
    if declared is not None and declared != len(frames):
        findings.append((None, "meta-frame-count", f"metadata says {declared}, file has {len(frames)}"))
    nums = [fr.get("number", i) for i, fr in enumerate(frames)]
    if nums != list(range(len(frames))):
        findings.append((None, "meta-gapless", f"frame numbers are {nums}"))
    total = sum(len((fr.get("narration") or "").split()) for fr in frames)
    if meta.get("word_count") not in (None, total):
        findings.append((None, "meta-word-count", f"metadata says {meta['word_count']}, frames sum to {total}"))

    # Only Manim scripts declare a frame_class; a script where none does is narration-only.
    cue_schedule = any(fr.get("frame_class") for fr in frames)
    for i, fr in enumerate(frames):
        f, st = audit_frame(fr, i, cue_schedule)
        findings += f
        stats.append(st)

    mode = detect_mode(video_dir, script, mode)
    if mode in HUMANITIES_MODES:
        hf, hst = humanities_checks(frames, load_dossier(video_dir), mode)
        findings += hf
        stats.append({"humanities": hst, "mode": mode})

    if siblings:
        me = " ".join((fr.get("narration") or "") for fr in frames)
        for sib in sorted(video_dir.parent.glob("Video-*")):
            if sib == video_dir or not (sib / "script.json").exists():
                continue
            other = json.loads((sib / "script.json").read_text(encoding="utf-8"))
            ot = " ".join((fr.get("narration") or "") for fr in other.get("frames", []))
            ov = ngram_overlap(me, ot)
            if ov > 0.02:
                findings.append((None, "sibling-overlap",
                                 f"{sib.name}: {100*ov:.1f}% of 8-grams shared — check for re-teaching"))
    return findings, stats, script, cue_schedule


def main():
    ap = argparse.ArgumentParser(description="Mechanical script-level audit (the half of script review that is arithmetic).")
    ap.add_argument("video_dir", nargs="?", help="pipeline/<L>/Video-N")
    ap.add_argument("--siblings", action="store_true", help="also n-gram against sibling videos")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--self-test", action="store_true", help="prove every check can FAIL")
    ap.add_argument("--mode", default="auto", choices=["auto", "folio", "math", "technical"],
                    help="script mode (auto: inferred; folio adds the dossier checks)")
    ap.add_argument("--argument", action="store_true",
                    help="check Video-N/argument.json only (humanities review round 0)")
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    if not a.video_dir:
        ap.error("video_dir required (or --self-test)")

    if a.argument:
        problems = audit_argument(Path(a.video_dir), a.mode)
        print(f"argument-map audit — {a.video_dir}")
        for p in problems:
            print(f"    CHECK [argument-map] {p}")
        print("\n  CLEAN — mechanical argument-map checks passed." if not problems else
              f"\n  {len(problems)} finding(s). Judgement (is the thesis a claim, is anything "
              "substantive cut, does each P-id say what it is cited for) is the round-0 reviewer's.")
        return 1 if problems else 0

    findings, stats, script, cue_schedule = audit(Path(a.video_dir), a.siblings, a.mode)
    hum = next((s for s in stats if "humanities" in s), None)
    stats = [s for s in stats if "humanities" not in s]
    if a.json:
        print(json.dumps({"narration_only": not cue_schedule,
                          "humanities": hum,
                          "findings": [{"frame": f, "check": c, "detail": d} for f, c, d in findings],
                          "stats": stats}, indent=2))
        return 1 if findings else 0

    print(f"script audit — {a.video_dir}")
    print(f"  {len(script.get('frames', []))} frames, {script.get('metadata', {}).get('word_count', '?')} words\n")
    if cue_schedule:
        hdr = f"  {'f':>3} {'class':<7}{'cues':>5}{'1st':>6}{'maxgap':>8}{'margin':>8}{'ref/w':>7}"
        print(hdr); print("  " + "-" * (len(hdr) - 2))
        for s in stats:
            print(f"  {s['frame']:>3} {str(s.get('frame_class') or '-'):<7}{s.get('cues', 0):>5}"
                  f"{s.get('first_beat', 0):>6}{s.get('max_gap', 0):>8}"
                  f"{s.get('final_margin', 0):>8}{s.get('ref_per_word', 0):>7}")
    else:
        print("  NARRATION-ONLY script (no frame declares a frame_class: folio). The visual track is")
        print("  authored separately, so the cue, scope, colour and Tex checks do not apply.")
        print("  Metadata, sibling-overlap and spoken-text TTS checks ran.")
    if hum:
        h = hum["humanities"]
        print(f"\n  HUMANITIES ({hum['mode']}) vs dossier: ratio {h.get('dossier_ratio', '?')}x "
              f"(target ~{h.get('target_words', '?')} words), {OVERLAP_N}-gram overlap "
              f"{100 * h.get('dossier_overlap', 0):.1f}%, sentences mean {h.get('sentence_mean', '?')} "
              f"words / {100 * h.get('sentence_long_share', 0):.0f}% over {CADENCE_LONG}")
        if h.get("movements"):
            print(f"  movements (s): {h['movements']}")
        if h.get("common_frames"):
            print(f"  frames tagged \"common\" (reviewer verifies each): {h['common_frames']}")
    if findings:
        order = {"BLOCK": 0, "CHECK": 1}
        ranked = sorted(findings, key=lambda x: order.get(SEVERITY.get(x[1], "CHECK"), 1))
        nb = sum(1 for _, c, _ in findings if SEVERITY.get(c, "CHECK") == "BLOCK")
        print(f"\n  {len(findings)} FINDING(S) — {nb} BLOCK, {len(findings)-nb} CHECK:")
        for fr, check, detail in ranked:
            where = f"frame {fr}" if fr is not None else "video"
            print(f"    {SEVERITY.get(check, 'CHECK'):<5} [{check}] {where}: {detail}")
    else:
        print("\n  CLEAN — every mechanical check passed.")
    if cue_schedule:
        print("\n  The first-beat and gap figures are derived from CUE PHRASES only. An element")
        print("  present from t=0 (a held title card, a problem line in the top band) is invisible")
        print("  to them, so a 'dead-open' on a frame that opens on a held board is a false")
        print("  positive — confirm against the reference before acting on it.")
    print("\n  Not checked here (needs judgement — that is the reviewer's job): the mathematics,")
    print("  fit/scroll simulation (scripts/utils/manim_probe.py), source fidelity, pedagogy,")
    print("  narration↔visual agreement, and mannered prose.")
    return 1 if findings else 0


def self_test():
    """A check that has never fired is untested, not reassuring. Prove each one fires."""
    def one(name, frame, want, cue_schedule=True):
        f, _ = audit_frame(frame, 0, cue_schedule)
        got = {c for _, c, _ in f}
        ok = want in got
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} expect '{want}'  got {sorted(got) or '[]'}")
        return ok

    good = "We begin with the product rule and then we prove it carefully today."
    cases = [
        ("duplicate cue", {"number": 0, "narration": "the rule and the rule again here now",
                           "visual": {"reference": 'On "the rule" a box.'}}, "cue-ambiguous"),
        ("missing cue", {"number": 0, "narration": good,
                         "visual": {"reference": 'On "nowhere in narration" a box.'}}, "cue-not-found"),
        ("substring cue", {"number": 0, "narration": "alpha beta gamma delta epsilon zeta eta theta",
                           "visual": {"reference": 'On "alpha" a box. On "alpha beta" a chip.'}}, "cue-substring"),
        ("zero margin", {"number": 0, "narration": good,
                         "visual": {"reference": 'On "carefully today" a box.'}}, "margin-zero"),
        ("dead span", {"number": 0, "narration": "start " + " ".join(["filler"] * 40) + " end here",
                       "visual": {"reference": 'On "start" a box. On "end here" a chip.'}}, "dead-span"),
        ("scope width", {"number": 0, "narration": good,
                         "visual": {"reference": 'On "product rule" a line measured 3.75 u wide.'}}, "scope-measured_width"),
        ("colour word", {"number": 0, "narration": good,
                         "visual": {"reference": 'On "product rule" a GREEN box.'}}, "colour-word"),
        ("tex unsafe", {"number": 0, "narration": good,
                        "visual": {"reference": """On "product rule" a tag 'v = x^n'."""}}, "tex-unsafe"),
        ("raw glyph", {"number": 0, "narration": good,
                       "visual": {"reference": """On "product rule" a tag 'let Δt → 0'."""}}, "raw-glyph"),
        ("tts digits", {"number": 0, "narration": "We take 3 steps and then we finish the proof.",
                        "visual": {"reference": 'On "We take" a box. On "finish the proof" a tag.'}}, "tts-digits"),
        ("math frame, no cues", {"number": 0, "frame_class": "math", "narration": good,
                                 "visual": {"reference": "A box appears."}}, "no-cues"),
        # A listed cue: only the FIRST quote is parsed, so "and then we prove" is never
        # scheduled and the gap it covers would be reported as the author's dead span.
        ("listed cue", {"number": 0, "narration": good,
                        "visual": {"reference": 'On "We begin", "and then we prove" a box. '
                                                'On "carefully" a tag.'}}, "cue-list-unscheduled"),
    ]
    # A narration-only frame skips the reference checks but must still get the TTS checks.
    narr_only = [
        ("narration-only digits", {"number": 0, "narration": "In 323 the king died at Babylon.",
                                   "visual": {"reference": "Placeholder: GREEN hills."}}, "tts-digits"),
    ]
    print("self-test — each case must FIRE its detector:")
    ok = all(one(n, fr, w) for n, fr, w in cases)
    ok = all(one(n, fr, w, cue_schedule=False) for n, fr, w in narr_only) and ok
    # NOTE: the clean control needs a real tail after its final cue, and no cue phrase may
    # repeat anywhere in the narration. Two earlier versions of this fixture were wrong (one
    # ended on its final cue, one duplicated "We begin") and the detectors correctly fired on
    # both — the checks were right and the test case was not.
    clean_narr = (good + " That identity is the one we will lean on for the rest of this "
                  "video, and it is worth stating plainly before we go any further at all.")
    # The clean control also carries a LISTED quote that is an on-screen label — "Leibniz
    # rule" is written on the board and never spoken — which must NOT fire
    # cue-list-unscheduled. That is the legitimate half of the CUE_LIST_RE shape.
    clean = {"number": 0, "narration": clean_narr,
             "visual": {"reference": 'On "We begin" a title. On "the product rule", '
                                     '"Leibniz rule" a box. '
                                     'On "prove it carefully" a tag. On "lean on" a chip.'}}
    f, _ = audit_frame(clean, 0)
    neg = not f
    print(f"  {'PASS' if neg else 'FAIL'}  {'negative control':<22} expect no findings  got {[c for _, c, _ in f] or '[]'}")
    # The false positive narration-only mode removes: a clean narration-only frame whose placeholder
    # reference has no cues and a colour word must produce NOTHING.
    wc = {"number": 0, "narration": clean_narr,
          "visual": {"type": "illustration", "reference": "Placeholder: a GREEN hillside at dawn."}}
    f2, _ = audit_frame(wc, 0, cue_schedule=False)
    neg2 = not f2
    print(f"  {'PASS' if neg2 else 'FAIL'}  {'narration-only control':<22} expect no findings  got {[c for _, c, _ in f2] or '[]'}")
    neg = neg and neg2
    hok = _humanities_self_test()
    ok = ok and hok
    print("\n" + ("all detectors fire and the clean control stays silent" if ok and neg else "SELF-TEST FAILED"))
    return 0 if (ok and neg) else 1


def _humanities_self_test():
    """Each dossier check FIRES on a known-bad script and stays QUIET on the clean one."""
    import copy
    pad = "\n\n".join(
        " ".join(f"ledger{i}x{j}" for j in range(95)) + "." for i in range(40))
    dossier = (
        "# Test dossier\n\n"
        "The Athenian assembly met on the Pnyx about forty times a year, and any adult male "
        "citizen could attend, speak and vote on the business of the day.\n\n"
        "Pericles introduced pay for jurors around 450 BC, and some 6,000 citizens were "
        "enrolled each year for the courts, which heard cases from dawn to dusk.\n\n"
        "Women could not vote, could not speak in the assembly, and could not own significant "
        "property in their own names under Athenian law.\n\n"
        "Aeschylus, Sophocles, Euripides, Aristophanes, Phrynichus, Agathon, Ion, Achaeus, "
        "Thespis and Choerilus all competed at the festival.\n\n" + pad)
    sents = [
        "Citizens climb the rocky hill before dawn and wait for the herald.",
        "The herald opens the meeting and invites any man to speak.",
        "Pericles argues that jurors deserve a daily wage for their time.",
        "Poor farmers now sit beside rich landowners in the crowded courts.",
        "Women run the household but stay outside every public decision.",
        "The Athenian courts hear quarrels over land, debts and inheritance.",
        "Each verdict comes from ordinary men voting with bronze tokens.",
        "The Pnyx holds a great crowd, yet few ever rise to talk.",
        "Speakers learn to persuade a crowd that can shout them down.",
        "Theatre audiences bring those same habits of judgment to the plays.",
    ]

    def para(k, n=7):
        return " ".join(sents[(k + i) % len(sents)] for i in range(n))

    frames = [{"number": 0, "movement": 0, "sources": ["P1"], "narration": para(0, 6)}]
    for m in (1, 2, 3):
        for r in range(3):
            frames.append({"number": len(frames), "movement": m,
                           "sources": [f"P{m}", "P2"], "narration": para(m * 3 + r, 9)})

    def run(fr):
        got, _ = humanities_checks(fr, dossier, "folio")
        return {c for _, c, _ in got}, got

    def mutate(fn):
        fr = copy.deepcopy(frames)
        fn(fr)
        return fr

    def prepend(i, text):
        return lambda fr: fr[i].__setitem__("narration", text + " " + fr[i]["narration"])

    bad = [
        ("verbatim run", prepend(1, "In those years any adult male citizen could attend, "
                                    "speak and vote on the business of the day."),
         "dossier-verbatim"),
        ("verbatim, numbers", prepend(2, "Records show some six thousand citizens were enrolled "
                                         "each year for the courts, which heard cases from "
                                         "dawn to dusk."), "dossier-verbatim"),
        ("overlap", lambda fr: [f.__setitem__("narration", dossier.split("\n\n")[k % 3 + 1])
                                for k, f in enumerate(fr)], "dossier-overlap"),
        ("ratio", lambda fr: fr.extend(copy.deepcopy(fr * 5)), "dossier-ratio"),
        ("unsourced name+number", prepend(3, "Cleisthenes built ten tribes in five hundred "
                                             "and eight."), "unsourced-figure"),
        ("missing sources", lambda fr: fr[4].pop("sources"), "sources"),
        ("unknown P-id", lambda fr: fr[4].__setitem__("sources", ["P999"]), "sources"),
        ("filler opener", prepend(5, "So, the vote mattered."), "lecture-filler"),
        ("rhetorical question", prepend(5, "Why did it matter so much?"), "lecture-filler"),
        ("long sentences", lambda fr: [f.__setitem__("narration", " ".join(
            [f["narration"].replace(".", " and").rstrip(" and") + "."])) for f in fr],
         "sentence-cadence"),
        ("no movement field", lambda fr: [f.pop("movement") for f in fr], "movement-shape"),
        ("two movements", lambda fr: [f.__setitem__("movement", min(f["movement"], 2))
                                      for f in fr], "movement-shape"),
    ]
    quiet = [
        ("quoted run", prepend(1, 'The law was plain: "any adult male citizen could attend, '
                                  'speak and vote on the business of the day."'),
         "dossier-verbatim"),
        ("name list", prepend(1, "Competitors included Aeschylus, Sophocles, Euripides, "
                                 "Aristophanes, Phrynichus, Agathon, Ion, Achaeus, Thespis and "
                                 "Choerilus."), "dossier-verbatim"),
        ("common-tagged frame", lambda fr: (prepend(3, "Cleisthenes built ten tribes in five "
                                                       "hundred and eight.")(fr),
                                            fr[3]["sources"].append("common")),
         "unsourced-figure"),
        ("quoted question", prepend(5, 'The herald asks, "Who wishes to speak?"'),
         "lecture-filler"),
    ]
    print("\nhumanities dossier checks — each bad case must FIRE, each control stay QUIET:")
    ok = True
    got0, raw0 = run(frames)
    good = not got0
    print(f"  {'PASS' if good else 'FAIL'}  {'clean humanities script':<24} expect no findings  "
          f"got {sorted(got0) or '[]'}{'' if good else ' ' + str(raw0[:3])}")
    ok = ok and good
    for name, fn, want in bad:
        got, _ = run(mutate(fn))
        hit = want in got
        print(f"  {'PASS' if hit else 'FAIL'}  {name:<24} expect '{want}'  got {sorted(got) or '[]'}")
        ok = ok and hit
    for name, fn, silent in quiet:
        got, _ = run(mutate(fn))
        q = silent not in got
        print(f"  {'PASS' if q else 'FAIL'}  {name:<24} expect NO '{silent}'  got {sorted(got) or '[]'}")
        ok = ok and q
    return ok


if __name__ == "__main__":
    sys.exit(main())
