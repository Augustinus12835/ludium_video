"""
Provider-agnostic pronunciation alias library (replaces the ElevenLabs pronunciation dictionary).

Spoken narration is sent to TTS VERBATIM, whichever provider voices it (ElevenLabs, Cartesia,
HeyGen).
A token that voices mispronounce is therefore fixed IN THE NARRATION TEXT: the script writes the
alias ("sinch", "mew", "cap-M"), and the subtitle / burned-in-caption compactor
(subtitle_compact.py) maps the alias back to its display form ("sinh", "μ", "CAPM"),
preserving word timing. Nothing provider-specific is involved, so the narration stays the single
source of truth for audio, word timestamps, anchors and captions.

Three layers read this table — keep them in sync by editing ONLY the table:
  - tts_rules.py         renders it into the script-generation prompts (alias_prompt_block)
  - narration_check.py   the pre-TTS gate flags a WRITTEN form that reached spoken text
                         (find_unaliased → category "alias")
  - subtitle_compact.py  maps a SPOKEN alias back to its display form (display_for)

Entry fields:
  written    the form the narration must NOT contain (case-sensitive, whole token)
  spoken     what the narration says instead
  display    subtitle form of `spoken`; None = a plain expansion that is already good display
             text ("that is" for i.e.) and is never compacted
  scope      "all" (every mode) or "math" (Manim math/technical/recitation videos only —
             "chi" in a history video is a word, not a statistic)
  component  also matches as one part of a hyphen compound ("mu-hat" → "mew-hat",
             "chi-squared" → "kai-squared"); otherwise only a standalone token (possessive 's allowed)
  not_before words after which the spoken form is ordinary English and must NOT be compacted
             (none needed yet — see the note on "pie" above the table)

What does NOT belong here: raw Greek letters and math symbols (they never reach narration —
the gate's `greek` / `math_symbol` categories reject them), numbers, initialisms read
letter-by-letter, and differentials — those have their own rules and detectors.

Add an entry when a voice mispronounces a token that recurs (a one-off proper noun can simply be
reworded). A spoken form that must be compacted back has to be ONE token (no spaces) — the
compactor works per word; --self-test enforces it.

CLI:  python -m scripts.utils.tts_aliases --self-test | --table [--math]
"""

from dataclasses import dataclass
import re
import sys
from typing import Dict, FrozenSet, List, Optional, Tuple


@dataclass(frozen=True)
class Alias:
    written: str
    spoken: str
    display: Optional[str]
    scope: str = "all"
    component: bool = False
    not_before: FrozenSet[str] = frozenset()
    note: str = ""
    icase: bool = False      # gate matches any case ("Mu-hat", sentence-initial "Pi")


def _hyp(written, spoken, note=""):
    return Alias(written, spoken, written, "math", note=note)


ALIASES: List[Alias] = [
    # A deliberately SMALL set: only mispronunciations actually heard, and only with a spoken
    # form that is never ordinary English — "pie" for pi and "eye" for i are OUT ("the size of
    # the pie", "to the eye" would be corrupted in subtitles). Drop entries when your voice
    # stops needing them; add ones it needs.
    # --- hyperbolic functions: voices drop the trailing "h" and read them as trig functions
    _hyp("sinh", "sinch", "heard as 'sine of h'"),
    _hyp("cosh", "kosh"),
    _hyp("tanh", "tanch"),
    _hyp("coth", "koth"),
    _hyp("sech", "sheck"),
    _hyp("csch", "co-sheck"),
    Alias("arcsinh", "arc sinch", None, "math"),
    Alias("arccosh", "arc kosh", None, "math"),
    Alias("arctanh", "arc tanch", None, "math"),
    # --- spelled-out Greek letter names that voices misread
    Alias("mu", "mew", "mu", "math", component=True, icase=True, note="read 'moo'"),
    Alias("rho", "roe", "rho", "math", component=True, icase=True),
    Alias("chi", "kai", "chi", "math", component=True, icase=True, note="read 'chee'"),
    # --- single letters
    Alias("V", "vee", "V", "math",
          note="standalone V after a sibilant ('times V') is heard as 'five'"),
    # --- names (Euler was read "woo-ler"; a cloned voice read every "Thessal-" stem as "Sicily")
    Alias("Euler", "Oiler", "Euler", note="heard as 'woo-ler'"),
    Alias("Eulerian", "Oilerian", "Eulerian"),
    Alias("Thessaly", "Tessaly", "Thessaly", note="read as 'Sicily'"),
    Alias("Thessalian", "Tessalian", "Thessalian"),
    Alias("Thessalians", "Tessalians", "Thessalians"),
    # --- initialisms said as a word plus a letter
    Alias("CAPM", "cap-M", "CAPM", note="capital asset pricing model"),
    # --- abbreviations: write the words (display = the words; never compacted)
    Alias("iff", "if and only if", None),
    Alias("i.e.", "that is", None),
    Alias("e.g.", "for example", None),
    Alias("etc.", "et cetera", None),
]


# ---------------------------------------------------------------------------
# Gate: written forms that reached spoken narration
# ---------------------------------------------------------------------------

def _written_regex(a: Alias) -> "re.Pattern":
    w = re.escape(a.written)
    if a.written.endswith("."):
        tail = ""                                   # "etc." / "i.e." end in their own dot
    elif a.component:
        tail = r"(?![A-Za-z]|\.[A-Za-z])"           # "mu-hat" ok, "i.e." is not "i"
    elif a.written.isupper():
        tail = r"(?:s|es)?(?![A-Za-z]|-[A-Za-z]|\.[A-Za-z])"  # initialism plural ("CAPMs")
    else:
        tail = r"(?![A-Za-z]|-[A-Za-z]|\.[A-Za-z])"  # standalone only, not inside a compound
    head = r"(?<![A-Za-z'’])" if a.component else r"(?<![A-Za-z'’-])"
    return re.compile(head + w + tail, re.I if a.icase else 0)


_GATE = [(a, _written_regex(a)) for a in ALIASES]


def find_unaliased(text: str, math: bool = True) -> List[Tuple[str, str]]:
    """[(written token, alias to write instead)] for every written form in `text`.
    math=False skips scope="math" entries (humanities / Latin narration)."""
    out = []
    for a, rx in _GATE:
        if a.scope == "math" and not math:
            continue
        if rx.search(text or ""):
            out.append((a.written, a.spoken))
    return out


# ---------------------------------------------------------------------------
# Compaction: spoken alias → display form
# ---------------------------------------------------------------------------

_DISPLAY: Dict[str, Alias] = {a.spoken.lower(): a for a in ALIASES
                              if a.display is not None and a.display != a.spoken}


def _case_like(src: str, display: str) -> str:
    """Carry a sentence-initial capital over ("Mew-hat" → "Mu-hat"); names keep their own case."""
    if src[:1].isupper() and display[:1].islower() and len(display) > 1:
        return display[0].upper() + display[1:]
    return display


def _lookup(part: str, math_mode: bool, standalone: bool) -> Optional[str]:
    a = _DISPLAY.get(part.lower())
    if a is None or (a.scope == "math" and not math_mode):
        return None
    if not standalone and not a.component:
        return None
    if part != a.spoken and part.lower() != a.spoken.lower():
        return None
    return _case_like(part, a.display)


def display_for(core: str, math_mode: bool, next_word: str = "") -> Optional[str]:
    """Display form of one spoken token (punctuation already stripped), or None if it carries
    no alias. Handles a possessive ("cap-M's" → "CAPM's") and hyphen compounds
    ('mew-hat' → 'mu-hat', 'kai-squared' → 'chi-squared'). `next_word` guards ordinary-English uses."""
    suffix = ""
    for s in ("'s", "’s"):
        if core.endswith(s):
            core, suffix = core[:-len(s)], s
            break
    whole = _lookup(core, math_mode, standalone=True)
    if whole is not None:
        a = _DISPLAY[core.lower()]
        if next_word.lower().strip(".,;:!?\"'’”)") in a.not_before:
            return None
        return whole + suffix
    if "-" in core:
        parts = core.split("-")
        mapped = [(_lookup(p, math_mode, standalone=False) or p) for p in parts]
        if mapped != parts:
            return "-".join(mapped) + suffix
    return None


# ---------------------------------------------------------------------------
# Prompt rendering (tts_rules.py)
# ---------------------------------------------------------------------------

def alias_prompt_block(math: bool) -> str:
    """The alias table as a script-prompt bullet. math=False lists scope="all" entries only."""
    rows = [a for a in ALIASES if math or a.scope == "all"]
    say = [a for a in rows if a.display is not None]
    words = [a for a in rows if a.display is None]
    lines = [
        "   - **Pronunciation aliases — write the ALIAS, never the written form, in `narration`.**",
        "     Narration goes to the voice verbatim (no pronunciation dictionary on any provider).",
        "     These tokens are mispronounced as written, so the narration spells the alias and the",
        "     subtitles/captions convert it back automatically — the viewer still reads `sinh`,",
        "     `μ`, `CAPM`. The pre-TTS gate rejects the written form. On-screen text keeps the",
        "     normal written form. Everything else (names, single letters) is written normally.",
        "     | Written | Narration says |",
        "     |---------|----------------|",
    ]
    for a in say:
        extra = " (also inside hyphen compounds)" if a.component else ""
        lines.append(f"     | `{a.written}` | \"{a.spoken}\"{extra} |")
    if words:
        lines.append("     Write these out in words: " + "; ".join(
            f"`{a.written}` → \"{a.spoken}\"" for a in words) + ".")
    if math:
        lines.append("     Compounds keep the alias: `μ̂` → \"mew-hat\", `μ_1` → \"mew-one\", "
                     "`χ²` → \"kai-squared\", `ρ_xy` → \"roe-X-Y\".")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test() -> int:
    errs = []
    for a in ALIASES:
        if a.display is not None and a.display != a.spoken and " " in a.spoken:
            errs.append(f"{a.written}: compacted spoken form must be one token: {a.spoken!r}")
        if a.scope not in ("all", "math"):
            errs.append(f"{a.written}: bad scope {a.scope}")
    spoken = [a.spoken.lower() for a in ALIASES if a.display is not None and a.display != a.spoken]
    if len(spoken) != len(set(spoken)):
        errs.append("duplicate spoken alias")
    gate_cases = [
        ("the hyperbolic sine, sinh of x", True, ["sinh"]),
        ("arcsinh of x", True, ["arcsinh"]),
        ("mu-hat and mu-one", True, ["mu"]),
        ("Mu-hat is unbiased", True, ["mu"]),
        ("mu-hat and mu-one", False, []),
        ("chi-square with one degree", True, ["chi"]),
        ("the CAPM says", False, ["CAPM"]),
        ("two CAPMs disagree", False, ["CAPM"]),
        ("I think Rome, etc.", False, ["etc."]),
        ("i.e. the limit, b i", True, ["i.e."]),
        ("times V, and V-one", True, ["V"]),
        ("Henry V was king", False, []),
        ("Thessaly and the Thessalians", False, ["Thessaly", "Thessalians"]),
        ("By Euler's definition, an Eulerian path", False, ["Euler", "Eulerian"]),
        ("a mew-hat, sinch of x, the cap-M, roe-one", True, []),
        ("the machine learning muon", True, []),
    ]
    for text, math, want in gate_cases:
        got = [w for w, _ in find_unaliased(text, math)]
        if got != want:
            errs.append(f"gate {text!r} (math={math}): got {got}, want {want}")
    disp_cases = [
        ("sinch", True, "", "sinh"), ("Sinch", True, "", "Sinh"), ("sinch", False, "", None),
        ("mew-hat", True, "", "mu-hat"), ("Mew", True, "", "Mu"), ("mew", False, "", None),
        ("cap-M", False, "", "CAPM"), ("cap-M's", False, "", "CAPM's"),
        ("Oiler's", False, "", "Euler's"), ("Oilerian", True, "", "Eulerian"),
        ("kai-squared", True, "", "chi-squared"), ("roe", True, "", "rho"),
        ("hat-mew", True, "", "hat-mu"), ("eye", True, "", None), ("pie", True, "", None),
        ("vee", True, "", "V"), ("vee", False, "", None), ("Tessaly", False, "", "Thessaly"),
        ("Tessalians", False, "", "Thessalians"),
    ]
    for core, math, nxt, want in disp_cases:
        got = display_for(core, math, nxt)
        if got != want:
            errs.append(f"display {core!r} (math={math}, next={nxt!r}): got {got!r}, want {want!r}")
    for e in errs:
        print("FAIL", e)
    print(f"tts_aliases self-test: {len(ALIASES)} entries, "
          f"{len(gate_cases) + len(disp_cases)} cases, {len(errs)} failure(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    print(alias_prompt_block(math="--math" in sys.argv))
