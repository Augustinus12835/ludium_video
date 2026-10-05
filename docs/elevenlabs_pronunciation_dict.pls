<?xml version="1.0" encoding="UTF-8"?>
<lexicon version="1.0"
         xmlns="http://www.w3.org/2005/01/pronunciation-lexicon"
         alphabet="ipa"
         xml:lang="en-US">

  <!-- Ready-to-upload ElevenLabs pronunciation dictionary for math narration.
       Create a dictionary from this file (dashboard or API), then set its ID as
       ELEVENLABS_PRONUNCIATION_DICT_ID in .env — scripts/generate_tts_elevenlabs.py
       attaches it to every TTS request, keyed by dict id only, so the LATEST live
       version is always used. Push later edits to the live dict through the rules API
       (pronunciation_dictionaries.rules.add/remove), then keep this file in sync.
       NB: the Greek SYMBOL rules are mostly dormant — pipeline narration is already
       spelled out, so the WORD rules in section 3b (rho/pi/…) are what actually fix
       the audio. -->

  <!-- 1. Hyperbolic functions -->
  <lexeme><grapheme>sinh</grapheme><alias>sinch</alias></lexeme>
  <lexeme><grapheme>cosh</grapheme><alias>kosh</alias></lexeme>
  <lexeme><grapheme>tanh</grapheme><alias>tanch</alias></lexeme>
  <lexeme><grapheme>coth</grapheme><alias>koth</alias></lexeme>
  <lexeme><grapheme>sech</grapheme><alias>sheck</alias></lexeme>
  <lexeme><grapheme>csch</grapheme><alias>co-sheck</alias></lexeme>

  <!-- 2. Inverse hyperbolic functions -->
  <lexeme><grapheme>arcsinh</grapheme><alias>arc sinch</alias></lexeme>
  <lexeme><grapheme>arsinh</grapheme><alias>arc sinch</alias></lexeme>
  <lexeme><grapheme>arccosh</grapheme><alias>arc kosh</alias></lexeme>
  <lexeme><grapheme>arcosh</grapheme><alias>arc kosh</alias></lexeme>
  <lexeme><grapheme>arctanh</grapheme><alias>arc tanch</alias></lexeme>
  <lexeme><grapheme>artanh</grapheme><alias>arc tanch</alias></lexeme>
  <lexeme><grapheme>arccoth</grapheme><alias>arc koth</alias></lexeme>
  <lexeme><grapheme>arcoth</grapheme><alias>arc koth</alias></lexeme>
  <lexeme><grapheme>arcsech</grapheme><alias>arc sheck</alias></lexeme>
  <lexeme><grapheme>arsech</grapheme><alias>arc sheck</alias></lexeme>
  <lexeme><grapheme>arccsch</grapheme><alias>arc co-sheck</alias></lexeme>
  <lexeme><grapheme>arcsch</grapheme><alias>arc co-sheck</alias></lexeme>

  <!-- 3. Greek letters (lowercase SYMBOLS).
       NOTE: these fire only if a raw Greek glyph leaks into narration. Our TTS input is
       spelled-out (the words "pi", "rho", …), so the WORD rules in section 3b are what
       actually fix our audio. The problematic short letters map straight to a phonetic
       respelling (pie/roe/…), NOT to the bare letter-name that ElevenLabs mangles. -->
  <lexeme><grapheme>π</grapheme><alias>pie</alias></lexeme>
  <lexeme><grapheme>θ</grapheme><alias>theta</alias></lexeme>
  <lexeme><grapheme>α</grapheme><alias>alpha</alias></lexeme>
  <lexeme><grapheme>β</grapheme><alias>beta</alias></lexeme>
  <lexeme><grapheme>γ</grapheme><alias>gamma</alias></lexeme>
  <lexeme><grapheme>δ</grapheme><alias>delta</alias></lexeme>
  <lexeme><grapheme>ε</grapheme><alias>epsilon</alias></lexeme>
  <lexeme><grapheme>ϵ</grapheme><alias>epsilon</alias></lexeme>
  <lexeme><grapheme>ζ</grapheme><alias>zeta</alias></lexeme>
  <lexeme><grapheme>η</grapheme><alias>eta</alias></lexeme>
  <lexeme><grapheme>λ</grapheme><alias>lambda</alias></lexeme>
  <lexeme><grapheme>μ</grapheme><alias>mew</alias></lexeme>
  <lexeme><grapheme>ν</grapheme><alias>nu</alias></lexeme>
  <lexeme><grapheme>ξ</grapheme><alias>ksi</alias></lexeme>
  <lexeme><grapheme>ρ</grapheme><alias>roe</alias></lexeme>
  <lexeme><grapheme>σ</grapheme><alias>sigma</alias></lexeme>
  <lexeme><grapheme>ς</grapheme><alias>sigma</alias></lexeme>
  <lexeme><grapheme>τ</grapheme><alias>tau</alias></lexeme>
  <lexeme><grapheme>φ</grapheme><alias>phi</alias></lexeme>
  <lexeme><grapheme>ϕ</grapheme><alias>phi</alias></lexeme>
  <lexeme><grapheme>χ</grapheme><alias>kai</alias></lexeme>
  <lexeme><grapheme>ψ</grapheme><alias>sigh</alias></lexeme>
  <lexeme><grapheme>ω</grapheme><alias>omega</alias></lexeme>

  <!-- Greek letters (capital, where distinct) -->
  <lexeme><grapheme>Δ</grapheme><alias>capital delta</alias></lexeme>
  <lexeme><grapheme>Σ</grapheme><alias>capital sigma</alias></lexeme>
  <lexeme><grapheme>Ω</grapheme><alias>capital omega</alias></lexeme>

  <!-- 3b. Greek letter NAMES, spelled out (THIS is what our narration actually contains —
       the TTS-safety rules spell every Greek letter as its English word). Only the letters
       ElevenLabs reliably mangles when read as a word are respelled here; the rest
       (alpha, beta, gamma, delta, theta, lambda, sigma, omega, …) read fine as words and
       are intentionally omitted. Both cases listed (alias matching is case-sensitive). -->
  <lexeme><grapheme>pi</grapheme><alias>pie</alias></lexeme>      <!-- else read as the letter "P" -->
  <lexeme><grapheme>Pi</grapheme><alias>pie</alias></lexeme>
  <lexeme><grapheme>rho</grapheme><alias>roe</alias></lexeme>     <!-- else spelled "R-H-O" -->
  <lexeme><grapheme>Rho</grapheme><alias>roe</alias></lexeme>
  <!-- hyphen-bound subscript forms (the TTS rules bind ρ_$ -> "rho-dollar" as one token,
       so the plain "rho" rule won't match it) -->
  <lexeme><grapheme>rho-dollar</grapheme><alias>roe-dollar</alias></lexeme>
  <lexeme><grapheme>rho-pound</grapheme><alias>roe-pound</alias></lexeme>

  <!-- chi -> "kai" (added 2026-08-26). ElevenLabs otherwise reads "chi" as the "chee" of
       cheese, which is wrong for every math/statistics use. In math/technical narration chi
       only ever appears as the Greek letter, so the global alias is safe — see the override
       note in the DELIBERATELY-NOT-ADDED block below. The hyphen-bound compounds need their
       OWN rules: narration says "chi-squared", which the bare "chi" rule does not match
       (same reason rho-dollar is listed separately). -->
  <lexeme><grapheme>chi</grapheme><alias>kai</alias></lexeme>
  <lexeme><grapheme>Chi</grapheme><alias>kai</alias></lexeme>
  <lexeme><grapheme>chi-squared</grapheme><alias>kai-squared</alias></lexeme>
  <lexeme><grapheme>Chi-squared</grapheme><alias>kai-squared</alias></lexeme>
  <lexeme><grapheme>chi-square</grapheme><alias>kai-square</alias></lexeme>
  <lexeme><grapheme>Chi-square</grapheme><alias>kai-square</alias></lexeme>

  <!-- DELIBERATELY NOT added here (collision risk — these are real English words/names/units,
       and the dict applies to ALL narration):
         psi  -> would clobber "psi" the pressure unit (say "P-S-I" in narration instead)
         xi   -> would clobber the name "Xi"
         nu   -> would clobber "nu"; tau/eta read acceptably as words (tau: a steady /taʊ/
                 "Tao" on isolated-clip checks)
       OVERRIDDEN: mu WAS on this list but is now aliased to "mew" in section 10.
       OVERRIDDEN 2026-08-26: chi WAS on this list (collision with "chi" the energy / Tai Chi)
       but is now aliased globally to "kai" in section 3b — in math/technical narration chi
       only ever appears as the Greek letter. If a script ever needs the "chee" sense, spell
       it "chee" in that frame's narration rather than removing the rule.
       If one of these appears as a Greek letter and is mis-said, respell it in that frame's
       narration (e.g. "ksai", "sigh") rather than globally aliasing the word here. -->

  <!-- Math operators -->
  <lexeme><grapheme>∇</grapheme><alias>nabla</alias></lexeme>
  <lexeme><grapheme>∂</grapheme><alias>partial</alias></lexeme>

  <!-- 3c. Differentials (else "dx" slurs to one syllable, "du" -> "do"). Narration is
       normally pre-spaced ("d x") by the TTS rules; these catch any unspaced leak. -->
  <lexeme><grapheme>dx</grapheme><alias>D X</alias></lexeme>
  <lexeme><grapheme>du</grapheme><alias>D U</alias></lexeme>

  <!-- 4. Function-name abbreviations -->
  <lexeme><grapheme>ln</grapheme><alias>lin</alias></lexeme>
  <lexeme><grapheme>lg</grapheme><alias>log base two</alias></lexeme>
  <lexeme><grapheme>lim</grapheme><alias>limit</alias></lexeme>
  <lexeme><grapheme>sup</grapheme><alias>soup</alias></lexeme>
  <lexeme><grapheme>gcd</grapheme><alias>G C D</alias></lexeme>
  <lexeme><grapheme>lcm</grapheme><alias>L C M</alias></lexeme>
  <lexeme><grapheme>tr</grapheme><alias>trace</alias></lexeme>
  <lexeme><grapheme>Re</grapheme><alias>real part</alias></lexeme>
  <lexeme><grapheme>Im</grapheme><alias>imaginary part</alias></lexeme>

  <!-- 5. Common abbreviations / Latin -->
  <lexeme><grapheme>i.e.</grapheme><alias>that is</alias></lexeme>
  <lexeme><grapheme>e.g.</grapheme><alias>for example</alias></lexeme>
  <lexeme><grapheme>iff</grapheme><alias>if and only if</alias></lexeme>
  <lexeme><grapheme>s.t.</grapheme><alias>such that</alias></lexeme>
  <lexeme><grapheme>WLOG</grapheme><alias>without loss of generality</alias></lexeme>
  <lexeme><grapheme>wlog</grapheme><alias>without loss of generality</alias></lexeme>
  <lexeme><grapheme>QED</grapheme><alias>Q E D</alias></lexeme>
  <lexeme><grapheme>cf.</grapheme><alias>compare</alias></lexeme>
  <lexeme><grapheme>etc.</grapheme><alias>et cetera</alias></lexeme>
  <lexeme><grapheme>vs.</grapheme><alias>versus</alias></lexeme>

  <!-- 7. Single capital letters used as variables (added 2026-09-02, listener-reported).
       A standalone capital V after a sibilant elides: "times V of T" and "one minus V of T"
       were both heard as "five of T" by a listener, and an isolated-clip Scribe pass
       transcribed them "length of T" and "f of T". NB a FULL-FRAME Scribe pass transcribes
       them CORRECTLY — its language model repairs the letter from context, so full-context
       ASR is a false-clean detector for this class; only a short isolated clip exposes it.
       "vee" is the phonetic respelling, matching section 3b's pie/roe pattern rather than
       the bare letter-name ElevenLabs mangles. Subtitles are unaffected (alignment is
       against the input text, so the SRT still reads "V"). -->
  <lexeme><grapheme>V</grapheme><alias>vee</alias></lexeme>

  <!-- 8. Mathematicians' names. The voice reads "Euler" as "YOO-ler"; it is "OY-ler".
       Hyphen-bound forms like "Euler-Lagrange" would need their OWN rule (cf. chi-squared).
       Isolated-clip ASR can't confirm this one: it writes "Euler" for both readings. -->
  <lexeme><grapheme>Euler</grapheme><alias>Oiler</alias></lexeme>
  <lexeme><grapheme>Euler's</grapheme><alias>Oiler's</alias></lexeme>
  <lexeme><grapheme>Euler’s</grapheme><alias>Oiler’s</alias></lexeme>
  <lexeme><grapheme>Eulerian</grapheme><alias>Oilerian</alias></lexeme>

  <!-- 9. Lowercase variable i -> "eye". Narration keeps the screen's case, so the imaginary
       unit / index i is spoken lowercase, and the multilingual model can give a bare "i" its
       Romance "ee" reading ("e to the i theta" heard as "e to the E theta"). Safe globally: a
       bare lowercase "i" is never an English word (the pronoun is "I"), and matching is
       case-sensitive. Hyphen-bound forms need their OWN rules (cf. chi-squared). -->
  <lexeme><grapheme>i</grapheme><alias>eye</alias></lexeme>
  <lexeme><grapheme>i-hat</grapheme><alias>eye-hat</alias></lexeme>
  <lexeme><grapheme>i-th</grapheme><alias>eye-th</alias></lexeme>
  <lexeme><grapheme>i-prime</grapheme><alias>eye-prime</alias></lexeme>
  <lexeme><grapheme>i-pound</grapheme><alias>eye-pound</alias></lexeme>
  <lexeme><grapheme>i-dollar</grapheme><alias>eye-dollar</alias></lexeme>
  <lexeme><grapheme>i-stem</grapheme><alias>eye-stem</alias></lexeme>
  <lexeme><grapheme>i-stems</grapheme><alias>eye-stems</alias></lexeme>
  <lexeme><grapheme>i-component</grapheme><alias>eye-component</alias></lexeme>
  <lexeme><grapheme>i-components</grapheme><alias>eye-components</alias></lexeme>
  <lexeme><grapheme>i-zero</grapheme><alias>eye-zero</alias></lexeme>
  <lexeme><grapheme>i-j</grapheme><alias>eye-j</alias></lexeme>

  <!-- 10. mu -> "mew". Left to itself the voice drifts between "myoo" and "moo" (isolated-clip
       ASR heard "Mew", "Muse", "More?", "new"). Most uses are hyphen-bound (mu-sub-k, mu-K,
       mu-hat-I, …) and each hyphen-bound form needs its OWN rule (cf. chi-squared), so the
       generic families are listed: mu-<letter>, mu-sub-<letter>, mu-hat(-<letter>),
       mu-<letter>-bar, mu-<number>. A lowercase i inside a form becomes "eye" to match rule 9.
       Bare capital "Mu" is deliberately NOT aliased (names such as Mu'awiya); sentence-initial
       forms are "Mu-hat-…" / "Mu-sub-…". -->
  <lexeme><grapheme>mu</grapheme><alias>mew</alias></lexeme>
  <lexeme><grapheme>mu-a</grapheme><alias>mew-a</alias></lexeme>
  <lexeme><grapheme>mu-b</grapheme><alias>mew-b</alias></lexeme>
  <lexeme><grapheme>mu-c</grapheme><alias>mew-c</alias></lexeme>
  <lexeme><grapheme>mu-d</grapheme><alias>mew-d</alias></lexeme>
  <lexeme><grapheme>mu-e</grapheme><alias>mew-e</alias></lexeme>
  <lexeme><grapheme>mu-f</grapheme><alias>mew-f</alias></lexeme>
  <lexeme><grapheme>mu-g</grapheme><alias>mew-g</alias></lexeme>
  <lexeme><grapheme>mu-h</grapheme><alias>mew-h</alias></lexeme>
  <lexeme><grapheme>mu-i</grapheme><alias>mew-eye</alias></lexeme>
  <lexeme><grapheme>mu-j</grapheme><alias>mew-j</alias></lexeme>
  <lexeme><grapheme>mu-k</grapheme><alias>mew-k</alias></lexeme>
  <lexeme><grapheme>mu-l</grapheme><alias>mew-l</alias></lexeme>
  <lexeme><grapheme>mu-m</grapheme><alias>mew-m</alias></lexeme>
  <lexeme><grapheme>mu-n</grapheme><alias>mew-n</alias></lexeme>
  <lexeme><grapheme>mu-o</grapheme><alias>mew-o</alias></lexeme>
  <lexeme><grapheme>mu-p</grapheme><alias>mew-p</alias></lexeme>
  <lexeme><grapheme>mu-q</grapheme><alias>mew-q</alias></lexeme>
  <lexeme><grapheme>mu-r</grapheme><alias>mew-r</alias></lexeme>
  <lexeme><grapheme>mu-s</grapheme><alias>mew-s</alias></lexeme>
  <lexeme><grapheme>mu-t</grapheme><alias>mew-t</alias></lexeme>
  <lexeme><grapheme>mu-u</grapheme><alias>mew-u</alias></lexeme>
  <lexeme><grapheme>mu-v</grapheme><alias>mew-v</alias></lexeme>
  <lexeme><grapheme>mu-w</grapheme><alias>mew-w</alias></lexeme>
  <lexeme><grapheme>mu-x</grapheme><alias>mew-x</alias></lexeme>
  <lexeme><grapheme>mu-y</grapheme><alias>mew-y</alias></lexeme>
  <lexeme><grapheme>mu-z</grapheme><alias>mew-z</alias></lexeme>
  <lexeme><grapheme>mu-A</grapheme><alias>mew-A</alias></lexeme>
  <lexeme><grapheme>mu-B</grapheme><alias>mew-B</alias></lexeme>
  <lexeme><grapheme>mu-C</grapheme><alias>mew-C</alias></lexeme>
  <lexeme><grapheme>mu-D</grapheme><alias>mew-D</alias></lexeme>
  <lexeme><grapheme>mu-E</grapheme><alias>mew-E</alias></lexeme>
  <lexeme><grapheme>mu-F</grapheme><alias>mew-F</alias></lexeme>
  <lexeme><grapheme>mu-G</grapheme><alias>mew-G</alias></lexeme>
  <lexeme><grapheme>mu-H</grapheme><alias>mew-H</alias></lexeme>
  <lexeme><grapheme>mu-I</grapheme><alias>mew-I</alias></lexeme>
  <lexeme><grapheme>mu-J</grapheme><alias>mew-J</alias></lexeme>
  <lexeme><grapheme>mu-K</grapheme><alias>mew-K</alias></lexeme>
  <lexeme><grapheme>mu-L</grapheme><alias>mew-L</alias></lexeme>
  <lexeme><grapheme>mu-M</grapheme><alias>mew-M</alias></lexeme>
  <lexeme><grapheme>mu-N</grapheme><alias>mew-N</alias></lexeme>
  <lexeme><grapheme>mu-O</grapheme><alias>mew-O</alias></lexeme>
  <lexeme><grapheme>mu-P</grapheme><alias>mew-P</alias></lexeme>
  <lexeme><grapheme>mu-Q</grapheme><alias>mew-Q</alias></lexeme>
  <lexeme><grapheme>mu-R</grapheme><alias>mew-R</alias></lexeme>
  <lexeme><grapheme>mu-S</grapheme><alias>mew-S</alias></lexeme>
  <lexeme><grapheme>mu-T</grapheme><alias>mew-T</alias></lexeme>
  <lexeme><grapheme>mu-U</grapheme><alias>mew-U</alias></lexeme>
  <lexeme><grapheme>mu-V</grapheme><alias>mew-V</alias></lexeme>
  <lexeme><grapheme>mu-W</grapheme><alias>mew-W</alias></lexeme>
  <lexeme><grapheme>mu-X</grapheme><alias>mew-X</alias></lexeme>
  <lexeme><grapheme>mu-Y</grapheme><alias>mew-Y</alias></lexeme>
  <lexeme><grapheme>mu-Z</grapheme><alias>mew-Z</alias></lexeme>
  <lexeme><grapheme>mu-sub-a</grapheme><alias>mew-sub-a</alias></lexeme>
  <lexeme><grapheme>mu-sub-b</grapheme><alias>mew-sub-b</alias></lexeme>
  <lexeme><grapheme>mu-sub-c</grapheme><alias>mew-sub-c</alias></lexeme>
  <lexeme><grapheme>mu-sub-d</grapheme><alias>mew-sub-d</alias></lexeme>
  <lexeme><grapheme>mu-sub-e</grapheme><alias>mew-sub-e</alias></lexeme>
  <lexeme><grapheme>mu-sub-f</grapheme><alias>mew-sub-f</alias></lexeme>
  <lexeme><grapheme>mu-sub-g</grapheme><alias>mew-sub-g</alias></lexeme>
  <lexeme><grapheme>mu-sub-h</grapheme><alias>mew-sub-h</alias></lexeme>
  <lexeme><grapheme>mu-sub-i</grapheme><alias>mew-sub-eye</alias></lexeme>
  <lexeme><grapheme>mu-sub-j</grapheme><alias>mew-sub-j</alias></lexeme>
  <lexeme><grapheme>mu-sub-k</grapheme><alias>mew-sub-k</alias></lexeme>
  <lexeme><grapheme>mu-sub-l</grapheme><alias>mew-sub-l</alias></lexeme>
  <lexeme><grapheme>mu-sub-m</grapheme><alias>mew-sub-m</alias></lexeme>
  <lexeme><grapheme>mu-sub-n</grapheme><alias>mew-sub-n</alias></lexeme>
  <lexeme><grapheme>mu-sub-o</grapheme><alias>mew-sub-o</alias></lexeme>
  <lexeme><grapheme>mu-sub-p</grapheme><alias>mew-sub-p</alias></lexeme>
  <lexeme><grapheme>mu-sub-q</grapheme><alias>mew-sub-q</alias></lexeme>
  <lexeme><grapheme>mu-sub-r</grapheme><alias>mew-sub-r</alias></lexeme>
  <lexeme><grapheme>mu-sub-s</grapheme><alias>mew-sub-s</alias></lexeme>
  <lexeme><grapheme>mu-sub-t</grapheme><alias>mew-sub-t</alias></lexeme>
  <lexeme><grapheme>mu-sub-u</grapheme><alias>mew-sub-u</alias></lexeme>
  <lexeme><grapheme>mu-sub-v</grapheme><alias>mew-sub-v</alias></lexeme>
  <lexeme><grapheme>mu-sub-w</grapheme><alias>mew-sub-w</alias></lexeme>
  <lexeme><grapheme>mu-sub-x</grapheme><alias>mew-sub-x</alias></lexeme>
  <lexeme><grapheme>mu-sub-y</grapheme><alias>mew-sub-y</alias></lexeme>
  <lexeme><grapheme>mu-sub-z</grapheme><alias>mew-sub-z</alias></lexeme>
  <lexeme><grapheme>mu-sub-A</grapheme><alias>mew-sub-A</alias></lexeme>
  <lexeme><grapheme>mu-sub-B</grapheme><alias>mew-sub-B</alias></lexeme>
  <lexeme><grapheme>mu-sub-C</grapheme><alias>mew-sub-C</alias></lexeme>
  <lexeme><grapheme>mu-sub-D</grapheme><alias>mew-sub-D</alias></lexeme>
  <lexeme><grapheme>mu-sub-E</grapheme><alias>mew-sub-E</alias></lexeme>
  <lexeme><grapheme>mu-sub-F</grapheme><alias>mew-sub-F</alias></lexeme>
  <lexeme><grapheme>mu-sub-G</grapheme><alias>mew-sub-G</alias></lexeme>
  <lexeme><grapheme>mu-sub-H</grapheme><alias>mew-sub-H</alias></lexeme>
  <lexeme><grapheme>mu-sub-I</grapheme><alias>mew-sub-I</alias></lexeme>
  <lexeme><grapheme>mu-sub-J</grapheme><alias>mew-sub-J</alias></lexeme>
  <lexeme><grapheme>mu-sub-K</grapheme><alias>mew-sub-K</alias></lexeme>
  <lexeme><grapheme>mu-sub-L</grapheme><alias>mew-sub-L</alias></lexeme>
  <lexeme><grapheme>mu-sub-M</grapheme><alias>mew-sub-M</alias></lexeme>
  <lexeme><grapheme>mu-sub-N</grapheme><alias>mew-sub-N</alias></lexeme>
  <lexeme><grapheme>mu-sub-O</grapheme><alias>mew-sub-O</alias></lexeme>
  <lexeme><grapheme>mu-sub-P</grapheme><alias>mew-sub-P</alias></lexeme>
  <lexeme><grapheme>mu-sub-Q</grapheme><alias>mew-sub-Q</alias></lexeme>
  <lexeme><grapheme>mu-sub-R</grapheme><alias>mew-sub-R</alias></lexeme>
  <lexeme><grapheme>mu-sub-S</grapheme><alias>mew-sub-S</alias></lexeme>
  <lexeme><grapheme>mu-sub-T</grapheme><alias>mew-sub-T</alias></lexeme>
  <lexeme><grapheme>mu-sub-U</grapheme><alias>mew-sub-U</alias></lexeme>
  <lexeme><grapheme>mu-sub-V</grapheme><alias>mew-sub-V</alias></lexeme>
  <lexeme><grapheme>mu-sub-W</grapheme><alias>mew-sub-W</alias></lexeme>
  <lexeme><grapheme>mu-sub-X</grapheme><alias>mew-sub-X</alias></lexeme>
  <lexeme><grapheme>mu-sub-Y</grapheme><alias>mew-sub-Y</alias></lexeme>
  <lexeme><grapheme>mu-sub-Z</grapheme><alias>mew-sub-Z</alias></lexeme>
  <lexeme><grapheme>mu-hat</grapheme><alias>mew-hat</alias></lexeme>
  <lexeme><grapheme>Mu-hat</grapheme><alias>Mew-hat</alias></lexeme>
  <lexeme><grapheme>mu-hat-i</grapheme><alias>mew-hat-eye</alias></lexeme>
  <lexeme><grapheme>mu-hat-I</grapheme><alias>mew-hat-I</alias></lexeme>
  <lexeme><grapheme>mu-hat-j</grapheme><alias>mew-hat-j</alias></lexeme>
  <lexeme><grapheme>mu-hat-J</grapheme><alias>mew-hat-J</alias></lexeme>
  <lexeme><grapheme>mu-hat-k</grapheme><alias>mew-hat-k</alias></lexeme>
  <lexeme><grapheme>mu-hat-K</grapheme><alias>mew-hat-K</alias></lexeme>
  <lexeme><grapheme>mu-hat-n</grapheme><alias>mew-hat-n</alias></lexeme>
  <lexeme><grapheme>mu-hat-N</grapheme><alias>mew-hat-N</alias></lexeme>
  <lexeme><grapheme>mu-hat-p</grapheme><alias>mew-hat-p</alias></lexeme>
  <lexeme><grapheme>mu-hat-P</grapheme><alias>mew-hat-P</alias></lexeme>
  <lexeme><grapheme>mu-hat-s</grapheme><alias>mew-hat-s</alias></lexeme>
  <lexeme><grapheme>mu-hat-S</grapheme><alias>mew-hat-S</alias></lexeme>
  <lexeme><grapheme>Mu-hat-i</grapheme><alias>Mew-hat-eye</alias></lexeme>
  <lexeme><grapheme>Mu-hat-I</grapheme><alias>Mew-hat-I</alias></lexeme>
  <lexeme><grapheme>Mu-hat-j</grapheme><alias>Mew-hat-j</alias></lexeme>
  <lexeme><grapheme>Mu-hat-J</grapheme><alias>Mew-hat-J</alias></lexeme>
  <lexeme><grapheme>Mu-hat-k</grapheme><alias>Mew-hat-k</alias></lexeme>
  <lexeme><grapheme>Mu-hat-K</grapheme><alias>Mew-hat-K</alias></lexeme>
  <lexeme><grapheme>Mu-hat-n</grapheme><alias>Mew-hat-n</alias></lexeme>
  <lexeme><grapheme>Mu-hat-N</grapheme><alias>Mew-hat-N</alias></lexeme>
  <lexeme><grapheme>Mu-hat-p</grapheme><alias>Mew-hat-p</alias></lexeme>
  <lexeme><grapheme>Mu-hat-P</grapheme><alias>Mew-hat-P</alias></lexeme>
  <lexeme><grapheme>Mu-hat-s</grapheme><alias>Mew-hat-s</alias></lexeme>
  <lexeme><grapheme>Mu-hat-S</grapheme><alias>Mew-hat-S</alias></lexeme>
  <lexeme><grapheme>Mu-sub-i</grapheme><alias>Mew-sub-eye</alias></lexeme>
  <lexeme><grapheme>Mu-sub-I</grapheme><alias>Mew-sub-I</alias></lexeme>
  <lexeme><grapheme>Mu-sub-j</grapheme><alias>Mew-sub-j</alias></lexeme>
  <lexeme><grapheme>Mu-sub-J</grapheme><alias>Mew-sub-J</alias></lexeme>
  <lexeme><grapheme>Mu-sub-k</grapheme><alias>Mew-sub-k</alias></lexeme>
  <lexeme><grapheme>Mu-sub-K</grapheme><alias>Mew-sub-K</alias></lexeme>
  <lexeme><grapheme>Mu-sub-s</grapheme><alias>Mew-sub-s</alias></lexeme>
  <lexeme><grapheme>Mu-sub-S</grapheme><alias>Mew-sub-S</alias></lexeme>
  <lexeme><grapheme>mu-k-bar</grapheme><alias>mew-k-bar</alias></lexeme>
  <lexeme><grapheme>mu-K-bar</grapheme><alias>mew-K-bar</alias></lexeme>
  <lexeme><grapheme>mu-s-bar</grapheme><alias>mew-s-bar</alias></lexeme>
  <lexeme><grapheme>mu-S-bar</grapheme><alias>mew-S-bar</alias></lexeme>
  <lexeme><grapheme>mu-i-bar</grapheme><alias>mew-eye-bar</alias></lexeme>
  <lexeme><grapheme>mu-j-bar</grapheme><alias>mew-j-bar</alias></lexeme>
  <lexeme><grapheme>mu-bar</grapheme><alias>mew-bar</alias></lexeme>
  <lexeme><grapheme>mu-squared</grapheme><alias>mew-squared</alias></lexeme>
  <lexeme><grapheme>mu-naught</grapheme><alias>mew-naught</alias></lexeme>
  <lexeme><grapheme>mu-zero</grapheme><alias>mew-zero</alias></lexeme>
  <lexeme><grapheme>mu-one</grapheme><alias>mew-one</alias></lexeme>
  <lexeme><grapheme>mu-two</grapheme><alias>mew-two</alias></lexeme>
  <lexeme><grapheme>mu-three</grapheme><alias>mew-three</alias></lexeme>
  <lexeme><grapheme>mu-four</grapheme><alias>mew-four</alias></lexeme>
  <lexeme><grapheme>mu-sub-zero</grapheme><alias>mew-sub-zero</alias></lexeme>
  <lexeme><grapheme>mu-sub-one</grapheme><alias>mew-sub-one</alias></lexeme>
  <lexeme><grapheme>mu-sub-two</grapheme><alias>mew-sub-two</alias></lexeme>
  <lexeme><grapheme>mu-prime</grapheme><alias>mew-prime</alias></lexeme>
  <lexeme><grapheme>mu-star</grapheme><alias>mew-star</alias></lexeme>
</lexicon>
