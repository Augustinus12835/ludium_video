# ElevenLabs Pronunciation Dictionary — Math Notation

Words and notations whose written form differs from how a human reader would say them in mathematical English. The ready-to-upload dictionary is `elevenlabs_pronunciation_dict.pls` in this folder — `python scripts/setup_pronunciation_dict.py` uploads it to your ElevenLabs account and wires its ID into `.env` as part of the default setup, and every TTS request then applies it.

This file is the human-readable reference for what's in the dictionary and why. **Extend the live dictionary to suit your own production needs** — proper nouns, domain terms, recurring acronyms — via the ElevenLabs dashboard or rules API; requests are keyed by dictionary ID only, so the latest version is always used. Keep the `.pls` in sync when you add entries.

ElevenLabs supports two entry types:
- **alias** — replace the source token with a phonetic respelling (simplest, used throughout below)
- **IPA** — provide an IPA transcription (more precise, used where the alias would be ambiguous)

For each entry below, the **alias** column is the recommended phonetic respelling; **IPA** is given where helpful.

---

## 1. Hyperbolic functions (highest priority)

These are written exactly like their trigonometric cousins with a trailing `h`, but the `h` is voiced. Without an entry, ElevenLabs treats the `h` as silent and reads `sinh` identically to `sin`.

| Written | Alias | IPA | Notes |
|---|---|---|---|
| `sinh` | `sinch` | /sɪntʃ/ | Rhymes with "pinch". "Shine" /ʃaɪn/ is also common in textbooks but conflicts with the English verb. |
| `cosh` | `kosh` | /kɒʃ/ | Rhymes with "gosh". |
| `tanh` | `tanch` | /tæntʃ/ | Rhymes with "ranch". British convention is "than" /θæn/ but that collides with the English word. |
| `coth` | `koth` | /kɒθ/ | Rhymes with "moth". |
| `sech` | `sheck` | /ʃɛk/ | Rhymes with "check". Some textbooks use "seech" /siːtʃ/. |
| `csch` | `co-sheck` | /koʊ ʃɛk/ | Sometimes "co-seech". |

## 2. Inverse hyperbolic functions

The same `h`-voiced rule applies. Both `arc-` and `ar-` prefixes occur in textbooks (the `ar-` form is technically more correct since these aren't arc lengths, but `arc-` dominates in narration).

| Written | Alias |
|---|---|
| `arcsinh`, `arsinh`, `sinh⁻¹` | `arc sinch` |
| `arccosh`, `arcosh`, `cosh⁻¹` | `arc kosh` |
| `arctanh`, `artanh`, `tanh⁻¹` | `arc tanch` |
| `arccoth`, `arcoth`, `coth⁻¹` | `arc koth` |
| `arcsech`, `arsech`, `sech⁻¹` | `arc sheck` |
| `arccsch`, `arcsch`, `csch⁻¹` | `arc co-sheck` |

## 3. Greek letters (when typed as Unicode)

`scripts/generate_scripts.py` already instructs Claude to spell Greek letters as English words, so these mostly never reach TTS. Add dictionary entries as a safety net for cases where the rule slips.

| Written (Unicode) | Alias | IPA | Notes |
|---|---|---|---|
| `π` | `pi` | /paɪ/ | |
| `θ` | `theta` | /ˈθeɪtə/ | |
| `α` | `alpha` | /ˈælfə/ | |
| `β` | `beta` | /ˈbeɪtə/ | American "bay-ta", British "bee-ta". |
| `γ` | `gamma` | /ˈɡæmə/ | |
| `δ` | `delta` | /ˈdɛltə/ | |
| `ε`, `ϵ` | `epsilon` | /ˈɛpsɪlɒn/ | |
| `ζ` | `zeta` | /ˈzeɪtə/ | |
| `η` | `eta` | /ˈeɪtə/ | |
| `λ` | `lambda` | /ˈlæmdə/ | |
| `μ` | `mu` | /mjuː/ | |
| `ν` | `nu` | /njuː/ | Often confused visually with Latin `v`. |
| `ξ` | `ksi` | /ksaɪ/ | TTS commonly mispronounces as "ex-eye". |
| `ρ` | `rho` | /roʊ/ | Rhymes with "row", not "row" the verb. |
| `σ`, `ς` | `sigma` | /ˈsɪɡmə/ | |
| `τ` | `tau` | /taʊ/ or /tɔː/ | |
| `φ`, `ϕ` | `phi` | /faɪ/ or /fiː/ | "Fye" in math, "fee" in physics — pick one and stay consistent. |
| `χ` | `kai` | /kaɪ/ | TTS commonly says "chee" or "chai". |
| `ψ` | `sigh` | /saɪ/ | TTS commonly says "puh-see". Some speakers voice the p: /psaɪ/. |
| `ω` | `omega` | /oʊˈmeɪɡə/ | American stress on second syllable. |
| `Δ` | `capital delta` | | When distinct from lowercase matters. |
| `Σ` | `capital sigma` | | |
| `Ω` | `capital omega` | | |
| `∇` | `nabla` | /ˈnæblə/ | Or "del" in physics contexts. |
| `∂` | `partial` | | Spoken as "partial" in "∂f/∂x" → "partial f partial x". |

## 4. Function-name abbreviations

These are usually fine, but ElevenLabs sometimes spells them letter-by-letter ("L-N") instead of as words.

| Written | Alias | Notes |
|---|---|---|
| `ln` | `lin` | Or "natural log". "lin" matches the standard mathematician's pronunciation. |
| `lg` | `log base two` | Used in CS for binary log. Spell out to avoid confusion with `log`. |
| `lim` | `limit` | Avoid letter-by-letter "L-I-M". |
| `sup` | `soup` | Supremum. Not the prefix "sup-". |
| `inf` | `inf` | Infimum. Pronounced as written, but watch for "info". |
| `arg` | `arg` | As in "arg max" — pronounced like the start of "argument". |
| `gcd` | `G C D` | Letter-by-letter is correct here. |
| `lcm` | `L C M` | Letter-by-letter. |
| `mod` | `mod` | Not "modify". |
| `det` | `det` | Pronounced as written, short for "determinant". |
| `tr` | `trace` | When standing for matrix trace. |
| `Re` | `real part` | Avoid "ree". |
| `Im` | `imaginary part` | Avoid "im" or "I-M". |

## 5. Common abbreviations / Latin

If they appear inline in narration text, TTS reads them inconsistently.

| Written | Alias |
|---|---|
| `i.e.` | `that is` |
| `e.g.` | `for example` |
| `iff` | `if and only if` |
| `s.t.`, `st.` | `such that` |
| `WLOG`, `wlog` | `without loss of generality` |
| `QED` | `Q E D` |
| `cf.` | `compare` |
| `etc.` | `et cetera` |
| `vs.` | `versus` |

## 6. Notation patterns (not pronunciation, but TTS-fragile)

Not strictly dictionary entries, but worth noting because TTS drops or misreads them. Handle these in the script-generation prompt rather than the pronunciation dictionary.

- **Primes**: `f'(x)` → write as "f prime of x", `f''(x)` → "f double prime of x". TTS drops the apostrophes.
- **Subscripts**: `x_n` → "x sub n" or "x n". TTS reads underscore literally.
- **Superscripts/exponents**: `x^2` → "x squared", `e^x` → "e to the x". TTS reads `^` as "caret".
- **Differentials**: `dx`, `dy` → "d-x", "d-y", or just "dx" depending on the voice.
- **Fractions inline**: `a/b` → "a over b" for clarity vs. "a divided by b".
- **Absolute value**: `|x|` → "absolute value of x". TTS reads pipes literally.
- **Set membership**: `x ∈ A` → "x in A" or "x is in A".
- **Quantifiers**: `∀`, `∃` → "for all", "there exists".

These are all already enforced in `scripts/generate_scripts.py` system prompts (Greek letters, no Unicode), so they should arrive at TTS as plain English. The dictionary backstops the cases where the model slips.

## 7. Programming / CS terms

For the technical pipeline applied to CS lectures (e.g. MIT OCW 6.100L). The script-generation prompt converts most code references to spoken prose ("the print function with the string hi", not "print open paren quote hi"); this table is the safety net for tokens that still slip through and for common library names ElevenLabs mispronounces.

| Written | Alias | Notes |
|---|---|---|
| `__init__` | `dunder init` | dunder methods read as words, not underscores |
| `__main__` | `dunder main` | |
| `__name__` | `dunder name` | |
| `__str__` | `dunder string` | |
| `__repr__` | `dunder rep` | |
| `numpy` | `num pie` | TTS slurs "numpy"; prefer two-syllable form |
| `matplotlib` | `mat plot lib` | three syllables, otherwise unintelligible |
| `pyplot` | `pie plot` | |
| `pytest` | `pie test` | |
| `venv` | `vee env` | |
| `tuple` | `too pull` | American "too-pull", not "tup-uhl" |
| `iter` | `it ter` | force two syllables |
| `enum` | `ee num` | |
| `argv` | `arg v` | space the v |
| `argc` | `arg c` | |
| `stdin` | `standard in` | |
| `stdout` | `standard out` | |
| `stderr` | `standard err` | |
| `regex` | `redge ex` | |
| `YAML` | `yammel` | spoken convention |
| `URL` | `U R L` | space the letters |
| `HTML` | `H T M L` | |
| `CSS` | `C S S` | |
| `IDE` | `I D E` | |
| `REPL` | `repple` | rhymes with "apple", spoken convention |
| `Big-O` | `big oh` | |
| `O(n)` | `oh of N` | upper-case the variable so TTS reads it as a letter |
| `O(n²)` | `oh of N squared` | |
| `O(log n)` | `oh of log N` | |
| `O(n log n)` | `oh of N log N` | |

`SQL` (sequel), `JSON` (jason), `API` (A P I) are already covered in §4 / §5 alongside the math/Bitcoin entries — no separate entry needed.

---

## How to apply

Maintain a single ElevenLabs pronunciation dictionary on the workspace and attach it to all TTS calls in `scripts/generate_tts_elevenlabs.py`. Sections 1–4 are the priority. Section 5 is small but cheap to add. Section 6 belongs in the script-gen prompt, not the dictionary.

When adding a new course in a new domain (physics, chemistry, biology, music theory, etc.), revisit and extend this list — every domain has its own set of "looks-like-X-but-said-like-Y" terms.
