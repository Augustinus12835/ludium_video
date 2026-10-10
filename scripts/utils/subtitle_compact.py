"""
Inverse text normalization for subtitles: spoken words -> compact display form.

Narration is written TTS-safe (zero digits, symbols spelled out — see
tts_rules.py), and subtitles are built verbatim from those spoken words, which
makes them tiresome to read ("three hundred seventy-seven point four one",
"delta X", "less than or equal to"). This module converts the word-timestamp
stream BACK to compact written form — Arabic numerals and Unicode math — while
preserving timing: a merged phrase keeps the start of its first word and the
end of its last, so cue timing never shifts.

Used by generate_subtitles.py (on by default; --no-compact disables).
Math-notation rules (Greek symbols, dx, subscripts, ≠, ∞) only fire when the
video is Manim-rendered (math/technical — frames/frame_*_manim.py exists); number/percent/era rules apply to every mode.

Conversions (conservative by design — a false positive is worse than a missed
compaction):
  - multi-word cardinals/decimals -> digits: "three hundred seventy-seven point
    four one" -> 377.41; "thirty thousand" -> 30,000. Single small number words
    (zero–nine) stay words ("one of the reasons" is prose).
  - year fusion: "nineteen seventy-three" -> 1973, "ten sixty six" -> 1066,
    "nineteen oh five" -> 1905, "twenty twenty-four" -> 2024.
  - "N percent" -> N%, "N degrees" -> N° (not before "of").
  - ordinals: "twenty-third" -> 23rd, "thirteenth" -> 13th (guarded against
    fractions: "a twentieth of" stays words; first..ninth always stay words).
  - powers of ten: "ten to the minus thirty-four" -> 10⁻³⁴.
  - phrases: ≤ ≥ ± √ (all modes); ≠ ∞ (math mode).
  - math mode: Greek names -> symbols ("delta X"/"delta-X" -> ΔX, "lambda two"
    -> λ₂), differentials ("d y over d x" -> dy/dx, "d squared y" -> d²y,
    "partial f" -> ∂f), letter subscripts ("X-zero" -> X₀,
    "epsilon-naught"/"v naught" -> ε₀/v₀), "squared"/"cubed"
    -> ²/³, unary "negative five" -> -5, "over" between math tokens -> "/".
  - pronunciation aliases back to the written form (scripts/utils/tts_aliases.py): every
    mode — "Oiler" -> Euler, "Tessaly" -> Thessaly, "cap-M" -> CAPM; math mode also —
    "sinch" -> sinh, "mew-hat" -> μ̂, "vee" -> V.
  - era letters after a number/century: "B C" -> BC, "A D" -> AD; and "A D"
    BEFORE a year, the usual English order ("A D eighty-six" -> AD 86) — only
    outside math mode, and only when a digit year follows directly.

CLI smoke test:  python -m scripts.utils.subtitle_compact "text ..." [--math]
"""

import sys
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

_UNITS = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
          'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9}
_TEENS = {'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
          'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
          'eighteen': 18, 'nineteen': 19}
_TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
         'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
_SCALES = {'hundred': 100, 'thousand': 1000, 'million': 10**6,
           'billion': 10**9, 'trillion': 10**12}
_OH = {'oh'}

_ORD_UNITS = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
              'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9}
_ORD_TEENS = {'tenth': 10, 'eleventh': 11, 'twelfth': 12, 'thirteenth': 13,
              'fourteenth': 14, 'fifteenth': 15, 'sixteenth': 16,
              'seventeenth': 17, 'eighteenth': 18, 'nineteenth': 19}
_ORD_TENS = {'twentieth': 20, 'thirtieth': 30, 'fortieth': 40, 'fiftieth': 50,
             'sixtieth': 60, 'seventieth': 70, 'eightieth': 80,
             'ninetieth': 90}
_ORD_ALL = {**_ORD_UNITS, **_ORD_TEENS, **_ORD_TENS}

# Greek names -> lowercase symbols. delta maps to Δ: in this corpus a spoken
# "delta" is an increment (delta X = ΔX) essentially always; formal ε-δ limit
# narration is rare enough that Δ is the right default.
_GREEK = {'alpha': 'α', 'beta': 'β', 'gamma': 'γ', 'delta': 'Δ',
          'epsilon': 'ε', 'zeta': 'ζ', 'eta': 'η', 'theta': 'θ',
          'iota': 'ι', 'kappa': 'κ', 'lambda': 'λ', 'mu': 'μ', 'nu': 'ν',
          'xi': 'ξ', 'rho': 'ρ', 'sigma': 'σ', 'tau': 'τ', 'upsilon': 'υ',
          'phi': 'φ', 'chi': 'χ', 'psi': 'ψ', 'omega': 'ω', 'pi': 'π'}
# A capitalized Greek name MID-sentence is deliberate (Lambda Q = ΛQ in
# diagonalization, Sigma V = ΣV in SVD); sentence-initial capitals are just
# capitalization and stay lowercase symbols.
_GREEK_UPPER = {'gamma': 'Γ', 'delta': 'Δ', 'theta': 'Θ', 'lambda': 'Λ',
                'xi': 'Ξ', 'pi': 'Π', 'sigma': 'Σ', 'phi': 'Φ',
                'psi': 'Ψ', 'omega': 'Ω'}

# Pronunciation aliases ("sinch", "mew", "Oiler", "cap-M") -> display form: the alias library
# (scripts/utils/tts_aliases.py) is the single table shared with the prompts and the pre-TTS gate.
from scripts.utils.tts_aliases import display_for as _alias_display  # noqa: E402

# Function names are finished notation (the old hyperbolic word map marked them mathy); Greek
# names and letters stay unmarked so _pass_math_tokens still turns "mu-one" into μ₁.
_FUNCTION_DISPLAYS = {'sinh', 'cosh', 'tanh', 'coth', 'sech', 'csch'}

# A subscript word after a letter or Greek name: the digit words plus "naught" ("epsilon-naught" ->
# ε₀, "v naught" -> v₀; physics narration says "naught" ~5x as often as "zero"). "naught" is only
# ever read as a subscript right after a symbol, so prose ("all for naught") is untouched.
_SUB_WORDS = dict(_UNITS, naught=0)

_SUB_DIGITS = {0: '₀', 1: '₁', 2: '₂', 3: '₃', 4: '₄',
               5: '₅', 6: '₆', 7: '₇', 8: '₈', 9: '₉'}
_SUP_MAP = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵',
            '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '-': '⁻'}

# Words that may precede "B C"/"A D" era letters (besides a numeric token).
_ERA_PRECEDERS = {'century', 'centuries', 'era', 'year', 'twenties',
                  'thirties', 'forties', 'fifties', 'sixties', 'seventies',
                  'eighties', 'nineties'}

_LEAD_CHARS = '"\'([{“‘«¿¡—–-…'
_TRAIL_CHARS = '.,;:!?"\')]}”’»…—–'
_SENT_END = set('.!?…')


# ---------------------------------------------------------------------------
# Token plumbing
# ---------------------------------------------------------------------------

class _Tok:
    __slots__ = ('lead', 'core', 'trail', 'start', 'end', 'mathy')

    def __init__(self, lead, core, trail, start, end, mathy=False):
        self.lead, self.core, self.trail = lead, core, trail
        self.start, self.end, self.mathy = start, end, mathy

    @property
    def text(self):
        return self.lead + self.core + self.trail

    def __repr__(self):
        return f'_Tok({self.text!r})'


def _split_punct(text: str) -> Tuple[str, str, str]:
    i, j = 0, len(text)
    while i < j and text[i] in _LEAD_CHARS:
        i += 1
    while j > i and text[j - 1] in _TRAIL_CHARS:
        j -= 1
    return text[:i], text[i:j], text[j:]


def _make_toks(words: List[Dict]) -> List[_Tok]:
    toks = []
    for w in words:
        lead, core, trail = _split_punct(w['word'].strip())
        toks.append(_Tok(lead, core, trail, w['start'], w['end']))
    return toks


def _sentence_initial(toks: List[_Tok], i: int) -> bool:
    if i == 0:
        return True
    prev = toks[i - 1]
    return bool(_SENT_END & set(prev.trail))


def _merge(toks: List[_Tok], i: int, j: int, core: str,
           mathy: bool = True) -> _Tok:
    """Replace toks[i:j] with one token; keeps first lead / last trail and the
    merged time span. Returns the new token (list modified in place)."""
    t = _Tok(toks[i].lead, core, toks[j - 1].trail,
             toks[i].start, toks[j - 1].end, mathy)
    toks[i:j] = [t]
    return t


def _clean_between(toks: List[_Tok], i: int, j: int) -> bool:
    """True if the span toks[i:j] reads as one uninterrupted phrase: no
    trailing punctuation before the last token, no leading punctuation after
    the first."""
    for k in range(i, j - 1):
        if toks[k].trail:
            return False
    for k in range(i + 1, j):
        if toks[k].lead:
            return False
    return True


def _hyphen_parts(core: str) -> List[str]:
    return core.split('-') if '-' in core else [core]


def _is_single_letter(core: str) -> bool:
    return len(core) == 1 and core.isalpha() and core.isascii()


# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------

def _word_number_value(parts: List[str]) -> Optional[int]:
    """Value of a hyphen-split simple group like ['seventy','three']."""
    lo = [p.lower() for p in parts]
    if len(lo) == 1:
        w = lo[0]
        if w in _UNITS:
            return _UNITS[w]
        if w in _TEENS:
            return _TEENS[w]
        if w in _TENS:
            return _TENS[w]
        return None
    if len(lo) == 2 and lo[0] in _TENS and lo[1] in _UNITS and _UNITS[lo[1]]:
        return _TENS[lo[0]] + _UNITS[lo[1]]
    return None


class _NumParse:
    __slots__ = ('end', 'value', 'n_words', 'decimal', 'text')

    def __init__(self, end, value, n_words, decimal, text):
        self.end, self.value = end, value
        self.n_words, self.decimal, self.text = n_words, decimal, text

    @property
    def strong(self):
        return self.n_words >= 2 or self.decimal is not None or \
            (self.value is not None and self.value >= 10)


def _format_int(value: int, hundreds_only: bool) -> str:
    """Digits with thousands grouping — except year-plausible values.

    hundreds_only marks a parse whose largest scale word was 'hundred'
    ("nineteen hundred", "twelve hundred fifty") — those are year-style and
    never take a comma. "two thousand eight" (2001–2099) is a year in
    narration essentially always; "one thousand nine hundred" is a quantity
    and keeps the comma.
    """
    if value < 1000:
        return str(value)
    if hundreds_only or 2001 <= value <= 2099:
        return str(value)
    return f'{value:,}'


def _parse_cardinal(toks: List[_Tok], i: int) -> Optional[_NumParse]:
    """Parse a maximal spoken cardinal (with optional decimal tail) at i.

    Grammar: groups (units/teens/tens[-unit], or spaced tens + unit) joined
    by scale words, 'and' after a scale, and a comma directly after a scale
    ("...million, seven hundred..."). Two independent groups in a row do NOT
    combine ("nineteen seventy" is a year, not 89) — parsing stops.
    """
    n = len(toks)
    total = 0
    current = 0
    j = i
    n_words = 0
    consumed_any = False
    used_big_scale = False
    last_group: Optional[int] = None   # value of the last simple group
    # state just before an 'and' after a scale, to back out of when the
    # continuation turns out to be a second number ("sixteen hundred and
    # twelve hundred" is 1600 and 1200, not 161200)
    before_and: Optional[Tuple[int, int, int, int]] = None
    while j < n:
        t = toks[j]
        if j > i and t.lead:
            break
        low = t.core.lower()
        gv = _word_number_value(_hyphen_parts(t.core))
        if gv is not None:
            if consumed_any and last_group is not None:
                # a group directly after a group: only "tens + unit"
                # ("seventy six") combines; anything else is a new number
                if not (last_group in _TENS.values() and 1 <= gv <= 9
                        and last_group % 10 == 0):
                    break
            current += gv
            n_words += len(_hyphen_parts(t.core))
            last_group = gv
            consumed_any = True
        elif low in _SCALES and consumed_any:
            if _SCALES[low] == 100:
                if current >= 100:
                    # a second 'hundred' inside one group: the words after
                    # the 'and' start a new number — end before the 'and'
                    if before_and is not None:
                        j, total, current, n_words = before_and
                    break
                current = (current or 1) * 100
            else:
                total += (current or 1) * _SCALES[low]
                current = 0
                used_big_scale = True
            n_words += 1
            last_group = None
            # a comma directly after a big scale word continues the number;
            # after 'hundred' it ends it ("A D one hundred, five centuries
            # later" is 100 and 5, not 105)
            if _SCALES[low] > 100 and t.trail == ',' and j + 1 < n and \
                    not toks[j + 1].lead and \
                    _word_number_value(_hyphen_parts(toks[j + 1].core)) is not None:
                j += 1
                continue
        elif low == 'and' and consumed_any and last_group is None and \
                not t.trail and j + 1 < n and not toks[j + 1].lead and \
                _word_number_value(_hyphen_parts(toks[j + 1].core)) is not None:
            # "four hundred and twenty" — 'and' allowed right after a scale
            before_and = (j, total, current, n_words)
            j += 1
            continue
        else:
            break
        j += 1
        if t.trail:      # any punctuation ends the phrase (the scale-comma
            break        # case was handled above)
    if not consumed_any:
        return None
    value = total + current
    end = j

    # decimal tail: "<int> point <digit words>"
    decimal = None
    if end < n and not toks[end - 1].trail and \
            toks[end].core.lower() == 'point' and \
            not toks[end].lead and not toks[end].trail:
        k = end + 1
        digits = []
        while k < n and not toks[k].lead:
            w = toks[k].core.lower()
            if w in _OH:
                digits.append('0')
            elif w in _UNITS:
                digits.append(str(_UNITS[w]))
            else:
                break
            k += 1
            if toks[k - 1].trail:
                break
        if digits:
            decimal = ''.join(digits)
            n_words += 1 + len(digits)
            end = k

    if decimal is not None:
        text = f'{value}.{decimal}'
    else:
        text = _format_int(value, hundreds_only=not used_big_scale)
    return _NumParse(end, value, n_words, decimal, text)


def _try_year(toks: List[_Tok], i: int) -> Optional[Tuple[int, str]]:
    """Fuse '<10..29> <two-digit tail>' into a year: nineteen seventy-three
    -> 1973, ten sixty six -> 1066, nineteen oh five -> 1905, twenty
    twenty-four -> 2024. Returns (end_index, text)."""
    n = len(toks)
    a = _word_number_value(_hyphen_parts(toks[i].core))
    if a is None or not (10 <= a <= 29) or toks[i].trail or \
            i + 1 >= n or toks[i + 1].lead:
        return None
    j = i + 1
    w = toks[j].core.lower()
    # "nineteen oh five"
    if w in _OH and not toks[j].trail and j + 1 < n and not toks[j + 1].lead:
        u = _UNITS.get(toks[j + 1].core.lower())
        if u is not None:
            return j + 2, f'{a}0{u}'
        return None
    b = _word_number_value(_hyphen_parts(toks[j].core))
    if b is None or not (10 <= b <= 99):
        return None
    end = j + 1
    # spaced tens + unit tail: "ten sixty six"
    if b % 10 == 0 and b in _TENS.values() and not toks[j].trail and \
            end < n and not toks[end].lead:
        u = _UNITS.get(toks[end].core.lower())
        if u:
            b += u
            end += 1
    return end, f'{a * 100 + b}'


def _parse_ordinal(toks: List[_Tok], i: int) -> Optional[Tuple[int, int]]:
    """Single-token ordinal: 'twenty-third' -> 23, 'thirteenth' -> 13,
    'twentieth' -> 20. first..ninth are never matched (they stay words).
    Returns (end_index, value)."""
    parts = _hyphen_parts(toks[i].core)
    lo = [p.lower() for p in parts]
    if len(lo) == 2 and lo[0] in _TENS and lo[1] in _ORD_UNITS:
        return i + 1, _TENS[lo[0]] + _ORD_UNITS[lo[1]]
    if len(lo) == 1 and (lo[0] in _ORD_TEENS or lo[0] in _ORD_TENS):
        return i + 1, _ORD_ALL[lo[0]]
    return None


def _ordinal_suffix(v: int) -> str:
    if 10 <= v % 100 <= 20:
        return 'th'
    return {1: 'st', 2: 'nd', 3: 'rd'}.get(v % 10, 'th')


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------

def _pass_aliases(toks: List[_Tok], math_mode: bool) -> None:
    """Spoken pronunciation aliases back to their written form (runs FIRST, so the math passes
    then see "mu-hat" / "sinh" exactly as if the script had written them)."""
    for i, t in enumerate(toks):
        nxt = toks[i + 1].core if i + 1 < len(toks) else ''
        rep = _alias_display(t.core, math_mode, nxt)
        if rep is not None:
            if rep.lower() in _FUNCTION_DISPLAYS:
                t.mathy = True
            t.core = rep


def _pass_pow10(toks: List[_Tok]) -> None:
    """'ten to the [power of] [minus] <exp>' -> 10^k in superscript digits.
    Runs BEFORE number conversion so 'ten' is still a word here."""
    i = 0
    while i < len(toks):
        if toks[i].core.lower() == 'ten' and i + 2 < len(toks) and \
                toks[i + 1].core.lower() == 'to' and \
                toks[i + 2].core.lower() == 'the' and \
                not toks[i].trail and not toks[i + 1].trail and \
                not toks[i + 2].trail:
            j = i + 3
            if j + 1 < len(toks) and toks[j].core.lower() == 'power' and \
                    toks[j + 1].core.lower() == 'of' and \
                    not toks[j].trail and not toks[j + 1].trail:
                j += 2
            sign = ''
            if j < len(toks) and \
                    toks[j].core.lower() in ('minus', 'negative') and \
                    not toks[j].trail:
                sign = '-'
                j += 1
            exp = None
            end = j
            if j < len(toks):
                parts = _hyphen_parts(toks[j].core)
                lo = [p.lower() for p in parts]
                if len(lo) == 1 and lo[0] in _ORD_ALL:
                    exp, end = _ORD_ALL[lo[0]], j + 1
                elif len(lo) == 2 and lo[0] in _TENS and lo[1] in _ORD_UNITS:
                    exp, end = _TENS[lo[0]] + _ORD_UNITS[lo[1]], j + 1
                else:
                    np = _parse_cardinal(toks, j)
                    if np and np.decimal is None and np.value < 1000:
                        exp, end = np.value, np.end
            if exp is not None and _clean_between(toks, i, end):
                sup = ''.join(_SUP_MAP[c] for c in sign + str(exp))
                _merge(toks, i, end, '10' + sup)
        i += 1


def _attach_unit(toks: List[_Tok], i: int) -> None:
    """toks[i] is a converted number; absorb a following percent/degrees."""
    if i + 1 >= len(toks) or toks[i].trail or toks[i + 1].lead:
        return
    w = toks[i + 1].core.lower()
    after = toks[i + 2].core.lower() if i + 2 < len(toks) else ''
    if w == 'percent':
        _merge(toks, i, i + 2, toks[i].core + '%')
    elif w == 'degrees' and after != 'of':
        _merge(toks, i, i + 2, toks[i].core + '°')


def _listing_context(toks: List[_Tok], i: int) -> bool:
    """True when a number starting at i sits inside a larger spoken phrase
    that is staying as words, so converting just this piece would garble it:
    "S H A two fifty six" (a bare digit word precedes), "a hundred and
    eighty" (an unconverted scale word precedes, directly or via 'and')."""
    if i == 0:
        return False
    p = toks[i - 1]
    if p.mathy or p.trail:
        return False
    pl = p.core.lower()
    if _word_number_value(_hyphen_parts(p.core)) is not None:
        return True
    if pl in _SCALES:
        return True
    if pl == 'and' and i >= 2 and not toks[i - 2].mathy and \
            toks[i - 2].core.lower() in _SCALES:
        return True
    return False


def _proper_number_word(toks: List[_Tok], i: int) -> bool:
    """A capitalized number word mid-sentence is part of a name — "the Thirty",
    "the Twelve Olympians", "the Thirteenth Amendment" — and stays a word
    (REP_E01 burned in "known as the 30")."""
    core = toks[i].core
    if not core[:1].isupper() or _sentence_initial(toks, i):
        return False
    return (_word_number_value(_hyphen_parts(core)) is not None
            or _parse_ordinal(toks, i) is not None)


def _pass_numbers(toks: List[_Tok]) -> None:
    i = 0
    while i < len(toks):
        if toks[i].mathy or _proper_number_word(toks, i):
            i += 1
            continue
        yr = _try_year(toks, i)
        if yr and _clean_between(toks, i, yr[0]):
            _merge(toks, i, yr[0], yr[1])
            i += 1
            continue
        ordn = _parse_ordinal(toks, i)
        if ordn:
            end, v = ordn
            prev = toks[i - 1].core.lower() if i else ''
            # fraction guard: "a twentieth of", "one tenth"
            if not (end - i == 1 and prev in ('a', 'an', 'one')):
                _merge(toks, i, end, f'{v}{_ordinal_suffix(v)}', mathy=False)
                i += 1
                continue
        np = _parse_cardinal(toks, i)
        if np:
            if _listing_context(toks, i):
                i = np.end
                continue
            nxt = toks[np.end].core.lower() if np.end < len(toks) else ''
            attach = nxt in ('percent', 'degrees')
            if np.strong or attach:
                t = _merge(toks, i, np.end, np.text)
                if attach:
                    _attach_unit(toks, toks.index(t))
                i += 1
            else:
                i = np.end
            continue
        i += 1


def _greek_symbol(name: str, sent_initial: bool) -> str:
    low = name.lower()
    if name[0].isupper() and not sent_initial and low in _GREEK_UPPER:
        return _GREEK_UPPER[low]
    return _GREEK[low]


# Hat forms: "I-hat" -> î, "r-hat" -> r̂. Spoken narration UPPERCASES every single-letter
# variable (tts_rules.py), so the case heard carries no information about the written form and a
# convention has to supply it. In this corpus the hat is a UNIT VECTOR — î ĵ k̂ r̂ n̂ û x̂ ŷ ẑ are
# conventionally lowercase — so those letters are lowercased; every other letter keeps its case,
# because a hatted capital elsewhere is an estimator or operator (Â, Θ̂). Corpus: i/j/k-hat 366,
# I/J/K-hat 204, r/R-hat 92, x/X-hat 38, n-hat 24, u-hat 26.
_HAT_LOWER = set("IJKRNUXYZ")
_HAT_PRECOMPOSED = {'i': 'î', 'j': 'ĵ'}     # dotted letters need the precomposed glyph
_COMBINING_CIRCUMFLEX = '\u0302'

# Spoken letter pairs that are a conventional lowercase PRODUCT, not a subscript. Only these are
# rewritten: every other "X-Y" pair in the corpus is a subscript, which the TTS rules deliberately
# hyphen-bind ("A_x" -> "A-X"), and joining those would assert a product that isn't there.
# Corpus: M-G 105, M-V 8, M-A 2.
_CONVENTIONAL_PRODUCTS = {('M', 'G'): 'mg', ('M', 'V'): 'mv', ('M', 'A'): 'ma'}

_TRIG_NAMES = {'sine': 'sin', 'cosine': 'cos', 'tangent': 'tan'}


def _hat_form(letter: str) -> str:
    ch = letter.lower() if letter in _HAT_LOWER else letter
    return _HAT_PRECOMPOSED.get(ch, ch + _COMBINING_CIRCUMFLEX)


_SUP_LETTERS = {'n': 'ⁿ', 'm': 'ᵐ', 'k': 'ᵏ', 'j': 'ʲ'}
_SCALE_WORDS = {'hundred': 100, 'thousand': 1000}


def _pass_space_powers(toks: List[_Tok], letters: frozenset) -> None:
    """Named spaces raised to a power: "R-two" -> R², "F-N" -> Fⁿ, "C-four" -> C⁴,
    "R-five-thousand" -> R⁵⁰⁰⁰.

    Course-scoped (linear algebra): elsewhere "C-one" is a constant C₁ and the
    generic letter+digit rule below must keep producing a subscript. Runs before
    it so the space letters never reach that rule.
    """
    for t in toks:
        parts = _hyphen_parts(t.core)
        if len(parts) < 2 or t.mathy or parts[0] not in letters:
            continue
        rest = [p.lower() for p in parts[1:]]
        if len(rest) == 1 and rest[0] in _SUP_LETTERS:
            t.core, t.mathy = parts[0] + _SUP_LETTERS[rest[0]], True
            continue
        mult = 1
        if len(rest) >= 2 and rest[-1] in _SCALE_WORDS:
            mult = _SCALE_WORDS[rest[-1]]
            rest = rest[:-1]
        val = _word_number_value(rest)
        if val is None:
            continue
        t.core = parts[0] + ''.join(_SUP_MAP[c] for c in str(val * mult))
        t.mathy = True


def _pass_math_tokens(toks: List[_Tok]) -> None:
    """Math-mode single-token and adjacency conversions."""
    # 1) token-internal hyphen forms: delta-t, sigma-two, X-zero, lambda-I
    for i, t in enumerate(toks):
        parts = _hyphen_parts(t.core)
        if len(parts) != 2 or t.mathy:
            continue
        a, b = parts
        if a.lower() in _GREEK and a.lower() not in _UNITS:
            sym = _greek_symbol(a, _sentence_initial(toks, i))
            if _is_single_letter(b) and b.lower() != 'd':
                t.core, t.mathy = sym + b, True
            elif b.lower() in _SUB_WORDS:
                t.core, t.mathy = sym + _SUB_DIGITS[_SUB_WORDS[b.lower()]], True
        elif _is_single_letter(a) and b.lower() in _SUB_WORDS:
            t.core, t.mathy = a + _SUB_DIGITS[_SUB_WORDS[b.lower()]], True
        elif _is_single_letter(a) and b.lower() == 'hat':
            t.core, t.mathy = _hat_form(a), True
        elif (a, b) in _CONVENTIONAL_PRODUCTS:
            t.core, t.mathy = _CONVENTIONAL_PRODUCTS[(a, b)], True

    # 2) spaced adjacency forms
    i = 0
    while i < len(toks) - 1:
        t, nxt = toks[i], toks[i + 1]
        if t.trail or nxt.lead:
            i += 1
            continue
        low = t.core.lower()
        # greek + letter / digit word: "delta X" -> ΔX, "lambda two" -> λ₂
        if low in _GREEK and not t.mathy:
            sym = _greek_symbol(t.core, _sentence_initial(toks, i))
            if _is_single_letter(nxt.core) and nxt.core.lower() != 'd':
                _merge(toks, i, i + 2, sym + nxt.core)
                continue
            if nxt.core.lower() in _SUB_WORDS:
                _merge(toks, i, i + 2,
                       sym + _SUB_DIGITS[_SUB_WORDS[nxt.core.lower()]])
                continue
        # any-case letter + "naught": "v naught" -> v₀ (a lowercase letter + digit word stays
        # prose — "a one" — but "naught" after a letter is always a subscript)
        if _is_single_letter(t.core) and nxt.core.lower() == 'naught' and not t.mathy:
            _merge(toks, i, i + 2, t.core + '₀')
            continue
        # UPPERCASE letter + digit word: "V one" -> V₁ — but not inside a
        # spelled initialism ("S H A two fifty six": prev is a bare letter)
        if _is_single_letter(t.core) and t.core.isupper() and \
                nxt.core.lower() in _UNITS and not t.mathy and \
                not (i > 0 and _is_single_letter(toks[i - 1].core)
                     and not toks[i - 1].trail):
            _merge(toks, i, i + 2,
                   t.core + _SUB_DIGITS[_UNITS[nxt.core.lower()]])
            continue
        # differentials: "d x" -> dx, "d T" -> dT, "d squared y" -> d²y,
        # "d theta" -> dθ
        if t.core == 'd' and not t.mathy:
            if _is_single_letter(nxt.core):
                _merge(toks, i, i + 2, 'd' + nxt.core)
                continue
            if nxt.core.lower() in _GREEK and _GREEK[nxt.core.lower()] != 'Δ':
                _merge(toks, i, i + 2, 'd' + _GREEK[nxt.core.lower()])
                continue
            if nxt.core.lower() == 'squared' and i + 2 < len(toks) and \
                    not nxt.trail and not toks[i + 2].lead and \
                    _is_single_letter(toks[i + 2].core):
                _merge(toks, i, i + 3, 'd²' + toks[i + 2].core)
                continue
        if low == 'partial' and _is_single_letter(nxt.core) and not t.mathy:
            _merge(toks, i, i + 2, '∂' + nxt.core)
            continue
        i += 1

    # 3) standalone greek names -> symbols
    for i, t in enumerate(toks):
        if not t.mathy and t.core.lower() in _GREEK:
            t.core = _greek_symbol(t.core, _sentence_initial(toks, i))
            t.mathy = True



def _pass_trig(toks: List[_Tok]) -> None:
    """"sine theta" -> "sin θ", "cosine of theta" -> "cos θ". Only when the argument is a math
    token or a bare single letter, so the geometric senses stay English ("the tangent line",
    "tangent to the curve", "the cosine of the angle between them"). A connecting "of" is
    absorbed, matching written notation; without one the argument keeps its own timing."""
    i = 0
    while i < len(toks) - 1:
        t = toks[i]
        name = _TRIG_NAMES.get(t.core.lower())
        if not name or t.mathy or t.trail:
            i += 1
            continue
        j = i + 1
        if toks[j].core.lower() == 'of' and j + 1 < len(toks):
            j += 1
        arg = toks[j]
        if not (arg.mathy or _is_single_letter(arg.core)) or \
                not _clean_between(toks, i, j + 1):
            i += 1
            continue
        if j == i + 1:
            t.core, t.mathy = name, True    # leave the argument its own timing
            i = j + 1
        else:
            _merge(toks, i, j + 1, name + ' ' + arg.core)
            i += 1


_PHRASES_ALWAYS = [(('less', 'than', 'or', 'equal', 'to'), '≤'),
                   (('greater', 'than', 'or', 'equal', 'to'), '≥'),
                   (('plus', 'or', 'minus'), '±'),
                   (('square', 'root', 'of'), '√')]
_PHRASES_MATH = [(('not', 'equal', 'to'), '≠')]


def _pass_phrases(toks: List[_Tok], math_mode: bool) -> None:
    phrases = _PHRASES_ALWAYS + (_PHRASES_MATH if math_mode else [])
    i = 0
    while i < len(toks):
        for words, sym in phrases:
            j = i + len(words)
            if j <= len(toks) and \
                    tuple(t.core.lower() for t in toks[i:j]) == words and \
                    _clean_between(toks, i, j):
                if sym == '√' and not _sqrt_scope_ok(toks, j, math_mode):
                    break
                _merge(toks, i, j, sym)
                if sym == '√':
                    _glue_sqrt(toks, i, math_mode)
                break
        i += 1
        if math_mode and i - 1 < len(toks) and \
                toks[i - 1].core.lower() == 'infinity':
            toks[i - 1].core, toks[i - 1].mathy = '∞', True


# Words that continue a spoken radicand past its first term. "square root of
# nine plus four" is √13, NOT √9 + 4 — the radical sign takes a scope the
# spoken form does not, so we only emit √ when the radicand is one atom.
_SQRT_CONTINUERS = frozenset({
    'plus', 'minus', 'times', 'over', 'divided', 'squared', 'cubed',
})


def _sqrt_scope_ok(toks: List[_Tok], j: int, math_mode: bool) -> bool:
    """True if "square root of" at toks[j-3:j] may become a √ sign.

    Only safe when the radicand is a single atom that nothing extends: √ binds
    exactly one following token, while the spoken form's scope runs to the end
    of the phrase. Emitting it for "nine plus four" or "three squared plus two
    squared" asserts arithmetic the narration never said, and a radicand the
    glue cannot take at all leaves a bare √ stranded in front of prose.
    """
    if j >= len(toks) or toks[j - 1].trail or toks[j].lead:
        return False
    nxt = toks[j]
    if nxt.mathy or (math_mode and _is_single_letter(nxt.core)):
        end = j + 1
    else:
        np = _parse_cardinal(toks, j)
        if not np:
            return False            # prose radicand: would strand a bare √
        end = np.end
    if end >= len(toks) or toks[end - 1].trail or toks[end].lead:
        return True                 # radicand closed by punctuation or EOS
    follow = toks[end]
    if follow.core.lower() in _SQRT_CONTINUERS:
        return False
    if follow.mathy or (math_mode and _is_single_letter(follow.core)):
        return False
    # A spoken cardinal after the radicand is a subscript ("a one squared…"),
    # so the radicand is a letter-plus-index pair, not the single atom √ takes.
    return _parse_cardinal(toks, end) is None


def _glue_sqrt(toks: List[_Tok], i: int, math_mode: bool) -> None:
    if i + 1 >= len(toks) or toks[i].trail or toks[i + 1].lead:
        return
    nxt = toks[i + 1]
    if nxt.mathy or (math_mode and _is_single_letter(nxt.core)):
        _merge(toks, i, i + 2, '√' + nxt.core)
        return
    np = _parse_cardinal(toks, i + 1)
    if np:
        _merge(toks, i + 1, np.end, np.text)
        _merge(toks, i, i + 2, '√' + toks[i + 1].core)


def _pass_powers(toks: List[_Tok], math_mode: bool) -> None:
    i = 1
    while i < len(toks):
        t = toks[i]
        w = t.core.lower()
        if w in ('squared', 'cubed') and not t.lead and \
                not toks[i - 1].trail:
            prev = toks[i - 1]
            ok = prev.mathy or (math_mode and
                                (_is_single_letter(prev.core) or
                                 (len(prev.core) == 2 and
                                  prev.core[0] in 'd∂')))
            if ok:
                _merge(toks, i - 1, i + 1,
                       prev.core + ('²' if w == 'squared' else '³'))
                continue
        i += 1


def _is_fraction_tail(toks: List[_Tok], end: int) -> bool:
    """True if a just-parsed cardinal is the numerator of a spoken fraction:
    "negative one third", "negative two thirds". Digitising only the numerator
    strands the denominator ("-1 third"), so leave the whole phrase alone --
    which is already what an unsigned "two thirds" does.
    """
    if end >= len(toks) or toks[end - 1].trail or toks[end].lead:
        return False
    w = toks[end].core.lower().rstrip('s')
    return w in _ORD_ALL or w in ('half', 'quarter')


def _pass_negative(toks: List[_Tok]) -> None:
    i = 0
    while i < len(toks) - 1:
        t = toks[i]
        if t.core.lower() == 'negative' and not t.trail and \
                not toks[i + 1].lead:
            nxt = toks[i + 1]
            if nxt.mathy and nxt.core[:1].isdigit():
                _merge(toks, i, i + 2, '-' + nxt.core)
                i += 1
                continue
            np = _parse_cardinal(toks, i + 1)
            if np and not _is_fraction_tail(toks, np.end):
                _merge(toks, i + 1, np.end, np.text)
                _merge(toks, i, i + 2, '-' + toks[i + 1].core)
                i += 1
                continue
        i += 1


def _pass_coefficients(toks: List[_Tok]) -> None:
    """Weak number word directly before a mathy symbol: 'two πR' -> '2 πR'."""
    for i in range(len(toks) - 1):
        t, nxt = toks[i], toks[i + 1]
        if nxt.mathy and not t.mathy and not t.trail and not nxt.lead:
            v = _word_number_value(_hyphen_parts(t.core))
            if v is not None and t.core.lower() != 'one':
                t.core, t.mathy = str(v), True


# Words that continue a spoken denominator past its first term. Exactly the
# scope problem _SQRT_CONTINUERS solves for the radical: "a over b plus c" is
# spoken for a/(b+c), but the slash binds only b, so emitting it asserts
# (a/b)+c -- a different, false statement. We therefore only emit "/" when the
# denominator is one atom that nothing extends, and otherwise leave the spoken
# "over" in place (uncompacted but true).
_DENOM_CONTINUERS = frozenset({'plus', 'minus'})


def _denom_scope_ok(toks: List[_Tok], i: int) -> bool:
    """True if the "over" at toks[i] may become a "/".

    "a over b plus c" is ambiguous: it is a/(b+c) when one fraction is being
    read aloud, and (a/b) + c when two terms are being summed. The tell is
    what follows the "plus": a SECOND "over" in the same clause means the plus
    joins two fractions, so each numerator/denominator pair is closed and the
    slash is safe ("one over m-one plus one over m-two" -> 1/m1 + 1/m2). With
    no further "over", the plus continues the denominator, and slashing it
    asserts a different, false statement.
    """
    end = i + 2
    if end >= len(toks) or toks[end - 1].trail or toks[end].lead:
        return True                 # closed by punctuation or end of stream
    # A math token juxtaposed after a NUMERIC denominator multiplies into it:
    # "fifteen over two pi squared" is 15/(2π²), but slashing only the "two"
    # displays "15/2 π²", which reads as (15/2)·π². A function or letter
    # denominator ("one over cos θ", "sin θ over sin φ") keeps its argument.
    if toks[end].mathy and toks[i + 1].core.replace(',', '').replace('.', '').isdigit():
        return False
    if toks[end].core.lower() not in _DENOM_CONTINUERS:
        return True
    # A continuer follows. This is the parallel-fractions shape ("one over m-one
    # plus one over m-two") only if a SECOND "over" follows with nothing but a
    # plausible numerator in between. Scanning to the end of the clause instead
    # is too permissive: "m-two over m-one plus m-two becomes m-one minus two
    # m-one over ..." finds the later, unrelated fraction and licenses a merge
    # that asserts the wrong grouping.
    for k in range(end + 1, len(toks)):
        t = toks[k]
        if t.core.lower() == 'over':
            return True
        if t.trail or t.lead:
            break
        if not _numerator_material(t):
            break
    return False


def _numerator_material(t: _Tok) -> bool:
    """True if `t` could be part of a spoken numerator: a math token, a single
    letter, or a bare number word. Anything else (a verb, a connective) means
    the following "over" belongs to a different fraction."""
    if t.mathy or _is_single_letter(t.core):
        return True
    return _word_number_value(_hyphen_parts(t.core)) is not None


def _pass_over_times(toks: List[_Tok]) -> None:
    i = 1
    while i < len(toks) - 1:
        t = toks[i]
        w = t.core.lower()
        if w in ('over', 'times') and not t.lead and not t.trail and \
                not toks[i - 1].trail and not toks[i + 1].lead:
            a, b = toks[i - 1], toks[i + 1]
            # A bare single letter is a legitimate operand ("L over two" -> L/2, "Y over X"
            # -> Y/X). Mark it BEFORE the number-word upgrade below, so the letter can license
            # the number on the other side.
            for side in (a, b):
                if not side.mathy and _is_single_letter(side.core):
                    side.mathy = True
            # Upgrade a weak single number word on one side of a mathy token.
            # Skip a number that is a spoken fraction's numerator ("times two
            # thirds"): digitising it strands the denominator as a word.
            for side, other, tail in ((a, b, i - 1), (b, a, i + 2)):
                if other.mathy and not side.mathy \
                        and not _is_fraction_tail(toks, tail):
                    v = _word_number_value(_hyphen_parts(side.core))
                    if v is not None:
                        side.core, side.mathy = str(v), True
            if a.mathy and b.mathy:
                if w == 'over':
                    if _denom_scope_ok(toks, i):
                        _merge(toks, i - 1, i + 2, a.core + '/' + b.core)
                        continue
                    i += 1
                    continue
                t.core, t.mathy = '×', True
        i += 1


_OPERAND_SYMBOLS = {'±', '≤', '≥', '≠', '×'}


def _pass_symbol_operands(toks: List[_Tok]) -> None:
    """A number word right after ± ≤ ≥ ≠ × is an operand: '± three' -> '± 3',
    '≠ zero' -> '≠ 0'. Runs in every mode (the symbols only exist once the
    phrase pass created them)."""
    for i in range(len(toks) - 1):
        t, nxt = toks[i], toks[i + 1]
        if t.core in _OPERAND_SYMBOLS and not t.trail and not nxt.lead \
                and not nxt.mathy and not _is_fraction_tail(toks, i + 2):
            v = _word_number_value(_hyphen_parts(nxt.core))
            if v is not None:
                nxt.core, nxt.mathy = str(v), True


_DECADE_WORDS = {'twenties': 2, 'thirties': 3, 'forties': 4, 'fifties': 5,
                 'sixties': 6, 'seventies': 7, 'eighties': 8, 'nineties': 9}


def _pass_decades(toks: List[_Tok]) -> None:
    """A spoken decade: "eighteen twenties" reaches here as "18 twenties" (the
    number pass converts the century word alone) -> "1820s". Only a century
    prefix 10–20 qualifies, so "the twenties" and "five sixties" stay put."""
    i = 0
    while i < len(toks) - 1:
        a, b = toks[i], toks[i + 1]
        tens = _DECADE_WORDS.get(b.core.lower())
        if tens and a.core.isdigit() and 10 <= int(a.core) <= 20 and \
                not a.trail and not b.lead:
            _merge(toks, i, i + 2, f'{a.core}{tens}0s', mathy=False)
        i += 1


def _pass_era(toks: List[_Tok], math_mode: bool = False) -> None:
    i = 1
    while i < len(toks) - 1:
        a, b = toks[i], toks[i + 1]
        pair = a.core + b.core
        if pair in ('BC', 'AD') and not a.trail and not a.lead and \
                not b.lead:
            prev = toks[i - 1]
            nxt = toks[i + 2] if i + 2 < len(toks) else None
            if (prev.core and prev.core[-1].isdigit()) or \
                    prev.core.lower() in _ERA_PRECEDERS:
                _merge(toks, i, i + 2, pair, mathy=False)
            # English writes AD BEFORE the year ("A D eighty-six" -> "AD 86"),
            # which the after-a-number rule above never sees. By this pass the
            # year is already one digit token. Two capital letters before a
            # number are plausible math, so this side stays off in math mode.
            elif pair == 'AD' and not math_mode and not b.trail and \
                    nxt is not None and not nxt.lead and nxt.core[:1].isdigit():
                _merge(toks, i, i + 2, pair, mathy=False)
        i += 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def compact_words(words: List[Dict], math_mode: bool = False,
                  space_letters: frozenset = frozenset()) -> List[Dict]:
    """Convert spoken-form word timestamps to compact display form.

    words: [{'word': str, 'start': float, 'end': float}, ...]
    space_letters: letters naming spaces whose spoken number is an EXPONENT
    ("R-two" -> R², not R₂) — linear-algebra courses pass {'R', 'C', 'F'}.
    Returns a new list in the same shape; merged phrases span the original
    words' time range. Timing of unchanged words is untouched.
    """
    toks = _make_toks(words)
    _pass_aliases(toks, math_mode)
    _pass_pow10(toks)
    _pass_numbers(toks)
    _pass_decades(toks)
    if math_mode:
        if space_letters:
            _pass_space_powers(toks, space_letters)
        _pass_math_tokens(toks)
        _pass_trig(toks)
    _pass_phrases(toks, math_mode)
    _pass_symbol_operands(toks)
    if math_mode:
        _pass_powers(toks, math_mode)
        _pass_negative(toks)
        _pass_coefficients(toks)
        _pass_over_times(toks)
    _pass_era(toks, math_mode)
    return [{'word': t.text, 'start': t.start, 'end': t.end}
            for t in toks if t.text]


def compact_stats(before: List[Dict], after: List[Dict]) -> str:
    import difflib
    a = [w['word'] for w in before]
    b = [w['word'] for w in after]
    changed = sum(i2 - i1
                  for tag, i1, i2, _, _ in
                  difflib.SequenceMatcher(None, a, b, autojunk=False)
                  .get_opcodes() if tag != 'equal')
    return (f"{len(before)} -> {len(after)} words, "
            f"{changed} spoken tokens compacted")


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--math']
    math = '--math' in sys.argv
    text = ' '.join(args) or sys.stdin.read()
    ws = [{'word': w, 'start': float(i), 'end': i + 0.5}
          for i, w in enumerate(text.split())]
    out = compact_words(ws, math_mode=math)
    print(' '.join(w['word'] for w in out))
