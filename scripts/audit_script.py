#!/usr/bin/env python3
"""
Mechanical script-level audit for a Video-N/script.json (math / technical).

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
        _audit_tts(f, num, narration)
        return f, st

    cues = CUE_RE.findall(ref)
    st["cues"] = len(cues)
    if not cues:
        f.append((num, "no-cues", "reference schedules nothing"))
        _audit_tts(f, num, narration)
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

    _audit_tts(f, num, narration)
    return f, st


def _audit_tts(f, num, narration):
    """TTS safety of the SPOKEN text: applies to every mode, narration-only included."""
    issues = []
    try:
        from scripts.utils.narration_check import find_tts_issues
        issues = find_tts_issues(narration)
    except Exception as e:                                    # noqa: BLE001
        f.append((num, "tts-check-unavailable", str(e)[:60]))
    for cat, tok in issues:
        f.append((num, f"tts-{cat}", repr(tok)[:50]))
    digits = re.findall(r"\d", narration)
    if digits:
        f.append((num, "tts-digits", f"{len(digits)} Arabic numeral(s) in spoken text"))


def ngram_overlap(a: str, b: str, n=8):
    """Fraction of a's n-grams also present in b."""
    aw, bw = norm(a).split(), norm(b).split()
    if len(aw) < n:
        return 0.0
    A = {tuple(aw[i:i + n]) for i in range(len(aw) - n + 1)}
    B = {tuple(bw[i:i + n]) for i in range(len(bw) - n + 1)}
    return len(A & B) / len(A) if A else 0.0


def audit(video_dir: Path, siblings=False):
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
    a = ap.parse_args()

    if a.self_test:
        return self_test()
    if not a.video_dir:
        ap.error("video_dir required (or --self-test)")

    findings, stats, script, cue_schedule = audit(Path(a.video_dir), a.siblings)
    if a.json:
        print(json.dumps({"narration_only": not cue_schedule,
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
        print("  NARRATION-ONLY script (no frame declares a frame_class). The visual track is")
        print("  authored separately, so the cue, scope, colour and Tex checks do not apply.")
        print("  Metadata, sibling-overlap and spoken-text TTS checks ran.")
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
    print("\n" + ("all detectors fire and the clean control stays silent" if ok and neg else "SELF-TEST FAILED"))
    return 0 if (ok and neg) else 1


if __name__ == "__main__":
    sys.exit(main())
