// The Brain page (T95): a force-directed graph of the brain, the JARVIS orb, and the live "what I am doing" panel.
// Read-only. Data: /api/brain/graph (nodes and edges, refetched when its fingerprint changes), /api/brain/now
// (polled), /api/brain/node?id= (one node's details). No libraries: a Barnes-Hut simulation on typed arrays and
// one canvas, redrawn only while the layout moves or the view changes, so 1,500 nodes stay smooth on a phone.
// The main page (/, app.js) embeds it as the living background (window.BRAIN_EMBED): there the big speaking orb is
// app.js's, the connection light is jarvis.js's, and window.brain.light(ids) lights nodes and flies to them.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const canvas = $("canvas"), ctx = canvas.getContext("2d"), wrap = $("graph");
  const POLL_MS = 4000;
  const EMBED = !!window.BRAIN_EMBED;
  const orbEl = EMBED ? null : $("orb"), orbLabel = EMBED ? null : $("orbLabel"), connEl = EMBED ? null : $("conn");

  // Colours per group (memory category or node kind), chosen to read on the dark blue.
  const COLOURS = {
    jarvis: "#a9dbff", agent: "#57e3a6", product: "#ffb23e", task: "#e8f1ff", decision: "#ff8fd0",
    approval: "#ffd27a", file: "#9fb4d6",
    mentor: "#ffd166", procedures: "#4cc9f0", "creative-inspiration": "#b388ff", preferences: "#ff6b7a",
    shopify: "#7ae582", "meta-ads": "#5aa9ff", "product-research": "#f4a261", testing: "#2ec4b6",
    business: "#e9c46a", "customer-research": "#f28482", "competitor-ads": "#c77dff", decisions: "#ff8fd0",
  };
  const FALLBACK = ["#8ecae6", "#cdb4db", "#ffafcc", "#bde0fe", "#a3c4bc", "#f6bd60"];
  const KIND_LABEL = { agent: "Agents", product: "Products", task: "Tasks", decision: "Decisions", approval: "Approvals", file: "Files" };
  const RADIUS = { jarvis: 0, category: 9, agent: 7, product: 8, task: 4, decision: 4, approval: 4, file: 3, memory: 3 };
  const CHARGE = { jarvis: -600, category: -260, agent: -160, product: -160, task: -26, decision: -26, approval: -26, file: -18, memory: -18 };
  const DIST = { in: 34, hub: 300, team: 220, product: 70, task: 60, decision: 60, agent: 70, parent: 40, ref: 80, link: 80, supersedes: 30, file: 30 };

  let G = null;               // {nodes, links, counts, sig}
  let N = 0, X, Y, VX, VY, fixed, adj = [];
  let alpha = 0, dirty = true, sizeW = 0, sizeH = 0, dpr = 1;
  const view = { x: 0, y: 0, k: 0.5 };
  const hidden = new Set();   // groups switched off in Filters
  let showOld = false;        // superseded and archived memory
  let selected = -1, hover = -1, focusSet = null, matchSet = null;
  let live = new Set();       // node indexes with work running now
  let lit = new Set(), litIds = [];   // nodes JARVIS is talking about (light()), shown whatever the filters say
  let tween = null;           // a camera flight: {from, to, t0, ms}
  let lastNow = null, failures = 0;

  const colour = (g) => COLOURS[g] || (COLOURS[g] = FALLBACK[Object.keys(COLOURS).length % FALLBACK.length]);
  const groupOf = (n) => (n.k === "memory" || n.k === "category" ? n.g : n.k);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const visible = (i) => {
    if (lit.has(i)) return true;
    const n = G.nodes[i];
    if (hidden.has(groupOf(n))) return false;
    return showOld || n.k !== "memory" || !n.s || n.s === "active" || n.s === "needs-review";
  };

  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`${url}: ${r.status}`);
    return r.json();
  }

  // ---------------------------------------------------------------- graph data
  async function loadGraph() {
    const data = await getJSON("/api/brain/graph");
    const old = new Map();
    if (G) G.nodes.forEach((n, i) => old.set(n.id, [X[i], Y[i], VX[i], VY[i]]));
    const selId = selected >= 0 ? G.nodes[selected].id : null;
    G = data; N = G.nodes.length;
    X = new Float32Array(N); Y = new Float32Array(N); VX = new Float32Array(N); VY = new Float32Array(N);
    fixed = new Uint8Array(N);
    adj = Array.from({ length: N }, () => []);
    for (const [a, b] of G.links) { adj[a].push(b); adj[b].push(a); }

    // Seed: hubs on a ring, everything else near the first hub it hangs off, kept positions for known nodes.
    const hubs = G.nodes.map((n, i) => (n.k === "category" || n.k === "agent" || n.k === "product" ? i : -1)).filter((i) => i >= 0);
    hubs.forEach((i, j) => {
      const a = (j / hubs.length) * Math.PI * 2, r = G.nodes[i].k === "category" ? 320 : 170;
      X[i] = Math.cos(a) * r; Y[i] = Math.sin(a) * r;
    });
    let reused = 0;
    G.nodes.forEach((n, i) => {
      const o = old.get(n.id);
      if (o) { [X[i], Y[i], VX[i], VY[i]] = o; reused++; return; }
      if (n.k === "jarvis") return;
      if (hubs.includes(i)) return;
      const h = adj[i].find((j) => G.nodes[j].k !== "memory" && G.nodes[j].k !== "jarvis") ?? adj[i][0];
      const bx = h !== undefined ? X[h] : 0, by = h !== undefined ? Y[h] : 0;
      const a = Math.random() * Math.PI * 2, r = 20 + Math.random() * 60;
      X[i] = bx + Math.cos(a) * r; Y[i] = by + Math.sin(a) * r;
    });
    G.nodes.forEach((n, i) => { if (n.k === "jarvis") { fixed[i] = 1; X[i] = 0; Y[i] = 0; } });
    alpha = reused > N * 0.8 ? 0.3 : 1;
    selected = selId ? G.nodes.findIndex((n) => n.id === selId) : -1;
    focusSet = selected >= 0 ? new Set([selected, ...adj[selected]]) : null;
    lit = new Set(litIds.map(indexOf).filter((i) => i >= 0));
    renderFilters();
    applySearch();
    if (lastNow) markLive(lastNow);
    $("empty").hidden = N > 1;
    if (N <= 1) $("empty").textContent = "The brain is empty: no memory, tasks or decisions yet.";
    $("stats").textContent = `${G.nodes.filter((n) => n.k !== "category" && n.k !== "jarvis").length.toLocaleString()} nodes · ${G.links.length.toLocaleString()} links`;
    if (!reused) fitSoon = true;
    kick();
    if (!reused && location.hash.length > 1) setTimeout(() => selectId(decodeURIComponent(location.hash.slice(1))), 1500);
  }
  window.addEventListener("hashchange", () => { if (location.hash.length > 1) selectId(decodeURIComponent(location.hash.slice(1))); });
  let fitSoon = false;
  let byId = null;   // id -> index, rebuilt lazily after each load
  function indexOf(id) {
    if (!G) return -1;
    if (!byId || byId.g !== G) { byId = new Map(G.nodes.map((n, i) => [n.id, i])); byId.g = G; }
    return byId.has(id) ? byId.get(id) : -1;
  }

  // ---------------------------------------------------------------- simulation
  // A quadtree over the nodes, stored in flat arrays and rebuilt each tick; each cell holds its total charge and
  // charge-weighted centre so far cells push as one body (Barnes-Hut, theta 0.9).
  let qx0, qy0, qsize, cellKids, cellBody, cellQ, cellX, cellY, cellCount;
  function buildTree() {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (let i = 0; i < N; i++) { if (X[i] < minX) minX = X[i]; if (X[i] > maxX) maxX = X[i]; if (Y[i] < minY) minY = Y[i]; if (Y[i] > maxY) maxY = Y[i]; }
    qsize = Math.max(maxX - minX, maxY - minY, 1) + 2; qx0 = minX - 1; qy0 = minY - 1;
    const cap = N * 12 + 64;
    if (!cellKids || cellBody.length < cap) {
      cellKids = new Int32Array(cap * 4); cellBody = new Int32Array(cap); cellQ = new Float32Array(cap);
      cellX = new Float32Array(cap); cellY = new Float32Array(cap);
    }
    cellCount = 1; cellKids.fill(-1, 0, 4); cellBody[0] = -1;
    for (let i = 0; i < N; i++) insert(i);
    accumulate(0);
  }
  function newCell() {
    const c = cellCount++;
    cellKids.fill(-1, c * 4, c * 4 + 4); cellBody[c] = -1; return c;
  }
  function insert(i) {
    let c = 0, x0 = qx0, y0 = qy0, s = qsize;
    for (let depth = 0; ; depth++) {
      const h = s / 2;
      const leaf = cellKids[c * 4] === -1 && cellKids[c * 4 + 1] === -1 && cellKids[c * 4 + 2] === -1 && cellKids[c * 4 + 3] === -1;
      if (leaf) {
        const j = cellBody[c];
        if (j === -1) { cellBody[c] = i; return; }
        // Two nodes on (almost) the same spot, or the cell store is full: leave this one out of the tree for a tick.
        if (depth > 30 || cellCount + 2 >= cellBody.length) { X[i] += Math.random() - 0.5; Y[i] += Math.random() - 0.5; return; }
        cellBody[c] = -1;
        const nc = newCell();
        cellKids[c * 4 + (Y[j] >= y0 + h ? 2 : 0) + (X[j] >= x0 + h ? 1 : 0)] = nc; cellBody[nc] = j;
      }
      const qx = X[i] >= x0 + h ? 1 : 0, qy = Y[i] >= y0 + h ? 1 : 0;
      let k = cellKids[c * 4 + qy * 2 + qx];
      if (k === -1) { k = newCell(); cellKids[c * 4 + qy * 2 + qx] = k; cellBody[k] = i; return; }
      c = k; x0 += qx * h; y0 += qy * h; s = h;
    }
  }
  function accumulate(c) {
    let q = 0, x = 0, y = 0;
    const b = cellBody[c];
    if (b >= 0) { const w = charge(b); q = w; x = X[b] * Math.abs(w); y = Y[b] * Math.abs(w); }
    for (let k = 0; k < 4; k++) {
      const kid = cellKids[c * 4 + k];
      if (kid < 0) continue;
      accumulate(kid);
      const w = Math.abs(cellQ[kid]); q += cellQ[kid]; x += cellX[kid] * w; y += cellY[kid] * w;
    }
    const aw = Math.abs(q) || 1;
    cellQ[c] = q; cellX[c] = x / aw; cellY[c] = y / aw;
  }
  const charge = (i) => CHARGE[G.nodes[i].k] ?? -20;

  const stack = new Int32Array(4096), sizes = new Float32Array(4096);
  function repel() {
    const theta2 = 0.81;
    for (let i = 0; i < N; i++) {
      if (fixed[i]) continue;
      let sp = 0; stack[sp] = 0; sizes[sp++] = qsize;
      let fx = 0, fy = 0;
      while (sp > 0) {
        const c = stack[--sp], s = sizes[sp];
        const dx = cellX[c] - X[i], dy = cellY[c] - Y[i];
        let d2 = dx * dx + dy * dy;
        const isLeaf = cellBody[c] >= 0;
        if (isLeaf && cellBody[c] === i) continue;
        if (isLeaf || (s * s) / (d2 || 1e-6) < theta2) {
          if (d2 < 1) d2 = 1;
          if (d2 > 1.6e7) continue;   // too far to matter
          const f = (cellQ[c] * alpha) / d2;
          fx += dx * f; fy += dy * f;
          continue;
        }
        for (let k = 0; k < 4; k++) {
          const kid = cellKids[c * 4 + k];
          if (kid >= 0 && sp < 4095) { stack[sp] = kid; sizes[sp++] = s / 2; }
        }
      }
      VX[i] += fx; VY[i] += fy;
    }
  }
  function springs() {
    for (const [a, b, kind] of G.links) {
      const da = adj[a].length, db = adj[b].length;
      const dx = X[b] + VX[b] - X[a] - VX[a] || 1e-3, dy = Y[b] + VY[b] - Y[a] - VY[a] || 1e-3;
      const l = Math.sqrt(dx * dx + dy * dy);
      const f = ((l - (DIST[kind] || 60)) / l) * alpha / Math.min(da, db);
      const bias = da / (da + db), fx = dx * f, fy = dy * f;
      if (!fixed[b]) { VX[b] -= fx * bias; VY[b] -= fy * bias; }
      if (!fixed[a]) { VX[a] += fx * (1 - bias); VY[a] += fy * (1 - bias); }
    }
  }
  function tick() {
    buildTree(); repel(); springs();
    for (let i = 0; i < N; i++) {
      if (fixed[i]) { VX[i] = VY[i] = 0; continue; }
      VX[i] -= X[i] * 0.004 * alpha; VY[i] -= Y[i] * 0.004 * alpha;   // weak pull to the centre
      VX[i] *= 0.6; VY[i] *= 0.6;
      X[i] += VX[i]; Y[i] += VY[i];
    }
    alpha += (0 - alpha) * 0.0228;
  }

  // ---------------------------------------------------------------- drawing
  function resize() {
    const r = wrap.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    sizeW = r.width; sizeH = r.height;
    canvas.width = Math.round(sizeW * dpr); canvas.height = Math.round(sizeH * dpr);
    dirty = true; kick();
  }
  const toScreen = (x, y) => [(x - view.x) * view.k + sizeW / 2, (y - view.y) * view.k + sizeH / 2];
  const toWorld = (sx, sy) => [(sx - sizeW / 2) / view.k + view.x, (sy - sizeH / 2) / view.k + view.y];

  function fit() {
    if (!N) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (let i = 0; i < N; i++) {
      if (!visible(i)) continue;
      minX = Math.min(minX, X[i]); maxX = Math.max(maxX, X[i]); minY = Math.min(minY, Y[i]); maxY = Math.max(maxY, Y[i]);
    }
    if (!isFinite(minX)) return;
    // The main page says where the whole brain should sit (round the orb on a desktop, under it on a phone).
    const h = EMBED && typeof window.BRAIN_HOME === "function" ? window.BRAIN_HOME() : null;
    const r = h && h.w > 40 && h.h > 40 ? h : { x: 0, y: 0, w: sizeW, h: sizeH };
    view.k = Math.max(0.05, Math.min(2, 0.9 * Math.min(r.w / (maxX - minX + 40), r.h / (maxY - minY + 40))));
    view.x = (minX + maxX) / 2 - (r.x + r.w / 2 - sizeW / 2) / view.k;
    view.y = (minY + maxY) / 2 - (r.y + r.h / 2 - sizeH / 2) / view.k;
    dirty = true;
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, sizeW, sizeH);
    if (!G) return;
    const k = view.k, ox = sizeW / 2 - view.x * k, oy = sizeH / 2 - view.y * k;
    ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * ox, dpr * oy);
    const [wx0, wy0] = toWorld(-20, -20), [wx1, wy1] = toWorld(sizeW + 20, sizeH + 20);
    const onScreen = (i) => X[i] >= wx0 && X[i] <= wx1 && Y[i] >= wy0 && Y[i] <= wy1;
    const vis = new Uint8Array(N);
    for (let i = 0; i < N; i++) vis[i] = visible(i) ? 1 : 0;
    const emph = focusSet || matchSet || (lit.size ? litNear() : null);

    // Edges: one path for all, a brighter one for the focused node's.
    ctx.lineWidth = 0.8 / k;
    ctx.strokeStyle = emph ? "rgba(140,170,220,.05)" : "rgba(140,170,220,.13)";
    ctx.beginPath();
    const hi = [];
    for (const l of G.links) {
      const [a, b] = l;
      if (!vis[a] || !vis[b] || (!onScreen(a) && !onScreen(b))) continue;
      if ((focusSet && (a === selected || b === selected)) || (!focusSet && (lit.has(a) || lit.has(b)))) { hi.push(l); continue; }
      ctx.moveTo(X[a], Y[a]); ctx.lineTo(X[b], Y[b]);
    }
    ctx.stroke();
    if (hi.length) {
      ctx.strokeStyle = "rgba(169,219,255,.7)"; ctx.lineWidth = 1.4 / k; ctx.beginPath();
      for (const [a, b] of hi) { ctx.moveTo(X[a], Y[a]); ctx.lineTo(X[b], Y[b]); }
      ctx.stroke();
    }

    // Nodes, batched by colour; dimmed ones first.
    const minR = 1.6 / k;   // stay visible when zoomed far out
    const batches = new Map();
    for (let i = 0; i < N; i++) {
      if (!vis[i] || !onScreen(i)) continue;
      const n = G.nodes[i];
      if (n.k === "jarvis") continue;
      const dim = (emph && !emph.has(i)) || (n.s && n.s !== "active" && n.k === "memory" && n.s !== "needs-review");
      const key = colour(groupOf(n)) + (dim ? "|d" : "");
      if (!batches.has(key)) batches.set(key, []);
      batches.get(key).push(i);
    }
    for (const pass of [true, false]) {
      for (const [key, list] of batches) {
        if (key.endsWith("|d") !== pass) continue;
        ctx.globalAlpha = pass ? 0.22 : 1;
        ctx.fillStyle = key.split("|")[0];
        ctx.beginPath();
        for (const i of list) {
          const r = Math.max(RADIUS[G.nodes[i].k] ?? 3, minR);
          ctx.moveTo(X[i] + r, Y[i]); ctx.arc(X[i], Y[i], r, 0, Math.PI * 2);
        }
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;

    // Rings: work running now (green), the selection and the hovered node (ice).
    const ring = (i, col, w, pad) => {
      if (i < 0 || !vis[i]) return;
      const r = Math.max(RADIUS[G.nodes[i].k] ?? 3, minR) + pad / k;
      ctx.strokeStyle = col; ctx.lineWidth = w / k; ctx.beginPath(); ctx.arc(X[i], Y[i], r, 0, Math.PI * 2); ctx.stroke();
    };
    for (const i of live) ring(i, "#57e3a6", 2, 3);
    for (const i of lit) { ring(i, "#e8f1ff", 2.5, 4); ring(i, "rgba(46,134,255,.6)", 6, 9); }
    ring(selected, "#e8f1ff", 2, 3);
    ring(hover, "rgba(169,219,255,.8)", 1.5, 2);

    // Labels in screen space: hubs always, the rest when zoomed in, the focus set and matches always.
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.textBaseline = "middle";
    // A label that would overlap one already drawn is skipped; the selection, hover and hubs go first.
    const placed = [];
    const label = (i, bold, force) => {
      const n = G.nodes[i];
      const [sx, sy] = toScreen(X[i], Y[i]);
      const r = Math.max(RADIUS[n.k] ?? 3, minR) * k;
      ctx.font = `${bold ? 600 : 400} ${bold ? 12 : 11}px Saira, "Segoe UI", sans-serif`;
      const text = n.l.length > 48 && !bold ? n.l.slice(0, 47) + "…" : n.l;
      const x = sx + r + 4, w = ctx.measureText(text).width, box = [x - 2, sy - 8, x + w + 2, sy + 8];
      if (!force && placed.some((p) => box[0] < p[2] && box[2] > p[0] && box[1] < p[3] && box[3] > p[1])) return false;
      placed.push(box);
      ctx.lineWidth = 3; ctx.strokeStyle = "rgba(2,8,20,.85)"; ctx.strokeText(text, x, sy);
      ctx.fillStyle = bold ? "#e8f1ff" : "rgba(232,241,255,.8)"; ctx.fillText(text, x, sy);
      return true;
    };
    if (hover >= 0 && vis[hover]) label(hover, true, true);
    if (selected >= 0 && vis[selected] && selected !== hover) label(selected, true, true);
    for (const i of lit) if (i !== hover && i !== selected) label(i, true, true);
    let budget = 160;
    for (const bigPass of [true, false]) {
      for (let i = 0; i < N && budget > 0; i++) {
        if (i === hover || i === selected || lit.has(i) || !vis[i] || !onScreen(i)) continue;
        const n = G.nodes[i];
        if (n.k === "jarvis") continue;
        const big = n.k === "category" || n.k === "agent" || n.k === "product";
        if (big !== bigPass) continue;
        if ((emph ? emph.has(i) : big || k > 1.8) && label(i, big)) budget--;
      }
    }

    placeOrb();
  }

  function placeOrb() {
    if (!orbEl) return;
    const j = G ? G.nodes.findIndex((n) => n.k === "jarvis") : -1;
    const [sx, sy] = j >= 0 ? toScreen(X[j], Y[j]) : [sizeW / 2, sizeH / 2];
    const s = Math.max(0.55, Math.min(1.6, view.k * 1.4));
    orbEl.style.transform = `translate(${sx}px, ${sy}px) scale(${s})`;
    orbLabel.style.transform = `translate(${sx}px, ${sy}px) translate(-50%, ${36 * s}px)`;
  }

  // ---------------------------------------------------------------- lighting what JARVIS talks about
  // light(ids): those nodes glow, their links brighten, everything else dims, and the camera flies to frame them
  // inside the free part of the screen (window.BRAIN_FOCUS() gives it on the main page). light([]) lets go.
  let nearCache = null;
  function litNear() {
    if (nearCache && nearCache.of === lit) return nearCache.set;
    const set = new Set();
    for (const i of lit) { set.add(i); for (const j of adj[i]) set.add(j); }
    nearCache = { of: lit, set };
    return set;
  }
  function flyTo(x, y, k, ms) {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) ms = 1;
    tween = { from: { ...view }, to: { x, y, k }, t0: performance.now(), ms };
    fitSoon = false; kick();
  }
  function focusRect() {
    const r = typeof window.BRAIN_FOCUS === "function" ? window.BRAIN_FOCUS() : null;
    return r && r.w > 40 && r.h > 40 ? r : { x: 0, y: 0, w: sizeW, h: sizeH };
  }
  function light(ids, opts = {}) {
    const found = (ids || []).map(indexOf).filter((i) => i >= 0);
    litIds = found.map((i) => G.nodes[i].id);
    lit = new Set(found);
    dirty = true; kick();
    if (!found.length) { if (opts.home !== false) home(); return []; }
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const i of found) { minX = Math.min(minX, X[i]); maxX = Math.max(maxX, X[i]); minY = Math.min(minY, Y[i]); maxY = Math.max(maxY, Y[i]); }
    const r = focusRect(), pad = 70;
    const k = Math.max(0.45, Math.min(2.2, Math.min((r.w - pad * 2) / (maxX - minX || 1), (r.h - pad * 2) / (maxY - minY || 1))));
    // The box's centre goes to the centre of the free area, not the middle of the canvas.
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    flyTo(cx - (r.x + r.w / 2 - sizeW / 2) / k, cy - (r.y + r.h / 2 - sizeH / 2) / k, k, 1100);
    return litIds.slice();
  }
  // Back to the whole brain, JARVIS in the middle.
  function home() {
    const keep = { ...view };
    fit();
    const to = { ...view };
    Object.assign(view, keep);
    flyTo(to.x, to.y, to.k, 1200);
  }

  let raf = 0;
  function kick() { if (!raf) raf = requestAnimationFrame(frame); }
  function frame() {
    raf = 0;
    if (tween) {
      const p = Math.min(1, (performance.now() - tween.t0) / tween.ms), e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      // Zoom on a log scale so the flight doesn't lurch when it starts far out.
      view.x = tween.from.x + (tween.to.x - tween.from.x) * e; view.y = tween.from.y + (tween.to.y - tween.from.y) * e;
      view.k = Math.exp(Math.log(tween.from.k) + (Math.log(tween.to.k) - Math.log(tween.from.k)) * e);
      if (p >= 1) tween = null;
      dirty = true;
    }
    if (G && alpha > 0.002) {
      const t0 = performance.now();
      let ticks = 0;
      do { tick(); ticks++; } while (alpha > 0.3 && ticks < 4 && performance.now() - t0 < 12);
      if (fitSoon && alpha < 0.5 && !tween) { fit(); if (alpha < 0.08) fitSoon = false; }
      dirty = true;
    }
    if (dirty) { dirty = false; draw(); }
    if ((G && alpha > 0.002) || tween) kick();
  }

  // ---------------------------------------------------------------- pointer: pan, zoom, drag, pick
  function pick(sx, sy) {
    if (!G) return -1;
    const [wx, wy] = toWorld(sx, sy);
    let best = -1, bestD = Infinity;
    const tol = 10 / view.k;
    for (let i = 0; i < N; i++) {
      if (!visible(i) || G.nodes[i].k === "jarvis") continue;
      const dx = X[i] - wx, dy = Y[i] - wy, d = dx * dx + dy * dy;
      const r = Math.max(RADIUS[G.nodes[i].k] ?? 3, 1.6 / view.k) + tol;
      if (d < r * r && d < bestD) { best = i; bestD = d; }
    }
    return best;
  }
  const pointers = new Map();
  let drag = null, pinch = null;
  const rel = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    const p = rel(e);
    pointers.set(e.pointerId, p);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, k: view.k, mid: toWorld((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) };
      if (drag && drag.node >= 0 && G.nodes[drag.node].k !== "jarvis") fixed[drag.node] = 0;
      drag = null; canvas.classList.remove("dragging"); return;
    }
    const i = pick(...p);
    drag = { start: p, last: p, node: i, moved: false };
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", (e) => {
    const p = rel(e);
    if (pointers.has(e.pointerId)) pointers.set(e.pointerId, p);
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      view.k = Math.max(0.04, Math.min(8, pinch.k * d / pinch.d));
      const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
      view.x = pinch.mid[0] - (mx - sizeW / 2) / view.k; view.y = pinch.mid[1] - (my - sizeH / 2) / view.k;
      fitSoon = false; tween = null; dirty = true; kick(); return;
    }
    if (drag) {
      if (Math.hypot(p[0] - drag.start[0], p[1] - drag.start[1]) > 4) drag.moved = true;
      if (drag.moved) {
        if (drag.node >= 0) {
          const [wx, wy] = toWorld(...p);
          X[drag.node] = wx; Y[drag.node] = wy; VX[drag.node] = VY[drag.node] = 0; fixed[drag.node] = 1;
          alpha = Math.max(alpha, 0.12);
        } else {
          view.x -= (p[0] - drag.last[0]) / view.k; view.y -= (p[1] - drag.last[1]) / view.k;
        }
        drag.last = p; fitSoon = false; tween = null; dirty = true; kick();
      }
      return;
    }
    if (e.pointerType === "mouse") {
      const i = pick(...p);
      if (i !== hover) { hover = i; canvas.classList.toggle("over", i >= 0); dirty = true; kick(); }
    }
  });
  const end = (e) => {
    pointers.delete(e.pointerId);
    if (pinch) { if (pointers.size < 2) pinch = null; drag = null; return; }
    if (!drag) return;
    canvas.classList.remove("dragging");
    if (drag.node >= 0 && G.nodes[drag.node].k !== "jarvis") fixed[drag.node] = 0;
    if (!drag.moved && e.type === "pointerup") select(drag.node, false);
    drag = null;
  };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", () => { if (hover >= 0) { hover = -1; dirty = true; kick(); } });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const [sx, sy] = rel(e), [wx, wy] = toWorld(sx, sy);
    view.k = Math.max(0.04, Math.min(8, view.k * Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0015))));
    view.x = wx - (sx - sizeW / 2) / view.k; view.y = wy - (sy - sizeH / 2) / view.k;
    fitSoon = false; tween = null; dirty = true; kick();
  }, { passive: false });

  function centreOn(i) {
    fitSoon = false;   // the owner chose where to look
    if (EMBED) {  // the main page frames it in the free area, as when JARVIS names it
      const r = focusRect(), k = Math.max(view.k, 1.1);
      flyTo(X[i] - (r.x + r.w / 2 - sizeW / 2) / k, Y[i] - (r.y + r.h / 2 - sizeH / 2) / k, k, 700);
      return;
    }
    view.x = X[i]; view.y = Y[i];
    if (view.k < 0.9) view.k = 1.1;
    tween = null; dirty = true; kick();
  }

  // ---------------------------------------------------------------- selection and details
  function select(i, centre) {
    selected = i;
    focusSet = i >= 0 ? new Set([i, ...adj[i]]) : null;
    dirty = true; kick();
    if (i < 0) return;
    if (centre) centreOn(i);
    showDetail(G.nodes[i].id);
  }
  function selectId(id) {
    if (!G) return;
    const i = G.nodes.findIndex((n) => n.id === id);
    if (i < 0) { showDetail(id); return; }
    const g = groupOf(G.nodes[i]);
    if (hidden.has(g)) { hidden.delete(g); renderFilters(); }
    if (!visible(i)) { showOld = true; renderFilters(); }
    select(i, true);
  }

  function tab(which) {
    const now = which === "now";
    $("tabNow").setAttribute("aria-selected", now); $("tabDetail").setAttribute("aria-selected", !now);
    $("paneNow").hidden = !now; $("paneDetail").hidden = now;
    window.dispatchEvent(new CustomEvent("brain:tab", { detail: which }));
  }
  $("tabNow").onclick = () => tab("now");
  $("tabDetail").onclick = () => tab("detail");

  // Record ids in text become links into the graph.
  const linkify = (text) => esc(text).replace(/\b(M-\d{6}-[0-9a-f]{4}|T\d{1,5}|D\d{1,5}|A\d{1,5})\b/g, (m) => `<button class="ref" data-id="${m}">${m}</button>`);
  const when = (ts) => (ts ? String(ts).replace("T", " ").slice(0, 16) : "");

  let detailToken = 0;
  async function showDetail(id) {
    const pane = $("paneDetail"), mine = ++detailToken;
    tab("detail");
    pane.innerHTML = `<p class="none" style="margin-top:16px">Loading ${esc(id)}…</p>`;
    let d;
    try { d = await getJSON(`/api/brain/node?id=${encodeURIComponent(id)}`); }
    catch { if (mine === detailToken) pane.innerHTML = `<p class="none" style="margin-top:16px">${esc(id)} isn't in the brain.</p>`; return; }
    if (mine !== detailToken) return;
    const i = G ? G.nodes.findIndex((n) => n.id === id) : -1;
    const col = i >= 0 ? colour(groupOf(G.nodes[i])) : "#7d93b5";
    const neighbours = i >= 0 ? adj[i].filter((j) => G.nodes[j].k !== "jarvis" && G.nodes[j].k !== "category") : [];
    const rows = (pairs) => `<dl>${pairs.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${linkify(v)}</dd>`).join("")}</dl>`;
    const taskList = (ts) => ts.length ? ts.map((t) => `<button class="row" data-id="T${t.id}"><span class="id">T${t.id}</span><span class="t">${esc(t.title)}${t.agent ? `<span class="sub">${esc(t.agent)}</span>` : ""}</span><span class="chip ${esc(t.status)}">${esc(t.status)}</span></button>`).join("") : `<p class="none">None.</p>`;
    let html = `<div class="kind"><span class="sw" style="width:9px;height:9px;border-radius:50%;background:${col}"></span>${esc(d.kind)} · ${esc(d.id)}</div><h1>${esc(d.title)}</h1>`;
    if (d.kind === "memory") {
      const m = d.meta;
      html += rows([["Category", m.category], ["Provenance", m.provenance], ["Status", m.status], ["Product", m.product],
        ["Tags", Array.isArray(m.tags) ? m.tags.join(", ") : m.tags], ["Source", m.source_ref || m.source],
        ["Supersedes", m.supersedes], ["Superseded by", m.superseded_by], ["Updated", when(m.updated)]]);
      html += `<div class="body">${linkify(d.body.trim())}</div>`;
    } else if (d.kind === "task") {
      const t = d.row;
      html += rows([["Status", t.status], ["Agent", t.agent], ["Product", t.product], ["Parent", t.parent_id ? `T${t.parent_id}` : ""],
        ["Deliverable", t.deliverable], ["Updated", when(t.updated)], ["Finished", when(t.completed)]]);
      if (t.description) html += `<div class="body">${linkify(t.description)}</div>`;
      if (t.summary) html += `<section><h2>Summary</h2><div class="body" style="border:0;padding:0;margin:0">${linkify(t.summary)}</div></section>`;
      if (d.subtasks.length) html += `<section><h2>Subtasks <span class="n">${d.subtasks.length}</span></h2>${taskList(d.subtasks)}</section>`;
      if (d.events.length) html += `<section><h2>History</h2>${d.events.map((e) => `<div class="row"><span class="id">${esc(when(e.ts).slice(5))}</span><span class="t">${esc(e.kind)}${e.note ? ` · ${linkify(e.note)}` : ""}</span></div>`).join("")}</section>`;
    } else if (d.kind === "decision") {
      const r = d.row;
      html += rows([["Decided", when(r.ts)], ["By", r.decided_by], ["Product", r.product], ["Task", r.task_id ? `T${r.task_id}` : ""]]);
      if (r.rationale) html += `<div class="body">${linkify(r.rationale)}</div>`;
    } else if (d.kind === "product") {
      html += rows([["Stage", d.row.stage], ["Memory entries", String(d.memory)], ["Updated", when(d.row.updated)]]);
      if (d.row.notes) html += `<div class="body">${linkify(d.row.notes)}</div>`;
      html += `<section><h2>Tasks <span class="n">${d.tasks.length}</span></h2>${taskList(d.tasks)}</section>`;
    } else if (d.kind === "agent") {
      html += rows([["Role", d.row.title], ["Model", d.row.model], ["Tasks done", String(d.done)]]);
      if (d.row.role) html += `<div class="body">${esc(d.row.role)}</div>`;
      html += `<section><h2>Open tasks <span class="n">${d.tasks.length}</span></h2>${taskList(d.tasks)}</section>`;
    } else if (d.kind === "approval") {
      const r = d.row;
      html += rows([["Kind", r.kind], ["Status", r.status], ["Amount", r.amount ? `${r.currency || ""} ${r.amount}` : ""],
        ["Task", r.task_id ? `T${r.task_id}` : ""], ["Asked", when(r.ts)], ["Decided", when(r.decided_ts)], ["Note", r.decision_note]]);
      if (r.detail) html += `<div class="body">${linkify(r.detail)}</div>`;
    } else if (d.kind === "file") {
      html += rows([["Path", d.path]]);
      html += `<p><a href="/api/hq/file?path=${encodeURIComponent(d.path)}" target="_blank" rel="noopener">Open the file</a></p>`;
      if (d.tasks.length) html += `<section><h2>Deliverable of</h2>${taskList(d.tasks)}</section>`;
      if (d.recent.length) html += `<section><h2>Source of</h2>${d.recent.map((r) => `<button class="row" data-id="${esc(r.id)}"><span class="t">${esc(r.title)}<span class="sub">${esc(r.id)}</span></span></button>`).join("")}</section>`;
    } else if (d.kind === "category") {
      html += rows([["Entries", String(d.count)]]);
      html += `<section><h2>Latest</h2>${d.recent.map((r) => `<button class="row" data-id="${esc(r.id)}"><span class="t">${esc(r.title)}<span class="sub">${esc(r.id)} · ${esc(when(r.updated))}</span></span></button>`).join("")}</section>`;
    }
    if (neighbours.length) {
      html += `<section><h2>Connected <span class="n">${neighbours.length}</span></h2><div class="links">${neighbours.slice(0, 60).map((j) => `<button class="ref" data-id="${esc(G.nodes[j].id)}" title="${esc(G.nodes[j].l)}">${esc(G.nodes[j].l)}</button>`).join("")}</div></section>`;
    }
    pane.innerHTML = html;
  }

  // ---------------------------------------------------------------- filters and search
  function renderFilters() {
    const box = $("filters");
    const counts = G ? G.counts : {};
    const cats = Object.keys(counts).filter((g) => !KIND_LABEL[g] && g !== "jarvis").sort((a, b) => counts[b] - counts[a]);
    const item = (g, name) => `<label><input type="checkbox" data-g="${esc(g)}" ${hidden.has(g) ? "" : "checked"}><span class="sw" style="background:${colour(g)}"></span>${esc(name)}<span class="n">${counts[g] || 0}</span></label>`;
    box.innerHTML = `<h3>Memory</h3>${cats.map((g) => item(g, g.replace(/-/g, " "))).join("")}<hr><h3>Records</h3>${Object.keys(KIND_LABEL).map((g) => item(g, KIND_LABEL[g])).join("")}<hr><label><input type="checkbox" id="oldBox" ${showOld ? "checked" : ""}>Show superseded memory</label>`;
  }
  $("filters").addEventListener("change", (e) => {
    const t = e.target;
    if (t.id === "oldBox") showOld = t.checked;
    else if (t.dataset.g) t.checked ? hidden.delete(t.dataset.g) : hidden.add(t.dataset.g);
    if (selected >= 0 && !visible(selected)) select(-1);
    dirty = true; kick();
  });
  $("filterBtn").onclick = () => {
    const f = $("filters"); f.hidden = !f.hidden; $("filterBtn").setAttribute("aria-pressed", !f.hidden);
  };
  $("fitBtn").onclick = () => { fit(); kick(); };
  if (orbEl) orbEl.onclick = () => { select(-1); tab("now"); };

  function applySearch() {
    const q = $("search").value.trim().toLowerCase();
    matchSet = null;
    if (G && q.length >= 2) {
      matchSet = new Set();
      G.nodes.forEach((n, i) => { if (visible(i) && (n.l.toLowerCase().includes(q) || n.id.toLowerCase() === q)) matchSet.add(i); });
    }
    dirty = true; kick();
  }
  $("search").addEventListener("input", applySearch);
  $("search").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && matchSet && matchSet.size) { select(matchSet.values().next().value, true); }
    if (e.key === "Escape") { $("search").value = ""; applySearch(); }
  });

  // Clicks on record links anywhere in the side panel.
  document.querySelector("aside").addEventListener("click", (e) => {
    const b = e.target.closest("[data-id]");
    if (b && b.dataset.id) selectId(b.dataset.id);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.activeElement !== $("search")) { select(-1); tab("now"); }
  });

  // ---------------------------------------------------------------- what I am doing
  function markLive(now) {
    live = new Set();
    if (!G) return;
    const ids = new Set();
    for (const w of now.workers) if (w.status === "running") { if (w.task) ids.add(w.task); if (w.agent) ids.add(`agent:${w.agent}`); }
    for (const a of now.agents) if (a.state !== "assigned") { ids.add(`agent:${a.agent}`); if (a.task) ids.add(a.task); }
    for (const t of now.tasks) if (t.status === "in_progress") ids.add(`T${t.id}`);
    G.nodes.forEach((n, i) => { if (ids.has(n.id)) live.add(i); });
    dirty = true; kick();
  }

  function renderNow(now) {
    const status = { idle: "standing by", thinking: "thinking", delegating: "handing out work" }[now.jarvis] || now.jarvis;
    const running = now.workers.filter((w) => w.status === "running");
    const parts = [];
    parts.push(`<div class="now-line"><b>JARVIS</b> is ${esc(status)}${now.request ? `: “${esc(now.request)}”` : ""}.
      ${running.length ? `${running.length} worker${running.length > 1 ? "s" : ""} running.` : "No workers running."}
      ${now.approvals.length ? `<span style="color:var(--signal)">${now.approvals.length} approval${now.approvals.length > 1 ? "s" : ""} waiting for you.</span>` : ""}</div>`);
    const workerRow = (w) => `<button class="row" data-id="${esc(w.task || "")}"><span class="id">${esc(w.id)}</span><span class="t">${esc(w.title || w.brief)}<span class="sub">${esc([w.task, w.agent, when(w.started)].filter(Boolean).join(" · "))}</span></span><span class="chip ${esc(w.status)}">${esc(w.status)}</span></button>`;
    parts.push(`<section><h2>Workers <span class="n">${running.length}</span></h2>${now.workers.length ? now.workers.slice(0, 6).map(workerRow).join("") : `<p class="none">No workers yet.</p>`}</section>`);
    parts.push(`<section><h2>Agents <span class="n">${now.agents.length}</span></h2>${now.agents.length ? now.agents.map((a) => `<button class="row" data-id="agent:${esc(a.agent)}"><span class="t">${esc(a.agent)}<span class="sub">${esc(a.action || "")}</span></span><span class="chip ${esc(a.state)}">${esc(a.state)}</span></button>`).join("") : `<p class="none">No agents active.</p>`}</section>`);
    if (now.approvals.length) parts.push(`<section><h2>Needs you <span class="n">${now.approvals.length}</span></h2>${now.approvals.map((a) => `<div class="row"><span class="id">A${a.id}</span><span class="t">${esc(a.summary)}</span><span class="chip pending">${esc(a.kind)}</span></div>`).join("")}</section>`);
    parts.push(`<section><h2>Open tasks <span class="n">${now.tasks.length}</span></h2>${now.tasks.length ? now.tasks.map((t) => `<button class="row" data-id="T${t.id}"><span class="id">T${t.id}</span><span class="t">${esc(t.title)}${t.agent ? `<span class="sub">${esc(t.agent)}</span>` : ""}</span><span class="chip ${esc(t.status)}">${esc(t.status.replace("_", " "))}</span></button>`).join("") : `<p class="none">Nothing open.</p>`}</section>`);
    parts.push(`<section class="journal"><h2>Recent conversation</h2>${now.journal.length ? now.journal.slice().reverse().map((j) => `<div class="row ${j.role === "user" || j.role === "owner" ? "owner" : "assistant"}"><span class="id">${esc(when(j.ts).slice(11))}</span><span class="t">${linkify(j.text)}</span></div>`).join("") : `<p class="none">Nothing said yet.</p>`}</section>`);
    const pane = $("paneNow"), top = pane.scrollTop;
    pane.innerHTML = parts.join("");
    pane.scrollTop = top;
    if (orbEl) {
      orbEl.classList.toggle("busy", now.busy);
      orbEl.setAttribute("aria-label", `JARVIS is ${status}${now.busy ? ", work running" : ""}`);
      orbLabel.textContent = now.busy ? "JARVIS · WORKING" : "JARVIS";
    }
    window.dispatchEvent(new CustomEvent("brain:now", { detail: now }));
  }

  async function poll() {
    if (document.hidden) return;
    try {
      const now = await getJSON("/api/brain/now");
      failures = 0;
      if (connEl) { connEl.classList.add("live"); connEl.lastElementChild.textContent = "Live"; }
      const same = lastNow && JSON.stringify({ ...lastNow, ts: 0 }) === JSON.stringify({ ...now, ts: 0 });
      lastNow = now;
      if (!same) renderNow(now);
      if (!G || now.sig !== G.sig) await loadGraph();
      markLive(now);
    } catch (err) {
      failures++;
      if (connEl) {
        connEl.classList.remove("live");
        connEl.lastElementChild.textContent = failures > 1 ? "Offline, retrying" : "Reconnecting";
      }
      if (!G) $("empty").textContent = "Can't reach the dashboard server. Is bin/jarvis dashboard running?";
    }
  }

  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  resize();
  poll();
  setInterval(poll, POLL_MS);
  // Screen positions for the main page's effects layer (app.js): the lit nodes, the nodes with work running, and a
  // random visible link to send a spark along.
  const onCanvas = (i) => { const [sx, sy] = toScreen(X[i], Y[i]); return { x: sx, y: sy, id: G.nodes[i].id }; };
  function randomLink() {
    if (!G || !G.links.length) return null;
    for (let tries = 0; tries < 8; tries++) {
      const [a, b] = G.links[(Math.random() * G.links.length) | 0];
      if (!visible(a) || !visible(b)) continue;
      const p = onCanvas(a), q = onCanvas(b);
      if ((p.x < 0 || p.x > sizeW || p.y < 0 || p.y > sizeH) && (q.x < 0 || q.x > sizeW || q.y < 0 || q.y > sizeH)) continue;
      return [p, q, colour(groupOf(G.nodes[b]))];
    }
    return null;
  }
  window.brain = {
    fit, select: selectId, light, home,
    lit: () => [...lit].map(onCanvas), live: () => [...live].map(onCanvas), randomLink,
    has: (id) => indexOf(id) >= 0,
    moving: () => !!tween || alpha > 0.002,
    stats: () => ({ nodes: N, links: G ? G.links.length : 0, alpha }),
  };
})();
