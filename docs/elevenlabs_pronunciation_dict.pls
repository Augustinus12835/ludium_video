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
  <lexeme><grapheme>μ</grapheme><alias>mu</alias></lexeme>
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
         nu   -> would clobber "nu"; mu/tau/eta read acceptably as words
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

</lexicon>
