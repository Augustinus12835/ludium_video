// Shared proxy kit: deterministic Three.js blocking scenes for the Omni "motion proxy".
// A proxy only has to carry LAYOUT, CAMERA and TIMING — the video model paints everything.
import * as THREE from 'three';
export { THREE };

export const W = 1280, H = 720, DUR = 10.0;

export function setup({ bg = 0xe9e2d2, fogNear = 60, fogFar = 420, fov = 38 } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setSize(W, H); renderer.setPixelRatio(1);
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  document.body.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(bg);
  scene.fog = new THREE.Fog(bg, fogNear, fogFar);
  const cam = new THREE.PerspectiveCamera(fov, W / H, 0.1, 3000);
  scene.add(new THREE.HemisphereLight(0xfff8ec, 0x8a7a60, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 2.2);
  sun.position.set(-40, 60, 30); sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -60, right: 60, top: 60, bottom: -60, near: 1, far: 300 });
  scene.add(sun);
  return { renderer, scene, cam, sun };
}

const _m = {};
export const M = (c, opts = {}) => {
  const k = c + JSON.stringify(opts);
  return _m[k] || (_m[k] = new THREE.MeshLambertMaterial({ color: c, ...opts }));
};
export const C = {
  ground: 0xb9a682, stone: 0xcfc6b4, rock: 0xa89c86, hill: 0x9c9484, far: 0xb4b0a6, skin: 0xc79a72,
  tunic: 0xe6dcc6, robe: 0xbfb39c, cloak: 0x7a5a46, bronze: 0x8a6a34, wood: 0x5a4430, crest: 0x2b241e,
  iron: 0x555048, leather: 0x6b5238, bush: 0x6f7258, water: 0x7f8f96, foam: 0xf4f2ec, tree: 0x5d6b4c,
};

// deterministic random
export function rng(seed = 7) { let s = seed; return () => (s = (s * 16807) % 2147483647) / 2147483647; }

// timing helpers
export const clamp01 = x => x < 0 ? 0 : x > 1 ? 1 : x;
export const ease = x => { x = clamp01(x); return x * x * (3 - 2 * x); };
export const lerp = (a, b, u) => a + (b - a) * u;
// keyframes: [[t, value], ...] (numbers or arrays), smoothstep between keys
export function kf(t, keys) {
  if (t <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [t1, v1] = keys[i], [t0, v0] = keys[i - 1];
    if (t <= t1) {
      const u = ease((t - t0) / (t1 - t0));
      return Array.isArray(v0) ? v0.map((a, j) => lerp(a, v1[j], u)) : lerp(v0, v1, u);
    }
  }
  return keys[keys.length - 1][1];
}

function limb(len, rTop, rBot, mat) {
  // pivot at the top, hangs down -y
  const g = new THREE.Group();
  const m = new THREE.Mesh(new THREE.CylinderGeometry(rTop, rBot, len, 8), mat);
  m.position.y = -len / 2; m.castShadow = true; g.add(m);
  return g;
}

// A person ~1.8 m tall, facing +z. Parts are pivots so we can pose / walk.
// opts: robe (long garment), helmet: 'corinthian'|'cap'|null, shield: 'aspis'|'small'|null,
//       spear: length (held upright in right hand), cloak, beard, scale, tunic colour
export function person(opts = {}) {
  const o = { robe: false, helmet: null, shield: null, spear: 0, cloak: true, scale: 1, tunic: C.tunic, ...opts };
  const p = new THREE.Group();
  const body = new THREE.Group(); p.add(body);
  const add = (geo, mat, x, y, z, parent = body) => { const m = new THREE.Mesh(geo, mat); m.position.set(x, y, z); m.castShadow = true; parent.add(m); return m; };
  const legL = limb(0.9, 0.09, 0.07, M(C.skin)), legR = limb(0.9, 0.09, 0.07, M(C.skin));
  legL.position.set(-0.12, 0.92, 0); legR.position.set(0.12, 0.92, 0); body.add(legL, legR);
  if (o.robe) add(new THREE.CylinderGeometry(0.26, 0.42, 0.85, 12), M(o.tunic), 0, 0.55, 0);
  else add(new THREE.CylinderGeometry(0.26, 0.33, 0.38, 12), M(o.tunic), 0, 0.88, 0);
  add(new THREE.CylinderGeometry(0.25, 0.24, 0.62, 10), M(o.helmet === 'corinthian' ? C.bronze : o.tunic), 0, 1.32, 0);
  const head = new THREE.Group(); head.position.set(0, 1.62, 0); body.add(head);
  add(new THREE.SphereGeometry(0.14, 12, 10), M(C.skin), 0, 0.12, 0, head);
  add(new THREE.BoxGeometry(0.06, 0.06, 0.08), M(C.skin), 0, 0.1, 0.14, head);           // nose = facing cue
  if (o.beard) add(new THREE.SphereGeometry(0.1, 8, 6), M(C.crest), 0, 0.0, 0.08, head);
  if (o.helmet === 'corinthian') {
    add(new THREE.SphereGeometry(0.17, 12, 10), M(C.bronze), 0, 0.14, 0, head);
    add(new THREE.BoxGeometry(0.05, 0.26, 0.45), M(C.crest), 0, 0.38, -0.02, head);
  } else if (o.helmet === 'cap') add(new THREE.SphereGeometry(0.16, 12, 10, 0, Math.PI * 2, 0, Math.PI / 2), M(C.bronze), 0, 0.15, 0, head);
  const armL = limb(0.62, 0.07, 0.06, M(C.skin)), armR = limb(0.62, 0.07, 0.06, M(C.skin));
  armL.position.set(-0.3, 1.58, 0); armR.position.set(0.3, 1.58, 0); body.add(armL, armR);
  if (o.cloak) add(new THREE.BoxGeometry(0.56, o.robe ? 1.1 : 0.7, 0.05), M(o.cloakColor || C.cloak), 0, o.robe ? 1.0 : 1.25, -0.24);
  let shield = null, spear = null;
  if (o.shield === 'aspis') { shield = add(new THREE.CylinderGeometry(0.48, 0.48, 0.06, 24), M(C.bronze), 0.02, -0.35, 0.12, armL); shield.rotation.x = Math.PI / 2; }
  if (o.shield === 'small') { shield = add(new THREE.CylinderGeometry(0.3, 0.3, 0.05, 18), M(C.bronze), 0, 1.3, 0.28); shield.rotation.x = Math.PI / 2; }
  if (o.spear) {
    spear = new THREE.Group(); spear.position.set(0, -0.55, 0.05); armR.add(spear);
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, o.spear, 5), M(C.wood));
    shaft.position.y = o.spear * 0.35; shaft.castShadow = true; spear.add(shaft);
    const tip = new THREE.Mesh(new THREE.ConeGeometry(0.035, 0.2, 5), M(C.iron)); tip.position.y = o.spear * 0.85 + 0.1; spear.add(tip);
  }
  p.scale.setScalar(o.scale);
  p.userData = { body, head, legL, legR, armL, armR, shield, spear, opts: o };
  return p;
}

// gait: phase advances with distance; amp 0 = standing
export function pose(p, { walk = 0, phase = 0, armSwing = true, bob = true } = {}) {
  const u = p.userData, s = Math.sin(phase);
  u.legL.rotation.x = 0.55 * walk * s; u.legR.rotation.x = -0.55 * walk * s;
  if (armSwing) { u.armL.rotation.x = -0.4 * walk * s; u.armR.rotation.x = 0.4 * walk * s; }
  u.body.position.y = bob ? Math.abs(Math.cos(phase)) * 0.05 * walk : 0;
}

export function ground(scene, size = 1200, color = C.ground) {
  const g = new THREE.Mesh(new THREE.PlaneGeometry(size, size), M(color));
  g.rotation.x = -Math.PI / 2; g.receiveShadow = true; scene.add(g); return g;
}
export function ridge(scene, x, z, r, h, color = C.far) {
  const m = new THREE.Mesh(new THREE.ConeGeometry(r, h, 9, 1), M(color));
  m.position.set(x, h / 2 - 2, z); m.scale.z = 0.5; scene.add(m); return m;
}
export function rock(scene, x, y, z, s, R) {
  const m = new THREE.Mesh(new THREE.DodecahedronGeometry(s, 0), M(C.rock));
  m.position.set(x, y, z); m.rotation.set(R() * 3, R() * 3, R() * 3); m.castShadow = true; m.receiveShadow = true;
  scene.add(m); return m;
}
export function tree(scene, x, z, h = 6, cypress = false) {
  const g = new THREE.Group();
  const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.18, h * 0.3, 6), M(C.wood)); trunk.position.y = h * 0.15; g.add(trunk);
  const crown = cypress ? new THREE.Mesh(new THREE.ConeGeometry(h * 0.12, h * 0.9, 8), M(C.tree))
                        : new THREE.Mesh(new THREE.SphereGeometry(h * 0.3, 8, 6), M(C.tree));
  crown.position.y = cypress ? h * 0.55 : h * 0.55; crown.castShadow = true; g.add(crown);
  g.position.set(x, 0, z); scene.add(g); return g;
}
export function building(scene, x, z, w, d, h, ry = 0) {
  const g = new THREE.Group();
  const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), M(C.stone)); b.position.y = h / 2; b.castShadow = true; b.receiveShadow = true; g.add(b);
  const roof = new THREE.Mesh(new THREE.ConeGeometry(Math.max(w, d) * 0.72, h * 0.35, 4), M(0x9a6e4e));
  roof.position.y = h + h * 0.17; roof.rotation.y = Math.PI / 4; roof.scale.set(w / Math.max(w, d), 1, d / Math.max(w, d)); g.add(roof);
  g.position.set(x, 0, z); g.rotation.y = ry; scene.add(g); return g;
}
export function colonnade(scene, x0, z0, x1, z1, n, h = 5) {
  const g = new THREE.Group();
  for (let i = 0; i < n; i++) {
    const u = i / (n - 1);
    const c = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.32, h, 12), M(C.stone));
    c.position.set(lerp(x0, x1, u), h / 2, lerp(z0, z1, u)); c.castShadow = true; g.add(c);
  }
  const len = Math.hypot(x1 - x0, z1 - z0);
  const ent = new THREE.Mesh(new THREE.BoxGeometry(len + 1, 0.8, 1.2), M(C.stone));
  ent.position.set((x0 + x1) / 2, h + 0.4, (z0 + z1) / 2); ent.rotation.y = -Math.atan2(z1 - z0, x1 - x0); g.add(ent);
  scene.add(g); return g;
}

// Water: a displaced plane, animated deterministically
export function water(scene, size = 600, seg = 120, color = C.water) {
  const geo = new THREE.PlaneGeometry(size, size, seg, seg);
  const m = new THREE.Mesh(geo, M(color, { flatShading: true }));
  m.rotation.x = -Math.PI / 2; m.receiveShadow = true; scene.add(m);
  const base = geo.attributes.position.array.slice();
  m.userData.update = (t, amp = 0.25) => {
    const a = geo.attributes.position.array;
    for (let i = 0; i < a.length; i += 3) {
      const x = base[i], y = base[i + 1];
      a[i + 2] = amp * (Math.sin(x * 0.35 + t * 1.7) + 0.6 * Math.sin(y * 0.5 - t * 1.3) + 0.3 * Math.sin((x + y) * 0.9 + t * 2.3));
    }
    geo.attributes.position.needsUpdate = true; geo.computeVertexNormals();
  };
  return m;
}

// Trireme, bow toward +z. Oars on pivots (oarPivots) for sweeping.
export function trireme(scene, { len = 30, beam = 4.5 } = {}) {
  const g = new THREE.Group();
  const hull = new THREE.Mesh(new THREE.BoxGeometry(beam, 2.0, len), M(C.wood)); hull.position.y = 1.0; hull.castShadow = true; g.add(hull);
  const bow = new THREE.Mesh(new THREE.ConeGeometry(beam * 0.55, 5, 4), M(C.wood));
  bow.rotation.x = Math.PI / 2; bow.rotation.y = Math.PI / 4; bow.position.set(0, 1.2, len / 2 + 2.3); bow.scale.set(1, 1, 0.6); g.add(bow);
  const ram = new THREE.Mesh(new THREE.ConeGeometry(0.4, 2.2, 6), M(C.bronze)); ram.rotation.x = Math.PI / 2; ram.position.set(0, 0.2, len / 2 + 4.5); g.add(ram);
  const stern = new THREE.Mesh(new THREE.BoxGeometry(beam * 0.6, 3.5, 1.2), M(C.wood)); stern.position.set(0, 2.5, -len / 2 - 0.3); stern.rotation.x = -0.5; g.add(stern);
  const deck = new THREE.Mesh(new THREE.BoxGeometry(beam * 0.8, 0.2, len * 0.95), M(0x7a6248)); deck.position.y = 2.1; g.add(deck);
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.15, 10, 6), M(C.wood)); mast.position.set(0, 7, 2); g.add(mast);
  const yard = new THREE.Mesh(new THREE.CylinderGeometry(0.08, 0.08, 9, 5), M(C.wood)); yard.rotation.z = Math.PI / 2; yard.position.set(0, 11.5, 2); g.add(yard);
  const oarPivots = [];
  for (const side of [-1, 1]) for (let i = 0; i < 14; i++) {
    const piv = new THREE.Group(); piv.position.set(side * beam / 2, 1.2, -len / 2 + 3 + i * (len - 6) / 13);
    const oar = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 6, 4), M(C.wood));
    oar.rotation.z = side * (Math.PI / 2 + 0.45); oar.position.set(side * 2.5, -1.0, 0); piv.add(oar);
    piv.userData.side = side; g.add(piv); oarPivots.push(piv);
  }
  g.userData.oars = oarPivots;
  g.userData.row = (t, rate = 1.2, amp = 0.45) => oarPivots.forEach(p => { p.rotation.y = p.userData.side * amp * Math.sin(t * rate * Math.PI * 2); });
  scene.add(g); return g;
}

// sparse splash particles (white blobs) whose visibility is driven by the scene
export function splash(scene, n, R) {
  const g = new THREE.Group();
  for (let i = 0; i < n; i++) {
    const s = new THREE.Mesh(new THREE.SphereGeometry(0.3 + R() * 0.5, 6, 5), M(C.foam));
    s.userData = { vx: (R() - 0.5) * 6, vy: 4 + R() * 6, vz: (R() - 0.5) * 6, d: R() * 0.3 };
    g.add(s);
  }
  g.userData.at = (t0, t, origin) => g.children.forEach(s => {
    const u = t - t0 - s.userData.d;
    s.visible = u > 0 && u < 1.6;
    if (s.visible) s.position.set(origin.x + s.userData.vx * u, origin.y + s.userData.vy * u - 4.9 * u * u, origin.z + s.userData.vz * u);
  });
  scene.add(g); return g;
}

export function register(update, cam, renderer, scene) {
  window.renderFrame = (t) => { update(t); renderer.render(scene, cam); return true; };
  window.ready = true;
}
