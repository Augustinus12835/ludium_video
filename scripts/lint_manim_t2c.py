#!/usr/bin/env python
"""Lint authored Manim frame sources for the silent `t2c` killers.

Manim's `tex_to_color_map` / `t2c` works by SUBSTRING SURGERY: it splits the tex
string at each key occurrence and rebuilds it as a multi-part MathTex. Several
ways of writing a key make that split produce invalid LaTeX or a junk part, and
in every case the render either dies with a misleading error or *succeeds* with a
broken frame. This lint catches them before a render is paid for.

This lint deliberately owns only what a compile CANNOT tell you. Anything that
kills LaTeX outright is caught definitively, with zero false positives, by
`preflight_manim.py` (which really compiles). Static analysis cannot predict those
reliably -- see the calibration note below -- so they are warnings here.

ERRORS (verified to render "successfully" while leaving the frame wrong, i.e.
exactly the class preflight is blind to):

  STRANDED-SPACING-PART two keys separated only by spacing (rule 32). Verified:
                        MathTex(r"a_x \\Delta x", t2c={"a_x":…, r"\\Delta x":…})
                        renders with part widths [0.447, 0.0, 0.654] -- a
                        zero-width part. It strands at the origin and inflates the
                        group bbox, so a later SurroundingRectangle comes out
                        enormous and slices its neighbours (three re-renders on
                        CM_Ch13 Video-2). Fold the spacing into the key: "a_x\\,".
  KEY-NOT-FOUND         key never occurs -> a dead key, i.e. a missed color link.

WARNINGS (may be fatal, may be fine -- let preflight decide):

  KEY-INSIDE-BRACES     key at brace depth > 0. Sometimes fatal:
                        MathTex(r"\\frac{\\vec{F}_{2,1}}{m_1}",
                        t2c={r"\\vec{F}_{2,1}": BLUE}) raises "latex error
                        converting to dvi". Often fine: the same shape inside
                        \\substack{...} renders cleanly. Brace depth alone does not
                        predict it, so this is an eyeball prompt, not a block.
  KEY-INSIDE-LEFT-RIGHT key between \\left( and \\right. Usually fine -- 16 such
                        keys across CM_Ch13 Video-9 all rendered. If preflight does
                        fail on one, switch to \\big(/\\big).

Calibrate before adding a check here; a lint that cries wolf gets ignored. Two
candidate checks were tried and dropped for firing on known-good frames: "key
followed by ^2" (MathTex("v_{y,f}^2 - …", t2c={"v_{y,f}": …}) renders cleanly) and
treating brace nesting as fatal.

Keys are matched longest-first, mirroring Manim's own greedy behaviour, and
module-level string constants (including `A = B + "..."` concatenation) are
resolved so keys/expressions built from constants are still checked.

usage:
    lint_manim_t2c.py pipeline/<L>/Video-N            # every frame in the video
    lint_manim_t2c.py path/to/frame_3_manim.py [...]  # specific files

Exit code is non-zero when any real problem is found, so it can gate a render.
"""
import ast
import re
import sys
from pathlib import Path

# Spacing that produces a part with no glyphs in it.
SPACING_ONLY = re.compile(r"^(?:\s|\\,|\\;|\\:|\\!|\\ |\\quad|\\qquad|~)*$")

# Calls whose first positional arg is a tex string and which accept a t2c mapping.
TEX_CALL_HINTS = ("MathTex", "Tex", "add_step", "add_")


def _resolve(node, consts):
    """Resolve an AST node to a str via literals, module constants and `+`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve(node.left, consts)
        right = _resolve(node.right, consts)
        if left is not None and right is not None:
            return left + right
    return None


def _module_constants(tree):
    """Module-level string bindings, resolved in order so later ones can build on
    earlier ones (ROW = LHS + " = " + RHS)."""
    consts = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            value = _resolve(node.value, consts)
            if value is not None:
                consts[node.targets[0].id] = value
    return consts


def _nesting_at(tex, idx):
    """(brace_depth, left_right_depth) immediately before position `idx`."""
    depth = left_right = 0
    i = 0
    while i < idx:
        if tex.startswith(r"\left", i):
            left_right += 1
            i += 5
            continue
        if tex.startswith(r"\right", i):
            left_right -= 1
            i += 6
            continue
        if tex[i] == "{" and (i == 0 or tex[i - 1] != "\\"):
            depth += 1
        elif tex[i] == "}" and (i == 0 or tex[i - 1] != "\\"):
            depth -= 1
        i += 1
    return depth, left_right


def _key_spans(tex, keys):
    """Non-overlapping key occurrences, longest key first (Manim matches greedily)."""
    spans = []
    missing = []
    for key in sorted(keys, key=len, reverse=True):
        found = False
        start = 0
        while (i := tex.find(key, start)) != -1:
            found = True
            start = i + 1
            if any(s < i + len(key) and i < e for s, e, _ in spans):
                continue  # already covered by a longer key
            spans.append((i, i + len(key), key))
        if not found:
            missing.append(key)
    spans.sort()
    return spans, missing


def _check(tex, keys, where, errors, warnings):
    spans, missing = _key_spans(tex, keys)
    for key in missing:
        errors.append((where, "KEY-NOT-FOUND", repr(key), tex))

    for start, _end, key in spans:
        depth, left_right = _nesting_at(tex, start)
        if depth > 0:
            warnings.append((where, "KEY-INSIDE-BRACES", repr(key), tex))
        elif left_right > 0:
            warnings.append((where, "KEY-INSIDE-LEFT-RIGHT", repr(key), tex))

    # Gaps between consecutive keys, plus the head and tail fragments.
    for (_s1, e1, k1), (s2, _e2, k2) in zip(spans, spans[1:]):
        gap = tex[e1:s2]
        if gap and SPACING_ONLY.match(gap):
            errors.append(
                (where, "STRANDED-SPACING-PART",
                 f"{gap!r} between {k1!r} and {k2!r}", tex))

    if spans:
        head, tail = tex[:spans[0][0]], tex[spans[-1][1]:]
        for frag, side in ((head, "head"), (tail, "tail")):
            if frag and SPACING_ONLY.match(frag):
                errors.append(
                    (where, "STRANDED-SPACING-PART", f"{side} fragment {frag!r}", tex))


def scan_file(path):
    """Return (n_calls_checked, errors, warnings, skipped) for one frame source."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    consts = _module_constants(tree)
    errors, warnings, skipped = [], [], []
    checked = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            fname = node.func.id
        elif isinstance(node.func, ast.Attribute):
            fname = node.func.attr
        else:
            continue
        if not any(h in fname for h in TEX_CALL_HINTS):
            continue

        # `t2c` colors the tex (first positional arg); `label_t2c` colors the
        # separate yellow note string, so each is checked against its own target.
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        targets = []
        if "tex_to_color_map" in kwargs or "t2c" in kwargs:
            mapping = kwargs.get("tex_to_color_map") or kwargs.get("t2c")
            targets.append(("t2c", mapping, node.args[0] if node.args else None))
        if "label_t2c" in kwargs:
            targets.append(("label_t2c", kwargs["label_t2c"], kwargs.get("note")))

        for kind, mapping, subject_node in targets:
            where = f"{fname}() line {node.lineno} [{kind}]"
            if not isinstance(mapping, ast.Dict):
                skipped.append(f"{where}: non-literal color map -- CHECK BY HAND")
                continue
            if subject_node is None:
                skipped.append(f"{where}: no target string to check against")
                continue

            keys = []
            for k in mapping.keys:
                s = _resolve(k, consts)
                if s is None:
                    skipped.append(f"{where}: unresolved key -- CHECK BY HAND")
                else:
                    keys.append(s)
            if not keys:
                continue

            subject = _resolve(subject_node, consts)
            if subject is None:
                skipped.append(f"{where}: unresolved target string -- CHECK BY HAND")
                continue

            checked += 1
            if kind == "label_t2c":
                # make_note_label splits plain Tex prose; only a dead key matters.
                _, missing = _key_spans(subject, keys)
                for key in missing:
                    errors.append((where, "KEY-NOT-FOUND", repr(key), subject))
            else:
                _check(subject, keys, where, errors, warnings)

    return checked, errors, warnings, skipped


def _frame_sources(args):
    if len(args) == 1 and Path(args[0]).is_dir():
        frames = Path(args[0]) / "frames"
        base = frames if frames.is_dir() else Path(args[0])
        return sorted(base.glob("frame_*_manim.py"),
                      key=lambda p: int(re.search(r"frame_(\d+)_", p.name).group(1)))
    return [Path(a) for a in args]


def main(argv):
    paths = _frame_sources(argv)
    if not paths:
        print(__doc__)
        return 2

    n_errors = n_warnings = 0
    for path in paths:
        if not path.exists():
            print(f"{path}: (missing)")
            continue
        checked, errors, warnings, skipped = scan_file(path)
        n_errors += len(errors)
        n_warnings += len(warnings)
        status = "clean" if not errors else f"{len(errors)} ERROR(S)"
        if warnings:
            status += f", {len(warnings)} warning(s)"
        print(f"{path.name}: {checked} color-mapped call(s) -- {status}")
        for where, kind, detail, tex in errors:
            print(f"  ERROR   {where:<34} {kind:<24} {detail}")
            print(f"          {'':<34} in: {tex[:80]}...")
        for where, kind, detail, tex in warnings:
            print(f"  warn    {where:<34} {kind:<24} {detail}")
        for note in skipped:
            print(f"  (skipped) {note}")

    print()
    if n_errors:
        print(f"LINT FAILED: {n_errors} error(s), {n_warnings} warning(s)")
    else:
        print(f"LINT CLEAN ({n_warnings} warning(s) -- eyeball, not blocking)")
    return 1 if n_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
