#!/usr/bin/env python3
"""
Drama 3 delivery tags for math / technical narration (TTS_PROVIDER=drama3, opt-in).

Drama 3 reads inline directions it never speaks (technical report, Appendix C). In A/B tests on
math narration, INLINE tags lifted the read clearly over plain, while a style prefix or a
director's note made the narrator sound mannered. So STEM narration gets a small whitelist of
inline controls, and nothing else:

    <emphasis>word</emphasis>   stress the word the meaning turns on (a contrast, a sign, the result)
    <slow>short span</slow>     slow, deliberate: a final formula, a definition on first statement
    [pause]                     a natural break of the model's choosing (before a reveal or result)


The tags live in a separate field and are TTS-only:

    script.json  frames[i].narration         clean text — every consumer reads this
                 frames[i].narration_tagged  the SAME words + Drama 3 controls (script.json
                                             "narration_tags": "drama3")

Invariant (apply / check / the TTS step): the spoken words of `narration_tagged` equal
`narration` exactly. Authored by a subagent (`render_step_prompt.py tag --provider drama3
--video-dir <dir>`), which returns {"frames": {"<n>": "<tagged>"}}; then

    venv/bin/python -m scripts.utils.drama3_tags apply <video_dir> <response.json>
    venv/bin/python -m scripts.utils.drama3_tags check <video_dir>
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drama3_modal import spoken_text  # noqa: E402  (stdlib + modal only)

SCOPED = ("emphasis", "slow")
CONTROL_RE = re.compile(r"\[[^\[\]\n]*\]|</?[A-Za-z][^<>\n]*>")
POINT_OK = re.compile(r"\[pause\]", re.I)
MAX_SPAN_WORDS = {"emphasis": 4, "slow": 24}   # a spoken formula runs long


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def frame_key(fr: dict, i: int) -> int:
    return int(fr.get("number", i))


def controls_in(text: str) -> List[str]:
    return CONTROL_RE.findall(text or "")


def validate_frame(clean: str, tagged: str) -> List[str]:
    """Problems with one frame's Drama 3 tags (empty list = OK)."""
    problems = []
    if CONTROL_RE.search(clean or ""):
        problems.append("`narration` itself contains markup — controls belong only in narration_tagged")
    if re.match(r"^\s*Direction:", tagged or "") or "\nLine:" in (tagged or ""):
        problems.append("director's notes are not used for STEM narration (they sound mannered)")
    a, b = spoken_text(tagged).split(), _norm(clean).split()
    if a != b:
        k = next((j for j in range(min(len(a), len(b))) if a[j] != b[j]), min(len(a), len(b)))
        problems.append(f"words differ from `narration` at word {k}: tagged "
                        f"{' '.join(a[max(0, k - 3):k + 4])!r} vs narration {' '.join(b[max(0, k - 3):k + 4])!r}")
    open_tag = None
    for m in CONTROL_RE.finditer(tagged or ""):
        c = m.group(0)
        if c.startswith("["):
            if not POINT_OK.fullmatch(c):
                problems.append(f"{c} is not allowed — only [pause] (no style prefixes or emotion tags)")
            if open_tag:
                problems.append(f"{c} inside <{open_tag[0]}>")
            continue
        name = c.strip("</>").split()[0].lower()
        if name not in SCOPED:
            problems.append(f"{c} is not allowed — only <emphasis> and <slow>")
            continue
        if not c.startswith("</"):
            if open_tag:
                problems.append(f"<{name}> nested inside <{open_tag[0]}>")
            open_tag = (name, m.end())
        else:
            if not open_tag or open_tag[0] != name:
                problems.append(f"{c} has no matching <{name}>")
            else:
                span = (tagged[open_tag[1]:m.start()]).split()
                if not span:
                    problems.append(f"empty <{name}></{name}>")
                elif len(span) > MAX_SPAN_WORDS[name]:
                    problems.append(f"<{name}> spans {len(span)} words (max {MAX_SPAN_WORDS[name]})")
                if re.search(r"[.!?]\s*$", tagged[open_tag[1]:m.start()]):
                    # fix_tts_sentence splits sentences at .!? + space: keep the full stop outside
                    problems.append(f"sentence-ending punctuation inside <{name}>…</{name}> — close "
                                    f"the tag before it")
            open_tag = None
    if open_tag:
        problems.append(f"<{open_tag[0]}> never closed")
    return problems


def load_script(video_dir: Path) -> dict:
    return json.loads((Path(video_dir) / "script.json").read_text(encoding="utf-8"))


def check_video(video_dir: Path) -> Dict[int, list]:
    """{frame: problems} for frames whose narration_tagged is stale or malformed."""
    out = {}
    for i, fr in enumerate(load_script(video_dir).get("frames", [])):
        if fr.get("narration_tagged"):
            probs = validate_frame(fr.get("narration", ""), fr["narration_tagged"])
            if probs:
                out[frame_key(fr, i)] = probs
    return out


def apply_response(video_dir: Path, response_path: Path) -> bool:
    """Validate a tag subagent's response and merge it into script.json as narration_tagged."""
    video_dir = Path(video_dir)
    raw = Path(response_path).read_text(encoding="utf-8").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    resp = json.loads(raw)
    tagged_map = {int(k): v for k, v in (resp.get("frames") or resp).items()}
    script = load_script(video_dir)
    ok = True
    for i, fr in enumerate(script.get("frames", [])):
        n = frame_key(fr, i)
        if n not in tagged_map:
            print(f"  frame {n}: MISSING from response"); ok = False; continue
        for p in validate_frame(fr.get("narration", ""), tagged_map[n]):
            print(f"  frame {n}: {p}"); ok = False
    if not ok:
        print("✗ Not applied — fix the response and re-run apply.")
        return False
    for i, fr in enumerate(script.get("frames", [])):
        fr["narration_tagged"] = _norm(tagged_map[frame_key(fr, i)])
    script["narration_tags"] = "drama3"
    (video_dir / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    frames = script["frames"]
    kinds = {"emphasis": 0, "slow": 0, "pause": 0}
    for fr in frames:
        t = fr["narration_tagged"]
        kinds["emphasis"] += t.count("<emphasis>"); kinds["slow"] += t.count("<slow>")
        kinds["pause"] += len(re.findall(r"\[pause\]", t, re.I))
    words = sum(len(_norm(fr.get("narration", "")).split()) for fr in frames)
    total = sum(kinds.values())
    print(f"✓ narration_tagged written for {len(frames)} frames — {total} controls "
          f"({', '.join(f'{v} {k}' for k, v in kinds.items())}; ≈1 per {words / max(total, 1):.0f} words)")
    return True


# ---------------------------------------------------------------------------
# Tagging prompt (rendered by render_step_prompt.py tag --provider drama3)
# ---------------------------------------------------------------------------

TAG_SYSTEM = """\
You mark up the narration of a math / technical lecture video for its narrator, a synthetic
voice (Fish Audio Drama 3) that reads inline delivery controls without speaking them. The
narration is finished and approved; your only job is to insert a FEW controls so it reads like
a good teacher at the board: plain, warm, unhurried, stressing the word the idea turns on.

CONTROLS — the only three allowed:
  <emphasis>word</emphasis>  stress. One to four words: the word the meaning turns on — a
                             contrast ("only the <emphasis>real</emphasis> ones"), a sign or a
                             count that decides the result ("equals <emphasis>plus</emphasis>
                             one", "there are <emphasis>thirty-one</emphasis>"), the term being
                             defined on its first statement.
  <slow>span</slow>          a deliberate read, up to 24 words: a final formula or result, or a
                             definition stated for the first time. At most once or twice a video
                             section; never a whole sentence of setup.
  [pause]                    a natural break: before a reveal or a result the viewer should
                             anticipate, or after a question the narration poses. Rare.

HARD RULES (a script rejects any violation):
1. Change NO words and NO punctuation. Removing your controls must give back the narration
   word for word.
2. Only the three controls above. No style prefixes, no emotion or performance tags ([warmly],
   [excited], [laughs]), no "Direction:" notes, no nesting, nothing inside a number or name.
3. Close a scoped tag BEFORE sentence-ending punctuation: "<emphasis>cancel</emphasis>." —
   never "<emphasis>cancel.</emphasis>".
4. Return every frame, including ones you leave untouched.

TASTE: less is more. About one control every two or three sentences; zero to four per frame.
Emphasis only where a listener would otherwise miss the point — never on every key term, never
two stressed words in a row. The narrator is a teacher, not a performer.

OUTPUT: a bare JSON object, no prose, no code fence:
  {"frames": {"<frame number>": "<the frame's narration with controls inserted>", ...}}
"""


def build_tag_prompt(video_dir: Path) -> str:
    script = load_script(video_dir)
    title = script.get("title") or script.get("metadata", {}).get("title") or Path(video_dir).parent.name
    lines = [f"VIDEO: {title}", "",
             "Mark up every frame below. VISUAL is what the animation shows while the narration "
             "plays — context for what the narration is pointing at.", ""]
    for i, fr in enumerate(script.get("frames", [])):
        lines.append(f"--- frame {frame_key(fr, i)} ---")
        vis = fr.get("visual")
        if isinstance(vis, dict):
            vis = vis.get("reference") or vis.get("description") or json.dumps(vis)
        if vis:
            lines.append("VISUAL: " + _norm(str(vis))[:1200])
        lines.append("NARRATION: " + _norm(fr.get("narration", "")))
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "check":
        bad = check_video(Path(argv[1]))
        for n, probs in bad.items():
            for p in probs:
                print(f"  frame {n}: {p}")
        print("✓ Drama 3 tags OK" if not bad else f"✗ {len(bad)} frame(s) need re-tagging")
        return 1 if bad else 0
    if len(argv) >= 3 and argv[0] == "apply":
        return 0 if apply_response(Path(argv[1]), Path(argv[2])) else 1
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
