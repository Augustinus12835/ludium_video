"""
Canonical TTS narration rules for script-generation prompts.

ElevenLabs reliably mangles certain token shapes (raw numerals, initialisms,
hex strings, code syntax). These rule blocks are injected verbatim into the
math/technical script-generation prompts in generate_scripts.py; the
math-frame equivalent lives in ClaudeClient.VERIFY_MATH_SYSTEM /
VERIFY_CODE_SYSTEM (scripts/utils/verify_prompts.py), and the post-hoc
detection gate is scripts/utils/narration_check.py.

When hardening a NEW recurring mispronunciation, update the relevant layer
here AND the narration_check.py detector so prompts and the gate stay in sync.

NOTE: these blocks are concatenated into prompt templates that later go
through str.format() — keep literal braces escaped as {{ }} (currently there
are none).
"""

# Injected into TECHNICAL_SCRIPT_GENERATION_PROMPT (spoken `narration` field
# rules for technical subjects: hex strings, initialisms, code references,
# numbers). Verbatim move from generate_scripts.py — do not reflow.
TECHNICAL_NARRATION_TTS_RULES = """   - **Long raw character strings — spell out for TTS:** Strings of raw characters
     that TTS cannot pronounce naturally must be written character-by-character with
     spaces in the `narration` field. This applies to hex digests, Bitcoin addresses,
     transaction IDs, hashes, and public keys — content that's a sequence of opaque
     hex/base58/alphanumeric chars. Examples:
       - Hex digest: `0xDEADBEEF` → `0 x D E A D B E E F`
       - Bitcoin address: `1A1zP1...DivfNa` → `1 A 1 z P 1 ... D i v f N a`
       - Hash: `a3b9...` → `a 3 b 9 ...`
   - **Initialisms and acronyms — space the letters for TTS:** ElevenLabs does
     NOT reliably pronounce initialisms — it often slurs `UTXO` into "you-tox-oh",
     `ECDSA` into a mumbled syllable, or treats `SHA-256` as one chunk and drops the
     number. The rule of thumb: **if you would pronounce it letter-by-letter when
     speaking aloud, write it with spaces in the `narration` field.** If it's
     pronounced as a word, leave it alone.

     **Spell letter-by-letter** (write with single spaces between letters):

     | Original | Narration |
     |----------|-----------|
     | `UTXO` / `UTXOs` | `U T X O` / `U T X Os` |
     | `ECDSA` | `E C D S A` |
     | `RSA` | `R S A` |
     | `HMAC` | `H M A C` |
     | `SHA-256` / `SHA256` | `S H A two fifty six` |
     | `SHA-1` | `S H A one` |
     | `RIPEMD-160` | `R I P E M D one sixty` |
     | `HASH160` | `hash one sixty` |
     | `P2P` | `P two P` |
     | `P2SH` | `P two S H` |
     | `P2PK` | `P two P K` |
     | `P2PKH` | `P two P K H` |
     | `P2WSH` | `P two W S H` |
     | `P2WPKH` | `P two W P K H` |
     | `P2TR` | `P two T R` |
     | `OP_RETURN` | `O P return` |
     | `OP_CHECKSIG` | `O P check sig` |
     | `OP_HASH160` | `O P hash one sixty` |
     | `OP_DUP` | `O P dup` |
     | `OP_EQUALVERIFY` | `O P equal verify` |
     | `OP_CHECKMULTISIG` | `O P check multi sig` |
     | `OP_IF` / `OP_ELSE` | `O P if` / `O P else` |
     | `OP_0` / `OP_1` | `O P zero` / `O P one` |
     | `BIP` | `B I P` |
     | `BIP32` | `B I P thirty two` |
     | `BIP39` | `B I P thirty nine` |
     | `BIP118` | `B I P one eighteen` |
     | `BIP143` | `B I P one forty three` |
     | `BIP327` | `B I P three twenty seven` |
     | `BIP340` | `B I P three forty` |
     | `SLIP39` | `S L I P thirty nine` |
     | `WIF` | `W I F` |
     | `RBF` | `R B F` |
     | `CPFP` | `C P F P` |
     | `CSV` / `CLTV` | `C S V` / `C L T V` |
     | `BTC` / `BCH` / `ETH` | `B T C` / `B C H` / `E T H` |
     | `RPC` / `RFC` / `IETF` | `R P C` / `R F C` / `I E T F` |
     | `RFC 7539` | `R F C seven five three nine` |
     | `HTTP` / `HTTPS` / `TLS` / `SSL` | `H T T P` / `H T T P S` / `T L S` / `S S L` |
     | `TCP` / `UDP` / `IP` / `DNS` | `T C P` / `U D P` / `I P` / `D N S` |
     | `API` / `SDK` / `CLI` / `URL` / `URI` | `A P I` / `S D K` / `C L I` / `U R L` / `U R I` |
     | `CPU` / `GPU` / `RAM` / `SSD` / `HDD` | `C P U` / `G P U` / `R A M` / `S S D` / `H D D` |
     | `LLM` / `AI` / `ML` / `NLP` | `L L M` / `A I` / `M L` / `N L P` |
     | `XML` / `CSV` (file format) / `YAML` | `X M L` / `C S V` / `yamel` |
     | `OOP` / `FP` / `DI` | `O O P` / `F P` / `D I` |
     | `QR` / `2FA` / `MFA` | `Q R` / `two F A` / `M F A` |
     | `NPV` / `IRR` / `DCF` / `WACC` | `N P V` / `I R R` / `D C F` / `W A C C` |
     | `APR` / `APY` / `LTV` / `LTM` | `A P R` / `A P Y` / `L T V` / `L T M` |
     | `FX` / `ETF` / `IPO` / `LBO` / `ROI` | `F X` / `E T F` / `I P O` / `L B O` / `R O I` |
     | `P&L` / `M&A` / `R&D` / `S&P` | `P and L` / `M and A` / `R and D` / `S and P` |
     | `EPS` / `PE` (ratio) / `EV` | `E P S` / `P E` / `E V` |
     | `B2B` / `B2C` / `SaaS` (rare letter form) | `B two B` / `B two C` / (see word list) |
     | `GDP` / `CPI` / `Fed` (letters) | `G D P` / `C P I` (Fed→"fed") |

     **Leave as-is — these are pronounced as words** (or as already-correct word
     pronunciations TTS handles):
       - `SIGHASH` (sig-hash, compound), `SegWit` (seg-wit), `Taproot`, `Schnorr`
       - `Bech32` (beck thirty two), `MAST` (mast), `Merkle`, `nonce`
       - `JSON` (jason), `REST`, `CRUD`, `SQL` (sequel), `gRPC` is letter-spaced but
         most people say "g-r-p-c" — prefer `g R P C`
       - `ASIC` (ay-sick), `RAID` (raid), `LASER`, `RADAR`, `SCUBA`, `NATO`
       - `EBITDA` (ee-bit-dah), `EBIT` (ee-bit), `COGS` (cogs), `SaaS` (sass), `PaaS` (pass)
       - `ALL`, `NONE`, `SINGLE`, `TRUE`, `FALSE`, `VERIFY`, `IF`, `ELSE`,
         `ANYONECANPAY` — these are normal English words used as flag/opcode names
       - `Bitcoin`, `altcoin`, `whitepaper`, `multisig`, `timelock`, `hashlock`
     If unsure whether something reads as a word or letters, say it aloud naturally
     — if your tongue spells the letters, write it spaced.

     **Numeric suffixes** stay as natural-spoken numbers, not digit-by-digit:
     `SHA-256` → `S H A two fifty six` (NOT `S H A two five six`), `BIP340` → `B I P
     three forty` (NOT `B I P three four zero`). Exception: long RFC/standards
     numbers may be digit-by-digit if more natural (`RFC 7539` → `R F C seven five
     three nine`).

     Applies ONLY to the `narration` field. Keep `visual`, titles, on-screen labels,
     and code listings in their normal written form (`UTXO`, not `U T X O`;
     `OP_CHECKSIG`, not `O P check sig`; `0xDEADBEEF`, not `0 x D E A D B E E F`).

   - **Hyphen-bind tightly-set math notation** so TTS reads it as one unit, not
     drifting letters (ElevenLabs voices "A X" as two loose letters; "A-X" pulls them
     together). Applies to math/finance/stats symbols in the `narration` field:
       - Adjacent products / juxtaposition: `Ax` → "A-X", `AB` → "A-B".
       - Letter/word subscripts: `A_x` → "A-sub-X", `B_y` → "B-sub-Y",
         `F_net` → "F-sub-net" (numeric subscripts keep `a_1` → "A-one", above).
       - Hats / accents: `Â` → "A-hat", `x̂` → "X-hat", `\bar{{x}}` → "X-bar". The
         hyphen also stops "A-hat" being misread as "uh hat".
     On-screen labels/equations keep normal notation; only the spoken narration hyphenates.
   - **Never begin a sentence with the variable name "A"** — including the hyphenated
     forms above (`A-hat`, `A-sub-X`, `A-X`). A sentence-initial "A" is read as the
     article "a" (uh) — "A times B equals C" comes out "Uh times B equals C", and the
     hyphen does NOT rescue it ("A-hat is ..." still leads with "uh"). When "A" names
     a matrix, vector, variable, list, or column, lead with the noun it labels:
     "Matrix A times B ..." not "A times B ..."; "The estimator A-hat ..." not
     "A-hat is ...". This applies ONLY to "A" — it is the one letter that collides
     with the article. Every other letter (B, C, X, P, …) reads correctly at the
     start of a sentence; leave those alone. A genuine article ("A vector is ...") is
     of course fine, and "A" as a name mid-sentence is also fine.

   - **Programming code references — rewrite as spoken prose in the narration, not
     literal syntax.** The on-screen frame shows the actual code; the narration should
     describe what it DOES, never dictate punctuation. ElevenLabs cannot pronounce
     parentheses, brackets, or underscores naturally — leaving them in produces
     "open paren close paren" noise.

     Operators — use the natural English form:

     | Code | Narration |
     |---|---|
     | `==` | "is equal to" |
     | `!=` | "is not equal to" |
     | `<=` / `>=` | "less than or equal to" / "greater than or equal to" |
     | `+=` / `-=` | "increases by" / "decreases by" |
     | `*=` / `/=` | "is multiplied by" / "is divided by" |
     | `**` | "to the power of" |
     | `//` | "integer divide" |
     | `%` | "mod" |
     | `->` | "returns" |
     | `=` (assignment) | "is set to" or "equals" |

     Function calls — describe, do not recite punctuation:

     | Code | Narration |
     |---|---|
     | `len(s)` | "the length of S" |
     | `range(10)` | "range of ten" |
     | `print("hi")` | "print the string hi" (drop quotes and parens) |
     | `int(x)` | "convert X to an integer" |
     | `lst.append(x)` | "append X to the list" |
     | `s.lower()` | "the lowercase form of S" |
     | `dict.get(k, 0)` | "get key K from the dict, defaulting to zero" |

     Indices and slices:
       - `lst[0]` → "the first element of lst"
       - `lst[-1]` → "the last element of lst"
       - `s[2:5]` → "the slice from index two up to but not including five"

     Identifiers — read snake_case and camelCase as the natural English phrase, dropping
     the separator: `my_func` → "my func", `firstName` → "first name", `is_valid` →
     "is valid". Only spell out underscores when they ARE the teaching point (dunder
     methods).
       - Dunder methods: `__init__` → "dunder init", `__name__` → "dunder name",
         `__main__` → "dunder main", `__str__` → "dunder string".
       - Single-letter variables: UPPERCASE in narration so TTS reads the letter name,
         not the article. "loop variable I" (not "loop variable i"), "list A" (not
         "list a"). Same rule as math narration. **Critical for a standalone variable
         `a` in flowing prose** — when the acceleration / leading-coefficient / generic
         variable `a` is spoken next to an operator word, an argument, or at a clause
         end, lowercase `a` reads as the article "uh". Uppercase it: "A times t" (not
         "a times t"), "A of t" (not "a of t"), "one-half A t squared" (not "... a t
         squared"), "A-t" / "A-t-squared" (not "a-t"), "the slope is A" / "the constant
         acceleration A" (not "... a"), "A equals d-v d-t" (not "a equals ..."). Only
         `a` collides with the article — leave b, c, t, x lowercase. Indexed/subscripted
         variables too: `a_1, a_2, a_3` → "A-one, A-two, A-three" (uppercase, hyphenated),
         never "a one, a two, a three" (TTS reads lowercase "a one" as the article "uh
         one"). The sentence-initial "A" rule above applies to code variables too — never
         start a sentence with the name "A".

     Keywords — read as written: `def`, `class`, `lambda`, `if`, `elif`, `else`, `for`,
     `while`, `return`, `yield`, `import`, `from`, `as`, `with`, `try`, `except`,
     `finally`, `raise`, `pass`, `break`, `continue`, `global`, `nonlocal`, `self`,
     `None`, `True`, `False`.

     File names and dotted paths:
       - `mymodule.py` → "the file my module dot p y"
       - `numpy.array` → "numpy dot array" (dots between modules stay as "dot")
       - `os.path.join` → "os dot path dot join"

     Whole-line examples for reference:
       - `for i in range(10):` → "for I in range of ten"
       - `if x % 2 == 0:` → "if X mod two is equal to zero"
       - `total += nums[i]` → "total increases by the I-th element of nums"
       - `if __name__ == "__main__":` → "if dunder name is equal to dunder main"

     **Spelling-out rule** (parallel to the hex / initialism rule above): when a literal
     token IS the teaching point — e.g. a debugger output, a stack-trace symbol, an
     exact attribute name being looked up — space it letter-by-letter. Example: an
     `AttributeError` mentioned inside a traceback → "A t t r i b u t e Error".
     Otherwise prefer the natural prose form.

     Library / tool names that ElevenLabs mispronounces — write them phonetically in
     narration:
       - `numpy` → "num pie", `matplotlib` → "mat plot lib", `pyplot` → "pie plot",
         `pytest` → "pie test", `venv` → "vee env"
       - `tuple` → "too pull", `iter` → "it ter", `enum` → "ee num", `regex` → "redge ex"
       - `argv` / `argc` → "arg v" / "arg c"
       - `stdin` / `stdout` / `stderr` → "standard in" / "standard out" / "standard err"
       - `REPL` → "repple" (spoken convention, rhymes with apple)
       - `SQL` → "sequel" (already in the existing list above)
       - Big-O notation: `O(n)` → "oh of N", `O(n²)` → "oh of N squared",
         `O(log n)` → "oh of log N", `O(n log n)` → "oh of N log N"

     **Numbers and years — spell out in English for TTS:** NEVER feed the spoken
     `narration` raw multi-digit Arabic numerals — ElevenLabs interprets them
     inconsistently (digit-by-digit, wrong groupings, dropped magnitudes), leaving
     ambiguity. 🚫 HARD CONSTRAINT: the `narration` field must contain ZERO digits
     0-9. Before finishing each frame, scan its `narration` — if you see any digit,
     it is a defect; rewrite it as English words. "Long" or "exact" numbers like
     `299,792,458` are NOT exceptions — they are the worst offenders and MUST be
     spelled out. Always write numbers as English words in the `narration` field:
       - Years: `1973` → "nineteen seventy-three", `1066` → "ten sixty six",
         `1905` → "nineteen oh five", `2008` → "two thousand eight". Eras
         letter-by-letter: `300 BC` → "three hundred B C", `476 AD` → "four hundred
         seventy six A D".
       - Large / exact numbers: `299,792,458` → "two hundred ninety-nine million,
         seven hundred ninety-two thousand, four hundred fifty-eight";
         `9,192,631,770` → "nine billion, one hundred ninety-two million, six hundred
         thirty-one thousand, seven hundred seventy"; `6.02×10^23` → "six point oh two
         times ten to the twenty-third".
       - Everyday quantities: `30,000` → "thirty thousand", `3.14` → "three point one
         four", `1/137` → "one over one hundred thirty-seven", `2024` (a count, not a
         year) → "two thousand twenty-four".
       - **Trailing zeros after a decimal point are meaningless when spoken — DROP
         them.** Say the value, not the slide's padding: `1.1500` → "one point one
         five", `1.0500` → "one point zero five", `174.00` → "one hundred seventy-four",
         `0.0050` → "zero point zero zero five". Significant digits stay: `1.1024` →
         "one point one zero two four", `156.62` → "one hundred fifty-six point six
         two". The slide keeps the padded form on screen; only the spoken word trims
         the trailing zeros.
     This applies ONLY to the spoken `narration` field. Keep the `visual` field's
     on-screen text, equations, tables, and labels in normal numeral form — the slide
     shows `299,792,458`, the narrator says it in words.

     Code shown ON SCREEN keeps its literal syntax — these rewrite rules apply ONLY to
     the spoken `narration` field. The `visual` field can include fenced ```python
     blocks with the actual code, indentation, and punctuation preserved verbatim."""

# Injected into MATH_SCRIPT_GENERATION_PROMPT (spoken `narration` field rules
# for math videos). Math frames get a TTS rewrite during verification
# (ClaudeClient.VERIFY_MATH_SYSTEM), but frames declared `frame_class: "visual"`
# skip verification — their narration is spoken EXACTLY as written, so it must
# already be TTS-safe. The rules apply to every frame's narration; the pre-TTS
# gate (narration_check.py) halts the pipeline on violations.
MATH_NARRATION_TTS_RULES = """   - **TTS-safe narration (CRITICAL — especially for "frame_class": "visual" frames,
     whose narration is spoken exactly as written with no rewrite pass):**
       - **Numbers — ZERO digits 0-9 in the narration field.** Spell every number out
         in English words: `3.14` → "three point one four", `1/137` → "one over one
         hundred thirty-seven", `12x^3` → "twelve x cubed". Match the value the slide
         displays — the slide shows `377.41`, the narrator says "three hundred
         seventy-seven point four one". Before finishing each frame, scan its
         narration — any digit is a defect. **Drop trailing zeros after a decimal
         point when spoken** — they are meaningless: `1.1500` → "one point one five",
         `174.00` → "one hundred seventy-four" (but significant digits stay: `1.1024`
         → "one point one zero two four"). The slide keeps the padded form on screen.
       - **Greek letters — write the name:** `α` → "alpha", `θ` → "theta", `π` → "pi",
         `Δ` → "delta". Never put a raw Greek character in the narration.
       - **Math symbols — write the words:** `√` → "the square root of", `∫` → "the
         integral of", `≤` / `≥` → "less than or equal to" / "greater than or equal
         to", `≠` → "not equal to", `×` → "times", `±` → "plus or minus", `∞` →
         "infinity", `°` → "degrees". No raw math symbols in the narration.
       - **Differentials — space the letters:** "d x", "d u", "d y over d x" — never
         "dx", "du", "dy/dx" (TTS reads "dx" as a word, not two letters).
       - **Single-letter variables — UPPERCASE** so TTS reads the letter name, not an
         article: "the function F of X", "solve for X". **Critical for a standalone
         variable `a` in flowing prose** — when the acceleration / leading-coefficient /
         generic variable `a` is spoken next to an operator word, an argument, or at a
         clause end, lowercase `a` reads as the article "uh". Uppercase it: "A times t"
         (not "a times t"), "A of t" (not "a of t"), "one-half A t squared" (not "... a t
         squared"), "A-t" / "A-t-squared" (not "a-t"), "the slope is A" / "the constant
         acceleration A" (not "... a"), "A equals d-v d-t" (not "a equals ..."). Only `a`
         collides with the article — leave b, c, t, x lowercase. Subscripted variables are
         uppercase and hyphenated: `a_1, a_2` → "A-one, A-two" — never lowercase
         "a one" (TTS reads it as the article "uh one").
       - **Hyphen-bind tightly-set notation** so TTS reads it as one unit, not
         drifting letters (ElevenLabs voices "A X" as two loose letters; "A-X" pulls
         them together):
           - Adjacent products / juxtaposition: `Ax` → "A-X", `AB` → "A-B",
             `rθ` → "R-theta".
           - Letter/word subscripts: `A_x` → "A-sub-X", `B_y` → "B-sub-Y",
             `F_net` → "F-sub-net" (numeric subscripts keep "A-one" above).
           - Hats / accents: `Â` → "A-hat", `x̂` → "X-hat", `\bar{{x}}` → "X-bar",
             `\tilde{{p}}` → "P-tilde". The hyphen also stops "A-hat" being misread as
             "uh hat".
         On-screen equations keep normal notation; only the spoken narration hyphenates.
   - **Never begin a sentence with the variable name "A"** — including the hyphenated
     forms above (`A-hat`, `A-sub-X`, `A-X`, `A-one`). A sentence-initial "A" is read
     as the article "a" (uh) — "A is a matrix with ..." comes out "Uh is a matrix
     with ...", and the hyphen does NOT rescue it ("A-hat is ..." still leads with
     "uh"). When "A" names a matrix, vector, or variable, lead with the noun it
     labels: "Matrix A is ..." not "A is a matrix ..."; "The estimator A-hat is ..."
     not "A-hat is ...". This applies ONLY to "A" — it is the one letter that
     collides with the article. Every other single letter (B, C, X, P, …) reads
     correctly at the start of a sentence; do not rewrite those. "A" mid-sentence is
     also fine.
   - These rules apply ONLY to the spoken `narration` field. The `visual` field's
     on-screen equations, labels, and axis numbers keep normal mathematical
     notation — the slide shows `f'(x) = 12x^3 - 4x`, the narrator says it in words."""
