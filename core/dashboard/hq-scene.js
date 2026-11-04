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
      const [ec, rp, bp, op] = await Promise.all([
        import("three/addons/postprocessing/EffectComposer.js"), import("three/addons/postprocessing/RenderPass.js"),
        import("three/addons/postprocessing/UnrealBloomPass.js"), import("three/addons/postprocessing/OutputPass.js")]);
      Object.assign(addons, { EffectComposer: ec.EffectComposer, RenderPass: rp.RenderPass, UnrealBloomPass: bp.UnrealBloomPass, OutputPass: op.OutputPass });
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
  controls.minDistance = 30; controls.maxDistance = 360; controls.minPolarAngle = 0.35; controls.maxPolarAngle = 1.32;
  controls.rotateSpeed = 0.5; controls.zoomSpeed = 0.8;
  // The concept's view: high over the estate's south lawn, looking north over HQ to the harbour and the city.
  const HOME = () => ({ az: 0, pol: 1.22, r: stage.clientWidth < stage.clientHeight ? 230 : 158, target: new V3(0, 4, -30) });

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
  const M = {
    paving: std({ map: pavingTex, roughness: 0.35, envMapIntensity: 0.6 }),
    marble: std({ map: marbleTex, roughness: 0.25, envMapIntensity: 0.8 }),
    ivory: std({ color: 0xeae5da, roughness: 0.55 }),
    stone: std({ color: 0xd8cebd, roughness: 0.85 }),
    gold: std({ color: 0xc6a46a, metalness: 1, roughness: 0.3, envMap: envTex, envMapIntensity: 1.1 }),
    glass: std({ color: 0x9fbccc, metalness: 0.3, roughness: 0.05, transparent: true, opacity: 0.3, depthWrite: false, side: THREE.DoubleSide, envMap: envTex, envMapIntensity: 0.9 }),
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
    interior: std({ color: 0xf2e2c8, emissive: 0xe8b878, emissiveIntensity: 0.55, roughness: 0.8 }),
  };

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
  const at = (x, y, z, ry = 0, sx = 1, sy = 1, sz = 1) => new THREE.Matrix4().compose(new V3(x, y, z),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(0, ry, 0)), new V3(sx, sy, sz));
  const box = (w, h, d, mat, x = 0, y = 0, z = 0, parent = scene3, shadow = true) => {
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z);
    if (shadow && !simple) { m.castShadow = true; m.receiveShadow = true; }
    parent.add(m); return m;
  };

  // ---------------------------------------------------------------- golden-hour light and sky
  scene3.add(new THREE.HemisphereLight(0xcfe2f4, 0xcdb08a, 1.25));
  const sun = new THREE.DirectionalLight(0xffd29a, 3.4);
  sun.position.set(-150, 95, -40);           // low in the west over the sea: long warm shadows towards the viewer
  sun.castShadow = !simple;
  sun.shadow.mapSize.set(4096, 4096);
  Object.assign(sun.shadow.camera, { left: -120, right: 120, top: 110, bottom: -110, near: 20, far: 420 });
  sun.shadow.bias = -0.0003; sun.shadow.normalBias = 0.05;
  scene3.add(sun, sun.target);
  const sky = new THREE.Mesh(new THREE.SphereGeometry(3200, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: C(0x6fa8dc) }, mid: { value: C(0xbcd8ee) }, low: { value: C(0xf6dcc0) }, sunDir: { value: sun.position.clone().normalize() } },
    vertexShader: "varying vec3 vp; void main(){ vp = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
    fragmentShader: "uniform vec3 top; uniform vec3 mid; uniform vec3 low; uniform vec3 sunDir; varying vec3 vp; void main(){ float h = vp.y; vec3 c = mix(low, mid, smoothstep(0.0, 0.16, h)); c = mix(c, top, smoothstep(0.16, 0.65, h)); float s = max(dot(vp, sunDir), 0.0); c += vec3(1.0, 0.78, 0.5) * pow(s, 40.0) * 0.35; gl_FragColor = vec4(c, 1.0); }",
  }));
  scene3.add(sky);

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
    const sea = new THREE.Mesh(seaGeo, std({ vertexColors: true, roughness: 0.15, metalness: 0.1, normalMap: rippleTex, normalScale: new THREE.Vector2(0.35, 0.35), envMap: envTex, envMapIntensity: 0.5 }));
    sea.position.y = SEA; scene3.add(sea);

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
    const cube = new THREE.BoxGeometry(1, 1, 1);
    const city = new THREE.InstancedMesh(cube, std({ roughness: 0.75 }), blocks.length);
    const glazing = new THREE.InstancedMesh(cube, std({ color: 0x8fb1c7, roughness: 0.15, metalness: 0.5 }), blocks.length);
    blocks.forEach(([x, y, z, w, h, d, c, ry], i) => {
      city.setMatrixAt(i, at(x, y + h / 2 - 1, z, ry, w, h, d)); city.setColorAt(i, C(c));
      glazing.setMatrixAt(i, at(x, y + h / 2, z, ry, w * 1.01, h * 0.8, d * 0.6));  // window bands
    });
    scene3.add(city, glazing);

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
    const cars = new THREE.InstancedMesh(carGeo, M.paint, liveries.length);
    liveries.forEach((c, i) => cars.setColorAt(i, C(c)));
    scene3.add(cars);
    const len = curve.getLength();
    const bend = pts.map((p, i) => { const a = pts[(i + N - 5) % N], b = pts[(i + 5) % N]; return a.clone().sub(p).normalize().dot(b.clone().sub(p).normalize()) + 1; });
    world.cars = { mesh: cars, len, bend, list: liveries.map((_, i) => ({ u: 1 - i * 0.035, v: 30, top: 60 + ((i * 7) % 9) })) };

    // The marina: jetties with moored yachts below the estate, superyachts at anchor in the bay.
    const hullShape = new THREE.Shape([[-0.5, -0.17], [0.22, -0.17], [0.5, 0], [0.22, 0.17], [-0.5, 0.17]].map(([x, y]) => new THREE.Vector2(x, y)));
    const hull = new THREE.ExtrudeGeometry(hullShape, { depth: 0.1, bevelEnabled: false }); hull.rotateX(-Math.PI / 2);
    const deck1 = new THREE.ExtrudeGeometry(new THREE.Shape([[-0.42, -0.13], [0.12, -0.13], [0.26, 0], [0.12, 0.13], [-0.42, 0.13]].map(([x, y]) => new THREE.Vector2(x, y))), { depth: 0.06, bevelEnabled: false });
    deck1.rotateX(-Math.PI / 2); deck1.translate(0, 0.1, 0);
    const band = new THREE.BoxGeometry(0.5, 0.03, 0.27); band.translate(-0.12, 0.135, 0);
    const deck2 = new THREE.BoxGeometry(0.34, 0.05, 0.2); deck2.translate(-0.16, 0.175, 0);
    const yachtGeo = merge([{ geo: hull, color: 0xffffff }, { geo: deck1, color: 0xf7f7f7 }, { geo: band, color: 0x1c2a3d }, { geo: deck2, color: 0xffffff }]);
    const yachts = [];
    seed = 23;
    const jetties = [];
    for (const x of [30, 62, 94, 126]) {
      jetties.push({ geo: new THREE.BoxGeometry(3, 1, 70), color: 0xefe9de, m: at(x, SEA + 0.2, -110) });
      for (let z = -82; z > -142; z -= 6) for (const side of [-1, 1]) if (rand() > 0.15) {
        const L = 11 + rand() * 9; yachts.push([x + side * (L / 2 + 2.2), z, side > 0 ? 0 : Math.PI, L, 0xffffff]);
      }
    }
    scene3.add(new THREE.Mesh(merge(jetties), M.vcol));
    [[-40, -110, 0.3, 46, 0x1f2c44], [170, -96, 1.9, 52, 0xffffff], [230, -150, 0.8, 64, 0xffffff], [-120, -268, 2.4, 44, 0xffffff],
      [90, -200, 0.1, 70, 0xffffff], [300, -230, 1.2, 48, 0x8c96a3], [10, -250, 2.9, 40, 0xffffff], [200, -270, 0.5, 58, 0x1b2a55]]
      .forEach((y) => yachts.push(y));
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
  [[-72, 72, 44, 20], [76, 70, 40, 20], [-74, -48, 40, 24], [74, -48, 40, 24], [0, 72, 40, 16], [-84, 14, 22, 40], [84, 14, 22, 40]].forEach((l) => lawn(...l));

  // Fountain plaza with the holographic globe: the shared brain every team reads from and writes to.
  const PLAZA = new V3(0, 0, 8);
  {
    const disc = new THREE.Mesh(new THREE.CircleGeometry(19, 96), std({ map: plazaTex, roughness: 0.3, envMapIntensity: 0.8 }));
    disc.rotation.x = -Math.PI / 2; disc.position.set(PLAZA.x, 0.06, PLAZA.z); disc.receiveShadow = !simple; scene3.add(disc);
    const basin = new THREE.Mesh(new THREE.CylinderGeometry(9, 9.4, 1.1, 64, 1, true), M.marble); basin.position.set(PLAZA.x, 0.55, PLAZA.z); scene3.add(basin);
    const lip = new THREE.Mesh(new THREE.TorusGeometry(9.1, 0.28, 10, 96), M.gold); lip.rotation.x = Math.PI / 2; lip.position.set(PLAZA.x, 1.12, PLAZA.z); scene3.add(lip);
    const water = new THREE.Mesh(new THREE.CircleGeometry(9, 64), std({ color: 0x3fb7cf, roughness: 0.08, metalness: 0.2, normalMap: poolRipple, normalScale: new THREE.Vector2(0.4, 0.4), envMap: envTex, envMapIntensity: 0.8 }));
    water.rotation.x = -Math.PI / 2; water.position.set(PLAZA.x, 0.9, PLAZA.z); scene3.add(water);
    const ped = new THREE.Mesh(new THREE.CylinderGeometry(1.4, 2.4, 3.2, 32), M.marble); ped.position.set(PLAZA.x, 2, PLAZA.z); ped.castShadow = !simple; scene3.add(ped);
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
  const HQ = new THREE.Group(); HQ.position.set(0, 0, -38); scene3.add(HQ);
  {
    box(64, 3, 30, M.marble, 0, 1.5, 0, HQ);
    for (let i = 0; i < 6; i++) box(26 - i * 0.6, 0.5, 1.2, M.marble, 0, 0.25 + i * 0.5, 15.6 + (5 - i) * 1.2, HQ);
    // Main block: three floors of floor-to-ceiling glass between white slabs with gold edges, lit warm inside.
    for (let f = 0; f < 4; f++) {
      box(36, 0.9, 20, M.marble, 0, 3 + f * 5.2 + 0.45, 0, HQ);
      box(36.2, 0.18, 0.18, M.gold, 0, 3 + f * 5.2 + 0.95, 10.05, HQ, false);
      if (f < 3) {
        box(35, 4.3, 19, M.tint, 0, 3 + f * 5.2 + 3.05, -0.4, HQ);
        box(34.6, 3.8, 0.2, M.warmLight, 0, 3 + f * 5.2 + 3.05, 9.0, HQ, false);
        box(35.2, 4.3, 0.12, M.glass, 0, 3 + f * 5.2 + 3.05, 9.3, HQ, false);
        for (let k = -4; k <= 4; k++) box(0.22, 4.3, 0.3, M.gold, k * 4.3, 3 + f * 5.2 + 3.05, 9.4, HQ, false);
      }
    }
    // Wings: lower glass blocks; the east wing roof is the helipad.
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
    box(62, 0.6, 3.4, M.marble, 0, 9, 12.6, HQ);
    // Crown: a setback penthouse with the JARVIS sign.
    box(18, 5, 12, M.tint, 0, 23.6, -1, HQ);
    box(19, 0.7, 13, M.marble, 0, 26.4, -1, HQ);
    const signTex = canvasTex(1024, 512, (g, w, h) => {
      g.fillStyle = "#121c29"; g.fillRect(0, 0, w, h);
      g.strokeStyle = "#c6a46a"; g.lineWidth = 10; g.strokeRect(14, 14, w - 28, h - 28);
      g.fillStyle = "#d8b878";
      g.beginPath(); g.moveTo(w / 2 - 110, 215); g.lineTo(w / 2 - 130, 80); g.lineTo(w / 2 - 55, 150); g.lineTo(w / 2, 60); g.lineTo(w / 2 + 55, 150); g.lineTo(w / 2 + 130, 80); g.lineTo(w / 2 + 110, 215); g.closePath(); g.fill();
      g.font = "500 150px 'Bodoni Moda', Georgia, serif"; g.textAlign = "center"; g.fillText("JARVIS", w / 2, 410);
    });
    const sign = new THREE.Mesh(new THREE.PlaneGeometry(13, 6.5), std({ map: signTex, emissive: 0xffffff, emissiveMap: signTex, emissiveIntensity: 0.45, roughness: 0.4 }));
    sign.position.set(0, 23.6, 5.1); HQ.add(sign);
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
  }
  // The light beam rising from the crown.
  const beamMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { t: { value: 0 } },
    vertexShader: "varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
    fragmentShader: "varying vec2 vUv; uniform float t; void main(){ float fade = pow(1.0 - vUv.y, 1.3); gl_FragColor = vec4(vec3(1.0, 0.86, 0.6) * fade * (2.2 + 0.4 * sin(t * 2.0)), fade); }",
  });
  {
    const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 1.8, 420, 24, 1, true), beamMat);
    beam.position.set(0, 26.8 + 210, -39); scene3.add(beam);
  }

  // Pool with loungers and parasols, the tennis court, the supercar forecourt.
  let turn;
  {
    box(34, 0.3, 18, M.ivory, -62, 0.15, 48);
    const pool = new THREE.Mesh(new THREE.PlaneGeometry(26, 9), std({ color: 0x2fb3cc, roughness: 0.06, metalness: 0.15, normalMap: poolRipple, normalScale: new THREE.Vector2(0.5, 0.5), envMap: envTex, envMapIntensity: 0.8 }));
    pool.rotation.x = -Math.PI / 2; pool.position.set(-62, 0.33, 46); scene3.add(pool);
    box(26.6, 0.2, 0.3, M.gold, -62, 0.35, 41.4, scene3, false); box(26.6, 0.2, 0.3, M.gold, -62, 0.35, 50.6, scene3, false);
    for (let i = 0; i < 6; i++) {
      const x = -73 + i * 4.4;
      box(1.1, 0.35, 2.6, M.white, x, 0.5, 54.2);
      const umb = new THREE.Mesh(new THREE.ConeGeometry(2, 0.8, 12), M.white); umb.position.set(x + 2.2, 3.1, 54.4); umb.castShadow = !simple; scene3.add(umb);
      box(0.08, 2.8, 0.08, M.gold, x + 2.2, 1.6, 54.4, scene3, false);
    }
    const courtTex = canvasTex(512, 256, (g, w, h) => {
      g.fillStyle = "#b4664a"; g.fillRect(0, 0, w, h); g.fillStyle = "#2f7d74"; g.fillRect(36, 30, w - 72, h - 60);
      g.strokeStyle = "#fff"; g.lineWidth = 4; g.strokeRect(36, 30, w - 72, h - 60); g.strokeRect(36, 52, w - 72, h - 104);
      g.beginPath(); g.moveTo(w / 2, 30); g.lineTo(w / 2, h - 30); g.moveTo(140, 52); g.lineTo(140, h - 52); g.moveTo(w - 140, 52); g.lineTo(w - 140, h - 52); g.moveTo(140, h / 2); g.lineTo(w - 140, h / 2); g.stroke();
    });
    const court = new THREE.Mesh(new THREE.PlaneGeometry(30, 15), std({ map: courtTex, roughness: 0.8 }));
    court.rotation.x = -Math.PI / 2; court.position.set(66, 0.08, 46); court.receiveShadow = !simple; scene3.add(court);
    box(0.1, 1, 11, M.white, 66, 0.5, 46, scene3, false);
    for (const [x, z] of [[51, 38.5], [81, 38.5], [51, 53.5], [81, 53.5]]) box(0.15, 3.2, 0.15, M.frame, x, 1.6, z, scene3, false);
    // Forecourt turntable with three supercars.
    turn = new THREE.Group(); turn.position.set(0, 0, 46); scene3.add(turn);
    const tt = new THREE.Mesh(new THREE.CylinderGeometry(11, 11, 0.4, 72), std({ color: 0x2b2f35, roughness: 0.25, metalness: 0.4 })); tt.position.y = 0.2; tt.receiveShadow = !simple; turn.add(tt);
    const ttRing = new THREE.Mesh(new THREE.TorusGeometry(11, 0.18, 8, 96), M.gold); ttRing.rotation.x = Math.PI / 2; ttRing.position.y = 0.42; turn.add(ttRing);
    const superGeo = merge([
      { geo: new THREE.BoxGeometry(4.6, 0.7, 2.0), color: 0xffffff, m: at(0, 0.55, 0) },
      { geo: new THREE.BoxGeometry(2.2, 0.5, 1.7), color: 0x14181e, m: at(-0.3, 1.1, 0) },
      { geo: new THREE.BoxGeometry(0.5, 0.12, 2.0), color: 0x14181e, m: at(-2.2, 1.15, 0) },
      ...[[1.5, 1.0], [1.5, -1.0], [-1.5, 1.0], [-1.5, -1.0]].map(([x, z]) => ({ geo: new THREE.CylinderGeometry(0.42, 0.42, 0.3, 14).rotateX(Math.PI / 2), color: 0x111111, m: at(x, 0.42, z) })),
    ]);
    const supers = new THREE.InstancedMesh(superGeo, M.paint, 3);
    [[0x111214, -6, 0.2], [0xf2c200, 0, 0], [0xc8102e, 6, -0.2]].forEach(([c, x, ry], i) => { supers.setMatrixAt(i, at(x, 0.4, 0, ry + Math.PI / 2, 1.4, 1.4, 1.4)); supers.setColorAt(i, C(c)); });
    supers.castShadow = !simple; turn.add(supers);
  }

  // Palms, hedges and lamp posts around the estate.
  {
    const palmParts = [{ geo: new THREE.CylinderGeometry(0.28, 0.45, 10, 6), color: 0x8a6d4e, m: at(0, 5, 0) }];
    for (let k = 0; k < 7; k++) { const leaf = new THREE.ConeGeometry(1.0, 6.5, 4); leaf.rotateZ(Math.PI / 2.35); leaf.translate(2.8, 9.6, 0); palmParts.push({ geo: leaf, color: 0x4f7d3a, m: at(0, 0, 0, (k / 7) * Math.PI * 2) }); }
    const spots = [];
    for (let i = 0; i < 8; i++) { const a = (i / 8) * Math.PI * 2 + Math.PI / 8; spots.push([PLAZA.x + Math.cos(a) * 24, PLAZA.z + Math.sin(a) * 24]); }
    for (let x = -94; x <= 94; x += 13) spots.push([x, 81]);
    for (let z = -50; z <= 74; z += 14) spots.push([-96, z], [96, z]);
    [[-82, 46], [-42, 46], [47, 58], [85, 58], [-18, 46], [18, 46], [-18, -12], [18, -12]].forEach((s) => spots.push(s));
    const palms = new THREE.InstancedMesh(merge(palmParts), M.vcol, spots.length);
    spots.forEach(([x, z], i) => palms.setMatrixAt(i, at(x, 0, z, i * 1.7, 1, 0.85 + (i % 5) * 0.08, 1)));
    palms.castShadow = !simple; scene3.add(palms);
    const lampPos = [];
    for (let i = 0; i < 16; i++) { const a = (i / 16) * Math.PI * 2 + 0.1; lampPos.push([PLAZA.x + Math.cos(a) * 17, PLAZA.z + Math.sin(a) * 17]); }
    const lamps = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.1, 0.16, 4, 8), M.frame, lampPos.length);
    const bulbs = new THREE.InstancedMesh(new THREE.SphereGeometry(0.32, 12, 8), M.warmLight, lampPos.length);
    lampPos.forEach(([x, z], i) => { lamps.setMatrixAt(i, at(x, 2, z)); bulbs.setMatrixAt(i, at(x, 4.1, z)); });
    scene3.add(lamps, bulbs);
    const hedges = [[-30, 64, 26, 1.4, 1.6], [30, 64, 26, 1.4, 1.6], [-46, 36, 1.6, 1.4, 12], [46, 36, 1.6, 1.4, 12]];
    const hm = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), M.hedge, hedges.length);
    hedges.forEach(([x, z, w, h, d], i) => hm.setMatrixAt(i, at(x, h / 2, z, 0, w, h, d)));
    hm.castShadow = !simple; scene3.add(hm);
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
  // A person in a suit, white shirt and a tie in the team colour. Looks are fixed per agent, so each one is recognisable.
  function character(name, teamColor, size = 1) {
    const h = hash(name);
    const suit = mat(SUIT[h % SUIT.length]), skin = mat(SKIN[(h >> 3) % SKIN.length]), hair = mat(HAIR[(h >> 6) % HAIR.length]);
    const trousers = mat(SUIT[(h >> 9) % 2 ? 0 : 1]);
    const root = new THREE.Group(); root.scale.setScalar(1.25 * size);
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
    torso.add(part(body.torso, suit, true), part(body.shirt, mat(0xffffff)), part(body.tie, mat(C(teamColor).getHex())), part(body.neck, skin));
    const head = new THREE.Group(); head.position.y = 0.86; torso.add(head);
    head.add(part(body.head, skin, true), part(body.hair, hair));
    const arm = (side) => {
      const sh = new THREE.Group(); sh.position.set(side * 0.29, 0.52, 0); sh.rotation.z = side * 0.08;
      const el = new THREE.Group(); el.position.y = -0.3;
      el.add(part(body.fore, suit), part(body.hand, skin));
      sh.add(part(body.upper, suit), el); torso.add(sh);
      return { sh, el };
    };
    const tablet = part(body.tablet, M.frame); tablet.visible = false;
    const racket = part(body.racket, mat(0x222222)); racket.visible = false;
    const c = { root, hips, legs: [leg(-1), leg(1)], torso, head, arms: [arm(-1), arm(1)], tablet, racket };
    c.arms[1].el.add(tablet, racket);
    return c;
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
  const SITES = {
    research: [-34, -14], ads: [34, -14], money: [-54, 12], creative: [54, 12],
    operations: [-30, 28], store: [30, 28], learning: [-70, -30], testing: [70, -30],
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
    const wallMesh = new THREE.Mesh(new THREE.PlaneGeometry(9, 3), new THREE.MeshBasicMaterial({ map: wall.t, toneMapped: false }));
    wallMesh.position.set(0, 4.4, -PD / 2 + 0.2); pav.add(wallMesh);
    // The team colour on a slim band above the door, and plants on the roof.
    box(4, 0.35, 0.12, std({ color: t.color, roughness: 0.4 }), 0, PH + 0.1, PD / 2 + 0.12, pav, false);
    for (const px of [-PW / 2 + 1, PW / 2 - 1]) { const plant = new THREE.Mesh(new THREE.IcosahedronGeometry(0.9, 0), M.leaf); plant.position.set(px, roofY + 1, PD / 2 - 0.2); pav.add(plant); }
    pav.updateWorldMatrix(true, true);
    const n = t.agents.length;
    const offsets = { 1: [0], 2: [-3.4, 3.4], 3: [-6, 0, 6] }[n] || t.agents.map((_, i) => (i - (n - 1) / 2) * 5);
    const desks = t.agents.map((a, i) => desk(t, a.name, pav, offsets[i], -1.6));
    const door = local(pav, 0, PD / 2 + 2.5, 0);
    const P = { team: t, pav, desks, wall, door, anchor: pav.localToWorld(new V3(0, PH + 4.5, 0)), status: "idle", helpers: [], hires: [] };
    pav.traverse((o) => { if (!o.userData.pick) o.userData.pick = t.id; });
    hitTargets.push(pav);
    // Each agent comes out of its pavilion door and takes its seat.
    desks.forEach((d, i) => {
      const A = actor(character(d.name, t.color), door, pav.rotation.y + Math.PI);
      A.name = d.name; A.desk = d; d.actor = A;
      goToDesk(A, P, REDUCED ? 0 : 0.5 + i * 0.6 + rand() * 1.5);
    });
    pavilions.set(t.id, P);
    return P;
  }
  function goToDesk(A, P, delay = 0) {
    const d = A.desk; A.seat = d.seat; A.wait = delay; A.away = false;
    if (A.leisure) { A.leisure.taken = false; A.leisure = null; }
    walk(A, [P.door, local(P.pav, d.x, 1.8), local(P.pav, d.x, 0.9)], sitDown);
  }

  // ---------------------------------------------------------------- idle life: leisure round the estate
  // An agent with no live task leaves its desk for the tennis court, the pool, the café terrace or the railing over
  // the circuit; the moment real work arrives it walks back. It is plainly leisure: panels say "idle" and where they are.
  const LEISURE = [
    ...[[56, 46, Math.PI / 2], [76, 46, -Math.PI / 2]].map(([x, z, f]) => ({ kind: "tennis", label: "on the tennis court", pos: new V3(x, 0.1, z), face: f })),
    ...Array.from({ length: 6 }, (_, i) => ({ kind: "pool", label: "by the pool", pos: new V3(-73 + i * 4.4, 0.7, 54.2), face: Math.PI, sit: true })),
    ...Array.from({ length: 6 }, (_, i) => ({ kind: "rail", label: "watching the cars", pos: new V3(-92 + i * 5.5, 0, -62.5), face: Math.PI })),
    ...Array.from({ length: 4 }, (_, i) => ({ kind: "cafe", label: "on the café terrace", pos: new V3(-8 + i * 5.3, 0, 31.5), face: Math.PI })),
  ];
  function goRelax(A, P) {
    const free = LEISURE.filter((l) => !l.taken);
    if (!free.length) return;
    const spot = free[hash(A.name) % free.length];
    spot.taken = true; A.leisure = spot; A.away = true;
    const side = Math.sign(spot.pos.x - PLAZA.x) || 1;
    walk(A, [local(P.pav, A.desk.x, 1.8), P.door, new V3(PLAZA.x + side * 23, 0, spot.pos.z > PLAZA.z ? PLAZA.z + 12 : PLAZA.z - 12), spot.pos], (x) => {
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
      walk(A, [local(P.pav, d.x, 3.4), local(P.pav, d.x + [-1.4, 1.4, 0][i % 3], 2.6)], (x) => { x.face = P.pav.rotation.y + Math.PI; });
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
    const cr = pavilions.get("creative"), rs = pavilions.get("research");
    if (!cr || !rs) return;
    if (on) {
      const later = (ms, fn) => demoTimers.push(setTimeout(fn, ms));
      dispatch("creative-director");
      later(700, () => helpers(cr, cr.desks[0], 2, true));
      later(2600, () => dispatch("market-research"));
      later(3200, () => helpers(rs, rs.desks.find((d) => d.name === "market-research") || rs.desks[0], 1, true));
      later(6500, () => hire(cr, true));
      focus("creative");
    } else {
      for (const P of pavilions.values()) {
        P.helpers.filter((h) => h.demo).forEach((h) => leave(P, h));
        P.hires.filter((h) => h.demo && !h.gone).forEach((h) => { h.gone = true; walk(h.actor, [P.door], (x) => { x.gone = true; }); });
      }
    }
  }

  // ---------------------------------------------------------------- data in
  let built = false, coreKick = 0, jarvisBusy = 0;
  function update(m) {
    if (!built) { m.teams.forEach(buildPavilion); built = true; }
    for (const t of m.teams) {
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
  }
  // Where each agent is right now, for the panels: at the desk, walking, or where it is taking its break.
  function whereabouts() {
    const out = {};
    for (const P of pavilions.values()) for (const d of P.desks) {
      const A = d.actor; if (!A) continue;
      out[d.name] = A.path.length ? (A.away ? "heading out for a break" : "walking to the desk") : A.leisure ? A.leisure.label : "at the desk";
    }
    return out;
  }

  // ---------------------------------------------------------------- camera: home view, flights, view controls
  const sph = new THREE.Spherical();
  let flight = null, lastInput = performance.now(), exploring = false;
  const fromCam = () => { const off = camera.position.clone().sub(controls.target); sph.setFromVector3(off); return { az: sph.theta, pol: sph.phi, r: sph.radius, target: controls.target.clone() }; };
  function flyTo(goal, dur = 1.4) {
    const from = fromCam();
    let daz = goal.az - from.az; daz = Math.atan2(Math.sin(daz), Math.cos(daz));
    flight = { from, goal: { ...goal, az: from.az + daz }, start: performance.now(), dur: REDUCED ? 0.001 : dur };
  }
  function place(v) { sph.set(v.r, v.pol, v.az); camera.position.copy(v.target).add(new V3().setFromSpherical(sph)); controls.target.copy(v.target); camera.lookAt(v.target); }
  function focus(id) {
    lastInput = performance.now();
    if (!id) return flyTo(HOME());
    if (id === "jarvis") return flyTo({ az: 0, pol: 1.12, r: 105, target: new V3(0, 10, -24) });
    const P = pavilions.get(id);
    if (!P) return;
    // In front of the pavilion's glass, a little above, looking in at the desks.
    const out = P.door.clone().sub(P.pav.position).setY(0).normalize();
    flyTo({ az: Math.atan2(out.x, out.z), pol: 0.98, r: 46, target: P.pav.position.clone().setY(2.5) });
  }
  function view(cmd) {
    lastInput = performance.now();
    if (cmd === "reset") { exploring = false; focus(null); return false; }
    if (cmd === "in" || cmd === "out") { const f = fromCam(); flyTo({ ...f, r: Math.max(controls.minDistance, Math.min(controls.maxDistance, f.r * (cmd === "in" ? 0.7 : 1.4))) }, 0.6); return exploring; }
    if (cmd === "explore") { exploring = !exploring; flight = null; return exploring; }
    return exploring;
  }
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  controls.addEventListener("start", () => { flight = null; if (exploring) { exploring = false; opts.onExplore?.(false); } lastInput = performance.now(); });
  controls.addEventListener("end", () => { lastInput = performance.now(); });

  // Rendering: bloom on capable devices (lit windows, the beam, the globe); plain rendering in simplified mode.
  let composer = null;
  if (!simple && addons.EffectComposer) {
    composer = new addons.EffectComposer(renderer);
    composer.addPass(new addons.RenderPass(scene3, camera));
    composer.addPass(new addons.UnrealBloomPass(new THREE.Vector2(256, 256), 0.45, 0.5, 1.15));  // only real light sources glow
    composer.addPass(new addons.OutputPass());
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
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) return;
    const rect = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
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

  // ---------------------------------------------------------------- the frame loop
  const damp = (a, b, lambda, dt) => a + (b - a) * (1 - Math.exp(-lambda * dt));
  const clock = new THREE.Clock();
  const tmp = new V3(), up = new V3(0, 1, 0), m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), one = new V3(1, 1, 1);
  let first = true, ringSpin = 0.2;
  place({ ...HOME(), az: -0.5, r: HOME().r * 1.35, pol: 1.0 });
  flyTo(HOME(), 3.2);

  function frame() {
    // Real time, so a slow device keeps pace; capped so a long pause (a hidden tab) does not jump everything.
    const dt = Math.min(clock.getDelta(), 0.25), time = clock.elapsedTime;
    if (!first && !onScreen()) return;
    if (flight) {
      flight.t = Math.min(1, (performance.now() - flight.start) / (flight.dur * 1000));
      const k = ease(flight.t), f = flight.from, g = flight.goal;
      place({ az: f.az + (g.az - f.az) * k, pol: f.pol + (g.pol - f.pol) * k, r: f.r + (g.r - f.r) * k, target: f.target.clone().lerp(g.target, k) });
      if (flight.t >= 1) flight = null;
    } else {
      controls.autoRotate = !REDUCED && (exploring || (!opts.selected() && performance.now() - lastInput > 40000));
      controls.autoRotateSpeed = exploring ? 0.9 : 0.15;
      controls.update();
      // Keep panning on the estate.
      controls.target.x = Math.max(-110, Math.min(110, controls.target.x));
      controls.target.z = Math.max(-120, Math.min(90, controls.target.z));
      controls.target.y = Math.max(0, Math.min(20, controls.target.y));
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

    for (const P of pavilions.values()) {
      for (const d of P.desks) {
        const A = d.actor, ph = d.phase + time;
        if (A && !A.leaving) {
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
    for (let i = actors.length - 1; i >= 0; i--) {
      const A = actors[i];
      stepActor(A, dt, time);
      if (A.gone && A.grow < 0.02) { scene3.remove(A.c.root); actors.splice(i, 1); }
    }
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

    // Scenery: ripples, the turntable, and the cars on the circuit.
    if (!REDUCED) {
      rippleTex.offset.x += dt * 0.004; rippleTex.offset.y += dt * 0.0025;
      poolRipple.offset.x += dt * 0.02;
      turn.rotation.y += dt * 0.08;
      const cs = world.cars;
      const order = [...cs.list].sort((a, b) => a.u - b.u);
      cs.list.forEach((c, i) => {
        // Look ahead for the next corner, brake for it, and don't drive through the car in front.
        const idx = Math.floor(((c.u + 12 / cs.len) % 1) * cs.bend.length) % cs.bend.length;
        let want = c.top * (1 - Math.min(0.68, cs.bend[idx] * 1.9));
        const k = order.indexOf(c), ahead = order[(k + 1) % order.length];
        const gap = ((((ahead.u - c.u) % 1) + 1) % 1) * cs.len;
        if (ahead !== c && gap < 20) want = Math.min(want, ahead.v * (gap < 11 ? 0.85 : 0.99));
        c.v = damp(c.v, want, want < c.v ? 2.4 : 0.9, dt);
        c.u = (c.u + (c.v * dt) / cs.len) % 1;
        const p = world.curve.getPointAt(c.u), t = world.curve.getTangentAt(c.u);
        q.setFromAxisAngle(up, Math.atan2(-t.z, t.x));
        m4.compose(p, q, one); cs.mesh.setMatrixAt(i, m4);
      });
      cs.mesh.instanceMatrix.needsUpdate = true;
    }

    if (composer) composer.render(); else renderer.render(scene3, camera);

    // Glass chips follow their buildings on screen.
    for (const el of tags.children) {
      const id = el.dataset.id;
      const P = pavilions.get(id);
      const anchor = id === "jarvis" ? tmp.set(0, 33, -38) : P ? tmp.copy(P.anchor) : null;
      if (!anchor) { el.style.opacity = 0; continue; }
      const v = anchor.clone().project(camera);
      const dist = camera.position.distanceTo(anchor);
      const visible = v.z < 1 && Math.abs(v.x) < 1.1 && Math.abs(v.y) < 1.1 && !(open && id === opts.selected());
      const x = (v.x * 0.5 + 0.5) * W, y = (-v.y * 0.5 + 0.5) * H;
      el.style.transform = `translate(${Math.round(x - el.offsetWidth / 2)}px, ${Math.round(y - el.offsetHeight)}px)`;
      el.style.opacity = visible ? "1" : "0";
      el.style.zIndex = String(2000 - Math.round(dist));
      el.style.pointerEvents = visible ? "auto" : "none";
    }
    if (first) { first = false; requestAnimationFrame(() => veil.classList.add("gone")); }
  }
  renderer.setAnimationLoop(frame);
  // For inspecting the view from the browser console: snap() renders one frame and returns it as an image.
  stage.hq = { camera, controls, actors, snap: (q = 0.85) => { if (composer) composer.render(); else renderer.render(scene3, camera); return canvas.toDataURL("image/jpeg", q); } };
  return { update, dispatch, focus, demo, view, whereabouts, simple };
}

// The scenery objects the frame loop moves; filled in while the world is built.
const world = {};
