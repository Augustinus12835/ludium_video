"""Faces on folio plates, and whether a <Plate camera> stop cuts one.

In one of our episodes the camera opened on a queen's spread arms at `on [600, 380] zoom 1.8`,
which frames image y ≈ 116–716; her face sat at y ≈ 88–186, so the shot showed a headless
figure for eight seconds. Scene authors read camera stops off a grid by eye, and the still
check missed it. Faces are what a viewer looks for first, so the framing is checked
mechanically: every plate's faces are detected once (OpenCV YuNet,
`models/face_detection_yunet_2023mar.onnx`, MIT, from the OpenCV model zoo; it finds
steel-engraving faces at 0.8–0.9 confidence) and cached beside the asset as `<id>.faces.json`;
`folio.py lint` replays each camera stop's framing (the same transform as `Plate` in
remotion/src/folio.tsx) and errors on a face cut by the frame edge; `folio.py grid` draws the
boxes so stops can aim at them.

All coordinates are in the 1920×1080 image space of Plate children and camera stops.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
MIN_SCORE = 0.6          # below this YuNet starts finding faces in horse heads and capitals
IW, IH = 1920, 1080
BLEED = (44, 44, 1832, 992)      # remotion/src/folio.tsx BLEED (x, y, w, h)
CAPTION_BAND = (872, 1006)       # screen y of the karaoke captions
CUT_ERR_H = 80           # on-screen face height (px) from which a cut face is an error …
CUT_WARN_H = 50          # … and a warning
CUT_RANGE = (0.08, 0.92)  # share of the face inside the frame that counts as "cut"


def detect(image: Path) -> list:
    """[[x, y, w, h, score], …] in 1920-wide image space, best first."""
    import cv2
    im = cv2.imread(str(image))
    if im is None:
        return []
    h, w = im.shape[:2]
    s = IW / w
    im = cv2.resize(im, (IW, round(h * s)), interpolation=cv2.INTER_AREA)
    det = cv2.FaceDetectorYN.create(str(MODEL), "", (im.shape[1], im.shape[0]), MIN_SCORE, 0.3, 5000)
    _, f = det.detect(im)
    if f is None:
        return []
    out = [[int(r[0]), int(r[1]), int(r[2]), int(r[3]), round(float(r[14]), 2)] for r in f]
    return sorted(out, key=lambda r: -r[4])


def faces(image: Path) -> list:
    """Cached detect(): `<stem>.faces.json` beside the image, keyed by the image's hash."""
    image = Path(image)
    if not image.exists():
        return []
    key = hashlib.sha1(image.read_bytes()).hexdigest()[:12]
    cache = image.with_suffix(".faces.json")
    try:
        c = json.loads(cache.read_text())
        if c.get("key") == key:
            return c["faces"]
    except (OSError, ValueError, KeyError):
        pass
    fs = detect(image)
    cache.write_text(json.dumps({"key": key, "faces": fs}))
    return fs


def viewport(on, zoom, box=BLEED, inset=False):
    """Image-space rectangle (x0, y0, x1, y1) a camera stop shows, plus the scale from image
    to screen px — mirrors Plate's camera transform (cover fit, framed point at 44% height,
    edges clamped so the plate always fills the box)."""
    bw, bh = box[2], box[3]
    fs = max(bw / IW, bh / IH)
    ox, oy = (bw - IW * fs) / 2, (bh - IH * fs) / 2
    z = max(1.0, zoom)
    cx, cy = bw / 2, bh * (0.5 if inset else 0.44)

    def clamp(v, o, span, b):
        return min(-z * o, max(b - z * (o + span), v))
    tx = clamp(cx - z * (ox + on[0] * fs), ox, IW * fs, bw)
    ty = clamp(cy - z * (oy + on[1] * fs), oy, IH * fs, bh)

    def inv(sx, t, o):
        return ((sx - t) / z - o) / fs
    return (inv(0, tx, ox), inv(0, ty, oy), inv(bw, tx, ox), inv(bh, ty, oy)), fs * z, (tx, ty, oy, z)


def dedupe(fcs: list, iou=0.5) -> list:
    """Drop repeats (a plate and its then-states share faces)."""
    out = []
    for f in sorted(fcs, key=lambda r: -r[4]):
        def ov(g):
            ix = max(0, min(f[0] + f[2], g[0] + g[2]) - max(f[0], g[0]))
            iy = max(0, min(f[1] + f[3], g[1] + g[3]) - max(f[1], g[1]))
            i = ix * iy
            return i / max(1, f[2] * f[3] + g[2] * g[3] - i)
        if all(ov(g) < iou for g in out):
            out.append(f)
    return out


def _frac(f, vp):
    x0, y0, x1, y1 = vp
    fx, fy, fw, fh = f[:4]
    iw = max(0.0, min(fx + fw, x1) - max(fx, x0))
    ih = max(0.0, min(fy + fh, y1) - max(fy, y0))
    return iw * ih / max(1, fw * fh)


def is_subject(f, on, vp) -> bool:
    """The figure the stop frames: a face roughly above or at the framed point. A camera
    centred on a body puts the head ABOVE `on` — exactly the face that gets cut at the top."""
    fx, fy, fw, fh = f[:4]
    vw = vp[2] - vp[0]
    return abs(fx + fw / 2 - on[0]) <= 0.22 * vw and fy + fh / 2 <= on[1] + 0.15 * (vp[3] - vp[1])


def check_stop(fcs: list, on, zoom, box=BLEED) -> list:
    """Problems with one framing: [(severity, message, face)]. 'error': the framed
    subject's face cut by the frame edge; 'warn': another large face cut nearly in half, or
    the subject's face under the captions. Faces the plate itself crops (not whole at zoom
    1) are the picture's composition, not the camera's, and are skipped."""
    vp, k, (tx, ty, oy, z) = viewport(on, zoom, box)
    full = viewport((960, 540), 1.0, box)[0]
    out = []
    for f in fcs:
        if _frac(f, full) < CUT_RANGE[1]:
            continue
        fx, fy, fw, fh = f[:4]
        frac = _frac(f, vp)
        sh = fh * k                                   # on-screen face height
        subj = is_subject(f, on, vp)
        c = f"{'subject ' if subj else ''}face at [{fx + fw // 2}, {fy + fh // 2}]"
        if CUT_RANGE[0] < frac < CUT_RANGE[1]:
            side = ("top" if fy < vp[1] else "bottom" if fy + fh > vp[3] else
                    "left" if fx < vp[0] else "right")
            msg = f"{c} is cut by the {side} edge ({frac:.0%} in frame, {sh:.0f} px tall)"
            if subj and sh >= CUT_WARN_H:
                out.append(("error", msg, f))
            elif sh >= CUT_ERR_H and 0.3 < frac < 0.85 and zoom >= 1.2:
                out.append(("warn", msg, f))
        elif frac >= CUT_RANGE[1] and subj and sh >= CUT_ERR_H:
            scr_y = box[1] + ty + z * oy + k * (fy + fh / 2)   # face centre, screen y
            if CAPTION_BAND[0] <= scr_y <= CAPTION_BAND[1]:
                out.append(("warn", f"{c} sits under the caption band", f))
    return out


def suggest(fcs_cut: list, on, zoom, box=BLEED, margin=0.25):
    """An `on` that frames every cut face whole (with `margin` × face height of air), or a
    smaller zoom if no pan can; None if neither does."""
    (x0, y0, x1, y1), _, _ = viewport(on, zoom, box)
    dx = dy = 0.0
    for f in fcs_cut:
        fx, fy, fw, fh = f[:4]
        m = margin * fh
        if fy - m < y0:
            dy = min(dy, fy - m - y0)
        if fy + fh + m > y1:
            dy = max(dy, fy + fh + m - y1)
        if fx - m < x0:
            dx = min(dx, fx - m - x0)
        if fx + fw + m > x1:
            dx = max(dx, fx + fw + m - x1)
    cand = [round(on[0] + dx), round(on[1] + dy)]
    if all(_frac(f, viewport(cand, zoom, box)[0]) >= CUT_RANGE[1] for f in fcs_cut):
        return "on", cand
    z = zoom
    while z > 1.0:
        z = round(z - 0.1, 2)
        if all(_frac(f, viewport(on, z, box)[0]) >= CUT_RANGE[1] for f in fcs_cut):
            return "zoom", z
    return None
