#!/usr/bin/env python3
"""On-screen presenter (OPTIONAL, opt-in per lecture): a lip-synced presenter made from ONE photo.

A photo of a person (or a drawn character) becomes a presenter who speaks the narration, generated
by LongCat-Video-Avatar 1.5 on a Modal GPU (scripts/longcat_avatar_modal.py) and keyed over the
finished video in any mode — Manim (math/technical) or Remotion (folio):

- **Hook** (the first frame): the presenter large on the RIGHT of the picture (720p, H200). The
  frame's visuals keep the left ~60 % of the screen; the right side is reserved.
- **Wipe** (every later frame): the presenter in a small rounded window in the bottom-right corner
  (480p, H100) — ONE clip per frame, so the presenter returns to rest where the frame changes.
  Every frame keeps that corner clear. `enable --no-wipe` = hook only (≈ 1/6 of the cost).

The presenter is DIRECTED, not decorative: a subagent gives chosen sentences a gesture and/or a
facial expression whose teaching job is spelled out in GESTURES / FACES below (mark the key point,
pose a question, direct the eye, warn, react). Directions sit on verbatim anchor phrases, like folio
cues. LongCat holds a pose once taken (each 3.2 s segment continues the last), so the FIRST gesture
in a clip lands reliably and later ones are best-effort — hence one clip per frame, ONE gesture per
frame, and a frame split just before its gesture (`_pieces`). The hook is face-only: a full-figure
gesture tends to leave a translucent ghost of the resting hand.

Cost (Modal, Oct 2026): hook ≈ US$0.075 per second (≈ US$2.40 for a 30 s hook); wipe ≈ US$0.02 per
second + ~2 min of model load per frame clip (≈ US$14 per 10 minutes). A 10-minute video with both
≈ US$16; hook only ≈ US$2.40.

    python scripts/longcat_avatar_modal.py download        # once: weights (~57 GB) -> a Modal Volume
    python scripts/presenter.py setup PHOTO [--name NAME]  # once per presenter -> inputs/presenter/NAME/
    python scripts/presenter.py enable pipeline/<L> [--name NAME] [--no-wipe]   # BEFORE frames are authored
    python scripts/presenter.py prompt pipeline/<L>/Video-N  # directions prompt -> subagent -> presenter/directions.json
    python scripts/presenter.py plan VIDEO [...]          # validate the directions, write presenter/plan.json
    python scripts/presenter.py generate VIDEO [...]      # LongCat clips (missing/stale only), all in parallel
    python scripts/presenter.py hands VIDEO [...]         # floating-hand check + review sheets
    python scripts/presenter.py reset VIDEO N [...]       # reset frame N after its gesture (then generate)
    python scripts/presenter.py zones STILL --hook|--wipe # outline the reserved zone on a 1920-wide still
    (compile_video.py / folio.py compile overlay the presenter automatically once its clips exist)
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'scripts'))

PRESENTERS = REPO / 'inputs' / 'presenter'
GREEN = (0, 177, 64)                       # #00B140, the backdrop the pictures are put on
KEY = '0x00B540'                           # the model drifts the source green a few levels; key between them
KEY_FILTER = f'chromakey={KEY}:0.14:0.06,despill=type=green:mix=0.6:expand=0.1'
WIPE_PX, WIPE_MARGIN = 380, 36
FADE = 0.5
CUE_LEAD = 0.5            # a direction starts this long before its sentence (the motion needs to build)
LONGCAT_REV = 'ref30-mask5-release-perframe-split'   # model settings; part of the clip digest

DEFAULT_CONFIG = {
    'look': '',
    'style': 'Consistent with the reference image.',
    'panel': None,         # wipe window fill; default navy (Manim) / paper (folio)
    'border': None,        # wipe window border; default gold (Manim) / ink (folio)
}

ONE_HAND = ' The other hand stays relaxed at the waist.'   # two-handed spreads read as strange
BASE_WIPE = ('The presenter talks warmly and clearly, explaining an idea, with natural head movement, nods and '
             'blinks, hands resting calmly in front of the body.')
BASE_HOOK = ('The presenter talks warmly and clearly, explaining an idea, with natural head movement and hands '
             'resting calmly in front of the body.')
RELEASE = 'The presenter lowers the hand back down to rest at the waist and keeps talking naturally.'

# gesture id -> (teaching job, wipe prompt, hook prompt). The job text is what the directions prompt
# shows the writer; a direction must do that job, or the sentence gets none. One hand at a time,
# near the face or chest — what reads naturally.
GESTURES = {
    'point_up': ('the key point: a definition, theorem, result or claim the viewer must remember',
                 'The presenter lifts the right hand up beside the face and raises ONE index finger straight up, '
                 'stressing an important point.' + ONE_HAND,
                 'The presenter lifts the right hand up beside the face and raises ONE index finger straight up, '
                 'stressing an important point.' + ONE_HAND),
    'think': ('a question put to the viewer, or a pause to wonder before the answer',
              'The presenter lifts the right hand to the chin and touches it with the index finger, tilting the '
              'head, thinking.' + ONE_HAND,
              'The presenter lifts the right hand to the chin and touches it with the index finger, tilting the '
              'head, thinking.' + ONE_HAND),
    'point_screen': ('look HERE: something on screen the viewer must look at now',
                     'The presenter turns the head toward the left edge of the picture and points the right index '
                     'finger toward the left edge of the picture.' + ONE_HAND,
                     'The presenter turns slightly and points the right index finger toward the left edge of the '
                     'picture, at what is shown there.' + ONE_HAND),
    'count': ('listing: first, second, two operations, three causes',
              'The presenter lifts the right hand beside the face and counts on the fingers, one finger, then two.'
              + ONE_HAND,
              'The presenter lifts the right hand beside the face and counts on the fingers, one finger, then two.'
              + ONE_HAND),
    'open_palm': ('introducing or presenting a new idea or example; a contrast ("on the other hand")',
                  'The presenter opens the right hand palm-up in front of the chest, presenting an idea.' + ONE_HAND,
                  'The presenter opens the right hand palm-up toward the left edge of the picture, presenting an '
                  'idea.' + ONE_HAND),
    'caution': ('a warning: a common mistake, a trap, a condition people forget',
                'The presenter raises the right index finger and wags it gently from side to side, warning about a '
                'mistake.' + ONE_HAND,
                'The presenter raises the right index finger and wags it gently from side to side, warning about a '
                'mistake.' + ONE_HAND),
    'nod': ('confirmation: yes, that is right; agreeing with what was just shown',
            'The presenter nods firmly twice with an encouraging smile, hands resting in front of the body.',
            'The presenter nods firmly twice with an encouraging smile, hands resting in front of the body.'),
}
FACES = {
    'smile': ('warm and encouraging (the default)', 'a warm, encouraging smile'),
    'curious': ('wondering, inviting the viewer to think', 'a curious, wondering expression'),
    'surprised': ('a counter-intuitive fact or surprising result', 'a surprised expression with wide eyes'),
    'serious': ('precision: a careful definition, a warning', 'a focused, serious expression'),
    'delighted': ('an aha moment, a beautiful result', 'a delighted, beaming expression'),
    'thoughtful': ('reflecting on what was just shown', 'a thoughtful, gentle expression'),
    'confident': ('stating a firm result', 'a confident, bright expression'),
}


# ---------------------------------------------------------------------------
# configuration: which lectures, which presenter
# ---------------------------------------------------------------------------

def _lecture_dir(path: Path) -> Path:
    path = Path(path).resolve()
    return path.parent if path.name.startswith('Video-') else path


def enabled(video: Path) -> bool:
    """True when the lecture opted in (`presenter.py enable` wrote pipeline/<L>/presenter.json)."""
    return (_lecture_dir(video) / 'presenter.json').exists()


def config(video: Path) -> dict:
    """The lecture's opt-in (name, wipe) merged with the presenter's own presenter.json."""
    lec = json.loads((_lecture_dir(video) / 'presenter.json').read_text(encoding='utf-8'))
    d = PRESENTERS / lec.get('name', 'default')
    own = d / 'presenter.json'
    cfg = {**DEFAULT_CONFIG, **(json.loads(own.read_text(encoding='utf-8')) if own.exists() else {}), **lec}
    cfg['dir'] = d
    cfg.setdefault('wipe', True)
    return cfg


def wipe_on(video: Path) -> bool:
    return enabled(video) and bool(config(video).get('wipe', True))


def is_folio(video: Path) -> bool:
    return (Path(video) / 'folio.json').exists()


# ---------------------------------------------------------------------------
# setup: one photo -> the two pictures LongCat starts from (on chroma green)
# ---------------------------------------------------------------------------

def _green_border(rgb) -> bool:
    import numpy as np
    a = np.asarray(rgb).astype(int)
    edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    return float(((edge[:, 1] > edge[:, 0] + 40) & (edge[:, 1] > edge[:, 2] + 40)).mean()) > 0.8


def _cutout(rgb, green: bool):
    """RGB photo -> RGBA cutout of the presenter. A photo already on a flat green backdrop is keyed
    directly; anything else goes through rembg (background removal)."""
    import numpy as np
    from PIL import Image
    if green or _green_border(rgb):
        a = np.asarray(rgb).astype(np.float32)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        dom = g - np.maximum(r, b)                                  # ~150 on the backdrop, < 0 on the person
        alpha = np.clip(1 - (dom - 20) / 60, 0, 1)
        a[..., 1] = np.minimum(g, np.maximum(r, b) + 10)            # despill
        return Image.fromarray(np.dstack([a, alpha * 255]).astype(np.uint8), 'RGBA')
    try:
        from rembg import remove
    except ImportError:
        sys.exit("✗ background removal needs rembg: pip install 'rembg[cpu]'\n"
                 "  (or pass a photo already on a flat green backdrop with --green)")
    return remove(rgb).convert('RGBA')


def _framings(cut):
    """RGBA cutout -> (hook 1024x1536 on green, wipe 1024x1024 on green).
    Hook: the whole figure, bottom-aligned (flush when the photo crops the body). Wipe: a square of
    the upper body with headroom, centred on the head."""
    import numpy as np
    from PIL import Image
    al = np.asarray(cut)[..., 3] > 128
    ys, xs = np.nonzero(al)
    if not len(xs):
        sys.exit('✗ no person found in the photo')
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    w, h = x1 - x0, y1 - y0
    flush = y1 >= cut.height - 2                                    # the photo crops the body at the bottom
    head = al[y0:y0 + max(1, int(0.12 * h))]
    hx = float(np.nonzero(head)[1].mean()) if head.any() else (x0 + x1) / 2   # (columns are absolute)

    def place(cw, ch, left, top, size):
        canvas = Image.new('RGBA', (round(cw), round(ch)), GREEN + (255,))
        canvas.alpha_composite(cut, (round(-left), round(-top)))
        return canvas.convert('RGB').resize(size, Image.LANCZOS)

    ch = h / (0.90 if flush else 0.86)
    cw = ch * 2 / 3
    if w > cw * 0.92:
        cw = w / 0.92
        ch = cw * 1.5
    bottom = y1 if flush else y1 + 0.04 * ch
    hook = place(cw, ch, (x0 + x1) / 2 - cw / 2, bottom - ch, (1024, 1536))

    s = min(max(w * 1.15, h * 0.74), h * 1.1)        # down past the hands: gestures stay in shot
    if flush:
        s = min(s, h / 0.93)
    top = y0 - 0.07 * s
    if flush:
        top = min(top, y1 - s)
    wipe = place(s, s, hx - s / 2, top, (1024, 1024))
    return hook, wipe


def cmd_setup(a):
    from PIL import Image, ImageOps
    d = PRESENTERS / a.name
    d.mkdir(parents=True, exist_ok=True)
    rgb = ImageOps.exif_transpose(Image.open(a.photo)).convert('RGB')
    rgb.save(d / 'source.png')
    hook, wipe = _framings(_cutout(rgb, a.green))
    hook.save(d / 'hook.png')
    wipe.save(d / 'wipe.png')
    cfg = d / 'presenter.json'
    if not cfg.exists():
        cfg.write_text(json.dumps({k: v for k, v in DEFAULT_CONFIG.items()}, indent=2) + '\n', encoding='utf-8')
    print(f'✓ {d / "hook.png"} (hook, full figure)\n✓ {d / "wipe.png"} (corner window, upper body)')
    if not json.loads(cfg.read_text(encoding='utf-8')).get('look'):
        print(f'→ LOOK at hook.png and write "look" in {cfg}: one sentence describing the presenter as\n'
              '  pictured (age, hair, glasses, clothing), e.g. "A man in his forties with short grey hair, rimless\n'
              '  glasses and a dark blue sweater." LongCat\'s prompt opens with it.')
    print('  Replace hook.png / wipe.png with your own framings if you like (keep the flat green backdrop).')


def cmd_enable(a):
    lec = _lecture_dir(Path(a.lecture))
    d = PRESENTERS / a.name
    for f in ('hook.png', 'wipe.png', 'presenter.json'):
        if not (d / f).exists():
            sys.exit(f'✗ {d / f} missing — run presenter.py setup PHOTO --name {a.name} first')
    (lec / 'presenter.json').write_text(json.dumps({'name': a.name, 'wipe': not a.no_wipe}, indent=2) + '\n',
                                        encoding='utf-8')
    print(f'✓ presenter "{a.name}" enabled for {lec.name} ({"hook + wipe" if not a.no_wipe else "hook only"}).'
          '\n  Frames authored from now on keep the presenter zones clear.')


# ---------------------------------------------------------------------------
# reserved screen zones (the layout tweak every mode makes for the presenter)
# ---------------------------------------------------------------------------

# 1920x1080 px. Wipe: 380 px window + 36 px margin, bottom-right. Hook: the presenter scaled to the
# frame height and placed from x ≈ 1210 px; a pointing hand reaches ~1180 px.
WIPE_ZONE_PX = (1504, 664, 1920, 1080)      # x0, y0, x1, y1
HOOK_ZONE_PX = (1180, 0, 1920, 1080)


def frame_numbers(video: Path) -> list:
    from scripts.utils.script_parser import load_script
    return [f.number for f in load_script(Path(video)).frames]


def manim_layout_section(video: Path, frame: int) -> str:
    """Appended to a Manim frame's codegen prompt when the lecture has a presenter."""
    nums = frame_numbers(video)
    if frame == nums[0]:
        return """

## Presenter layout — THIS IS THE HOOK FRAME

An on-screen presenter stands large on the RIGHT of this frame for its whole length (keyed over
your render afterwards). **Keep every object inside x < 1.4** (the left ~60 % of the screen) at
every moment: compose for that space — scale the whole composition if needed, keeping text
readable. Nothing may enter x ≥ 1.4. `audit_frames.py` draws the reserved zone as a red box on
its stills — nothing may sit in it.
"""
    if not wipe_on(video):
        return ''
    return """

## Presenter layout — keep the corner clear

A presenter window sits in the bottom-right corner for this whole frame. **No object may enter
x > 4.0 AND y < −0.9** (that corner rectangle) at any time — move or scale content up or left (a
footer line or a right column's last row is what usually lands there). `audit_frames.py` draws the
reserved zone as a red box on its stills — nothing may sit in it.
"""


def folio_layout_section(video: Path) -> str:
    """Appended to the folio scene-author prompt when the lecture has a presenter."""
    spans = frame_spans(video)
    hook_end = spans[0][1] + spans[0][2]
    out = f"""

### Presenter zones — this video has an on-screen presenter

- **Hook (video time 0 – {hook_end:.1f} s):** the presenter stands large on the RIGHT, x ≥ 1180,
  keyed over the scene afterwards. During it keep every element AND the plate's subject inside
  x < 1160 (a camera `on` point on the left half, or a Plate box that ends at x 1160); the right
  side is covered. Captions move left automatically.
"""
    if wipe_on(video):
        out += """- **After the hook:** a presenter window covers the bottom-right corner, x > 1500 and y > 660.
  Nothing that matters may sit there — no label, caption-like text, map key or the subject of a
  plate (a full-bleed plate is fine as long as its subject is elsewhere). Captions move left automatically.
"""
    out += ('- `folio.py still` draws the reserved zone as a red box on every still — nothing that matters '
            'may sit in it.\n')
    return out


def folio_scene_zone(video: Path, scene: dict, fps: int) -> str:
    """One line for a scene's brief: which presenter zone it has to respect."""
    spans = frame_spans(video)
    hook_end = spans[0][1] + spans[0][2]
    t0, t1 = scene['f0'] / fps, scene['f1'] / fps
    if t0 < hook_end - 0.6:          # (the hook figure fades out over its last 0.5 s)
        return (f"- Presenter: HOOK zone (x ≥ 1180 covered) until {min(t1, hook_end) - t0:.1f} s into this scene"
                + (', then the corner window.\n' if t1 > hook_end and wipe_on(video) else '.\n'))
    return '- Presenter: corner window (x > 1500, y > 660 covered).\n' if wipe_on(video) else ''


def zone_at(video: Path, t: float):
    """'hook' / 'wipe' / None for video time t."""
    spans = frame_spans(video)
    if t < spans[0][1] + spans[0][2]:
        return 'hook'
    return 'wipe' if wipe_on(video) else None


def draw_zone(png: Path, which: str):
    """Outline the reserved zone in red on a still (any width; zones are in 1920 px)."""
    from PIL import Image, ImageDraw
    if not which:
        return
    im = Image.open(png).convert('RGB')
    s = im.width / 1920
    x0, y0, x1, y1 = HOOK_ZONE_PX if which == 'hook' else WIPE_ZONE_PX
    ImageDraw.Draw(im).rectangle([x0 * s, y0 * s, x1 * s - 1, y1 * s - 1], outline=(255, 40, 40), width=4)
    im.save(png)


def cmd_zones(a):
    draw_zone(Path(a.still), 'hook' if a.hook else 'wipe')
    print(f'✓ {a.still}')


# ---------------------------------------------------------------------------
# timeline + directions
# ---------------------------------------------------------------------------

def _probe(p: Path) -> float:
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                 str(p)], capture_output=True, text=True).stdout)


def frame_spans(video: Path) -> list:
    """[(frame number, start, duration)] on the compiled timeline. Folio: the narration timeline
    (decoded durations, what folio.py compile stacks); Manim: compile_video.py's clock (container
    durations of audio/frame_N.mp3, back to back)."""
    video = Path(video)
    tl = video / 'narration_timeline.json'
    if is_folio(video) and tl.exists():
        beats = json.loads(tl.read_text(encoding='utf-8'))['narration_beats']
        return [(b['frame'], b['t_start'], b['t_end'] - b['t_start']) for b in beats]
    out, t = [], 0.0
    for n in frame_numbers(video):
        d = _probe(video / 'audio' / f'frame_{n}.mp3')
        out.append((n, t, d))
        t += d
    return out


def load_words(video: Path, n: int) -> list:
    return json.loads((Path(video) / 'audio' / f'frame_{n}_timestamps.json').read_text(encoding='utf-8'))['words']


def _norm(tok: str) -> str:
    return re.sub(r"[^\w]+", '', tok.lower())


SENT_END = re.compile(r"[.?!][\"'”’)\]]*$")


def anchor_span(words: list, anchor: str):
    """-> ((start, end) of the sentence holding the anchor, hit count). (None, n) when not found once."""
    toks = [_norm(w['word']) for w in words]
    a = [x for x in (_norm(t) for t in anchor.split()) if x]
    hits = [i for i in range(len(toks) - len(a) + 1) if a and toks[i:i + len(a)] == a]
    if len(hits) != 1:
        return None, len(hits)
    i = hits[0]
    s0 = i
    while s0 > 0 and not SENT_END.search(words[s0 - 1]['word']):
        s0 -= 1
    s1 = i + len(a) - 1
    while s1 < len(words) - 1 and not SENT_END.search(words[s1]['word']):
        s1 += 1
    return (words[s0]['start'], words[s1]['end']), 1


def directions_path(video: Path) -> Path:
    return Path(video) / 'presenter' / 'directions.json'


def resolve_directions(video: Path):
    """-> ({frame: [(start, end, direction)]}, problems). Times are frame-local seconds."""
    video = Path(video)
    p = directions_path(video)
    if not p.exists():
        return {}, []
    nums = frame_numbers(video)
    first = nums[0]
    out, probs = {}, []
    for k, d in enumerate(json.loads(p.read_text(encoding='utf-8')).get('directions', [])):
        where = f'direction {k} (frame {d.get("frame")}, "{d.get("anchor", "")}")'
        n, g, fc = d.get('frame'), d.get('gesture'), d.get('face')
        if n not in nums:
            probs.append(f'{where}: no such frame')
            continue
        if g and g not in GESTURES:
            probs.append(f'{where}: unknown gesture {g!r} (one of {", ".join(GESTURES)})')
        if fc and fc not in FACES:
            probs.append(f'{where}: unknown face {fc!r} (one of {", ".join(FACES)})')
        if not g and not fc:
            probs.append(f'{where}: empty direction')
        if g and n == first:
            probs.append(f'{where}: the hook is FACE-ONLY — no gesture (a full-figure gesture ghosts the resting hand)')
        span, hits = anchor_span(load_words(video, n), d.get('anchor', ''))
        if span is None:
            probs.append(f'{where}: anchor found {hits}× in the frame — must be verbatim and occur exactly once')
            continue
        out.setdefault(n, []).append((span[0], span[1], {x: d[x] for x in ('gesture', 'face', 'reset') if d.get(x)}))
    for n, ds in out.items():
        ds.sort(key=lambda x: x[0])
        if sum(1 for _, _, x in ds if x.get('gesture')) > 1:
            probs.append(f'frame {n}: more than one gesture — ONE gesture per frame (later ones rarely happen)')
    return out, probs


def direction_menu() -> str:
    g = '\n'.join(f'- `{k}` — {job}' for k, (job, _, _) in GESTURES.items())
    f = '\n'.join(f'- `{k}` — {job}' for k, (job, _) in FACES.items())
    return f'Gestures:\n{g}\n\nFaces:\n{f}'


def directions_prompt(video: Path) -> str:
    from scripts.utils.script_parser import load_script
    video = Path(video).resolve()
    sd = load_script(video)
    rel = video.relative_to(REPO) if video.is_relative_to(REPO) else video
    wipe = wipe_on(video)
    frames = '\n\n'.join(f'### Frame {f.number}' + (' — THE HOOK (faces only)' if i == 0 else '')
                         + f'\n\n{f.narration}' for i, f in enumerate(sd.frames)
                         if i == 0 or wipe)
    return f"""# Direct the on-screen presenter

This video has an on-screen presenter who speaks the narration below, lip-synced: large beside the
visuals in the first frame (the hook){', then from a small window in the bottom-right corner for every later frame' if wipe else ''}.
**You direct the presenter**: pick the sentences where a gesture or an expression helps the viewer
LEARN at that moment. The presenter is a teacher, not decoration — every direction does one of these jobs:

- **mark what to remember** — the definition, theorem, result or thesis sentence: `point_up` + `confident`/`serious`
- **pose a question** — the sentence that asks it: `think` + `curious` (the answer gets its own reaction)
- **direct the eye** — the sentence that refers to something on screen right now: `point_screen`
- **show structure** — listing: `count`; a contrast ("on the other hand"): `open_palm`
- **warn** — a common mistake or a forgotten condition: `caution` + `serious`
- **react** — a counter-intuitive fact: `surprised`; an elegant result or a finished proof: `delighted`;
  confirming: `nod`; introducing an idea or example: `open_palm`

{direction_menu()}

Rules:
- **ONE gesture per frame**, on the sentence of that frame that matters most. The presenter starts every
  frame at rest and performs the frame's first gesture reliably, then tends to hold that pose (still
  moving naturally) — a second gesture in the same frame may never happen. The gate rejects two.
- **Faces work anywhere** and change freely: a `face` without a `gesture` is the normal direction. Use
  them where the sentence has an emotional or teaching beat — not on every sentence, and never on filler.
- **The hook (first frame) is FACE-ONLY** — no `gesture` (a full-figure gesture leaves a ghost of the
  resting hand). Give its sentences expressions only.
- A direction lasts for its sentence (it starts just before it).
- `anchor` = a few words copied VERBATIM from the frame's narration that occur exactly once in that
  frame; the direction applies to the sentence containing them.

Write `{rel}/presenter/directions.json`:

```json
{{"directions": [
  {{"frame": {sd.frames[0].number}, "anchor": "verbatim words from the hook", "face": "curious"}},
  {{"frame": {sd.frames[min(1, len(sd.frames) - 1)].number}, "anchor": "verbatim words", "gesture": "point_up", "face": "confident"}}
]}}
```

Then run `python scripts/presenter.py plan {rel}` — it rejects an anchor that is not found exactly
once, an unknown id, a gesture in the hook, or two gestures in a frame. Fix and re-run until clean.

## Narration

{frames}
"""


def cmd_prompt(a):
    text = directions_prompt(Path(a.video))
    if a.out:
        Path(a.out).write_text(text, encoding='utf-8')
        print(f'prompt → {a.out} ({len(text.split())} words)')
    else:
        print(text)


# ---------------------------------------------------------------------------
# plan: sections (hook, one per wipe frame) -> LongCat clips
# ---------------------------------------------------------------------------

def _look(cfg: dict, where: str) -> str:
    look = (cfg.get('look') or 'A presenter').strip().rstrip('.') + '. '
    if where == 'wipe':
        return look + ('The presenter is shown from the waist up in front of a uniform flat chroma-key green '
                       'background, speaking to the viewer. ')
    return look + 'The presenter stands in front of a uniform flat chroma-key green background and speaks to the viewer. '


def _style(cfg: dict) -> str:
    return (' The background stays a uniform flat green the whole time. Static camera, the presenter stays in '
            'frame. ' + (cfg.get('style') or ''))


def _prompt(cfg: dict, direction: dict, where: str) -> str:
    g, f = direction.get('gesture'), direction.get('face')
    base = BASE_WIPE if where == 'wipe' else BASE_HOOK
    act = GESTURES[g][1 if where == 'wipe' else 2] if g else base
    face = f' The presenter has {FACES[f][1]} while talking.' if f else ' The presenter keeps talking.'
    return _look(cfg, where) + act + face + _style(cfg)


def base_prompt(cfg: dict, where: str) -> str:
    return _look(cfg, where) + (BASE_WIPE if where == 'wipe' else BASE_HOOK) + _style(cfg)


def segment_prompts(cfg: dict, directions: list, seconds: float, where: str) -> list:
    """LongCat: one prompt per 3.2 s segment — the direction overlapping it most; '' = the base prompt;
    the segment after a gesture gets RELEASE (it rarely takes: see the module note on pose stickiness)."""
    from longcat_avatar_modal import segment_window, segments_for
    out, prev = [], False
    for k in range(segments_for(seconds)):
        a, b = segment_window(k)
        hit = max(((min(b, e) - max(a, s - CUE_LEAD), d) for s, e, d in directions if s - CUE_LEAD < b and e > a),
                  default=None, key=lambda x: x[0])
        if hit and hit[0] > 0.8:
            out.append(_prompt(cfg, hit[1], where))
            prev = bool(hit[1].get('gesture')) and hit[1].get('gesture') != 'nod'
        else:
            out.append(_look(cfg, where) + RELEASE + _style(cfg) if prev else '')
            prev = False
    return out


SPLIT_LEAD = 2.0          # a gesture's piece starts this long before its sentence...
SPLIT_MIN = 4.0           # ...unless the gesture is already this close to the clip start (lands early anyway)
XFADE = 0.3               # the two pieces overlap by this much (same audio) and cross-fade, rest pose to rest pose
RESET_MIN = 3.0           # a reset needs at least this much of the section left after the gesture's sentence
RESET_FADE = 0.35         # the wipe presenter fades out/in at every frame change


def _pieces(cfg: dict, dirs: list, dur: float, where: str) -> list:
    """Split a section so its first GESTURE lands near the start of a clip. LongCat performs a gesture
    reliably only early in a clip and holds a pose once taken — so the piece before the gesture is
    undirected and ends at rest, the gesture piece starts from the same resting picture, and the
    join is invisible. -> [{start, end, segment_prompts}] in section time."""
    g = next((d for d in dirs if d[2].get('gesture')), None)
    split = (g[0] - SPLIT_LEAD) if g else None
    if split is None or split < SPLIT_MIN:
        return [{'start': 0.0, 'end': round(dur, 3), 'segment_prompts': segment_prompts(cfg, dirs, dur, where)}]
    a_dirs = [d for d in dirs if d[0] < split]
    b_dirs = [(s - split, e - split, d) for s, e, d in dirs if e > split]
    return [{'start': 0.0, 'end': round(split + XFADE, 3),
             'segment_prompts': segment_prompts(cfg, a_dirs, split + XFADE, where)},
            {'start': round(split, 3), 'end': round(dur, 3),
             'segment_prompts': segment_prompts(cfg, b_dirs, dur - split, where)}]


def _with_resets(cfg: dict, pieces: list, dirs: list, dur: float, where: str) -> list:
    """A direction with `"reset": true` (a hand left floating after its gesture) adds ONE clip that
    starts from the resting picture where its sentence ends and runs to the section end, cross-faded
    in over the same audio. The pieces before it keep their digest — nothing else regenerates."""
    for s, e, d in dirs:
        if d.get('reset') and dur - e >= RESET_MIN and e > pieces[-1]['start'] + RESET_MIN:
            r_dirs = [(s2 - e, e2 - e, d2) for s2, e2, d2 in dirs if e2 > e]
            pieces = pieces + [{'start': round(e, 3), 'end': round(dur, 3),
                                'segment_prompts': segment_prompts(cfg, r_dirs, dur - e, where)}]
    return pieces


def build_plan(video: Path) -> dict:
    """Hook = the first frame; wipe = one section PER later frame. Each section is one to three
    LongCat clips, each starting from the presenter at rest. Raises SystemExit on bad directions."""
    video = Path(video)
    cfg = config(video)
    dirs, probs = resolve_directions(video)
    if probs:
        sys.exit('✗ presenter directions:\n  ' + '\n  '.join(probs))
    spans = frame_spans(video)
    first, _, hook_dur = spans[0]
    # the hook is face-only: a gesture there is dropped (the gate also rejects it)
    hook_dirs = [(s, e, {k: v for k, v in d.items() if k == 'face'}) for s, e, d in dirs.get(first, [])
                 if d.get('face')]
    wipe_frames = []
    if cfg.get('wipe', True):
        for n, t0, dur in spans[1:]:
            fd = dirs.get(n, [])
            wipe_frames.append({'frame': n, 'start': round(t0, 3), 'duration': round(dur, 3),
                                'audio': str(video / 'audio' / f'frame_{n}.mp3'), 'directions': len(fd),
                                'pieces': _with_resets(cfg, _pieces(cfg, fd, dur, 'wipe'), fd, dur, 'wipe')})
    plan = {
        'rev': LONGCAT_REV,
        'presenter': cfg.get('name', 'default'),
        'hook': {'frame': first, 'audio': str(video / 'audio' / f'frame_{first}.mp3'), 'duration': round(hook_dur, 3),
                 'prompt': base_prompt(cfg, 'hook'), 'directions': len(hook_dirs),
                 'pieces': _with_resets(cfg, _pieces(cfg, hook_dirs, hook_dur, 'hook'), hook_dirs, hook_dur, 'hook')},
        'wipe': {'res': '480p', 'prompt': base_prompt(cfg, 'wipe'),
                 'start': wipe_frames[0]['start'] if wipe_frames else None, 'frames': wipe_frames},
        'timeline': [{'frame': n, 'start': round(t, 3), 'duration': round(d, 3)} for n, t, d in spans],
    }
    pd = video / 'presenter'
    pd.mkdir(parents=True, exist_ok=True)
    (pd / 'plan.json').write_text(json.dumps(plan, indent=1), encoding='utf-8')
    return plan


def _sections(video: Path, plan: dict) -> list:
    """(section, image, resolution, audio, duration, pieces, base prompt) for the hook and every wipe frame."""
    d = config(video)['dir']
    h, w = plan['hook'], plan['wipe']
    out = [('hook', d / 'hook.png', '720p', Path(h['audio']), h['duration'], h['pieces'], h['prompt'])]
    out += [(f"wipe_{f['frame']}", d / 'wipe.png', w['res'], Path(f['audio']), f['duration'], f['pieces'], w['prompt'])
            for f in w['frames']]
    return out


def _digest(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, sort_keys=True).encode())
    return h.hexdigest()[:16]


def _slice(src: Path, start: float, end: float, out: Path) -> Path:
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-t', f'{end - start:.3f}', '-i', str(src),
                    '-ar', '44100', '-ac', '1', '-c:a', 'pcm_s16le', str(out)], check=True)
    return out


def clips(video: Path, plan: dict) -> list:
    """Every LongCat clip this video needs: (name, digest, job). A clip is current when its saved
    `<name>.digest` matches — a changed direction or re-voiced frame regenerates only its clips."""
    pd = Path(video) / 'presenter'
    pd.mkdir(parents=True, exist_ok=True)
    out = []
    for sec, image, res, audio, dur, pieces, prompt in _sections(video, plan):
        for k, pc in enumerate(pieces):
            name = f'{sec}_p{k}'
            wav = _slice(audio, pc['start'], pc['end'], pd / f'{name}.wav')
            dg = _digest(plan['rev'], res, prompt, pc, wav.read_bytes(), image.read_bytes())
            # the demo's FIRST segment uses `prompt` (segment_prompts only drive continuations), so a
            # direction in the first 3.72 s goes in as the clip's prompt or it would be ignored
            first = pc['segment_prompts'][0] if pc['segment_prompts'] and pc['segment_prompts'][0] else prompt
            out.append((name, dg, {'kind': sec, 'image': image, 'audio': wav, 'prompt': first, 'key': str(pd / name),
                                   'segment_prompts': pc['segment_prompts'], 'resolution': res}))
    return out


def _current(pd: Path, name: str, digest: str) -> bool:
    return (pd / f'{name}_green.mp4').exists() and (pd / f'{name}.digest').exists() \
        and (pd / f'{name}.digest').read_text() == digest


def readiness(video: Path) -> list:
    """[] when every clip of this video is generated and current; else what is missing."""
    video = Path(video)
    if not config(video).get('look'):
        return [f'"look" is empty in {config(video)["dir"] / "presenter.json"}']
    dirs, probs = resolve_directions(video)
    if probs:
        return probs
    pd = video / 'presenter'
    bad = [n for n, d, _ in clips(video, build_plan(video)) if not _current(pd, n, d)]
    return [f'{len(bad)} presenter clip(s) missing or stale: {", ".join(bad)}'] if bad else []


def cmd_plan(a):
    for v in map(Path, a.videos):
        if not enabled(v):
            print(f'  {v}: presenter not enabled for this lecture — skipped')
            continue
        plan = build_plan(v)
        secs = _sections(v, plan)
        n = sum(len(x[5]) for x in secs)
        split = [x[0] for x in secs if len(x[5]) > 1]
        nd = plan['hook']['directions'] + sum(f['directions'] for f in plan['wipe']['frames'])
        if not directions_path(v).exists():
            print(f'  ⚠ {v}: no presenter/directions.json — the presenter will only talk (presenter.py prompt)')
        print(f"✓ {v}: {len(secs)} sections, {n} clips, {nd} directions; split before a gesture: "
              f"{', '.join(split) or 'none'}")


def assemble_sections(video: Path, plan: dict):
    """<section>_green.mp4 from its pieces: trimmed to the section and cross-faded (XFADE) where
    they overlap — both sides at rest, over the same audio, so the join does not show."""
    pd = Path(video) / 'presenter'
    for sec, _, _, _, dur, pieces, _ in _sections(video, plan):
        out = pd / f'{sec}_green.mp4'
        srcs = [pd / f'{sec}_p{k}_green.mp4' for k in range(len(pieces))]
        # each piece runs until the next one has faded in (a reset piece cuts the one before it short)
        lens = [(pieces[k + 1]['start'] + XFADE if k + 1 < len(pieces) else dur) - pc['start']
                for k, pc in enumerate(pieces)]
        fc = ''.join(f"[{k}:v]fps=25,tpad=stop_mode=clone:stop_duration=5,trim=0:{n:.3f},setpts=PTS-STARTPTS[p{k}];"
                     for k, n in enumerate(lens))
        prev = 'p0'
        for k in range(1, len(pieces)):
            nxt = 'v' if k == len(pieces) - 1 else f'x{k}'
            fc += f"[{prev}][p{k}]xfade=transition=fade:duration={XFADE}:offset={pieces[k]['start']:.3f}[{nxt}];"
            prev = nxt
        fc = fc.rstrip(';') if len(pieces) > 1 else fc.replace('[p0];', '[v]')
        args = ['ffmpeg', '-v', 'error', '-y']
        for p in srcs:
            args += ['-i', str(p)]
        subprocess.run(args + ['-filter_complex', fc, '-map', '[v]', '-an', '-c:v', 'libx264', '-crf', '14',
                               '-preset', 'fast', str(out)], check=True)


def _window_assets(video: Path) -> tuple:
    """Rounded window fill + mask (WIPE_PX square), in the presenter's folder, per colour pair."""
    from PIL import Image, ImageDraw
    cfg = config(video)
    folio = is_folio(video)
    panel = cfg.get('panel') or ('#E8E1D3' if folio else '#14213D')
    border = cfg.get('border') or ('#1C1A18' if folio else '#C9A227')
    tag = f"{panel}_{border}".replace('#', '')
    fill, mask = cfg['dir'] / f'window_{tag}.png', cfg['dir'] / f'window_{tag}_mask.png'
    if not fill.exists() or not mask.exists():
        r = 28
        im = Image.new('RGB', (WIPE_PX, WIPE_PX), border)
        ImageDraw.Draw(im).rounded_rectangle([5, 5, WIPE_PX - 6, WIPE_PX - 6], r - 5, fill=panel)
        im.save(fill)
        mk = Image.new('L', (WIPE_PX, WIPE_PX), 0)
        ImageDraw.Draw(mk).rounded_rectangle([0, 0, WIPE_PX - 1, WIPE_PX - 1], r, fill=255)
        mk.save(mask)
    return fill, mask, border


def build_wipe_window(video: Path, plan: dict) -> Path:
    """The corner window as ONE transparent track (frames back to back): each frame's clip trimmed to
    its frame, keyed, faded out/in at the frame change (RESET_FADE), set on the window with rounded
    corners and a border drawn over it; the whole window fades in at the start and out at the end."""
    from PIL import Image, ImageDraw
    pd = Path(video) / 'presenter'
    fill, mask, border = _window_assets(video)
    frame_png = pd / 'window_border.png'                # the border, drawn OVER the presenter
    ring = Image.new('RGBA', (WIPE_PX, WIPE_PX), (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle([2, 2, WIPE_PX - 3, WIPE_PX - 3], 26, outline=border, width=5)
    ring.save(frame_png)
    inner = WIPE_PX - 10
    parts = []
    for f in plan['wipe']['frames']:
        src, out, d = pd / f"wipe_{f['frame']}_green.mp4", pd / f"wipe_{f['frame']}_window.mov", f['duration']
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(src), '-loop', '1', '-t', f'{d:.3f}', '-i', str(fill),
                        '-loop', '1', '-t', f'{d:.3f}', '-i', str(mask), '-loop', '1', '-t', f'{d:.3f}', '-i', str(frame_png),
                        '-filter_complex',
                        f"[0:v]fps=30,tpad=stop_mode=clone:stop_duration=5,trim=0:{d:.3f},setpts=PTS-STARTPTS,"
                        f"crop='min(iw,ih)':'min(iw,ih)',scale={inner}:{inner}:flags=lanczos,{KEY_FILTER},format=rgba,"
                        f"fade=t=in:st=0:d={RESET_FADE}:alpha=1,"
                        f"fade=t=out:st={max(0, d - RESET_FADE):.3f}:d={RESET_FADE}:alpha=1[ch];"
                        f"[1:v]fps=30,format=rgba[pn];[pn][ch]overlay=x=5:y=5:shortest=1[pc];"
                        f"[3:v]fps=30,format=rgba[rg];[pc][rg]overlay=0:0:shortest=1[pr];"
                        f"[2:v]fps=30,format=gray[mk];[pr][mk]alphamerge[w]",
                        '-map', '[w]', '-t', f'{d:.3f}', '-c:v', 'qtrle', str(out)], check=True)
        parts.append(out)
    lst = pd / 'wipe_window.txt'
    lst.write_text(''.join(f"file '{p.resolve()}'\n" for p in parts))
    joined, out = pd / 'wipe_window_joined.mov', pd / 'wipe_window.mov'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(lst), '-c', 'copy',
                    str(joined)], check=True)
    total = sum(f['duration'] for f in plan['wipe']['frames'])
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(joined), '-vf',
                    f'format=rgba,fade=t=in:st=0:d={FADE}:alpha=1,fade=t=out:st={max(0, total - FADE):.3f}:d={FADE}:alpha=1',
                    '-c:v', 'qtrle', str(out)], check=True)
    joined.unlink(missing_ok=True)
    for p in parts:
        p.unlink(missing_ok=True)
    return out


def cmd_generate(a):
    """Every missing/stale clip of every video, each in its own LongCat container, all at once; then
    the sections are assembled and the corner window built."""
    import longcat_avatar_modal
    jobs, plans = [], {}
    for v in map(Path, a.videos):
        if not enabled(v):
            print(f'  {v}: presenter not enabled for this lecture — skipped')
            continue
        if not config(v).get('look'):
            sys.exit(f'✗ write "look" in {config(v)["dir"] / "presenter.json"} first (see presenter.py setup)')
        plans[v] = build_plan(v)
        pd = v / 'presenter'
        for name, dg, job in clips(v, plans[v]):
            if a.force or not _current(pd, name, dg):
                jobs.append({**job, 'name': name, 'digest': dg, 'pd': pd})
    secs = sum(_probe(j['audio']) for j in jobs)
    print(f'  {len(jobs)} clip(s) to generate, {secs:.0f} s of presenter')
    bad = 0
    if jobs:
        for job, r in zip(jobs, longcat_avatar_modal.run_presenters(jobs)):
            if 'mp4' not in r:
                bad += 1
                print(f"  ✗ {job['key']}: rc={r['returncode']}\n{r['log_tail'][-1500:]}")
                continue
            (job['pd'] / f"{job['name']}_green.mp4").write_bytes(r['mp4'])
            (job['pd'] / f"{job['name']}.digest").write_text(job['digest'])
            print(f"  ✓ {job['key']}: {r['segments']} segments in {r['seconds']:.0f}s")
    for v, plan in plans.items():
        if not readiness(v):
            assemble_sections(v, plan)
            if plan['wipe']['frames']:
                print(f'  ✓ wipe window: {build_wipe_window(v, plan)}')
    sys.exit(1 if bad else 0)


# ---------------------------------------------------------------------------
# hand check: a finger left floating after its gesture
# ---------------------------------------------------------------------------

# Where a raised hand shows while the resting pose shows backdrop (fractions of the clip picture; the
# presenter's right hand is on the viewer's left). A heuristic — the contact sheet is the evidence.
HAND_REGION = {'wipe': (0.02, 0.25, 0.30, 0.62), 'hook': (0.03, 0.10, 0.30, 0.40)}   # x0, y0, x1, y1
HAND_DELTA = 0.05         # flagged when the samples after the sentence sit this far above the resting picture


def _hand_score(clip: Path, t: float, where: str) -> float:
    import numpy as np
    w, h = (int(x) for x in subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
         str(clip)], capture_output=True, text=True).stdout.strip().split(','))
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', str(clip), '-frames:v', '1', '-f', 'rawvideo',
                          '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)
    x0, y0, x1, y1 = HAND_REGION[where]
    r = a[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    green = (r[..., 1] > r[..., 0] + 40) & (r[..., 1] > r[..., 2] + 40)
    return float(1 - green.mean())


def hand_check(video: Path) -> list:
    """For every section whose LAST hand gesture (not `nod`) has ≥ RESET_MIN of section left after its
    sentence and no reset yet: score the assembled clip after the sentence against the resting
    picture, and write a contact sheet `presenter/hands_<section>.png` (rest | during | after)."""
    video = Path(video)
    pd = video / 'presenter'
    dirs, _ = resolve_directions(video)
    first = frame_numbers(video)[0]
    out = []
    for n, ds in sorted(dirs.items()):
        where, sec = ('hook', 'hook') if n == first else ('wipe', f'wipe_{n}')
        clip = pd / f'{sec}_green.mp4'
        g = [(s, e, d) for s, e, d in ds if d.get('gesture') not in (None, 'nod')]
        if not g or not clip.exists():
            continue
        s, e, d = g[-1]
        dur = _probe(clip)
        if d.get('reset') or dur - e < RESET_MIN:
            continue
        after = [e + 1.0 + k * (dur - e - 1.3) / 5 for k in range(6)]
        during = [s + 0.8, (s + e) / 2]
        rest = _hand_score(clip, 0.3, where)
        scores = [_hand_score(clip, t, where) for t in after]
        flagged = sorted(scores)[len(scores) // 2] - rest > HAND_DELTA      # median of the after-samples
        times = [0.3] + during + after
        sheet = pd / f'hands_{sec}.png'
        sel = '+'.join(f'eq(n\\,{round(t * 25)})' for t in times)          # sections are assembled at 25 fps
        vf = (f"select='{sel}',scale=-1:220,"
              f"drawtext=text='%{{pts\\:hms}}':x=3:y=3:fontsize=15:fontcolor=white:box=1:boxcolor=black,"
              f"tile={len(times)}x1")
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(clip), '-vf', vf, '-vsync', 'vfr', '-frames:v', '1',
                        str(sheet)], check=True)
        out.append({'section': sec, 'frame': n, 'gesture': d['gesture'], 'start': round(s, 2), 'end': round(e, 2),
                    'rest': round(rest, 3), 'after': [round(x, 3) for x in scores], 'flagged': flagged,
                    'sheet': str(sheet)})
    (pd / 'hands.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    return out


def cmd_hands(a):
    for v in a.videos:
        rows = hand_check(Path(v))
        print(f'{v}: {len(rows)} section(s) with a gesture and time left after it')
        for r in rows:
            mark = 'FLAG ' if r['flagged'] else 'ok   '
            print(f"  {mark}{r['section']:8} {r['gesture']:12} ends {r['end']:6.2f}s  rest {r['rest']:.2f}"
                  f"  after {' '.join(f'{x:.2f}' for x in r['after'])}  -> {r['sheet']}")


def cmd_reset(a):
    """Put `"reset": true` on frame N's last hand gesture in presenter/directions.json."""
    p = directions_path(Path(a.video))
    data = json.loads(p.read_text(encoding='utf-8'))
    for n in a.frames:
        hits = [d for d in data['directions'] if d.get('frame') == n and d.get('gesture') not in (None, 'nod')]
        if not hits:
            print(f'frame {n}: no hand gesture — nothing to reset')
            continue
        hits[-1]['reset'] = True
        print(f"frame {n} ({hits[-1]['gesture']}): reset — now run presenter.py generate {a.video}")
    p.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


# ---------------------------------------------------------------------------
# overlay (one pass over the compiled video, so the window never blinks at frame fades)
# ---------------------------------------------------------------------------

def overlay(video: Path, base: Path, out: Path):
    """Hook over the first frame (fades out with it), wipe from the second frame to the end of the
    narration (fades in/out). Audio is copied untouched."""
    video = Path(video)
    pd = video / 'presenter'
    plan = build_plan(video)
    hook = pd / 'hook_green.mp4'
    inputs, chains, last, idx = ['-i', str(base)], ['[0:v]fps=30[b0]'], 'b0', 1
    if hook.exists():
        hd = min(_probe(hook), plan['hook']['duration'])
        inputs += ['-i', str(hook)]
        chains.append(f'[{idx}:v]trim=0:{hd:.3f},setpts=PTS-STARTPTS,scale=-1:1080:flags=lanczos,{KEY_FILTER},'
                      f'fps=30,format=rgba,fade=t=out:st={max(0, hd - FADE):.3f}:d={FADE}:alpha=1[hk]')
        chains.append(f'[{last}][hk]overlay=x=W-w+40:y=0:eof_action=pass[b{idx}]')
        last, idx = f'b{idx}', idx + 1
    if plan['wipe']['frames']:
        window = pd / 'wipe_window.mov'
        if not window.exists():
            build_wipe_window(video, plan)
        inputs += ['-i', str(window)]
        chains.append(f"[{idx}:v]setpts=PTS+{plan['wipe']['start']:.3f}/TB[wf]")
        chains.append(f'[{last}][wf]overlay=x=W-w-{WIPE_MARGIN}:y=H-h-{WIPE_MARGIN}:eof_action=pass[b{idx}]')
        last = f'b{idx}'
    chains.append(f'[{last}]format=yuv420p[v]')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *inputs, '-filter_complex', ';'.join(chains),
                    '-map', '[v]', '-map', '0:a', '-c:v', 'libx264', '-crf', '20', '-preset', 'medium',
                    '-c:a', 'copy', '-movflags', '+faststart', str(out)], check=True)


def apply_to_final(video: Path, final: Path) -> Path:
    """Compile hook: keep the visuals-only cut as final_video_board.mp4 and write the presenter cut
    to `final` (the same path)."""
    board = final.with_name(final.stem + '_board.mp4')
    final.replace(board)
    overlay(video, board, final)
    return final


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('setup', help='photo -> inputs/presenter/NAME/{hook,wipe}.png + presenter.json')
    p.add_argument('photo')
    p.add_argument('--name', default='default')
    p.add_argument('--green', action='store_true', help='the photo is already on a flat green backdrop')
    p.set_defaults(fn=cmd_setup)
    p = sp.add_parser('enable', help='opt a lecture in (writes pipeline/<L>/presenter.json)')
    p.add_argument('lecture')
    p.add_argument('--name', default='default')
    p.add_argument('--no-wipe', action='store_true', help='hook only: no corner window after the first frame')
    p.set_defaults(fn=cmd_enable)
    p = sp.add_parser('prompt', help='the directions prompt for a subagent')
    p.add_argument('video')
    p.add_argument('--out')
    p.set_defaults(fn=cmd_prompt)
    p = sp.add_parser('plan')
    p.add_argument('videos', nargs='+')
    p.set_defaults(fn=cmd_plan)
    p = sp.add_parser('generate')
    p.add_argument('videos', nargs='+')
    p.add_argument('--force', action='store_true')
    p.set_defaults(fn=cmd_generate)
    p = sp.add_parser('hands')
    p.add_argument('videos', nargs='+')
    p.set_defaults(fn=cmd_hands)
    p = sp.add_parser('reset')
    p.add_argument('video')
    p.add_argument('frames', type=int, nargs='+')
    p.set_defaults(fn=cmd_reset)
    p = sp.add_parser('zones')
    p.add_argument('still')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--hook', action='store_true')
    g.add_argument('--wipe', action='store_true')
    p.set_defaults(fn=cmd_zones)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
