#!/usr/bin/env python3
"""
Manim layout probes: scroll simulation, width/height measurement, Tex compile safety,
glyph-count parity.

WHY THIS EXISTS. Every script reviewer and every Stage-2 producer used to re-author these
four probes from scratch, once per video. On one five-video lecture that was ten agents
writing the same `make_step_column` simulator, the same Tex compile checker and the same
glyph differ — and two of them shipped a probe that reported a defect that was not there
(one crop ate a header band; one measured OT1 instead of the T1 encoding we actually
render in). Import these instead.

    from scripts.utils.manim_probe import simulate_stack, measure, compile_check, glyph_parity

    python scripts/utils/manim_probe.py --self-test          # prove every probe can FAIL
    python scripts/utils/manim_probe.py --stack 6 --scale 0.65 --step-buff 0.22
    python scripts/utils/manim_probe.py --measure 'x^{n-1}' '\\frac{u}{v}'

TWO THINGS THESE ENCODE THAT COST REAL DEFECTS WHEN REDISCOVERED BY HAND:

 * **The scroll trigger is the `board_top` 2.3 -> `scroll_bottom` -3.2 band (~5.5 u usable),
   NOT the 7.4 u safe height.** A stack checked against 7.4 u "does not scroll", does, and
   drops its TOP row — which is usually the punchline. Layout C's bottom zone raises
   `scroll_bottom` to -0.8, which costs three rows at the default scale.
 * **`render_manim_scene()` injects a T1 `fontenc` + `textcomp` preamble** (rule 67). A bare
   `from manim import *` probe measures OT1, an encoding this pipeline never ships, so its
   widths and its crash/no-crash verdicts are both wrong. `_ensure_preamble()` below adds it.

Every probe here is paired with a negative control in `--self-test`. A check that has never
fired is untested, not reassuring.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_PREAMBLE_DONE = False


def _ensure_preamble():
    """Match the render's LaTeX encoding. Without this the probe measures OT1 (rule 67)."""
    global _PREAMBLE_DONE
    from manim import config
    if not _PREAMBLE_DONE:
        config.tex_template.add_to_preamble(
            r"\usepackage[T1]{fontenc}" + "\n" + r"\usepackage{textcomp}")
        _PREAMBLE_DONE = True


def measure(tex, scale=1.0, math=True, parts=None):
    """Return {width, height, glyphs} for a MathTex/Tex at `scale`, measured not estimated.

    The `0.222 * chars + 2.2` rule of thumb UNDER-estimates inline glyph runs at font_size=32
    (6 of 7 recorded cases, by up to 1.29 u) and badly OVER-estimates \\frac-heavy lines,
    because a stacked fraction is about half the width and twice the height of the same
    glyphs inline. Never estimate; call this.
    """
    _ensure_preamble()
    from manim import MathTex, Tex
    cls = MathTex if math else Tex
    m = cls(*parts) if parts else cls(tex)
    if scale != 1.0:
        m.scale(scale)
    return {"width": round(float(m.width), 4),
            "height": round(float(m.height), 4),
            "glyphs": len(m.family_members_with_points())}


def simulate_stack(rows, scale=0.75, step_buff=0.28, label_scale=0.4,
                   board_top=2.3, scroll_bottom=-3.2, max_w=11, notes=None,
                   label_pad=0.145, safety=0.8):
    """Replicate make_step_column()'s geometry for `rows` (list of LaTeX strings).

    Returns {scrolls, first_scroll_row, bottom, margin, stack_height, rows_detail}.
    `margin` is the room left above `scroll_bottom`; rule 52 fires under ~0.08 u, so treat
    anything below ~0.2 u as "tune it or lose a row".

    TWO DETAILS THAT MAKE THE DIFFERENCE BETWEEN A USEFUL AND A DANGEROUS ANSWER — both were
    wrong in the first version of this function, and both errors pointed the SAME optimistic
    way (reporting ~0.9 u more headroom than the frame really has, on a six-row stack):

      * The FIRST group is CENTRED at `board_top` (`grp.move_to(UP * board_top)` in the
        helper), not hung from it. Its top therefore sits at `board_top + h/2`.
      * `add_step` always attaches a note label under the step at `buff=0.1`, and a REAL
        one-line note measures ~0.145 u at `label_scale=0.4` — not the ~0.12 u an empty
        one costs. Pass the actual note strings as `notes` and they are measured; the
        `label_pad` default is the conservative fallback when you do not.

    An optimistic scroll simulator is worse than none, because it green-lights the stack that
    drops its top row — which is usually the punchline.

    **`safety` (default 0.8 u) exists because this function is still an APPROXIMATION of the
    helper and is known to read optimistic.** Cross-checked against an independent
    real-mobject measurement of the same six-row stack on a real frame, it under-reported the
    stack height by 0.73-0.90 u even after both fixes above — the residue is note wording and
    per-row ink this function cannot know without the frame's real strings. The allowance is
    subtracted before the scroll verdict, so the verdict errs toward "cut a row". Pass
    `safety=0` only when you have supplied the frame's ACTUAL `rows` and `notes`, and even
    then confirm on a rendered still at the punchline beat — the playbook requires that
    anyway, and no simulation replaces it.
    """
    _ensure_preamble()
    from manim import MathTex, Tex
    detail, y, first_scroll = [], None, None
    for i, r in enumerate(rows):
        step = MathTex(r)
        step.scale(scale)
        if step.width > max_w:                       # add_step's clamp only ever SHRINKS
            step.scale_to_fit_width(max_w)
        sh, sw = float(step.height), float(step.width)
        if notes and i < len(notes) and notes[i]:
            lab = Tex(notes[i]); lab.scale(label_scale)
            lh = float(lab.height)
        else:
            lh = label_pad
        h = sh + 0.1 + lh                            # step + label buff + label ink
        if i == 0:
            top = board_top + h / 2.0                # the helper CENTRES the first group
        else:
            top = y - step_buff
        bottom = top - h
        if bottom < scroll_bottom + safety and first_scroll is None:
            first_scroll = i + 1                     # 1-indexed, as producers report it
        detail.append({"row": i + 1, "w": round(sw, 3), "h": round(h, 3),
                       "top": round(top, 3), "bottom": round(bottom, 3)})
        y = bottom
    top0 = detail[0]["top"] if detail else board_top
    return {"rows": len(rows), "scale": scale, "step_buff": step_buff,
            "stack_height": round(top0 - y, 3), "bottom": round(y, 3),
            "margin": round(y - scroll_bottom, 3),
            "margin_after_safety": round(y - scroll_bottom - safety, 3), "safety": safety,
            "scrolls": first_scroll is not None,
            "first_scroll_row": first_scroll, "rows_detail": detail}


def compile_check(strings, math=False):
    """Which of these would CRASH as Tex()/MathTex()? Returns {string: None | error}.

    The recurring killers: a bare `^` or `_` outside $...$ in a Tex note; a bare macro
    (`\\sin`) outside $...$; a `$` inside a MathTex spec; and raw Unicode math glyphs —
    U+22EF crashes outright, U+2212 crashes inside MathTex, and U+2192 does NOT crash but
    renders ~0.5 u narrower with the arrow silently absent.
    """
    _ensure_preamble()
    from manim import MathTex, Tex
    cls = MathTex if math else Tex
    out = {}
    for s in strings:
        try:
            cls(s)
            out[s] = None
        except Exception as e:                                  # noqa: BLE001
            out[s] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def glyph_parity(single, parts):
    """Compare glyph counts of a single-string MathTex against its multi-part split.

    A part split that silently drops a subscript renders at an IDENTICAL width and passes the
    LaTeX dry run — glyph count is the only thing that catches it. Note MathTex's default
    arg_separator is " " and Tex's is "" (rule 73), so a probe that rebuilds the reference
    with "".join(parts) MISMATCHES on a correct frame; this joins correctly.
    """
    a = measure(single)["glyphs"]
    b = measure(None, parts=parts)["glyphs"]
    return {"single": a, "parts": b, "parity": a == b}


def _self_test():
    print("manim_probe self-test — every probe must FIRE on a known-bad input\n")
    ok = True

    tall = [r"\frac{d}{dx}\left(\frac{u}{v}\right)"] * 10
    bad = simulate_stack(tall)
    good = simulate_stack([r"x^2"] * 3)
    print(f"  {'PASS' if bad['scrolls'] else 'FAIL'}  stack control (10 tall frac rows): "
          f"scrolls={bad['scrolls']} at row {bad['first_scroll_row']}, bottom {bad['bottom']}")
    print(f"  {'PASS' if not good['scrolls'] else 'FAIL'}  stack clean (3 short rows):      "
          f"scrolls={good['scrolls']}, margin {good['margin']} u")
    ok &= bad["scrolls"] and not good["scrolls"]

    crashes = compile_check([r"v = x^n", r"$v = x^n$", "let Δt → 0", r"let $\Delta t \to 0$"])
    fired = crashes[r"v = x^n"] is not None
    clean = crashes[r"$v = x^n$"] is None
    print(f"  {'PASS' if fired else 'FAIL'}  compile control (bare ^):        "
          f"{(crashes[r'v = x^n'] or 'compiled')[:60]}")
    print(f"  {'PASS' if clean else 'FAIL'}  compile clean ($-wrapped):       "
          f"{crashes[r'$v = x^n$'] or 'compiled'}")
    for s in ("let Δt → 0", r"let $\Delta t \to 0$"):
        print(f"        raw-glyph form {s!r}: {(crashes[s] or 'compiled')[:70]}")
    ok &= fired and clean

    par_bad = glyph_parity(r"x_{n} + 1", [r"x", r"+ 1"])          # a dropped subscript
    par_ok = glyph_parity(r"a + b", [r"a", r"+", r"b"])
    print(f"  {'PASS' if not par_bad['parity'] else 'FAIL'}  glyph control (dropped sub):     {par_bad}")
    print(f"  {'PASS' if par_ok['parity'] else 'FAIL'}  glyph clean (faithful split):    {par_ok}")
    ok &= (not par_bad["parity"]) and par_ok["parity"]

    w = measure(r"\frac{u}{v}")
    print(f"  {'PASS' if w['width'] > 0 else 'FAIL'}  measure returns geometry:        {w}")
    ok &= w["width"] > 0

    print("\n" + ("all probes fire on a control and stay silent on clean input"
                  if ok else "SELF-TEST FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Manim layout probes (scroll / width / compile / glyphs).")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--stack", type=int, help="simulate N identical fraction rows")
    ap.add_argument("--row", default=r"\frac{d}{dx}\left(\frac{u}{v}\right)", help="row LaTeX for --stack")
    ap.add_argument("--scale", type=float, default=0.75)
    ap.add_argument("--step-buff", type=float, default=0.28)
    ap.add_argument("--scroll-bottom", type=float, default=-3.2,
                    help="-3.2 default; Layout C's bottom zone is -0.8")
    ap.add_argument("--measure", nargs="+", help="measure these LaTeX strings")
    ap.add_argument("--compile-check", nargs="+", help="would these crash as Tex()?")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    if a.stack:
        print(simulate_stack([a.row] * a.stack, scale=a.scale, step_buff=a.step_buff,
                             scroll_bottom=a.scroll_bottom))
    if a.measure:
        for s in a.measure:
            print(f"{s!r}: {measure(s, scale=a.scale)}")
    if a.compile_check:
        for s, err in compile_check(a.compile_check).items():
            print(f"{s!r}: {err or 'compiles'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
