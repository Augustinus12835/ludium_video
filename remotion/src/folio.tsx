/**
 * FOLIO mode library ("a book of plates").
 *
 * A folio video is a sequence of freely-authored scenes rendered over one
 * narration. Every scene is wrapped by <FolioStage> (paper, hairline frame,
 * corner furniture, burned-in karaoke captions, scene dissolves) — scene TSX
 * never draws those. Scene files import ONLY from 'react', 'remotion' and
 * '../../folio' (they are copied into src/generated/folio_<job>/ at render).
 *
 * House rules (enforced by review, some by `folio.py lint`):
 *  - Everything is a pure function of time (useT / useCue). No CSS transitions
 *    or keyframes, no Math.random (use seeded(n)).
 *  - Caption band y 872–1006 is reserved: no scene element may intrude.
 *    Keep content inside SAFE (x 96–1824, y 96–860) unless it is a full-bleed Plate.
 *  - Crimson = what is being said NOW (strike, punch, current word). Never a faction.
 *  - Every entrance lands on a spoken word: pass `at="verbatim phrase"` (a cue
 *    resolved against this scene's word timestamps) rather than hand-typed seconds.
 */
import React, {createContext, useContext, useMemo} from 'react';
import {AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig, Easing, interpolate} from 'remotion';
import {loadFont as loadLocalFont} from '@remotion/fonts';

loadLocalFont({family: 'Bodoni Moda', url: staticFile('fonts/BodoniModa.ttf'), weight: '400 900'});
loadLocalFont({family: 'Bodoni Moda', url: staticFile('fonts/BodoniModa-Italic.ttf'), weight: '400 900', style: 'italic'});
loadLocalFont({family: 'Libre Baskerville', url: staticFile('fonts/LibreBaskerville.ttf'), weight: '400 700'});
loadLocalFont({family: 'Libre Baskerville', url: staticFile('fonts/LibreBaskerville-Italic.ttf'), weight: '400 700', style: 'italic'});
loadLocalFont({family: 'Anton', url: staticFile('fonts/Anton.ttf'), weight: '400'});
loadLocalFont({family: 'Special Elite', url: staticFile('fonts/SpecialElite.ttf'), weight: '400'});
loadLocalFont({family: 'EB Garamond', url: staticFile('fonts/EBGaramond.ttf'), weight: '400 800'});
loadLocalFont({family: 'EB Garamond', url: staticFile('fonts/EBGaramond-Italic.ttf'), weight: '400 800', style: 'italic'});

export {Easing, interpolate};

// ───────────────────────────────────────────────────────────── tokens

export const FPS = 30;
export const W = 1920;
export const H = 1080;
/** Content safe area. Full-bleed Plates may exceed it; nothing else should. */
export const SAFE = {x0: 96, x1: 1824, y0: 96, y1: 860};
/** Reserved caption band — no scene element may intrude. */
export const BAND = {y0: 872, y1: 1006};
/** Full-bleed image box (inside the inner hairline). */
export const BLEED = {x: 44, y: 44, w: 1832, h: 992};

export const PAL = {
  paper: '#E8E1D3',
  paperEdge: '#D9CFBC',
  card: '#F2EDE3',
  ink: '#1C1A18',
  grey: '#6E6659',
  faint: '#9C9385',
  crimson: '#8E1B20',
  blue: '#23395B', // Attic blue — italic concepts
  gold: '#B08D57',
  night: '#1B1A1E',
  cream: '#EDE6D6',
  // faction washes — maps/rosters only, at low opacity
  athens: '#4A5E7A',
  sparta: '#7A2E2A',
  persia: '#A8843C',
  other: '#6B7146',
};

/**
 * Bodoni Moda is variable in optical size (opsz 6–96). Left on `auto`, display
 * sizes pick the 96 pt cut whose hairlines vanish on a phone. Pinning a TEXT cut
 * keeps the Didone contrast but thick enough to survive downscaling. The stage
 * sets it on the root (font-variation-settings inherits); fonts without an opsz
 * axis ignore it.
 */
export const OPSZ = 14;

export const FONT = {
  display: `'Bodoni Moda', 'Libre Baskerville', Georgia, serif`, // statements, concepts
  text: `'Libre Baskerville', Georgia, serif`, // captions, names, kickers
  punch: `'Anton', 'Impact', sans-serif`, // rare punch lines
  type: `'Special Elite', 'Courier New', monospace`, // typewriter rows
  greek: `'EB Garamond', Georgia, serif`, // polytonic Greek terms
};

// ───────────────────────────────────────────────────────────── data context

export type Word = {w: string; s: number; e: number};
export type CaptionGroup = {t0: number; t1: number; words: Word[]};
export type SceneSpan = {
  index: number;
  f0: number;
  f1: number;
  plate: string;
  tone: 'paper' | 'night';
  enter: 'dissolve' | 'cut';
};
export type FolioData = {
  chrome: {running: string; subtitle: string};
  total: number;
  words: Word[];
  captions: CaptionGroup[];
  assets: Record<string, string>;
  scenes: Record<string, SceneSpan>;
};

type Ctx = {data: FolioData; scene: SceneSpan; next?: SceneSpan; t0: number; dur: number; tokens: string[]};
const FolioCtx = createContext<Ctx | null>(null);

const norm = (s: string) => s.toLowerCase().replace(/[’']/g, '').replace(/[^a-z0-9]+/g, ' ').trim();

export const useFolio = (): Ctx => {
  const c = useContext(FolioCtx);
  if (!c) throw new Error('folio: component used outside <FolioStage>');
  return c;
};

/** Seconds since this scene started. */
export const useT = (): number => {
  const f = useCurrentFrame();
  const {fps} = useVideoConfig();
  return f / fps;
};

/** Seconds on the GLOBAL narration clock. */
export const useGlobalT = (): number => {
  const {t0} = useFolio();
  return t0 + useT();
};

export type At = number | string;

/**
 * Resolve a cue phrase to scene-local seconds (start of its first word).
 * `n` picks the n-th occurrence inside this scene (1-based); `end: true`
 * returns the END of the phrase's last word. Throws (render fails loudly) if
 * the phrase is not spoken inside this scene.
 */
export const useCue = () => {
  const {data, scene, t0, dur, tokens} = useFolio();
  return useMemo(() => {
    const f = (phrase: string, opts: {n?: number; end?: boolean} = {}): number => {
      const want = norm(phrase).split(' ').filter(Boolean);
      if (!want.length) throw new Error(`folio cue: empty phrase`);
      let seen = 0;
      for (let i = 0; i < data.words.length; i++) {
        const w0 = data.words[i];
        if (w0.s < t0 - 0.06) continue;
        if (w0.s > t0 + dur) break;
        let ok = true;
        let j = i;
        let k = 0;
        while (k < want.length) {
          if (j >= tokens.length) { ok = false; break; }
          const tk = tokens[j].split(' ').filter(Boolean);
          if (!tk.length) { j++; continue; }
          for (const piece of tk) {
            if (k < want.length && piece === want[k]) k++;
            else { ok = false; break; }
          }
          if (!ok) break;
          j++;
        }
        if (ok) {
          seen++;
          if (seen === (opts.n ?? 1)) {
            const w = opts.end ? data.words[j - 1].e : w0.s;
            return Math.max(0, w - t0);
          }
        }
      }
      throw new Error(`folio cue not spoken in scene ${scene.index}: "${phrase}"${opts.n ? ` (n=${opts.n})` : ''}`);
    };
    return f;
  }, [data, scene.index, t0, dur, tokens]);
};

/** number → itself; string → cue start; undefined → fallback. */
export const useAt = (at: At | undefined, fallback = 0): number => {
  const cue = useCue();
  if (at === undefined) return fallback;
  return typeof at === 'number' ? at : cue(at);
};

/** Scene duration in seconds. */
export const useDur = (): number => useFolio().dur;

// ───────────────────────────────────────────────────────────── easing helpers

const clamp01 = (x: number) => Math.max(0, Math.min(1, x));
export const E = {
  enter: Easing.out(Easing.poly(4)), // arrivals
  move: Easing.inOut(Easing.cubic), // re-arrangements
  impact: Easing.in(Easing.quad), // punches
  linear: (x: number) => x,
};
/** 0→1 progress of an event starting at `at` lasting `dur`. */
export const prog = (t: number, at: number, dur: number, ease = E.enter) =>
  dur <= 0 ? (t >= at ? 1 : 0) : ease(clamp01((t - at) / dur));
/** Visibility envelope: fade in at `at`, fade out at `until`. */
export const env = (t: number, at: number, until?: number, inDur = 0.45, outDur = 0.35) => {
  const a = prog(t, at, inDur);
  const b = until === undefined ? 0 : prog(t, until, outDur, E.move);
  return a * (1 - b);
};
export const mix = (a: number, b: number, p: number) => a + (b - a) * p;

/** Stable short hash (djb2) — unique SVG ids per path. */
export const hashStr = (str: string) => {
  let h = 5381;
  for (let i = 0; i < str.length; i++) h = ((h << 5) + h + str.charCodeAt(i)) >>> 0;
  return h.toString(36);
};

/** Deterministic PRNG (mulberry32) — use instead of Math.random. */
export const seeded = (seed: number) => {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};

/** Public path of a generated asset id (folio.json `assets[].id`). */
export const useAsset = () => {
  const {data} = useFolio();
  return (id: string) => {
    const p = data.assets[id];
    if (!p) throw new Error(`folio asset not found: "${id}"`);
    return staticFile(p);
  };
};

/** Tone-aware palette (night scenes invert ink/paper). */
export const useInk = () => {
  const {scene} = useFolio();
  const night = scene.tone === 'night';
  return {
    night,
    ink: night ? PAL.cream : PAL.ink,
    grey: night ? '#A79F92' : PAL.grey,
    faint: night ? '#6F685F' : PAL.faint,
    paper: night ? PAL.night : PAL.paper,
    card: PAL.card,
    crimson: night ? '#B4262C' : PAL.crimson,
    blue: night ? '#8FA3C4' : PAL.blue,
  };
};

// ───────────────────────────────────────────────────────────── stage

const romanTC = (sec: number) => {
  const s = Math.max(0, Math.floor(sec));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

const Chrome: React.FC<{running: string; subtitle: string; plate: string; T: number; night: boolean}> = ({running, subtitle, plate, T, night}) => {
  const c = night ? 'rgba(237,230,214,0.55)' : 'rgba(70,62,52,0.72)';
  const line = night ? 'rgba(237,230,214,0.28)' : 'rgba(60,52,44,0.45)';
  const halo = night ? '0 0 6px rgba(20,19,22,0.9)' : '0 0 6px rgba(232,225,211,0.95), 0 0 2px rgba(232,225,211,0.95)';
  // A soft paper chip behind the corner text: invisible on plain paper, legible when a
  // full-bleed engraving runs under the chrome (reviewers: text sank into hatching).
  const chip = night ? 'rgba(27,26,30,0.72)' : 'rgba(234,227,213,0.8)';
  const txt: React.CSSProperties = {
    position: 'absolute', fontFamily: FONT.text, fontSize: 15, letterSpacing: '0.38em',
    textTransform: 'uppercase', color: c, textShadow: halo, whiteSpace: 'nowrap',
    background: chip, boxShadow: `0 0 10px 6px ${chip}`, padding: '1px 2px 1px 6px', borderRadius: 3,
  };
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <div style={{position: 'absolute', left: 34, top: 34, right: 34, bottom: 34, border: `1.5px solid ${line}`}} />
      <div style={{position: 'absolute', left: 42, top: 42, right: 42, bottom: 42, border: `1px solid ${line}`}} />
      <div style={{...txt, left: 80, top: 58}}>{running}</div>
      <div style={{...txt, right: 76, top: 58}}>{plate ? `Pl. ${plate}` : ''}</div>
      <div style={{...txt, left: 80, bottom: 52}}>{subtitle}</div>
      <div style={{...txt, right: 76, bottom: 52, letterSpacing: '0.2em'}}>{romanTC(T)}</div>
    </AbsoluteFill>
  );
};

const CAPTION_LEAD = 0.2; // show a caption slightly before its first word
const CAPTION_HOLD = 1.4; // keep it after its last word unless the next arrives

const Captions: React.FC<{groups: CaptionGroup[]; T: number; night: boolean}> = ({groups, T, night}) => {
  let gi = -1;
  for (let i = 0; i < groups.length; i++) {
    if (groups[i].t0 - CAPTION_LEAD <= T) gi = i; else break;
  }
  if (gi < 0) return null;
  const g = groups[gi];
  const next = groups[gi + 1];
  const endVis = Math.min(next ? next.t0 - CAPTION_LEAD : Infinity, g.t1 + CAPTION_HOLD);
  if (T >= endVis) return null;
  const abutsPrev = gi > 0 && g.t0 - CAPTION_LEAD - Math.min(groups[gi - 1].t1 + CAPTION_HOLD, g.t0 - CAPTION_LEAD) < 0.05;
  const fin = abutsPrev ? 1 : prog(T, g.t0 - CAPTION_LEAD, 0.15);
  const endsIntoNext = next && next.t0 - CAPTION_LEAD <= g.t1 + CAPTION_HOLD;
  const fout = endsIntoNext ? 0 : prog(T, endVis - 0.2, 0.2, E.linear);
  const op = fin * (1 - fout);
  const spoken = night ? PAL.cream : PAL.ink;
  const upcoming = night ? 'rgba(237,230,214,0.42)' : 'rgba(28,26,24,0.40)';
  const current = night ? '#C8373C' : PAL.crimson;
  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 1080 - 1000, display: 'flex', justifyContent: 'center', opacity: op}}>
      <div style={{
        maxWidth: 1460, padding: '12px 30px 14px', background: night ? 'rgba(22,21,25,0.84)' : 'rgba(242,237,227,0.9)',
        fontFamily: FONT.text, fontSize: 33, lineHeight: 1.36, textAlign: 'center', letterSpacing: '-0.005em',
        boxShadow: night ? 'none' : '0 1px 0 rgba(60,52,44,0.08)',
      }}>
        {g.words.map((w, i) => {
          const nextS = i + 1 < g.words.length ? g.words[i + 1].s : w.e + 0.35;
          const color = T < w.s ? upcoming : T < nextS ? current : spoken;
          return <span key={i} style={{color}}>{w.w}{i + 1 < g.words.length ? ' ' : ''}</span>;
        })}
      </div>
    </div>
  );
};

/**
 * Wraps one scene: paper ground, scene content (dissolving in/out through
 * paper), chrome and captions. Generated entries do this — scene TSX never.
 */
export const FolioStage: React.FC<{data: FolioData; scene: number; children: React.ReactNode}> = ({data, scene, children}) => {
  const f = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const sc = data.scenes[String(scene)];
  const next = data.scenes[String(scene + 1)];
  const t0 = sc.f0 / fps;
  const dur = durationInFrames / fps;
  const t = f / fps;
  const T = t0 + t;
  const tokens = useMemo(() => data.words.map((w) => norm(w.w)), [data]);
  const ctx = useMemo(() => ({data, scene: sc, next, t0, dur, tokens}), [data, sc, next, t0, dur, tokens]);
  const night = sc.tone === 'night';
  const fin = sc.enter === 'cut' ? 1 : prog(t, 0, 0.45, E.move);
  const nextDissolves = next && next.enter !== 'cut';
  const nextNight = next && next.tone !== sc.tone;
  const fout = nextDissolves || nextNight ? prog(t, dur - 0.3, 0.3, E.move) : 0;
  return (
    <FolioCtx.Provider value={ctx}>
      <AbsoluteFill style={{backgroundColor: night ? PAL.night : PAL.paper, fontVariationSettings: `'opsz' ${OPSZ}`}}>
        <Img src={staticFile(night ? 'folio/paper_night.jpg' : 'folio/paper.jpg')} style={{position: 'absolute', width: W, height: H}} />
        <AbsoluteFill style={{opacity: fin * (1 - fout)}}>{children}</AbsoluteFill>
        <Chrome running={data.chrome.running} subtitle={data.chrome.subtitle} plate={sc.plate} T={T} night={night} />
        <Captions groups={data.captions} T={T} night={night} />
      </AbsoluteFill>
    </FolioCtx.Provider>
  );
};

// ───────────────────────────────────────────────────────────── primitives

type Box = {x: number; y: number; w: number; h: number};
type WashStep = {at: At; to: number; dur?: number};
type ImgState = {src: string; at: At; dur?: number};

const useSteps = (steps: WashStep[] | undefined, t: number, initial: number) => {
  const cue = useCue();
  let v = initial;
  for (const s of steps || []) {
    const a = typeof s.at === 'number' ? s.at : cue(s.at);
    v = mix(v, s.to, prog(t, a, s.dur ?? 0.45, E.move));
  }
  return v;
};

/**
 * A generated image. Full-bleed by default (BLEED box, cover-fit, slow push-in
 * across its visible span); pass `box` for an inset plate (thin rule + shadow).
 *  - wash: paper-coloured veil over the image: a number (constant) or steps
 *    [{at, to}] — wash to ~0.55–0.65 the moment foreground cards/text arrive.
 *  - then: later states of the SAME composition (e.g. intact → ruined),
 *    cross-dissolved on their cue.
 *  - push: [scaleStart, scaleEnd]; focus: transform-origin ('50% 40%').
 *  - children: overlays in the IMAGE's own 1920×1080 pixel space (read coordinates
 *    off `folio.py grid <video> <asset>`); they move with the push-in. Put pins,
 *    routes and tags that name things IN the picture here; free text goes outside.
 */
export const Plate: React.FC<{
  src: string; at?: At; until?: At; box?: Partial<Box>; push?: [number, number]; focus?: string;
  wash?: number | WashStep[]; then?: ImgState[]; fit?: 'cover' | 'contain'; fade?: number;
  rule?: boolean; style?: React.CSSProperties; children?: React.ReactNode;
}> = ({src, at, until, box, push, focus = '50% 45%', wash, then, fit = 'cover', fade = 0.5, rule, style, children}) => {
  const t = useT();
  const dur = useDur();
  const asset = useAsset();
  const cue = useCue();
  const {paper} = useInk();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const inset = !!box;
  const b = {...BLEED, ...(box || {})};
  const pz = push ?? (inset ? [1.0, 1.025] : [1.0, 1.05]);
  const end = u ?? dur;
  const scale = mix(pz[0], pz[1], clamp01((t - a) / Math.max(0.1, end - a)));
  const op = env(t, a, u, a === 0 ? 0 : fade, 0.4);
  const washSteps = useSteps(typeof wash === 'number' ? undefined : wash, t, 0);
  const washV = typeof wash === 'number' ? wash : washSteps;
  const layers: {src: string; o: number}[] = [{src, o: 1}];
  for (const s of then || []) {
    const sa = typeof s.at === 'number' ? s.at : cue(s.at);
    layers.push({src: s.src, o: prog(t, sa, s.dur ?? 0.6, E.move)});
  }
  if (op <= 0.001) return null;
  // Image space: plates and maps are normalised to 1920×1080. The image is fitted
  // into the box (cover/contain); CHILDREN are drawn in that same 1920×1080 image
  // space, so pins, routes and tags stay glued to the picture through the push-in.
  const IW = 1920, IH = 1080;
  const fs = fit === 'cover' ? Math.max(b.w / IW, b.h / IH) : Math.min(b.w / IW, b.h / IH);
  const ox = (b.w - IW * fs) / 2, oy = (b.h - IH * fs) / 2;
  return (
    <div style={{position: 'absolute', left: b.x, top: b.y, width: b.w, height: b.h, opacity: op, overflow: 'hidden',
      boxShadow: inset && rule !== false ? '0 8px 26px rgba(40,30,20,0.22)' : undefined,
      outline: inset && rule !== false ? '1px solid rgba(60,52,44,0.45)' : undefined, ...style}}>
      <div style={{position: 'absolute', inset: 0, transform: `scale(${scale})`, transformOrigin: focus}}>
        <div style={{position: 'absolute', left: ox, top: oy, width: IW, height: IH, transform: `scale(${fs})`, transformOrigin: '0 0'}}>
          {layers.map((l, i) => (
            <Img key={i} src={asset(l.src)} style={{position: 'absolute', left: 0, top: 0, width: IW, height: IH, opacity: l.o}} />
          ))}
          {washV > 0.001 && <div style={{position: 'absolute', inset: 0, background: paper, opacity: washV}} />}
          {children}
        </div>
      </div>
    </div>
  );
};

/**
 * An isolated engraved object (ink cutout with transparent paper — asset kind
 * `object`). Positioned by CENTRE (x, y) and width w. `draw` wipes it on left→right
 * like a pen stroke instead of fading. `then` = later states (same framing).
 */
export const Ink: React.FC<{
  src: string; x: number; y: number; w: number; at?: At; until?: At; draw?: boolean | number;
  rotate?: number; then?: ImgState[]; opacity?: number; rise?: number; backing?: number; style?: React.CSSProperties;
}> = ({src, x, y, w, at, until, draw, rotate = 0, then, opacity = 1, rise = 14, backing, style}) => {
  const t = useT();
  const asset = useAsset();
  const cue = useCue();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const dd = typeof draw === 'number' ? draw : 1.1;
  const pIn = draw ? prog(t, a, dd, E.move) : prog(t, a, 0.5);
  const pOut = u === undefined ? 0 : prog(t, u, 0.35, E.move);
  if (pIn <= 0 || pOut >= 1) return null;
  // Cutouts are transparent, so a later state must REPLACE the earlier one: each
  // layer fades in on its cue while the layer before it fades out.
  const ps = (then || []).map((s) => prog(t, typeof s.at === 'number' ? s.at : cue(s.at), s.dur ?? 0.6, E.move));
  const srcs = [src, ...(then || []).map((s) => s.src)];
  const layers = srcs.map((sr, i) => ({src: sr, o: (i === 0 ? 1 : ps[i - 1]) * (i < ps.length ? 1 - ps[i] : 1)}));
  const clip = draw ? `inset(0 ${(1 - pIn) * 100}% 0 0)` : undefined;
  return (
    <div style={{position: 'absolute', left: x - w / 2, top: y, width: w, transform: `translateY(${draw ? 0 : (1 - pIn) * rise}px) translateY(-50%) rotate(${rotate}deg)`,
      opacity: (draw ? 1 : pIn) * (1 - pOut) * opacity, clipPath: clip, ...style}}>
      {backing ? <div style={{position: 'absolute', inset: '-12%', background: `radial-gradient(closest-side, rgba(236,229,215,${backing}) 62%, rgba(236,229,215,0) 100%)`}} /> : null}
      {layers.map((l, i) => (
        <Img key={i} src={asset(l.src)} style={{width: '100%', display: 'block', position: i ? 'absolute' : 'relative', top: 0, left: 0, opacity: l.o}} />
      ))}
    </div>
  );
};

type CardKey = {at: At; x?: number; y?: number; w?: number; dur?: number};

/**
 * Portrait card (asset kind `portrait`, 3:4): cream card, double crimson rule,
 * NAME in spaced caps, one italic line beneath (dates or a ≤5-word title).
 * Positioned by CENTRE (x, y); w = card width (S 220 · M 300 · L 400).
 * `keys` moves/resizes it later in the scene (row → column → tree).
 */
export const Card: React.FC<{
  src: string; name: string; sub?: string; x: number; y: number; w?: number; at?: At; until?: At;
  keys?: CardKey[]; dim?: WashStep[]; enter?: 'rise' | 'none'; tilt?: number; nameSize?: number;
}> = ({src, name, sub, x, y, w = 300, at, until, keys, dim, enter = 'rise', tilt = 0, nameSize}) => {
  const t = useT();
  const asset = useAsset();
  const cue = useCue();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  let cx = x, cy = y, cw = w;
  for (const k of keys || []) {
    const ka = typeof k.at === 'number' ? k.at : cue(k.at);
    const p = prog(t, ka, k.dur ?? 0.6, E.move);
    cx = mix(cx, k.x ?? cx, p); cy = mix(cy, k.y ?? cy, p); cw = mix(cw, k.w ?? cw, p);
  }
  const dimV = useSteps(dim, t, 0);
  const pIn = enter === 'none' ? 1 : prog(t, a, 0.45);
  const pOut = u === undefined ? 0 : prog(t, u, 0.35, E.move);
  if (pIn <= 0 || pOut >= 1) return null;
  const pad = cw * 0.055;
  const imgW = cw - pad * 2;
  const imgH = imgW * 4 / 3;
  const nameH = cw * 0.3;
  const ch = pad * 2 + imgH + nameH;
  return (
    <div style={{position: 'absolute', left: cx - cw / 2, top: cy - ch / 2, width: cw, height: ch, background: PAL.card,
      boxShadow: '0 10px 28px rgba(40,30,20,0.30), 0 1px 2px rgba(40,30,20,0.2)',
      opacity: pIn * (1 - pOut),
      transform: `translateY(${(1 - pIn) * 24}px) scale(${0.96 + 0.04 * pIn}) rotate(${tilt}deg)`}}>
      <div style={{position: 'absolute', inset: cw * 0.018, border: `1.5px solid ${PAL.crimson}`}} />
      <div style={{position: 'absolute', inset: cw * 0.032, border: `1px solid ${PAL.crimson}`, opacity: 0.8}} />
      <Img src={asset(src)} style={{position: 'absolute', left: pad, top: pad, width: imgW, height: imgH, objectFit: 'cover'}} />
      <div style={{position: 'absolute', left: pad, top: pad + imgH, width: imgW, height: nameH, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center'}}>
        <div style={{width: cw * 0.28, height: 1.5, background: PAL.crimson, marginBottom: cw * 0.035, opacity: 0.85}} />
        <div style={{fontFamily: FONT.text, fontSize: nameSize ?? Math.min(Math.max(20, cw * 0.062), (imgW * 0.98) / Math.max(1, name.length * 0.86)),
          letterSpacing: '0.18em', textTransform: 'uppercase', color: PAL.ink, textAlign: 'center', lineHeight: 1.15, whiteSpace: 'nowrap'}}>{name}</div>
        {sub && <div style={{fontFamily: FONT.display, fontStyle: 'italic', fontWeight: 500, fontSize: Math.max(18, cw * 0.05), color: PAL.grey, marginTop: cw * 0.022, whiteSpace: 'nowrap'}}>{sub}</div>}
      </div>
      {dimV > 0.001 && <div style={{position: 'absolute', inset: 0, background: PAL.paper, opacity: dimV}} />}
    </div>
  );
};

/** Paper-coloured glow behind type sitting on an unwashed plate. */
export const haloShadow = (night = false) => night
  ? '0 0 10px rgba(20,19,22,0.95), 0 0 4px rgba(20,19,22,0.95)'
  : '0 0 12px rgba(236,229,215,0.98), 0 0 5px rgba(236,229,215,0.98), 0 0 2px rgba(236,229,215,1)';

/** Tiny wide-tracked caps above a composition ("THE FIFTH CENTURY"). `halo` for busy plates. */
export const Kicker: React.FC<{text: string; at?: At; until?: At; x?: number; y?: number; color?: string; size?: number; align?: 'center' | 'left'; halo?: boolean}> = ({text, at, until, x = 960, y = 128, color, size = 21, align = 'center', halo}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const op = env(t, a, u, 0.3);
  if (op <= 0) return null;
  return (
    <div style={{position: 'absolute', top: y, left: align === 'center' ? x - 800 : x, width: align === 'center' ? 1600 : 1400, textAlign: align,
      fontFamily: FONT.text, fontSize: size, letterSpacing: '0.34em', textTransform: 'uppercase', color: color ?? k.ink, opacity: op,
      textShadow: halo ? haloShadow(k.night) : undefined}}>{text}</div>
  );
};

/**
 * Display type — a name, term, date or ≤7-word statement (never a restatement
 * of the caption). Bodoni roman = statement; italic = concept (blue by default).
 * (x, y) = anchor point; align sets which edge x refers to. y = top of the text.
 */
export const Statement: React.FC<{
  text: React.ReactNode; at?: At; until?: At; x?: number; y?: number; size?: number; italic?: boolean;
  color?: string; align?: 'center' | 'left' | 'right'; width?: number; weight?: number; font?: 'display' | 'text' | 'greek';
  rise?: number; letterSpacing?: string; caps?: boolean; halo?: boolean; style?: React.CSSProperties;
}> = ({text, at, until, x = 960, y = 400, size = 76, italic, color, align = 'center', width = 1500, weight = 500, font = 'display', rise = 8, letterSpacing, caps, halo, style}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const pIn = prog(t, a, 0.45);
  const op = env(t, a, u, 0.45);
  if (op <= 0) return null;
  const left = align === 'center' ? x - width / 2 : align === 'left' ? x : x - width;
  return (
    <div style={{position: 'absolute', left, top: y, width, textAlign: align, fontFamily: FONT[font], fontStyle: italic ? 'italic' : 'normal',
      fontWeight: weight, fontSize: size, lineHeight: 1.12, color: color ?? (italic ? k.blue : k.ink), opacity: op,
      letterSpacing, textTransform: caps ? 'uppercase' : undefined, transform: `translateY(${(1 - pIn) * rise}px)`,
      textShadow: halo ? haloShadow(k.night) : undefined, ...style}}>{text}</div>
  );
};

/** Heavy condensed crimson caps with a full stop. ≤1 per ~40 s. Slams in. */
export const Punch: React.FC<{text: string; at?: At; until?: At; x?: number; y?: number; size?: number; color?: string; width?: number}> = ({text, at, until, x = 960, y = 560, size = 150, color, width = 1700}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const p = prog(t, a, 0.18, E.impact);
  const out = u === undefined ? 0 : prog(t, u, 0.3, E.move);
  if (p <= 0 || out >= 1) return null;
  return (
    <div style={{position: 'absolute', left: x - width / 2, top: y, width, textAlign: 'center', fontFamily: FONT.punch, fontSize: size,
      lineHeight: 1, letterSpacing: '0.01em', color: color ?? k.crimson, textTransform: 'uppercase',
      opacity: p * (1 - out), transform: `scale(${1.15 - 0.15 * p})`, transformOrigin: '50% 50%'}}>{text}</div>
  );
};

/** Crimson rule drawn through its children L→R on the cue; children fade to `to`. */
export const Strike: React.FC<{at: At; dur?: number; to?: number; color?: string; thickness?: number; children: React.ReactNode}> = ({at, dur = 0.35, to = 0.5, color, thickness = 4, children}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at);
  const p = prog(t, a, dur, E.move);
  return (
    <span style={{position: 'relative', display: 'inline-block'}}>
      <span style={{opacity: mix(1, to, p)}}>{children}</span>
      <span style={{position: 'absolute', left: '-3%', top: '54%', width: '106%', height: thickness, background: color ?? k.crimson,
        transform: `scaleX(${p}) rotate(-1.5deg)`, transformOrigin: 'left center'}} />
    </span>
  );
};

/** A row list that builds on cues. (x, y) = top-left. numerals: roman i, ii, iii… */
export const List: React.FC<{
  rows: {text: React.ReactNode; at: At; strike?: At; color?: string}[]; x: number; y: number; size?: number; gap?: number;
  italic?: boolean; numerals?: boolean; font?: 'display' | 'text' | 'type'; until?: At;
}> = ({rows, x, y, size = 46, gap = 1.35, italic, numerals, font = 'display', until}) => {
  const t = useT();
  const k = useInk();
  const cue = useCue();
  const u = until === undefined ? undefined : useAt(until);
  const ROM = ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x', 'xi', 'xii'];
  return (
    <>
      {rows.map((r, i) => {
        const a = typeof r.at === 'number' ? r.at : cue(r.at);
        const p = prog(t, a, 0.4);
        const op = env(t, a, u, 0.4);
        if (op <= 0) return null;
        const body = r.strike !== undefined ? <Strike at={r.strike}>{r.text}</Strike> : r.text;
        return (
          <div key={i} style={{position: 'absolute', left: x, top: y + i * size * gap, opacity: op, transform: `translateX(${(1 - p) * -10}px)`,
            fontFamily: FONT[font], fontSize: size, fontStyle: italic ? 'italic' : 'normal', color: r.color ?? k.ink, whiteSpace: 'nowrap', lineHeight: 1.1}}>
            {numerals && <span style={{fontFamily: FONT.display, fontStyle: 'italic', color: k.crimson, fontSize: size * 0.55, display: 'inline-block', width: size * 1.1, textAlign: 'right', marginRight: size * 0.4}}>{ROM[i]}</span>}
            {body}
          </div>
        );
      })}
    </>
  );
};

type Pt = [number, number];
const pathFrom = (pts: Pt[], smooth = true) => {
  if (pts.length < 2) return '';
  if (!smooth || pts.length === 2) return 'M' + pts.map((p) => `${p[0]},${p[1]}`).join(' L');
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p2[0]},${p2[1]}`;
  }
  return d;
};

/**
 * A line that draws itself: pass `d` (SVG path) or `points` (smoothed).
 * dashed/dotted lines draw through a mask so the dash pattern stays fixed.
 * `head` puts a dot at the moving tip (routes).
 */
export const Draw: React.FC<{
  d?: string; points?: Pt[]; smooth?: boolean; at?: At; dur?: number; until?: At; color?: string; width?: number;
  dash?: 'solid' | 'dashed' | 'dotted'; head?: boolean; arrow?: boolean; opacity?: number; fill?: string; fillAt?: At;
}> = ({d, points, smooth = true, at, dur = 1.0, until, color, width = 3, dash = 'solid', head, arrow, opacity = 1, fill, fillAt}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const fa = fillAt === undefined ? undefined : useAt(fillAt);
  const p = prog(t, a, dur, E.move);
  const out = u === undefined ? 0 : prog(t, u, 0.35, E.move);
  if (p <= 0 || out >= 1) return null;
  const path = d ?? pathFrom(points || [], smooth);
  const c = color ?? k.crimson;
  const id = `m${hashStr(`${path}|${a}|${dur}|${width}|${dash}`)}`;
  const da = dash === 'dashed' ? `${width * 4} ${width * 3}` : dash === 'dotted' ? `0.1 ${width * 3}` : undefined;
  const fp = fa === undefined ? 0 : prog(t, fa, 0.45, E.move);
  return (
    <svg width={W} height={H} style={{position: 'absolute', left: 0, top: 0, opacity: opacity * (1 - out), overflow: 'visible'}}>
      <defs>
        <mask id={id} maskUnits="userSpaceOnUse" x={0} y={0} width={W} height={H}>
          <path d={path} pathLength={1} stroke="#fff" strokeWidth={width * 4 + 6} fill="none" strokeDasharray={`${p} 1`} strokeLinecap="round" />
        </mask>
        {arrow && (
          <marker id={id + 'a'} viewBox="0 0 10 10" refX="6" refY="5" markerWidth={4} markerHeight={4} orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={c} />
          </marker>
        )}
      </defs>
      {fill && <path d={path} fill={fill} opacity={fp} stroke="none" />}
      <path d={path} stroke={c} strokeWidth={width} fill="none" strokeDasharray={da} strokeLinecap="round" strokeLinejoin="round"
        mask={`url(#${id})`} markerEnd={arrow && p > 0.98 ? `url(#${id}a)` : undefined} />
      {head && p < 1 && points && <HeadDot path={path} p={p} color={c} r={width * 2.2} />}
    </svg>
  );
};

const HeadDot: React.FC<{path: string; p: number; color: string; r: number}> = ({path, p, color, r}) => {
  // Approximate the tip by sampling the path in the DOM-free way: SVG
  // getPointAtLength is unavailable at SSR, so render a <circle> on an
  // animateMotion-free offset using a dash trick instead.
  return <path d={path} pathLength={1} stroke={color} strokeWidth={r * 2} fill="none" strokeLinecap="round" strokeDasharray={`0.0001 1`} strokeDashoffset={-p} />;
};

/** Map pin: crimson dot drops in with one ring pulse; optional tag label. */
export const Pin: React.FC<{x: number; y: number; at?: At; until?: At; label?: string; side?: 'right' | 'left' | 'above' | 'below'; color?: string; r?: number}> = ({x, y, at, until, label, side = 'right', color, r = 9}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const p = prog(t, a, 0.25, E.impact);
  const out = u === undefined ? 0 : prog(t, u, 0.3, E.move);
  if (p <= 0 || out >= 1) return null;
  const ring = prog(t, a + 0.2, 0.7, E.enter);
  const c = color ?? k.crimson;
  const off = r + 14;
  const pos: React.CSSProperties = side === 'right' ? {left: x + off, top: y, transform: 'translateY(-50%)'}
    : side === 'left' ? {left: x - off, top: y, transform: 'translate(-100%, -50%)'}
    : side === 'above' ? {left: x, top: y - off, transform: 'translate(-50%, -100%)'}
    : {left: x, top: y + off, transform: 'translate(-50%, 0)'};
  return (
    <div style={{position: 'absolute', left: 0, top: 0, opacity: 1 - out}}>
      <div style={{position: 'absolute', left: x - r, top: y - r, width: r * 2, height: r * 2, borderRadius: '50%', background: c,
        transform: `scale(${1.6 - 0.6 * p})`, opacity: p, boxShadow: '0 0 0 2px rgba(242,237,227,0.85)'}} />
      {ring < 1 && <div style={{position: 'absolute', left: x - r * 3, top: y - r * 3, width: r * 6, height: r * 6, borderRadius: '50%',
        border: `2px solid ${c}`, transform: `scale(${0.4 + 0.6 * ring})`, opacity: (1 - ring) * p}} />}
      {label && <div style={{position: 'absolute', ...pos, opacity: prog(t, a + 0.1, 0.35)}}><TagBox text={label} /></div>}
    </div>
  );
};

const TagBox: React.FC<{text: string; size?: number}> = ({text, size = 26}) => (
  <div style={{background: 'rgba(242,237,227,0.94)', border: '1px solid rgba(60,52,44,0.5)', padding: `${size * 0.28}px ${size * 0.6}px ${size * 0.22}px`,
    fontFamily: FONT.text, fontSize: size, letterSpacing: '0.16em', textTransform: 'uppercase', color: PAL.ink, whiteSpace: 'nowrap',
    boxShadow: '0 2px 8px rgba(40,30,20,0.18)'}}>{text}</div>
);

/**
 * Cream label tag with an optional leader line to a point on the image
 * (x, y = tag centre; to = the point it names). Never letter onto the picture itself.
 */
export const Tag: React.FC<{text: string; x: number; y: number; to?: Pt; at?: At; until?: At; size?: number}> = ({text, x, y, to, at, until, size = 26}) => {
  const t = useT();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const op = env(t, a, u, 0.35);
  if (op <= 0) return null;
  const lp = prog(t, a, 0.5, E.move);
  return (
    <div style={{position: 'absolute', left: 0, top: 0, width: W, height: H, opacity: op}}>
      {to && (
        <svg width={W} height={H} style={{position: 'absolute', left: 0, top: 0}}>
          <line x1={x} y1={y} x2={mix(x, to[0], lp)} y2={mix(y, to[1], lp)} stroke={PAL.ink} strokeWidth={1.4} opacity={0.75} />
          <circle cx={to[0]} cy={to[1]} r={4} fill={PAL.crimson} opacity={lp} />
        </svg>
      )}
      <div style={{position: 'absolute', left: x, top: y, transform: 'translate(-50%, -50%)'}}><TagBox text={text} size={size} /></div>
    </div>
  );
};

/** Deterministic ink splatter blooming behind a word (≤1 per video). Centre (x, y). */
export const Splat: React.FC<{x: number; y: number; at?: At; size?: number; seed?: number; color?: string; until?: At}> = ({x, y, at, size = 180, seed = 3, color, until}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const p = prog(t, a, 0.3, E.enter);
  const out = u === undefined ? 0 : prog(t, u, 0.35, E.move);
  if (p <= 0 || out >= 1) return null;
  const rnd = seeded(seed);
  const blobs: {cx: number; cy: number; r: number}[] = [];
  for (let i = 0; i < 14; i++) {
    const ang = rnd() * Math.PI * 2, dist = rnd() * size * 0.35;
    blobs.push({cx: Math.cos(ang) * dist, cy: Math.sin(ang) * dist, r: size * (0.12 + rnd() * 0.22)});
  }
  const drops: {cx: number; cy: number; r: number; d: number}[] = [];
  for (let i = 0; i < 22; i++) {
    const ang = rnd() * Math.PI * 2, dist = size * (0.55 + rnd() * 0.9);
    drops.push({cx: Math.cos(ang) * dist, cy: Math.sin(ang) * dist, r: 2 + rnd() * size * 0.04, d: rnd()});
  }
  const c = color ?? k.crimson;
  return (
    <svg width={size * 4} height={size * 4} style={{position: 'absolute', left: x - size * 2, top: y - size * 2, opacity: 0.9 * (1 - out)}}
      viewBox={`${-size * 2} ${-size * 2} ${size * 4} ${size * 4}`}>
      <g transform={`scale(${0.3 + 0.7 * p})`}>{blobs.map((b, i) => <circle key={i} cx={b.cx} cy={b.cy} r={b.r} fill={c} />)}</g>
      {drops.map((dd, i) => {
        const q = clamp01((p - dd.d * 0.4) / 0.6);
        return <circle key={i} cx={dd.cx * q} cy={dd.cy * q} r={dd.r} fill={c} opacity={q} />;
      })}
    </svg>
  );
};

/** Typewriter text: characters appear at `cps` from the cue; blinking caret while typing. */
export const Type: React.FC<{text: string; at?: At; until?: At; x: number; y: number; size?: number; cps?: number; color?: string; width?: number}> = ({text, at, until, x, y, size = 30, cps = 26, color, width = 1400}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  if (t < a) return null;
  const n = Math.min(text.length, Math.floor((t - a) * cps));
  const out = u === undefined ? 0 : prog(t, u, 0.3, E.move);
  const caret = n < text.length || Math.floor(t * 2) % 2 === 0;
  return (
    <div style={{position: 'absolute', left: x, top: y, width, fontFamily: FONT.type, fontSize: size, color: color ?? k.ink, whiteSpace: 'pre-wrap', opacity: 1 - out, lineHeight: 1.35}}>
      {text.slice(0, n)}<span style={{opacity: caret ? 1 : 0}}>▍</span>
    </div>
  );
};

/** Fade-and-rise wrapper for any custom JSX (absolute children position themselves). */
export const Enter: React.FC<{at?: At; until?: At; rise?: number; dur?: number; children: React.ReactNode; style?: React.CSSProperties}> = ({at, until, rise = 12, dur = 0.45, children, style}) => {
  const t = useT();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const p = prog(t, a, dur);
  const op = env(t, a, u, dur);
  if (op <= 0) return null;
  return <div style={{position: 'absolute', inset: 0, opacity: op, transform: `translateY(${(1 - p) * rise}px)`, ...style}}>{children}</div>;
};

/** Thin gold or crimson hairline rule (section/title cards). Draws from centre. */
export const Rule: React.FC<{x?: number; y: number; w?: number; at?: At; color?: string; thickness?: number}> = ({x = 960, y, w = 520, at, color = PAL.gold, thickness = 1.5}) => {
  const t = useT();
  const a = useAt(at, 0);
  const p = prog(t, a, 0.6, E.move);
  if (p <= 0) return null;
  return <div style={{position: 'absolute', left: x - (w * p) / 2, top: y, width: w * p, height: thickness, background: color}} />;
};

/**
 * Unit chart: n dots in a grid (top-left x, y; `cols` per row, `gap` px pitch)
 * cascading in over `spread` s from the cue. `keep` = {at, count} fades all but
 * the first `count` to ghosts and turns the survivors crimson ("82 written,
 * 7 survive"). `extra` = {at, count} adds dashed-outline dots after the n
 * (disputed / alternative tallies). Dot i sits at
 * (x + (i % cols) * gap + gap/2, y + floor(i / cols) * gap + gap/2).
 */
export const Tally: React.FC<{
  n: number; x: number; y: number; cols?: number; gap?: number; r?: number; at?: At; spread?: number;
  keep?: {at: At; count: number}; extra?: {at: At; count: number}; color?: string; until?: At;
}> = ({n, x, y, cols = 20, gap = 34, r = 11, at, spread = 1.6, keep, extra, color, until}) => {
  const t = useT();
  const k = useInk();
  const a = useAt(at, 0);
  const cue = useCue();
  const u = until === undefined ? undefined : useAt(until);
  const ka = keep ? (typeof keep.at === 'number' ? keep.at : cue(keep.at)) : undefined;
  const ea = extra ? (typeof extra.at === 'number' ? extra.at : cue(extra.at)) : undefined;
  const out = u === undefined ? 0 : prog(t, u, 0.35, E.move);
  if (t < a || out >= 1) return null;
  const kp = ka === undefined ? 0 : prog(t, ka, 0.6, E.move);
  const dots: React.ReactNode[] = [];
  const total = n + (extra?.count ?? 0);
  for (let i = 0; i < total; i++) {
    const isExtra = i >= n;
    const start = isExtra ? (ea ?? 1e9) + ((i - n) / Math.max(1, extra!.count)) * 0.6 : a + (i / Math.max(1, n)) * spread;
    const p = prog(t, start, 0.25);
    if (p <= 0) continue;
    const kept = keep && i < keep.count;
    const cx = x + (i % cols) * gap + gap / 2;
    const cy = y + Math.floor(i / cols) * gap + gap / 2;
    const fill = isExtra ? 'none' : kept ? (kp > 0 ? k.crimson : color ?? k.ink) : color ?? k.ink;
    const op = isExtra ? p * (1 - kp * 0.75) : kept ? p : p * (1 - kp * 0.82);
    dots.push(<circle key={i} cx={cx} cy={cy} r={r * (0.6 + 0.4 * p) * (kept ? 1 + 0.15 * kp : 1)} fill={fill}
      stroke={isExtra ? color ?? k.ink : 'none'} strokeWidth={isExtra ? 1.6 : 0} strokeDasharray={isExtra ? '3 3' : undefined} opacity={op} />);
  }
  return <svg width={W} height={H} style={{position: 'absolute', left: 0, top: 0, opacity: 1 - out}}>{dots}</svg>;
};

// ───────────────────────────────────────────────────────────── routes

/** Sample the same smoothed curve `Draw points` renders into a polyline. */
const samplePath = (pts: Pt[], smooth = true, perSeg = 24): Pt[] => {
  if (pts.length < 2) return pts;
  if (!smooth || pts.length === 2) return pts;
  const out: Pt[] = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1: Pt = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2: Pt = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    for (let k = i === 0 ? 0 : 1; k <= perSeg; k++) {
      const u = k / perSeg, v = 1 - u;
      out.push([v * v * v * p1[0] + 3 * v * v * u * c1[0] + 3 * v * u * u * c2[0] + u * u * u * p2[0],
        v * v * v * p1[1] + 3 * v * v * u * c1[1] + 3 * v * u * u * c2[1] + u * u * u * p2[1]]);
    }
  }
  return out;
};

/**
 * Point at fraction p (0–1, by arc length) along the route `Draw points={…}` draws,
 * plus the heading in degrees — for anything that must ride a drawn route.
 */
export const routePoint = (points: Pt[], p: number, smooth = true): {x: number; y: number; angle: number} => {
  const poly = samplePath(points, smooth);
  const seg: number[] = [0];
  for (let i = 1; i < poly.length; i++) seg.push(seg[i - 1] + Math.hypot(poly[i][0] - poly[i - 1][0], poly[i][1] - poly[i - 1][1]));
  const target = clamp01(p) * seg[seg.length - 1];
  let i = 1;
  while (i < seg.length - 1 && seg[i] < target) i++;
  const f = (target - seg[i - 1]) / Math.max(1e-6, seg[i] - seg[i - 1]);
  const a = poly[i - 1], b = poly[i];
  return {x: mix(a[0], b[0], f), y: mix(a[1], b[1], f), angle: (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI};
};

/**
 * An object cutout that travels a route: pair it with `<Draw points={P} at dur>` using the
 * SAME points, `at` and `dur` and it sits on the drawing tip. `turn` rotates it to the
 * heading (ships); `until` fades it out (e.g. on arrival). Inside a Plate, coordinates are
 * image space like everything else there.
 */
export const Ride: React.FC<{src: string; points: Pt[]; w: number; at?: At; dur?: number; until?: At; smooth?: boolean; turn?: boolean; flip?: boolean}> = ({src, points, w, at, dur = 1.0, until, smooth = true, turn, flip}) => {
  const t = useT();
  const asset = useAsset();
  const a = useAt(at, 0);
  const u = until === undefined ? undefined : useAt(until);
  const op = env(t, a, u, 0.3);
  if (op <= 0) return null;
  const pos = routePoint(points, prog(t, a, dur, E.move), smooth);
  const rot = turn ? pos.angle + (flip ? 180 : 0) : 0;
  return (
    <div style={{position: 'absolute', left: pos.x - w / 2, top: pos.y, width: w, opacity: op, transform: `translateY(-50%) rotate(${rot}deg)`}}>
      <Img src={asset(src)} style={{width: '100%', display: 'block'}} />
    </div>
  );
};
