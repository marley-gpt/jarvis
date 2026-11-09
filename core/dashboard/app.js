// The main JARVIS page (T97), served at /. One screen from two earlier ones: the brain graph (brain.js, T95) is the
// living background, the speaking orb and the cards of what JARVIS is saying (stage.js, T86) sit in front of it,
// and the records a sentence names (T12, A3, D7, M-…, products, brain files) light up in the graph while the camera
// flies to them. This file only joins them up: the side panel, the focus area the graph flies into, and an effects
// layer (beams from the orb to what he is talking about, pulses on running work, sparks while he thinks).
(() => {
  "use strict";
  const J = window.J;
  const $ = (id) => document.getElementById(id);
  const app = $("app");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const phoneQ = matchMedia("(max-width: 820px)");
  const B = () => window.brain;
  const RELEASE_MS = 25000;   // after he stops talking, the graph lets go of what he showed

  // ------------------------------------------------------------ side panel ("what I'm doing")
  const SIDE_KEY = "jarvis.app.side";
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
                  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window */ } } };
  function sideOpen() { return phoneQ.matches ? app.classList.contains("side-open") : !app.classList.contains("side-off"); }
  function setSide(open, remember) {
    if (phoneQ.matches) app.classList.toggle("side-open", open);
    else { app.classList.toggle("side-off", !open); if (remember) store.set(SIDE_KEY, open ? "1" : "0"); }
    $("sideBtn").setAttribute("aria-pressed", String(open));
  }
  function initSide() {
    app.classList.remove("side-open", "side-off");
    if (phoneQ.matches) setSide(false);
    else { const s = store.get(SIDE_KEY); setSide(s ? s === "1" : innerWidth > 1180); }
  }
  initSide();
  phoneQ.addEventListener("change", initSide);
  $("sideBtn").onclick = () => setSide(!sideOpen(), true);
  $("sideClose").onclick = () => setSide(false, true);
  // A node tapped in the graph (or a record in a card) opens its details: make sure the panel is showing.
  window.addEventListener("brain:tab", (e) => { if (e.detail === "detail" && !sideOpen()) setSide(true); });
  $("demo2").onclick = () => { setSide(false); $("demo").click(); };
  $("fit2").onclick = () => { setSide(false); B()?.home(); };

  // ------------------------------------------------------------ ROAS (T103)
  // Today's and the last 7 days' ROAS from the ledger (Shopify sales over Meta spend, fed every 15 minutes by the
  // sales watch), against the breakeven line: green at or above it, red below. When the last pull is old or a
  // source failed, the tile says "No live data" and shows the ledger's figures dimmed, never anything made up.
  const ROAS_EVERY = 60000;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const aud = (n) => "A$" + Number(n || 0).toLocaleString("en-AU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const times = (v) => v == null ? "n/a" : v.toFixed(2) + "<small>×</small>";
  function roasCell(label, w) {
    const sub = w.spend_aud > 0 ? `${aud(w.revenue_aud)} sales / ${aud(w.spend_aud)} ads`
      : w.revenue_aud > 0 ? `${aud(w.revenue_aud)} sales, no ad spend` : "no ad spend";
    return `<div class="roas-cell" data-verdict="${w.verdict || ""}"><div class="k">${label}</div>` +
      `<div class="v">${times(w.roas)}</div><div class="m">${sub}</div></div>`;
  }
  function renderRoas(r) {
    const el = $("roas"), pill = $("roasPill");
    const at = r.pulled ? new Date(r.pulled).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    el.classList.toggle("stale", !r.live);
    pill.classList.toggle("stale", !r.live);
    pill.dataset.verdict = r.week.verdict || "";
    pill.querySelector("b").innerHTML = r.week.roas == null ? "n/a" : r.week.roas.toFixed(2) + "×";
    pill.title = `ROAS last 7 days: ${r.week.roas == null ? "no ad spend" : r.week.roas.toFixed(2) + "×"}` +
      (r.breakeven ? `, breakeven ${r.breakeven.toFixed(2)}×` : ", breakeven not set") + (r.live ? "" : ". No live data.");
    // The gauge runs from 0 to a little past the larger of breakeven and the figures shown.
    const vals = [r.breakeven, r.today.roas, r.week.roas].filter((v) => v != null);
    const top = Math.max(2, ...vals) * 1.25, pct = (v) => Math.min(100, (v / top) * 100).toFixed(1) + "%";
    const gauge = r.breakeven ? `<div class="roas-gauge" style="--be:${pct(r.breakeven)}" aria-hidden="true"><div class="track"></div>` +
      `<div class="be" style="left:${pct(r.breakeven)}" title="Breakeven ${r.breakeven.toFixed(2)}×"></div>` +
      (r.week.roas != null ? `<div class="dot week" style="left:${pct(r.week.roas)}" title="7 days"></div>` : "") +
      (r.today.roas != null ? `<div class="dot today" style="left:${pct(r.today.roas)}" title="Today"></div>` : "") + `</div>` : "";
    el.innerHTML =
      `<div class="roas-head">ROAS <span class="src">${r.live ? `<i></i>live · ${esc(at)}` : esc(at ? "last pull " + at : "")}</span></div>` +
      (r.live ? "" : `<div class="nolive"><b>NO LIVE DATA</b> · ${esc(r.why_not_live)} Figures below are the ledger as it stands.</div>`) +
      `<div class="roas-grid">${roasCell("Today", r.today)}${roasCell("Last 7 days", r.week)}</div>` + gauge +
      `<div class="be-line">${r.breakeven ? `Breakeven <b>${r.breakeven.toFixed(2)}×</b>${r.breakeven_note ? " · " + esc(r.breakeven_note) : ""}`
        : "Breakeven not set: <code>bin/jarvis sales breakeven &lt;roas&gt;</code>"}</div>`;
  }
  async function pollRoas() {
    if (document.hidden) return;
    try {
      const res = await fetch("/api/ops/roas", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      renderRoas(await res.json());
    } catch {
      $("roas").classList.add("stale"); $("roasPill").classList.add("stale");
      $("roasPill").querySelector("b").textContent = "?";
      $("roas").innerHTML = `<div class="roas-head">ROAS</div><div class="nolive"><b>NO LIVE DATA</b> · The dashboard can't reach the ledger right now.</div>`;
    }
  }
  pollRoas();
  setInterval(pollRoas, ROAS_EVERY);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollRoas(); });
  $("roasPill").onclick = () => { setSide(true); $("roas").scrollIntoView({ block: "nearest" }); };

  // ------------------------------------------------------------ where the graph flies what he names
  // The free part of the screen, in canvas pixels (the canvas fills the window): between the panels, under the
  // orb (which steps back to the top while records are shown) and above the talk bar or the card sheet.
  const box = (id) => $(id).getBoundingClientRect();
  window.BRAIN_FOCUS = () => {
    const W = innerWidth, talk = box("talk").top, head = document.querySelector("header").getBoundingClientRect().bottom;
    const cardsUp = $("stage").childElementCount > 0;
    const orbSmall = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--orb-small")) || 120;
    const top = head + orbSmall + (phoneQ.matches ? 70 : 96);
    if (phoneQ.matches) {
      const bottom = cardsUp ? Math.min(talk, box("stage").top) : talk;
      return { x: 0, y: top, w: W, h: Math.max(80, bottom - top - 8) };
    }
    const left = sideOpen() ? box("side").right : 0, right = cardsUp ? box("stage").left : W;
    return { x: left, y: top, w: right - left, h: talk - top - 8 };
  };

  // Where the whole brain sits at rest: round the orb on a desktop (between the panels), under it on a phone.
  window.BRAIN_HOME = () => {
    const W = innerWidth, talk = box("talk").top, head = document.querySelector("header").getBoundingClientRect().bottom;
    if (phoneQ.matches) {
      const top = box("orb").bottom + 30, bottom = $("stage").childElementCount ? Math.min(talk, box("stage").top) : talk;
      return { x: 0, y: top, w: W, h: bottom - top };
    }
    const p = box("orb"), cx = p.left + p.width / 2, cy = p.top + p.height / 2;
    const half = Math.min(cx - (sideOpen() ? box("side").right : 0), ($("stage").childElementCount ? box("stage").left : W) - cx);
    return { x: cx - half, y: head, w: half * 2, h: Math.max(80, 2 * Math.min(cy - head, talk - cy)) };
  };

  // ------------------------------------------------------------ light what he is talking about
  let releaseTimer = 0;
  function release() {
    clearTimeout(releaseTimer);
    if (!app.classList.contains("focus")) return;
    app.classList.remove("focus");
    B()?.light([], { home: false });
    // Home once the orb is back in the middle, since that is where the whole brain gathers round it.
    setTimeout(() => { if (!app.classList.contains("focus")) B()?.home(); }, 560);
  }
  function releaseLater() {
    clearTimeout(releaseTimer);
    releaseTimer = setTimeout(() => {
      if (J && (J.speaking || J.shown() !== "idle")) return releaseLater();
      release();
    }, RELEASE_MS);
  }
  window.addEventListener("jarvis:beat", (e) => {
    const brain = B();
    if (!brain) return;
    const ids = [...new Set(e.detail.refs || [])].filter((id) => brain.has(id));
    if (!ids.length) return;
    clearTimeout(releaseTimer);
    app.classList.add("focus");
    // Let the orb start stepping back first, so the flight aims at the area it leaves free.
    requestAnimationFrame(() => brain.light(ids));
  });
  window.addEventListener("jarvis:clear", release);
  if (J) J.on("status", ({ shown }) => { if (shown === "idle" && app.classList.contains("focus")) releaseLater(); });

  // A card that stands for a record opens it in the graph and the panel.
  $("stage").addEventListener("click", (e) => {
    if (e.target.closest("button, a")) return;
    const c = e.target.closest(".card[data-ref]");
    if (c && B()) B().select(c.dataset.ref);
  });
  const markLinked = () => $("stage").querySelectorAll(".card[data-ref]:not(.linked)").forEach((c) => {
    if (B()?.has(c.dataset.ref)) { c.classList.add("linked"); c.title = "Show it in the brain"; }
  });
  new MutationObserver(markLinked).observe($("stage"), { childList: true, subtree: true });

  // ------------------------------------------------------------ effects layer
  // Drawn on its own canvas so the graph itself only redraws when it moves. Cheap: a beam and a halo per lit
  // node, a ring per running job, and a few sparks along random links (more while JARVIS thinks or speaks).
  const fx = $("fx"), g = fx.getContext("2d");
  let W = 0, H = 0, dpr = 1;
  const sparks = [];
  let lastSpark = 0, drewLast = false;
  function fitFx() {
    dpr = Math.min(2, devicePixelRatio || 1); W = innerWidth; H = innerHeight;
    fx.width = Math.round(W * dpr); fx.height = Math.round(H * dpr);
  }
  addEventListener("resize", fitFx); fitFx();
  function orbCentre() {
    const r = box("orb");
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, r: r.width * 0.3 };
  }
  function frame(now) {
    requestAnimationFrame(frame);
    if (document.hidden) return;
    const brain = B();
    if (!brain) return;
    const t = now / 1000, lit = brain.lit(), live = brain.live();
    // Nothing to draw and nothing left from last frame: leave the canvas alone (a phone thanks us).
    const shown = J ? J.shown() : "idle";
    const every = shown === "thinking" || shown === "delegating" ? 70 : shown === "speaking" ? 150 : live.length ? 600 : 1500;
    const busy = lit.length || (live.length && !reduced) || sparks.length;
    if (!busy && !drewLast && (reduced || now - lastSpark < every)) return;
    drewLast = !!busy;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);

    // Beams from the orb to each record he is talking about, and a breathing halo on it.
    if (lit.length) {
      const o = orbCentre();
      for (const p of lit) {
        const dx = p.x - o.x, dy = p.y - o.y, d = Math.hypot(dx, dy) || 1;
        const sx = o.x + (dx / d) * o.r, sy = o.y + (dy / d) * o.r;
        const grad = g.createLinearGradient(sx, sy, p.x, p.y);
        grad.addColorStop(0, "rgba(169,219,255,.55)"); grad.addColorStop(1, "rgba(46,134,255,.12)");
        g.strokeStyle = grad; g.lineWidth = 1.4; g.setLineDash([6, 8]); g.lineDashOffset = reduced ? 0 : -t * 40;
        g.beginPath(); g.moveTo(sx, sy); g.lineTo(p.x, p.y); g.stroke();
        g.setLineDash([]);
        const r = 22 + (reduced ? 0 : 6 * Math.sin(t * 3));
        const halo = g.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
        halo.addColorStop(0, "rgba(232,241,255,.55)"); halo.addColorStop(0.35, "rgba(46,134,255,.28)"); halo.addColorStop(1, "rgba(46,134,255,0)");
        g.fillStyle = halo; g.beginPath(); g.arc(p.x, p.y, r, 0, Math.PI * 2); g.fill();
      }
    }
    // Work running now: a green ring that keeps going out.
    if (!reduced) {
      for (const p of live) {
        const k = (t * 0.6 + (p.x % 7) / 7) % 1;
        g.strokeStyle = `rgba(87,227,166,${0.6 * (1 - k)})`; g.lineWidth = 1.5;
        g.beginPath(); g.arc(p.x, p.y, 6 + k * 22, 0, Math.PI * 2); g.stroke();
      }
    }
    // Sparks: the brain at work. None while the camera is moving (they would slide off their links).
    if (brain.moving()) sparks.length = 0;
    else if (!reduced) {
      if (now - lastSpark > every && sparks.length < 40) {
        lastSpark = now;
        const l = brain.randomLink();
        if (l) sparks.push({ a: l[0], b: l[1], col: l[2], t0: now, dur: 500 + Math.random() * 700 });
      }
    }
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s = sparks[i], k = (now - s.t0) / s.dur;
      if (k >= 1) { sparks.splice(i, 1); continue; }
      const x = s.a.x + (s.b.x - s.a.x) * k, y = s.a.y + (s.b.y - s.a.y) * k;
      const k0 = Math.max(0, k - 0.18), x0 = s.a.x + (s.b.x - s.a.x) * k0, y0 = s.a.y + (s.b.y - s.a.y) * k0;
      g.globalAlpha = Math.sin(k * Math.PI);
      g.strokeStyle = s.col; g.lineWidth = 1.6;
      g.beginPath(); g.moveTo(x0, y0); g.lineTo(x, y); g.stroke();
      g.fillStyle = "#e8f1ff"; g.beginPath(); g.arc(x, y, 1.8, 0, Math.PI * 2); g.fill();
      g.globalAlpha = 1;
    }
  }
  requestAnimationFrame(frame);

  window.JApp = { release, focusRect: () => window.BRAIN_FOCUS() };
})();
