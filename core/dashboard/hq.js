// JARVIS HQ app: header, Now / Needs you / Done / To do board, teams, talk bar, panels, and the 3D HQ.
//
// Data: jarvis.js (window.J) owns the live connection, chat and voice; it hands us /api/state and every
// live event. Feed below adds /api/hq (teams, board, week, connections, previews) and turns it all into
// one model that every part of the page draws from. Nothing is made up: when something isn't known
// or isn't built yet, the page says so. See core/dashboard/README.md for where each part lives.

const J = window.J;
const TEAM_COLORS = {
  research: "#3a6ea5", creative: "#c2477a", store: "#2e8b6e", ads: "#cf6d2a",
  testing: "#7a5bc7", money: "#a8862b", learning: "#2b8fa3", operations: "#5a6b84",
};
const JARVIS_TEAM = { id: "jarvis", name: "JARVIS", color: "#a8832f" };
const STATUS = {
  working: { word: "Working", color: "#13a06f" },
  waiting: { word: "Waiting", color: "#d9960f" },
  blocked: { word: "Blocked", color: "#dc3540" },
  idle: { word: "Idle", color: "#9aa6b5" },
};
const RANK = { blocked: 3, working: 2, waiting: 1, idle: 0 };
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isPhone = () => innerWidth <= 860;

function ago(ts) {
  const t = Date.parse(ts || "");
  if (!t) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}
function localDay(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
const money = (n) => `A$${Math.round(n).toLocaleString("en-AU")}`;
// "Bash: bin/jarvis task list" -> "Bash"; "mcp__claude-in-chrome__navigate: ..." -> "Chrome: navigate"
function toolName(line) {
  const m = /^([\w.-]+):\s/.exec(line || "");
  if (!m) return null;
  const parts = m[1].split("__");
  if (parts[0] !== "mcp") return m[1];
  const server = { "claude-in-chrome": "Chrome", "computer-use": "Computer use", Shopify: "Shopify", claude_ai_Shopify: "Shopify" }[parts[1]] || parts[1];
  return `${server}: ${(parts[2] || "").replace(/_/g, " ")}`;
}
const modelName = (m) => (!m || m === "inherit" ? "JARVIS's model" : m[0].toUpperCase() + m.slice(1));

// ================================================================== data adapter

const Feed = {
  state: null, hq: null, model: null, jarvis: { status: "idle", request: null },
  runs: new Map(),   // live hand-offs seen on the stream: id -> { agent, description, at }
  lines: {},         // latest tool lines per agent from the stream
  subs: [],
  on(fn) { this.subs.push(fn); },
  emit(kind, data) { this.subs.forEach((fn) => { try { fn(kind, data); } catch (e) { console.error(e); } }); },

  async loadHQ() {
    // One request at a time; anything asked for meanwhile is folded into one more after it.
    if (this._loading) { this._again = true; return; }
    this._loading = true;
    try { const r = await fetch("/api/hq", { cache: "no-store" }); if (r.ok) this.hq = await r.json(); } catch (e) { /* offline: keep the last */ }
    this._loading = false;
    if (this._again) { this._again = false; return this.loadHQ(); }
    this.rebuild();
  },
  rebuild() {
    if (!this.state || !this.hq) return;
    this.model = this.build();
    this.emit("model", this.model);
  },

  teamOf(agentName) {
    const t = (this.hq?.teams || []).find((x) => x.agents.includes(agentName));
    return t ? { id: t.id, name: t.name, color: TEAM_COLORS[t.id] } : JARVIS_TEAM;
  },

  agent(name) {
    const s = this.state, h = this.hq;
    const a = (s.agents || []).find((x) => x.name === name) || { name, label: name, title: "", tasks: [], activity: [] };
    const meta = h.agents[name] || { connections: [], skills: [], model: "" };
    const ev = h.activity?.agents?.[name];
    const runs = [...this.runs.values()].filter((r) => r.agent === name);
    const open = h.board.open.filter((t) => t.agent === name);
    const line = (this.lines[name] || a.activity || []).slice(-1)[0];
    const out = { name, label: a.label, title: a.title, role: a.role, model: meta.model, connections: meta.connections, skills: meta.skills,
      tools: meta.tools, open, runs, recent: h.recent[name] || [], status: "idle", now: null, tool: null, file: null, since: null, via: null, next: null };

    if (ev && ev.state !== "idle") {
      Object.assign(out, { status: ev.state === "started" ? "working" : ev.state, now: ev.action, tool: ev.tool, file: ev.file, since: ev.ts, via: "activity log" });
    } else if (runs.length) {
      Object.assign(out, { status: "working", now: runs[0].description, since: runs[0].at, via: "JARVIS, live" });
    } else if (a.status === "working") {
      // An agent run can be going with no task attached to it.
      Object.assign(out, { status: "working", now: a.now || "Working, no task logged for it", via: a.now ? "task list" : "agent runs" });
    }
    if (out.status === "idle") {
      const blocked = open.find((t) => t.status === "blocked");
      const asking = open.find((t) => t.status === "waiting_approval");
      if (blocked) Object.assign(out, { status: "blocked", now: blocked.title, via: "task list" });
      else if (asking) Object.assign(out, { status: "waiting", now: `Waiting for your approval: ${asking.title}`, via: "task list" });
      else out.next = (open.find((t) => t.status === "todo") || {}).title || null;
    }
    if (!out.tool && line && out.status === "working") out.tool = toolName(line);
    if (!out.since && out.status === "working") out.since = (open.find((t) => t.status === "in_progress") || {}).updated || null;
    const doing = open.find((t) => t.status === "in_progress" && t.deliverable);
    const made = out.recent.find((t) => t.deliverable);
    out.preview = out.file ? { path: out.file, why: "File it is working on" }
      : doing ? { path: doing.deliverable, why: `Deliverable for T${doing.id}, in progress` }
      : made ? { path: made.deliverable, why: `Latest deliverable, T${made.id}` } : null;
    return out;
  },

  build() {
    const s = this.state, h = this.hq;
    const teams = h.teams.map((t) => {
      const agents = t.agents.map((n) => this.agent(n));
      const status = agents.reduce((best, a) => (RANK[a.status] > RANK[best] ? a.status : best), "idle");
      const lead = agents.find((a) => a.status === status) || agents[0];
      return { ...t, color: TEAM_COLORS[t.id] || "#5a6b84", agents, status, lead };
    });
    const all = teams.flatMap((t) => t.agents);
    const labelOf = (n) => (all.find((a) => a.name === n) || {}).label || (n ? n : "JARVIS");
    const tag = (t) => ({ ...t, team: this.teamOf(t.agent), who: labelOf(t.agent) });
    const open = h.board.open.map(tag);
    const today = localDay();

    // The board: what is being worked on, what needs the owner, what is done, what is still to do.
    const now = [
      ...[...this.runs.values()].map((r) => ({ id: null, title: r.description || "Working", agent: r.agent, live: true, updated: r.at, team: this.teamOf(r.agent), who: labelOf(r.agent) })),
      ...open.filter((t) => t.status === "in_progress"),
      ...all.filter((a) => a.status === "working" && a.via === "agent runs").map((a) => ({ id: null, title: `${a.label}: working, no task logged for it`, agent: a.name, team: this.teamOf(a.name), who: a.label })),
    ];
    const ask = {
      approvals: s.approvals || [],
      tasks: open.filter((t) => t.status === "waiting_approval" || t.status === "blocked"),
    };
    const doneAll = h.board.done.map(tag);
    const done = { today: doneAll.filter((t) => (t.completed || "").slice(0, 10) === today), earlier: doneAll.filter((t) => (t.completed || "").slice(0, 10) !== today) };
    const todo = open.filter((t) => t.status === "todo");
    return {
      teams, agents: all, source: h.activity ? "activity" : "tasks", allTasks: [...open, ...doneAll],
      board: { now, ask, done, todo },
      counts: {
        now: now.length, ask: ask.approvals.length + ask.tasks.length, doneToday: done.today.length, todo: todo.length,
        agents: all.length, working: all.filter((a) => a.status === "working").length, idle: all.filter((a) => a.status === "idle").length,
      },
      week: h.week, milestone: h.milestone, allowance: h.allowance, jarvis: this.jarvis,
    };
  },

  event(type, e) {
    switch (type) {
      case "status": this.jarvis.status = e.status; if (e.request) this.jarvis.request = e.request; break;
      case "dispatch": this.runs.set(e.id, { agent: e.agent, description: e.description, at: new Date().toISOString() });
        this.emit("dispatch", { agent: e.agent }); break;
      case "agent_activity": (this.lines[e.agent] ||= []).push(e.line); this.lines[e.agent] = this.lines[e.agent].slice(-6); break;
      case "agent_done": this.runs.delete(e.id); break;
      default: return;
    }
    this.rebuild();
  },
};

J.on("state", (s) => {
  Feed.state = s;
  Feed.jarvis.status = s.jarvis?.status || Feed.jarvis.status;
  Feed.jarvis.request = s.jarvis?.request || Feed.jarvis.request;
  Feed.loadHQ();
});
["status", "dispatch", "agent_activity", "agent_done"].forEach((t) => J.on(t, (e) => Feed.event(t, e)));
J.on("connection", ({ live }) => {
  $("conn").classList.toggle("live", live);
  $("conn").querySelector("span").textContent = live ? "Live" : "Reconnecting";
});
setInterval(() => J.refresh(), 60000);  // keeps "since" times and quiet agents up to date

// ================================================================== header

let boardTab = "now";
function renderHeader(m) {
  const c = m.counts;
  const items = [["now", "Now", c.now], ["ask", "Needs you", c.ask, c.ask > 0], ["done", "Done today", c.doneToday], ["todo", "To do", c.todo], ["agents", "Agents", c.agents]];
  $("stats").innerHTML = items.map(([k, label, v, alert]) =>
    `<button class="stat${alert ? " alert" : ""}${k === "agents" ? " agents" : ""}" data-jump="${k}"><dt>${label}</dt><dd>${v}</dd></button>`).join("");

  const ms = m.milestone;
  const el = $("milestone");
  if (ms && ms.title) {
    const when = ms.when ? relDay(ms.when) : "";
    el.className = "milestone"; el.innerHTML = `<b>${esc(ms.title)}</b>${when ? `: ${esc(when)}` : ""}`;
  } else {
    el.className = "milestone unset"; el.textContent = "No milestone set yet";
    el.title = "JARVIS sets the next milestone in brain/config.json (hq_milestone)";
  }

  const w = m.week;
  if (w) {
    const pct = Math.max(0, Math.min(100, (w.profit / w.target) * 100));
    const pace = Math.min(100, (w.pace / w.target) * 100);
    const state = w.profit >= w.pace ? "on pace" : "behind pace";
    $("target").innerHTML = `<div class="line"><span>Profit this week <b>${money(w.profit)}</b> of ${money(w.target)}</span><span>${state}</span></div>
      <div class="meter" role="img" aria-label="${money(w.profit)} of ${money(w.target)}, pace mark at ${money(w.pace)}"><i style="width:${pct}%"></i><s style="left:${pace}%"></s></div>`;
  }
}
function relDay(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const days = Math.round((new Date(localDay(d)) - new Date(localDay())) / 86400000);
  const time = iso.length > 10 ? d.toLocaleTimeString("en-AU", { hour: "numeric", minute: "2-digit" }) : "";
  if (days === 0) return d.getHours() >= 17 && time ? `tonight, ${time}` : time ? `today, ${time}` : "today";
  if (days === 1) return time ? `tomorrow, ${time}` : "tomorrow";
  if (days < 0) return `${-days} day${days === -1 ? "" : "s"} ago`;
  return d.toLocaleDateString("en-AU", { weekday: "long", day: "numeric", month: "short" });
}
document.addEventListener("click", (e) => {
  const j = e.target.closest("[data-jump]");
  if (!j) return;
  if (j.dataset.jump === "agents") return $("more").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
  setTab(j.dataset.jump);
  $("board").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
});

// ================================================================== the board

const COLS = [["now", "Now"], ["ask", "Needs you"], ["done", "Done"], ["todo", "To do"]];
function setTab(k) {
  boardTab = k;
  document.querySelectorAll(".col").forEach((c) => c.classList.toggle("show", c.dataset.col === k));
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === k));
}
$("tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) setTab(b.dataset.tab); });

function teamTag(t) { return `<span class="team-tag" style="--team:${t.color}">${esc(t.name)}</span>`; }
function taskItem(t) {
  const cls = t.status === "blocked" ? " blocked" : t.live ? " live" : "";
  const when = t.completed || t.updated;
  const meta = `${teamTag(t.team)}${t.who && t.who !== t.team.name ? `<span>${esc(t.who)}</span>` : ""}${t.id ? `<span>T${t.id}</span>` : ""}`
    + `${t.status === "blocked" ? "<span>blocked</span>" : t.status === "waiting_approval" ? "<span>waiting for your approval</span>" : ""}`
    + `${t.live ? "<span>handed out just now</span>" : ""}${when ? `<span>${esc(ago(when))}</span>` : ""}`;
  const target = t.id ? `data-task="${t.id}"` : t.agent ? `data-agent="${esc(t.agent)}"` : "";
  return `<li><button class="item${cls}" ${target}><div class="t">${esc(t.title)}</div><div class="m">${meta}</div></button></li>`;
}
function renderBoard(m) {
  const b = m.board, c = m.counts;
  const doneCount = b.done.today.length + b.done.earlier.length;
  const tabCount = { now: c.now, ask: c.ask, done: c.doneToday, todo: c.todo };
  $("tabs").innerHTML = COLS.map(([k, label]) => `<button role="tab" data-tab="${k}" aria-selected="${k === boardTab}"><b>${tabCount[k]}</b>${k === "done" ? "Done today" : label}</button>`).join("");
  const fill = (k, html, n) => {
    const col = document.querySelector(`.col[data-col="${k}"]`);
    col.querySelector(".n").textContent = n || "";
    col.querySelector("ol").innerHTML = html;
  };
  fill("now", b.now.length ? b.now.map(taskItem).join("") : `<p class="empty">Nobody is mid-task right now.</p>`, b.now.length);
  fill("ask", c.ask ? [
    ...b.ask.approvals.map((a) => `<li class="ask-card"><div class="t">${esc(a.summary)}</div>
      <div class="m"><span class="team-tag" style="--team:${JARVIS_TEAM.color}">${esc(a.kind)}</span>${a.amount != null ? `<span>${esc(a.amount)} ${esc(a.currency || "")}</span>` : ""}${a.task_id ? `<span>T${a.task_id}</span>` : ""}<span>${esc(ago(a.ts))}</span></div>
      ${a.detail ? `<div class="m">${esc(a.detail)}</div>` : ""}
      <div class="btns"><button class="btn yes" data-approve="${a.id}">Approve</button><button class="btn" data-reject="${a.id}">Reject</button></div></li>`),
    ...b.ask.tasks.map(taskItem),
  ].join("") : `<p class="empty">Nothing is waiting for you.</p>`, c.ask);
  const done = [];
  if (b.done.today.length) done.push(`<li class="sub-h">Today</li>`, ...b.done.today.map(taskItem));
  if (b.done.earlier.length) done.push(`<li class="sub-h">Last two weeks</li>`, ...b.done.earlier.slice(0, 20).map(taskItem));
  fill("done", done.join("") || `<p class="empty">Nothing finished in the last two weeks.</p>`, doneCount);
  fill("todo", b.todo.length ? b.todo.map(taskItem).join("") : `<p class="empty">The to-do list is empty.</p>`, b.todo.length);
  setTab(boardTab);
  $("board-note").textContent = m.source === "activity" ? "Live from the agent activity log" : "Live from the task list, agent runs and JARVIS's hand-offs";
}

// Approve and Reject: one tap each, as the owner asked. The board sits apart from the 3D view, so a drag can't hit them.
document.addEventListener("click", async (e) => {
  const yes = e.target.closest("[data-approve]"), no = e.target.closest("[data-reject]");
  const btn = yes || no;
  if (!btn) return;
  btn.disabled = true;
  try {
    await J.post("/api/approval", { id: Number(btn.dataset.approve || btn.dataset.reject), approve: !!yes });
    btn.textContent = yes ? "Approved" : "Rejected";
    J.refresh();
  } catch (err) { btn.disabled = false; btn.textContent = err.message; }
});

// ================================================================== teams and allowance

function renderTeams(m) {
  $("teams").innerHTML = m.teams.map((t) => {
    const lead = t.lead;
    const now = lead && lead.status !== "idle" ? lead.now : lead?.next ? `Next up: ${lead.next}` : "Nothing running";
    return `<button class="team-row" style="--team:${t.color}" data-team="${t.id}">
      <span class="nm">${esc(t.name)}<span class="lamp ${t.status}" title="${STATUS[t.status].word}"></span></span>
      <span class="who">${t.agents.map((a) => `${esc(a.label)} (${esc(modelName(a.model))})`).join(", ")}</span>
      <span class="now">${esc(now)}</span></button>`;
  }).join("");

  // Claude Max allowance: meant to be used nearly in full each week, never run out early.
  const a = m.allowance, c = m.counts;
  const idle = `${c.idle} of ${c.agents} agents are idle right now, with ${c.todo} task${c.todo === 1 ? "" : "s"} in To do that could fill that time.`;
  if (a && typeof a.used_pct === "number" && a.resets) {
    const end = Date.parse(a.resets), start = end - 7 * 86400000;
    const pace = Math.max(0, Math.min(100, ((Date.now() - start) / (end - start)) * 100));
    const diff = a.used_pct - pace;
    const word = diff > 5 ? "Ahead of pace: ease off a little so it lasts to the reset" : diff < -5 ? "Behind pace: room to give the agents more backlog work" : "On pace";
    $("allowance").innerHTML = `<h3>Claude Max this week</h3>
      <p><b>${Math.round(a.used_pct)}%</b> used, pace mark at ${Math.round(pace)}%. ${esc(word)}.</p>
      <div class="meter"><i style="width:${Math.min(100, a.used_pct)}%"></i><s style="left:${pace}%"></s></div>
      <p class="label-quiet">Resets ${esc(relDay(a.resets))}${a.source ? `. Source: ${esc(a.source)}` : ""}</p><p>${esc(idle)}</p>`;
  } else {
    $("allowance").innerHTML = `<h3>Claude Max this week</h3><span class="placeholder">Not connected yet</span>
      <p>Claude doesn't report Max usage to JARVIS yet. Once it does, this shows the week's usage against a pace line, so the allowance is used nearly in full without running out before the reset.</p>
      <p>${esc(idle)}</p>`;
  }
}

// ================================================================== panels: team, agent, task, JARVIS

let selected = null;   // "team:<id>" | "agent:<name>" | "task:<id>" | "jarvis" | null
let scene = null;

const previewCache = new Map();
function lightMarkdown(text) {
  const lines = text.split(/\r?\n/).slice(0, 80);
  let html = "", inList = false;
  const inline = (s) => esc(s).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^---\s*$/.test(line)) continue;
    const li = /^\s*[-*]\s+(.*)/.exec(line);
    if (li) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(li[1])}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    const h = /^#{1,6}\s+(.*)/.exec(line);
    if (h) html += `<h4>${inline(h[1])}</h4>`;
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (inList ? "</ul>" : "");
}
async function fillPreview(box, ref) {
  if (!box) return;
  if (!ref) { box.className = "preview none"; box.textContent = "Nothing built yet. A preview appears here as soon as a deliverable is saved."; return; }
  const url = `/api/hq/file?path=${encodeURIComponent(ref.path)}`;
  const name = ref.path.split(/[\\/]/).pop();
  const bar = `<div class="pbar"><b>${esc(name)}</b><span>${esc(ref.why)}</span></div>`;
  box.className = "preview";
  if (/\.(png|jpe?g|webp|gif)$/i.test(name)) {
    box.innerHTML = `${bar}<img alt="${esc(name)}" src="${url}">`;
    box.querySelector("img").onerror = () => { box.querySelector("img").outerHTML = `<div class="doc"><p class="empty">This image can't be shown here (it may have moved): ${esc(ref.path)}</p></div>`; };
    return;
  }
  box.innerHTML = `${bar}<div class="doc"><p class="empty">Opening…</p></div>`;
  let text = previewCache.get(ref.path);
  if (text === undefined) {
    try { const r = await fetch(url); text = r.status === 200 ? await r.text() : null; } catch (e) { text = null; }
    previewCache.set(ref.path, text);
    setTimeout(() => previewCache.delete(ref.path), 30000);
  }
  const doc = box.querySelector(".doc");
  if (doc) doc.innerHTML = text == null ? `<p class="empty">This file can't be shown here (it may have moved): ${esc(ref.path)}</p>` : lightMarkdown(text);
}

const facts = (rows) => `<dl class="facts">${rows.filter(Boolean).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
function agentFacts(a) {
  return facts([
    ["Now", a.now ? esc(a.now) : a.next ? `Nothing running. Next up: ${esc(a.next)}` : "Nothing running"],
    a.status === "working" && ["Tool", a.tool ? esc(a.tool) : "Not reported yet"],
    a.since && ["Since", esc(ago(a.since))],
    ["Model", esc(modelName(a.model))],
    ["Open tasks", String(a.open.length)],
    a.via && ["Seen in", esc(a.via)],
  ]);
}
function agentCard(a, color) {
  const subs = a.runs.length > 1 || a.runs.some((r) => r.description !== a.now)
    ? `<div class="subs">${a.runs.map((r) => `<div>Sub-agent: ${esc(r.description || "working")}</div>`).join("")}</div>` : "";
  return `<button class="agent" style="--team:${color}" data-agent="${esc(a.name)}">
    <span class="nm">${esc(a.label)}<span class="lamp ${a.status}" title="${STATUS[a.status].word}"></span></span>
    <span class="ttl">${esc(a.title)}</span>${agentFacts(a)}${subs}</button>`;
}
const taskList = (rows, empty) => rows.length ? `<ol>${rows.map(taskItem).join("")}</ol>` : `<p class="empty">${empty}</p>`;

function panelTeam(m, t) {
  const counts = Object.entries(t.agents.reduce((acc, a) => ({ ...acc, [a.status]: (acc[a.status] || 0) + 1 }), {}))
    .sort((a, b) => RANK[b[0]] - RANK[a[0]]).map(([s, n]) => `${n} ${STATUS[s].word.toLowerCase()}`).join(", ");
  const names = new Set(t.agents.map((a) => a.name));
  const conns = [...new Set(t.agents.flatMap((a) => a.connections))];
  const skills = [...new Set(t.agents.flatMap((a) => a.skills))];
  const tasks = m.allTasks.filter((x) => names.has(x.agent));
  const ref = (t.agents.find((a) => a.preview && a.status !== "idle") || t.agents.find((a) => a.preview) || {}).preview || null;
  return { ref, html: `<div class="p-head" style="--team:${t.color}"><span class="swatch"></span><h2 id="p-title">${esc(t.name)}</h2>
    <p>${esc(t.job)}</p><div class="state-line"><span class="lamp ${t.status}"></span>${esc(counts)}</div></div>
    <div class="p-body" style="--team:${t.color}">
      <div class="btns"><button class="btn yes" data-ask="${esc(t.name)}, ">Talk to ${esc(t.name)}</button></div>
      <h3>What it's building</h3><div class="preview" id="p-preview"></div>
      <h3>Agents <span class="n">${t.agents.length}</span></h3>${t.agents.map((a) => agentCard(a, t.color)).join("")}
      <h3>Connected to</h3><div class="chips">${conns.map((c) => `<span class="chip plug">${esc(c)}</span>`).join("")}</div>
      <h3>Skills and playbooks</h3><div class="chips">${skills.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div>
      <h3>Open tasks <span class="n">${tasks.filter((x) => x.status !== "done").length || ""}</span></h3>${taskList(tasks.filter((x) => x.status !== "done"), "No open tasks.")}
      <h3>Finished in the last two weeks</h3>${taskList(tasks.filter((x) => x.status === "done"), "Nothing finished lately.")}
      <p class="note">Team grouping is the draft from the T54 plan, still to agree.</p>
    </div>` };
}
function panelAgent(m, a) {
  const team = m.teams.find((t) => t.agents.includes(a)) || JARVIS_TEAM;
  const tasks = m.allTasks.filter((x) => x.agent === a.name);
  const subs = a.runs.length ? `<h3>Sub-agents working now <span class="n">${a.runs.length}</span></h3><ol>${a.runs.map((r) => `<li class="item live"><div class="t">${esc(r.description || "Working")}</div><div class="m"><span>started ${esc(ago(r.at))}</span></div></li>`).join("")}</ol>` : "";
  return { ref: a.preview, html: `<div class="p-head" style="--team:${team.color}"><span class="swatch"></span><h2 id="p-title">${esc(a.label)}</h2>
    <p>${esc(a.title)}, ${esc(team.name)} team</p><div class="state-line"><span class="lamp ${a.status}"></span>${STATUS[a.status].word}</div></div>
    <div class="p-body" style="--team:${team.color}">
      <div class="btns"><button class="btn yes" data-ask="${esc(a.label)}, ">Talk to ${esc(a.label)}</button><button class="btn" data-team="${team.id}">Open ${esc(team.name)}</button></div>
      ${agentFacts(a)}
      <p class="note">Default model is set in .claude/agents/${esc(a.name)}.md. Choosing a different model for a single task isn't built yet.</p>
      ${subs}
      <h3>What it's building</h3><div class="preview" id="p-preview"></div>
      <h3>Connected to</h3><div class="chips">${a.connections.map((c) => `<span class="chip plug">${esc(c)}</span>`).join("")}</div>
      <h3>Skills and playbooks</h3><div class="chips">${a.skills.map((c) => `<span class="chip">${esc(c)}</span>`).join("")}</div>
      <h3>Open tasks</h3>${taskList(tasks.filter((x) => x.status !== "done"), "No open tasks.")}
      <h3>Finished lately</h3>${taskList(tasks.filter((x) => x.status === "done"), "Nothing finished in the last two weeks.")}
    </div>` };
}
function panelTask(m, t) {
  const subs = m.allTasks.filter((x) => x.parent_id === t.id);
  const parent = t.parent_id ? m.allTasks.find((x) => x.id === t.parent_id) : null;
  const word = { in_progress: "In progress", todo: "To do", blocked: "Blocked", waiting_approval: "Waiting for your approval", done: "Done" }[t.status] || t.status;
  return { ref: t.deliverable ? { path: t.deliverable, why: `T${t.id}` } : null, html: `<div class="p-head" style="--team:${t.team.color}"><span class="swatch"></span>
    <p>T${t.id}, ${esc(word)}</p><h2 id="p-title" style="font-size:24px">${esc(t.title)}</h2></div>
    <div class="p-body" style="--team:${t.team.color}">
      ${facts([
        ["Team", t.team.id === "jarvis" ? teamTag(t.team) : `<button data-team="${t.team.id}">${teamTag(t.team)}</button>`],
        t.agent && ["Agent", `<button data-agent="${esc(t.agent)}" style="text-decoration:underline">${esc(t.who)}</button>`],
        t.product && ["Product", esc(t.product)],
        parent && ["Part of", `<button data-task="${parent.id}" style="text-decoration:underline;text-align:left">T${parent.id} ${esc(parent.title)}</button>`],
        ["Updated", esc(ago(t.completed || t.updated))],
      ])}
      <div class="btns"><button class="btn yes" data-ask="About T${t.id}: ">Ask JARVIS about this</button></div>
      ${t.description ? `<h3>Brief</h3><p class="desc">${esc(t.description)}</p>` : ""}
      ${t.summary ? `<h3>Result</h3><p class="desc">${esc(t.summary)}</p>` : ""}
      <h3>Deliverable</h3><div class="preview" id="p-preview"></div>
      ${subs.length ? `<h3>Sub-tasks <span class="n">${subs.length}</span></h3>${taskList(subs, "")}` : ""}
    </div>` };
}
function panelJarvis(m) {
  const working = m.teams.filter((t) => t.status === "working").map((t) => t.name);
  const word = { idle: "Ready", thinking: "Thinking", delegating: "Handing out work" }[m.jarvis.status] || m.jarvis.status;
  return { ref: null, html: `<div class="p-head" style="--team:${JARVIS_TEAM.color}"><span class="swatch"></span><h2 id="p-title">JARVIS</h2>
    <p>The main brain. Every team reads from and writes to the same memory, and JARVIS hands out the work.</p>
    <div class="state-line"><span class="lamp ${m.jarvis.status === "idle" ? "" : "working"}"></span>${esc(word)}</div></div>
    <div class="p-body">
      <div class="btns"><button class="btn yes" data-ask="">Talk to JARVIS</button><a class="btn" href="/chat">Full chat</a><a class="btn" href="/office">Old office view</a></div>
      <h3>Last request</h3><p class="desc">${m.jarvis.request ? esc(m.jarvis.request) : "None since the dashboard started."}</p>
      <h3>Teams at work</h3><p class="desc">${working.length ? esc(working.join(", ")) : "No team is working right now."}</p>
      <h3>Memory</h3><p class="desc">${Feed.state.memory?.total ?? "?"} entries, ${Feed.state.memory?.needs_review ?? 0} need review.</p>
    </div>` };
}

function renderPanel(keepScroll) {
  const m = Feed.model;
  if (!m || !selected) return;
  const [kind, id] = selected.split(":");
  let p = null;
  if (kind === "team") { const t = m.teams.find((x) => x.id === id); if (t) p = panelTeam(m, t); }
  else if (kind === "agent") { const a = m.agents.find((x) => x.name === id); if (a) p = panelAgent(m, a); }
  else if (kind === "task") { const t = m.allTasks.find((x) => String(x.id) === id); if (t) p = panelTask(m, t); }
  else if (kind === "jarvis") p = panelJarvis(m);
  if (!p) return;
  const pane = $("panel"), y = pane.scrollTop, shown = $("p-preview")?.dataset.ref;
  $("p-content").innerHTML = p.html;
  if (keepScroll) pane.scrollTop = y;
  const pv = $("p-preview");
  if (pv) {
    const ref = keepScroll && shown ? { path: shown, why: p.ref?.path === shown ? p.ref.why : "Shown" } : p.ref;
    pv.dataset.ref = ref ? ref.path : "";
    fillPreview(pv, ref);
  }
}

function open(sel) {
  selected = sel;
  document.body.classList.toggle("panel-open", !!sel);
  const team = sel?.startsWith("team:") ? sel.slice(5) : sel === "jarvis" ? "jarvis" : null;
  document.querySelectorAll(".tag").forEach((el) => el.classList.toggle("on", el.dataset.id === team));
  if (sel) { renderPanel(false); $("panel").scrollTop = 0; $("close").focus({ preventScroll: true }); }
  if (team || !sel) scene?.focus(team);
}
$("close").addEventListener("click", () => open(null));
addEventListener("keydown", (e) => { if (e.key === "Escape" && selected) open(null); });
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-team], [data-agent], [data-task], [data-ask]");
  if (!el || el.closest(".tag")) return;
  if (el.dataset.ask !== undefined) return askTo(el.dataset.ask);
  if (el.dataset.task) return open(`task:${el.dataset.task}`);
  if (el.dataset.agent) return open(`agent:${el.dataset.agent}`);
  if (el.dataset.team) return open(`team:${el.dataset.team}`);
});

// ================================================================== labels over the 3D view

function renderTags(m) {
  const layer = $("labels");
  const items = [{ id: "jarvis", name: "JARVIS", status: m.jarvis.status === "idle" ? "idle" : "working", core: true,
    now: m.jarvis.status === "idle" ? "Ready" : (m.jarvis.status === "delegating" ? "Handing out work" : "Thinking") }, ...m.teams];
  for (const t of items) {
    let el = layer.querySelector(`[data-id="${t.id}"]`);
    if (!el) {
      el = document.createElement("button");
      el.className = "tag" + (t.core ? " core-tag" : "");
      el.dataset.id = t.id;
      el.addEventListener("click", () => {
        const sel = t.core ? "jarvis" : `team:${t.id}`;
        open(selected === sel ? null : sel);
      });
      layer.appendChild(el);
    }
    if (!t.core) el.style.setProperty("--team", t.color);
    const lead = t.lead;
    const now = t.core ? t.now : lead && lead.status !== "idle" ? lead.now : lead?.next ? `Next up: ${lead.next}` : "Nothing running";
    el.innerHTML = `<span class="row">${esc(t.name)}<span class="lamp ${t.status}"></span></span><span class="now">${esc(now)}</span>`;
    el.setAttribute("aria-label", `${t.name}: ${STATUS[t.status]?.word || ""}. ${now}`);
  }
}

// ================================================================== talk bar (chat and voice come from jarvis.js)

const input = $("say");
const PLACEHOLDER = isPhone() ? "Talk to JARVIS or a team" : input.placeholder;
input.placeholder = PLACEHOLDER;
function askTo(prefix) {
  input.value = prefix;
  input.focus();
  input.setSelectionRange(prefix.length, prefix.length);
}
$("talkform").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  J.send(text);
});
$("mic").addEventListener("click", () => (J.listening ? J.stop() : J.listen()));
$("stop").addEventListener("click", () => J.stop());

let captionTimer = null, said = "";
function caption(who, text, hold) {
  const c = $("caption");
  c.hidden = !text;
  c.innerHTML = text ? `<small>${esc(who)}</small>${esc(text)}` : "";
  clearTimeout(captionTimer);
  if (text && !hold) captionTimer = setTimeout(() => { c.hidden = true; }, 20000);
}
J.on("owner", (e) => { said = ""; caption("You", e.text || "(image)", true); });
J.on("say_chunk", (e) => { said = (said ? said + " " : "") + e.text; caption("JARVIS", said, true); });
J.on("say", (e) => { said = e.text; caption("JARVIS", said); });
J.on("error", (e) => caption("JARVIS", e.text));
J.on("interim", (e) => { input.placeholder = e.text || "Listening…"; });
J.on("status", ({ shown }) => {
  const words = { idle: "", thinking: "Thinking…", delegating: "Handing out work…", listening: "Listening…", speaking: "Speaking…" };
  $("jstate").textContent = words[shown] ?? "";
  $("mic").classList.toggle("on", shown === "listening");
  $("stop").hidden = !["thinking", "delegating", "speaking"].includes(shown);
  if (shown !== "listening") input.placeholder = PLACEHOLDER;
});

// ================================================================== putting it together

Feed.on((kind, data) => {
  if (kind === "model") {
    renderHeader(data); renderBoard(data); renderTeams(data); renderTags(data);
    if (selected) renderPanel(true);
    scene?.update(data);
  } else if (kind === "dispatch") scene?.dispatch(data.agent);
});

$("reset-view").addEventListener("click", () => open(null));
$("demo").addEventListener("click", () => {
  if (!scene?.demo) return;
  const on = $("demo").getAttribute("aria-pressed") !== "true";
  $("demo").setAttribute("aria-pressed", on);
  $("demo").textContent = on ? "Stop demo" : "Play demo";
  $("demo-note").hidden = !on;
  scene.demo(on);
});

import("/hq-scene.js").then(({ startScene }) => startScene({
  stage: $("stage"), canvas: $("scene"), labels: $("labels"), veil: $("veil"), status: STATUS,
  selected: () => (selected === "jarvis" ? "jarvis" : selected?.startsWith("team:") ? selected.slice(5) : null),
  onPick: (id) => open(id === "jarvis" ? "jarvis" : `team:${id}`),
  fail: (text) => { $("nogl").hidden = false; $("nogl").textContent = text; $("veil").classList.add("gone"); $("stage").querySelector(".stage-tools").hidden = true; return null; },
})).then((s) => {
  scene = s;
  if (scene && Feed.model) scene.update(Feed.model);
}).catch((e) => console.error(e));
