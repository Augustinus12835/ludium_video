#!/usr/bin/env python3
"""
Audit a lecture's `figure_updates.json` — the log written by `render_step_prompt.py clean
--refresh-figures` — against the `content_cleaned.txt` it claims to describe.

    venv/bin/python scripts/audit_figure_updates.py pipeline/<L>          # one lecture
    venv/bin/python scripts/audit_figure_updates.py pipeline/Finance_*    # a course
    venv/bin/python scripts/audit_figure_updates.py --self-test

WHY THIS EXISTS. The dated-figure review asks a clean subagent to web-search every
currency-claiming number and log each refresh with its source. Two things go wrong silently:

  * a figure is RECALLED, not researched — the entry has no `source_url`, or the number in
    the prose is not the number in the log (the agent rounded one of them from memory);
  * the log is a PARAPHRASE — `refreshed` is meant to be a verbatim excerpt of the prose,
    but agents write an ellipsis-joined summary of several sentences, and then nothing can
    be grepped. On the first dated-course run 37 of 123 entries were like that: every number was
    in the prose, but the audit rule "every refreshed string must be findable" was
    unenforceable as written.

Each entry is therefore traced in tiers, most to least exact, and the tier is reported:

    verbatim     `refreshed` is a substring of content_cleaned.txt (what the prompt asks for)
    normalized   same after NFKC + quote/dash/whitespace/case folding (harmless)
    figures      the phrase is not there but EVERY numeric/date token in it is — a paraphrase
                 of prose that does carry the researched numbers (CHECK: fix the log entry,
                 not the prose)
    MISSING      at least one numeric token of `refreshed` is absent from the prose (BLOCK:
                 either the research never landed or the log and the prose disagree)

Other checks: `source_url` must be a URL (BLOCK otherwise — a removal entry, `refreshed` =
"(removed)", is exempt and is instead checked for the ORIGINAL claim having actually gone);
`as_of` present (CHECK); an ellipsis inside `refreshed` (CHECK — not a contiguous excerpt).
Exit status 1 on any BLOCK, so the orchestrator can gate on it.
"""
import argparse
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

FIGURE_UPDATES_FILENAME = "figure_updates.json"
CONTENT_FILENAME = "content_cleaned.txt"
REMOVED_MARKERS = ("removed", "dropped", "deleted", "n/a", "none")
SEVERITY = {"missing-figure": "BLOCK", "no-source-url": "BLOCK", "no-refreshed": "BLOCK",
            "removed-still-present": "BLOCK", "bad-json": "BLOCK",
            "paraphrased": "CHECK", "ellipsis": "CHECK", "no-as-of": "CHECK"}

# A number with its magnitude word or unit glued on: "$61.2 trillion", "38.1%", "2026",
# "3.63%", "eight cents" is NOT caught (words) — the figures tier is about digits.
NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s*(?:trillion|billion|million|thousand|percent|%|bp|bps)?",
                    re.IGNORECASE)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for a, b in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"),
                 ("—", "-"), ("–", "-"), ("--", "-")):
        s = s.replace(a, b)
    s = re.sub(r"\\%", "%", s)          # LaTeX-escaped percent in math-mode prose
    s = re.sub(r"\$(\d)", r"\1", s)      # "$4.17$" math delimiters vs "$4.17" currency: drop both
    s = re.sub(r"(\d|%)\$", r"\1", s)     # …and the closing math delimiter
    return re.sub(r"\s+", " ", s).strip().lower()


def _num_tokens(s: str):
    return [re.sub(r"\s+", "", t).rstrip(",") for t in NUM_RE.findall(_norm(s)) if re.search(r"\d", t)]


def is_removed(refreshed: str) -> bool:
    return refreshed.strip().strip("()").lower() in REMOVED_MARKERS


def load_updates(path: Path):
    """Return (items, error). Accepts {"figure_updates": [...]} or a bare list."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [], str(e)
    items = data.get("figure_updates", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return [], "figure_updates is not a list"
    return items, ""


def audit_entry(it: dict, idx: int, content: str, content_norm: str):
    """Returns (findings, tier). findings = [(idx, check, detail)]."""
    f = []
    refreshed = (it.get("refreshed") or "").strip()
    original = (it.get("original") or "").strip()
    url = (it.get("source_url") or "").strip()
    if not refreshed:
        return [(idx, "no-refreshed", "entry has no `refreshed` text")], "-"

    if is_removed(refreshed):
        # A cut claim: the thing to check is that the ORIGINAL wording is gone.
        tier = "removed"
        if original and _norm(original) in content_norm:
            f.append((idx, "removed-still-present",
                      f"logged as removed but the original wording is still in the prose: '{original[:60]}'"))
        if not it.get("note"):
            f.append((idx, "no-as-of", "a removal should carry a `note` saying why"))
        return f, tier

    if refreshed in content:
        tier = "verbatim"
    elif _norm(refreshed) in content_norm:
        tier = "normalized"
    else:
        nums = _num_tokens(refreshed)
        missing = [n for n in nums if n not in content_norm.replace(" ", "")
                   and n not in content_norm]
        if nums and not missing:
            tier = "figures"
            f.append((idx, "paraphrased",
                      f"`refreshed` is not an excerpt of the prose (its {len(nums)} figures are all present) — "
                      f"log the sentence verbatim: '{refreshed[:70]}…'"))
        elif nums:
            tier = "MISSING"
            f.append((idx, "missing-figure",
                      f"figure(s) {missing} in the log are NOT in content_cleaned.txt — recalled number, "
                      f"or the refresh never landed: '{refreshed[:70]}…'"))
        else:
            # No digits at all (a renamed institution, a wording change): fall back to word
            # overlap — a paraphrase of prose that IS there shares most of its content words.
            words = {w for w in re.findall(r"[a-z][a-z'-]{3,}", _norm(refreshed))}
            cw = set(re.findall(r"[a-z][a-z'-]{3,}", content_norm))
            share = len(words & cw) / len(words) if words else 0.0
            if share >= 0.8:
                tier = "figures"
                f.append((idx, "paraphrased",
                          f"`refreshed` (no figures) is not an excerpt of the prose ({share:.0%} of its words are) — "
                          f"log the sentence verbatim: '{refreshed[:70]}…'"))
            else:
                tier = "MISSING"
                f.append((idx, "missing-figure",
                          f"`refreshed` carries no digits and only {share:.0%} of its words are in the prose: "
                          f"'{refreshed[:70]}…'"))
    if "..." in refreshed or "…" in refreshed:
        f.append((idx, "ellipsis", "`refreshed` joins several sentences with an ellipsis — one entry per sentence"))
    if not url.startswith(("http://", "https://")):
        f.append((idx, "no-source-url",
                  f"no source_url — a figure without a source is a recalled figure, not a researched one: "
                  f"'{refreshed[:60]}…'"))
    if not (it.get("as_of") or "").strip():
        f.append((idx, "no-as-of", "no `as_of` — the prose must carry the date the figure describes"))
    return f, tier


def audit(pipeline_dir: Path):
    upd = pipeline_dir / FIGURE_UPDATES_FILENAME
    content_path = pipeline_dir / CONTENT_FILENAME
    if not upd.exists():
        return None, [], {}
    items, err = load_updates(upd)
    if err:
        return [(None, "bad-json", f"{upd}: {err}")], [], {}
    content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
    if not content:
        return [(None, "bad-json", f"{content_path} missing or empty — nothing to trace against")], [], {}
    cn = _norm(content)
    findings, tiers = [], []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            findings.append((i, "bad-json", "entry is not an object"))
            continue
        f, tier = audit_entry(it, i, content, cn)
        findings += f
        tiers.append(tier)
    counts = {t: tiers.count(t) for t in ("verbatim", "normalized", "figures", "MISSING", "removed") if tiers.count(t)}
    return findings, tiers, counts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pipeline_dirs", nargs="*", help="pipeline/<L> lecture folders")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if not a.pipeline_dirs:
        ap.error("pipeline_dirs required (or --self-test)")

    worst = 0
    report = {}
    for d in a.pipeline_dirs:
        d = Path(d)
        findings, tiers, counts = audit(d)
        if findings is None:
            if not a.json:
                print(f"{d.name}: no {FIGURE_UPDATES_FILENAME} (not a dated-figure run)")
            continue
        blocks = [x for x in findings if SEVERITY.get(x[1]) == "BLOCK"]
        worst = max(worst, 1 if blocks else 0)
        report[str(d)] = {"entries": len(tiers), "tiers": counts,
                          "findings": [{"entry": i, "check": c, "detail": t} for i, c, t in findings]}
        if a.json:
            continue
        print(f"{d.name}: {len(tiers)} entries  " + "  ".join(f"{k}={v}" for k, v in counts.items()))
        for i, c, t in sorted(findings, key=lambda x: 0 if SEVERITY.get(x[1]) == "BLOCK" else 1):
            print(f"    {SEVERITY.get(c, 'CHECK'):<5} [{c}] entry {i}: {t}")
    if a.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif report:
        tot = sum(r["entries"] for r in report.values())
        agg = {}
        for r in report.values():
            for k, v in r["tiers"].items():
                agg[k] = agg.get(k, 0) + v
        print(f"\nTOTAL {tot} entries over {len(report)} lecture(s): " + "  ".join(f"{k}={v}" for k, v in agg.items()))
        print("  verbatim/normalized = traceable as logged; figures = numbers traceable, log entry paraphrased;")
        print("  MISSING = the log and the prose disagree — treat as an unresearched figure until resolved.")
    return worst


def self_test():
    """Every detector must fire on a known-bad control and stay silent on a clean one."""
    content = ("In 2008 the bond market was roughly $30 trillion. U.S. fixed-income outstanding stood at "
               "$61.2 trillion in 2025, some 38.1% of the $160.7 trillion worldwide — as of 2025. "
               "The thirty-year Treasury yielded $4.17\\%$ that morning. He runs a fund.")
    clean = {"original": "the bond market is about $30 trillion",
             "refreshed": "U.S. fixed-income outstanding stood at $61.2 trillion in 2025, some 38.1% of the "
                          "$160.7 trillion worldwide — as of 2025.",
             "as_of": "2025", "source": "SIFMA", "source_url": "https://www.sifma.org/x", "note": "stale"}
    cases = [
        ("normalized quotes/dashes", dict(clean, refreshed=clean["refreshed"].replace("—", "--")), None, "normalized"),
        ("latex percent", dict(clean, refreshed="The thirty-year Treasury yielded 4.17% that morning."), None, "normalized"),
        ("paraphrase, figures present",
         dict(clean, refreshed="Fixed income reached $61.2 trillion in 2025 ... 38.1% of $160.7 trillion."),
         "paraphrased", "figures"),
        ("ellipsis", dict(clean, refreshed="Fixed income reached $61.2 trillion in 2025 ... 38.1% of $160.7 trillion."),
         "ellipsis", "figures"),
        ("missing figure", dict(clean, refreshed="Fixed income reached $59 trillion in 2025."), "missing-figure", "MISSING"),
        ("no digits, not in prose", dict(clean, refreshed="renamed Renaissance Technologies"), "missing-figure", "MISSING"),
        ("no digits, paraphrase", dict(clean, refreshed="Thirty-year Treasury yielded that morning, he runs a fund."),
         "paraphrased", "figures"),
        ("no source url", dict(clean, source_url=""), "no-source-url", "verbatim"),
        ("no as_of", dict(clean, as_of=""), "no-as-of", "verbatim"),
        ("no refreshed", dict(clean, refreshed=""), "no-refreshed", "-"),
        ("removed but present", {"original": "He runs a fund.", "refreshed": "(removed)", "source_url": "",
                                 "note": "x"}, "removed-still-present", "removed"),
    ]
    cn = _norm(content)
    ok = True
    print("self-test — each case must FIRE its detector / land in its tier:")
    for name, entry, want, tier in cases:
        f, got_tier = audit_entry(entry, 0, content, cn)
        got = {c for _, c, _ in f}
        hit = (want is None or want in got) and got_tier == tier
        ok = ok and hit
        print(f"  {'PASS' if hit else 'FAIL'}  {name:<28} expect {want or 'silent'}/{tier}  got {sorted(got) or '[]'}/{got_tier}")
    f, tier = audit_entry(clean, 0, content, cn)
    neg = not f and tier == "verbatim"
    ok = ok and neg
    print(f"  {'PASS' if neg else 'FAIL'}  {'negative control':<28} expect silent/verbatim  got {[c for _, c, _ in f] or '[]'}/{tier}")
    removed_ok = {"original": "Gone sentence.", "refreshed": "(removed)", "source_url": "", "note": "died 2020"}
    f, tier = audit_entry(removed_ok, 0, content, cn)
    neg2 = not f and tier == "removed"
    ok = ok and neg2
    print(f"  {'PASS' if neg2 else 'FAIL'}  {'removal control':<28} expect silent/removed  got {[c for _, c, _ in f] or '[]'}/{tier}")
    # End-to-end on a temp lecture dir: exit status must be 1 on a BLOCK.
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / CONTENT_FILENAME).write_text(content, encoding="utf-8")
        (d / FIGURE_UPDATES_FILENAME).write_text(json.dumps({"figure_updates": [clean, dict(clean, source_url="")]}))
        findings, tiers, counts = audit(d)
        e2e = any(SEVERITY.get(c) == "BLOCK" for _, c, _ in findings) and counts.get("verbatim") == 2
        ok = ok and e2e
        print(f"  {'PASS' if e2e else 'FAIL'}  {'end-to-end dir':<28} expect 1 BLOCK, verbatim=2  got {counts}")
    print("\n" + ("all detectors fire and the controls stay silent" if ok else "SELF-TEST FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
