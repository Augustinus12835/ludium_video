#!/usr/bin/env python3
"""On-screen presenter (OPTIONAL, opt-in per lecture): a lip-synced presenter made from ONE photo.

A photo of a person (or a drawn character) becomes a presenter who speaks the narration, lip-synced
on Modal GPUs and keyed over the finished video in any mode — Manim (math/technical) or Remotion (folio):

- **Hook** (the first frame): the presenter large on the RIGHT of the picture — LongCat-Video-Avatar
  1.5 at 720p on an H200 (scripts/longcat_avatar_modal.py). The frame's visuals keep the left ~60 %
  of the screen; the right side is reserved. FACE-ONLY: a full-figure gesture tends to leave a
  translucent ghost of the resting hand.
- **Wipe** (every later frame): the presenter, head and shoulders, in a small window — SoulX-FlashTalk-14B
  at 480x480 on an H100 (scripts/flashtalk_modal.py), ONE clip per frame, all of a video's clips in ONE
  container. Lip-sync, expressions and nods; no hands. Every frame keeps that corner clear.
  Manim: a rounded navy window in the BOTTOM-RIGHT corner (the presenter resets at each frame change).
  Folio: a square of the folio paper with the chrome's double hairline in the TOP-RIGHT corner (a plate
  tipped into the page — the top of a folio screen is quiet; captions, legends and the camera's low
  framing live in the lower half), each frame dissolving 0.3 s from the last picture of the one
  before (no blink between paragraphs). Hook plates in a folio video carry `"hook": true` so the
  scene continues behind the figure (folio.py). `enable --no-wipe` = hook only.

The presenter is DIRECTED, not decorative: a subagent gives chosen sentences a facial expression (or a
`nod`) whose teaching job is spelled out in FACES below (mark the key point, pose a question, warn,
react). Directions sit on verbatim anchor phrases, like folio cues; FlashTalk switches its text prompt
on those sentences per 1.12 s chunk.

**LongCat wipe (opt-in, `enable --longcat-wipe`):** waist up, with hand GESTURES (point, count, think…).
It is ~6x the cost and the gestures proved unreliable: LongCat holds a pose once taken (each 3.2 s
segment continues the last), so only the first gesture in a clip lands — hence one gesture per frame
and a split just before it (`_pieces`) — a raised hand can float to the end of the clip (`hands` +
`reset`), a ghost copy of the resting hands can appear, and long clips can grow drawn-in captions.

Cost (Modal, Oct 2026): hook ≈ US$0.075 per second (≈ US$2.40 for a 30 s hook); FlashTalk wipe ≈
US$0.003 per second + ~3 min of start-up per video (≈ US$2.50 per 10 minutes). A 10-minute video ≈
US$5; hook only ≈ US$2.40; with a LongCat wipe ≈ US$16.

    python scripts/longcat_avatar_modal.py download        # once: hook weights (~57 GB) -> a Modal Volume
    python scripts/flashtalk_modal.py download             # once: wipe weights (~54 GB) -> a Modal Volume
    python scripts/presenter.py setup PHOTO [--name NAME]  # once per presenter -> inputs/presenter/NAME/
    python scripts/presenter.py enable pipeline/<L> [--name NAME] [--no-wipe | --longcat-wipe]   # BEFORE frames are authored
    python scripts/presenter.py prompt pipeline/<L>/Video-N  # directions prompt -> subagent -> presenter/directions.json
    python scripts/presenter.py plan VIDEO [...]          # validate the directions, write presenter/plan.json
    python scripts/presenter.py generate VIDEO [...]      # clips (missing/stale only): hooks, then wipes
    python scripts/presenter.py hands VIDEO [...]         # LongCat gestures only: floating-hand check
    python scripts/presenter.py reset VIDEO N [...]       # reset frame N after its gesture (then generate)
    python scripts/presenter.py zones STILL --hook|--wipe [--folio]  # outline the reserved zone on a still
    (compile_video.py / folio.py compile overlay the presenter automatically once its clips exist)
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
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
WIPE_MODELS = ('flashtalk', 'longcat')
FLASHTALK_REV = 'ft-480sq-4step-face'       # FlashTalk settings; part of the clip digest
FT_SIZE = (480, 480)                        # one size per container (FlashTalk recompiles per size)
FT_GESTURES = ('nod',)                      # the one gesture a head-and-shoulders wipe can show

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
    cfg.setdefault('wipe_model', 'flashtalk')
    return cfg


def wipe_on(video: Path) -> bool:
    return enabled(video) and bool(config(video).get('wipe', True))


def wipe_model(video: Path) -> str:
    """'flashtalk' (default: head and shoulders, expressions + nods) or 'longcat' (waist up, gestures)."""
    return config(video).get('wipe_model', 'flashtalk')


def gestures_in_wipe(video: Path) -> bool:
    return wipe_on(video) and wipe_model(video) == 'longcat'


def is_folio(video: Path) -> bool:
    """A folio (documentary) video rather than a Manim one: it has a word timeline or a folio.json
    (the presenter can be directed and generated before the director has written folio.json)."""
    v = Path(video)
    return (v / 'narration_timeline.json').exists() or (v / 'folio.json').exists()


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
    """RGBA cutout -> (hook 1024x1536, wipe 1024x1024, wipe_head 1024x1024), all on green.
    Hook: the whole figure, bottom-aligned (flush when the photo crops the body). Wipe (LongCat): a
    square of the upper body with headroom, centred on the head. Wipe head (FlashTalk): a tighter
    head-and-shoulders square — the hands out of shot."""
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

    hs = min(max(w * 0.95, h * 0.36), h * 0.6)       # head and shoulders: a third of a full figure
    if flush:
        hs = min(hs, h / 0.93)
    htop = y0 - 0.08 * hs
    head_sq = place(hs, hs, hx - hs / 2, htop, (1024, 1024))
    return hook, wipe, head_sq


def cmd_setup(a):
    from PIL import Image, ImageOps
    d = PRESENTERS / a.name
    d.mkdir(parents=True, exist_ok=True)
    rgb = ImageOps.exif_transpose(Image.open(a.photo)).convert('RGB')
    rgb.save(d / 'source.png')
    hook, wipe, head_sq = _framings(_cutout(rgb, a.green))
    hook.save(d / 'hook.png')
    wipe.save(d / 'wipe.png')
    head_sq.save(d / 'wipe_head.png')
    cfg = d / 'presenter.json'
    if not cfg.exists():
        cfg.write_text(json.dumps({k: v for k, v in DEFAULT_CONFIG.items()}, indent=2) + '\n', encoding='utf-8')
    print(f'✓ {d / "hook.png"} (hook, full figure)\n✓ {d / "wipe_head.png"} (corner window, head and shoulders)'
          f'\n✓ {d / "wipe.png"} (corner window, upper body — only for --longcat-wipe)')
    if not json.loads(cfg.read_text(encoding='utf-8')).get('look'):
        print(f'→ LOOK at hook.png and write "look" in {cfg}: one sentence describing the presenter as\n'
              '  pictured (age, hair, glasses, clothing), e.g. "A man in his forties with short grey hair, rimless\n'
              '  glasses and a dark blue sweater." Every presenter prompt opens with it.')
    print('  Replace hook.png / wipe_head.png / wipe.png with your own framings if you like (keep the flat\n'
          '  green backdrop; wipe_head.png = head and shoulders, hands out of shot).')


def cmd_enable(a):
    lec = _lecture_dir(Path(a.lecture))
    d = PRESENTERS / a.name
    model = 'longcat' if a.longcat_wipe else 'flashtalk'
    for f in ('hook.png', 'wipe.png' if model == 'longcat' else 'wipe_head.png', 'presenter.json'):
        if not (d / f).exists():
            sys.exit(f'✗ {d / f} missing — run presenter.py setup PHOTO --name {a.name} first')
    (lec / 'presenter.json').write_text(json.dumps({'name': a.name, 'wipe': not a.no_wipe, 'wipe_model': model},
                                                   indent=2) + '\n', encoding='utf-8')
    what = 'hook only' if a.no_wipe else f'hook + {model} wipe'
    print(f'✓ presenter "{a.name}" enabled for {lec.name} ({what}).'
          '\n  Frames authored from now on keep the presenter zones clear.')


# ---------------------------------------------------------------------------
# reserved screen zones (the layout tweak every mode makes for the presenter)
# ---------------------------------------------------------------------------

# 1920x1080 px. Wipe: 380 px window + 36 px margin, bottom-right. Hook: the presenter scaled to the
# frame height and placed from x ≈ 1210 px; a pointing hand reaches ~1180 px.
WIPE_ZONE_PX = (1504, 664, 1920, 1080)      # x0, y0, x1, y1
HOOK_ZONE_PX = (1180, 0, 1920, 1080)
HOOK_X0 = HOOK_ZONE_PX[0]

# Folio: the window is a paper square in the TOP-RIGHT corner, inside the chrome's hairline frame.
FOLIO_WIPE_BOX = (1530, 56, 334, 334)       # x, y, w, h of the window (paper panel + presenter)
FOLIO_WIPE_ZONE_PX = (1496, 0, 1920, 424)   # reserved: the window + breathing room
FOLIO_CAPTION_HOOK = (90, 1150)             # hook: the caption box keeps within these x (the figure is right)
FOLIO_XFADE = 0.3                           # folio wipe: frame k's last picture dissolves into frame k+1
FOLIO_PANEL_REV = 'folio-paper-v1'          # part of the window digest
PAPER_JPG = REPO / 'remotion' / 'public' / 'folio' / 'paper.jpg'


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


def hook_end(video: Path) -> float:
    """Video time at which the hook (the first frame) ends."""
    spans = frame_spans(video)
    return spans[0][1] + spans[0][2]


def folio_layout_section(video: Path, hook_scenes=None) -> str:
    """Appended to the folio director and scene-author prompts when the lecture has a presenter:
    templates/folio_presenter_addendum.md + this video's hook timing (and its hook scenes)."""
    text = (REPO / 'templates' / 'folio_presenter_addendum.md').read_text(encoding='utf-8')
    if not wipe_on(video):
        text += ('\n- **This lecture is hook-only** (`enable --no-wipe`): there is no window after the hook, '
                 'so the top-right corner is free after it.\n')
    text += f"\n- This video's hook runs 0–{hook_end(video):.1f} s"
    text += (f"; hook scenes: {', '.join(map(str, hook_scenes))}.\n" if hook_scenes
             else ': scenes starting before it are hook scenes.\n')
    return text


def folio_stage_data(video: Path) -> dict:
    """The block folio.py puts in data.presenter for the Remotion stage (PresenterInfo in folio.tsx)."""
    x, y, w, h = FOLIO_WIPE_BOX
    wipe = wipe_on(video)
    return {'hook_end': round(hook_end(video), 3), 'wipe': [x, y, w, h] if wipe else None,
            'zone': list(FOLIO_WIPE_ZONE_PX) if wipe else None, 'hook_x0': HOOK_X0,
            'caption_hook': list(FOLIO_CAPTION_HOOK)}


def folio_scene_zone(video: Path, scene: dict, fps: int) -> str:
    """One line for a scene's brief: which presenter zone it has to respect."""
    spans = frame_spans(video)
    hook_end = spans[0][1] + spans[0][2]
    t0, t1 = scene['f0'] / fps, scene['f1'] / fps
    if t0 < hook_end - 0.6:          # (the hook figure fades out over its last 0.5 s)
        return (f"- Presenter: HOOK scene — the figure covers x ≥ 1180 until {min(t1, hook_end) - t0:.1f} s into "
                "this scene; its plates carry \"hook\": true"
                + (', then the top-right window.\n' if t1 > hook_end and wipe_on(video) else '.\n'))
    return '- Presenter: top-right window (x ≥ 1496, y ≤ 424 covered).\n' if wipe_on(video) else ''


def zone_at(video: Path, t: float):
    """'hook' / 'wipe' / None for video time t."""
    spans = frame_spans(video)
    if t < spans[0][1] + spans[0][2]:
        return 'hook'
    return 'wipe' if wipe_on(video) else None


def zone_px(which: str, folio: bool = False):
    if which == 'hook':
        return HOOK_ZONE_PX
    return FOLIO_WIPE_ZONE_PX if folio else WIPE_ZONE_PX


def draw_zone(png: Path, which: str, folio: bool = False):
    """Outline the reserved zone in red on a still (any width; zones are in 1920 px)."""
    from PIL import Image, ImageDraw
    if not which:
        return
    im = Image.open(png).convert('RGB')
    s = im.width / 1920
    x0, y0, x1, y1 = zone_px(which, folio)
    ImageDraw.Draw(im).rectangle([x0 * s, y0 * s, x1 * s - 1, y1 * s - 1], outline=(255, 40, 40), width=4)
    im.save(png)


def _keyed(path: Path):
    """A presenter picture on chroma green -> RGBA cutout (the same key as setup)."""
    from PIL import Image
    return _cutout(Image.open(path).convert('RGB'), True)


def wipe_panel(video: Path, size: int = None):
    """The folio window: a square of the folio paper (the stage's own texture) with the chrome's double
    hairline, like a small plate tipped into the page. Cached in the presenter's folder."""
    from PIL import Image, ImageDraw
    n = FOLIO_WIPE_BOX[2]
    cached = config(video)['dir'] / f'window_{FOLIO_PANEL_REV}.png'
    if not cached.exists():
        paper = Image.open(PAPER_JPG).convert('RGB')
        px, py = int(paper.width * 0.78), int(paper.height * 0.12)      # an even patch near the top right
        px, py = min(px, paper.width - n), min(py, paper.height - n)
        tile = paper.crop((px, py, px + n, py + n)).convert('RGBA')
        d = ImageDraw.Draw(tile)
        d.rectangle([0, 0, n - 1, n - 1], outline=(60, 52, 44, 140), width=2)       # platemark
        d.rectangle([6, 6, n - 7, n - 7], outline=(60, 52, 44, 110), width=1)       # inner hairline
        tmp = cached.with_name(f'{cached.stem}.{os.getpid()}.{threading.get_ident()}.png')
        tile.save(tmp)
        os.replace(tmp, cached)          # atomic: parallel stills may read it while another writes it
    p = Image.open(cached).convert('RGBA')
    size = size or n
    return p if p.width == size else p.resize((size, size), Image.LANCZOS)


def wipe_picture(video: Path) -> Path:
    return config(video)['dir'] / ('wipe.png' if wipe_model(video) == 'longcat' else 'wipe_head.png')


def composite_preview(video: Path, png: Path, t: float):
    """Folio stills: put the presenter's actual keyed picture where the compile will put it at video
    time t — the full figure on the right in the hook, the paper window top-right after it — plus a
    thin red outline of the reserved zone, so authors and reviewers judge the frame as viewers see it."""
    from PIL import Image, ImageDraw
    which = zone_at(video, t)
    if not which:
        return
    cfg = config(video)
    im = Image.open(png).convert('RGBA')
    s = im.width / 1920
    if which == 'hook':
        b = _keyed(cfg['dir'] / 'hook.png')
        b = b.resize((round(b.width * im.height / b.height), im.height), Image.LANCZOS)
        im.alpha_composite(b, (round(im.width - b.width + 40 * s), 0))     # as overlay(): x = W - w + 40
    else:
        x, y, w, _ = FOLIO_WIPE_BOX
        panel = wipe_panel(video, round(w * s))
        inner = round((w - 20) * s)
        head = _keyed(wipe_picture(video))
        side = min(head.size)
        head = head.crop(((head.width - side) // 2, (head.height - side) // 2,
                          (head.width + side) // 2, (head.height + side) // 2)).resize((inner, inner), Image.LANCZOS)
        panel.alpha_composite(head, (round(10 * s), round(10 * s)))
        im.alpha_composite(panel, (round(x * s), round(y * s)))
    x0, y0, x1, y1 = zone_px(which, folio=True)
    ImageDraw.Draw(im).rectangle([x0 * s, y0 * s, x1 * s - 1, y1 * s - 1], outline=(220, 40, 40, 255),
                                 width=max(1, round(2 * s)))
    im.convert('RGB').save(png)


def cmd_zones(a):
    draw_zone(Path(a.still), 'hook' if a.hook else 'wipe', folio=a.folio)
    print(f'✓ {a.still}')


# ---------------------------------------------------------------------------
# timeline + directions
# ---------------------------------------------------------------------------

def _probe(p: Path) -> float:
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                                 str(p)], capture_output=True, text=True).stdout)


def _decoded(p: Path) -> float:
    """Decoded audio length — what a stacked audio track really runs (container durations drift
    ~30 ms a file, which over a long video becomes visible lip-sync drift)."""
    pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(p), '-f', 's16le', '-ac', '1', '-ar', '48000', '-'],
                         capture_output=True).stdout
    return len(pcm) / (2 * 48000)


def frame_spans(video: Path) -> list:
    """[(frame number, start, duration)] on the compiled timeline. Folio: the narration timeline
    (DECODED durations, what folio.py compile stacks; decoded here too when the timeline is not built
    yet); Manim: compile_video.py's clock (container durations of audio/frame_N.mp3, back to back)."""
    video = Path(video)
    tl = video / 'narration_timeline.json'
    folio = is_folio(video)
    if folio and tl.exists():
        beats = json.loads(tl.read_text(encoding='utf-8'))['narration_beats']
        return [(b['frame'], b['t_start'], b['t_end'] - b['t_start']) for b in beats]
    dur = _decoded if folio else _probe
    out, t = [], 0.0
    for n in frame_numbers(video):
        d = dur(video / 'audio' / f'frame_{n}.mp3')
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
        elif g and g in GESTURES and g not in FT_GESTURES and not gestures_in_wipe(video):
            probs.append(f'{where}: gesture {g!r} — the wipe is head and shoulders (FlashTalk): give a `face`, '
                         f'or `nod` at most')
        span, hits = anchor_span(load_words(video, n), d.get('anchor', ''))
        if span is None:
            probs.append(f'{where}: anchor found {hits}× in the frame — must be verbatim and occur exactly once')
            continue
        out.setdefault(n, []).append((span[0], span[1], {x: d[x] for x in ('gesture', 'face', 'reset') if d.get(x)}))
    for n, ds in out.items():
        ds.sort(key=lambda x: x[0])
        if gestures_in_wipe(video) and sum(1 for _, _, x in ds if x.get('gesture')) > 1:
            probs.append(f'frame {n}: more than one gesture — ONE gesture per frame (later ones rarely happen)')
    return out, probs


def direction_menu(gestures: bool = True) -> str:
    g = '\n'.join(f'- `{k}` — {job}' for k, (job, _, _) in GESTURES.items() if gestures or k in FT_GESTURES)
    f = '\n'.join(f'- `{k}` — {job}' for k, (job, _) in FACES.items())
    return f'Gestures:\n{g}\n\nFaces:\n{f}'


def directions_prompt(video: Path) -> str:
    if gestures_in_wipe(video):
        return _directions_prompt_gestures(video)
    from scripts.utils.script_parser import load_script
    video = Path(video).resolve()
    sd = load_script(video)
    rel = video.relative_to(REPO) if video.is_relative_to(REPO) else video
    wipe = wipe_on(video)
    folio = is_folio(video)
    frames = '\n\n'.join(f'### Frame {f.number}' + (' — THE HOOK' if i == 0 else '')
                         + f'\n\n{f.narration}' for i, f in enumerate(sd.frames)
                         if i == 0 or wipe)
    corner = 'top-right' if folio else 'bottom-right'
    jobs = FOLIO_JOBS if folio else TEACHING_JOBS
    return f"""# Direct the on-screen presenter

This video has an on-screen presenter who speaks the narration below, lip-synced: large beside the
{'opening picture' if folio else 'visuals'} in the first frame (the hook){f', then head and shoulders from a small window in the {corner} corner for every later frame' if wipe else ''}.
{jobs}
{direction_menu(gestures=False)}

Rules:
- **No hand gestures.** The hands are out of shot in the corner window and the hook is face-only;
  `nod` (a head movement) is the only gesture, and never in the hook. The gate rejects any other.
- **Faces work anywhere** and change freely: a direction on most sentences that carry a point is normal.
  Expressions read subtly (a warm talker, not an actor), so pick the clearest one. Nothing on filler.
- A direction lasts for its sentence (it starts just before it).
- `anchor` = a few words copied VERBATIM from the frame's narration that occur exactly once in that
  frame; the direction applies to the sentence containing them.

Write `{rel}/presenter/directions.json`:

```json
{{"directions": [
  {{"frame": {sd.frames[0].number}, "anchor": "verbatim words from the hook", "face": "curious"}},
  {{"frame": {sd.frames[min(1, len(sd.frames) - 1)].number}, "anchor": "verbatim words", "face": "confident"}}
]}}
```

Then run `python scripts/presenter.py plan {rel}` — it rejects an anchor that is not found exactly
once, an unknown id, or a hand gesture. Fix and re-run until clean.

## Narration

{frames}
"""


TEACHING_JOBS = """**You direct the presenter's EXPRESSION**: pick the sentences where a reaction helps the viewer LEARN at
that moment. The presenter is a teacher, not decoration — every direction does one of these jobs:

- **mark what to remember** — the definition, theorem, result or thesis sentence: `confident` / `serious`
- **pose a question** — the sentence that asks it: `curious` (the answer gets its own reaction)
- **warn** — a common mistake or a forgotten condition: `serious`
- **react** — a counter-intuitive fact: `surprised`; an elegant result or a finished proof: `delighted`;
  reflecting on what was just shown: `thoughtful`; confirming ("yes, that's right"): `"gesture": "nod"`
"""

# A documentary (folio) presenter is a storyteller reacting to her or his own story.
FOLIO_JOBS = """**You direct the presenter's EXPRESSION** the way a storyteller reacts to their own story — never
decoration. Every direction does one of these jobs:

- **a question** the story poses ("Why did the assembly vote for it?") → `curious`
- **a turn or a shock** (a betrayal, a reversal, an absurd fact) → `surprised`
- **a death, a defeat, cruelty, a plague** → `serious`
- **a verdict** — the episode's claim, the source's judgement on a person → `confident`
- **a clever stroke** (a stratagem that works, a winning argument) → `delighted`
- **reflection** on what it meant → `thoughtful`
- **confirming** ("And so it proved.") → `"gesture": "nod"`
- warm narration otherwise (`smile` is the resting face; no direction needed)

The presenter narrates; they are not a character in the story. The directions are metadata only —
the narration is not changed.
"""


def _directions_prompt_gestures(video: Path) -> str:
    """The LongCat-wipe version: hand gestures in the wipe, one per frame."""
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
visuals in the first frame (the hook){f", then from a small window in the {'top-right' if is_folio(video) else 'bottom-right'} corner for every later frame" if wipe else ''}.
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
    if where == 'ft':
        return look + ('The presenter is shown head and shoulders in front of a uniform flat chroma-key green '
                       'background, speaking to the viewer. ')
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


# FlashTalk wipe: prompts as trialled (Oct 2026) — expressions read, subtly.
BASE_FT = 'The presenter talks warmly and clearly, explaining an idea, with natural head movement, small nods and blinks.'


def _style_ft(cfg: dict) -> str:
    return ' The background stays a uniform flat green the whole time. Static camera. ' + (cfg.get('style') or '')


def ft_base_prompt(cfg: dict) -> str:
    return _look(cfg, 'ft') + BASE_FT + _style_ft(cfg)


def ft_prompt(cfg: dict, direction: dict) -> str:
    """FlashTalk prompt for one direction: its face, or a nod (hand gestures are dropped)."""
    if direction.get('gesture') == 'nod':
        act = 'The presenter nods firmly twice with an encouraging smile while talking.'
    else:
        act = (f"The presenter talks with {FACES[direction.get('face') or 'smile'][1]}, the expression clearly "
               'showing it, with natural head movement.')
    return _look(cfg, 'ft') + act + _style_ft(cfg)


def ft_schedule(cfg: dict, dirs: list) -> list:
    """[[start, end, prompt]]: each directed sentence from CUE_LEAD before it to its end."""
    return [[round(max(0.0, s - CUE_LEAD), 3), round(e, 3), ft_prompt(cfg, d)] for s, e, d in dirs
            if d.get('face') or d.get('gesture') == 'nod']


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
    """Hook = the first frame (LongCat: one to three clips, each starting from the presenter at rest);
    wipe = one section PER later frame (FlashTalk: one clip with the frame's expressions as a prompt
    schedule; LongCat wipe: one to three clips). Raises SystemExit on bad directions."""
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
    model = cfg.get('wipe_model', 'flashtalk')
    if cfg.get('wipe', True):
        for n, t0, dur in spans[1:]:
            fd = dirs.get(n, [])
            pieces = ([{'start': 0.0, 'end': round(dur, 3), 'schedule': ft_schedule(cfg, fd)}] if model == 'flashtalk'
                      else _with_resets(cfg, _pieces(cfg, fd, dur, 'wipe'), fd, dur, 'wipe'))
            wipe_frames.append({'frame': n, 'start': round(t0, 3), 'duration': round(dur, 3),
                                'audio': str(video / 'audio' / f'frame_{n}.mp3'), 'directions': len(fd),
                                'pieces': pieces})
    plan = {
        'rev': LONGCAT_REV,
        'presenter': cfg.get('name', 'default'),
        'hook': {'frame': first, 'audio': str(video / 'audio' / f'frame_{first}.mp3'), 'duration': round(hook_dur, 3),
                 'prompt': base_prompt(cfg, 'hook'), 'directions': len(hook_dirs),
                 'pieces': _with_resets(cfg, _pieces(cfg, hook_dirs, hook_dur, 'hook'), hook_dirs, hook_dur, 'hook')},
        'wipe': ({'model': 'flashtalk', 'rev': FLASHTALK_REV, 'res': 'x'.join(map(str, FT_SIZE)),
                  'prompt': ft_base_prompt(cfg)} if model == 'flashtalk' else
                 {'model': 'longcat', 'res': '480p', 'prompt': base_prompt(cfg, 'wipe')})
                | {'start': wipe_frames[0]['start'] if wipe_frames else None, 'frames': wipe_frames},
        'timeline': [{'frame': n, 'start': round(t, 3), 'duration': round(d, 3)} for n, t, d in spans],
    }
    pd = video / 'presenter'
    pd.mkdir(parents=True, exist_ok=True)
    (pd / 'plan.json').write_text(json.dumps(plan, indent=1), encoding='utf-8')
    return plan


def _sections(video: Path, plan: dict) -> list:
    """(section, image, resolution, audio, duration, pieces, base prompt, model) for the hook and every wipe frame."""
    d = config(video)['dir']
    h, w = plan['hook'], plan['wipe']
    model = w.get('model', 'longcat')
    out = [('hook', d / 'hook.png', '720p', Path(h['audio']), h['duration'], h['pieces'], h['prompt'], 'longcat')]
    out += [(f"wipe_{f['frame']}", d / ('wipe_head.png' if model == 'flashtalk' else 'wipe.png'), w['res'],
             Path(f['audio']), f['duration'], f['pieces'], w['prompt'], model) for f in w['frames']]
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
    """Every clip this video needs: (name, digest, job). A clip is current when its saved
    `<name>.digest` matches — a changed direction or re-voiced frame regenerates only its clips."""
    pd = Path(video) / 'presenter'
    pd.mkdir(parents=True, exist_ok=True)
    out = []
    for sec, image, res, audio, dur, pieces, prompt, model in _sections(video, plan):
        for k, pc in enumerate(pieces):
            name = f'{sec}_p{k}'
            wav = _slice(audio, pc['start'], pc['end'], pd / f'{name}.wav')
            if model == 'flashtalk':
                dg = _digest(plan['wipe']['rev'], res, prompt, pc, wav.read_bytes(), image.read_bytes())
                out.append((name, dg, {'kind': sec, 'model': 'flashtalk', 'image': image, 'wav': wav, 'audio': wav,
                                       'prompt': prompt, 'schedule': pc['schedule'], 'key': str(pd / name)}))
                continue
            dg = _digest(plan['rev'], res, prompt, pc, wav.read_bytes(), image.read_bytes())
            # the demo's FIRST segment uses `prompt` (segment_prompts only drive continuations), so a
            # direction in the first 3.72 s goes in as the clip's prompt or it would be ignored
            first = pc['segment_prompts'][0] if pc['segment_prompts'] and pc['segment_prompts'][0] else prompt
            out.append((name, dg, {'kind': sec, 'model': 'longcat', 'image': image, 'audio': wav, 'prompt': first,
                                   'key': str(pd / name),
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
        print(f"  {v}: wipe = {plan['wipe'].get('model', 'longcat') if plan['wipe']['frames'] else 'none'}")
        if not directions_path(v).exists():
            print(f'  ⚠ {v}: no presenter/directions.json — the presenter will only talk (presenter.py prompt)')
        print(f"✓ {v}: {len(secs)} sections, {n} clips, {nd} directions; split before a gesture: "
              f"{', '.join(split) or 'none'}")


def assemble_sections(video: Path, plan: dict):
    """<section>_green.mp4 from its pieces: trimmed to the section and cross-faded (XFADE) where
    they overlap — both sides at rest, over the same audio, so the join does not show."""
    pd = Path(video) / 'presenter'
    for sec, _, _, _, dur, pieces, _, _ in _sections(video, plan):
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
    corners and a border drawn over it; the whole window fades in at the start and out at the end.
    A folio video gets the paper window instead (build_folio_window)."""
    from PIL import Image, ImageDraw
    if is_folio(video):
        return build_folio_window(video, plan)
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
    (pd / 'wipe_window.digest').write_text(window_digest(video, plan))
    return out


def window_digest(video: Path, plan: dict) -> str:
    """What the built window depends on: the wipe plan, the clips it is made of, the window's look.
    PATH-FREE on purpose: `generate` may be given the video folder as typed (relative) and `compile`
    resolve it (absolute) — audio paths in the plan would then differ and a fresh window would look
    stale (or a stale one fresh). Only file names and contents go in."""
    video = Path(video)
    pd = video / 'presenter'
    wipe = {**plan['wipe'], 'frames': [{**f, 'audio': Path(f['audio']).name if f.get('audio') else None}
                                       for f in plan['wipe']['frames']]}
    clip_digests = [(pd / f"wipe_{f['frame']}_p{k}.digest").read_text()
                    if (pd / f"wipe_{f['frame']}_p{k}.digest").exists() else None
                    for f in plan['wipe']['frames'] for k in range(len(f['pieces']))]
    if is_folio(video):
        look = ['folio', list(FOLIO_WIPE_BOX), FOLIO_XFADE, FOLIO_PANEL_REV]
    else:
        cfg = config(video)
        look = ['manim', WIPE_PX, RESET_FADE, cfg.get('panel'), cfg.get('border')]
    return _digest(wipe, clip_digests, look, KEY_FILTER, FADE)


def window_current(video: Path, plan: dict) -> bool:
    pd = Path(video) / 'presenter'
    dg = pd / 'wipe_window.digest'
    return (pd / 'wipe_window.mov').exists() and dg.exists() and dg.read_text() == window_digest(video, plan)


def build_folio_window(video: Path, plan: dict) -> Path:
    """The folio top-right window as ONE transparent track: every frame's clip trimmed to its frame,
    frame k's last picture dissolving into frame k+1 over FOLIO_XFADE (the presenter never blinks out
    between paragraphs), keyed, on the folio paper panel; joined with the concat demuxer (-c copy);
    the whole window fades in after the hook and out at the end. Built one frame at a time — at most
    three small inputs per ffmpeg, never one graph over every clip (memory grows with every input)."""
    video = Path(video)
    pd = video / 'presenter'
    x, y, w, h = FOLIO_WIPE_BOX
    panel = pd / 'window_panel.png'
    wipe_panel(video, w).save(panel)
    inner = w - 20
    parts, prev = [], None
    for f in plan['wipe']['frames']:
        src, out, d = pd / f"wipe_{f['frame']}_green.mp4", pd / f"wipe_{f['frame']}_window.mov", f['duration']
        args = ['ffmpeg', '-v', 'error', '-y', '-i', str(src), '-loop', '1', '-t', f'{d:.3f}', '-i', str(panel)]
        g = (f"[0:v]fps=30,tpad=stop_mode=clone:stop_duration=5,trim=0:{d:.3f},setpts=PTS-STARTPTS,"
             f"crop='min(iw,ih)':'min(iw,ih)',scale={inner}:{inner}:flags=lanczos")
        if prev is not None:                       # dissolve from the previous frame's last picture
            args += ['-loop', '1', '-t', f'{FOLIO_XFADE:.3f}', '-i', str(prev)]
            fc = (f"{g}[g];[2:v]fps=30,scale={inner}:{inner},format=yuv420p,setsar=1[st];"
                  f"[g]format=yuv420p,setsar=1[g2];"
                  f"[st][g2]xfade=transition=fade:duration={FOLIO_XFADE}:offset=0[gx];[gx]{KEY_FILTER},format=rgba[ch];")
        else:
            fc = f"{g},{KEY_FILTER},format=rgba[ch];"
        fc += "[1:v]fps=30,format=rgba[pn];[pn][ch]overlay=x=10:y=10:shortest=1[wv]"
        subprocess.run(args + ['-filter_complex', fc, '-map', '[wv]', '-t', f'{d:.3f}', '-c:v', 'qtrle', str(out)],
                       check=True)
        prev = pd / f"wipe_{f['frame']}_last.png"
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-sseof', '-0.1', '-i', str(src), '-frames:v', '1',
                        '-vf', f"crop='min(iw,ih)':'min(iw,ih)',scale={inner}:{inner}", str(prev)], check=True)
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
    for f in plan['wipe']['frames']:
        (pd / f"wipe_{f['frame']}_last.png").unlink(missing_ok=True)
    (pd / 'wipe_window.digest').write_text(window_digest(video, plan))
    return out


def cmd_generate(a):
    """Every missing/stale clip of every video: each LongCat clip (the hooks; a LongCat wipe) in its own
    container, all in parallel; then each video's FlashTalk wipe clips in ONE container per video, the
    videos in parallel (~3 min of start-up per container, then ~2.9 s of H100 per second of video).
    Then the sections are assembled and the corner window built."""
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
                jobs.append({**job, 'name': name, 'digest': dg, 'pd': pd, 'video': v})
    lc = [j for j in jobs if j['model'] == 'longcat']
    ft = [j for j in jobs if j['model'] == 'flashtalk']
    secs = sum(_probe(j['audio']) for j in jobs)
    print(f'  {len(jobs)} clip(s) to generate ({len(lc)} LongCat, {len(ft)} FlashTalk), {secs:.0f} s of presenter')
    res = []
    # LongCat first, then FlashTalk: two Modal apps driven from parallel threads can hang
    if lc:
        import longcat_avatar_modal
        res += longcat_avatar_modal.run_presenters(lc)
    if ft:
        import flashtalk_modal
        by_image = {}                       # one picture per call; one container per video
        for j in ft:
            by_image.setdefault(str(j['image']), {}).setdefault(j['video'], []).append(j)
        for image, batches in by_image.items():
            res += flashtalk_modal.run_wipe_batches(Path(image), list(batches.values()), FT_SIZE)
    done = {r['key']: r for r in res}
    bad = 0
    for job in lc + ft:
        r = done.get(job['key'], {})
        if 'mp4' not in r:
            bad += 1
            print(f"  ✗ {job['key']}: rc={r.get('returncode')}\n{(r.get('log_tail') or r.get('error') or '')[-1500:]}")
            continue
        (job['pd'] / f"{job['name']}_green.mp4").write_bytes(r['mp4'])
        (job['pd'] / f"{job['name']}.digest").write_text(job['digest'])
        what = f"{r['segments']} segments" if 'segments' in r else f"{r['chunks']} chunks"
        print(f"  ✓ {job['key']}: {what} in {r['seconds']:.0f}s")
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
        if n != first and not gestures_in_wipe(video):
            continue                        # a FlashTalk wipe is head and shoulders: no hands to check
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
    narration (fades in/out) — bottom right in a Manim video, top right in a folio one. Audio is
    copied untouched. Two or three inputs: the cut, the hook clip and the pre-built window track."""
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
        if not window_current(video, plan):      # missing, or built for other clips / another look
            build_wipe_window(video, plan)
        inputs += ['-i', str(window)]
        chains.append(f"[{idx}:v]setpts=PTS+{plan['wipe']['start']:.3f}/TB[wf]")
        at = (f'x={FOLIO_WIPE_BOX[0]}:y={FOLIO_WIPE_BOX[1]}' if is_folio(video)      # folio: top right
              else f'x=W-w-{WIPE_MARGIN}:y=H-h-{WIPE_MARGIN}')                       # Manim: bottom right
        chains.append(f'[{last}][wf]overlay={at}:eof_action=pass[b{idx}]')
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
    g = p.add_mutually_exclusive_group()
    g.add_argument('--no-wipe', action='store_true', help='hook only: no corner window after the first frame')
    g.add_argument('--longcat-wipe', action='store_true',
                   help='LongCat corner window (waist up, hand gestures; ~6x the cost) instead of FlashTalk')
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
    p.add_argument('--folio', action='store_true', help='the folio window zone (top right)')
    p.set_defaults(fn=cmd_zones)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
