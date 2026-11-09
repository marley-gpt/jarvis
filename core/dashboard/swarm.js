// The Live view (T106) on the main page: every agent at work right now, as the owner asked to see them
// (M-261109-dea4, the multi-agent editor reel M-261109-dbbe). Each running worker and each agent the dashboard
// session has out gets a colour and a named cursor; the swarm feed lists what they really did (from the worker logs,
// GET /api/swarm), the tiles show the last thing each one made and how far through its plan it is, and the timeline
// fills in with one tick per action. No fake motion: a cursor blinks only while its agent acted in the last 20 s,
// and when nothing runs the view says so.
(() => {
  "use strict";
  const J = window.J;
  const $ = (id) => document.getElementById(id);
  const view = $("swarm"), btn = $("liveBtn");
  if (!view || !btn) return;
  const OPEN_EVERY = 2000, CLOSED_EVERY = 15000, FRESH_MS = 20000;
  const HUES = ["#5ec8ff", "#ff7aa8", "#9d8cff", "#62e6b1", "#ff9d5c", "#d6f25e", "#f37bff", "#6fa6ff", "#ffd36b", "#4fe0e0"];
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const hm = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const ago = (iso) => {
    if (!iso) return "";
    const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
    return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
  };

  // A colour per agent that stays put while the page is open: the first agent seen gets the first hue, and so on.
  const colours = new Map();
  const colour = (key) => {
    if (!colours.has(key)) colours.set(key, HUES[colours.size % HUES.length]);
    return colours.get(key);
  };

  let data = null, timer = 0, lastFetch = 0, feedSeen = 0;
  const isOpen = () => !view.hidden;

  function open(push) {
    view.hidden = false;
    btn.setAttribute("aria-pressed", "true");
    document.documentElement.classList.add("swarm-on");
    if (push && location.hash !== "#live") history.replaceState(null, "", "#live");
    poll();
    $("swarmClose").focus({ preventScroll: true });
  }
  function close() {
    view.hidden = true;
    btn.setAttribute("aria-pressed", "false");
    document.documentElement.classList.remove("swarm-on");
    if (location.hash === "#live") history.replaceState(null, "", location.pathname);
    schedule();
  }
  btn.onclick = () => (isOpen() ? close() : open(true));
  $("swarmClose").onclick = close;
  addEventListener("keydown", (e) => { if (e.key === "Escape" && isOpen() && !e.defaultPrevented) close(); });
  addEventListener("hashchange", () => { if (location.hash === "#live") open(false); });

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(poll, ms ?? (isOpen() ? OPEN_EVERY : CLOSED_EVERY));
  }
  async function poll() {
    clearTimeout(timer);
    if (document.hidden) return schedule();
    try {
      const res = await fetch("/api/swarm", { cache: "no-store" });
      if (!res.ok) throw new Error(res.status);
      data = await res.json();
      lastFetch = Date.now();
      render();
    } catch {
      $("swarmUpd").textContent = "Can't reach the dashboard. Retrying.";
      $("swarmUpd").classList.add("bad");
    }
    schedule();
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  // The dashboard session's own hand-offs arrive as live events: fetch straight away rather than wait.
  if (J) for (const t of ["agent_activity", "dispatch", "agent_done"]) J.on(t, () => schedule(150));

  // ------------------------------------------------------------ render
  function render() {
    const d = data;
    const n = d.live;
    btn.querySelector(".n").textContent = n;
    btn.classList.toggle("on", n > 0);
    btn.title = n ? `${n} agent${n === 1 ? "" : "s"} working now: open the Live view` : "No agents working: open the Live view";
    if (!isOpen()) return;
    $("swarmUpd").classList.remove("bad");
    $("swarmUpd").textContent = "Updated " + new Date(lastFetch).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    $("swarmCount").innerHTML = n ? `<b>${n}</b> agent${n === 1 ? "" : "s"} live` : "Nobody working";
    view.classList.toggle("idle", !n);
    renderTiles(d);
    renderFeed(d);
    renderLanes(d);
  }

  function progressHtml(a) {
    const p = a.progress;
    if (!p) return `<div class="sw-prog none">No plan shared yet</div>`;
    const pct = Math.round((p.done / p.total) * 100);
    const what = p.source === "plan" ? "of its plan" : `of ${esc(a.task)}'s subtasks`;
    return `<div class="sw-prog" role="progressbar" aria-valuemin="0" aria-valuemax="${p.total}" aria-valuenow="${p.done}">` +
      `<div class="bar"><i style="width:${pct}%"></i></div><span>${p.done} of ${p.total} ${what}</span></div>`;
  }

  function previewHtml(a, fresh) {
    const m = a.made;
    const cursor = `<span class="sw-cursor${fresh ? " fresh" : ""}" aria-hidden="true"><i></i><b>${esc(a.name)}</b></span>`;
    if (m && m.kind === "image") {
      return `<figure class="sw-prev img"><img src="${esc(m.url)}" alt="${esc(m.shown)}" loading="lazy">` +
        `<figcaption>${esc(m.shown)}</figcaption>${cursor}</figure>`;
    }
    if (m && m.kind === "text") {
      const lines = m.snippet.split("\n").slice(-9).join("\n");
      return `<figure class="sw-prev txt"><figcaption>${m.verb === "wrote" ? "Wrote" : "Edited"} ${esc(m.shown)}</figcaption>` +
        `<pre><code>${esc(lines)}${cursor}</code></pre></figure>`;
    }
    if (a.words) return `<figure class="sw-prev say"><blockquote>${esc(a.words.text)}${cursor}</blockquote></figure>`;
    return `<figure class="sw-prev empty"><p>Nothing made yet.</p></figure>`;
  }

  function renderTiles(d) {
    const box = $("swarmTiles");
    if (!d.agents.length) {
      box.innerHTML = `<div class="sw-idle"><p>No agents are working right now.</p>` +
        `<p>Ask JARVIS for a job, or start a worker from Control, and it will show up here as it starts.</p></div>`;
      return;
    }
    box.innerHTML = d.agents.map((a) => {
      const c = colour(a.key);
      const fresh = a.running && a.last && Date.now() - Date.parse(a.last) < FRESH_MS;
      const state = a.running ? (fresh ? "Working" : "Thinking") : a.finished ? (a.finished.ok ? "Finished" : "Stopped with an error") : "Stopped";
      return `<article class="sw-tile${a.running ? "" : " past"}" style="--c:${c}">` +
        `<div class="sw-head"><span class="sw-badge"><i></i>${esc(a.label)}</span><span class="sw-state">${state}</span></div>` +
        `<h3>${esc(a.title || a.task_title || "")}</h3>` +
        `<p class="sw-doing">${esc(a.doing || "Starting up")}</p>` +
        previewHtml(a, fresh) + progressHtml(a) +
        `<div class="sw-foot">${a.task ? `<span>${esc(a.task)}</span>` : ""}<span>${a.actions} action${a.actions === 1 ? "" : "s"}</span>` +
        `<span>${a.last ? "last " + ago(a.last) : ""}</span></div></article>`;
    }).join("");
  }

  function renderFeed(d) {
    const list = $("swarmFeed");
    const stick = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    if (!d.feed.length) {
      list.innerHTML = `<li class="none">Actions appear here as the agents take them.</li>`;
      return;
    }
    const newest = d.feed[d.feed.length - 1].t;
    list.innerHTML = d.feed.map((f) =>
      `<li style="--c:${colour(f.agent)}"${f.t > feedSeen && feedSeen ? ' class="new"' : ""}>` +
      `<time datetime="${esc(f.at)}">${hm(f.t)}</time><b>${esc(f.agent.startsWith("S:") ? f.who : f.agent)}</b>` +
      `<span>${f.who !== "general-purpose" && !f.agent.startsWith("S:") ? `<em>${esc(f.who)}</em> ` : ""}${esc(f.text)}</span></li>`).join("");
    feedSeen = newest;
    if (stick) list.scrollTop = list.scrollHeight;
  }

  // The timeline: one lane per agent over the last 30 minutes, one tick per action, the now line on the right.
  function renderLanes(d) {
    const box = $("swarmLanes"), end = d.now, start = end - d.window;
    const marks = [30, 20, 10].map((m) => `<span style="left:${((d.window - m * 60) / d.window) * 100}%">-${m} min</span>`).join("");
    if (!d.agents.length) { box.innerHTML = `<div class="sw-scale">${marks}<span class="now">now</span></div>`; return; }
    box.innerHTML = d.agents.map((a) => {
      const ticks = a.ticks.map((t) => `<i style="left:${(((t - start) / d.window) * 100).toFixed(2)}%"></i>`).join("");
      return `<div class="sw-lane" style="--c:${colour(a.key)}"><span class="who">${esc(a.key.startsWith("S:") ? a.name : a.key.split(":")[0] + (a.kind === "helper" ? " helper" : ""))}</span>` +
        `<div class="track">${ticks}</div></div>`;
    }).join("") + `<div class="sw-scale">${marks}<span class="now">now</span></div>`;
  }

  if (location.hash === "#live") open(false); else poll();
  window.JSwarm = { open, close, poll };
})();
