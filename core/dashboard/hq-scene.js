// The 3D HQ: "The Monaco Estate". A white-marble and champagne-gold estate on the cliff above the harbour:
// the crowned JARVIS HQ building, a fountain plaza with a holographic globe, one glass pavilion per team
// (its agents work at desks inside), pool, tennis court, supercar forecourt, and beyond it the superyacht
// marina, the street circuit with its grandstand, and the city on the hills.
//
// startScene(opts) builds it in opts.stage and returns { update(model), dispatch(agent), focus(id), demo(on),
// view(cmd), whereabouts() }. It only draws what the page's data adapter (hq.js) hands it: agents sit at their
// desks while they have real work and spend idle time around the estate (plainly leisure). The city, yachts
// and cars are scenery. Simplified mode (phones, weak devices, or ?simple) skips bloom and shadows and draws less.

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
let STATUS = {};

export async function startScene(opts) {
  STATUS = opts.status;
  let THREE, addons;
  try {
    THREE = await import("three");
    const [oc, re] = await Promise.all([
      import("three/addons/controls/OrbitControls.js"), import("three/addons/environments/RoomEnvironment.js")]);
    addons = { OrbitControls: oc.OrbitControls, RoomEnvironment: re.RoomEnvironment };
    if (!opts.simple) {
      const [ec, rp, bp, op, sm, ao, wa] = await Promise.all([
        import("three/addons/postprocessing/EffectComposer.js"), import("three/addons/postprocessing/RenderPass.js"),
        import("three/addons/postprocessing/UnrealBloomPass.js"), import("three/addons/postprocessing/OutputPass.js"),
        import("three/addons/postprocessing/SMAAPass.js"), import("three/addons/postprocessing/SSAOPass.js"), import("three/addons/objects/Water.js")]);
      Object.assign(addons, { EffectComposer: ec.EffectComposer, RenderPass: rp.RenderPass, UnrealBloomPass: bp.UnrealBloomPass, OutputPass: op.OutputPass,
        SMAAPass: sm.SMAAPass, SSAOPass: ao.SSAOPass, Water: wa.Water });
    }
  } catch (e) {
    return opts.fail("The 3D view needs an internet connection the first time, to load three.js. Everything else works.");
  }
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas: opts.canvas, antialias: true, powerPreference: "high-performance" }); }
  catch (e) { return opts.fail("This device can't show the 3D view. Everything else works."); }
  try { await document.fonts.ready; } catch (e) { /* system fonts are fine */ }
  return buildWorld(THREE, addons, renderer, opts);
}

function buildWorld(THREE, addons, renderer, opts) {
  const { canvas, stage, labels: tags, veil } = opts;
  const simple = !!opts.simple;
  const V3 = THREE.Vector3, C = (hex) => new THREE.Color(hex);
  renderer.setPixelRatio(Math.min(devicePixelRatio, simple ? 1.5 : 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.88;
  renderer.shadowMap.enabled = !simple;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene3 = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  // Reflections only where they belong (gold, glass, paint, water): an environment on everything flattens the light.
  const envTex = pmrem.fromScene(new addons.RoomEnvironment(renderer), 0.04).texture;
  scene3.fog = new THREE.Fog(0xf0dcc4, 420, 1700);

  const camera = new THREE.PerspectiveCamera(32, 1, 1, 5000);
  const controls = new addons.OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = 0.07;
  controls.enablePan = true; controls.screenSpacePanning = false; controls.panSpeed = 0.7;
  controls.minDistance = 5; controls.maxDistance = 360; controls.minPolarAngle = 0.25; controls.maxPolarAngle = 1.5;
  controls.rotateSpeed = 0.5; controls.zoomSpeed = 0.8;
  // The concept's view: high over the estate's south lawn, looking north over HQ to the harbour and the city.
  const HOME = () => ({ az: 0, pol: 1.22, r: stage.clientWidth < stage.clientHeight ? 230 : 158, target: new V3(0, 4, -30) });
  // What walk mode bumps into, in world x/z: round things (trees, lamps, desks), wall segments, and raised floors (a
  // function of x and z giving the floor height there, or null). Each is registered where the thing is built.
  const solid = { circles: [], segs: [], floors: [], camSegs: [] };   // camSegs: walls only the camera respects (upper floors)
  solid.circle = (x, z, r) => solid.circles.push([x, z, r]);
  solid.seg = (ax, az, bx, bz) => solid.segs.push([ax, az, bx, bz]);
  solid.rect = (x, z, w, d) => { const l = x - w / 2, r = x + w / 2, t = z - d / 2, b = z + d / 2; solid.seg(l, t, r, t); solid.seg(r, t, r, b); solid.seg(r, b, l, b); solid.seg(l, b, l, t); };

  // ---------------------------------------------------------------- textures and materials
  function canvasTex(w, h, draw, repeat) {
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    draw(c.getContext("2d"), w, h);
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = renderer.capabilities.getMaxAnisotropy();
    if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(repeat, repeat); }
    return t;
  }
  let seed = 7;
  const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const veins = (g, w, h, tones, n, step = 18) => {
    for (let i = 0; i < n; i++) {
      const [col, a0] = tones[i % tones.length];
      g.strokeStyle = col.replace("A", (a0 * (0.4 + rand() * 0.9)).toFixed(3));
      g.lineWidth = 0.5 + rand() * 1.8; g.beginPath();
      let x = rand() * w, y = rand() * h, ang = rand() * Math.PI * 2;
      g.moveTo(x, y);
      for (let k = 0; k < 40; k++) { ang += (rand() - 0.5) * 0.7; x += Math.cos(ang) * step; y += Math.sin(ang) * step; g.lineTo(x, y); }
      g.stroke();
    }
  };
  // Estate paving: large ivory marble slabs with fine joints and faint grey-gold veining.
  const pavingTex = canvasTex(1024, 1024, (g, w, h) => {
    g.fillStyle = "#e6dccb"; g.fillRect(0, 0, w, h);
    for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) { g.fillStyle = `rgba(${200 + rand() * 30},${190 + rand() * 30},${170 + rand() * 30},.08)`; g.fillRect(x * 128, y * 128, 128, 128); }
    veins(g, w, h, [["rgba(140,140,150,A)", 0.16], ["rgba(190,160,100,A)", 0.12]], 70);
    g.strokeStyle = "rgba(160,150,130,.35)"; g.lineWidth = 2;
    for (let i = 0; i <= 8; i++) { g.beginPath(); g.moveTo(i * 128, 0); g.lineTo(i * 128, h); g.stroke(); g.beginPath(); g.moveTo(0, i * 128); g.lineTo(w, i * 128); g.stroke(); }
  }, 14);
  pavingTex.repeat.set(14, 10.5);
  const marbleTex = canvasTex(512, 512, (g, w, h) => { g.fillStyle = "#f6f4ef"; g.fillRect(0, 0, w, h); veins(g, w, h, [["rgba(130,134,146,A)", 0.18], ["rgba(184,147,63,A)", 0.12]], 40, 12); }, 2);
  const plazaTex = canvasTex(1024, 1024, (g, w, h) => {
    const cx = w / 2;
    g.fillStyle = "#f3efe7"; g.fillRect(0, 0, w, h);
    veins(g, w, h, [["rgba(140,140,150,A)", 0.14]], 40);
    const ring = (r, col, lw) => { g.strokeStyle = col; g.lineWidth = lw; g.beginPath(); g.arc(cx, cx, r, 0, Math.PI * 2); g.stroke(); };
    ring(500, "#c6a46a", 10); ring(470, "rgba(23,40,58,.55)", 4); ring(330, "#c6a46a", 6);
    for (let i = 0; i < 32; i++) {   // a compass-rose inlay of alternating navy and gold rays
      const a = (i / 32) * Math.PI * 2;
      g.fillStyle = i % 2 ? "rgba(198,164,106,.55)" : "rgba(23,40,58,.35)";
      g.beginPath(); g.moveTo(cx + Math.cos(a) * 330, cx + Math.sin(a) * 330);
      g.lineTo(cx + Math.cos(a + 0.09) * 470, cx + Math.sin(a + 0.09) * 470); g.lineTo(cx + Math.cos(a - 0.09) * 470, cx + Math.sin(a - 0.09) * 470); g.fill();
    }
  });
  const lawnTex = canvasTex(256, 256, (g, w, h) => {
    g.fillStyle = "#7e9d52"; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 2500; i++) { g.fillStyle = `rgba(${60 + rand() * 60},${110 + rand() * 50},${40 + rand() * 30},.25)`; g.fillRect(rand() * w, rand() * h, 2, 2); }
    for (let i = 0; i < 8; i++) { g.fillStyle = i % 2 ? "rgba(255,255,255,.04)" : "rgba(0,0,0,.03)"; g.fillRect(0, i * 32, w, 32); }  // mowing stripes
  }, 4);
  const rippleTex = canvasTex(256, 256, (g, w, h) => {
    const img = g.createImageData(w, h);
    const hgt = (x, y) => Math.sin(x * 0.098 + Math.sin(y * 0.05) * 2) * 0.5 + Math.sin(y * 0.122 + x * 0.03) * 0.35 + Math.sin((x + y) * 0.21) * 0.15;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const dx = hgt(x + 1, y) - hgt(x - 1, y), dy = hgt(x, y + 1) - hgt(x, y - 1), i = (y * w + x) * 4;
      img.data[i] = 128 + dx * 60; img.data[i + 1] = 128 + dy * 60; img.data[i + 2] = 255; img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
  }, 60);
  rippleTex.colorSpace = THREE.NoColorSpace;
  const poolRipple = rippleTex.clone(); poolRipple.repeat.set(3, 3); poolRipple.needsUpdate = true;
  const glowTex = (rgb) => canvasTex(128, 128, (g, w) => { const gr = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2); gr.addColorStop(0, `rgba(${rgb},.95)`); gr.addColorStop(0.35, `rgba(${rgb},.35)`); gr.addColorStop(1, `rgba(${rgb},0)`); g.fillStyle = gr; g.fillRect(0, 0, w, w); });

  const std = (o) => new THREE.MeshStandardMaterial(o);
  const world = {};       // scenery the frame loop moves
  const glowMats = [];    // [material, base emissive intensity]: everything that lights up in the evening
  const M = {
    paving: std({ map: pavingTex, roughness: 0.35, envMapIntensity: 0.6 }),
    marble: std({ map: marbleTex, roughness: 0.25, envMapIntensity: 0.8 }),
    ivory: std({ color: 0xeae5da, roughness: 0.55 }),
    stone: std({ color: 0xd8cebd, roughness: 0.85 }),
    gold: std({ color: 0xc6a46a, metalness: 1, roughness: 0.3, envMap: envTex, envMapIntensity: 1.1 }),
    glass: std({ color: 0x9fbccc, metalness: 0.35, roughness: 0.04, transparent: true, opacity: 0.34, depthWrite: false, side: THREE.DoubleSide, envMap: envTex, envMapIntensity: 1.3 }),
    tint: std({ color: 0x4f7186, metalness: 0.7, roughness: 0.1, envMap: envTex, envMapIntensity: 0.9 }),
    oak: std({ color: 0xc9a77c, roughness: 0.55 }),
    walnut: std({ color: 0x5b3a25, roughness: 0.42 }),
    leather: std({ color: 0xf1ede6, roughness: 0.55 }),
    frame: std({ color: 0x1b2533, roughness: 0.35, metalness: 0.4 }),
    navy: std({ color: 0x17283a, roughness: 0.35, metalness: 0.3, envMap: envTex }),
    white: std({ color: 0xffffff, roughness: 0.6 }),
    leaf: std({ color: 0x55743d, roughness: 0.85, flatShading: true }),
    hedge: std({ color: 0x4d6b38, roughness: 0.9, flatShading: true }),
    lawn: std({ map: lawnTex, roughness: 0.95 }),
    vcol: std({ vertexColors: true, roughness: 0.6 }),
    paint: std({ vertexColors: true, roughness: 0.25, metalness: 0.45, envMap: envTex, envMapIntensity: 1.2 }),
    warmLight: std({ color: 0xfff1d8, emissive: 0xffc98a, emissiveIntensity: 2.6 }),
    screenGlow: std({ color: 0xe6eef8, emissive: 0xcfe1ff, emissiveIntensity: 0.9, roughness: 0.3 }),
    interior: std({ color: 0xf2e2c8, emissive: 0xe8b878, emissiveIntensity: 0.55, roughness: 0.8 }),
    lampGlass: std({ color: 0xfff6e6, emissive: 0xffd8a0, emissiveIntensity: 2 }),
  };

  glowMats.push([M.warmLight, 1.1], [M.interior, 0.7], [M.lampGlass, 1.3], [M.screenGlow, 0.9]);

  // Merge simple parts into one geometry with vertex colours (cars, yachts, palms are drawn as one batch each).
  function merge(parts) {
    const pos = [], nor = [], col = [];
    for (const { geo, color, m } of parts) {
      const g = geo.index ? geo.toNonIndexed() : geo.clone();
      if (m) g.applyMatrix4(m);
      g.computeVertexNormals();
      const c = C(color), p = g.attributes.position, n = g.attributes.normal;
      for (let i = 0; i < p.count; i++) { pos.push(p.getX(i), p.getY(i), p.getZ(i)); nor.push(n.getX(i), n.getY(i), n.getZ(i)); col.push(c.r, c.g, c.b); }
    }
    const out = new THREE.BufferGeometry();
    out.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    out.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
    out.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    return out;
  }
  // Like merge(), but keeps each part's UVs (textured facades).
  function mergeUV(parts) {
    const pos = [], nor = [], col = [], uvs = [];
    for (const { geo, color, m } of parts) {
      const g = geo.index ? geo.toNonIndexed() : geo.clone();
      if (m) g.applyMatrix4(m);
      g.computeVertexNormals();
      const c = C(color), pp = g.attributes.position, n = g.attributes.normal, uv = g.attributes.uv;
      for (let i = 0; i < pp.count; i++) { pos.push(pp.getX(i), pp.getY(i), pp.getZ(i)); nor.push(n.getX(i), n.getY(i), n.getZ(i)); col.push(c.r, c.g, c.b); uvs.push(uv.getX(i), uv.getY(i)); }
    }
    const out = new THREE.BufferGeometry();
    out.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    out.setAttribute("normal", new THREE.Float32BufferAttribute(nor, 3));
    out.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    out.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
    return out;
  }
  const at = (x, y, z, ry = 0, sx = 1, sy = 1, sz = 1) => new THREE.Matrix4().compose(new V3(x, y, z),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(0, ry, 0)), new V3(sx, sy, sz));
  const box = (w, h, d, mat, x = 0, y = 0, z = 0, parent = scene3, shadow = true) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z);
    if (shadow && !simple) { m.castShadow = true; m.receiveShadow = true; }
    parent.add(m); return m;
  };

  // Scenery props (furniture, planting, visitors) placed in a pavilion's frame or the estate's, drawn as one
  // instanced mesh per kind once everything is built. None of them is an agent or an agent's desk.
  const PROPS = {};
  const propKind = (kind, geo, mat) => { PROPS[kind] = { geo, mat, list: [] }; };
  const prop = (kind, parent, x, y, z, ry = 0, sc = 1, color = null) => PROPS[kind].list.push([parent, x, y, z, ry, sc, color]);
  function buildProps() {
    const m = new THREE.Matrix4();
    for (const k of Object.values(PROPS)) {
      if (!k.list.length || k.mesh) continue;
      const im = new THREE.InstancedMesh(k.geo, k.mat, k.list.length);
      k.list.forEach(([parent, x, y, z, ry, sc, color], i) => {
        parent.updateWorldMatrix(true, false);
        m.copy(at(x, y, z, ry, sc, sc, sc)).premultiply(parent.matrixWorld); im.setMatrixAt(i, m);
        if (color != null) im.setColorAt(i, C(color));
      });
      im.castShadow = !simple && k.shadow !== false; k.mesh = im; scene3.add(im);
    }
  }
  propKind("workstation", merge([
    { geo: new THREE.BoxGeometry(2.2, 0.08, 1.0), color: 0x5b3a25, m: at(0, 0.76, 0) },
    { geo: new THREE.BoxGeometry(0.06, 0.72, 0.9), color: 0xc6a46a, m: at(-1.0, 0.38, 0) },
    { geo: new THREE.BoxGeometry(0.06, 0.72, 0.9), color: 0xc6a46a, m: at(1.0, 0.38, 0) },
    { geo: new THREE.BoxGeometry(1.3, 0.8, 0.05), color: 0x1b2533, m: at(0, 1.3, -0.3) },
    { geo: new THREE.BoxGeometry(0.7, 0.12, 0.65), color: 0xf1ede6, m: at(0, 0.5, 0.85) },
    { geo: new THREE.BoxGeometry(0.7, 0.75, 0.1), color: 0xf1ede6, m: at(0, 0.95, 1.2) },
    { geo: new THREE.CylinderGeometry(0.04, 0.25, 0.45, 10), color: 0xc6a46a, m: at(0, 0.22, 0.85) },
  ]), M.vcol);
  propKind("screen", new THREE.PlaneGeometry(1.22, 0.72).translate(0, 1.3, -0.27), M.screenGlow);
  PROPS.screen.shadow = false;
  propKind("flower", new THREE.IcosahedronGeometry(0.5, 0), std({ roughness: 0.9, flatShading: true }));
  propKind("cypress", new THREE.ConeGeometry(0.9, 7.5, 7).translate(0, 3.75, 0), std({ color: 0x2f4d2a, roughness: 0.95, flatShading: true }));
  propKind("oliveTrunk", new THREE.CylinderGeometry(0.16, 0.26, 2.6, 7).translate(0, 1.3, 0), std({ color: 0x6b5340, roughness: 0.9 }));
  propKind("oliveCanopy", new THREE.IcosahedronGeometry(2.1, 1).translate(0, 3.6, 0), std({ color: 0x7c9a66, roughness: 0.9, flatShading: true }));
  propKind("bench", merge([
    { geo: new THREE.BoxGeometry(1.9, 0.08, 0.55), color: 0x5b3a25, m: at(0, 0.48, 0) },
    { geo: new THREE.BoxGeometry(1.9, 0.4, 0.06), color: 0x5b3a25, m: at(0, 0.75, -0.26) },
    { geo: new THREE.BoxGeometry(0.06, 0.48, 0.5), color: 0xc6a46a, m: at(-0.85, 0.24, 0) },
    { geo: new THREE.BoxGeometry(0.06, 0.48, 0.5), color: 0xc6a46a, m: at(0.85, 0.24, 0) },
  ]), M.vcol);
  propKind("bollard", new THREE.CylinderGeometry(0.09, 0.11, 0.9, 8).translate(0, 0.45, 0), M.frame);
  propKind("bollardLamp", new THREE.CylinderGeometry(0.11, 0.11, 0.16, 8).translate(0, 0.98, 0), M.lampGlass);
  PROPS.bollardLamp.shadow = false;
  // Visitors are the same build as the agents, standing easy, drawn as four instanced parts for all of them: the suit (one
  // colour per person), shoes and hands, the head (a skin tone each) and hair.
  const fig = (parts) => merge(parts.map(([geo, x, y, z, color]) => ({ geo, color, m: at(x, y, z) })));
  propKind("visitorBody", fig([
    [new THREE.CylinderGeometry(0.1, 0.08, 0.44, 7), -0.11, 0.69, 0.02, 0xffffff], [new THREE.CylinderGeometry(0.1, 0.08, 0.44, 7), 0.11, 0.69, -0.02, 0xffffff],
    [new THREE.CylinderGeometry(0.075, 0.062, 0.42, 7), -0.11, 0.26, 0.03, 0xffffff], [new THREE.CylinderGeometry(0.075, 0.062, 0.42, 7), 0.11, 0.26, -0.03, 0xffffff],
    [new THREE.BoxGeometry(0.36, 0.17, 0.22), 0, 0.95, 0, 0xffffff], [new THREE.CylinderGeometry(0.25, 0.19, 0.56, 8), 0, 1.31, 0, 0xffffff],
    [new THREE.CylinderGeometry(0.068, 0.058, 0.3, 7), -0.3, 1.38, 0, 0xffffff], [new THREE.CylinderGeometry(0.068, 0.058, 0.3, 7), 0.3, 1.38, 0, 0xffffff],
    [new THREE.CylinderGeometry(0.056, 0.048, 0.27, 7), -0.31, 1.1, 0.04, 0xffffff], [new THREE.CylinderGeometry(0.056, 0.048, 0.27, 7), 0.31, 1.1, 0.04, 0xffffff],
  ]), std({ color: 0xffffff, roughness: 0.65, flatShading: true }));
  propKind("visitorTrim", fig([
    [new THREE.BoxGeometry(0.15, 0.08, 0.28), -0.11, 0.04, 0.09, 0x2a1c14], [new THREE.BoxGeometry(0.15, 0.08, 0.28), 0.11, 0.04, 0.03, 0x2a1c14],
    [new THREE.IcosahedronGeometry(0.058, 0), -0.31, 0.94, 0.06, 0xd9a07a], [new THREE.IcosahedronGeometry(0.058, 0), 0.31, 0.94, 0.06, 0xd9a07a],
    [new THREE.BoxGeometry(0.12, 0.26, 0.05), 0, 1.45, 0.2, 0xffffff],
  ]), M.vcol);
  propKind("visitorHead", fig([[new THREE.CylinderGeometry(0.06, 0.07, 0.14, 8), 0, 1.65, 0, 0xffffff], [new THREE.IcosahedronGeometry(0.19, 1), 0, 1.87, 0, 0xffffff]]), std({ color: 0xffffff, roughness: 0.7, flatShading: true }));
  propKind("visitorHair", new THREE.SphereGeometry(0.205, 12, 8, 0, Math.PI * 2, 0, 1.35).translate(0, 1.9, -0.02), std({ color: 0xffffff, roughness: 0.8, flatShading: true }));
  const SHIRTS = [0x1f2a44, 0xe9d8c0, 0xd4202f, 0x2f3338, 0xf1c40f, 0x3a7bd5, 0xf2f2f2, 0x2f8f83, 0xc8b79a, 0x6b3fa0];
  const VSKIN = [0xf1c7a5, 0xe0ac83, 0xc68a62, 0x8d5a3b, 0xf5d6bf, 0xd9a07a], VHAIR = [0x2b1d14, 0x4a3022, 0x7a5a3a, 0x1b1b1b, 0xb8925c];
  const visitor = (parent, x, y, z, ry) => {
    const sc = 1.15 + rand() * 0.15;
    prop("visitorBody", parent, x, y, z, ry, sc, SHIRTS[(rand() * SHIRTS.length) | 0]); prop("visitorTrim", parent, x, y, z, ry, sc);
    prop("visitorHead", parent, x, y, z, ry, sc, VSKIN[(rand() * VSKIN.length) | 0]); prop("visitorHair", parent, x, y, z, ry, sc, VHAIR[(rand() * VHAIR.length) | 0]);
  };
  const FLOWERS = [0xe24b6a, 0xf7f7f7, 0xd94c9c, 0xf29e3b, 0x7b4fbf, 0xffffff, 0xff6b6b];
  const flower = (parent, x, y, z) => prop("flower", parent, x, y, z, rand() * 6.3, 0.55 + rand() * 0.55, FLOWERS[(rand() * FLOWERS.length) | 0]);
  const workstation = (parent, x, y, z, ry, sc = 1) => { prop("workstation", parent, x, y, z, ry, sc); prop("screen", parent, x, y, z, ry, sc); };
  const olive = (parent, x, y, z, sc = 1) => { prop("oliveTrunk", parent, x, y, z, 0, sc); prop("oliveCanopy", parent, x, y, z, rand() * 6.3, sc); };

  // ---------------------------------------------------------------- light and sky, by the time of day
  const hemi = new THREE.HemisphereLight(0xcfe2f4, 0xcdb08a, 1.25);
  scene3.add(hemi);
  const sun = new THREE.DirectionalLight(0xffd29a, 3.4);
  sun.position.set(-150, 95, -40);           // in the west over the sea: long warm shadows towards the viewer
  sun.castShadow = !simple;
  sun.shadow.mapSize.set(4096, 4096);
  Object.assign(sun.shadow.camera, { left: -120, right: 120, top: 110, bottom: -110, near: 20, far: 420 });
  sun.shadow.bias = -0.0003; sun.shadow.normalBias = 0.05;
  scene3.add(sun, sun.target);
  const sky = new THREE.Mesh(new THREE.SphereGeometry(3200, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: C(0x6fa8dc) }, mid: { value: C(0xbcd8ee) }, low: { value: C(0xf6dcc0) }, sunDir: { value: sun.position.clone().normalize() }, sunGlow: { value: 0.35 } },
    vertexShader: "varying vec3 vp; void main(){ vp = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
    fragmentShader: "uniform vec3 top; uniform vec3 mid; uniform vec3 low; uniform vec3 sunDir; uniform float sunGlow; varying vec3 vp; void main(){ float h = vp.y; vec3 c = mix(low, mid, smoothstep(0.0, 0.16, h)); c = mix(c, top, smoothstep(0.16, 0.65, h)); float s = max(dot(vp, sunDir), 0.0); c += vec3(1.0, 0.72, 0.42) * (pow(s, 40.0) + pow(s, 6.0) * 0.25) * sunGlow; gl_FragColor = vec4(c, 1.0); }",
  }));
  scene3.add(sky);
  // Day, sunset and evening. "live" follows this computer's clock: day until late afternoon, golden hour, then the
  // estate lights up for the evening. Everything that glows (windows, lamps, the beam) is scaled by `glow`.
  const TIMES = {
    day: { sun: [0xfff2df, 3.3, -150, 150, -40], hemi: [0xd3e6f6, 0xcdb48e, 1.3], sky: [0x5c9ede, 0xaed2ef, 0xe4eef2, 0.25],
      fog: [0xe2ebef, 460, 1800], exposure: 0.9, glow: 0.4, bloom: 0.3 },
    sunset: { sun: [0xffc27a, 3.0, -160, 70, -30], hemi: [0xd8d3e4, 0xcfa57a, 1.05], sky: [0x6c9ad2, 0xf1caa6, 0xffb27a, 0.6],
      fog: [0xf0d2b4, 420, 1700], exposure: 0.9, glow: 1, bloom: 0.45 },
    evening: { sun: [0xff8f5a, 0.9, -160, 22, -10], hemi: [0x40507e, 0x5c4330, 0.7], sky: [0x18234d, 0x6c5b8c, 0xf2996a, 1.0],
      fog: [0x6e5f7c, 300, 1500], exposure: 1.0, glow: 1.5, bloom: 0.55 },
  };
  let timeMode = "live";
  function timeBlend() {
    if (timeMode !== "live") return [TIMES[timeMode], TIMES[timeMode], 0];
    const d = new Date(), h = d.getHours() + d.getMinutes() / 60;
    const seg = (a, b, from, to) => [TIMES[a], TIMES[b], Math.min(1, Math.max(0, (h - from) / (to - from)))];
    if (h >= 6.5 && h < 16.5) return [TIMES.day, TIMES.day, 0];
    if (h >= 16.5 && h < 18.5) return seg("day", "sunset", 16.5, 18.5);
    if (h >= 18.5 && h < 19.75) return seg("sunset", "evening", 18.5, 19.75);
    if (h >= 5.25 && h < 6) return seg("evening", "sunset", 5.25, 6);
    if (h >= 6 && h < 6.5) return seg("sunset", "day", 6, 6.5);
    return [TIMES.evening, TIMES.evening, 0];
  }
  const mixC = (a, b, t) => C(a).lerp(C(b), t);
  let bloomPass = null;
  function applyTime() {
    const [a, b, t] = timeBlend(), L = (x, y) => x + (y - x) * t;
    sun.color.copy(mixC(a.sun[0], b.sun[0], t)); sun.intensity = L(a.sun[1], b.sun[1]);
    sun.position.set(L(a.sun[2], b.sun[2]), L(a.sun[3], b.sun[3]), L(a.sun[4], b.sun[4]));
    hemi.color.copy(mixC(a.hemi[0], b.hemi[0], t)); hemi.groundColor.copy(mixC(a.hemi[1], b.hemi[1], t)); hemi.intensity = L(a.hemi[2], b.hemi[2]);
    const u = sky.material.uniforms;
    u.top.value.copy(mixC(a.sky[0], b.sky[0], t)); u.mid.value.copy(mixC(a.sky[1], b.sky[1], t)); u.low.value.copy(mixC(a.sky[2], b.sky[2], t));
    u.sunGlow.value = L(a.sky[3], b.sky[3]); u.sunDir.value.copy(sun.position).normalize();
    if (world.water) { world.water.material.uniforms.sunDirection.value.copy(sun.position).normalize(); world.water.material.uniforms.sunColor.value.copy(sun.color); }
    scene3.fog.color.copy(mixC(a.fog[0], b.fog[0], t)); scene3.fog.near = L(a.fog[1], b.fog[1]); scene3.fog.far = L(a.fog[2], b.fog[2]);
    renderer.toneMappingExposure = L(a.exposure, b.exposure);
    const glow = L(a.glow, b.glow);
    glowMats.forEach(([m, base]) => { m.emissiveIntensity = base * glow; });
    if (bloomPass) bloomPass.strength = L(a.bloom, b.bloom);
    world.glow = glow;
  }

  // ---------------------------------------------------------------- the harbour, the waterfront circuit and the city
  const SEA = -26;
  const bayNorth = (x) => -360 + 40 * Math.sin(x / 140);
  const onShelf = (x, z) => x < 6 && z < -150 && z > -238;
  function ground(x, z) {
    const n = bayNorth(x);
    if (z < n) return SEA + 1 + Math.min(62, (n - z) * 0.13) * (0.82 + 0.18 * Math.sin(x / 47 + z / 63));
    if (onShelf(x, z)) return SEA + 0.8;
    if (z < -60) return SEA - 8;
    const edge = Math.max(Math.abs(x) - 98, z - 84, 0);
    return -2 - edge * 0.3 + Math.sin(x / 23) * Math.cos(z / 31) * 2.5;
  }
  {
    const terrain = new THREE.PlaneGeometry(2400, 1800, simple ? 120 : 220, simple ? 90 : 165);
    terrain.rotateX(-Math.PI / 2); terrain.translate(0, 0, -300);
    const p = terrain.attributes.position, cols = [];
    const green = C(0x7a9455), olive = C(0x8d9468), rock = C(0xc4b49a), sand = C(0xdccfb4);
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), z = p.getZ(i), y = ground(x, z);
      p.setY(i, y);
      const c = onShelf(x, z) ? sand : y > SEA + 52 ? rock.clone().lerp(olive, 0.3) : z < bayNorth(x) ? olive.clone().lerp(green, 0.5 + 0.5 * Math.sin(x / 30)) : green;
      cols.push(c.r, c.g, c.b);
    }
    terrain.setAttribute("color", new THREE.Float32BufferAttribute(cols, 3));
    terrain.computeVertexNormals();
    const land = new THREE.Mesh(terrain, std({ vertexColors: true, roughness: 0.95, flatShading: true }));
    land.receiveShadow = !simple;
    scene3.add(land);

    // Sea: Mediterranean blue, turquoise in the harbour, moving ripples.
    const seaGeo = new THREE.PlaneGeometry(4000, 3000, 60, 45); seaGeo.rotateX(-Math.PI / 2); seaGeo.translate(0, 0, -400);
    const sp = seaGeo.attributes.position, sc = [];
    const shallow = C(0x3fb2c4), deep = C(0x1b67a8);
    for (let i = 0; i < sp.count; i++) { const z = sp.getZ(i), x = sp.getX(i); const k = Math.min(1, Math.max(0, (-z - 60) / 260 + Math.abs(x) / 1400)); const c = shallow.clone().lerp(deep, k); sc.push(c.r, c.g, c.b); }
    seaGeo.setAttribute("color", new THREE.Float32BufferAttribute(sc, 3));
    if (addons.Water && !simple) {
      // Reflecting water (three.js Water): the city, yachts and sky mirror in the bay. Its reflection is a second render of the
      // scene, so it is drawn at a small size and refreshed every other frame to keep the frame rate.
      const normals = new THREE.TextureLoader().load("https://cdn.jsdelivr.net/npm/three@0.160.0/examples/textures/waternormals.jpg", (t) => { t.wrapS = t.wrapT = THREE.RepeatWrapping; });
      const water = new addons.Water(new THREE.PlaneGeometry(4000, 3000), { textureWidth: 384, textureHeight: 384, waterNormals: normals, sunDirection: new V3(0, 1, 0),
        sunColor: 0xffffff, waterColor: 0x1f6f9f, distortionScale: 2.2, fog: true });
      water.rotation.x = -Math.PI / 2; water.position.set(0, SEA, -400);
      const draw = water.onBeforeRender.bind(water); let tick = 0;
      water.onBeforeRender = (...a) => { if ((tick++ & 1) === 0) draw(...a); };
      scene3.add(water); world.water = water;
    }
    const sea = new THREE.Mesh(seaGeo, std({ vertexColors: true, roughness: 0.15, metalness: 0.1, normalMap: rippleTex, normalScale: new THREE.Vector2(0.35, 0.35), envMap: envTex, envMapIntensity: 0.5 }));
    sea.position.y = world.water ? SEA - 0.6 : SEA; scene3.add(sea);   // under the reflecting water it only tints the shallows

    // Mountains far behind the city, softened by the haze.
    const peaks = new THREE.InstancedMesh(new THREE.ConeGeometry(1, 1, 7), std({ color: 0x8f9a7e, roughness: 1, flatShading: true }), 9);
    [[-700, -1250, 260, 170], [-380, -1180, 210, 150], [-60, -1300, 300, 200], [260, -1200, 240, 165], [560, -1280, 280, 180],
      [880, -1150, 220, 140], [-980, -1100, 230, 130], [120, -1060, 160, 110], [-520, -1020, 150, 100]].forEach(([x, z, r, h], i) => peaks.setMatrixAt(i, at(x, SEA + h * 0.25, z, i, r * 1.4, h * 0.5, r)));  // a low hazy ridge on the horizon
    scene3.add(peaks);

    // The city climbing the hills across the bay: towers along the shore, villas higher up.
    const tones = [0xf7f3ec, 0xf1e6d2, 0xfbf8f2, 0xead2b4, 0xffffff, 0xf4ece0, 0xf0dcc6, 0xffffff];
    const blocks = [];
    seed = 31;
    for (let t = 0; t < 4000 && blocks.length < (simple ? 260 : 620); t++) {
      const x = (rand() - 0.5) * 1400, n = bayNorth(x), z = n - 8 - rand() * 380;
      const y = ground(x, z), shore = Math.max(0, 1 - (n - z) / 140);
      const w = 8 + rand() * 12, d = 8 + rand() * 12, h = 8 + rand() * (10 + 50 * shore * shore);
      blocks.push([x, y, z, w, h, d, tones[(rand() * tones.length) | 0], rand() * 0.6]);
    }
    // Facades: a window grid (8 windows per tile) with a random share lit, repeated across each building by its size.
    const facadeTex = canvasTex(512, 512, (g, w, h) => {
      g.fillStyle = "#ece6da"; g.fillRect(0, 0, w, h);
      for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) {
        const x = c * 64 + 10, y = r * 64 + 8;
        const gr = g.createLinearGradient(0, y, 0, y + 46); gr.addColorStop(0, "#9db6c9"); gr.addColorStop(1, "#5e7a92");
        g.fillStyle = gr; g.fillRect(x, y, 44, 46);
        g.fillStyle = "rgba(255,255,255,.18)"; g.fillRect(x, y, 44, 6);
      }
      g.fillStyle = "#ece6da"; g.fillRect(0, h - 10, 10, 10);   // a plain texel for roofs
    }, 1);
    seed = 77;
    const litTex = canvasTex(512, 512, (g, w, h) => {
      g.fillStyle = "#000"; g.fillRect(0, 0, w, h);
      for (let r = 0; r < 8; r++) for (let c = 0; c < 8; c++) if (rand() < 0.42) { g.fillStyle = rand() < 0.5 ? "#ffd58e" : "#ffe9c0"; g.fillRect(c * 64 + 10, r * 64 + 8, 44, 46); }
    }, 1);
    const facadeMat = std({ map: facadeTex, vertexColors: true, roughness: 0.55, metalness: 0.15, emissive: 0xffffff, emissiveMap: litTex, emissiveIntensity: 0 });
    glowMats.push([facadeMat, 0.9]);
    const cityParts = [], roofParts = [];
    blocks.forEach(([x, y, z, w, h, d, c, ry]) => {
      const g = new THREE.BoxGeometry(w, h, d), uv = g.attributes.uv, cell = 3.2, ox = rand() * 4, oy = rand() * 4;
      for (let i = 0; i < uv.count; i++) {
        const face = Math.floor(i / 4);   // px nx py ny pz nz
        if (face === 2 || face === 3) uv.setXY(i, 0.008, 0.008);
        else { const across = face < 2 ? d : w; uv.setXY(i, uv.getX(i) * across / cell + ox, uv.getY(i) * h / cell + oy); }
      }
      cityParts.push({ geo: g, color: c, m: at(x, y + h / 2 - 1, z, ry) });
      // Roofs: a parapet and plant room, or a terracotta hip on the smaller villas.
      if (h < 16) roofParts.push({ geo: new THREE.ConeGeometry(Math.max(w, d) * 0.72, 3, 4).rotateY(Math.PI / 4), color: 0xb8613f, m: at(x, y + h + 0.4, z, ry, 1, 1, d / Math.max(w, d)) });
      else { roofParts.push({ geo: new THREE.BoxGeometry(w + 0.4, 0.8, d + 0.4), color: 0xf3f0ea, m: at(x, y + h - 0.6, z, ry) }); roofParts.push({ geo: new THREE.BoxGeometry(w * 0.35, 2, d * 0.35), color: 0xe4ded2, m: at(x, y + h, z, ry) }); }
    });
    scene3.add(new THREE.Mesh(mergeUV(cityParts), facadeMat), new THREE.Mesh(merge(roofParts), M.vcol));

    // The waterfront circuit: a closed street loop on the shelf, kerbs, grandstand and banner.
    const ctrl = [[-12, -160], [-80, -157], [-150, -162], [-200, -180], [-196, -208], [-150, -224], [-80, -222], [-30, -212], [-8, -188]];
    const curve = new THREE.CatmullRomCurve3(ctrl.map(([x, z]) => new V3(x, SEA + 0.9, z)), true, "centripetal");
    world.curve = curve;
    const N = 500, pts = curve.getSpacedPoints(N);
    const ribbon = (inner, outer, y) => {
      const pos = [], uv = [], idx = [];
      for (let i = 0; i <= N; i++) {
        const p0 = pts[i % N], p1 = pts[(i + 1) % N], t = p1.clone().sub(p0).normalize(), nrm = new V3(-t.z, 0, t.x);
        pos.push(p0.x + nrm.x * inner, y, p0.z + nrm.z * inner, p0.x + nrm.x * outer, y, p0.z + nrm.z * outer);
        uv.push(i / N, 0, i / N, 1);
        if (i < N) idx.push(i * 2, i * 2 + 1, i * 2 + 2, i * 2 + 1, i * 2 + 3, i * 2 + 2);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
      g.setIndex(idx); g.computeVertexNormals(); return g;
    };
    const kerbTex = canvasTex(64, 8, (g) => { g.fillStyle = "#ffffff"; g.fillRect(0, 0, 64, 8); g.fillStyle = "#d4202f"; g.fillRect(0, 0, 32, 8); });
    kerbTex.wrapS = THREE.RepeatWrapping; kerbTex.repeat.set(300, 1);
    const kerb = std({ map: kerbTex, roughness: 0.7, side: THREE.DoubleSide });
    scene3.add(new THREE.Mesh(ribbon(-5, 5, SEA + 0.95), std({ color: 0x45494f, roughness: 0.85, side: THREE.DoubleSide })),
      new THREE.Mesh(ribbon(5, 6.4, SEA + 1.0), kerb), new THREE.Mesh(ribbon(-6.4, -5, SEA + 1.0), kerb));
    // Grandstand facing the track and the estate, crowd in the seats, the banner along the top.
    const crowd = canvasTex(512, 128, (g, w, h) => { g.fillStyle = "#c8c2b8"; g.fillRect(0, 0, w, h); for (let i = 0; i < 1600; i++) { g.fillStyle = ["#d4202f", "#ffffff", "#1d3557", "#f1c40f", "#2a2a2a"][i % 5]; g.fillRect(rand() * w, rand() * h, 3, 4); } });
    crowd.wrapS = THREE.RepeatWrapping; crowd.repeat.set(3, 1);
    const crowdMat = std({ map: crowd, roughness: 0.9 });
    const stand = new THREE.Group(); stand.position.set(-110, SEA + 0.9, -236);
    for (let r = 0; r < 6; r++) box(110, 2, 3, crowdMat, 0, 1 + r * 2, -r * 3, stand);
    box(114, 0.6, 22, M.white, 0, 15, -7, stand);
    const banner = canvasTex(1024, 128, (g, w) => { g.fillStyle = "#d4202f"; g.fillRect(0, 0, w, 128); g.fillStyle = "#fff"; g.font = "italic 900 92px 'Instrument Sans', sans-serif"; g.textAlign = "center"; g.fillText("MONACO", w / 2, 100); });
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(60, 7.5), std({ map: banner, roughness: 0.6 }));
    sign.position.set(0, 19, 4); stand.add(sign);
    scene3.add(stand);
    // Armco barriers both sides of the track, and bunting along the fence.
    const armco = [], flags = [], flagCols = [0xd4202f, 0xffffff, 0x1d3557, 0xf1c40f];
    for (let i = 0; i < N; i += 6) {
      const p = pts[i], t2 = pts[(i + 3) % N].clone().sub(p).normalize(), ang = Math.atan2(-t2.z, t2.x);
      for (const off of [-7, 7]) armco.push({ geo: new THREE.BoxGeometry(5.5, 0.9, 0.25), color: 0xbfc4ca, m: at(p.x - t2.z * off, SEA + 1.4, p.z + t2.x * off, ang) });
      flags.push({ geo: new THREE.BoxGeometry(1.2, 0.8, 0.04), color: flagCols[(i / 6) % 4], m: at(p.x + t2.z * 7.6, SEA + 3.2, p.z - t2.x * 7.6, ang) });
      flags.push({ geo: new THREE.BoxGeometry(0.06, 2.6, 0.06), color: 0xdddddd, m: at(p.x + t2.z * 7.6, SEA + 2.2, p.z - t2.x * 7.6, ang) });
    }
    scene3.add(new THREE.Mesh(merge(armco), std({ vertexColors: true, roughness: 0.4, metalness: 0.6 })), new THREE.Mesh(merge(flags), std({ vertexColors: true, roughness: 0.8, side: THREE.DoubleSide })));
    // Spectators along the circuit fence and the marina promenade (scenery, not agents).
    if (!simple) {
      const people = new THREE.InstancedMesh(new THREE.CapsuleGeometry(0.32, 0.9, 3, 6), std({ roughness: 0.7 }), 420);
      const shirts = [0xffffff, 0xd4202f, 0x1d3557, 0xf1c40f, 0x2a2a2a, 0xe9d8c0, 0x3a7bd5];
      let n = 0;
      for (let i = 0; i < 260; i++) { const p = pts[(i * 2) % N], t2 = pts[(i * 2 + 1) % N].clone().sub(p).normalize(); const off = 8.5 + rand() * 2; people.setMatrixAt(n, at(p.x - t2.z * off, SEA + 1.6, p.z + t2.x * off)); people.setColorAt(n++, C(shirts[i % shirts.length])); }
      for (let i = 0; i < 160; i++) { people.setMatrixAt(n, at(20 + rand() * 120, SEA + 1.4, -74 + rand() * 4)); people.setColorAt(n++, C(shirts[(i * 3) % shirts.length])); }
      scene3.add(people);
    }

    // Grand Prix cars racing round the circuit: braking for corners, queueing behind slower cars.
    const wheel = new THREE.CylinderGeometry(0.34, 0.34, 0.38, 14); wheel.rotateX(Math.PI / 2);
    const DARK = 0x16191e;
    const carGeo = merge([
      { geo: new THREE.BoxGeometry(3.2, 0.36, 0.78), color: 0xffffff, m: at(-0.2, 0.42, 0) },
      { geo: new THREE.BoxGeometry(1.5, 0.22, 0.42), color: 0xffffff, m: at(2.0, 0.34, 0) },
      { geo: new THREE.BoxGeometry(0.45, 0.06, 2.0), color: DARK, m: at(2.75, 0.18, 0) },
      { geo: new THREE.BoxGeometry(1.4, 0.3, 0.42), color: 0xffffff, m: at(-0.3, 0.44, 0.58) },
      { geo: new THREE.BoxGeometry(1.4, 0.3, 0.42), color: 0xffffff, m: at(-0.3, 0.44, -0.58) },
      { geo: new THREE.BoxGeometry(1.3, 0.34, 0.3), color: 0xffffff, m: at(-0.9, 0.75, 0) },
      { geo: new THREE.BoxGeometry(0.42, 0.3, 0.32), color: DARK, m: at(0.45, 0.72, 0) },
      { geo: new THREE.SphereGeometry(0.17, 10, 8), color: 0xf4f4f4, m: at(0.32, 0.88, 0) },
      { geo: new THREE.BoxGeometry(0.42, 0.08, 1.5), color: DARK, m: at(-2.05, 1.05, 0) },
      { geo: new THREE.BoxGeometry(0.12, 0.55, 0.12), color: DARK, m: at(-2.0, 0.75, 0) },
      ...[[1.75, 0.9], [1.75, -0.9], [-1.6, 0.9], [-1.6, -0.9]].map(([x, z]) => ({ geo: wheel, color: 0x101010, m: at(x, 0.34, z) })),
    ]);
    carGeo.scale(1.6, 1.6, 1.6);
    const liveries = [0xd0101b, 0x9aa3ab, 0x1b2a55, 0xff8000, 0x0d5c46, 0x1f5fd1, 0xf2f2f2, 0xf1c40f, 0xd0101b, 0x0aa5a0];
    const grid = simple ? liveries.slice(0, 5) : liveries;   // a lighter race on phones
    const cars = new THREE.InstancedMesh(carGeo, M.paint, grid.length);
    grid.forEach((c, i) => cars.setColorAt(i, C(c)));
    scene3.add(cars);
    const len = curve.getLength();
    const bend = pts.map((p, i) => { const a = pts[(i + N - 5) % N], b = pts[(i + 5) % N]; return a.clone().sub(p).normalize().dot(b.clone().sub(p).normalize()) + 1; });
    world.cars = { mesh: cars, len, bend, list: grid.map((_, i) => ({ u: 1 - i * 0.035, v: 30, top: 58 + ((i * 7) % 13), lane: 0, want: 0, passing: null })) };

    // The marina: jetties with moored yachts below the estate, superyachts at anchor in the bay.
    const hullShape = new THREE.Shape([[-0.5, -0.17], [0.22, -0.17], [0.5, 0], [0.22, 0.17], [-0.5, 0.17]].map(([x, y]) => new THREE.Vector2(x, y)));
    const hull = new THREE.ExtrudeGeometry(hullShape, { depth: 0.1, bevelEnabled: false }); hull.rotateX(-Math.PI / 2);
    const deck1 = new THREE.ExtrudeGeometry(new THREE.Shape([[-0.42, -0.13], [0.12, -0.13], [0.26, 0], [0.12, 0.13], [-0.42, 0.13]].map(([x, y]) => new THREE.Vector2(x, y))), { depth: 0.06, bevelEnabled: false });
    deck1.rotateX(-Math.PI / 2); deck1.translate(0, 0.1, 0);
    const band = new THREE.BoxGeometry(0.5, 0.03, 0.27); band.translate(-0.12, 0.135, 0);
    const deck2 = new THREE.BoxGeometry(0.34, 0.05, 0.2); deck2.translate(-0.16, 0.175, 0);
    const deckShape = (len, wid, bow) => new THREE.Shape([[-len / 2, -wid], [len / 2 - bow, -wid], [len / 2, 0], [len / 2 - bow, wid], [-len / 2, wid]].map(([x, y]) => new THREE.Vector2(x, y)));
    const tier = (len, wid, bow, h, y, x) => { const g = new THREE.ExtrudeGeometry(deckShape(len, wid, bow), { depth: h, bevelEnabled: false }); g.rotateX(-Math.PI / 2); g.translate(x, y, 0); return g; };
    const yachtGeo = merge([
      { geo: hull, color: 0xffffff }, { geo: deck1, color: 0xf7f7f7 },
      { geo: tier(0.52, 0.135, 0.12, 0.028, 0.115, -0.1), color: 0x1c2a3d },     // window band, main deck
      { geo: tier(0.4, 0.11, 0.1, 0.05, 0.16, -0.13), color: 0xffffff },
      { geo: tier(0.41, 0.112, 0.1, 0.022, 0.175, -0.13), color: 0x1c2a3d },     // window band, upper deck
      { geo: tier(0.24, 0.085, 0.07, 0.04, 0.21, -0.17), color: 0xffffff },      // sun deck
      { geo: new THREE.BoxGeometry(0.03, 0.06, 0.15), color: 0xd8d8d8, m: at(-0.16, 0.27, 0) },   // radar arch
      { geo: new THREE.BoxGeometry(0.5, 0.012, 0.3), color: 0x8a6a4a, m: at(-0.05, 0.106, 0) },   // teak deck
    ]);
    void band; void deck2;
    const yachts = [];
    seed = 23;
    const jetties = [{ geo: new THREE.BoxGeometry(300, 1.4, 7), color: 0xefe9de, m: at(40, SEA + 0.4, -72) }];   // the promenade
    for (const x of [8, 36, 64, 92, 120, 148]) {
      jetties.push({ geo: new THREE.BoxGeometry(3, 1, 84), color: 0xefe9de, m: at(x, SEA + 0.2, -118) });
      for (let z = -82; z > -156; z -= 6) for (const side of [-1, 1]) if (rand() > 0.12) {
        const L = 10 + rand() * 9; yachts.push([x + side * (L / 2 + 2.2), z, side > 0 ? 0 : Math.PI, L, [0xffffff, 0xffffff, 0xf4f4f4, 0x1f2c44][(rand() * 4) | 0]]);
      }
      for (let z = -84; z > -156; z -= 24) { prop("bollard", scene3, x, SEA + 0.7, z, 0, 1.6); prop("bollardLamp", scene3, x, SEA + 0.7, z, 0, 1.6); }
    }
    scene3.add(new THREE.Mesh(merge(jetties), M.vcol));
    // Superyachts at anchor across the bay.
    [[70, -205, 0.3, 46, 0x1f2c44], [200, -186, 1.9, 54, 0xffffff], [260, -240, 0.8, 66, 0xffffff], [-150, -290, 2.4, 44, 0xffffff],
      [120, -262, 0.1, 74, 0xffffff], [330, -310, 1.2, 50, 0x8c96a3], [20, -312, 2.9, 42, 0xffffff], [220, -330, 0.5, 60, 0x1b2a55],
      [-240, -272, 1.0, 40, 0xffffff], [410, -230, 2.2, 48, 0xffffff], [-40, -360, 0.6, 80, 0xffffff], [340, -150, 2.7, 44, 0xffffff]].forEach((y) => yachts.push(y));
    const fleet = new THREE.InstancedMesh(yachtGeo, M.paint, yachts.length);
    yachts.forEach(([x, z, ry, L, c], i) => { fleet.setMatrixAt(i, at(x, SEA - 0.1, z, ry, L, L * 0.9, L)); fleet.setColorAt(i, C(c)); });
    scene3.add(fleet);
  }

  // ---------------------------------------------------------------- the estate
  const plat = new THREE.Mesh(new THREE.BoxGeometry(200, 30, 150), [M.stone, M.stone, M.paving, M.stone, M.stone, M.stone]);
  plat.position.set(0, -15, 10); plat.receiveShadow = !simple; scene3.add(plat);
  // Glass balustrade with a champagne-gold rail round the edge of the estate.
  for (const z of [85, -65]) { box(200, 0.25, 0.5, M.gold, 0, 1.3, z); box(200, 1.2, 0.3, M.glass, 0, 0.6, z, scene3, false); }
  for (const x of [-100, 100]) { box(0.5, 0.25, 150, M.gold, x, 1.3, 10); box(0.3, 1.2, 150, M.glass, x, 0.6, 10, scene3, false); }

  const lawn = (x, z, w, d) => { const m = new THREE.Mesh(new THREE.PlaneGeometry(w, d), M.lawn); m.rotation.x = -Math.PI / 2; m.position.set(x, 0.04, z); m.receiveShadow = !simple; scene3.add(m); return m; };
  [[0, -59, 196, 10], [-92, 10, 12, 108], [92, 10, 12, 108], [0, 80, 196, 8], [-72, -48, 36, 8], [72, -48, 36, 8]].forEach((l) => {
    lawn(...l);
    const [x, z, w, d] = l;
    const n = Math.max(2, Math.round((w * d) / 420));
    for (let k = 0; k < n; k++) { const ox = x + (rand() - 0.5) * (w - 6), oz = z + (rand() - 0.5) * (d - 6); olive(scene3, ox, 0, oz, 0.9 + rand() * 0.4); solid.circle(ox, oz, 0.6); }
    for (let k = 0; k < n * 4; k++) flower(scene3, x + (rand() - 0.5) * (w - 3), 0.3, z + (rand() - 0.5) * (d - 3));
  });

  // Fountain plaza with the holographic globe: the shared brain every team reads from and writes to.
  const PLAZA = new V3(0, 0, 8);
  {
    const disc = new THREE.Mesh(new THREE.CircleGeometry(19, 96), std({ map: plazaTex, roughness: 0.3, envMapIntensity: 0.8 }));
    disc.rotation.x = -Math.PI / 2; disc.position.set(PLAZA.x, 0.06, PLAZA.z); disc.receiveShadow = !simple; scene3.add(disc);
    const basin = new THREE.Mesh(new THREE.CylinderGeometry(9, 9.4, 1.1, 64, 1, true), M.marble); basin.position.set(PLAZA.x, 0.55, PLAZA.z); scene3.add(basin);
    const lip = new THREE.Mesh(new THREE.TorusGeometry(9.1, 0.28, 10, 96), M.gold); lip.rotation.x = Math.PI / 2; lip.position.set(PLAZA.x, 1.12, PLAZA.z); scene3.add(lip);
    solid.circle(PLAZA.x, PLAZA.z, 9.9);
    const water = new THREE.Mesh(new THREE.CircleGeometry(9, 64), std({ color: 0x3fb7cf, roughness: 0.08, metalness: 0.2, normalMap: poolRipple, normalScale: new THREE.Vector2(0.4, 0.4), envMap: envTex, envMapIntensity: 0.8 }));
    water.rotation.x = -Math.PI / 2; water.position.set(PLAZA.x, 0.9, PLAZA.z); scene3.add(water);
    const ped = new THREE.Mesh(new THREE.CylinderGeometry(1.4, 2.4, 3.2, 32), M.marble); ped.position.set(PLAZA.x, 2, PLAZA.z); ped.castShadow = !simple; scene3.add(ped);
    // Fountain jets leaning in towards the globe, with spray.
    const jetMat = new THREE.MeshBasicMaterial({ color: 0xe8fbff, transparent: true, opacity: 0.5, depthWrite: false });
    world.jets = [];
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2;
      const jet = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.2, 5.5, 8, 1, true).translate(0, 2.75, 0), jetMat);
      jet.position.set(PLAZA.x + Math.cos(a) * 6.8, 0.9, PLAZA.z + Math.sin(a) * 6.8);
      jet.quaternion.setFromAxisAngle(new V3(-Math.sin(a), 0, Math.cos(a)), 0.45);
      scene3.add(jet); world.jets.push(jet);
      const spray = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex("255,255,255"), transparent: true, opacity: 0.3, depthWrite: false }));
      spray.scale.setScalar(3); spray.position.set(PLAZA.x + Math.cos(a) * 4.6, 5.4, PLAZA.z + Math.sin(a) * 4.6); scene3.add(spray);
    }
    const pedRing = new THREE.Mesh(new THREE.TorusGeometry(1.45, 0.12, 8, 48), M.gold); pedRing.rotation.x = Math.PI / 2; pedRing.position.set(PLAZA.x, 3.6, PLAZA.z); scene3.add(pedRing);
  }
  const globe = new THREE.Group(); globe.position.set(PLAZA.x, 9, PLAZA.z); scene3.add(globe);
  const globeCore = new THREE.Mesh(new THREE.SphereGeometry(4.2, 48, 32), std({ color: 0xbfe8ff, emissive: 0x3aa6e0, emissiveIntensity: 1.4, transparent: true, opacity: 0.38, roughness: 0.1, depthWrite: false }));
  const lines = new THREE.LineSegments(new THREE.WireframeGeometry(new THREE.SphereGeometry(4.3, 24, 16)), new THREE.LineBasicMaterial({ color: 0xbff0ff, transparent: true, opacity: 0.55 }));
  // Continents as a dotted shell, so it reads as a globe rather than a ball.
  const dots = [];
  for (let i = 0; i < 2600; i++) {
    const u = rand() * 2 - 1, a = rand() * Math.PI * 2, r = Math.sqrt(1 - u * u);
    const x = r * Math.cos(a), y = u, z = r * Math.sin(a);
    if (Math.sin(x * 3.1 + z * 1.7) + Math.cos(y * 4.3 + x * 2.2) + Math.sin(z * 5.1 - y * 1.3) > 0.9) dots.push(x * 4.35, y * 4.35, z * 4.35);
  }
  const dotGeo = new THREE.BufferGeometry(); dotGeo.setAttribute("position", new THREE.Float32BufferAttribute(dots, 3));
  const landDots = new THREE.Points(dotGeo, new THREE.PointsMaterial({ color: 0xffffff, size: 0.18, transparent: true, opacity: 0.9 }));
  const orbit = [0, 1].map((i) => { const r = new THREE.Mesh(new THREE.TorusGeometry(5.6 + i * 0.6, 0.06, 8, 128), M.gold); r.rotation.set(Math.PI / 2 + (i ? 0.45 : -0.3), 0, i * 0.8); globe.add(r); return r; });
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex("120,210,255"), transparent: true, depthWrite: false, opacity: 0.55 })); halo.scale.setScalar(20);
  globe.add(globeCore, lines, landDots, halo);
  globe.add(new THREE.PointLight(0x7fd3ff, 60, 40, 2));

  // The JARVIS HQ building: podium with grand steps, a glass main block, two wings, the crown sign and the light beam.
  const HQ = new THREE.Group(); HQ.position.set(0, 0, -40); HQ.scale.setScalar(1.15); scene3.add(HQ);
  let ceoSeat, ceoScreen, jarvisSeat;   // the owner's chair and desk figures, and JARVIS's chair, set up with the building below
  {
    box(64, 3, 30, M.marble, 0, 1.5, 0, HQ);
    for (let i = 0; i < 6; i++) box(26 - i * 0.6, 0.5, 1.2, M.marble, 0, 0.25 + i * 0.5, 15.6 + (5 - i) * 1.2, HQ);
    // Main block: three floors of floor-to-ceiling glass between white slabs with gold edges. The front 8 m of each
    // floor is open office (desks, screens, plants) behind the glass, with a terrace and glass rail along the front.
    for (let f = 0; f < 4; f++) {
      const sy = 3 + f * 5.2, top = sy + 0.9;
      box(36, 0.9, 20, M.marble, 0, sy + 0.45, 0, HQ);
      box(36.2, 0.18, 0.18, M.gold, 0, sy + 0.95, 10.05, HQ, false);
      if (f < 3) {
        box(35, 4.3, 11, M.tint, 0, sy + 3.05, -4.4, HQ);                       // the core
        box(34.6, 4.1, 0.2, M.interior, 0, sy + 3.05, 1.2, HQ, false);          // lit back wall of the office
        box(34.6, 0.08, 7.8, M.oak, 0, top + 0.04, 5.2, HQ, false);             // office floor
        if (f !== 2) for (let k = -3; k <= 3; k++) if (f || k) workstation(HQ, k * 4.8, top + 0.08, 6.2, Math.PI, 1.15);   // (the ground floor's door is at k = 0; floor 2 is JARVIS's office)
        for (const px of [-16.4, 16.4]) { box(0.8, 0.8, 0.8, M.white, px, top + 0.48, 8, HQ, false); const pl = new THREE.Mesh(new THREE.IcosahedronGeometry(0.7, 0), M.leaf); pl.position.set(px, top + 1.5, 8); HQ.add(pl); }
        if (f === 0) {
          // The ground floor has a door in the glass, a gap in the rail and steps down to the podium: walk mode goes in.
          for (const s of [-1, 1]) { box(15.3, 4.3, 0.12, M.glass, s * 9.95, sy + 3.05, 9.3, HQ, false); box(15.8, 1.0, 0.06, M.glass, s * 10.2, top + 0.5, 11.55, HQ, false); box(0.22, 4.3, 0.3, M.gold, s * 2.3, sy + 3.05, 9.4, HQ, false); }
          box(4.8, 0.25, 0.3, M.gold, 0, sy + 5.08, 9.4, HQ, false);
          for (let i = 0; i < 3; i++) box(5, 0.3, 0.7, M.marble, 0, 3.75 - i * 0.3, 11.95 + i * 0.7, HQ);
        } else {
          box(35.2, 4.3, 0.12, M.glass, 0, sy + 3.05, 9.3, HQ, false);
          box(36.2, 1.0, 0.06, M.glass, 0, top + 0.5, 11.55, HQ, false);           // glass rail
        }
        for (let k = -4; k <= 4; k++) if (f || k) box(0.22, 4.3, 0.3, M.gold, k * 4.3, sy + 3.05, 9.4, HQ, false);
        box(36.2, 0.3, 1.6, M.marble, 0, top - 0.15, 10.8, HQ);                 // terrace
        box(36.2, 0.08, 0.08, M.gold, 0, top + 1.0, 11.55, HQ, false);
      }
    }
    // People on the grand steps and the podium (scenery, not agents).
    for (let i = 0; i < 9; i++) visitor(HQ, -9 + i * 2.3 + (i % 2) * 0.7, 3, 13.5 + (i % 3) * 1.1, Math.PI + (rand() - 0.5));
    // Roof garden round the penthouse.
    for (const [ox, oz] of [[-13, 5], [13, 5], [-13, -6], [13, -6]]) olive(HQ, ox, 19.6, oz, 1.1);
    for (let k = -3; k <= 3; k++) { flower(HQ, k * 3.5 + 1, 19.9, 8.2); flower(HQ, k * 3.5 - 1, 19.9, 7.6); }
    for (const s of [-1, 1]) {
      box(16, 10, 18, M.tint, s * 25, 8, 1, HQ);
      box(16.6, 0.8, 18.6, M.marble, s * 25, 13.4, 1, HQ);
      box(16.6, 0.8, 18.6, M.marble, s * 25, 3.4, 1, HQ);
      box(16.8, 0.16, 0.16, M.gold, s * 25, 13.85, 10.35, HQ, false);
      box(15.6, 8.6, 0.2, M.warmLight, s * 25, 8.3, 9.8, HQ, false);
      box(16.2, 9.2, 0.12, M.glass, s * 25, 8.3, 10.1, HQ, false);
      for (let k = -3; k <= 3; k++) box(0.2, 9.2, 0.25, M.gold, s * 25 + k * 2.3, 8.3, 10.2, HQ, false);
    }
    // Colonnade along the podium front.
    for (let k = -7; k <= 7; k++) if (Math.abs(k) > 2) { box(0.9, 5.6, 0.9, M.white, k * 4, 5.8, 12.6, HQ); box(1.2, 0.3, 1.2, M.gold, k * 4, 8.7, 12.6, HQ, false); }
    // Lanterns along the podium and the grand stairs: they come on in the evening.
    for (let k = -7; k <= 7; k += 2) { const lan = new THREE.Mesh(new THREE.BoxGeometry(0.45, 0.6, 0.45), M.lampGlass); lan.position.set(k * 4, 3.4, 14.2); HQ.add(lan); }
    box(62, 0.6, 3.4, M.marble, 0, 9, 12.6, HQ);
    // Crown: the setback penthouse is the owner's office: a glass front over the plaza and the sea, tinted sides,
    // the desk with the business figures, and the owner in his chair. The JARVIS sign stands on the roof above it.
    box(18, 0.3, 12, M.marble, 0, 21.25, -1, HQ);
    const pfloor = new THREE.Mesh(new THREE.PlaneGeometry(17.4, 11.4), M.oak); pfloor.rotation.x = -Math.PI / 2; pfloor.position.set(0, 21.42, -1); HQ.add(pfloor);
    box(18, 1.2, 0.3, M.marble, 0, 22.0, -6.85, HQ);                                 // a low back wall, glass above: the circuit and the city show behind
    box(17.4, 3.4, 0.08, M.glass, 0, 24.3, -6.85, HQ, false);
    for (let k = -2; k <= 2; k++) box(0.18, 3.4, 0.25, M.gold, k * 4.3, 24.3, -6.85, HQ, false);
    for (const s of [-1, 1]) box(0.3, 4.7, 12, M.tint, s * 8.85, 23.75, -1, HQ);
    { const S = 1.15, w = (x, z) => [x * S, -40 + z * S];
      solid.camSegs.push([...w(-8.85, -6.85), ...w(8.85, -6.85)], [...w(-8.85, -6.85), ...w(-8.85, 5)], [...w(8.85, -6.85), ...w(8.85, 5)]); }
    box(17.4, 4.5, 0.08, M.glass, 0, 23.65, 4.96, HQ, false);
    for (let k = -2; k <= 2; k++) box(0.18, 4.5, 0.25, M.gold, k * 4.3, 23.65, 5.0, HQ, false);
    const ceiling = new THREE.Mesh(new THREE.PlaneGeometry(17.4, 11.4), M.warmLight); ceiling.rotation.x = Math.PI / 2; ceiling.position.set(0, 25.98, -1); HQ.add(ceiling);
    box(19, 0.7, 13, M.marble, 0, 26.4, -1, HQ);
    const ceoDesk = new THREE.Group(); ceoDesk.position.set(0, 21.42, 1.2); HQ.add(ceoDesk);
    box(4.4, 0.1, 1.6, M.walnut, 0, 0.78, 0, ceoDesk);
    for (const sx of [-2.0, 2.0]) box(0.08, 0.74, 1.3, M.gold, sx, 0.37, 0, ceoDesk, false);
    ceoScreen = screenTexture(1024, 346);
    const ceoPanel = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 1.15), new THREE.MeshBasicMaterial({ map: ceoScreen.t, toneMapped: false }));
    ceoPanel.position.set(0, 1.5, 0.52); ceoPanel.rotation.y = Math.PI; ceoDesk.add(ceoPanel);
    box(3.5, 1.25, 0.06, M.frame, 0, 1.5, 0.56, ceoDesk, false);
    box(0.5, 0.06, 0.3, M.frame, 0, 0.86, 0.5, ceoDesk, false);
    const ceoLamp = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 8), M.lampGlass); ceoLamp.position.set(1.7, 1.25, -0.3); ceoDesk.add(ceoLamp);
    box(0.05, 0.4, 0.05, M.gold, 1.7, 1.0, -0.3, ceoDesk, false);
    box(0.85, 0.12, 0.8, M.walnut, 0, 0.6, -1.3, ceoDesk, false);                   // the owner's chair, low-backed so he stays in view
    box(0.85, 0.55, 0.1, M.walnut, 0, 0.9, -1.68, ceoDesk, false);
    const ceoBase = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.26, 0.55, 12), M.gold); ceoBase.position.set(0, 0.28, -1.3); ceoDesk.add(ceoBase);
    box(2.8, 0.55, 1.0, M.leather, -6.2, 0.28, -3.5, ceoDesk);                      // sofa
    box(2.8, 0.7, 0.25, M.leather, -6.2, 0.9, -3.95, ceoDesk, false);
    box(1.3, 0.06, 0.7, M.walnut, -6.2, 0.5, -1.9, ceoDesk, false);                 // low table
    box(0.08, 0.5, 0.08, M.gold, -6.2, 0.25, -1.9, ceoDesk, false);
    box(2.2, 1.2, 0.5, M.walnut, 6.6, 0.6, -5.5, ceoDesk);                          // cabinet
    box(0.12, 2.6, 1.6, M.gold, -9.95, 1.3, -4.6, ceoDesk, false);                     // the lift door, in the side wall
    box(0.3, 0.12, 1.9, M.frame, -9.9, 2.66, -4.6, ceoDesk, false);
    for (const [px, pz] of [[-7.6, 4.2], [7.6, 4.2], [7.6, -2.4]]) { box(0.8, 0.8, 0.8, M.white, px, 0.4, pz, ceoDesk, false); const pl = new THREE.Mesh(new THREE.IcosahedronGeometry(0.7, 0), M.leaf); pl.position.set(px, 1.3, pz); ceoDesk.add(pl); }
    HQ.updateWorldMatrix(true, true);
    ceoSeat = { pos: HQ.localToWorld(new V3(0, 21.42, -0.1)), face: 0 };   // the owner's chair, facing the glass
    const signTex = canvasTex(1024, 512, (g, w, h) => {
      g.fillStyle = "#121c29"; g.fillRect(0, 0, w, h);
      g.strokeStyle = "#c6a46a"; g.lineWidth = 10; g.strokeRect(14, 14, w - 28, h - 28);
      g.fillStyle = "#d8b878";
      g.beginPath(); g.moveTo(w / 2 - 110, 215); g.lineTo(w / 2 - 130, 80); g.lineTo(w / 2 - 55, 150); g.lineTo(w / 2, 60); g.lineTo(w / 2 + 55, 150); g.lineTo(w / 2 + 130, 80); g.lineTo(w / 2 + 110, 215); g.closePath(); g.fill();
      g.font = "500 150px 'Bodoni Moda', Georgia, serif"; g.textAlign = "center"; g.fillText("JARVIS", w / 2, 410);
    });
    const signMat = std({ map: signTex, emissive: 0xffffff, emissiveMap: signTex, emissiveIntensity: 0.45, roughness: 0.4 });
    glowMats.push([signMat, 0.4]);
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(13, 6.5), signMat);
    sign.position.set(0, 30.2, 5.2); HQ.add(sign);
    for (const s of [-1, 1]) box(0.25, 3.6, 0.25, M.gold, s * 5.5, 28.5, 5.2, HQ, false);
    // JARVIS's own office: floor 2 behind the glass, a dark command desk, a wireframe globe and the sign on the wall.
    {
      const top = 3 + 2 * 5.2 + 0.9;
      box(7.2, 0.12, 1.6, M.walnut, 0, top + 0.86, 5.6, HQ);
      box(6.6, 0.76, 1.2, M.frame, 0, top + 0.42, 5.6, HQ, false);
      for (const sx of [-2.3, 0, 2.3]) { const sc = new THREE.Mesh(new THREE.PlaneGeometry(1.9, 1.1), std({ color: 0x0f1b2c, emissive: 0x9fd3ff, emissiveIntensity: 0.35, roughness: 0.3 })); sc.position.set(sx, top + 1.8, 4.9); sc.rotation.y = Math.PI; HQ.add(sc); }
      const mini = new THREE.Mesh(new THREE.SphereGeometry(0.75, 20, 14), new THREE.MeshBasicMaterial({ color: 0x7fd3ff, wireframe: true, transparent: true, opacity: 0.8 })); mini.position.set(0, top + 2.3, 6.3); HQ.add(mini);
      const jsign = new THREE.Mesh(new THREE.PlaneGeometry(6, 3), signMat); jsign.position.set(0, top + 2.4, 1.45); HQ.add(jsign);
      box(1.0, 0.7, 1.0, M.leather, 0, top + 0.43, 7.4, HQ, false);
      HQ.updateWorldMatrix(true, true);
      jarvisSeat = { pos: HQ.localToWorld(new V3(0, top + 0.02, 7.4)), face: Math.PI };
      for (const px of [-8, 8]) { box(0.9, 0.9, 0.9, M.white, px, top + 0.53, 7.6, HQ, false); const pl = new THREE.Mesh(new THREE.IcosahedronGeometry(0.8, 0), M.leaf); pl.position.set(px, top + 1.6, 7.6); HQ.add(pl); }
    }
    // A champagne-gold crown on the penthouse roof, where the beam starts.
    const crown = new THREE.Group(); crown.position.set(0, 26.8, -1);
    const band = new THREE.Mesh(new THREE.CylinderGeometry(2.6, 2.4, 1.1, 40, 1, true), M.gold); band.position.y = 0.55;
    crown.add(band);
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2, tip = new THREE.Mesh(new THREE.ConeGeometry(0.45, 1.8, 6), M.gold);
      tip.position.set(Math.cos(a) * 2.45, 1.9, Math.sin(a) * 2.45); crown.add(tip);
      const jewel = new THREE.Mesh(new THREE.SphereGeometry(0.22, 10, 8), M.lampGlass); jewel.position.set(Math.cos(a) * 2.5, 0.6, Math.sin(a) * 2.5); crown.add(jewel);
    }
    HQ.add(crown);
    // Roof terrace on the west wing: planters, loungers and parasols.
    for (let i = 0; i < 3; i++) {
      const px = -31 + i * 6;
      const pl = new THREE.Mesh(new THREE.IcosahedronGeometry(1, 0), M.leaf); pl.position.set(px, 14.8, -6); HQ.add(pl);
      const um = new THREE.Mesh(new THREE.ConeGeometry(1.8, 0.6, 12), M.white); um.position.set(px + 1.5, 16.6, 4); HQ.add(um);
      box(0.08, 2.2, 0.08, M.gold, px + 1.5, 15.2, 4, HQ, false);
      box(1, 0.3, 2.2, M.white, px, 14.0, 4, HQ, false);
    }
    box(17, 1, 0.08, M.glass, -25, 14.3, 10.2, HQ, false);
    // Helipad and a parked helicopter on the east wing.
    const padTex = canvasTex(256, 256, (g, w) => { g.fillStyle = "#2a2f36"; g.fillRect(0, 0, w, w); g.strokeStyle = "#e8d39b"; g.lineWidth = 10; g.beginPath(); g.arc(w / 2, w / 2, 110, 0, Math.PI * 2); g.stroke(); g.fillStyle = "#fff"; g.font = "bold 130px sans-serif"; g.textAlign = "center"; g.fillText("H", w / 2, w / 2 + 46); });
    const pad = new THREE.Mesh(new THREE.CircleGeometry(6.4, 48), std({ map: padTex, roughness: 0.7 }));
    pad.rotation.x = -Math.PI / 2; pad.position.set(25, 13.85, 1); HQ.add(pad);
    const heli = new THREE.Group(); heli.position.set(25, 14, 1); heli.rotation.y = 0.6;
    const hb = new THREE.Mesh(new THREE.SphereGeometry(1.6, 20, 14), M.navy); hb.scale.set(1.6, 0.9, 0.9); hb.position.y = 1.4;
    const tail = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.35, 5, 8), M.navy); tail.rotation.z = Math.PI / 2; tail.position.set(-4, 1.7, 0);
    const rotor = new THREE.Mesh(new THREE.BoxGeometry(9, 0.06, 0.3), M.frame); rotor.position.y = 2.6;
    const rotor2 = rotor.clone(); rotor2.rotation.y = Math.PI / 2;
    const fin = new THREE.Mesh(new THREE.BoxGeometry(0.8, 1.4, 0.12), M.navy); fin.position.set(-6.2, 2.2, 0);
    heli.add(hb, tail, fin, rotor, rotor2);
    for (const z of [0.8, -0.8]) box(4, 0.12, 0.12, M.frame, 0, 0.1, z, heli, false);
    HQ.add(heli);
    HQ.traverse((o) => { o.userData.pick = "jarvis"; });
    // Walk mode: the podium edge (open at the steps), the core, the ground-floor office through its door, the wings;
    // and the floors: podium, office and terrace, with ramps on the steps.
    {
      const S = 1.15, Z0 = -40, w = (x, z) => [x * S, Z0 + z * S];
      const seg = (ax, az, bx, bz) => solid.seg(...w(ax, az), ...w(bx, bz)), rect = (x, z, wd, d) => solid.rect(x * S, Z0 + z * S, wd * S, d * S);
      seg(-32, 15, -13, 15); seg(13, 15, 32, 15); seg(-32, -15, 32, -15); seg(-32, -15, -32, 15); seg(32, -15, 32, 15);
      rect(0, -4.4, 35, 11.2);
      for (const s of [-1, 1]) { seg(s * 17.5, 1.2, s * 17.5, 9.3); seg(s * 2.3, 9.3, s * 17.6, 9.3); seg(s * 2.3, 11.55, s * 18.1, 11.55); seg(s * 18.1, 9.3, s * 18.1, 11.6); rect(s * 25, 1, 16, 18); }
      for (let k = -3; k <= 3; k++) if (k) solid.circle(...w(k * 4.8, 6.2), 1.4);
      for (const px of [-16.4, 16.4]) solid.circle(...w(px, 8), 0.8);
      solid.floors.push((x, z) => {
        const lx = x / S, lz = (z - Z0) / S;
        if (Math.abs(lx) <= 17.5 && lz >= 1.2 && lz <= 11.6) return 3.9 * S;
        if (Math.abs(lx) <= 2.5 && lz > 11.6 && lz <= 13.8) return (3 + 0.9 * (13.8 - lz) / 2.2) * S;
        if (Math.abs(lx) <= 32 && Math.abs(lz) <= 15) return 3 * S;
        if (Math.abs(lx) <= 13 && lz > 15 && lz <= 21.6) return 3 * S * (21.6 - lz) / 6.6;
        return null;
      });
    }
  }
  // The light beam rising from the crown.
  const beamMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { t: { value: 0 }, glow: { value: 1 } },
    vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
    fragmentShader: "varying vec2 vUv; uniform float t; uniform float glow; void main(){ float fade = pow(1.0 - vUv.y, 1.3); gl_FragColor = vec4(vec3(1.0, 0.86, 0.6) * fade * (2.2 + 0.4 * sin(t * 2.0)) * glow, fade * glow); }",
  });
  {
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 1.8, 420, 24, 1, true), beamMat);
    beam.position.set(0, 33.5 + 210, -39); scene3.add(beam);   // from the crown up, clear of the owner's office below it
  }

  // Pool with loungers and parasols, the tennis court, the supercar forecourt.
  let turn;
  {
    box(34, 0.3, 18, M.ivory, -80, 0.15, 52);
    const pool = new THREE.Mesh(new THREE.PlaneGeometry(26, 9), std({ color: 0x2fb3cc, roughness: 0.06, metalness: 0.15, normalMap: poolRipple, normalScale: new THREE.Vector2(0.5, 0.5), envMap: envTex, envMapIntensity: 0.8 }));
    pool.rotation.x = -Math.PI / 2; pool.position.set(-80, 0.33, 50); scene3.add(pool);
    box(26.6, 0.2, 0.3, M.gold, -80, 0.35, 45.4, scene3, false); box(26.6, 0.2, 0.3, M.gold, -80, 0.35, 54.6, scene3, false);
    solid.rect(-80, 50, 26.6, 9.6);
    for (let i = 0; i < 6; i++) {
      const x = -91 + i * 4.4;
      box(1.1, 0.35, 2.6, M.white, x, 0.5, 58.2);
      const umb = new THREE.Mesh(new THREE.ConeGeometry(2, 0.8, 12), M.white); umb.position.set(x + 2.2, 3.1, 58.4); umb.castShadow = !simple; scene3.add(umb);
      box(0.08, 2.8, 0.08, M.gold, x + 2.2, 1.6, 58.4, scene3, false);
      solid.circle(x + 2.2, 58.4, 0.2); solid.circle(x, 58.2, 1.0);
    }
    const courtTex = canvasTex(512, 256, (g, w, h) => {
      g.fillStyle = "#b4664a"; g.fillRect(0, 0, w, h); g.fillStyle = "#2f7d74"; g.fillRect(36, 30, w - 72, h - 60);
      g.strokeStyle = "#fff"; g.lineWidth = 4; g.strokeRect(36, 30, w - 72, h - 60); g.strokeRect(36, 52, w - 72, h - 104);
      g.beginPath(); g.moveTo(w / 2, 30); g.lineTo(w / 2, h - 30); g.moveTo(140, 52); g.lineTo(140, h - 52); g.moveTo(w - 140, 52); g.lineTo(w - 140, h - 52); g.moveTo(140, h / 2); g.lineTo(w - 140, h / 2); g.stroke();
    });
    const court = new THREE.Mesh(new THREE.PlaneGeometry(30, 15), std({ map: courtTex, roughness: 0.8 }));
    court.rotation.x = -Math.PI / 2; court.position.set(82, 0.08, 50); court.receiveShadow = !simple; scene3.add(court);
    box(0.1, 1, 11, M.white, 82, 0.5, 50, scene3, false); solid.seg(82, 44.5, 82, 55.5);
    for (const [x, z] of [[67, 42.5], [97, 42.5], [67, 57.5], [97, 57.5]]) box(0.15, 3.2, 0.15, M.frame, x, 1.6, z, scene3, false);
    // Forecourt turntable with three supercars.
    turn = new THREE.Group(); turn.position.set(0, 0, 46); scene3.add(turn); solid.circle(0, 46, 11.4);
    const tt = new THREE.Mesh(new THREE.CylinderGeometry(11, 11, 0.4, 72), std({ color: 0x2b2f35, roughness: 0.25, metalness: 0.4 })); tt.position.y = 0.2; tt.receiveShadow = !simple; turn.add(tt);
    const ttRing = new THREE.Mesh(new THREE.TorusGeometry(11, 0.18, 8, 96), M.gold); ttRing.rotation.x = Math.PI / 2; ttRing.position.y = 0.42; turn.add(ttRing);
    // Side profile of a mid-engined supercar, extruded across its width, with a smoked canopy, intakes and wheels.
    const profile = (pts, depth) => { const g = new THREE.ExtrudeGeometry(new THREE.Shape(pts.map(([x, y]) => new THREE.Vector2(x, y))), { depth, bevelEnabled: true, bevelSize: 0.06, bevelThickness: 0.06, bevelSegments: 2 }); g.translate(0, 0, -depth / 2); return g; };
    const bodyGeo = profile([[-2.35, 0.32], [2.25, 0.32], [2.42, 0.48], [2.1, 0.66], [1.0, 0.78], [0.35, 1.14], [-0.9, 1.2], [-1.75, 0.98], [-2.35, 0.86]], 1.9);
    const canopy = profile([[0.85, 0.82], [0.32, 1.17], [-0.85, 1.22], [-1.35, 0.98]], 1.5);
    const wheelG = new THREE.CylinderGeometry(0.42, 0.42, 0.32, 18).rotateX(Math.PI / 2);
    const rimG = new THREE.CylinderGeometry(0.26, 0.26, 0.34, 10).rotateX(Math.PI / 2);
    const superGeo = merge([
      { geo: bodyGeo, color: 0xffffff },
      { geo: canopy, color: 0x12161c, m: at(0, 0.02, 0) },
      { geo: new THREE.BoxGeometry(0.9, 0.2, 2.0), color: 0x12161c, m: at(-1.2, 0.62, 0) },     // side intakes
      { geo: new THREE.BoxGeometry(0.12, 0.1, 1.9), color: 0x12161c, m: at(-2.3, 1.0, 0) },     // rear wing
      ...[[1.45, 0.92], [1.45, -0.92], [-1.45, 0.92], [-1.45, -0.92]].flatMap(([x, z]) => [
        { geo: wheelG, color: 0x111111, m: at(x, 0.42, z) }, { geo: rimG, color: 0xb9bcc2, m: at(x, 0.42, z) }]),
    ]);
    const supers = new THREE.InstancedMesh(superGeo, M.paint, 3);
    [[0x111214, -6, 0.2], [0xf2c200, 0, 0], [0xc8102e, 6, -0.2]].forEach(([c, x, ry], i) => { supers.setMatrixAt(i, at(x, 0.4, 0, ry + Math.PI / 2, 1.35, 1.35, 1.35)); supers.setColorAt(i, C(c)); });
    // Spotlights on the turntable for the evening.
    for (let i = 0; i < 12; i++) { const a = (i / 12) * Math.PI * 2; const sp = new THREE.Mesh(new THREE.SphereGeometry(0.18, 8, 6), M.lampGlass); sp.position.set(Math.cos(a) * 10.4, 0.5, Math.sin(a) * 10.4); turn.add(sp); }
    supers.castShadow = !simple; turn.add(supers);
  }

  // Palms, hedges and lamp posts around the estate.
  {
    // Trunk: tapered and gently leaning, ringed. Fronds: arched strips with a feathered leaf texture.
    const trunk = new THREE.CylinderGeometry(0.22, 0.42, 10, 7, 10);
    { const tp = trunk.attributes.position; for (let i = 0; i < tp.count; i++) { const y = tp.getY(i) + 5; tp.setX(i, tp.getX(i) + 0.012 * y * y); tp.setY(i, y); const ring = 1 + 0.06 * Math.sin(y * 6); tp.setX(i, tp.getX(i) * ring); tp.setZ(i, tp.getZ(i) * ring); } trunk.computeVertexNormals(); }
    const leafTex = canvasTex(128, 512, (g, w, h) => {
      g.clearRect(0, 0, w, h); g.strokeStyle = "#5d7f3a"; g.lineWidth = 4; g.beginPath(); g.moveTo(w / 2, 0); g.lineTo(w / 2, h); g.stroke();
      for (let y = 8; y < h; y += 7) { const reach = (w / 2 - 4) * Math.sin(Math.PI * (y / h)) ** 0.7; g.strokeStyle = `rgb(${70 + rand() * 30},${115 + rand() * 30},${45 + rand() * 20})`; g.lineWidth = 3;
        g.beginPath(); g.moveTo(w / 2, y); g.lineTo(w / 2 - reach, y + 26); g.moveTo(w / 2, y); g.lineTo(w / 2 + reach, y + 26); g.stroke(); }
    });
    const frond = (() => {
      const pos = [], uv = [], idx = [], N = 10, L = 6.2;
      for (let i = 0; i <= N; i++) {
        const t = i / N, x = t * L, y = 0.9 * x - 0.13 * x * x, w = 1.15;
        pos.push(x, y, -w, x, y, w); uv.push(0, 1 - t, 1, 1 - t);
        if (i < N) idx.push(i * 2, i * 2 + 1, i * 2 + 2, i * 2 + 1, i * 2 + 3, i * 2 + 2);
      }
      const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3)); g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2)); g.setIndex(idx); g.computeVertexNormals(); return g;
    })();
    const leafMat = std({ map: leafTex, alphaTest: 0.45, side: THREE.DoubleSide, roughness: 0.8, color: 0xd8f0c0 });
    const spots = [];
    for (let i = 0; i < 8; i++) { const a = (i / 8) * Math.PI * 2 + Math.PI / 8; spots.push([PLAZA.x + Math.cos(a) * 24, PLAZA.z + Math.sin(a) * 24]); }
    for (let x = -94; x <= 94; x += 13) spots.push([x, 81]);
    for (let z = -50; z <= 74; z += 14) spots.push([-96, z], [96, z]);
    [[-86, 38], [-30, 70], [30, 70], [86, 38], [-18, 46], [18, 46], [-18, -12], [18, -12],
     [-47, -14], [-57, 3], [-61, 21], [-58, 38], [-49, 54], [47, -14], [57, 3], [61, 21], [58, 38], [49, 54]].forEach((s) => spots.push(s));
    const FRONDS = simple ? 7 : 10;
    const trunks = new THREE.InstancedMesh(trunk, std({ color: 0x8a6d4e, roughness: 0.9, flatShading: true }), spots.length);
    const fronds = new THREE.InstancedMesh(frond, leafMat, spots.length * FRONDS);
    const pm = new THREE.Matrix4(), fm = new THREE.Matrix4(), q = new THREE.Quaternion();
    spots.forEach(([x, z], i) => {
      const s = 0.85 + (i % 5) * 0.08, ry = i * 1.7;
      pm.copy(at(x, 0, z, ry, 1, s, 1)); trunks.setMatrixAt(i, pm); solid.circle(x, z, 0.55);
      const top = new V3(0.012 * 100, 10, 0).applyMatrix4(pm);
      for (let k = 0; k < FRONDS; k++) {
        q.setFromEuler(new THREE.Euler(0, ry + (k / FRONDS) * Math.PI * 2 + (k % 2) * 0.2, (k % 3 - 1) * 0.12));
        fm.compose(top, q, new V3(1, 1, 1)); fronds.setMatrixAt(i * FRONDS + k, fm);
      }
    });
    trunks.castShadow = fronds.castShadow = !simple;
    scene3.add(trunks, fronds);
    const lampPos = [];
    for (let i = 0; i < 16; i++) { const a = (i / 16) * Math.PI * 2 + 0.1; lampPos.push([PLAZA.x + Math.cos(a) * 17, PLAZA.z + Math.sin(a) * 17]); }
    const lamps = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.1, 0.16, 4, 8), M.frame, lampPos.length);
    const bulbs = new THREE.InstancedMesh(new THREE.SphereGeometry(0.32, 12, 8), M.lampGlass, lampPos.length);
    lampPos.forEach(([x, z], i) => { lamps.setMatrixAt(i, at(x, 2, z)); bulbs.setMatrixAt(i, at(x, 4.1, z)); solid.circle(x, z, 0.3); });
    scene3.add(lamps, bulbs);
    const hedges = [[-16, 74, 22, 1.4, 1.6], [16, 74, 22, 1.4, 1.6]];
    const hm = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), M.hedge, hedges.length);
    hedges.forEach(([x, z, w, h, d], i) => { hm.setMatrixAt(i, at(x, h / 2, z, 0, w, h, d)); solid.rect(x, z, w, d); });
    hm.castShadow = !simple; scene3.add(hm);
    // Planting round the plaza ring: a bed of flowers outside the lamps, benches facing the globe, cypress pairs at the
    // corners of the estate, and visitors about the place (scenery, not agents).
    for (let i = 0; i < (simple ? 70 : 170); i++) { const a = rand() * Math.PI * 2, r = 20.2 + rand() * 1.8; flower(scene3, PLAZA.x + Math.cos(a) * r, 0.32, PLAZA.z + Math.sin(a) * r); }
    for (let i = 0; i < 12; i++) { const a = (i / 12) * Math.PI * 2 + 0.13, bx = PLAZA.x + Math.cos(a) * 15.2, bz = PLAZA.z + Math.sin(a) * 15.2; prop("bench", scene3, bx, 0.06, bz, -a + Math.PI / 2); solid.circle(bx, bz, 1.1); }
    for (const [x, z] of [[-97, 84], [-92, 84], [97, 84], [92, 84], [-97, -62], [-92, -62], [97, -62], [92, -62], [-48, 84], [48, 84]]) { prop("cypress", scene3, x, 0, z, 0, 0.9 + rand() * 0.3); solid.circle(x, z, 0.5); }
    for (const x of [-80, -60, 60, 80]) { olive(scene3, x, 0, 82, 1); solid.circle(x, 82, 0.6); }
    if (!simple) {
      for (let i = 0; i < 26; i++) { const a = rand() * Math.PI * 2, r = 11 + rand() * 7; visitor(scene3, PLAZA.x + Math.cos(a) * r, 0, PLAZA.z + Math.sin(a) * r, rand() * 6.3); }
      for (let i = 0; i < 8; i++) visitor(scene3, -92 + i * 3.6, 0.3, 48 + (i % 2) * 1.2, Math.PI);
      for (let i = 0; i < 10; i++) visitor(scene3, -14 + i * 3, 0, 30 + (i % 3), rand() * 6.3);
      for (let i = 0; i < 6; i++) visitor(scene3, 54 + i * 4.5, 0.1, 55.5, Math.PI);
    }
  }

  // ---------------------------------------------------------------- the agents: people in one consistent low-poly style
  const SKIN = [0xf1c7a5, 0xe0ac83, 0xc68a62, 0x8d5a3b, 0xf5d6bf, 0xd9a07a];
  const HAIR = [0x2b1d14, 0x4a3022, 0x7a5a3a, 0x1b1b1b, 0xb8925c, 0x3b2a20];
  const SUIT = [0x1f2a44, 0x2f3338, 0xc8b79a, 0x24324a, 0x3a3f46, 0xe9e6df];
  const mats = new Map();
  const mat = (hex) => { if (!mats.has(hex)) mats.set(hex, std({ color: hex, roughness: 0.6, flatShading: true })); return mats.get(hex); };
  const shoeMat = std({ color: 0x2a1c14, roughness: 0.4 });
  const body = {
    thigh: new THREE.CylinderGeometry(0.1, 0.08, 0.44, 7).translate(0, -0.22, 0),
    shin: new THREE.CylinderGeometry(0.075, 0.062, 0.42, 7).translate(0, -0.21, 0),
    shoe: new THREE.BoxGeometry(0.15, 0.08, 0.28).translate(0, -0.44, 0.06),
    pelvis: new THREE.BoxGeometry(0.36, 0.17, 0.22),
    torso: new THREE.CylinderGeometry(0.25, 0.19, 0.56, 8).translate(0, 0.3, 0),
    shirt: new THREE.BoxGeometry(0.12, 0.26, 0.05).translate(0, 0.44, 0.2),
    tie: new THREE.BoxGeometry(0.05, 0.22, 0.03).translate(0, 0.42, 0.225),
    neck: new THREE.CylinderGeometry(0.06, 0.07, 0.14, 8).translate(0, 0.64, 0),
    head: new THREE.IcosahedronGeometry(0.19, 1),
    hair: new THREE.SphereGeometry(0.205, 12, 8, 0, Math.PI * 2, 0, 1.35).translate(0, 0.03, -0.02),
    upper: new THREE.CylinderGeometry(0.068, 0.058, 0.3, 7).translate(0, -0.15, 0),
    fore: new THREE.CylinderGeometry(0.056, 0.048, 0.27, 7).translate(0, -0.135, 0),
    hand: new THREE.IcosahedronGeometry(0.058, 0).translate(0, -0.3, 0),
    tablet: new THREE.BoxGeometry(0.24, 0.3, 0.02).translate(0, -0.32, 0.05),
    racket: new THREE.TorusGeometry(0.13, 0.02, 6, 16).translate(0, -0.55, 0.05),
  };
  const part = (geo, m, shadow) => { const x = new THREE.Mesh(geo, m); if (shadow && !simple) x.castShadow = true; return x; };
  const hash = (s) => { let h = 2166136261; for (const ch of String(s)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619); return Math.abs(h); };
  // Each head agent has a look of its own so the owner can tell who is who at a glance: the suit in its department's
  // colour, a hairstyle, a build, and a prop for the job in its hand. JARVIS is black with a glowing gold accent; the
  // owner wears cream; Blake and Mark are casual persona figures (not likenesses of anyone).
  const LOOKS = {
    "product-research":  { hair: "short", hairColor: 0x2b1d14, build: [1.0, 1.0], glasses: true, prop: "magnifier" },
    "market-research":   { hair: "long", hairColor: 0x7a5a3a, build: [0.92, 0.98], prop: "notebook" },
    "creative-research": { hair: "bun", hairColor: 0x1b1b1b, build: [0.9, 1.02], glasses: true, prop: "camera" },
    "mentor-learning":   { hair: "bald", build: [1.08, 1.0], glasses: true, prop: "book" },
    "operations":        { hair: "crop", hairColor: 0x4a3022, build: [1.05, 1.03], prop: "clipboard" },
    "accountant":        { hair: "short", hairColor: 0xb8925c, build: [0.95, 0.96], glasses: true, prop: "ledger" },
    "meta-ads":          { hair: "crop", hairColor: 0x1b1b1b, build: [1.02, 1.04], prop: "chart" },
    "meta-ads-auditor":  { hair: "long", hairColor: 0x2b1d14, build: [0.94, 1.0], glasses: true, prop: "chart" },
    "shopify":           { hair: "bun", hairColor: 0x7a5a3a, build: [0.95, 1.0], prop: "bag" },
    "store-qa":          { hair: "short", hairColor: 0x3b2a20, build: [1.0, 0.97], prop: "bag" },
    "creative-director": { hair: "long", hairColor: 0xb8925c, build: [0.96, 1.05], prop: "camera" },
    "product-testing":   { hair: "crop", hairColor: 0x4a3022, build: [1.06, 0.98], prop: "beaker" },
    "jarvis": { suit: 0x101216, tie: 0xffd98a, glow: true, hair: "short", hairColor: 0x1b1b1b, build: [1.0, 1.04] },
    "owner":  { suit: 0xf1ead9, trousers: 0xf1ead9, tie: 0xc6a46a, hair: "short", hairColor: 0x2b1d14, build: [1.0, 1.0], sunglasses: true },
    "blake":  { suit: 0x2f8f83, casual: true, hair: "crop", hairColor: 0x7a5a3a, build: [1.06, 1.02], prop: "chart" },
    "mark":   { suit: 0x3a3f46, casual: true, hair: "bun", hairColor: 0x1b1b1b, build: [1.0, 1.04], prop: "notebook" },
  };
  const propGeo = {};
  function propMesh(kind, teamColor) {
    const g = new THREE.Group(); g.position.set(0, -0.3, 0.06);
    const add = (geo, color, x = 0, y = 0, z = 0, emissive) => { const m = new THREE.Mesh(geo, emissive ? std({ color, emissive, emissiveIntensity: 1.1, roughness: 0.4 }) : mat(color)); m.position.set(x, y, z); g.add(m); return m; };
    propGeo.cam ??= new THREE.BoxGeometry(0.2, 0.13, 0.09); propGeo.lens ??= new THREE.CylinderGeometry(0.045, 0.05, 0.07, 10).rotateX(Math.PI / 2);
    propGeo.bag ??= new THREE.BoxGeometry(0.2, 0.26, 0.1); propGeo.handle ??= new THREE.TorusGeometry(0.07, 0.012, 6, 14);
    propGeo.tab ??= new THREE.BoxGeometry(0.24, 0.3, 0.02); propGeo.bar ??= new THREE.BoxGeometry(0.04, 0.1, 0.01);
    propGeo.book ??= new THREE.BoxGeometry(0.22, 0.3, 0.05); propGeo.spine ??= new THREE.BoxGeometry(0.02, 0.3, 0.052);
    propGeo.ring ??= new THREE.TorusGeometry(0.08, 0.014, 6, 16); propGeo.stick ??= new THREE.CylinderGeometry(0.012, 0.012, 0.16, 6);
    propGeo.beaker ??= new THREE.CylinderGeometry(0.05, 0.04, 0.16, 10); propGeo.liquid ??= new THREE.CylinderGeometry(0.046, 0.038, 0.07, 10);
    propGeo.clip ??= new THREE.BoxGeometry(0.08, 0.03, 0.02);
    switch (kind) {
      case "camera": add(propGeo.cam, 0x1a1a1a); add(propGeo.lens, 0x2a2f36, 0.02, 0, 0.08); add(new THREE.SphereGeometry(0.018, 6, 6), 0xf1c40f, -0.07, 0.08, 0); break;
      case "bag": add(propGeo.bag, teamColor, 0, -0.12, 0); add(propGeo.handle, 0xc6a46a, 0, 0.03, 0); break;
      case "chart": add(propGeo.tab, 0x16191e); add(propGeo.bar, 0x7fd3ff, -0.07, -0.06, 0.012, 0x7fd3ff); add(propGeo.bar, 0x7fd3ff, 0, -0.03, 0.012, 0x7fd3ff).scale.y = 1.6; add(propGeo.bar, 0x7fd3ff, 0.07, 0, 0.012, 0x7fd3ff).scale.y = 2.2; break;
      case "ledger": add(propGeo.book, 0x5b3a25); add(propGeo.spine, 0xc6a46a, -0.1, 0, 0); break;
      case "book": add(propGeo.book, 0x1d3557); add(propGeo.spine, 0xe9e6df, -0.1, 0, 0); break;
      case "notebook": add(new THREE.BoxGeometry(0.18, 0.24, 0.03), 0xe9e6df); add(propGeo.spine, 0x2f3338, -0.08, 0, 0).scale.y = 0.8; break;
      case "clipboard": add(new THREE.BoxGeometry(0.2, 0.28, 0.015), 0xc8b79a); add(propGeo.clip, 0x8a8f96, 0, 0.13, 0.012); break;
      case "magnifier": add(propGeo.ring, 0xc6a46a, 0, 0.06, 0); add(propGeo.stick, 0x5b3a25, 0, -0.08, 0); break;
      case "beaker": add(propGeo.beaker, 0xdfe8ee); add(propGeo.liquid, 0x2fb3cc, 0, -0.04, 0, 0x1f8fa8); break;
    }
    return g;
  }
  function character(name, teamColor, size = 1) {
    const h = hash(name), L = LOOKS[name] || {};
    const suitHex = L.suit != null ? L.suit : new THREE.Color(teamColor).lerp(new THREE.Color(0x1f2a44), 0.5).getHex();
    const suit = mat(suitHex), skin = mat(SKIN[(h >> 3) % SKIN.length]), hair = mat(L.hairColor != null ? L.hairColor : HAIR[(h >> 6) % HAIR.length]);
    const trousers = mat(L.trousers != null ? L.trousers : L.casual ? 0x2f3338 : suitHex);
    const root = new THREE.Group(); root.scale.setScalar(1.25 * size);
    if (L.build) root.scale.y *= L.build[1];
    const hips = new THREE.Group(); hips.position.y = 0.95; root.add(hips);
    hips.add(part(body.pelvis, trousers));
    const leg = (side) => {
      const hip = new THREE.Group(); hip.position.set(side * 0.11, -0.04, 0);
      const knee = new THREE.Group(); knee.position.y = -0.44;
      knee.add(part(body.shin, trousers), part(body.shoe, shoeMat));
      hip.add(part(body.thigh, trousers, true), knee); hips.add(hip);
      return { hip, knee };
    };
    const torso = new THREE.Group(); torso.position.y = 0.06; hips.add(torso);
    if (L.build) torso.scale.x = L.build[0];
    torso.add(part(body.torso, suit, true), part(body.neck, skin));
    if (!L.casual) torso.add(part(body.shirt, mat(0xffffff)), L.glow
      ? new THREE.Mesh(body.tie, std({ color: L.tie, emissive: L.tie, emissiveIntensity: 1.6, roughness: 0.3 }))
      : part(body.tie, mat(L.tie != null ? L.tie : C(teamColor).getHex())));
    if (L.glow) { const pin = new THREE.Mesh(new THREE.SphereGeometry(0.03, 8, 6), std({ color: 0xffd98a, emissive: 0xffd98a, emissiveIntensity: 2.2 })); pin.position.set(-0.12, 0.5, 0.2); torso.add(pin);
      const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex("255,217,138"), transparent: true, depthWrite: false, opacity: 0.35 })); halo.scale.setScalar(0.9); halo.position.set(0, 0.4, 0.1); torso.add(halo); }
    const head = new THREE.Group(); head.position.y = 0.86; torso.add(head);
    head.add(part(body.head, skin, true));
    const style = L.hair || "short";
    if (style !== "bald") { const hm = part(body.hair, hair); if (style === "crop") hm.scale.y = 0.72; head.add(hm); }
    if (style === "long") { const back = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.34, 0.16), hair); back.position.set(0, -0.12, -0.12); head.add(back); }
    if (style === "bun") { const bun = new THREE.Mesh(new THREE.SphereGeometry(0.085, 8, 6), hair); bun.position.set(0, 0.17, -0.12); head.add(bun); }
    if (L.glasses || L.sunglasses) {
      const lens = L.sunglasses ? mat(0x111111) : std({ color: 0xffffff, transparent: true, opacity: 0.35, roughness: 0.1 });
      for (const s of [-1, 1]) { const r = new THREE.Mesh(L.sunglasses ? new THREE.BoxGeometry(0.09, 0.055, 0.02) : new THREE.TorusGeometry(0.045, 0.008, 6, 12), lens); r.position.set(s * 0.075, 0.02, 0.17); head.add(r); }
      const bridge = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.008, 0.008), mat(0x2a2a2a)); bridge.position.set(0, 0.02, 0.175); head.add(bridge);
    }
    const arm = (side) => {
      const sh = new THREE.Group(); sh.position.set(side * 0.29, 0.52, 0); sh.rotation.z = side * 0.08;
      const el = new THREE.Group(); el.position.y = -0.3;
      el.add(part(body.fore, suit), part(body.hand, skin));
      sh.add(part(body.upper, suit), el); torso.add(sh);
      return { sh, el };
    };
    const tablet = part(body.tablet, M.frame); tablet.visible = false;
    const racket = part(body.racket, mat(0x222222)); racket.visible = false;
    const c = { root, hips, legs: [leg(-1), leg(1)], torso, head, arms: [arm(-1), arm(1)], tablet, racket, prop: null, look: L };
    c.arms[1].el.add(tablet, racket);
    if (L.prop) { c.prop = propMesh(L.prop, C(teamColor).getHex()); c.arms[1].el.add(c.prop); }
    return c;
  }
  // A floating name over a character's head; fades out when the view is far away (the chips name the buildings then).
  function nameTag(c, label, color) {
    const tex = canvasTex(256, 64, (g, w, h) => {
      g.fillStyle = "rgba(14, 20, 32, 0.78)"; g.beginPath(); g.roundRect(4, 8, w - 8, h - 16, 18); g.fill();
      g.strokeStyle = color; g.lineWidth = 3; g.stroke();
      g.fillStyle = "#f3efe6"; g.font = "600 26px 'Inter', system-ui, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(label, w / 2, h / 2 + 1);
    });
    const tag = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: false }));
    tag.scale.set(1.3, 0.33, 1); tag.position.set(0, 2.05, 0); tag.renderOrder = 5;
    c.root.add(tag); c.tag = tag;
  }
  const lerp = (a, b, t) => a + (b - a) * t;
  // Pose from blend weights: walk, sit, type (hands on the keys), lean (+ back when relaxed, - slumped when blocked),
  // look (waiting: watching for the hand-off), tab (tablet), swing (tennis).
  function pose(c, st, ph) {
    const a = st.walk, s = st.sit, ty = st.type, w = st.walkPhase, calm = REDUCED ? 0 : 1;
    const swing = Math.sin(w) * 0.55 * a;
    c.legs[0].hip.rotation.x = lerp(swing, -1.5, s);
    c.legs[1].hip.rotation.x = lerp(-swing, -1.5, s);
    c.legs[0].knee.rotation.x = lerp(Math.max(0, Math.cos(w)) * 0.85 * a, 1.5, s);
    c.legs[1].knee.rotation.x = lerp(Math.max(0, -Math.cos(w)) * 0.85 * a, 1.5, s);
    c.hips.position.y = lerp(0.95 + Math.abs(Math.cos(w)) * 0.035 * a - 0.02 * a, 0.6, s);
    c.torso.scale.y = 1 + Math.sin(ph * 1.7) * 0.01 * calm;
    c.torso.rotation.x = 0.04 * a + 0.12 * ty - 0.16 * Math.max(0, st.lean) * s + 0.22 * Math.max(0, -st.lean);
    c.torso.rotation.y = Math.sin(w) * 0.08 * a + Math.sin(ph * 2.4) * 0.5 * st.swing * calm;
    const tap = (Math.sin(ph * 11) * 0.5 + Math.sin(ph * 17.3) * 0.5) * 0.08 * calm;
    c.arms.forEach((arm, i) => {
      const side = i ? 1 : -1;
      const rest = lerp(0.04, -0.32, s), restEl = lerp(-0.12, -0.9, s);
      let sh = lerp(rest, -0.55, ty), el = lerp(restEl, -1.1, ty) + tap * ty * side;
      sh = lerp(sh, -side * Math.sin(w) * 0.5, a); el = lerp(el, -0.3, a);
      sh = lerp(sh, -0.5, st.tab); el = lerp(el, -1.35 + tap * 0.4 * side, st.tab);
      if (i === 1) { sh = lerp(sh, -1.2 + Math.sin(ph * 2.4) * 0.9 * calm, st.swing); el = lerp(el, -0.4, st.swing); }
      arm.sh.rotation.x = sh; arm.el.rotation.x = el;
    });
    c.tablet.visible = st.tab > 0.5;
    c.racket.visible = st.swing > 0.5;
    if (c.prop) c.prop.visible = s < 0.5 && st.tab < 0.5 && st.swing < 0.5;
    c.head.rotation.x = 0.1 * ty + 0.32 * Math.max(0, -st.lean) - 0.12 * st.look + 0.18 * st.tab + Math.sin(ph * 0.6) * 0.03 * calm;
    c.head.rotation.y = Math.sin(ph * 0.31) * (0.06 + 0.25 * Math.max(0, st.lean) * (1 - a) + 0.35 * st.look) * calm;
  }

  // An actor walks a path of points, then sits, stands facing a way, plays, or leaves.
  const actors = [];
  const wrap = (x) => Math.atan2(Math.sin(x), Math.cos(x));
  function actor(c, from, face) {
    scene3.add(c.root);
    c.root.position.copy(from); c.root.rotation.y = face;
    const A = { c, path: [], then: null, seat: null, sitting: false, face, status: "idle", grow: 0, gone: false, wait: 0, activity: null,
      phase: Math.random() * 10, size: c.root.scale.x, st: { walk: 0, walkPhase: 0, sit: 0, type: 0, lean: 0, look: 0, tab: 0, swing: 0 } };
    c.root.scale.setScalar(0.001);
    actors.push(A);
    return A;
  }
  function walk(A, points, then) { A.path = points.map((p) => p.clone()); A.then = then || null; A.sitting = false; A.activity = null; }
  const sitDown = (A) => { A.sitting = true; A.face = A.seat.face; };
  const tmp2 = new V3();
  function stepActor(A, dt, time) {
    const r = A.c.root, st = A.st;
    if (A.wait > 0) { A.wait -= dt; r.scale.setScalar(0.001); return; }
    A.grow = damp(A.grow, A.gone ? 0 : 1, 5, dt);
    r.scale.setScalar(Math.max(0.001, A.grow * A.size));
    let moving = false;
    if (A.path.length) {
      const d = tmp2.copy(A.path[0]).sub(r.position); const dy = d.y; d.y = 0;
      const dist = d.length();
      r.position.y += dy * Math.min(1, dt * 5);   // steps up into a pavilion and down again
      if (dist < 0.08) A.path.shift();
      else {
        moving = true;
        r.position.addScaledVector(d.normalize(), Math.min(dist, 3.2 * (0.35 + 0.65 * st.walk) * dt));
        A.face = Math.atan2(d.x, d.z);
      }
      if (!A.path.length && A.then) { const next = A.then; A.then = null; next(A); }
    }
    if (A.sitting && A.seat) r.position.lerp(A.seat.pos, 1 - Math.exp(-7 * dt));
    r.rotation.y += wrap(A.face - r.rotation.y) * (1 - Math.exp(-7 * dt));
    st.walk = damp(st.walk, moving ? 1 : 0, 6, dt);
    if (st.walk > 0.01) st.walkPhase += dt * 7.2 * (0.5 + 0.5 * st.walk);
    st.sit = damp(st.sit, A.sitting && !moving ? 1 : 0, 5, dt);
    const atDesk = A.sitting && st.sit > 0.6 && A.desk && A.seat === A.desk.seat;
    st.type = damp(st.type, atDesk && A.status === "working" ? 1 : 0, 4, dt);
    st.lean = damp(st.lean, A.sitting ? (atDesk && A.status === "blocked" ? -1 : atDesk && A.status !== "idle" ? 0 : 1) : 0, 2, dt);
    st.look = damp(st.look, atDesk && A.status === "waiting" ? 1 : 0, 2.5, dt);
    st.tab = damp(st.tab, A.helper && !moving && !A.gone ? 1 : 0, 4, dt);
    st.swing = damp(st.swing, !moving && A.activity === "tennis" ? 1 : 0, 4, dt);
    pose(A.c, st, A.phase + time);
  }

  // ---------------------------------------------------------------- pavilions: one per team, round the plaza
  // One pavilion per agent, as the old office had one room per agent, placed so each team's agents sit together.
  // Two curved rows round the plaza, back (by the HQ) to front, as in the rendered estate.
  const SITES = {
    "market-research": [-40, -22], "product-research": [-54, -6], "creative-research": [-60, 12], "operations": [-60, 30], "mentor-learning": [-54, 46], "accountant": [-42, 62],
    "meta-ads-auditor": [40, -22], "meta-ads": [54, -6], "shopify": [60, 12], "creative-director": [60, 30], "store-qa": [54, 46], "product-testing": [42, 62],
  };
  const PW = 20, PD = 13, PH = 7;
  const pavilions = new Map();
  const hitTargets = [];
  const lampGlow = glowTex("255,255,255");
  function screenTexture(w = 512, h = 300) {
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
    return { c, t };
  }
  function wrapText(g, text, x, y, maxW, lineH, maxLines) {
    const words = String(text).split(/\s+/); let line = "", n = 0;
    for (const word of words) {
      const test = line ? line + " " + word : word;
      if (g.measureText(test).width > maxW && line) { g.fillText(n === maxLines - 1 ? line + "…" : line, x, y); y += lineH; line = word; if (++n >= maxLines) return; }
      else line = test;
    }
    if (line) g.fillText(line, x, y);
  }
  function drawScreen(sc, team, a) {
    const g = sc.c.getContext("2d"), W = sc.c.width, H = sc.c.height, st = STATUS[a.status] || STATUS.idle;
    g.fillStyle = "#0f1b2c"; g.fillRect(0, 0, W, H);
    g.fillStyle = team.color; g.fillRect(0, 0, W, 8);
    g.fillStyle = "#ffffff"; g.font = "600 30px 'Instrument Sans', sans-serif"; g.fillText(a.label, 26, 58);
    g.fillStyle = st.color; g.beginPath(); g.arc(W - 40, 48, 10, 0, Math.PI * 2); g.fill();
    g.font = "500 20px 'Instrument Sans', sans-serif"; g.fillText(st.word, W - 40 - g.measureText(st.word).width - 20, 55);
    g.fillStyle = a.now ? "#e8eef7" : "#8796ab"; g.font = "400 24px 'Instrument Sans', sans-serif";
    wrapText(g, a.now || (a.next ? `Next up: ${a.next}` : "Nothing running"), 26, 108, W - 52, 34, 4);
    g.fillStyle = "#8796ab"; g.font = "400 19px 'Instrument Sans', sans-serif";
    g.fillText(a.status === "working" ? (a.tool ? `Using ${a.tool}` : "Tool not reported yet") : `${(a.open || []).length} open tasks`, 26, H - 26);
    sc.t.needsUpdate = true;
  }
  // The pavilion's wall display shows the team's real numbers.
  function drawWall(sc, t) {
    const g = sc.c.getContext("2d"), W = sc.c.width, H = sc.c.height;
    g.fillStyle = "#0d1826"; g.fillRect(0, 0, W, H);
    g.fillStyle = "#c6a46a"; g.fillRect(0, 0, W, 6);
    g.fillStyle = "#fff"; g.font = "500 54px 'Bodoni Moda', Georgia, serif"; g.fillText(t.name, 34, 82);
    const open = t.agents.reduce((n, a) => n + a.open.length, 0), working = t.agents.filter((a) => a.status === "working").length;
    g.font = "500 30px 'Instrument Sans', sans-serif"; g.fillStyle = "#d8e2ee";
    g.fillText(`${open} open task${open === 1 ? "" : "s"}   ${working} of ${t.agents.length} working`, 34, 136);
    g.fillStyle = "#9fb0c4"; g.font = "400 26px 'Instrument Sans', sans-serif";
    const lead = t.agents.find((a) => a.status === "working") || t.agents[0];
    wrapText(g, lead?.now || (lead?.next ? `Next up: ${lead.next}` : "Nothing running right now"), 34, 186, W - 68, 34, 2);
    sc.t.needsUpdate = true;
  }
  function desk(team, agentName, pav, x, z) {
    const grp = new THREE.Group(); grp.position.set(x, 0.6, z); pav.add(grp);
    grp.scale.setScalar(1.25);
    const top = new THREE.Mesh(new THREE.BoxGeometry(2.5, 0.09, 1.15), M.walnut); top.position.y = 0.8; top.castShadow = !simple; grp.add(top);
    for (const sx of [-1.1, 1.1]) { const leg = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.72, 0.9), M.gold); leg.position.set(sx, 0.37, 0); grp.add(leg); }
    const sc = screenTexture();
    const mon = new THREE.Group(); mon.position.set(0, 1.3, -0.3);
    const bezel = new THREE.Mesh(new THREE.BoxGeometry(1.62, 0.98, 0.05), M.frame);
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.54, 0.9), new THREE.MeshBasicMaterial({ map: sc.t, toneMapped: false }));
    screen.position.z = 0.03;
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.05, 0.3), M.gold); stem.position.y = -0.5;
    mon.add(bezel, screen, stem); grp.add(mon);
    const chair = new THREE.Group(); chair.position.set(0, 0, 0.95);
    const cushion = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.14, 0.75), M.leather); cushion.position.y = 0.55;
    const back = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.85, 0.12), M.leather); back.position.set(0, 1.0, 0.36);
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.3, 0.5, 12), M.gold); base.position.y = 0.25;
    chair.add(cushion, back, base); grp.add(chair);
    const bulbMat = std({ color: 0xffffff, emissive: 0x9aa6b5, emissiveIntensity: 1.2 });
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.1, 16, 12), bulbMat); bulb.position.set(1.0, 1.44, -0.35);
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: lampGlow, color: 0x9aa6b5, transparent: true, depthWrite: false, opacity: 0.6 }));
    glow.position.copy(bulb.position); glow.scale.setScalar(0.9);
    grp.add(bulb, glow);
    // Where its agent sits, in world terms: facing its screen, back to the plaza.
    grp.updateWorldMatrix(true, true);
    const seatPos = chair.getWorldPosition(new V3()); seatPos.y = 0.6;
    grp.traverse((o) => { o.userData.pick = team.id; o.userData.desk = agentName; });
    return { name: agentName, grp, sc, bulbMat, glow, seat: { pos: seatPos, face: pav.rotation.y + Math.PI }, pav, x, z, status: "idle", phase: rand() * 10 };
  }
  // A point in a pavilion's own frame (x along the front, z towards the plaza), on its floor, in world terms.
  const local = (pav, x, z, y = 0.6) => pav.localToWorld(new V3(x, 0, z)).setY(y);
  function buildPavilion(t) {
    const [x, z] = SITES[t.id] || [0, 64];
    const pav = new THREE.Group(); pav.position.set(x, 0, z);
    pav.rotation.y = Math.atan2(PLAZA.x - x, PLAZA.z - z);   // the glass front faces the plaza
    scene3.add(pav);
    box(PW + 2, 0.6, PD + 2, M.marble, 0, 0.3, 0, pav);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(PW, PD), M.oak); floor.rotation.x = -Math.PI / 2; floor.position.y = 0.62; floor.receiveShadow = !simple; pav.add(floor);
    // Roof: a white slab with a champagne-gold fascia, and a planted roof terrace with a glass balustrade.
    const roofY = PH + 0.6;
    box(PW + 2.4, 0.8, PD + 2.4, M.marble, 0, roofY, 0, pav);
    for (const oz of [PD / 2 + 1.22, -(PD / 2) - 1.22]) box(PW + 2.5, 0.22, 0.12, M.gold, 0, roofY, oz, pav, false);
    for (const ox of [PW / 2 + 1.22, -(PW / 2) - 1.22]) box(0.12, 0.22, PD + 2.5, M.gold, ox, roofY, 0, pav, false);
    const deck = new THREE.Mesh(new THREE.PlaneGeometry(PW - 1, PD - 1), M.ivory); deck.rotation.x = -Math.PI / 2; deck.position.y = roofY + 0.41; pav.add(deck);
    box(PW + 1.6, 0.9, 0.08, M.glass, 0, roofY + 0.85, PD / 2 + 0.8, pav, false);
    for (const px of [-PW / 2 + 1.5, -PW / 2 + 4, PW / 2 - 1.5]) { const shrub = new THREE.Mesh(new THREE.IcosahedronGeometry(0.85, 0), M.leaf); shrub.position.set(px, roofY + 1.2, -PD / 2 + 1.4); shrub.castShadow = !simple; pav.add(shrub); }
    const parasol = new THREE.Mesh(new THREE.ConeGeometry(1.6, 0.6, 12), M.white); parasol.position.set(PW / 2 - 4, roofY + 2.6, 0); parasol.castShadow = !simple; pav.add(parasol);
    box(0.08, 2.2, 0.08, M.gold, PW / 2 - 4, roofY + 1.5, 0, pav, false);
    box(PW, 0.12, 0.16, M.gold, 0, PH * 0.72 + 0.6, PD / 2 + 0.06, pav, false);   // transom band across the glass front
    // Upper storey: a smaller glass pavilion set back on the terrace, with its own slab and fascia.
    const UW = PW - 8, UD = PD - 6, UH = 4.4, uy = roofY + 0.4, uz = -1.8;
    box(UW - 0.3, UH - 0.3, 0.1, M.interior, 0, uy + UH / 2, uz - UD / 2 - 0.06, pav, false);
    box(UW, UH, 0.1, M.glass, 0, uy + UH / 2, uz + UD / 2, pav, false);
    box(0.1, UH, UD, M.glass, -UW / 2, uy + UH / 2, uz, pav, false); box(0.1, UH, UD, M.glass, UW / 2, uy + UH / 2, uz, pav, false);
    for (const [cx, cz] of [[-UW / 2, uz - UD / 2], [UW / 2, uz - UD / 2], [-UW / 2, uz + UD / 2], [UW / 2, uz + UD / 2]]) box(0.5, UH, 0.5, M.white, cx, uy + UH / 2, cz, pav);
    box(UW + 1.6, 0.6, UD + 1.6, M.marble, 0, uy + UH + 0.3, uz, pav);
    box(UW + 1.7, 0.18, 0.1, M.gold, 0, uy + UH + 0.3, uz + UD / 2 + 0.82, pav, false);
    // Lit soffit under the front overhang, for the evening.
    box(PW + 1.8, 0.06, 0.5, M.lampGlass, 0, roofY - 0.43, PD / 2 + 0.75, pav, false);
    // Corner columns and glass walls; the front has a door in the middle.
    for (const [cx, cz] of [[-PW / 2, -PD / 2], [PW / 2, -PD / 2], [-PW / 2, PD / 2], [PW / 2, PD / 2]]) box(0.7, PH, 0.7, M.white, cx, PH / 2 + 0.6, cz, pav);
    box(PW - 0.4, PH - 0.4, 0.1, M.interior, 0, PH / 2 + 0.6, -PD / 2 - 0.08, pav, false);   // warm-lit back wall
    box(PW, PH, 0.1, M.glass, 0, PH / 2 + 0.6, -PD / 2, pav, false);
    box(0.1, PH, PD, M.glass, -PW / 2, PH / 2 + 0.6, 0, pav, false);
    box(0.1, PH, PD, M.glass, PW / 2, PH / 2 + 0.6, 0, pav, false);
    box(PW / 2 - 2, PH, 0.1, M.glass, -(PW / 4 + 1), PH / 2 + 0.6, PD / 2, pav, false);
    box(PW / 2 - 2, PH, 0.1, M.glass, PW / 4 + 1, PH / 2 + 0.6, PD / 2, pav, false);
    for (const gx of [-PW / 2, -2, 2, PW / 2]) box(0.14, PH, 0.2, M.gold, gx, PH / 2 + 0.6, PD / 2 + 0.05, pav, false);
    // Warm ceiling light, and the wall display with the team's real numbers.
    const light = new THREE.Mesh(new THREE.PlaneGeometry(PW - 3, 0.5), M.warmLight); light.rotation.x = Math.PI / 2; light.position.set(0, PH + 0.2, PD / 2 - 1.4); pav.add(light);
    const wall = screenTexture(768, 256);
    const wallMesh = new THREE.Mesh(new THREE.PlaneGeometry(13, 4.3), new THREE.MeshBasicMaterial({ map: wall.t, toneMapped: false }));
    wallMesh.position.set(0, 4.0, -PD / 2 + 0.2); pav.add(wallMesh);
    box(13.3, 4.6, 0.1, M.frame, 0, 4.0, -PD / 2 + 0.12, pav, false);
    // The team colour on a slim band above the door, and plants on the roof.
    box(4, 0.35, 0.12, std({ color: t.color, roughness: 0.4 }), 0, PH + 0.1, PD / 2 + 0.12, pav, false);
    for (const px of [-PW / 2 + 1, PW / 2 - 1]) { const plant = new THREE.Mesh(new THREE.IcosahedronGeometry(0.9, 0), M.leaf); plant.position.set(px, roofY + 1, PD / 2 - 0.2); pav.add(plant); }
    pav.updateWorldMatrix(true, true);
    const n = t.agents.length;
    const offsets = { 1: [0], 2: [-3.4, 3.4], 3: [-6, 0, 6] }[n] || t.agents.map((_, i) => (i - (n - 1) / 2) * 5);
    const desks = t.agents.map((a, i) => desk(t, a.name, pav, offsets[i], -1.6));
    if (n === 1) for (const wx of [-5.6, 5.6]) workstation(pav, wx, 0.62, -1.6, 0, 1.1);   // the rest of the back row
    // The rest of the office (furniture, not agents' desks): workstations along the side walls, a meeting table, plants.
    for (const [wx, wz, wr] of [[8.6, 2.2, -Math.PI / 2], [8.6, 5.0, -Math.PI / 2], [-8.6, 2.4, Math.PI / 2]]) workstation(pav, wx, 0.62, wz, wr, 1.1);
    const table = new THREE.Mesh(new THREE.CylinderGeometry(1.2, 1.2, 0.08, 24), M.walnut); table.position.set(-6.6, 1.4, 5.6); pav.add(table);
    box(0.12, 0.75, 0.12, M.gold, -6.6, 1.0, 5.6, pav, false);
    for (let k = 0; k < 4; k++) { const a = (k / 4) * Math.PI * 2 + 0.4; box(0.55, 0.5, 0.55, M.leather, -6.6 + Math.cos(a) * 1.7, 0.88, 5.6 + Math.sin(a) * 1.7, pav, false); }
    for (const [px, pz] of [[-9, -5.6], [9, -5.6], [9.2, 6.2], [-9.3, 2.0]]) { box(0.8, 0.8, 0.8, M.white, px, 1.0, pz, pav, false); const pl = new THREE.Mesh(new THREE.IcosahedronGeometry(0.7, 0), M.leaf); pl.position.set(px, 1.9, pz); pav.add(pl); }
    for (const ux of [-3.2, 3.2]) workstation(pav, ux, uy + 0.02, uz + 0.6, 0, 1.05);   // the upper floor
    // Outside: a flower bed along the front, cypresses at the back corners, and a lit path to the plaza.
    for (let bx = -PW / 2 - 0.5; bx <= PW / 2 + 0.5; bx += 1.1) if (Math.abs(bx) > 2.6) flower(pav, bx, 0.3, PD / 2 + 2.3);
    for (const cx of [-(PW / 2) - 1.8, PW / 2 + 1.8]) prop("cypress", pav, cx, 0, -PD / 2 - 1.4, 0, 0.85);
    for (const sx of [-1, 1]) { olive(pav, sx * (PW / 2 + 3.2), 0, 2, 0.8 + rand() * 0.3); for (let k = 0; k < 7; k++) flower(pav, sx * (PW / 2 + 2 + rand() * 2.5), 0.3, -PD / 2 + 1 + rand() * (PD - 2)); }
    box(4.4, 0.05, 10, M.ivory, 0, 0.03, PD / 2 + 7, pav, false);
    for (const bz of [PD / 2 + 4, PD / 2 + 8, PD / 2 + 12]) for (const bx of [-2.7, 2.7]) { prop("bollard", pav, bx, 0, bz); prop("bollardLamp", pav, bx, 0, bz); }
    // Walk mode: the glass walls (a door in the front), columns, furniture and planting are solid; the floor is raised.
    const inv = pav.matrixWorld.clone().invert();
    {
      const W = (lx, lz) => { const p = pav.localToWorld(new V3(lx, 0, lz)); return [p.x, p.z]; };
      const seg = (ax, az, bx, bz) => solid.seg(...W(ax, az), ...W(bx, bz)), circ = (lx, lz, r) => solid.circle(...W(lx, lz), r);
      seg(-PW / 2, -PD / 2, PW / 2, -PD / 2); seg(-PW / 2, -PD / 2, -PW / 2, PD / 2); seg(PW / 2, -PD / 2, PW / 2, PD / 2);
      seg(-PW / 2, PD / 2, -2, PD / 2); seg(2, PD / 2, PW / 2, PD / 2);
      for (const [cx, cz] of [[-PW / 2, -PD / 2], [PW / 2, -PD / 2], [-PW / 2, PD / 2], [PW / 2, PD / 2]]) circ(cx, cz, 0.6);
      offsets.forEach((ox) => circ(ox, -1.6, 1.7));
      if (n === 1) for (const wx of [-5.6, 5.6]) circ(wx, -1.6, 1.3);
      for (const [wx, wz] of [[8.6, 2.2], [8.6, 5.0], [-8.6, 2.4]]) circ(wx, wz, 1.3);
      circ(-6.6, 5.6, 2.1);
      for (const [px, pz] of [[-9, -5.6], [9, -5.6], [9.2, 6.2], [-9.3, 2.0]]) circ(px, pz, 0.7);
      for (const cx of [-(PW / 2) - 1.8, PW / 2 + 1.8]) circ(cx, -PD / 2 - 1.4, 0.5);
      for (const bz of [PD / 2 + 4, PD / 2 + 8, PD / 2 + 12]) for (const bx of [-2.7, 2.7]) circ(bx, bz, 0.3);
      const lp = new V3();
      solid.floors.push((x, z) => { lp.set(x, 0, z).applyMatrix4(inv); return Math.abs(lp.x) <= PW / 2 + 1 && Math.abs(lp.z) <= PD / 2 + 1 ? 0.62 : null; });
    }
    const door = local(pav, 0, PD / 2 + 2.5, 0);
    const P = { team: t, pav, desks, wall, door, inv, anchor: pav.localToWorld(new V3(0, PH + 9.5, -1.8)), status: "idle", helpers: [], hires: [] };
    pav.traverse((o) => { if (!o.userData.pick) o.userData.pick = t.id; });
    hitTargets.push(pav);
    // Each agent comes out of its pavilion door and takes its seat.
    desks.forEach((d, i) => {
      const A = actor(character(d.name, t.color), door, pav.rotation.y + Math.PI);
      A.name = d.name; A.desk = d; d.actor = A;
      nameTag(A.c, t.agents.find((x) => x.name === d.name)?.label || d.name, t.color);
      goToDesk(A, P, REDUCED ? 0 : 0.5 + i * 0.6 + rand() * 1.5);
    });
    pavilions.set(t.id, P);
    return P;
  }
  function goToDesk(A, P, delay = 0) {
    const d = A.desk; A.seat = d.seat; A.wait = delay; A.away = false;
    if (A.leisure) { A.leisure.taken = false; A.leisure = null; }
    walk(A, [P.door, local(P.pav, 0, 4.6), local(P.pav, d.x, 1.8), local(P.pav, d.x, 0.9)], sitDown);
  }

  // ---------------------------------------------------------------- idle life: leisure round the estate
  // An agent with no live task leaves its desk for the tennis court, the pool, the café terrace or the railing over
  // the circuit; the moment real work arrives it walks back. It is plainly leisure: panels say "idle" and where they are.
  const LEISURE = [
    ...[[72, 50, Math.PI / 2], [92, 50, -Math.PI / 2]].map(([x, z, f]) => ({ kind: "tennis", label: "on the tennis court", pos: new V3(x, 0.1, z), face: f })),
    ...Array.from({ length: 6 }, (_, i) => ({ kind: "pool", label: "by the pool", pos: new V3(-91 + i * 4.4, 0.7, 58.2), face: Math.PI, sit: true })),
    ...Array.from({ length: 6 }, (_, i) => ({ kind: "rail", label: "watching the cars", pos: new V3(-92 + i * 5.5, 0, -62.5), face: Math.PI })),
    ...Array.from({ length: 4 }, (_, i) => ({ kind: "cafe", label: "on the café terrace", pos: new V3(-8 + i * 5.3, 0, 31.5), face: Math.PI })),
  ];
  function goRelax(A, P) {
    const free = LEISURE.filter((l) => !l.taken);
    if (!free.length) return;
    const spot = free[hash(A.name) % free.length];
    spot.taken = true; A.leisure = spot; A.away = true;
    const side = Math.sign(spot.pos.x - PLAZA.x) || 1;
    walk(A, [local(P.pav, A.desk.x, 1.8), local(P.pav, 0, 4.6), P.door, new V3(PLAZA.x + side * 23, 0, spot.pos.z > PLAZA.z ? PLAZA.z + 12 : PLAZA.z - 12), spot.pos], (x) => {
      x.face = spot.face; x.activity = spot.kind;
      if (spot.sit) { x.seat = { pos: spot.pos, face: spot.face }; x.sitting = true; }
    });
  }

  // ---------------------------------------------------------------- live hand-offs: a pulse from the globe to the pavilion
  const pulses = [];
  const pulseMat = new THREE.SpriteMaterial({ map: glowTex("255,236,200"), transparent: true, depthWrite: false });
  function dispatch(agentName) {
    const P = [...pavilions.values()].find((x) => x.desks.some((d) => d.name === agentName));
    const target = P ? P.anchor.clone().setY(PH) : new V3(0, 20, -38);
    const sprite = new THREE.Sprite(pulseMat.clone()); sprite.scale.setScalar(5); scene3.add(sprite);
    pulses.push({ sprite, from: globe.position.clone(), to: target, t: 0 });
    coreKick = 1;
  }

  // ---------------------------------------------------------------- sub-agents and new hires
  // More than one live run for an agent means JARVIS has sub-agents on it: they join its desk with tablets, then leave.
  function helpers(P, d, want, demo) {
    const mine = P.helpers.filter((h) => h.lead === d && h.demo === demo && !h.leaving);
    while (mine.length < Math.min(want, 3)) {
      const i = P.helpers.filter((h) => h.lead === d && !h.leaving).length;
      const A = actor(character(`${d.name}-sub-${i}-${demo}`, P.team.color, 0.92), P.door, P.pav.rotation.y + Math.PI);
      A.helper = true; A.lead = d; A.demo = demo; A.status = "working";
      walk(A, [local(P.pav, 0, 4.6), local(P.pav, d.x, 3.4), local(P.pav, d.x + [-1.4, 1.4, 0][i % 3], 2.6)], (x) => { x.face = P.pav.rotation.y + Math.PI; });
      P.helpers.push(A); mine.push(A);
    }
    mine.slice(Math.max(0, want)).forEach((A) => leave(P, A));
  }
  function leave(P, A) {
    if (A.leaving) return;
    A.leaving = true;
    walk(A, [P.door], (x) => { x.gone = true; });
  }
  // A new permanent agent: a desk appears in the pavilion and its agent walks in to it.
  function hire(P, demo) {
    const used = P.desks.map((d) => d.x).concat(P.hires.map((h) => h.desk.x));
    const x = [7.5, -7.5, 3.4, -3.4, 0].find((o) => used.every((u) => Math.abs(u - o) > 3.3));
    if (x === undefined) return;
    const d = desk(P.team, demo ? "new-hire-demo" : "new-hire", P.pav, x, -1.6);
    d.grp.scale.setScalar(0.001);
    drawScreen(d.sc, P.team, { label: demo ? "New hire (demo)" : "New hire", status: "working", now: "Setting up: reading the team's playbooks", tool: null, open: [] });
    d.bulbMat.emissive.copy(C(STATUS.working.color)); d.glow.material.color.copy(C(STATUS.working.color));
    const A = actor(character(`hire-${P.team.id}-${x}`, P.team.color), P.door, P.pav.rotation.y + Math.PI);
    A.status = "working"; A.desk = d; A.name = "new hire";
    goToDesk(A, P, 1.2);
    P.hires.push({ desk: d, actor: A, demo, grow: 0, gone: false });
  }
  let demoTimers = [];
  function demo(on) {
    demoTimers.forEach(clearTimeout); demoTimers = [];
    const cr = pavilions.get("creative-director"), rs = pavilions.get("market-research");
    if (!cr || !rs) return;
    if (on) {
      const later = (ms, fn) => demoTimers.push(setTimeout(fn, ms));
      dispatch("creative-director");
      later(700, () => helpers(cr, cr.desks[0], 2, true));
      later(2600, () => dispatch("market-research"));
      later(3200, () => helpers(rs, rs.desks[0], 1, true));
      later(6500, () => hire(cr, true));
      focus("creative");
    } else {
      for (const P of pavilions.values()) {
        P.helpers.filter((h) => h.demo).forEach((h) => leave(P, h));
        P.hires.filter((h) => h.demo && !h.gone).forEach((h) => { h.gone = true; walk(h.actor, [P.door], (x) => { x.gone = true; }); });
      }
    }
  }

  // ---------------------------------------------------------------- rewards (plan section 16, parked)
  // reward(kind) is the hook for unlocks earned on real strong sales days (for example a car for the garage). The
  // page calls it with each unlocked reward from /api/hq; nothing is unlocked until the rules are agreed, so it is a no-op.
  const rewards = new Set();
  function reward(kind) { if (!kind || rewards.has(kind)) return; rewards.add(kind); }

  // ---------------------------------------------------------------- data in
  let built = false, coreKick = 0, jarvisBusy = 0;
  function update(m) {
    const sites = m.departments || m.teams;
    if (!built) { sites.forEach(buildPavilion); buildProps(); built = true; }
    for (const t of sites) {
      const P = pavilions.get(t.id);
      if (!P) continue;
      P.team = t; P.status = t.status;
      const key = JSON.stringify(t.agents.map((a) => [a.status, a.now, a.next, a.open.length]));
      if (key !== P.key) { P.key = key; drawWall(P.wall, t); }
      t.agents.forEach((a) => {
        const d = P.desks.find((x) => x.name === a.name);
        if (!d) return;
        const k2 = JSON.stringify([a.status, a.now, a.next, a.tool, a.open.length]);
        if (k2 !== d.key) { d.key = k2; drawScreen(d.sc, t, a); }
        d.status = a.status;
        if (d.actor) d.actor.status = a.status;
        const c = C(STATUS[a.status].color);
        d.bulbMat.emissive.copy(c); d.glow.material.color.copy(c);
        helpers(P, d, Math.max(0, a.runs.length - 1), false);
      });
    }
    jarvisBusy = m.jarvis.status === "idle" ? 0 : m.jarvis.status === "delegating" ? 1 : 0.6;
    const ck = JSON.stringify([m.week, m.counts]);
    if (ck !== ceoKey) { ceoKey = ck; drawCeo(m); }
    syncErrands(m);
  }
  let ceoKey = "";
  // Where each agent is right now, for the panels: at the desk, walking, or where it is taking its break.
  function whereabouts() {
    const out = {};
    for (const P of pavilions.values()) for (const d of P.desks) {
      const A = d.actor; if (!A) continue;
      out[d.name] = A.errand ? (A.errand.stage === "asking" ? "in your office with an approval" : A.errand.stage === "back" ? "coming back from your office" : "on the way to your office with an approval")
        : A.path.length ? (A.away ? "heading out for a break" : "walking to the desk") : A.leisure ? A.leisure.label : "at the desk";
    }
    return out;
  }

  // ---------------------------------------------------------------- the owner: at his desk in the penthouse, or out walking
  const boss = actor(character("owner", 0xc6a46a), ceoSeat.pos, 0);
  boss.seat = ceoSeat; boss.sitting = true; boss.name = "owner";
  nameTag(boss.c, "You", "#c6a46a");
  // JARVIS at his own desk on floor 2; he walks up with his own approvals.
  const jarvisA = actor(character("jarvis", 0xc6a46a), jarvisSeat.pos, Math.PI);
  jarvisA.seat = jarvisSeat; jarvisA.sitting = true; jarvisA.name = "jarvis";
  nameTag(jarvisA.c, "JARVIS", "#ffd98a");
  // Blake and Mark, the method personas, on the first-floor terrace of the HQ (figures of their own, not likenesses).
  const personas = ["blake", "mark"].map((who, i) => {
    const P = actor(character(who, who === "blake" ? 0x2f8f83 : 0x3a3f46), HQ.localToWorld(new V3(-5 + i * 10, 9.1, 10.6)), 0);
    P.name = who; nameTag(P.c, who === "blake" ? "Blake" : "Mark", who === "blake" ? "#2f8f83" : "#9aa3ab");
    return P;
  });

  // ---------------------------------------------------------------- errands: an agent brings an approval to the owner's office
  // A pending approval sends the agent whose task it is (JARVIS for his own) out of its office, round the plaza, up the
  // HQ steps and into the lift; it reappears at the penthouse lift door and stands in front of the owner's desk with
  // the card until the approval is decided, then goes back the same way. Driven by the real approval records.
  const errands = new Map();
  const FOOT = new V3(0, 0, -15), STEP_TOP = new V3(0, 3.45, -23), FRONT_DOOR = new V3(0, 4.49, -29.1), LOBBY = new V3(0, 4.49, -31.5);
  const LIFT_TOP = HQ.localToWorld(new V3(-7.6, 21.42, -3.4));
  // Where a visitor stands: beside the desk, in the owner's view, not behind his screen; two spots, then they queue.
  const ASK_SPOTS = [[3.0, 2.0], [-3.0, 2.0], [3.6, 4.6], [-3.6, 4.6]].map(([x, z]) => HQ.localToWorld(new V3(x, 21.42, z)));
  const askSpot = () => ASK_SPOTS[[...errands.values()].filter((E) => E.spot != null).length % ASK_SPOTS.length];
  const JARVIS_LIFT = HQ.localToWorld(new V3(-6, 14.3, 7.4));
  function errandStart(id, agent) {
    let A = null, home = null;
    if (agent === "jarvis") A = jarvisA;
    else for (const P of pavilions.values()) { const d = P.desks.find((x) => x.name === agent); if (d?.actor) { A = d.actor; home = P; break; } }
    if (!A || A.errand) return;
    const E = { A, home, stage: "going", id, spot: null };
    E.spot = askSpot();
    errands.set(id, E);
    A.errand = E; A.leisure = null; A.away = false; A.activity = null;
    const lift = (A) => { A.wait = 1.6; A.c.root.position.copy(LIFT_TOP); walk(A, [E.spot], (A) => { A.face = Math.atan2(ceoSeat.pos.x - E.spot.x, ceoSeat.pos.z - E.spot.z); E.stage = "asking"; }); };
    if (home) walk(A, [home.door.clone(), ...route(home.door, FOOT), STEP_TOP, FRONT_DOOR, LOBBY], lift);
    else walk(A, [JARVIS_LIFT], lift);
  }
  function errandEnd(id) {
    const E = errands.get(id); if (!E) return;
    errands.delete(id);
    const { A, home } = E; E.stage = "back";
    const done = (A) => { A.errand = null; if (home) goToDesk(A, home); else { A.seat = jarvisSeat; A.face = Math.PI; A.sitting = true; } };
    walk(A, [LIFT_TOP], (A) => {
      A.wait = 1.6;
      if (home) { A.c.root.position.copy(LOBBY); walk(A, [FRONT_DOOR, STEP_TOP, FOOT, ...route(FOOT, home.door), home.door.clone()], done); }
      else { A.c.root.position.copy(JARVIS_LIFT); walk(A, [jarvisSeat.pos.clone()], done); }
    });
  }
  function syncErrands(m) {
    const pending = m.board?.ask?.approvals || [];
    const ids = new Set(pending.map((a) => String(a.id)));
    for (const a of pending) {
      if (errands.has(String(a.id))) continue;
      const task = a.task_id ? m.allTasks.find((t) => t.id === a.task_id) : null;
      errandStart(String(a.id), task?.agent || "jarvis");
    }
    for (const id of [...errands.keys()]) if (!ids.has(id)) errandEnd(id);
  }
  // Where the approval card sits: over the agent's head once it is standing at the desk.
  function errandAnchor(id) {
    const E = errands.get(String(id));
    if (!E || E.stage !== "asking") return null;
    return E.A.c.root.position.clone().add(new V3(0, 2.6, 0));
  }
  const bossHome = (home) => { boss.c.root.visible = home; };
  function drawCeo(m) {
    const g = ceoScreen.c.getContext("2d"), W = ceoScreen.c.width, H = ceoScreen.c.height, w = m.week || {}, c = m.counts || {};
    g.fillStyle = "#0f1b2c"; g.fillRect(0, 0, W, H);
    g.fillStyle = "#c6a46a"; g.fillRect(0, 0, W, 6);
    g.textAlign = "left"; g.fillStyle = "#d8b878"; g.font = "500 40px 'Bodoni Moda', Georgia, serif"; g.fillText("Urban Supplied", 40, 70);
    g.fillStyle = "#8fa3b8"; g.font = "26px 'Inter', system-ui, sans-serif"; g.fillText("this week", 380, 70);
    const money = (n) => `${n < 0 ? "-" : ""}A$${Math.abs(Math.round(n || 0)).toLocaleString("en-AU")}`;
    g.fillStyle = "#ffffff"; g.font = "600 92px 'Bodoni Moda', Georgia, serif"; g.fillText(money(w.profit), 40, 175);
    g.fillStyle = "#8fa3b8"; g.font = "26px 'Inter', system-ui, sans-serif";
    g.fillText(w.target ? `profit of the A$${Math.round(w.target)} target, ${w.target ? Math.round(((w.profit || 0) / w.target) * 100) : 0}%` : "profit", 40, 215);
    // The figures sit on the right of the panel, clear of the owner's head when seen from behind his chair.
    const cells = [["Sales", money(w.income)], ["Costs", money(w.expenses)], ["Working now", String(c.now ?? 0)], ["Needs you", String(c.ask ?? 0)], ["To do", String(c.todo ?? 0)], ["Agents", String(c.agents ?? 0)]];
    cells.forEach(([k, v], i) => { const x = 640 + (i % 2) * 190, y = 70 + Math.floor(i / 2) * 92; g.fillStyle = "#8fa3b8"; g.font = "22px 'Inter', system-ui, sans-serif"; g.fillText(k, x, y); g.fillStyle = "#ffffff"; g.font = "600 40px 'Inter', system-ui, sans-serif"; g.fillText(v, x, y + 44); });
    ceoScreen.t.needsUpdate = true;
  }

  // ---------------------------------------------------------------- camera: home view, flights, view controls
  const sph = new THREE.Spherical();
  let flight = null, lastInput = performance.now(), exploring = false;
  const fromCam = () => { const off = camera.position.clone().sub(controls.target); sph.setFromVector3(off); return { az: sph.theta, pol: sph.phi, r: sph.radius, target: controls.target.clone() }; };
  // The owner's office view: from behind his chair, over the desk and out across the plaza to the sea.
  const CEO = () => ({ az: Math.PI, pol: 1.3, r: 8.5, target: HQ.localToWorld(new V3(0, 22.4, 1.2)) });
  let mode = "ceo";
  function setMode(m) { mode = m; controls.minDistance = m === "ceo" ? 2 : 5; }
  function flyTo(goal, dur = 1.4) {
    const from = fromCam();
    let daz = goal.az - from.az; daz = Math.atan2(Math.sin(daz), Math.cos(daz));
    flight = { from, goal: { ...goal, az: from.az + daz }, start: performance.now(), dur: REDUCED ? 0.001 : dur };
  }
  function place(v) { sph.set(v.r, v.pol, v.az); camera.position.copy(v.target).add(new V3().setFromSpherical(sph)); controls.target.copy(v.target); camera.lookAt(v.target); }
  function focus(id) {
    lastInput = performance.now();
    if (me.on) { if (id) walkTo(id); return; }
    if (id) setMode("estate");
    if (!id) return flyTo(mode === "ceo" ? CEO() : HOME());
    if (id === "jarvis") return flyTo({ az: 0, pol: 1.12, r: 105, target: new V3(0, 10, -24) });
    const P = pavilions.get(id);
    if (!P) return;
    // In front of the pavilion's glass, a little above, looking in at the desks.
    const out = P.door.clone().sub(P.pav.position).setY(0).normalize();
    flyTo({ az: Math.atan2(out.x, out.z), pol: 0.98, r: 46, target: P.pav.position.clone().setY(2.5) });
  }
  function view(cmd) {
    lastInput = performance.now();
    if (cmd === "walk") { if (me.on) stopWalk(); else { const sel = opts.selected(); startWalk(); if (sel) walkTo(sel); } return me.on; }
    if (cmd === "eye") { setDist(me.third ? 0 : 5.5); return me.on; }
    if ((cmd === "in" || cmd === "out") && me.on) { setDist(me.dist * (cmd === "in" ? 0.7 : 1.4)); return true; }
    if (cmd === "ceo") { stopWalk(); exploring = false; setMode("ceo"); bossHome(true); flyTo(CEO(), 1.6); return false; }
    if (cmd === "reset") { stopWalk(); exploring = false; setMode("estate"); focus(null); return false; }
    if (me.on) stopWalk();
    if (cmd === "explore") setMode("estate");
    if (cmd === "in" || cmd === "out") { const f = fromCam(); flyTo({ ...f, r: Math.max(controls.minDistance, Math.min(controls.maxDistance, f.r * (cmd === "in" ? 0.7 : 1.4))) }, 0.6); return exploring; }
    if (cmd === "explore") { exploring = !exploring; flight = null; return exploring; }
    return exploring;
  }
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  controls.addEventListener("start", () => { flight = null; if (exploring) { exploring = false; opts.onExplore?.(false); } lastInput = performance.now(); });
  controls.addEventListener("end", () => { lastInput = performance.now(); });

  // ---------------------------------------------------------------- me mode: through the estate at eye level
  // W A S D (or the arrows) and the mouse on a desktop, a stick and drag-to-look on a phone. The walker is a circle on
  // the ground pushed out of the registered solids, so walls, trees and desks stop it and the doors let it inside.
  const COARSE = matchMedia("(pointer: coarse)").matches;
  const me = { on: false, third: false, dist: 0, pos: new V3(0, 0, 34), yaw: Math.PI, pitch: -0.04, vel: new V3(), keys: new Set(), stick: { x: 0, y: 0 }, near: null, bob: 0, drag: null, touches: new Map() };
  // The walker's view distance: 0 is eye level; anything from 1.6 m out is over the shoulder, up to 12 m.
  function setDist(d) {
    me.dist = d < 1.6 ? 0 : Math.min(12, d);
    const third = me.dist > 0;
    if (third !== me.third) { me.third = third; you.root.visible = me.on && third; tell(); }
  }
  const you = character("you", 0xc6a46a, 0.95); you.root.visible = false; scene3.add(you.root);
  const youSt = { walk: 0, walkPhase: 0, sit: 0, type: 0, lean: 0, look: 0, tab: 0, swing: 0 };
  const R = 0.55;   // the walker's radius
  const locked = () => document.pointerLockElement === canvas;
  const walkState = () => ({ on: me.on, third: me.third, near: me.near, locked: locked(), touch: COARSE, office: me.fromOffice, auto: !!me.auto });
  const tell = () => opts.onWalk?.(walkState());
  function groundAt(x, z) { let y = 0; for (const f of solid.floors) { const h = f(x, z); if (h !== null && h > y) y = h; } return y; }
  function unstick(p) {
    for (let pass = 0; pass < 3; pass++) {
      for (const [cx, cz, r] of solid.circles) {
        const dx = p.x - cx, dz = p.z - cz, d = Math.hypot(dx, dz), m = r + R;
        if (d < m) { if (d < 1e-4) { p.x = cx + m; } else { p.x = cx + dx * (m / d); p.z = cz + dz * (m / d); } }
      }
      for (const [ax, az, bx, bz] of solid.segs) {
        const vx = bx - ax, vz = bz - az, L = vx * vx + vz * vz;
        const t = Math.max(0, Math.min(1, ((p.x - ax) * vx + (p.z - az) * vz) / L));
        const qx = ax + vx * t, qz = az + vz * t, dx = p.x - qx, dz = p.z - qz, d = Math.hypot(dx, dz);
        if (d < R) { const s = Math.sqrt(L), nx = d < 1e-4 ? -vz / s : dx / d, nz = d < 1e-4 ? vx / s : dz / d; p.x = qx + nx * R; p.z = qz + nz * R; }
      }
    }
    for (const A of actors) {   // people: nobody is walked through
      if (A.grow < 0.5 || A.wait > 0) continue;
      const o = A.c.root.position, dx = p.x - o.x, dz = p.z - o.z, d = Math.hypot(dx, dz), m = 0.5 + R;
      if (d < m && d > 1e-4 && Math.abs(p.y - o.y) < 2) { p.x = o.x + dx * (m / d); p.z = o.z + dz * (m / d); }
    }
    p.x = Math.max(-97, Math.min(97, p.x)); p.z = Math.max(-63, Math.min(83, p.z));
  }
  function startWalk() {
    me.on = true; exploring = false; flight = null; controls.enabled = false; controls.autoRotate = false;
    me.fromOffice = mode === "ceo"; me.auto = null;
    if (me.fromOffice) { me.pos.set(0, 0, -12); me.yaw = 0; me.third = true; me.dist = 5.5; bossHome(false); }   // down the lift to the forecourt
    else if (me.third) me.dist = me.dist || 5.5;
    else { me.pos.set(0, 0, 34); me.yaw = Math.PI; }
    me.pitch = -0.04;
    me.vel.set(0, 0, 0); me.keys.clear(); me.stick.x = me.stick.y = 0; me.near = null;
    unstick(me.pos); me.pos.y = groundAt(me.pos.x, me.pos.z);
    you.root.position.copy(me.pos); you.root.rotation.y = me.yaw; you.root.visible = me.third;
    tell();
  }
  function stopWalk() {
    if (!me.on) return;
    me.on = false; controls.enabled = true; you.root.visible = false; me.auto = null;
    if (locked()) document.exitPointerLock();
    if (me.fromOffice) { me.fromOffice = false; bossHome(true); setMode("ceo"); flyTo(CEO(), 1.6); tell(); return; }   // back up to the office
    // Back to the orbit view, lifting off from behind where you stood.
    place({ az: me.yaw + Math.PI, pol: 1.25, r: 14, target: me.pos.clone().setY(me.pos.y + 1.6) });
    flyTo({ az: me.yaw + Math.PI, pol: 1.05, r: 52, target: me.pos.clone().setY(2) }, 1.2);
    tell();
  }
  // A chip or a panel while walking is a destination: the owner walks there, round the plaza rather than through it.
  function walkTo(id) {
    let to;
    if (id === "jarvis") to = new V3(0, 0, -10);
    else {
      const P = pavilions.get(id); if (!P) return;
      const out = P.door.clone().sub(P.pav.position).setY(0).normalize();
      to = P.door.clone().setY(0).addScaledVector(out, 1.2);
    }
    me.auto = route(me.pos, to); if (!me.third) setDist(5.5); tell();
  }
  function route(from, to) {
    const pts = [], c = PLAZA, ring = 12.5;
    // Every door faces the plaza, so anything but a short hop goes to the ring, round it, then straight out to the door:
    // that never crosses the basin or another pavilion.
    if (Math.hypot(to.x - from.x, to.z - from.z) > 8) {
      const a0 = Math.atan2(from.z - c.z, from.x - c.x), a1 = Math.atan2(to.z - c.z, to.x - c.x);
      let da = a1 - a0; da = Math.atan2(Math.sin(da), Math.cos(da));
      const n = Math.max(1, Math.ceil(Math.abs(da) / 0.4));
      for (let i = 0; i <= n; i++) { const a = a0 + (da * i) / n; pts.push(new V3(c.x + Math.cos(a) * ring, 0, c.z + Math.sin(a) * ring)); }
    }
    pts.push(to.clone());
    return pts;
  }
  // What you could open from here: an agent at its desk, the office you are in or at the door of, or JARVIS.
  const lp = new V3();
  function nearest() {
    const p = me.pos;
    let best = null, bd = 4;
    for (const P of pavilions.values()) for (const d of P.desks) {
      const dist = Math.hypot(d.seat.pos.x - p.x, d.seat.pos.z - p.z);
      if (dist < bd) { bd = dist; best = { kind: "agent", id: d.name, label: P.team.agents.find((a) => a.name === d.name)?.label || d.name }; }
    }
    if (best) return best;
    for (const P of pavilions.values()) {
      lp.copy(p).setY(0).applyMatrix4(P.inv);
      if (Math.abs(lp.x) <= PW / 2 + 1.5 && lp.z >= -PD / 2 - 1.5 && lp.z <= PD / 2 + 5.5) return { kind: "team", id: P.team.id, label: P.team.name };
    }
    const lx = p.x / 1.15, lz = (p.z + 40) / 1.15;
    if ((Math.abs(lx) <= 18 && lz >= 1.2 && lz <= 14) || (Math.abs(lx) <= 13 && lz > 14 && lz <= 23) || Math.hypot(p.x - PLAZA.x, p.z - PLAZA.z) < 12.5) return { kind: "jarvis", id: "jarvis", label: "JARVIS" };
    return null;
  }
  function interact() {
    if (!me.on || !me.near) return;
    if (locked()) document.exitPointerLock();
    opts.onPick(me.near.kind, me.near.id);
  }
  const typing = (e) => /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
  const KEYS = { w: "f", arrowup: "f", s: "b", arrowdown: "b", a: "l", arrowleft: "l", d: "r", arrowright: "r", shift: "run" };
  addEventListener("keydown", (e) => {
    if (!me.on || typing(e)) return;
    const k = e.key.toLowerCase();
    if (KEYS[k]) { me.keys.add(KEYS[k]); e.preventDefault(); }
    else if (k === "e" || k === "enter") interact();
    else if (k === "v") view("eye");
    else if (k === "escape" && !locked() && !document.body.classList.contains("panel-open")) view("walk");   // Esc closes a panel first
  }, true);
  addEventListener("keyup", (e) => { const k = KEYS[e.key.toLowerCase()]; if (k) me.keys.delete(k); });
  addEventListener("blur", () => me.keys.clear());
  document.addEventListener("pointerlockchange", tell);
  const look = (dx, dy, k) => { me.yaw -= dx * k; me.pitch = Math.max(-1.1, Math.min(1.1, me.pitch - dy * k)); };
  canvas.addEventListener("mousemove", (e) => { if (me.on && locked()) look(e.movementX, e.movementY, 0.0022); });
  canvas.addEventListener("pointerdown", (e) => {
    if (!me.on) return;
    if (e.pointerType === "touch") me.touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (!locked()) me.drag = { id: e.pointerId, x: e.clientX, y: e.clientY };
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!me.on) return;
    if (e.pointerType === "touch" && me.touches.has(e.pointerId)) {
      const t = me.touches.get(e.pointerId);
      if (me.touches.size === 2) {   // pinch: the view distance
        const [a, b] = [...me.touches.values()], before = Math.hypot(a.x - b.x, a.y - b.y);
        t.x = e.clientX; t.y = e.clientY;
        const after = Math.hypot(a.x - b.x, a.y - b.y);
        if (before > 0) setDist((me.dist || 1.6) * (before / after)); return;
      }
      t.x = e.clientX; t.y = e.clientY;
    }
    const d = me.drag; if (!d || d.id !== e.pointerId) return;
    look(e.clientX - d.x, e.clientY - d.y, COARSE ? 0.006 : 0.004); d.x = e.clientX; d.y = e.clientY;
  });
  const endTouch = (e) => me.touches.delete(e.pointerId);
  canvas.addEventListener("pointerup", endTouch); canvas.addEventListener("pointercancel", endTouch);
  canvas.addEventListener("wheel", (e) => { if (!me.on) return; e.preventDefault(); setDist((me.dist || 1.2) * Math.exp(e.deltaY * 0.0012)); }, { passive: false });
  // The on-screen stick (phones): its knob follows the finger, and the direction and push drive the me.
  if (opts.stick) {
    const stick = opts.stick, knob = stick.firstElementChild; let sid = null;
    const set = (e) => {
      const r = stick.getBoundingClientRect(), rad = r.width / 2;
      let x = (e.clientX - r.left - rad) / rad, y = (e.clientY - r.top - rad) / rad; const L = Math.hypot(x, y);
      if (L > 1) { x /= L; y /= L; }
      me.stick.x = x; me.stick.y = y; knob.style.transform = `translate(${x * rad * 0.55}px, ${y * rad * 0.55}px)`;
    };
    stick.addEventListener("pointerdown", (e) => { sid = e.pointerId; try { stick.setPointerCapture(sid); } catch {} set(e); e.preventDefault(); });
    stick.addEventListener("pointermove", (e) => { if (e.pointerId === sid) set(e); });
    const end = (e) => { if (e.pointerId !== sid) return; sid = null; me.stick.x = me.stick.y = 0; knob.style.transform = ""; };
    stick.addEventListener("pointerup", end); stick.addEventListener("pointercancel", end);
  }
  const fwd = new V3(), side = new V3(), want = new V3(), eye = new V3(), up = new V3(0, 1, 0);
  // How far behind the walker the view can sit before it meets a wall or a tree (a ray against the solids, on the ground plane).
  function clearBehind(x, z, dx, dz, max, wallsOnly) {
    let best = max;
    for (const [ax, az, bx, bz] of wallsOnly ? [...solid.segs, ...solid.camSegs] : solid.segs) {
      const vx = bx - ax, vz = bz - az, den = dx * vz - dz * vx;
      if (Math.abs(den) < 1e-6) continue;
      const t = ((ax - x) * vz - (az - z) * vx) / den, u = ((ax - x) * dz - (az - z) * dx) / den;
      if (t > 0 && t < best && u >= 0 && u <= 1) best = t;
    }
    for (const [cx, cz, r] of wallsOnly ? [] : solid.circles) {
      const ox = x - cx, oz = z - cz, b = ox * dx + oz * dz, c = ox * ox + oz * oz - (r + 0.3) * (r + 0.3), disc = b * b - c;
      if (disc < 0) continue;
      const t = -b - Math.sqrt(disc);
      if (t > 0 && t < best) best = t;
    }
    return best;
  }
  function stepWalk(dt, time) {
    const k = me.keys;
    let ix = (k.has("r") ? 1 : 0) - (k.has("l") ? 1 : 0) + me.stick.x, iz = (k.has("f") ? 1 : 0) - (k.has("b") ? 1 : 0) - me.stick.y;
    if (me.auto && (ix || iz)) { me.auto = null; tell(); }   // taking the controls cancels the walk there
    if (me.auto) {
      const p = me.auto[0], dx = p.x - me.pos.x, dz = p.z - me.pos.z, dist = Math.hypot(dx, dz);
      if (dist < 0.7) { me.auto.shift(); if (!me.auto.length) { me.auto = null; tell(); } }
      else { me.yaw += wrap(Math.atan2(dx, dz) - me.yaw) * (1 - Math.exp(-6 * dt)); me.pitch += (-0.08 - me.pitch) * (1 - Math.exp(-3 * dt)); iz = Math.min(1, dist / 2); }
    }
    const L = Math.hypot(ix, iz); if (L > 1) { ix /= L; iz /= L; }
    fwd.set(Math.sin(me.yaw), 0, Math.cos(me.yaw)); side.crossVectors(fwd, up);
    const speed = k.has("run") ? 8 : 4.5;
    want.copy(fwd).multiplyScalar(iz * speed).addScaledVector(side, ix * speed);
    me.vel.lerp(want, 1 - Math.exp(-9 * dt));
    // Short sub-steps so a fast walker cannot pass through a wall between frames.
    const n = Math.max(1, Math.ceil(me.vel.length() * dt / 0.35));
    for (let i = 0; i < n; i++) { me.pos.addScaledVector(me.vel, dt / n); unstick(me.pos); }
    const g = groundAt(me.pos.x, me.pos.z);
    me.pos.y += (g - me.pos.y) * (1 - Math.exp(-8 * dt));
    const moving = Math.min(1, me.vel.length() / 4.5);
    me.bob += dt * (5 + 4 * moving) * moving;
    // The avatar (seen over the shoulder) walks with you.
    youSt.walk = damp(youSt.walk, moving > 0.15 ? 1 : 0, 6, dt);
    if (youSt.walk > 0.01) youSt.walkPhase += dt * 7.2 * (0.5 + 0.5 * youSt.walk) * (k.has("run") ? 1.4 : 1);
    pose(you, youSt, time);
    you.root.position.copy(me.pos);
    you.root.rotation.y += wrap(me.yaw - you.root.rotation.y) * (1 - Math.exp(-10 * dt));
    const bob = REDUCED ? 0 : Math.sin(me.bob) * 0.035 * moving;
    const cp = Math.cos(me.pitch), sp = Math.sin(me.pitch);
    eye.set(me.pos.x, me.pos.y + 1.6, me.pos.z);
    if (me.third) {
      const back = Math.max(1.2, Math.min(me.dist, clearBehind(eye.x, eye.z, -fwd.x * cp, -fwd.z * cp, me.dist) - 0.4));
      camera.position.set(eye.x - fwd.x * back * cp, Math.max(g + 0.5, eye.y + 0.4 - back * sp), eye.z - fwd.z * back * cp);
      controls.target.set(eye.x + fwd.x * 3 * cp, eye.y + 3 * sp, eye.z + fwd.z * 3 * cp);
    } else {
      camera.position.set(eye.x, eye.y + 0.05 + bob, eye.z);
      controls.target.set(eye.x + fwd.x * cp * 10, eye.y + sp * 10, eye.z + fwd.z * cp * 10);
    }
    camera.lookAt(controls.target);
    if ((frameCount % 8) === 0) { const nr = nearest(); if (nr?.kind !== me.near?.kind || nr?.id !== me.near?.id) { me.near = nr; tell(); } }
  }

  // Rendering: bloom on capable devices (lit windows, the beam, the globe); plain rendering in simplified mode.
  let composer = null;
  if (!simple && addons.EffectComposer) {
    composer = new addons.EffectComposer(renderer);
    composer.addPass(new addons.RenderPass(scene3, camera));
    if (opts.ao && addons.SSAOPass) {   // ambient occlusion: contact shadows under eaves, furniture and people (desktops with the cores for it)
      const ssao = new addons.SSAOPass(scene3, camera, stage.clientWidth, stage.clientHeight);
      ssao.kernelRadius = 6; ssao.minDistance = 0.0008; ssao.maxDistance = 0.06;
      composer.addPass(ssao);
    }
    bloomPass = new addons.UnrealBloomPass(new THREE.Vector2(256, 256), 0.45, 0.5, 1.15);  // only real light sources glow
    composer.addPass(bloomPass);
    composer.addPass(new addons.OutputPass());
    if (addons.SMAAPass) composer.addPass(new addons.SMAAPass(stage.clientWidth * renderer.getPixelRatio(), stage.clientHeight * renderer.getPixelRatio()));   // smooth edges
  }
  const viewShift = { x: 0 };
  function resize() {
    const w = stage.clientWidth, h = stage.clientHeight;
    renderer.setSize(w, h, false);
    composer?.setSize(w, h);
    camera.aspect = w / h;
    camera.fov = w < h ? 52 : 42;   // a wide lens, like the concept: the estate, the harbour and the sky
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(stage); resize();
  const onScreen = () => { const r = stage.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; };  // a hidden tab already gets no frames

  // Clicking: a desk opens its agent, a pavilion its team, HQ or the globe opens JARVIS (a tap, not the end of a drag).
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let down = null;
  canvas.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
  canvas.addEventListener("pointerup", (e) => {
    me.drag = null;
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) return;
    if (me.on && !COARSE && !locked()) { try { canvas.requestPointerLock?.()?.catch?.(() => {}); } catch {} return; }   // the first click takes the mouse (not allowed in some embedded views)
    const rect = canvas.getBoundingClientRect();
    if (me.on && locked()) ndc.set(0, 0);   // with the mouse taken, a click picks what is in the middle of the view
    else ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects([...hitTargets, HQ, globe], true)[0];
    if (!hit) return;
    let o = hit.object;
    if (o.userData.desk && !o.userData.desk.startsWith("new-hire")) return opts.onPick("agent", o.userData.desk);
    while (o && !o.userData.pick && o !== globe) o = o.parent;
    if (!o) return;
    if (o === globe || o.userData.pick === "jarvis") return opts.onPick("jarvis");
    opts.onPick("team", o.userData.pick);
  });

  // Nobody walks through anyone: walking people are pushed apart from each other, and round the owner (seated at his
  // desk or out walking), with a sideways nudge so they go round rather than stop dead.
  let kaDt = 0.016;
  function keepApart(dt) {
    kaDt = dt;
    const walking = actors.filter((A) => A.path.length && A.grow > 0.5 && A.wait <= 0 && !A.sitting);
    const push = (A, ox, oz, minD, both) => {
      const o = A.c.root.position, dx = o.x - ox, dz = o.z - oz, d = Math.hypot(dx, dz);
      if (d >= minD + 0.6 || d < 1e-4) return null;
      const nx = dx / d, nz = dz / d, head = A.path[0];
      // The next waypoint sits behind whoever is in the way: move it to one side, so the route goes round them.
      if (Math.hypot(head.x - ox, head.z - oz) < minD + 0.4) { const s = (head.x - o.x) * nz - (head.z - o.z) * nx < 0 ? -1 : 1; head.x = ox + nx * 0.3 + s * -nz * (minD + 1.2); head.z = oz + nz * 0.3 + s * nx * (minD + 1.2); }
      if (d >= minD) return null;
      const k = (minD - d) * (both ? 0.5 : 1);
      // Mostly away, and a steady slide to the side they were heading, so they pass rather than stop dead.
      const hx = head.x - o.x, hz = head.z - o.z, side = (hx * nz - hz * nx) < 0 ? -1 : 1, slide = 2.2 * kaDt;
      o.x += nx * k + side * -nz * slide; o.z += nz * k + side * nx * slide;
      return [nx * k, nz * k];
    };
    for (let i = 0; i < walking.length; i++) {
      const A = walking[i], a = A.c.root.position;
      for (let j = i + 1; j < walking.length; j++) {
        const B = walking[j], b = B.c.root.position;
        if (Math.abs(a.y - b.y) > 2) continue;
        const moved = push(A, b.x, b.z, 1.0, true);
        if (moved) { b.x -= moved[0]; b.z -= moved[1]; }
      }
      for (const S of actors) if (!S.path.length && S !== A && S.grow > 0.5 && Math.abs(S.c.root.position.y - a.y) < 2) push(A, S.c.root.position.x, S.c.root.position.z, 1.0, false);
      if (me.on && Math.abs(me.pos.y - a.y) < 2) push(A, me.pos.x, me.pos.z, 1.2, false);
    }
  }

  // ---------------------------------------------------------------- the race's sound
  // Engine note from the nearest car, synthesised in the browser (no audio files): louder as the view comes within
  // 90 m of the track, pitch following the car's speed. Off in Settings, and silent until the page has been touched
  // (browsers allow sound only after that).
  let audio = null, soundOn = true;
  function engineStart() {
    if (audio || !soundOn) return;
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const gain = ctx.createGain(); gain.gain.value = 0;
      const low = ctx.createBiquadFilter(); low.type = "lowpass"; low.frequency.value = 900;
      const o1 = ctx.createOscillator(); o1.type = "sawtooth"; o1.frequency.value = 70;
      const o2 = ctx.createOscillator(); o2.type = "square"; o2.frequency.value = 141;
      const g2 = ctx.createGain(); g2.gain.value = 0.25;
      o1.connect(low); o2.connect(g2); g2.connect(low); low.connect(gain); gain.connect(ctx.destination);
      o1.start(); o2.start();
      audio = { ctx, gain, o1, o2, level: 0 };
    } catch { audio = null; }
  }
  for (const ev of ["pointerdown", "keydown"]) addEventListener(ev, engineStart, { passive: true });
  function engine(cs, dt) {
    if (!audio) return;
    let best = 1e9, car = null;
    for (const c of cs.list) { if (!c.pos) continue; const d = camera.position.distanceTo(c.pos); if (d < best) { best = d; car = c; } }
    const target = soundOn && car && best < 90 ? Math.pow(1 - best / 90, 2) * 0.22 : 0;
    audio.level = damp(audio.level, target, 4, dt);
    const t = audio.ctx.currentTime;
    audio.gain.gain.setTargetAtTime(audio.level, t, 0.05);
    if (car) { const f = 55 + car.v * 2.2; audio.o1.frequency.setTargetAtTime(f, t, 0.08); audio.o2.frequency.setTargetAtTime(f * 2.01, t, 0.08); }
  }
  function setSound(on) { soundOn = !!on; if (!on && audio) audio.gain.gain.setTargetAtTime(0, audio.ctx.currentTime, 0.05); }

  // ---------------------------------------------------------------- the frame loop
  const damp = (a, b, lambda, dt) => a + (b - a) * (1 - Math.exp(-lambda * dt));
  const clock = new THREE.Clock();
  const tmp = new V3(), m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), one = new V3(1, 1, 1);
  let first = true, ringSpin = 0.2, frameCount = 0, paused = false;
  applyTime();
  // HQ opens in the owner's office; the 3D View button goes out to the estate.
  setMode("ceo");
  place({ ...CEO(), r: 16, pol: 1.05 });
  flyTo(CEO(), 2.6);

  function frame() {
    // Real time, so a slow device keeps pace; capped so a long pause (a hidden tab) does not jump everything.
    const dt = Math.min(clock.getDelta(), 0.25), time = clock.elapsedTime;
    if (!first && (!onScreen() || paused)) return;
    if (me.on) stepWalk(dt, time);
    else if (flight) {
      flight.t = Math.min(1, (performance.now() - flight.start) / (flight.dur * 1000));
      const k = ease(flight.t), f = flight.from, g = flight.goal;
      place({ az: f.az + (g.az - f.az) * k, pol: f.pol + (g.pol - f.pol) * k, r: f.r + (g.r - f.r) * k, target: f.target.clone().lerp(g.target, k) });
      if (flight.t >= 1) flight = null;
    } else {
      controls.autoRotate = !REDUCED && (exploring || (mode !== "ceo" && !opts.selected() && performance.now() - lastInput > 40000));
      controls.autoRotateSpeed = exploring ? 0.9 : 0.15;
      controls.update();
      // Keep panning on the estate.
      controls.target.x = Math.max(-110, Math.min(110, controls.target.x));
      controls.target.z = Math.max(-120, Math.min(90, controls.target.z));
      controls.target.y = Math.max(0, Math.min(32, controls.target.y));
      // Close in, the view stops at a wall instead of passing through it, and never goes under a floor.
      const dx = camera.position.x - controls.target.x, dz = camera.position.z - controls.target.z, flat = Math.hypot(dx, dz);
      if (flat > 0.05 && flat < 12) {
        const free = clearBehind(controls.target.x, controls.target.z, dx / flat, dz / flat, flat, true);
        if (free < flat) { const k = Math.max(0.15, (free - 0.3) / flat); camera.position.set(controls.target.x + dx * k, controls.target.y + (camera.position.y - controls.target.y) * k, controls.target.z + dz * k); }
      }
      camera.position.y = Math.max(camera.position.y, groundAt(camera.position.x, camera.position.z) + 0.7);
    }
    // A side panel covers the right of the view on a desktop: shift the picture so the subject stays visible.
    const open = document.body.classList.contains("panel-open");
    const W = stage.clientWidth, H = stage.clientHeight;
    const cover = open && W > 860 ? Math.min(460, W) : 0;
    viewShift.x = damp(viewShift.x, cover / 2, 6, dt);
    if (Math.abs(viewShift.x) > 0.5) camera.setViewOffset(W, H, viewShift.x, 0, W, H); else camera.clearViewOffset();

    // The globe turns; faster and brighter while JARVIS is thinking or handing out work.
    coreKick = damp(coreKick, 0, 2.5, dt);
    ringSpin = damp(ringSpin, 0.2 + jarvisBusy * 1.1 + coreKick * 2, 2, dt);
    const spin = REDUCED ? 0 : dt * 0.15;
    globeCore.rotation.y += spin; lines.rotation.y += spin; landDots.rotation.y += spin;
    orbit.forEach((r, i) => { r.rotation.z += (REDUCED ? 0 : dt) * ringSpin * (i ? -1 : 1); });
    const beat = REDUCED ? 0 : Math.sin(time * (1.2 + jarvisBusy * 1.6)) * 0.5 + 0.5;
    globeCore.material.emissiveIntensity = 1.2 + beat * 0.5 + coreKick * 1.5;
    globe.position.y = 9 + (REDUCED ? 0 : Math.sin(time * 0.7) * 0.25);
    beamMat.uniforms.t.value = time;
    beamMat.uniforms.glow.value = 0.55 + 0.45 * Math.min(1, world.glow || 1);
    if ((frameCount++ & 31) === 0) applyTime();   // the light follows the clock; checking twice a second is plenty

    for (const P of pavilions.values()) {
      for (const d of P.desks) {
        const A = d.actor, ph = d.phase + time;
        if (A && !A.leaving && !A.errand) {
          // Real work brings an agent back to its desk; with nothing to do it heads out for a while.
          if (A.status !== "idle" && A.away) goToDesk(A, P);
          else if (A.status === "idle" && !A.away && A.sitting && !REDUCED) {
            A.idleFor = (A.idleFor || 0) + dt;
            if (A.idleFor > 6 + (hash(A.name) % 20)) { A.idleFor = 0; goRelax(A, P); }
          } else if (A.status !== "idle") A.idleFor = 0;
        }
        const pulse = REDUCED ? 1 : d.status === "working" ? 0.75 + Math.sin(ph * 2.2) * 0.25 : d.status === "blocked" ? 0.7 + Math.sin(ph * 4) * 0.3 : 0.85;
        d.bulbMat.emissiveIntensity = 1.4 * pulse + (d.status === "idle" ? -0.5 : 0.6);
        d.glow.material.opacity = d.status === "idle" ? 0.25 : 0.75 * pulse;
      }
      for (let i = P.hires.length - 1; i >= 0; i--) {
        const h = P.hires[i];
        h.grow = damp(h.grow, h.gone ? 0 : 1, 3, dt);
        h.desk.grp.scale.setScalar(Math.max(0.001, h.grow * 1.25));
        if (h.gone && h.grow < 0.02 && h.actor.grow < 0.02) { P.pav.remove(h.desk.grp); P.hires.splice(i, 1); }
      }
      P.helpers = P.helpers.filter((h) => !(h.gone && h.grow < 0.02));
    }
    const far = camera.position.distanceTo(controls.target) > 90;
    for (let i = actors.length - 1; i >= 0; i--) {
      const A = actors[i];
      if (A.c.tag) A.c.tag.material.opacity = far ? 0 : Math.max(0, Math.min(1, (70 - camera.position.distanceTo(A.c.root.position)) / 25));
      stepActor(A, dt, time);
      if (A.gone && A.grow < 0.02) { scene3.remove(A.c.root); actors.splice(i, 1); }
    }
    keepApart(dt);
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      p.t += dt / 1.6;
      const k = ease(Math.min(1, p.t));
      const mid = p.from.clone().lerp(p.to, 0.5).setY(26);
      tmp.copy(p.from).multiplyScalar((1 - k) * (1 - k)).addScaledVector(mid, 2 * (1 - k) * k).addScaledVector(p.to, k * k);
      p.sprite.position.copy(tmp);
      p.sprite.material.opacity = Math.min(1, (1.15 - p.t) * 4);
      if (p.t > 1.15) { scene3.remove(p.sprite); pulses.splice(i, 1); }
    }

    // Scenery: the fountain, ripples, the turntable, and the cars on the circuit.
    if (!REDUCED) world.jets.forEach((j, i) => { j.scale.y = 1 + 0.08 * Math.sin(time * 5 + i); });
    if (!REDUCED) {
      rippleTex.offset.x += dt * 0.004; rippleTex.offset.y += dt * 0.0025;
      if (world.water) world.water.material.uniforms.time.value += dt * 0.6;
      poolRipple.offset.x += dt * 0.02;
      turn.rotation.y += dt * 0.08;
      const cs = world.cars;
      const gapTo = (a, b) => ((((b.u - a.u) % 1) + 1) % 1) * cs.len;   // metres from a to b along the lap
      cs.list.forEach((c, i) => {
        // Look ahead for the next corner and brake for it; stay behind the car in front in the same lane, or, if
        // this car is quicker and the other lane is clear, pull out and pass, then tuck back in.
        const idx = Math.floor(((c.u + 12 / cs.len) % 1) * cs.bend.length) % cs.bend.length;
        const straight = cs.bend[idx] < 0.08;
        let want = c.top * (1 - Math.min(0.68, cs.bend[idx] * 1.9));
        let ahead = null, gap = 1e9;
        for (const o of cs.list) { if (o === c) continue; const g = gapTo(c, o); if (g < gap && Math.abs(o.lane - c.lane) < 1.6) { gap = g; ahead = o; } }
        if (ahead && gap < 20) {
          const clear = cs.list.every((o) => o === c || Math.abs(o.lane - (c.lane > 0 ? -2.8 : 2.8)) > 1.6 || gapTo(c, o) > 16 && gapTo(o, c) > 10);
          if (!c.passing && c.top > ahead.top + 2 && straight && clear) { c.passing = ahead; c.want = c.lane > 0 ? -2.8 : 2.8; }
          else want = Math.min(want, ahead.v * (gap < 11 ? 0.85 : 0.99));
        }
        if (c.passing && gapTo(c.passing, c) < cs.len / 2 && gapTo(c.passing, c) > 9) { c.passing = null; c.want = 0; }   // clear ahead: back to the line
        c.lane = damp(c.lane, c.want, 1.6, dt);
        c.v = damp(c.v, want, want < c.v ? 2.4 : 0.9, dt);
        c.u = (c.u + (c.v * dt) / cs.len) % 1;
        const p = world.curve.getPointAt(c.u), t = world.curve.getTangentAt(c.u);
        p.x += -t.z * c.lane; p.z += t.x * c.lane;
        q.setFromAxisAngle(up, Math.atan2(-t.z, t.x));
        m4.compose(p, q, one); cs.mesh.setMatrixAt(i, m4);
        c.pos = p;
      });
      cs.mesh.instanceMatrix.needsUpdate = true;
      engine(cs, dt);
    }

    if (composer) composer.render(); else renderer.render(scene3, camera);

    // Glass chips follow their buildings on screen.
    const placed = [];   // chips already placed this frame, so later ones stack under them rather than overlap
    for (const el of tags.children) {
      const id = el.dataset.id;
      const P = pavilions.get(id);
      const anchor = el.dataset.approval ? errandAnchor(el.dataset.approval) : id === "jarvis" ? tmp.set(0, 37, -40) : P ? tmp.copy(P.anchor) : null;
      if (!anchor) { el.style.opacity = 0; continue; }
      const v = anchor.clone().project(camera);
      const dist = camera.position.distanceTo(anchor);
      const x = (v.x * 0.5 + 0.5) * W;
      let y = (-v.y * 0.5 + 0.5) * H;
      // A chip that would sit under one of the page's cards slides just above or below it, whichever is nearer.
      const hw = el.offsetWidth / 2, hh = el.offsetHeight;
      for (const r of opts.avoid?.() || []) {
        if (!(x + hw > r.left && x - hw < r.right && y > r.top && y - hh < r.bottom)) continue;
        const above = r.top - 2, below = r.bottom + hh + 2;
        y = (Math.abs(above - y) <= Math.abs(below - y) && above - hh > 0) || below > H ? above : below;
      }
      const visible = v.z < 1 && Math.abs(v.x) < 1.1 && Math.abs(v.y) < 1.1 && !(open && id === opts.selected());
      if (visible) {
        for (let guard = 0; guard < 6; guard++) {
          const hit = placed.find((q) => x + hw > q.l && x - hw < q.r && y > q.t && y - hh < q.b);
          if (!hit) break;
          y = hit.b + hh + 3 <= H ? hit.b + hh + 3 : hit.t - 3;
        }
        placed.push({ l: x - hw, r: x + hw, t: y - hh, b: y });
      }
      el.style.transform = `translate(${Math.round(x - el.offsetWidth / 2)}px, ${Math.round(y - el.offsetHeight)}px)`;
      el.style.opacity = visible ? "1" : "0";
      el.style.visibility = visible ? "visible" : "hidden";
      el.style.zIndex = String(2000 - Math.round(dist));
      el.style.pointerEvents = visible ? "auto" : "none";
    }
    if (first) { first = false; requestAnimationFrame(() => veil.classList.add("gone")); }
  }
  renderer.setAnimationLoop(frame);
  // For inspecting the view from the browser console: snap() renders one frame and returns it as an image.
  // lineup(): stand everyone in a row on the forecourt for a picture; returns a function that puts them back.
  function lineup() {
    const saved = actors.map((A) => ({ A, pos: A.c.root.position.clone(), face: A.face, sitting: A.sitting, path: A.path, wait: A.wait }));
    const n = actors.length;
    actors.forEach((A, i) => { A.path = []; A.sitting = false; A.wait = 0; A.c.root.position.set((i - (n - 1) / 2) * 1.5, 0, -4); A.face = 0; A.c.root.rotation.y = 0; });
    return () => saved.forEach((s) => { s.A.c.root.position.copy(s.pos); s.A.face = s.face; s.A.sitting = s.sitting; s.A.path = s.path; s.A.wait = s.wait; });
  }
  stage.hq = { camera, controls, actors, frame, me, solid, boss, jarvisA, errands, lineup, personas, snap: (q = 0.85) => { if (composer) composer.render(); else renderer.render(scene3, camera); return canvas.toDataURL("image/jpeg", q); } };
  return { update, dispatch, focus, demo, view, whereabouts, simple, setTime: (mode) => { timeMode = TIMES[mode] ? mode : "live"; applyTime(); }, reward, interact, walking: () => me.on, pause: (on) => { paused = !!on; }, setSound };
}
