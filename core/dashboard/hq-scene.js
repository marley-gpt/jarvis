// The 3D HQ: the top floor of the JARVIS tower above a Monaco-style harbour.
//
// startScene() builds the world in the given stage and returns { update(model), dispatch(agent), focus(id) }.
// It only draws what the page's data adapter (hq.js) hands it; the city, yachts and cars are scenery.

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
let STATUS = {};

export async function startScene(opts) {
  STATUS = opts.status;
  let THREE, OrbitControls, RoomEnvironment;
  try {
    THREE = await import("three");
    ({ OrbitControls } = await import("three/addons/controls/OrbitControls.js"));
    ({ RoomEnvironment } = await import("three/addons/environments/RoomEnvironment.js"));
  } catch (e) {
    return opts.fail("The 3D view needs an internet connection the first time, to load three.js. Everything else works.");
  }
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas: opts.canvas, antialias: true, powerPreference: "high-performance" }); }
  catch (e) { return opts.fail("This device can't show the 3D view. Everything else works."); }
  try { await document.fonts.ready; } catch (e) { /* system fonts are fine */ }
  return buildWorld(THREE, OrbitControls, RoomEnvironment, renderer, opts);
}

function buildWorld(THREE, OrbitControls, RoomEnvironment, renderer, opts) {
  const { canvas, stage, labels: tags, veil } = opts;
  const isPhone = () => stage.clientWidth <= 700;
  const V3 = THREE.Vector3, C = (hex) => new THREE.Color(hex);
  const phone = isPhone();
  renderer.setPixelRatio(Math.min(devicePixelRatio, phone ? 1.6 : 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.98;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene3 = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene3.environment = pmrem.fromScene(new RoomEnvironment(renderer), 0.04).texture;
  scene3.fog = new THREE.Fog(0xd9ecf8, 260, 1500);

  const camera = new THREE.PerspectiveCamera(phone ? 52 : 40, 1, 0.5, 4000);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = 0.07; controls.enablePan = false;
  controls.minDistance = 20; controls.maxDistance = 120; controls.minPolarAngle = 0.32; controls.maxPolarAngle = 1.36;
  controls.rotateSpeed = 0.55; controls.zoomSpeed = 0.7;
  controls.target.set(0, 1.5, 0);
  // Low enough over the floor that the harbour and the open sea show beyond it.
  const HOME = { az: 0.0, pol: phone ? 1.1 : 1.13, r: phone ? 78 : 58, target: new V3(0, phone ? 6 : 2, 0) };

  // ---------------------------------------------------------------- materials
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
  const veins = (g, w, h, base, tones, n) => {
    g.fillStyle = base; g.fillRect(0, 0, w, h);
    for (let i = 0; i < n; i++) {
      const [col, a0] = tones[i % tones.length];
      g.strokeStyle = col.replace("A", (a0 * (0.4 + rand() * 0.9)).toFixed(3));
      g.lineWidth = 0.5 + rand() * 2.2; g.beginPath();
      let x = rand() * w, y = rand() * h, ang = rand() * Math.PI * 2;
      g.moveTo(x, y);
      for (let k = 0; k < 40; k++) { ang += (rand() - 0.5) * 0.7; x += Math.cos(ang) * 22; y += Math.sin(ang) * 22; g.lineTo(x, y); }
      g.stroke();
    }
  };
  const marbleTex = canvasTex(1024, 1024, (g, w, h) => veins(g, w, h, "#f4f3f0",
    [["rgba(118,124,136,A)", 0.22], ["rgba(150,154,162,A)", 0.14], ["rgba(184,147,63,A)", 0.16]], 90), 3);
  const darkMarbleTex = canvasTex(512, 512, (g, w, h) => veins(g, w, h, "#18202d",
    [["rgba(235,236,240,A)", 0.35], ["rgba(214,186,120,A)", 0.3]], 50), 1);

  const M = {
    marble: new THREE.MeshStandardMaterial({ map: marbleTex, roughness: 0.22, metalness: 0.0, envMapIntensity: 0.8 }),
    darkMarble: new THREE.MeshStandardMaterial({ map: darkMarbleTex, roughness: 0.18, metalness: 0.1, envMapIntensity: 1.1 }),
    gold: new THREE.MeshStandardMaterial({ color: 0xcfa951, metalness: 1, roughness: 0.26, envMapIntensity: 1.25 }),
    glass: new THREE.MeshStandardMaterial({ color: 0xd9eefa, metalness: 0.1, roughness: 0.04, transparent: true, opacity: 0.16, depthWrite: false, side: THREE.DoubleSide, envMapIntensity: 1.6 }),
    walnut: new THREE.MeshStandardMaterial({ color: 0x5b3a25, roughness: 0.42, metalness: 0.05 }),
    leather: new THREE.MeshStandardMaterial({ color: 0xf1ede6, roughness: 0.55 }),
    ceramic: new THREE.MeshStandardMaterial({ color: 0xf7f7f9, roughness: 0.22, metalness: 0.05, envMapIntensity: 1.1 }),
    visor: new THREE.MeshStandardMaterial({ color: 0x0f1a2b, roughness: 0.08, metalness: 0.6 }),
    frame: new THREE.MeshStandardMaterial({ color: 0x1b2533, roughness: 0.35, metalness: 0.4 }),
    white: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.6 }),
    tower: new THREE.MeshStandardMaterial({ color: 0x8fb9d6, metalness: 0.75, roughness: 0.07, envMapIntensity: 1.4 }),
    leaf: new THREE.MeshStandardMaterial({ color: 0x5f7a45, roughness: 0.85, flatShading: true }),
    vcol: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.6 }),
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

  // ---------------------------------------------------------------- light and sky
  scene3.add(new THREE.HemisphereLight(0xcfe6ff, 0xd9c29a, 0.85));
  const sun = new THREE.DirectionalLight(0xfff0d8, 2.3);
  sun.position.set(-38, 70, 34);
  sun.castShadow = true;
  sun.shadow.mapSize.set(phone ? 1024 : 2048, phone ? 1024 : 2048);
  Object.assign(sun.shadow.camera, { left: -32, right: 32, top: 32, bottom: -32, near: 10, far: 180 });
  sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.03;
  scene3.add(sun);
  const sky = new THREE.Mesh(new THREE.SphereGeometry(2600, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: C(0x4f9de0) }, mid: { value: C(0xa9d3f2) }, low: { value: C(0xe8f4fb) } },
    vertexShader: "varying vec3 vp; void main(){ vp = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
    fragmentShader: "uniform vec3 top; uniform vec3 mid; uniform vec3 low; varying vec3 vp; void main(){ float h = vp.y; vec3 c = mix(low, mid, smoothstep(-0.02, 0.12, h)); c = mix(c, top, smoothstep(0.12, 0.6, h)); gl_FragColor = vec4(c, 1.0); }",
  }));
  scene3.add(sky);

  // ---------------------------------------------------------------- the city and harbour, far below
  const G = -50;                       // street level
  const LAND = [[-1600, 900], [1600, 900], [1600, -150], [420, -170], [260, -205], [190, -262], [170, -318], [140, -340],
    [100, -330], [78, -300], [74, -196], [-74, -196], [-78, -300], [-104, -322], [-150, -312], [-230, -270], [-360, -232],
    [-520, -214], [-1600, -200]];
  const inLand = (x, z) => {
    let inside = false;
    for (let i = 0, j = LAND.length - 1; i < LAND.length; j = i++) {
      const [xi, zi] = LAND[i], [xj, zj] = LAND[j];
      if ((zi > z) !== (zj > z) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
    }
    return inside;
  };
  const shoreDist = (x, z) => {
    let best = 1e9;
    for (let i = 0; i < LAND.length; i++) {
      const [ax, az] = LAND[i], [bx, bz] = LAND[(i + 1) % LAND.length];
      const dx = bx - ax, dz = bz - az, t = Math.max(0, Math.min(1, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)));
      best = Math.min(best, Math.hypot(x - ax - dx * t, z - az - dz * t));
    }
    return best;
  };
  {
    // Water: deep Mediterranean blue, turquoise along the shore, a moving ripple.
    const sea = new THREE.PlaneGeometry(4200, 4200, 90, 90);
    sea.rotateX(-Math.PI / 2);
    const p = sea.attributes.position, cols = [];
    const shallow = C(0x37b5c8), deep = C(0x1361a0), far = C(0x2a73ad);
    for (let i = 0; i < p.count; i++) {
      const x = p.getX(i), z = p.getZ(i), d = inLand(x, z) ? 0 : shoreDist(x, z);
      const c = shallow.clone().lerp(deep, Math.min(1, d / 120)).lerp(far, Math.min(1, Math.max(0, (d - 500) / 900)));
      cols.push(c.r, c.g, c.b);
    }
    sea.setAttribute("color", new THREE.Float32BufferAttribute(cols, 3));
    const ripple = canvasTex(256, 256, (g, w, h) => {
      const img = g.createImageData(w, h);
      const hgt = (x, y) => Math.sin(x * 0.098 + Math.sin(y * 0.05) * 2) * 0.5 + Math.sin(y * 0.122 + x * 0.03) * 0.35 + Math.sin((x + y) * 0.21) * 0.15;
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        const dx = hgt(x + 1, y) - hgt(x - 1, y), dy = hgt(x, y + 1) - hgt(x, y - 1), i = (y * w + x) * 4;
        img.data[i] = 128 + dx * 60; img.data[i + 1] = 128 + dy * 60; img.data[i + 2] = 255; img.data[i + 3] = 255;
      }
      g.putImageData(img, 0, 0);
    }, 70);
    ripple.colorSpace = THREE.NoColorSpace;
    const water = new THREE.Mesh(sea, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.16, metalness: 0.05,
      normalMap: ripple, normalScale: new THREE.Vector2(0.35, 0.35), envMapIntensity: 0.9 }));
    water.position.y = G - 1.6;
    scene3.add(water);
    world.ripple = ripple;

    // Land: pale stone quays and streets.
    const shape = new THREE.Shape(LAND.map(([x, z]) => new THREE.Vector2(x, -z)));
    const land = new THREE.ExtrudeGeometry(shape, { depth: 4, bevelEnabled: false });
    land.rotateX(-Math.PI / 2);
    const landMesh = new THREE.Mesh(land, new THREE.MeshStandardMaterial({ color: 0xc9bca4, roughness: 0.92 }));
    landMesh.position.y = G - 4;
    scene3.add(landMesh);

    // Hills and the mountains behind the town; the Rock headland on the right.
    const hill = new THREE.IcosahedronGeometry(1, 2);
    const hills = [[-430, -150, 150, 70], [-700, 60, 260, 130], [420, -230, 120, 46], [600, 80, 280, 150], [-200, 520, 420, 210],
      [250, 600, 460, 240], [900, -60, 260, 110], [-1000, -60, 300, 120], [170, -280, 46, 20]];
    const hm = new THREE.InstancedMesh(hill, new THREE.MeshStandardMaterial({ color: 0x6f8d50, roughness: 0.95, flatShading: true }), hills.length);
    hills.forEach(([x, z, r, h], i) => hm.setMatrixAt(i, at(x, G - 4, z, i, r, h, r * 0.9)));
    scene3.add(hm);

    // The circuit: a closed street loop along the harbour and up through the town, red and white kerbs.
    const ctrl = [[-66, -184], [0, -186], [66, -184], [112, -164], [150, -120], [146, -66], [104, -40], [52, -58], [10, -84],
      [-38, -70], [-60, -40], [-104, -52], [-140, -96], [-132, -152], [-100, -178]];
    const curve = new THREE.CatmullRomCurve3(ctrl.map(([x, z]) => new V3(x, G + 0.06, z)), true, "centripetal");
    world.curve = curve;
    const N = 600, pts = curve.getSpacedPoints(N);
    const ribbon = (inner, outer, y, uRep) => {
      const pos = [], uv = [], idx = [];
      for (let i = 0; i <= N; i++) {
        const p0 = pts[i % N], p1 = pts[(i + 1) % N], t = p1.clone().sub(p0).normalize(), n = new V3(-t.z, 0, t.x);
        pos.push(p0.x + n.x * inner, y, p0.z + n.z * inner, p0.x + n.x * outer, y, p0.z + n.z * outer);
        uv.push((i / N) * uRep, 0, (i / N) * uRep, 1);
        if (i < N) idx.push(i * 2, i * 2 + 1, i * 2 + 2, i * 2 + 1, i * 2 + 3, i * 2 + 2);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
      g.setIndex(idx); g.computeVertexNormals();
      return g;
    };
    const asphalt = new THREE.Mesh(ribbon(-4, 4, G + 0.06, 1), new THREE.MeshStandardMaterial({ color: 0x4a4f57, roughness: 0.85, side: THREE.DoubleSide }));
    const kerbTex = canvasTex(64, 8, (g) => { g.fillStyle = "#ffffff"; g.fillRect(0, 0, 64, 8); g.fillStyle = "#d4202f"; g.fillRect(0, 0, 32, 8); });
    kerbTex.wrapS = THREE.RepeatWrapping; kerbTex.repeat.set(380, 1);
    const kerbMat = new THREE.MeshStandardMaterial({ map: kerbTex, roughness: 0.7, side: THREE.DoubleSide });
    scene3.add(asphalt, new THREE.Mesh(ribbon(4, 5.2, G + 0.09, 1), kerbMat), new THREE.Mesh(ribbon(-5.2, -4, G + 0.09, 1), kerbMat));

    // Grandstands along the harbour straight.
    const stands = [];
    for (let s = 0; s < 3; s++) for (let r = 0; r < 4; r++) {
      stands.push({ geo: new THREE.BoxGeometry(34, 1.4, 2.4), color: r % 2 ? 0xffffff : 0xc81d2c, m: at(-44 + s * 44, G + 0.7 + r * 1.4, -172 + r * 2.4) });
    }
    scene3.add(new THREE.Mesh(merge(stands), M.vcol));

    // Town: pastel Riviera blocks with white roofs, kept off the road, the water and the tower.
    const near = (x, z, d) => pts.some((p, i) => i % 6 === 0 && Math.hypot(p.x - x, p.z - z) < d);
    const blocks = [];
    const tones = [0xf0dcbc, 0xe7c29b, 0xf4e7d2, 0xdfae80, 0xf7f1e6, 0xd9b48c, 0xeed2b0, 0xe9cfae, 0xf2c9a8];
    seed = 11;
    for (let tries = 0; tries < 2600 && blocks.length < (phone ? 300 : 460); tries++) {
      const x = (rand() - 0.5) * 900, z = -330 + rand() * 640;
      if (!inLand(x, z) || shoreDist(x, z) < 12 || Math.hypot(x, z) < 52 || near(x, z, 11)) continue;
      if (x > -82 && x < 82 && z < -186) continue;
      // Low along the view from the tower to the harbour, taller up the hillsides.
      const view = Math.abs(x) < 120 && z < -40;
      const w = 8 + rand() * 12, d = 8 + rand() * 12, h = 6 + rand() * (view ? 6 : Math.abs(x) > 180 ? 30 : 16);
      blocks.push({ x, z, w, d, h, c: tones[(rand() * tones.length) | 0], ry: (rand() - 0.5) * 0.5 });
    }
    const box = new THREE.BoxGeometry(1, 1, 1);
    const town = new THREE.InstancedMesh(box, new THREE.MeshStandardMaterial({ roughness: 0.8 }), blocks.length);
    const roofs = new THREE.InstancedMesh(box, new THREE.MeshStandardMaterial({ roughness: 0.75 }), blocks.length);
    const roofTones = [0xb8613f, 0xc4704c, 0xa95a3c, 0xf3f1ec];
    blocks.forEach((b, i) => {
      town.setMatrixAt(i, at(b.x, G + b.h / 2, b.z, b.ry, b.w, b.h, b.d));
      roofs.setMatrixAt(i, at(b.x, G + b.h + 0.3, b.z, b.ry, b.w * 0.92, 0.6, b.d * 0.92));
      town.setColorAt(i, C(b.c));
      roofs.setColorAt(i, C(roofTones[i % 4]));
    });
    scene3.add(town, roofs);

    // Palms along the harbour boulevard.
    const palmParts = [{ geo: new THREE.CylinderGeometry(0.25, 0.4, 9, 6), color: 0x8a6d4e, m: at(0, 4.5, 0) }];
    for (let k = 0; k < 6; k++) {
      const leaf = new THREE.ConeGeometry(0.9, 6, 4); leaf.rotateZ(Math.PI / 2.4); leaf.translate(2.6, 8.6, 0);
      palmParts.push({ geo: leaf, color: 0x4f7d3a, m: at(0, 0, 0, (k / 6) * Math.PI * 2) });
    }
    const palmGeo = merge(palmParts);
    const palmSpots = [];
    for (let x = -66; x <= 66; x += 11) palmSpots.push([x, -192.5]);
    for (let z = -296; z <= -205; z += 13) palmSpots.push([-81, z], [81, z]);
    const palms = new THREE.InstancedMesh(palmGeo, M.vcol, palmSpots.length);
    palmSpots.forEach(([x, z], i) => palms.setMatrixAt(i, at(x, G, z, i * 1.7)));
    scene3.add(palms);

    // Piers, the moored yachts and superyachts at anchor outside the harbour.
    const piers = [];
    for (const x of [-46, -16, 14, 44]) piers.push({ geo: new THREE.BoxGeometry(3, 1, 74), color: 0xf2efe9, m: at(x, G - 1.2, -238) });
    piers.push({ geo: new THREE.BoxGeometry(110, 2, 6), color: 0xe8e2d6, m: at(-12, G - 1, -334) });
    scene3.add(new THREE.Mesh(merge(piers), M.vcol));
    const hullShape = new THREE.Shape([[-0.5, -0.17], [0.22, -0.17], [0.5, 0], [0.22, 0.17], [-0.5, 0.17]].map(([x, y]) => new THREE.Vector2(x, y)));
    const hull = new THREE.ExtrudeGeometry(hullShape, { depth: 0.1, bevelEnabled: false }); hull.rotateX(-Math.PI / 2);
    const deck1 = new THREE.ExtrudeGeometry(new THREE.Shape([[-0.42, -0.13], [0.12, -0.13], [0.26, 0], [0.12, 0.13], [-0.42, 0.13]].map(([x, y]) => new THREE.Vector2(x, y))), { depth: 0.06, bevelEnabled: false });
    deck1.rotateX(-Math.PI / 2); deck1.translate(0, 0.1, 0);
    const band = new THREE.BoxGeometry(0.5, 0.03, 0.27); band.translate(-0.12, 0.135, 0);
    const deck2 = new THREE.BoxGeometry(0.34, 0.05, 0.2); deck2.translate(-0.16, 0.175, 0);
    const yachtGeo = merge([{ geo: hull, color: 0xffffff }, { geo: deck1, color: 0xf7f7f7 }, { geo: band, color: 0x1c2a3d }, { geo: deck2, color: 0xffffff }]);
    const yachts = [];
    seed = 23;
    for (const x of [-46, -16, 14, 44]) for (let z = -206; z > -270; z -= 5.6) {
      for (const side of [-1, 1]) if (rand() > 0.12) {
        const L = 10 + rand() * 9;
        yachts.push({ x: x + side * (L / 2 + 2), z, ry: side > 0 ? 0 : Math.PI, L, c: 0xffffff });
      }
    }
    for (let z = -214; z > -296; z -= 27) { yachts.push({ x: -68, z, ry: Math.PI / 2, L: 20 + rand() * 5, c: 0xffffff }); yachts.push({ x: 68, z: z - 10, ry: -Math.PI / 2, L: 19 + rand() * 6, c: 0xffffff }); }
    const anchored = [[-150, -430, 0.4, 40, 0x1f2c44], [40, -480, -0.3, 46, 0xffffff], [215, -410, 0.9, 34, 0x8c96a3], [-310, -390, 2.1, 38, 0xffffff],
      [340, -540, 1.4, 42, 0x10233f], [-40, -640, 0.2, 50, 0xffffff], [160, -720, 2.6, 38, 0xffffff]];
    anchored.forEach(([x, z, ry, L, c]) => yachts.push({ x, z, ry, L, c }));
    const fleet = new THREE.InstancedMesh(yachtGeo, M.vcol, yachts.length);
    yachts.forEach((y, i) => { fleet.setMatrixAt(i, at(y.x, G - 1.7, y.z, y.ry, y.L, y.L * 0.9, y.L)); fleet.setColorAt(i, C(y.c)); });
    scene3.add(fleet);

    // Tenders crossing the bay with a wake.
    const wakeTex = canvasTex(64, 256, (g, w, h) => { const gr = g.createLinearGradient(0, 0, 0, h); gr.addColorStop(0, "rgba(255,255,255,.85)"); gr.addColorStop(1, "rgba(255,255,255,0)"); g.fillStyle = gr; g.beginPath(); g.moveTo(w / 2 - 4, 0); g.lineTo(w / 2 + 4, 0); g.lineTo(w, h); g.lineTo(0, h); g.fill(); });
    world.tenders = [];
    for (let k = 0; k < 4; k++) {
      const grp = new THREE.Group();
      const boat = new THREE.Mesh(yachtGeo, M.vcol); boat.scale.setScalar(12);
      const wake = new THREE.Mesh(new THREE.PlaneGeometry(14, 60), new THREE.MeshBasicMaterial({ map: wakeTex, transparent: true, depthWrite: false }));
      wake.rotation.x = -Math.PI / 2; wake.rotation.z = -Math.PI / 2; wake.position.set(-36, 0.05, 0);
      grp.add(boat, wake); grp.position.y = G - 1.65;
      scene3.add(grp);
      world.tenders.push({ grp, cx: -60 + k * 90, cz: -470 - k * 60, r: 120 + k * 40, speed: 0.012 + k * 0.004, a: k * 1.6 });
    }

    // Supercars on the circuit (scenery), slowing for the corners.
    const carGeo = merge([
      { geo: new THREE.BoxGeometry(4.6, 0.8, 2.1), color: 0xffffff, m: at(0, 0.55, 0) },
      { geo: new THREE.BoxGeometry(2.0, 0.55, 1.6), color: 0x1a1f26, m: at(-0.3, 1.15, 0) },
      { geo: new THREE.BoxGeometry(0.4, 0.25, 2.2), color: 0x1a1f26, m: at(-2.2, 1.05, 0) },
    ]);
    const carColors = [0xf28c1b, 0xc8102e, 0x7cc42d, 0xf5c518, 0xf4f4f4, 0x1d2a3f, 0x2f6fd6, 0xc8102e, 0xf28c1b];
    const cars = new THREE.InstancedMesh(carGeo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.25, metalness: 0.4, envMapIntensity: 1.3 }), carColors.length);
    carColors.forEach((c, i) => cars.setColorAt(i, C(c)));
    scene3.add(cars);
    const len = curve.getLength();
    const bend = pts.map((p, i) => { const a = pts[(i + N - 4) % N], b = pts[(i + 4) % N]; return a.clone().sub(p).normalize().dot(b.clone().sub(p).normalize()) + 1; });
    world.cars = { mesh: cars, len, bend, list: carColors.map((_, i) => ({ u: i / carColors.length, v: 0, top: 26 + (i % 3) * 3 })) };
  }

  // ---------------------------------------------------------------- the tower and the HQ floor
  const R = 26;
  {
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(R - 1, R + 3, -G + 2, 72, 1, true), M.tower);
    shaft.position.y = G / 2 - 1.5;
    scene3.add(shaft);
    const ring = new THREE.TorusGeometry(R - 0.2, 0.16, 6, 96); ring.rotateX(Math.PI / 2);
    const bands = new THREE.InstancedMesh(ring, M.gold, 12);
    for (let i = 0; i < 12; i++) { const y = -4 - i * 4; const s = 1 + (-y / -G) * (4 / (R - 1)) * 0.95; bands.setMatrixAt(i, at(0, y, 0, 0, s, 1, s)); }
    scene3.add(bands);

    const slab = new THREE.Mesh(new THREE.CylinderGeometry(R, R, 1.2, 96), M.white);
    slab.position.y = -0.6; slab.receiveShadow = true;
    const top = new THREE.Mesh(new THREE.CircleGeometry(R, 96), M.marble);
    top.rotation.x = -Math.PI / 2; top.position.y = 0.005; top.receiveShadow = true;
    const rim = new THREE.Mesh(new THREE.TorusGeometry(R, 0.2, 8, 128), M.gold); rim.rotation.x = Math.PI / 2;
    const parapet = new THREE.Mesh(new THREE.CylinderGeometry(R - 0.15, R - 0.15, 1.7, 128, 1, true), M.glass); parapet.position.y = 0.85;
    const rail = new THREE.Mesh(new THREE.TorusGeometry(R - 0.15, 0.07, 6, 128), M.gold); rail.rotation.x = Math.PI / 2; rail.position.y = 1.7;
    scene3.add(slab, top, rim, parapet, rail);

    // The dais at the centre where JARVIS sits.
    const dais = new THREE.Mesh(new THREE.CylinderGeometry(6.4, 6.8, 0.36, 72), M.darkMarble);
    dais.position.y = 0.18; dais.receiveShadow = true; dais.castShadow = true;
    const daisRim = new THREE.Mesh(new THREE.TorusGeometry(6.45, 0.07, 6, 96), M.gold); daisRim.rotation.x = Math.PI / 2; daisRim.position.y = 0.37;
    const plinth = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.9, 1.6, 32), M.gold); plinth.position.y = 1.15; plinth.castShadow = true;
    scene3.add(dais, daisRim, plinth);
  }

  // JARVIS: a luminous core inside a glass shell, with gold rings that turn faster when it is busy.
  const core = new THREE.Group(); core.position.set(0, 4.1, 0); scene3.add(core);
  const coreMat = new THREE.MeshStandardMaterial({ color: 0x9fe3ff, emissive: 0x2fb8ff, emissiveIntensity: 2.2, roughness: 0.3 });
  const coreMesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1.05, 3), coreMat);
  const shell = new THREE.Mesh(new THREE.SphereGeometry(1.75, 48, 32), new THREE.MeshStandardMaterial({ color: 0xe8f7ff, transparent: true, opacity: 0.2, roughness: 0.02, metalness: 0.2, envMapIntensity: 2, depthWrite: false }));
  const glowTex = canvasTex(128, 128, (g, w) => { const gr = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2); gr.addColorStop(0, "rgba(120,215,255,.9)"); gr.addColorStop(0.35, "rgba(70,180,255,.35)"); gr.addColorStop(1, "rgba(70,180,255,0)"); g.fillStyle = gr; g.fillRect(0, 0, w, w); });
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, transparent: true, depthWrite: false })); halo.scale.setScalar(9);
  const rings = [0, 1, 2].map((i) => {
    const r = new THREE.Mesh(new THREE.TorusGeometry(2.3 + i * 0.32, 0.035, 8, 120), M.gold);
    r.rotation.set(Math.PI / 2 + (i - 1) * 0.5, i * 0.7, 0); core.add(r); return r;
  });
  core.add(coreMesh, shell, halo);
  const coreLight = new THREE.PointLight(0x5cc8ff, 30, 24, 2); core.add(coreLight);

  // ---------------------------------------------------------------- the eight suites
  const SUITE_R = 16.6;
  const suites = new Map();
  const hitTargets = [];
  const lampGlow = canvasTex(64, 64, (g, w) => { const gr = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2); gr.addColorStop(0, "rgba(255,255,255,1)"); gr.addColorStop(0.4, "rgba(255,255,255,.4)"); gr.addColorStop(1, "rgba(255,255,255,0)"); g.fillStyle = gr; g.fillRect(0, 0, w, w); });
  const flowTex = canvasTex(256, 8, (g, w, h) => { const gr = g.createLinearGradient(0, 0, w, 0); gr.addColorStop(0, "rgba(255,255,255,0)"); gr.addColorStop(0.7, "rgba(255,255,255,.9)"); gr.addColorStop(1, "rgba(255,255,255,0)"); g.fillStyle = gr; g.fillRect(0, 0, w, h); });

  function screenTexture() {
    const c = document.createElement("canvas"); c.width = 512; c.height = 300;
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
    return { c, t };
  }
  function drawScreen(sc, team, a) {
    const g = sc.c.getContext("2d"), W = 512, H = 300, st = STATUS[a.status];
    g.fillStyle = "#0f1b2c"; g.fillRect(0, 0, W, H);
    g.fillStyle = team.color; g.fillRect(0, 0, W, 8);
    g.fillStyle = "#ffffff"; g.font = "600 30px 'Instrument Sans', sans-serif"; g.fillText(a.label, 26, 58);
    g.fillStyle = st.color; g.beginPath(); g.arc(W - 40, 48, 10, 0, Math.PI * 2); g.fill();
    g.font = "500 20px 'Instrument Sans', sans-serif"; g.fillText(st.word, W - 40 - g.measureText(st.word).width - 20, 55);
    const text = a.now || (a.next ? `Next up: ${a.next}` : "Nothing running");
    g.fillStyle = a.now ? "#e8eef7" : "#8796ab"; g.font = "400 24px 'Instrument Sans', sans-serif";
    const words = text.split(/\s+/); let line = "", y = 108, n = 0;
    for (const w of words) {
      const test = line ? line + " " + w : w;
      if (g.measureText(test).width > W - 52 && line) { g.fillText(n === 3 ? line + "…" : line, 26, y); y += 34; line = w; if (++n > 3) break; }
      else line = test;
    }
    if (n <= 3 && line) g.fillText(line, 26, y);
    g.fillStyle = "#8796ab"; g.font = "400 19px 'Instrument Sans', sans-serif";
    const foot = a.status === "working" ? (a.tool ? `Using ${a.tool}` : "Tool not reported yet") : `${a.open.length} open task${a.open.length === 1 ? "" : "s"}`;
    g.fillText(foot, 26, H - 26);
    sc.t.needsUpdate = true;
  }

  function avatar(color) {
    const grp = new THREE.Group();
    const suit = new THREE.MeshStandardMaterial({ color, roughness: 0.5, metalness: 0.05 });
    // Seated: the body sits on the chair (seat top at 0.62), the head just above the screen.
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.34, 0.5, 6, 16), suit); body.position.y = 1.21; body.castShadow = true;
    const head = new THREE.Group(); head.position.y = 2.1;
    const skull = new THREE.Mesh(new THREE.SphereGeometry(0.3, 32, 20), M.ceramic); skull.castShadow = true;
    const visor = new THREE.Mesh(new THREE.SphereGeometry(0.305, 32, 12, Math.PI / 2 - 0.9, 1.8, 1.25, 0.55), M.visor);
    head.add(skull, visor);
    const arm = (side) => {
      const pivot = new THREE.Group(); pivot.position.set(side * 0.4, 1.64, 0);
      const limb = new THREE.Mesh(new THREE.CapsuleGeometry(0.1, 0.46, 4, 8), suit); limb.position.y = -0.28; limb.castShadow = true;
      pivot.add(limb); pivot.rotation.x = -0.9; grp.add(pivot); return pivot;
    };
    grp.add(body, head);
    const arms = [arm(-1), arm(1)];
    return { grp, body, head, arms, suit };
  }

  function desk(team, agentName, angle, offset) {
    const grp = new THREE.Group();
    const radial = new V3(Math.cos(angle), 0, Math.sin(angle)), tangent = new V3(-Math.sin(angle), 0, Math.cos(angle));
    grp.position.copy(radial.clone().multiplyScalar(SUITE_R).add(tangent.clone().multiplyScalar(offset)));
    grp.rotation.y = Math.PI / 2 - angle;   // local +z points outward, towards the harbour
    grp.scale.setScalar(1.3);
    const topM = new THREE.Mesh(new THREE.BoxGeometry(2.5, 0.09, 1.15), M.walnut); topM.position.y = 0.8; topM.castShadow = topM.receiveShadow = true;
    const frameM = new THREE.Mesh(new THREE.BoxGeometry(2.3, 0.05, 0.9), M.gold); frameM.position.y = 0.73;
    grp.add(topM, frameM);
    for (const sx of [-1.1, 1.1]) { const leg = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.72, 0.9), M.gold); leg.position.set(sx, 0.37, 0); grp.add(leg); }
    const sc = screenTexture();
    const mon = new THREE.Group(); mon.position.set(0, 1.42, -0.3);
    const bezel = new THREE.Mesh(new THREE.BoxGeometry(1.62, 0.98, 0.05), M.frame); bezel.castShadow = true;
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.54, 0.9), new THREE.MeshBasicMaterial({ map: sc.t, toneMapped: false }));
    screen.position.z = 0.03;
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.05, 0.42), M.gold); stem.position.y = -0.62;
    mon.add(bezel, screen, stem); grp.add(mon);
    const chair = new THREE.Group(); chair.position.set(0, 0, 0.95);
    const seat = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.14, 0.75), M.leather); seat.position.y = 0.55; seat.castShadow = true;
    const back = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.85, 0.12), M.leather); back.position.set(0, 1.0, 0.36); back.castShadow = true;
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.3, 0.5, 12), M.gold); base.position.y = 0.25;
    chair.add(seat, back, base); grp.add(chair);
    const av = avatar(team.color);
    av.grp.position.set(0, 0, 0.95); av.grp.rotation.y = Math.PI;  // faces its screen and, past it, JARVIS
    grp.add(av.grp);
    // Status lamp: a gold stem with a glowing bead.
    const lamp = new THREE.Group(); lamp.position.set(1.0, 0.84, -0.35);
    const lstem = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.05, 0.55), M.gold); lstem.position.y = 0.27;
    const bulbMat = new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0x9aa6b5, emissiveIntensity: 1.2 });
    const bulb = new THREE.Mesh(new THREE.SphereGeometry(0.1, 16, 12), bulbMat); bulb.position.y = 0.6;
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: lampGlow, color: 0x9aa6b5, transparent: true, depthWrite: false, opacity: 0.6 }));
    glow.position.y = 0.6; glow.scale.setScalar(0.9);
    lamp.add(lstem, bulb, glow); grp.add(lamp);
    grp.traverse((o) => { o.userData.team = team.id; });
    return { name: agentName, grp, av, sc, bulbMat, glow, lampPos: new V3(), status: "idle", pose: { lean: 0, look: 0, type: 0 }, phase: rand() * 10 };
  }

  function buildSuites(teams) {
    const offsets = { 1: [0], 2: [-2.6, 2.6], 3: [-4.2, 0, 4.2] };
    teams.forEach((t, k) => {
      const angle = Math.PI / 2 + (k * Math.PI) / 4;  // the first team faces the default view
      const group = new THREE.Group(); scene3.add(group);
      const col = C(t.color);
      // A rug in the team colour marks the suite.
      const half = Math.PI / 8 - 0.035;
      const rugGeo = new THREE.RingGeometry(8.6, R - 1.4, 40, 1, -(angle + half), half * 2);
      const rug = new THREE.Mesh(rugGeo, new THREE.MeshStandardMaterial({ color: col.clone().lerp(C(0xffffff), 0.55), roughness: 0.95 }));
      rug.rotation.x = -Math.PI / 2; rug.position.y = 0.02; rug.receiveShadow = true; rug.userData.team = t.id;
      group.add(rug); hitTargets.push(rug);
      // Glass partition with a gold cap on the boundary, and an olive tree by the window.
      const edge = angle + Math.PI / 8, dir = new V3(Math.cos(edge), 0, Math.sin(edge));
      const len = R - 1.4 - 8.8, mid = dir.clone().multiplyScalar(8.8 + len / 2);
      const pane = new THREE.Mesh(new THREE.BoxGeometry(len, 1.5, 0.06), M.glass); pane.position.set(mid.x, 0.75, mid.z); pane.rotation.y = -edge;
      const cap = new THREE.Mesh(new THREE.BoxGeometry(len, 0.06, 0.1), M.gold); cap.position.set(mid.x, 1.52, mid.z); cap.rotation.y = -edge;
      group.add(pane, cap);
      const tree = new THREE.Group(), tp = dir.clone().multiplyScalar(R - 2.6); tree.position.set(tp.x, 0, tp.z);
      const pot = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.42, 0.9, 20), M.white); pot.position.y = 0.45; pot.castShadow = true;
      const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.1, 1.6), M.walnut); trunk.position.y = 1.5;
      tree.add(pot, trunk);
      for (let i = 0; i < 4; i++) { const f = new THREE.Mesh(new THREE.IcosahedronGeometry(0.55 + rand() * 0.25, 0), M.leaf); f.position.set((rand() - 0.5) * 0.8, 2.3 + rand() * 0.6, (rand() - 0.5) * 0.8); f.castShadow = true; tree.add(f); }
      group.add(tree);
      // A brass inlay from JARVIS to the suite; light runs along it while the suite works.
      const inlayLen = SUITE_R - 1.4 - 6.6, inlayMid = new V3(Math.cos(angle), 0, Math.sin(angle)).multiplyScalar(6.6 + inlayLen / 2);
      const inlay = new THREE.Mesh(new THREE.PlaneGeometry(inlayLen, 0.14), M.gold);
      inlay.rotation.set(-Math.PI / 2, 0, -angle); inlay.position.set(inlayMid.x, 0.012, inlayMid.z);
      const tex = flowTex.clone(); tex.wrapS = THREE.RepeatWrapping; tex.needsUpdate = true;
      const flowMat = new THREE.MeshBasicMaterial({ map: tex, color: STATUS.idle.color, transparent: true, opacity: 0, depthWrite: false, toneMapped: false });
      const flow = new THREE.Mesh(new THREE.PlaneGeometry(inlayLen, 0.34), flowMat);
      flow.rotation.copy(inlay.rotation); flow.position.set(inlayMid.x, 0.02, inlayMid.z);
      group.add(inlay, flow);
      const desks = t.agents.map((a, i) => { const d = desk(t, a.name, angle, offsets[t.agents.length]?.[i] ?? (i - 1) * 4); group.add(d.grp); return d; });
      group.traverse((o) => { o.userData.team = t.id; });
      suites.set(t.id, { team: t, angle, group, desks, flowMat, tex, anchor: new V3(Math.cos(angle) * SUITE_R, 4.4, Math.sin(angle) * SUITE_R), status: "idle", ghosts: [] });
    });
  }

  // ---------------------------------------------------------------- live hand-offs: a pulse from JARVIS to the suite
  const pulses = [];
  const pulseMat = new THREE.SpriteMaterial({ map: glowTex, color: 0xffffff, transparent: true, depthWrite: false });
  function dispatch(agentName) {
    const s = [...suites.values()].find((x) => x.desks.some((d) => d.name === agentName));
    const target = s ? s.anchor.clone().setY(2.2) : new V3(0, 6, 0);
    const sprite = new THREE.Sprite(pulseMat.clone()); sprite.scale.setScalar(2.2); scene3.add(sprite);
    const ripple = new THREE.Mesh(new THREE.RingGeometry(0.6, 0.85, 48), new THREE.MeshBasicMaterial({ color: s ? s.team.color : 0x5cc8ff, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide }));
    ripple.rotation.x = -Math.PI / 2; ripple.position.set(target.x, 0.05, target.z); scene3.add(ripple);
    pulses.push({ sprite, ripple, from: core.position.clone(), to: target, t: 0 });
    coreKick = 1;
  }

  // ---------------------------------------------------------------- data in
  let built = false, coreKick = 0, jarvisBusy = 0;
  function update(m) {
    if (!built) { buildSuites(m.teams); built = true; }
    for (const t of m.teams) {
      const s = suites.get(t.id);
      if (!s) continue;
      s.team = t; s.status = t.status;
      t.agents.forEach((a) => {
        const d = s.desks.find((x) => x.name === a.name);
        if (!d) return;
        const key = JSON.stringify([a.status, a.now, a.next, a.tool, a.open.length]);
        if (key !== d.key) { d.key = key; drawScreen(d.sc, t, a); }
        d.status = a.status;
        const c = C(STATUS[a.status].color);
        d.bulbMat.emissive.copy(c); d.glow.material.color.copy(c);
        d.runs = a.runs.length;
      });
      // Extra sub-agents JARVIS has running in parallel for this team appear as translucent helpers.
      const extra = Math.max(0, t.agents.reduce((n, a) => n + Math.max(0, a.runs.length - 1), 0));
      while (s.ghosts.length < Math.min(extra, 4)) {
        const g = avatar(t.color); g.suit.transparent = true; g.suit.opacity = 0.55;
        const i = s.ghosts.length, ang = s.angle + (i % 2 ? 1 : -1) * (0.17 + 0.06 * Math.floor(i / 2));
        g.grp.position.set(Math.cos(ang) * (SUITE_R + 3.2), 0, Math.sin(ang) * (SUITE_R + 3.2));
        g.grp.rotation.y = -Math.PI / 2 - ang; g.grp.scale.setScalar(0.01);
        s.group.add(g.grp); s.ghosts.push({ ...g, grow: 0, gone: false });
      }
      s.ghosts.forEach((g, i) => { g.gone = i >= extra; });
    }
    jarvisBusy = m.jarvis.status === "idle" ? 0 : m.jarvis.status === "delegating" ? 1 : 0.6;
  }

  // ---------------------------------------------------------------- camera: home view and flying to a suite
  const sph = new THREE.Spherical();
  let flight = null, lastInput = performance.now();
  const fromCam = () => { const off = camera.position.clone().sub(controls.target); sph.setFromVector3(off); return { az: sph.theta, pol: sph.phi, r: sph.radius, target: controls.target.clone() }; };
  function flyTo(goal, dur = 1.3) {
    const from = fromCam();
    let daz = goal.az - from.az; daz = Math.atan2(Math.sin(daz), Math.cos(daz));
    // Timed by the clock, not by frames, so a slow phone still lands on time.
    flight = { from, goal: { ...goal, az: from.az + daz }, start: performance.now(), dur: REDUCED ? 0.001 : dur };
  }
  function place(v) {
    sph.set(v.r, v.pol, v.az); camera.position.copy(v.target).add(new V3().setFromSpherical(sph)); controls.target.copy(v.target); camera.lookAt(v.target);
  }
  function focus(id) {
    lastInput = performance.now();
    if (!id) return flyTo(HOME);
    if (id === "jarvis") return flyTo({ az: fromCam().az, pol: 1.05, r: 30, target: new V3(0, 3.2, 0) });
    const s = suites.get(id);
    if (!s) return;
    // Over the shoulders of the team: outside the suite, looking in at their screens and JARVIS beyond.
    const az = Math.atan2(Math.cos(s.angle), Math.sin(s.angle));
    flyTo({ az, pol: phone ? 1.0 : 1.1, r: phone ? 36 : 34, target: s.anchor.clone().multiplyScalar(phone ? 0.85 : 0.62).setY(1.4) });
  }
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  controls.addEventListener("start", () => { flight = null; lastInput = performance.now(); canvas.classList.remove("grab"); });
  controls.addEventListener("end", () => { lastInput = performance.now(); canvas.classList.add("grab"); });

  // Leave room for the panel: shift the picture so the subject sits in the visible part.
  let viewShift = { x: 0, y: 0 };
  function resize() {
    const w = stage.clientWidth, h = stage.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.fov = w < h ? 54 : 40;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(stage); resize();
  // Nothing to draw while the HQ is scrolled out of view (saves a phone's battery).
  const onScreen = () => { const r = stage.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; };  // a hidden tab already gets no frames

  // Clicking a suite in the 3D view (a tap, not the end of a drag).
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let down = null;
  canvas.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
  canvas.addEventListener("pointerup", (e) => {
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) return;
    const rect = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hitCore = ray.intersectObject(shell, false)[0];
    const hit = ray.intersectObjects([...suites.values()].map((s) => s.group), true)[0];
    if (hitCore && (!hit || hitCore.distance < hit.distance)) return opts.onPick("jarvis");
    if (hit?.object.userData.team) opts.onPick(hit.object.userData.team);
  });

  // ---------------------------------------------------------------- the frame loop
  const damp = (a, b, lambda, dt) => a + (b - a) * (1 - Math.exp(-lambda * dt));
  const clock = new THREE.Clock();
  const tmp = new V3();
  let coreSpin = 0.2, first = true;
  place({ ...HOME, az: HOME.az - 0.9, r: HOME.r + 30, pol: HOME.pol - 0.15 });
  flyTo(HOME, 2.6);

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05), time = clock.elapsedTime;
    if (!first && !onScreen()) return;
    if (flight) {
      flight.t = Math.min(1, (performance.now() - flight.start) / (flight.dur * 1000));
      const k = ease(flight.t), f = flight.from, g = flight.goal;
      place({ az: f.az + (g.az - f.az) * k, pol: f.pol + (g.pol - f.pol) * k, r: f.r + (g.r - f.r) * k, target: f.target.clone().lerp(g.target, k) });
      if (flight.t >= 1) flight = null;
    } else {
      // A slow drift around the floor once nobody has touched it for a while.
      controls.autoRotate = !REDUCED && !opts.selected() && performance.now() - lastInput > 25000;
      controls.autoRotateSpeed = 0.18;
      controls.update();
    }
    // Panel offset (desktop: panel on the right; phone: sheet at the bottom).
    const open = document.body.classList.contains("panel-open");
    const W = stage.clientWidth, H = stage.clientHeight;
    // On a desktop the panel covers the right of the HQ: shift the picture so the suite stays in view.
    const cover = open && !isPhone() ? Math.max(0, stage.getBoundingClientRect().right - (innerWidth - Math.min(460, innerWidth))) : 0;
    const wantX = cover / 2, wantY = 0;
    viewShift.x = damp(viewShift.x, wantX, 6, dt); viewShift.y = damp(viewShift.y, wantY, 6, dt);
    if (Math.abs(viewShift.x) > 0.5 || Math.abs(viewShift.y) > 0.5) camera.setViewOffset(W, H, viewShift.x, viewShift.y, W, H);
    else camera.clearViewOffset();

    // JARVIS breathes; rings speed up when it's thinking or handing out work.
    coreKick = damp(coreKick, 0, 2.5, dt);
    coreSpin = damp(coreSpin, 0.2 + jarvisBusy * 1.1 + coreKick * 2, 2, dt);
    rings.forEach((r, i) => { r.rotation.z += dt * coreSpin * (i % 2 ? -1 : 1) * (1 + i * 0.25); });
    const beat = REDUCED ? 0 : Math.sin(time * (1.2 + jarvisBusy * 1.6)) * 0.5 + 0.5;
    coreMat.emissiveIntensity = 1.9 + beat * 0.7 + coreKick * 1.5;
    halo.scale.setScalar(8.4 + beat * 1.2 + coreKick * 3);
    coreMesh.rotation.y += dt * 0.25;
    core.position.y = 4.1 + (REDUCED ? 0 : Math.sin(time * 0.8) * 0.12);
    coreLight.intensity = 26 + beat * 10;

    for (const s of suites.values()) {
      const working = s.status === "working";
      s.flowMat.color.set(STATUS[s.status].color);
      s.flowMat.opacity = damp(s.flowMat.opacity, working ? 0.85 : s.status === "idle" ? 0 : 0.35, 3, dt);
      if (!REDUCED) s.tex.offset.x -= dt * (working ? 0.55 : 0.15);
      for (const d of s.desks) {
        const p = d.pose, a = d.av, ph = d.phase + time;
        // Poses ease between states: typing at work, turned towards JARVIS while waiting, head down when blocked, leaning back when idle.
        p.type = damp(p.type, d.status === "working" ? 1 : 0, 4, dt);
        p.look = damp(p.look, d.status === "waiting" ? 1 : 0, 2.5, dt);
        p.lean = damp(p.lean, d.status === "idle" ? 1 : d.status === "blocked" ? -1 : 0, 2, dt);
        const breathe = REDUCED ? 0 : Math.sin(ph * 1.7) * 0.012;
        a.body.scale.set(1, 1 + breathe, 1);
        a.body.rotation.x = -p.lean * 0.12;
        const tap = REDUCED ? 0 : (Math.sin(ph * 11) * 0.5 + Math.sin(ph * 17.3) * 0.5) * 0.09 * p.type;
        a.arms[0].rotation.x = -0.9 - 0.45 * p.type + tap + p.lean * 0.6;
        a.arms[1].rotation.x = -0.9 - 0.45 * p.type - tap + p.lean * 0.6;
        a.head.rotation.x = 0.12 * p.type + (d.status === "blocked" ? 0.35 : 0) * (1 - p.look) - p.lean * 0.1 + (REDUCED ? 0 : Math.sin(ph * 0.6) * 0.03);
        // Waiting: glances up and across, as if watching for the hand-off; idle: a slow look around.
        a.head.rotation.y = REDUCED ? 0 : Math.sin(ph * 0.31) * (0.08 + 0.22 * Math.max(0, p.lean) + 0.3 * p.look);
        a.head.position.y = 2.1 - 0.05 * p.type;
        // Lamp: steady when waiting or idle, a soft slow pulse while working, a quicker one when blocked.
        const pulse = REDUCED ? 1 : d.status === "working" ? 0.75 + Math.sin(ph * 2.2) * 0.25 : d.status === "blocked" ? 0.7 + Math.sin(ph * 4) * 0.3 : 0.85;
        d.bulbMat.emissiveIntensity = 1.4 * pulse + (d.status === "idle" ? -0.5 : 0.6);
        d.glow.material.opacity = d.status === "idle" ? 0.25 : 0.75 * pulse;
      }
      for (let i = s.ghosts.length - 1; i >= 0; i--) {
        const g = s.ghosts[i];
        g.grow = damp(g.grow, g.gone ? 0 : 1, 3.5, dt);
        g.grp.scale.setScalar(Math.max(0.001, g.grow));
        if (g.gone && g.grow < 0.02) { s.group.remove(g.grp); s.ghosts.splice(i, 1); }
      }
    }

    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      p.t += dt / 1.5;
      const k = ease(Math.min(1, p.t));
      const mid = p.from.clone().lerp(p.to, 0.5).setY(9);
      tmp.copy(p.from).multiplyScalar((1 - k) * (1 - k)).addScaledVector(mid, 2 * (1 - k) * k).addScaledVector(p.to, k * k);
      p.sprite.position.copy(tmp);
      p.sprite.material.opacity = Math.min(1, (1 - p.t) * 4);
      const r = Math.max(0, p.t - 0.85) / 0.6;
      p.ripple.scale.setScalar(1 + r * 9); p.ripple.material.opacity = r > 0 ? Math.max(0, 0.8 - r) : 0;
      if (p.t > 1.45) { scene3.remove(p.sprite, p.ripple); pulses.splice(i, 1); }
    }

    // Scenery: ripples, tenders and the cars on the circuit.
    if (!REDUCED) {
      world.ripple.offset.x += dt * 0.004; world.ripple.offset.y += dt * 0.0025;
      for (const b of world.tenders) {
        b.a += dt * b.speed;
        b.grp.position.x = b.cx + Math.cos(b.a) * b.r; b.grp.position.z = b.cz + Math.sin(b.a) * b.r * 0.5;
        b.grp.rotation.y = -Math.atan2(Math.cos(b.a) * b.r * 0.5, -Math.sin(b.a) * b.r);
      }
      const cs = world.cars, m4 = new THREE.Matrix4(), q = new THREE.Quaternion();
      cs.list.forEach((c, i) => {
        const idx = Math.floor(c.u * cs.bend.length) % cs.bend.length;
        const want = c.top * (1 - Math.min(0.65, cs.bend[idx] * 1.6));
        c.v = damp(c.v, want, 1.2, dt);
        c.u = (c.u + (c.v * dt) / cs.len) % 1;
        const p = world.curve.getPointAt(c.u), t = world.curve.getTangentAt(c.u);
        q.setFromAxisAngle(new V3(0, 1, 0), Math.atan2(-t.z, t.x));
        m4.compose(p, q, new V3(1, 1, 1)); cs.mesh.setMatrixAt(i, m4);
      });
      cs.mesh.instanceMatrix.needsUpdate = true;
    }

    renderer.render(scene3, camera);

    // Labels follow their suites on screen.
    const w = W, h = H;
    for (const el of tags.children) {
      const id = el.dataset.id;
      const s = suites.get(id);
      const anchor = id === "jarvis" ? tmp.set(0, 7.2, 0) : s ? tmp.copy(s.anchor) : null;
      if (!anchor) { el.style.opacity = 0; continue; }
      const v = anchor.clone().project(camera);
      const dist = camera.position.distanceTo(anchor);
      // The open suite's own label would sit over its desks; the panel says it all.
      const visible = v.z < 1 && Math.abs(v.x) < 1.15 && Math.abs(v.y) < 1.15 && !(open && id === opts.selected());
      const x = (v.x * 0.5 + 0.5) * w, y = (-v.y * 0.5 + 0.5) * h;
      const sc = Math.max(0.78, Math.min(1, 46 / dist));
      el.style.transform = `translate(${Math.round(x - el.offsetWidth / 2)}px, ${Math.round(y - el.offsetHeight)}px) scale(${sc.toFixed(3)})`;
      el.style.opacity = visible ? "1" : "0";
      el.style.zIndex = String(1000 - Math.round(dist));
      el.style.pointerEvents = visible ? "auto" : "none";
    }
    if (first) { first = false; requestAnimationFrame(() => veil.classList.add("gone")); }
  }
  renderer.setAnimationLoop(frame);
  stage.hq = { camera, controls };  // for inspecting the view from the browser console
  return { update, dispatch, focus };
}

// The scenery objects the frame loop moves; filled in while the world is built.
const world = {};
