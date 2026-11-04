// JARVIS HQ app: the Monaco Estate (hq-scene.js) with glass overlays: top navigation, business overview,
// live activity, quick actions, agent performance, view controls, the talk bar, and panels for every team,
// agent, task, JARVIS, the task board, analytics and settings.
//
// Data: jarvis.js (window.J) owns the live connection, chat and voice (the same Claude Code route as /chat),
// and hands us /api/state and every live event. Feed adds /api/hq and turns it all into one model that every
// part of the page draws from. Nothing is made up: what isn't known or isn't built yet is said so on the page.
// See core/dashboard/README.md for where each part lives.

const J = window.J;
const TEAM_COLORS = {
  research: "#4a7fc0", creative: "#c2477a", store: "#2e9b78", ads: "#d77a35",
  testing: "#8a6bd4", money: "#c6a46a", learning: "#2b9fb3", operations: "#7286a3",
};
const TEAM_ICONS = { research: "i-search", creative: "i-brush", store: "i-bag", ads: "i-mega", testing: "i-flask", money: "i-coin", learning: "i-book", operations: "i-cog" };
const JARVIS_TEAM = { id: "jarvis", name: "JARVIS", color: "#c6a46a" };
const STATUS = {
  working: { word: "Working", color: "#3fcf8e" },
  waiting: { word: "Waiting", color: "#f0b43c" },
  blocked: { word: "Blocked", color: "#ff5a5f" },
  idle: { word: "Idle", color: "#a9b3c1" },
};
const RANK = { blocked: 3, working: 2, waiting: 1, idle: 0 };
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isPhone = () => innerWidth <= 860;
const icon = (id, cls = "i") => `<svg class="${cls}"><use href="#${id}"/></svg>`;

function ago(ts) {
  const t = Date.parse(ts || "");
  if (!t) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  const d = Math.round(s / 86400);
  return d === 1 ? "yesterday" : `${d} days ago`;
}
function localDay(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
const money = (n) => `${n < 0 ? "-" : ""}A$${Math.abs(Math.round(n)).toLocaleString("en-AU")}`;
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
// "claude.ai Shopify" -> "Shopify", "plugin:github:github" -> "GitHub"
const connectorName = (n) => { const x = n.replace(/^claude\.ai /i, "").replace(/^plugin:[^:]+:/i, ""); return x === "github" ? "GitHub" : x; };
const initials = (label) => String(label || "?").split(/[\s&-]+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");

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
      return { ...t, color: TEAM_COLORS[t.id] || "#7286a3", agents, status, lead, openCount: agents.reduce((n, a) => n + a.open.length, 0) };
    });
    const all = teams.flatMap((t) => t.agents);
    const labelOf = (n) => (all.find((a) => a.name === n) || {}).label || (n ? n : "JARVIS");
    const tag = (t) => ({ ...t, team: this.teamOf(t.agent), who: labelOf(t.agent) });
    const open = h.board.open.map(tag);
    const today = localDay();
    const now = [
      ...[...this.runs.values()].map((r) => ({ id: null, title: r.description || "Working", agent: r.agent, live: true, updated: r.at, team: this.teamOf(r.agent), who: labelOf(r.agent) })),
      ...open.filter((t) => t.status === "in_progress"),
      ...all.filter((a) => a.status === "working" && a.via === "agent runs").map((a) => ({ id: null, title: `${a.label}: working, no task logged for it`, agent: a.name, team: this.teamOf(a.name), who: a.label })),
    ];
    const ask = { approvals: s.approvals || [], tasks: open.filter((t) => t.status === "waiting_approval" || t.status === "blocked") };
    const doneAll = h.board.done.map(tag);
    const done = { today: doneAll.filter((t) => (t.completed || "").slice(0, 10) === today), earlier: doneAll.filter((t) => (t.completed || "").slice(0, 10) !== today) };
    const todo = open.filter((t) => t.status === "todo");
    const openCount = open.length;
    // One pavilion per agent in the estate (the old office had a room per agent), coloured by its team.
    const departments = teams.flatMap((t) => t.agents.map((a) => ({ id: a.name, name: a.label, color: t.color, teamId: t.id, job: a.title,
      agents: [a], status: a.status, lead: a, openCount: a.open.length })));
    return {
      teams, departments, agents: all, source: h.activity ? "activity" : "tasks", allTasks: [...open, ...doneAll], doneAll,
      board: { now, ask, done, todo },
      counts: {
        now: now.length, ask: ask.approvals.length + ask.tasks.length, doneToday: done.today.length, todo: todo.length, open: openCount,
        agents: all.length, working: all.filter((a) => a.status === "working").length, idle: all.filter((a) => a.status === "idle").length,
        onTrack: openCount ? Math.round((open.filter((t) => t.status === "in_progress" || t.status === "todo").length / openCount) * 100) : 100,
      },
      week: h.week, daily: h.daily || [], milestone: h.milestone, allowance: h.allowance, connectors: h.connectors, actions: h.actions || [], metrics: h.metrics,
      tests: s.tests || [], activity: s.activity || [], journal: s.journal || [], jarvis: this.jarvis,
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
  $("conn").querySelector("span").textContent = live ? "Online" : "Reconnecting";
});
setInterval(() => J.refresh().catch(() => {}), 60000);  // keeps times and quiet agents up to date; offline is shown by the badge

// ================================================================== top bar

function renderTop(m) {
  $("n-agents").textContent = m.counts.agents;
  $("n-tasks").textContent = m.counts.open;
  const ms = m.milestone, el = $("milestone");
  if (ms && ms.title) el.innerHTML = `${icon("i-flag")}<span><b>${esc(ms.title)}</b>${ms.when ? ` ${esc(relDay(ms.when))}` : ""}</span>`;
  else el.innerHTML = `${icon("i-flag")}<span>No milestone set</span>`;
  el.title = "The next milestone. JARVIS sets it in brain/config.json (hq_milestone). Opens the task board.";
}
$("milestone").addEventListener("click", () => open("tasks"));
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
// Monaco weather, live from Open-Meteo (free, no key). Hidden if it can't be fetched.
async function weather() {
  try {
    const r = await fetch("https://api.open-meteo.com/v1/forecast?latitude=43.7384&longitude=7.4246&current=temperature_2m&timezone=Europe%2FMonaco");
    const d = await r.json();
    const t = d?.current?.temperature_2m;
    if (typeof t !== "number") return;
    $("weather").innerHTML = `${icon("i-sun")}<span><b>${Math.round(t)}°C</b> Monaco</span>`;
    $("weather").hidden = false;
  } catch (e) { /* no weather is fine */ }
}
weather(); setInterval(weather, 30 * 60000);

// ================================================================== the corner cards

function renderOverview(m) {
  const w = m.week, daily = m.daily;
  if (!w) return;
  const pace = w.pace ? Math.round((w.profit / w.target) * 100) : 0;
  const pts = daily.map((d) => d.profit), hasData = daily.some((d) => d.income || d.expenses);
  const max = Math.max(1, ...pts.map(Math.abs)), W = 268, H = 46;
  const path = pts.map((v, i) => `${i ? "L" : "M"}${((i / Math.max(1, pts.length - 1)) * W).toFixed(1)},${(H / 2 - (v / max) * (H / 2 - 4)).toFixed(1)}`).join("");
  // Live store and ad figures when JARVIS has pulled a snapshot; otherwise the ledger and logged tests.
  const s = m.metrics, sh = s?.shopify || {}, me = s?.meta || {};
  const usd = (n) => `$${Math.round(n).toLocaleString("en-US")}`;
  const cells = s ? [
      ["Revenue", sh.revenue != null ? usd(sh.revenue) : "–"], ["Orders", sh.orders ?? "–"],
      ["ROAS", me.roas != null ? `${Number(me.roas).toFixed(1)}x` : me.spend && sh.revenue != null ? `${(sh.revenue / me.spend).toFixed(1)}x` : "–"],
      ["Ad spend", me.spend != null ? usd(me.spend) : "–"], ["CPA", me.cpa != null ? usd(me.cpa) : "–"], ["CVR", sh.cvr != null ? `${Number(sh.cvr).toFixed(1)}%` : "–"],
    ] : [["Sales", money(w.income)], ["Costs", money(w.expenses)], ["ROAS", "–"]];
  $("overview").innerHTML = `<h2 class="card-title">${icon("i-spark")}Business overview<span class="delta">${w.profit >= w.pace ? "On pace" : "Behind pace"}</span></h2>
    <div class="big">${money(w.profit)}<small>profit this week of ${money(w.target)}</small></div>
    <svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Daily profit, last 14 days">
      <line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="rgba(255,255,255,.12)"/>
      <path d="${path}" fill="none" stroke="${hasData ? "#3fcf8e" : "rgba(255,255,255,.35)"}" stroke-width="2"/></svg>
    <div class="metrics">${cells.map(([k, v]) => `<div><span>${k}</span><b>${esc(String(v))}</b></div>`).join("")}</div>
    <p class="source">${s ? `Shopify and Meta figures for ${esc(s.period)}, pulled ${esc(ago(s.pulled))} read-only by JARVIS. Profit line from the accountant's ledger.`
      : `${hasData ? "" : "No sales or costs logged in the last 14 days. "}From the accountant's ledger. Live Shopify and Meta figures appear here once JARVIS pulls a snapshot (ask: "pull the store and ad figures").`}</p>`;
  void pace;
}

function avatar(label, color) { return `<span class="avatar" style="--team:${color}">${esc(initials(label))}</span>`; }
function renderActivity(m) {
  const labelOf = (n) => (m.agents.find((a) => a.name === n) || {}).label || "JARVIS";
  const kind = { created: "New task", updated: "Updated", status: "Status", done: "Finished", note: "Note", delegated: "Handed out" };
  const live = [...Feed.runs.values()].map((r) => ({ who: labelOf(r.agent), color: Feed.teamOf(r.agent).color, what: r.description || "Working", ts: r.at, agent: r.agent }));
  const events = m.activity.slice(0, 8).map((e) => ({ who: e.agent ? labelOf(e.agent) : "JARVIS", color: Feed.teamOf(e.agent).color,
    what: `${kind[e.kind] || e.kind}: ${e.title}`, ts: e.ts, task: e.task_id }));
  // Real changes JARVIS made through the connectors (the hooks' action log): store edits, ads, files.
  const did = m.actions.slice(0, 4).map((a) => ({ who: "JARVIS", color: JARVIS_TEAM.color, what: `${a.service}: ${a.tool}${a.what ? ` ${a.what}` : ""}`, ts: a.ts, link: a.link }));
  const rows = [...live, ...did, ...events].sort((a, b) => Date.parse(b.ts || 0) - Date.parse(a.ts || 0)).slice(0, 5);
  $("activity").innerHTML = `<h2 class="card-title"><span class="lamp working"></span>Live activity<button class="more" data-nav="tasks">View all</button></h2>
    ${rows.length ? `<ol>${rows.map((r) => `<li>${r.link ? `<a class="act" href="${esc(r.link)}" target="_blank" rel="noopener">` : `<button class="act" ${r.task ? `data-task="${r.task}"` : r.agent ? `data-agent="${esc(r.agent)}"` : ""}>`}${avatar(r.who, r.color)}
      <span><b>${esc(r.who)}</b><span>${esc(r.what)}</span></span><time>${esc(ago(r.ts))}</time>${r.link ? "</a>" : "</button>"}</li>`).join("")}</ol>` : `<p class="empty">Nothing has happened yet today.</p>`}`;
}

function renderPerf(m) {
  // Tasks finished per day over the last 10 days, and the share of open tasks that are on track (not blocked or stuck on approval).
  const days = [...Array(10)].map((_, i) => localDay(new Date(Date.now() - (9 - i) * 86400000)));
  const per = days.map((d) => m.doneAll.filter((t) => (t.completed || "").slice(0, 10) === d).length);
  const max = Math.max(1, ...per);
  const pct = m.counts.onTrack, C2 = 2 * Math.PI * 30;
  $("perf").innerHTML = `<h2 class="card-title">${icon("i-chart")}Agent performance</h2>
    <div class="perf"><div><div class="bars" role="img" aria-label="Tasks finished per day, last 10 days: ${per.join(", ")}">${per.map((n) => `<i class="${n ? "" : "zero"}" style="height:${(n / max) * 100}%" title="${n}"></i>`).join("")}</div>
      <p class="quiet" style="margin:6px 0 0">${per.reduce((a, b) => a + b, 0)} tasks finished in 10 days</p></div>
      <div class="gauge"><svg viewBox="0 0 74 74"><circle cx="37" cy="37" r="30" stroke="rgba(255,255,255,.12)" stroke-width="6" fill="none"/>
        <circle cx="37" cy="37" r="30" stroke="${pct >= 80 ? "#3fcf8e" : pct >= 50 ? "#f0b43c" : "#ff5a5f"}" stroke-width="6" fill="none" stroke-linecap="round" stroke-dasharray="${(C2 * pct) / 100} ${C2}"/></svg>
        <b>${pct}%</b><span>Tasks on track</span></div></div>`;
}

let lastToast = null;
function renderToast(m) {
  const t = m.doneAll[0];
  if (!t) { $("toast").hidden = true; return; }
  $("toast").hidden = false;
  $("toast").dataset.task = t.id;
  if (lastToast !== t.id) {
    lastToast = t.id;
    $("toast").innerHTML = `<span class="ok">${icon("i-ok", "")}</span><span>${esc(t.who)} finished T${t.id}: ${esc(t.title)}</span><time>${esc(ago(t.completed))}</time>`;
  }
}

// ================================================================== the task board (Tasks panel; inline on a phone)

let boardTab = "now";
const COLS = [["now", "Now"], ["ask", "Needs you"], ["done", "Done today"], ["todo", "To do"]];
function teamTag(t) { return `<span class="team-tag" style="--team:${t.color}">${esc(t.name)}</span>`; }
function taskItem(t) {
  const cls = t.status === "blocked" ? " blocked" : t.status === "waiting_approval" ? " ask" : t.live ? " live" : "";
  const when = t.completed || t.updated;
  const meta = `${teamTag(t.team)}${t.who && t.who !== t.team.name ? `<span>${esc(t.who)}</span>` : ""}${t.id ? `<span>T${t.id}</span>` : ""}`
    + `${t.status === "blocked" ? "<span>blocked</span>" : t.status === "waiting_approval" ? "<span>waiting for your approval</span>" : ""}`
    + `${t.live ? "<span>handed out just now</span>" : ""}${when ? `<span>${esc(ago(when))}</span>` : ""}`;
  const target = t.id ? `data-task="${t.id}"` : t.agent ? `data-agent="${esc(t.agent)}"` : "";
  return `<li><button class="row${cls}" ${target}><div class="t">${esc(t.title)}</div><div class="m">${meta}</div></button></li>`;
}
function boardHTML(m) {
  const b = m.board, c = m.counts;
  const count = { now: c.now, ask: c.ask, done: c.doneToday, todo: c.todo };
  let list;
  if (boardTab === "now") list = b.now.length ? b.now.map(taskItem).join("") : `<p class="empty">Nobody is mid-task right now.</p>`;
  else if (boardTab === "ask") list = c.ask ? [
    ...b.ask.approvals.map((a) => `<li class="row ask"><div class="t">${esc(a.summary)}</div>
      <div class="m"><span class="team-tag" style="--team:${JARVIS_TEAM.color}">${esc(a.kind)}</span>${a.amount != null ? `<span>${esc(a.amount)} ${esc(a.currency || "")}</span>` : ""}${a.task_id ? `<span>T${a.task_id}</span>` : ""}<span>${esc(ago(a.ts))}</span></div>
      ${a.detail ? `<div class="m">${esc(a.detail)}</div>` : ""}
      <div class="btns"><button class="btn gold" data-approve="${a.id}">Approve</button><button class="btn" data-reject="${a.id}">Reject</button></div></li>`),
    ...b.ask.tasks.map(taskItem),
  ].join("") : `<p class="empty">Nothing is waiting for you.</p>`;
  else if (boardTab === "done") list = [
    ...(b.done.today.length ? b.done.today.map(taskItem) : [`<p class="empty">Nothing finished yet today.</p>`]),
    ...(b.done.earlier.length ? [`<li class="quiet" style="margin-top:8px">Earlier, last two weeks</li>`, ...b.done.earlier.slice(0, 20).map(taskItem)] : []),
  ].join("");
  else list = b.todo.length ? b.todo.map(taskItem).join("") : `<p class="empty">The to-do list is empty.</p>`;
  return `<div class="tabs" role="tablist">${COLS.map(([k, label]) => `<button role="tab" data-tab="${k}" aria-selected="${k === boardTab}"><b>${count[k]}</b>${label}</button>`).join("")}</div><ol>${list}</ol>`;
}
function renderMobileBoard(m) {
  $("m-board").innerHTML = `<h2 class="card-title">${icon("i-board")}Tasks<span class="more">${m.source === "activity" ? "Activity log" : "Task list"}</span></h2>${boardHTML(m)}`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-tab]");
  if (!b) return;
  boardTab = b.dataset.tab;
  renderAll(Feed.model, true);
});
// Approve and Reject: one tap each, as the owner asked. Approving tells JARVIS to carry it out.
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

// ================================================================== panels

let selected = null;   // "team:<id>" | "agent:<name>" | "task:<id>" | "jarvis" | "agents" | "tasks" | "analytics" | "settings" | null
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
    box.querySelector("img").onerror = () => { box.querySelector("img").outerHTML = `<div class="doc"><p>This image can't be shown here (it may have moved): ${esc(ref.path)}</p></div>`; };
    return;
  }
  box.innerHTML = `${bar}<div class="doc"><p>Opening…</p></div>`;
  let text = previewCache.get(ref.path);
  if (text === undefined) {
    try { const r = await fetch(url); text = r.status === 200 ? await r.text() : null; } catch (e) { text = null; }
    previewCache.set(ref.path, text);
    setTimeout(() => previewCache.delete(ref.path), 30000);
  }
  const doc = box.querySelector(".doc");
  if (doc) doc.innerHTML = text == null ? `<p>This file can't be shown here (it may have moved): ${esc(ref.path)}</p>` : lightMarkdown(text);
}

const facts = (rows) => `<dl class="facts">${rows.filter(Boolean).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
const taskList = (rows, empty) => rows.length ? `<ol>${rows.map(taskItem).join("")}</ol>` : `<p class="empty">${empty}</p>`;
// Built-in Claude Code tools are not connectors; they are listed apart from the account's connectors.
const BUILTIN = new Set(["JARVIS command line", "Files", "Web search", "Web pages"]);
function connectionsHTML(names, m) {
  const conns = names.filter((c) => !BUILTIN.has(c)), tools = names.filter((c) => BUILTIN.has(c));
  return `<h3>Connectors</h3><div class="chips">${conns.length ? connectorTags(conns, m) : `<span class="quiet">None in its agent file</span>`}</div>
    <h3>Built-in tools</h3><div class="chips">${tools.map((c) => `<span class="tag">${esc(c)}</span>`).join("")}</div>`;
}
function connectorTags(names, m) {
  const known = m.connectors?.servers || [];
  return names.map((c) => {
    const k = known.find((s) => c.toLowerCase().includes(connectorName(s.name).toLowerCase().split(" ")[0]));
    const cls = k ? (k.ok ? "ok" : "bad") : "unknown";
    return `<span class="tag ${cls}" title="${k ? esc(k.status) : "Status not checked yet"}">${esc(c)}${k && !k.ok ? `: ${esc(k.status)}` : ""}</span>`;
  }).join("");
}
function agentRow(a, color) {
  const where = scene?.whereabouts?.()[a.name];
  const line = a.status === "idle" ? (where && where !== "at the desk" ? `Idle, ${where}` : a.next ? `Idle. Next up: ${a.next}` : "Idle") : a.now || STATUS[a.status].word;
  return `<li><button class="row agent-row" data-agent="${esc(a.name)}">${avatar(a.label, color)}<span><b>${esc(a.label)}</b><span>${esc(line)}</span></span><span class="lamp ${a.status}" title="${STATUS[a.status].word}"></span></button></li>`;
}
function agentFacts(a) {
  const where = scene?.whereabouts?.()[a.name];
  return facts([
    ["Status", `${STATUS[a.status].word}${a.status === "idle" && where ? `, ${esc(where)}` : ""}`],
    ["Now", a.now ? esc(a.now) : a.next ? `Nothing running. Next up: ${esc(a.next)}` : "Nothing running"],
    a.status === "working" && ["Tool", a.tool ? esc(a.tool) : "Not reported yet"],
    a.since && ["Since", esc(ago(a.since))],
    ["Model", esc(modelName(a.model))],
    ["Open tasks", String(a.open.length)],
    a.via && ["Seen in", esc(a.via)],
  ]);
}
const head = (kicker, title, sub, extra = "") => `<div class="p-head"><div class="kicker">${kicker}</div><h2 id="p-title">${title}</h2>${sub ? `<p>${sub}</p>` : ""}${extra}</div>`;

function panelTeam(m, t) {
  const counts = Object.entries(t.agents.reduce((acc, a) => ({ ...acc, [a.status]: (acc[a.status] || 0) + 1 }), {}))
    .sort((a, b) => RANK[b[0]] - RANK[a[0]]).map(([s, n]) => `${n} ${STATUS[s].word.toLowerCase()}`).join(", ");
  const names = new Set(t.agents.map((a) => a.name));
  const conns = [...new Set(t.agents.flatMap((a) => a.connections))];
  const skills = [...new Set(t.agents.flatMap((a) => a.skills))];
  const tasks = m.allTasks.filter((x) => names.has(x.agent));
  const ref = (t.agents.find((a) => a.preview && a.status !== "idle") || t.agents.find((a) => a.preview) || {}).preview || null;
  return { ref, html: head(`${esc(t.name)} pavilion`, esc(t.name), esc(t.job), `<div class="state-line"><span class="lamp ${t.status}"></span>${esc(counts)}</div>`) + `
    <div class="p-body">
      <div class="btns"><button class="btn gold" data-ask="${esc(t.name)}, ">Talk to ${esc(t.name)}</button><button class="btn" data-ask="${esc(t.name)}, take on this task: ">Assign a task</button></div>
      <h3>What it's building</h3><div class="preview" id="p-preview"></div>
      <h3>Agents <span class="n">${t.agents.length}</span></h3><ol>${t.agents.map((a) => agentRow(a, t.color)).join("")}</ol>
      ${connectionsHTML(conns, m)}
      <p class="note">From the agents' files. Status comes from Claude Code on this PC (Settings, Check connections)${m.connectors?.checked ? `, last checked ${esc(ago(m.connectors.checked))}` : ", not checked yet"}. JARVIS itself can also use every connector on the account when it works on this team's behalf.</p>
      <h3>Skills and playbooks</h3><div class="chips">${skills.map((c) => `<span class="tag">${esc(c)}</span>`).join("")}</div>
      <h3>Open tasks <span class="n">${tasks.filter((x) => x.status !== "done").length || ""}</span></h3>${taskList(tasks.filter((x) => x.status !== "done"), "No open tasks.")}
      <h3>Finished in the last two weeks</h3>${taskList(tasks.filter((x) => x.status === "done"), "Nothing finished lately.")}
      <p class="note">Team grouping is the draft from the T54 plan, still to agree.</p>
    </div>` };
}
function panelAgent(m, a) {
  const team = m.teams.find((t) => t.agents.includes(a)) || JARVIS_TEAM;
  const tasks = m.allTasks.filter((x) => x.agent === a.name);
  const subs = a.runs.length ? `<h3>Sub-agents working now <span class="n">${a.runs.length}</span></h3><ol>${a.runs.map((r) => `<li class="row live"><div class="t">${esc(r.description || "Working")}</div><div class="m"><span>started ${esc(ago(r.at))}</span></div></li>`).join("")}</ol>` : "";
  return { ref: a.preview, html: head(`${esc(team.name)} team`, esc(a.label), esc(a.title), `<div class="state-line"><span class="lamp ${a.status}"></span>${STATUS[a.status].word}</div>`) + `
    <div class="p-body">
      <div class="btns"><button class="btn gold" data-ask="${esc(a.label)}, ">Talk to ${esc(a.label)}</button><button class="btn" data-ask="${esc(a.label)}, take on this task: ">Assign a task</button><button class="btn" data-team="${team.id}">Open ${esc(team.name)}</button></div>
      ${agentFacts(a)}
      ${subs}
      <h3>Workstation: what it's building</h3><div class="preview" id="p-preview"></div>
      <h3>Responsibilities</h3><p class="desc">${esc(a.role || a.title)}</p>
      ${connectionsHTML(a.connections, m)}
      <h3>Skills and playbooks</h3><div class="chips">${a.skills.map((c) => `<span class="tag">${esc(c)}</span>`).join("")}</div>
      <h3>Open tasks</h3>${taskList(tasks.filter((x) => x.status !== "done"), "No open tasks.")}
      <h3>History, last two weeks</h3>${taskList(tasks.filter((x) => x.status === "done"), "Nothing finished in the last two weeks.")}
      <p class="note">Default model is set in .claude/agents/${esc(a.name)}.md. Pausing or resuming an agent, and choosing a model for one task, aren't supported by the backend yet.</p>
    </div>` };
}
function panelTask(m, t) {
  const subs = m.allTasks.filter((x) => x.parent_id === t.id);
  const parent = t.parent_id ? m.allTasks.find((x) => x.id === t.parent_id) : null;
  const word = { in_progress: "In progress", todo: "To do", blocked: "Blocked", waiting_approval: "Waiting for your approval", done: "Done" }[t.status] || t.status;
  return { ref: t.deliverable ? { path: t.deliverable, why: `T${t.id}` } : null, html: head(`T${t.id}, ${esc(word)}`, esc(t.title), "") + `
    <div class="p-body">
      ${facts([
        ["Team", t.team.id === "jarvis" ? teamTag(t.team) : `<button data-team="${t.team.id}">${teamTag(t.team)}</button>`],
        t.agent && ["Agent", `<button data-agent="${esc(t.agent)}" style="text-decoration:underline">${esc(t.who)}</button>`],
        t.product && ["Product", esc(t.product)],
        parent && ["Part of", `<button data-task="${parent.id}" style="text-decoration:underline;text-align:left">T${parent.id} ${esc(parent.title)}</button>`],
        ["Updated", esc(ago(t.completed || t.updated))],
      ])}
      <div class="btns"><button class="btn gold" data-ask="About T${t.id}: ">Ask JARVIS about this</button></div>
      ${t.description ? `<h3>Brief</h3><p class="desc">${esc(t.description)}</p>` : ""}
      ${t.summary ? `<h3>Result</h3><p class="desc">${esc(t.summary)}</p>` : ""}
      <h3>Deliverable</h3><div class="preview" id="p-preview"></div>
      ${subs.length ? `<h3>Sub-tasks <span class="n">${subs.length}</span></h3>${taskList(subs, "")}` : ""}
    </div>` };
}
// JARVIS: the command chat (the recent conversation, from the journal) and the boardroom (work shared across teams).
function panelJarvis(m) {
  const word = { idle: "Ready", thinking: "Thinking", delegating: "Handing out work" }[m.jarvis.status] || m.jarvis.status;
  const msgs = m.journal.filter((j) => j.role === "owner" || j.role === "jarvis" || j.role === "assistant").slice(0, 12).reverse();
  const shared = m.allTasks.filter((t) => t.status !== "done" && m.allTasks.some((x) => x.parent_id === t.id));
  return { ref: null, html: head("Main brain", "JARVIS", "Every team reads from and writes to the same memory, and JARVIS hands out the work.", `<div class="state-line"><span class="lamp ${m.jarvis.status === "idle" ? "" : "working"}"></span>${esc(word)}</div>`) + `
    <div class="p-body">
      <div class="btns"><button class="btn gold" data-ask="">Talk to JARVIS</button><a class="btn" href="/chat">Full chat</a></div>
      <h3>Command chat</h3>
      ${msgs.length ? `<div class="chat">${msgs.map((j) => `<div class="msg ${j.role === "owner" ? "owner" : "jarvis"}"><small>${j.role === "owner" ? "You" : "JARVIS"}, ${esc(ago(j.ts))}</small>${esc((j.text || "").slice(0, 600))}</div>`).join("")}</div>` : `<p class="empty">No conversation yet.</p>`}
      <h3>Boardroom: work shared across teams <span class="n">${shared.length || ""}</span></h3>
      ${shared.length ? `<ol>${shared.map((t) => { const parts = m.allTasks.filter((x) => x.parent_id === t.id); const teams = [...new Set(parts.map((x) => x.team.name))]; return `<li><button class="row" data-task="${t.id}"><div class="t">${esc(t.title)}</div><div class="m"><span>T${t.id}</span><span>${parts.length} sub-tasks</span><span>${esc(teams.join(", "))}</span></div></button></li>`; }).join("")}</ol>` : `<p class="empty">No shared work right now.</p>`}
      <h3>Store, ads and file changes <span class="n">${m.actions.length || ""}</span></h3>
      ${m.actions.length ? `<ol>${m.actions.map((a) => `<li>${a.link ? `<a class="row" href="${esc(a.link)}" target="_blank" rel="noopener" style="text-decoration:none">` : `<div class="row">`}<div class="t">${esc(a.service)}: ${esc(a.tool)}${a.what ? ` ${esc(a.what)}` : ""}</div><div class="m"><span>${esc(ago(a.ts))}</span>${a.link ? "<span>Open in Shopify</span>" : ""}</div>${a.link ? "</a>" : "</div>"}</li>`).join("")}</ol>`
        : `<p class="empty">No changes made through the connectors yet. Every store, ad or file change JARVIS makes is logged here and, for Shopify, links to the object in the admin (set shopify_admin_url in brain/config.json).</p>`}
      <h3>Memory</h3><p class="desc">${Feed.state.memory?.total ?? "?"} entries, ${Feed.state.memory?.needs_review ?? 0} need review.</p>
    </div>` };
}
function panelAgents(m) {
  return { ref: null, html: head("The team", `${m.counts.agents} agents`, `${m.counts.working} working, ${m.counts.idle} idle, in ${m.teams.length} teams.`) + `
    <div class="p-body">${m.teams.map((t) => `<h3><button data-team="${t.id}" class="team-tag" style="--team:${t.color}">${esc(t.name)}</button></h3><ol>${t.agents.map((a) => agentRow(a, t.color)).join("")}</ol>`).join("")}</div>` };
}
function panelTasks(m) {
  return { ref: null, html: head("Task board", "Tasks", `${m.counts.open} open. ${m.source === "activity" ? "Live from the agent activity log." : "Live from the task list, agent runs and JARVIS's hand-offs."}`) + `<div class="p-body">${boardHTML(m)}</div>` };
}
function panelAnalytics(m) {
  const w = m.week;
  const rows = m.daily.slice().reverse().filter((d) => d.income || d.expenses);
  const tests = m.tests.slice(0, 12);
  const conn = m.connectors?.servers || [];
  const metaShop = conn.filter((s) => /meta|shopify/i.test(s.name));
  return { ref: null, html: head("Reports", "Analytics", "Real numbers only. Where there isn't enough data, it says so.") + `
    <div class="p-body">
      <h3>This week (from ${esc(w.start)})</h3>
      ${facts([["Sales", money(w.income)], ["Costs", money(w.expenses)], ["Profit", money(w.profit)], ["Weekly target", `${money(w.target)} (pace so far ${money(w.pace)})`]])}
      <h3>By day, last 14 days</h3>
      ${rows.length ? `<table class="num"><tr><th>Day</th><th class="r">Sales</th><th class="r">Costs</th><th class="r">Profit</th></tr>${rows.map((d) => `<tr><td>${esc(d.day)}</td><td class="r">${money(d.income)}</td><td class="r">${money(d.expenses)}</td><td class="r">${money(d.profit)}</td></tr>`).join("")}</table>` : `<p class="empty">No sales or costs logged in the last 14 days.</p>`}
      <h3>Product tests</h3>
      ${tests.length ? `<table class="num"><tr><th>Test</th><th>Status</th><th class="r">Spend</th><th class="r">Sales</th><th class="r">Orders</th><th class="r">ROAS</th></tr>${tests.map((t) => { const e = t.eval || {}; return `<tr><td>X${t.id} ${esc(t.product)}</td><td>${esc(t.status)}</td><td class="r">${e.spend != null ? "$" + Number(e.spend).toFixed(0) : "–"}</td><td class="r">${e.revenue != null ? "$" + Number(e.revenue).toFixed(0) : "–"}</td><td class="r">${e.purchases ?? "–"}</td><td class="r">${e.spend ? (e.revenue / e.spend).toFixed(2) : "–"}</td></tr>`; }).join("")}</table>` : `<p class="empty">No product tests logged yet. Results appear here once the brush test starts.</p>`}
      <h3>Claude Max this week</h3>${allowanceHTML(m)}
      <h3>Shopify and Meta</h3>
      ${metricsHTML(m)}
      <p class="desc">Connectors on the account${metaShop.length ? `: ${metaShop.map((s) => `${esc(connectorName(s.name))} ${esc(s.status.toLowerCase())}`).join(", ")}` : ""}. Read-only: JARVIS pulls, saves a snapshot, and nothing here can change the store or the ads.</p>
      <div class="btns"><button class="btn gold" data-ask="Pull today's and this week's Shopify and Meta figures read-only (sessions, orders, revenue, AOV, CVR, refunds; spend, CPM, CTR, CPC, add to cart, checkouts, purchases, CPA, ROAS, per product test) and save them with bin/jarvis metrics set.">${m.metrics ? "Pull fresh figures" : "Pull the figures now"}</button></div>
    </div>` };
}
// Claude Max allowance: meant to be used nearly in full each week, never run out early. A pace line shows where usage
// should be by now; idle agents with work waiting are the capacity to fill.
function allowanceHTML(m) {
  const a = m.allowance, c = m.counts;
  const idle = `${c.idle} of ${c.agents} agents are idle right now, with ${c.todo} task${c.todo === 1 ? "" : "s"} in To do that could fill that time.`;
  if (a && typeof a.used_pct === "number" && a.resets) {
    const end = Date.parse(a.resets), start = end - 7 * 86400000;
    const pace = Math.max(0, Math.min(100, ((Date.now() - start) / (end - start)) * 100));
    const diff = a.used_pct - pace;
    const word = diff > 5 ? "Ahead of pace: ease off a little so it lasts to the reset" : diff < -5 ? "Behind pace: room to give the agents more backlog work" : "On pace";
    return `<p class="desc"><b>${Math.round(a.used_pct)}%</b> used, pace mark at ${Math.round(pace)}%. ${esc(word)}. Resets ${esc(relDay(a.resets))}.</p><p class="desc">${esc(idle)}</p>`;
  }
  return `<p class="desc"><span class="tag unknown">Not connected yet</span></p><p class="desc">Claude doesn't report Max usage to JARVIS yet. Once it does, this shows the week's usage against a pace line, so the allowance is used nearly in full without running out before the reset.</p><p class="desc">${esc(idle)}</p>`;
}
// Plan section 14: every important Shopify and Meta number, from the snapshot JARVIS pulled. Missing figures show as "–".
function metricsHTML(m) {
  const s = m.metrics;
  if (!s) return `<p class="desc"><span class="tag unknown">Not pulled yet</span></p><p class="desc">Sessions, orders, revenue, AOV, CVR and refunds from Shopify; spend, CPM, CTR, CPC, add to cart, checkouts, purchases, CPA and ROAS from Meta, per day and per product test, appear here once JARVIS pulls them.</p>`;
  const usd = (n) => `$${Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  const f = (v, kind) => v == null ? "–" : kind === "$" ? usd(v) : kind === "%" ? `${Number(v).toFixed(2)}%` : kind === "x" ? `${Number(v).toFixed(2)}x` : Number(v).toLocaleString("en-US");
  const rows = (pairs) => `<table class="num">${pairs.map(([k, v, kind]) => `<tr><th>${k}</th><td class="r">${f(v, kind)}</td></tr>`).join("")}</table>`;
  const sh = s.shopify || {}, me = s.meta || {};
  return `<p class="quiet">${esc(s.period)}, pulled ${esc(ago(s.pulled))}. ${esc(s.source)}${s.notes ? `. ${esc(s.notes)}` : ""}</p>
    <h3>Shopify</h3>${rows([["Sessions", sh.sessions], ["Orders", sh.orders], ["Revenue", sh.revenue, "$"], ["Average order", sh.aov, "$"], ["Conversion rate", sh.cvr, "%"], ["Add to cart", sh.add_to_cart], ["Checkouts", sh.checkouts], ["Refunds", sh.refunds, "$"]])}
    <h3>Meta ads</h3>${rows([["Spend", me.spend, "$"], ["Impressions", me.impressions], ["CPM", me.cpm, "$"], ["Clicks", me.clicks], ["CTR", me.ctr, "%"], ["CPC", me.cpc, "$"], ["Add to cart", me.add_to_cart], ["Checkouts", me.checkouts], ["Purchases", me.purchases], ["CPA", me.cpa, "$"], ["ROAS", me.roas, "x"]])}
    ${s.products?.length ? `<h3>By product test</h3><table class="num"><tr><th>Product</th><th class="r">Spend</th><th class="r">Revenue</th><th class="r">Orders</th><th class="r">ROAS</th></tr>${s.products.map((p) => `<tr><td>${esc(p.name)}</td><td class="r">${f(p.spend, "$")}</td><td class="r">${f(p.revenue, "$")}</td><td class="r">${f(p.orders)}</td><td class="r">${f(p.roas, "x")}</td></tr>`).join("")}</table>` : ""}`;
}
function panelSettings(m) {
  const c = m.connectors || {};
  const voices = J.voiceOptions(), cur = J.currentVoice();
  const simple = J.pref("hqSimple") === "1";
  return { ref: null, html: head("Preferences", "Settings", "") + `
    <div class="p-body">
      <h3>Voice</h3>
      <label class="switch">Speak replies aloud<input type="checkbox" data-set="speak" ${J.settings.speak ? "checked" : ""}></label>
      <label class="switch">Listen for "Hey JARVIS"<input type="checkbox" data-set="wake" ${J.settings.wake ? "checked" : ""}></label>
      <label class="switch">Hands-free follow-ups<input type="checkbox" data-set="handsfree" ${J.settings.handsfree ? "checked" : ""}></label>
      <label class="switch">JARVIS's voice<select data-set="voice">${voices.map((o) => `<option value="${esc(o.id)}" ${o.id === cur ? "selected" : ""}>${esc(o.label)}</option>`).join("") || "<option>Default voice</option>"}</select></label>
      <h3>Display</h3>
      <label class="switch">Simplified 3D (for phones and slower computers)<input type="checkbox" data-set="simple" ${simple ? "checked" : ""}></label>
      <label class="switch">Time of day<select data-set="time">${[["live", "Live (follows the clock)"], ["day", "Day"], ["sunset", "Sunset"], ["evening", "Evening"]].map(([v, l]) => `<option value="${v}" ${(J.pref("hqTime") || "live") === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <h3>Connections</h3>
      <p class="desc">The connectors Claude Code has on this PC, the same ones the desktop app and JARVIS use.</p>
      ${c.servers ? `<div class="chips" style="margin-top:8px">${c.servers.map((s) => `<span class="tag ${s.ok ? "ok" : "bad"}" title="${esc(s.status)}">${esc(connectorName(s.name))}${s.ok ? "" : `: ${esc(s.status)}`}</span>`).join("")}</div>` : ""}
      ${c.error ? `<p class="note">Last check failed: ${esc(c.error)}</p>` : ""}
      <div class="btns"><button class="btn" id="check-conn" ${c.running ? "disabled" : ""}>${c.running ? "Checking…" : "Check connections"}</button></div>
      ${c.checked ? `<p class="note">Last checked ${esc(ago(c.checked))}.</p>` : ""}
      <h3>Other views</h3>
      <div class="btns"><a class="btn" href="/chat">Full chat</a><a class="btn" href="/office">Earlier office view</a></div>
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
  else p = { jarvis: panelJarvis, agents: panelAgents, tasks: panelTasks, analytics: panelAnalytics, settings: panelSettings }[kind]?.(m);
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
  // The pavilion to fly to and light up: the agent's own, or its team's first agent's.
  const pav = pavilionFor(sel);
  document.querySelectorAll(".chip3d").forEach((el) => el.classList.toggle("on", el.dataset.id === pav));
  document.querySelectorAll("nav.pill [data-nav]").forEach((b) => b.setAttribute("aria-current", String((b.dataset.nav === "home" && !sel) || b.dataset.nav === sel)));
  if (sel) { renderPanel(false); $("panel").scrollTop = 0; $("close").focus({ preventScroll: true }); }
  if (pav || !sel) scene?.focus(pav);
}
function pavilionFor(sel) {
  if (!sel) return null;
  if (sel === "jarvis") return "jarvis";
  if (sel.startsWith("agent:")) return sel.slice(6);
  if (sel.startsWith("team:")) return Feed.model?.teams.find((t) => t.id === sel.slice(5))?.agents[0]?.name || null;
  return null;
}
$("close").addEventListener("click", () => open(null));
addEventListener("keydown", (e) => { if (e.key === "Escape" && selected) open(null); });
document.addEventListener("click", (e) => {
  const nav = e.target.closest("[data-nav]");
  if (nav) return open(nav.dataset.nav === "home" ? null : nav.dataset.nav);
  const el = e.target.closest("[data-team], [data-agent], [data-task], [data-ask]");
  if (!el || el.closest(".chip3d")) return;
  if (el.dataset.ask !== undefined) return askTo(el.dataset.ask);
  if (el.dataset.task) return open(`task:${el.dataset.task}`);
  if (el.dataset.agent) return open(`agent:${el.dataset.agent}`);
  if (el.dataset.team) return open(`team:${el.dataset.team}`);
});
$("toast").addEventListener("click", () => $("toast").dataset.task && open(`task:${$("toast").dataset.task}`));
// Settings: every switch drives the real setting in jarvis.js (or this page's own display preference).
document.addEventListener("change", (e) => {
  const k = e.target.dataset?.set;
  if (!k) return;
  if (k === "simple") { J.pref("hqSimple", e.target.checked ? "1" : "0"); location.reload(); return; }
  if (k === "voice") { J.settings.voice = e.target.value; J.preview(); return; }
  if (k === "time") { J.pref("hqTime", e.target.value); scene?.setTime(e.target.value); return; }
  J.settings[k] = e.target.checked;
});
document.addEventListener("click", async (e) => {
  if (!e.target.closest("#check-conn")) return;
  try { await J.post("/api/hq/connectors", {}); } catch (err) { /* shown on the next refresh */ }
  Feed.loadHQ();
  const poll = setInterval(async () => { await Feed.loadHQ(); if (!Feed.hq?.connectors?.running) clearInterval(poll); }, 3000);
});

// ================================================================== chips over the buildings

function renderChips(m) {
  const layer = $("labels");
  const word = { idle: "Online", thinking: "Thinking", delegating: "Handing out work" }[m.jarvis.status] || "Online";
  const mem = Feed.state.memory || {};
  const items = [{ id: "jarvis", hq: true, name: "Jarvis HQ", ic: "i-crown",
    sub: `<span class="lamp ${m.jarvis.status === "idle" ? "working" : "waiting"}"></span>${word}, ${mem.total ?? "?"} notes in the shared brain, ${mem.needs_review ?? 0} to review` },
    ...m.departments.map((t) => ({ id: t.id, name: t.name, ic: TEAM_ICONS[t.teamId] || "i-cog",
      sub: `${t.openCount} task${t.openCount === 1 ? "" : "s"}<span class="lamp ${t.status}"></span>${STATUS[t.status].word}` }))];
  for (const t of items) {
    let el = layer.querySelector(`[data-id="${t.id}"]`);
    if (!el) {
      el = document.createElement("button");
      el.className = "chip3d" + (t.hq ? " hq" : "");
      el.dataset.id = t.id;
      el.addEventListener("click", () => { const sel = t.hq ? "jarvis" : `agent:${t.id}`; open(selected === sel ? null : sel); });
      layer.appendChild(el);
    }
    el.innerHTML = `<span class="ic">${icon(t.ic)}</span><b>${esc(t.name)}</b><small>${t.sub}</small>`;
    el.setAttribute("aria-label", `${t.name}: ${el.querySelector("small").textContent}`);
  }
}

// ================================================================== talk bar (chat and voice come from jarvis.js)

const input = $("say");
const PLACEHOLDER = isPhone() ? "Talk to JARVIS or a team" : input.placeholder;
input.placeholder = PLACEHOLDER;
// Who the owner is talking to. Everything still goes through JARVIS's own Claude Code session (the one with the
// connectors, memory and approval gates); addressing a team or agent tells JARVIS to hand the message to them.
let addressee = null;
function address(name) {
  addressee = name || null;
  $("to").hidden = !addressee;
  $("to").querySelector("b").textContent = addressee || "";
  input.placeholder = addressee ? `Message ${addressee}` : PLACEHOLDER;
  input.focus();
}
$("to").querySelector("button").addEventListener("click", () => address(null));
const rawSend = J.send;
J.send = (text, images) => {   // typed or spoken: both come through here
  const t = (text || "").trim();
  return rawSend(addressee && t && !t.toLowerCase().startsWith(addressee.toLowerCase()) ? `${addressee}, ${t}` : t, images);
};
function askTo(prefix) {
  // "Name, " addresses that team or agent; anything else is a prefilled request to JARVIS.
  const m = /^([^,:]+), $/.exec(prefix);
  if (m) { address(m[1]); return; }
  address(null);
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
  if (shown !== "listening") input.placeholder = addressee ? `Message ${addressee}` : PLACEHOLDER;
});

// ================================================================== putting it together

function renderAll(m, quick) {
  if (!m) return;
  renderTop(m); renderOverview(m); renderActivity(m); renderPerf(m); renderToast(m); renderMobileBoard(m); renderChips(m);
  if (selected) renderPanel(true);
  if (!quick) { scene?.update(m); (Feed.hq?.rewards || []).forEach((r) => scene?.reward?.(r)); }
}
Feed.on((kind, data) => {
  if (kind === "model") renderAll(data);
  else if (kind === "dispatch") scene?.dispatch(data.agent);
});
setInterval(() => { if (selected?.startsWith("agent:") || selected?.startsWith("team:") || selected === "agents") renderPanel(true); }, 5000);  // whereabouts change as agents move

document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.view === "reset") open(null);
  const on = scene?.view(b.dataset.view);
  document.querySelector('[data-view="explore"]').setAttribute("aria-pressed", String(!!on));
}));
$("demo").addEventListener("click", () => {
  if (!scene?.demo) return;
  const on = $("demo").getAttribute("aria-pressed") !== "true";
  $("demo").setAttribute("aria-pressed", String(on));
  $("demo-note").hidden = !on;
  scene.demo(on);
});

// Where the page's cards sit over the estate, so building chips can hide behind them (refreshed once a second).
let rectsAt = 0, rects = [];
function cardRects() {
  if (performance.now() - rectsAt > 1000) {
    rectsAt = performance.now();
    const base = $("stage").getBoundingClientRect();
    rects = [...document.querySelectorAll(".card, .views, .top > *, .center-bottom")].filter((e) => e.offsetParent !== null)
      .map((e) => { const r = e.getBoundingClientRect(); return { left: r.left - base.left - 6, right: r.right - base.left + 6, top: r.top - base.top - 6, bottom: r.bottom - base.top + 6 }; });
  }
  return rects;
}
// Simplified mode: the owner's choice in Settings, ?simple in the address, or automatically on phones and low-power devices.
const simple = /[?&]simple\b/.test(location.search) || J.pref("hqSimple") === "1" || (J.pref("hqSimple") !== "0" && (isPhone() || (navigator.hardwareConcurrency || 8) <= 4));
import("/hq-scene.js").then(({ startScene }) => startScene({
  stage: $("stage"), canvas: $("scene"), labels: $("labels"), veil: $("veil"), status: STATUS, simple,
  selected: () => pavilionFor(selected),
  avoid: cardRects,
  onPick: (kind, id) => open(kind === "jarvis" ? "jarvis" : kind === "team" ? `agent:${id}` : `${kind}:${id}`),   // a pavilion is an agent's
  onExplore: (on) => document.querySelector('[data-view="explore"]').setAttribute("aria-pressed", String(on)),
  fail: (text) => { $("nogl").hidden = false; $("nogl").textContent = text; $("veil").classList.add("gone"); document.querySelector(".views").hidden = true; return null; },
})).then((s) => {
  scene = s;
  scene?.setTime(J.pref("hqTime") || "live");
  if (scene && Feed.model) scene.update(Feed.model);
}).catch((e) => console.error(e));
