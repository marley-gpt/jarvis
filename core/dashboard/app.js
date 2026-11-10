// The app shell (T110, D75): one frame for every page. The navigation, the status strip, the inbox, the record
// drawer and the talk panel live here and stay put; a page is a script (page-<name>.js) that draws into the middle
// and is loaded the first time it is opened. Moving between pages changes the address without reloading, and every
// page draws from the same state (J.sections), which arrives on the one event stream jarvis.js holds open.
//
// A page registers itself:  App.register("work", { title, render(el, S), open(arg, el) })
//   render  is called when the page is shown and whenever the state changes while it is showing
//   open    is called with the rest of the address (/work/T110 -> "T110")
// Markup conventions the shell acts on anywhere:  data-ref="T12" opens the record drawer; data-file="brain/..."
// previews a deliverable; data-act="<name>" runs App.acts[name](element); a[href="/..."] navigates in place.
(function () {
  const J = window.J;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = J.esc;
  const App = window.App = { pages: {}, acts: {}, page: null, arg: "" };
  const S = () => J.sections;

  // ---------------------------------------------------------------- formatting
  const money = (sym) => (v, dp = 2) => v == null ? "n/a" : (v < 0 ? "-" : "") + sym
    + Math.abs(v).toLocaleString("en-AU", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const F = App.F = {
    aud: money("A$"), usd: money("US$"),
    num: (v, dp = 0) => v == null ? "n/a" : Number(v).toLocaleString("en-AU", { minimumFractionDigits: dp, maximumFractionDigits: dp }),
    pct: (v, dp = 1) => v == null ? "n/a" : (v * 100).toFixed(dp) + "%",
    x: (v) => v == null ? "n/a" : Number(v).toFixed(2) + "x",
    hhmm: (ts) => (ts || "").slice(11, 16),
    day: (ts) => (ts || "").slice(0, 10),
    ago(ts) {
      const t = typeof ts === "number" ? ts * 1000 : Date.parse(ts || "");
      if (!t) return "";
      const m = Math.max(0, Math.round((Date.now() - t) / 60000));
      return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
    },
    plural: (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`,
  };
  // Record ids in running text become links: T12, A3, D7, X1 and memory ids open the drawer, W36 opens its log.
  const REF = /\b(M-\d{6}-[0-9a-f]{4}|[TADX]\d{1,5}|W\d{1,4})\b/g;
  const link = App.link = (text) => esc(text).replace(REF, (m) => m[0] === "W"
    ? `<a class="ref" href="/work/${m}">${m}</a>` : `<a class="ref" data-ref="${m}">${m}</a>`);
  const ref = App.ref = (id) => id ? `<a class="ref" data-ref="${esc(id)}">${esc(id)}</a>` : "";
  const STATUS = { in_progress: "run", running: "run", working: "run", active: "run", done: "done", approved: "done", passed: "done",
    blocked: "wait", waiting_approval: "wait", pending: "wait", queued: "", todo: "", stopped: "bad", failed: "bad",
    rejected: "bad", cancelled: "" };
  const chip = App.chip = (status, label) => `<span class="chip ${STATUS[status] ?? ""}">${esc(label || String(status || "").replace(/_/g, " "))}</span>`;
  const bar = App.bar = (frac, cls = "", mark = null) => `<div class="bar ${cls}"><i style="width:${Math.max(0, Math.min(1, frac || 0)) * 100}%"></i>${
    mark == null ? "" : `<s style="left:${Math.max(0, Math.min(1, mark)) * 100}%"></s>`}</div>`;
  // Set a block's markup only when it changed, so scrolling, focus and typing survive the state arriving.
  const block = App.block = (el, html) => { if (el && el._h !== html) { el._h = html; el.innerHTML = html; return true; } return false; };

  function toast(text, kind = "", ms = 4500) {
    const t = document.createElement("div");
    t.className = `toast ${kind}`; t.textContent = text;
    $("#toasts").append(t);
    setTimeout(() => t.remove(), ms);
  }
  App.toast = toast;
  App.get = async (path) => {
    const r = await fetch(path);
    if (r.status === 204) return null;
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  };
  const fresh = () => fetch("/api/app?fresh=1").then((r) => r.json()).then((d) => { Object.assign(J.sections, d.sections); J.emit("state", { sections: d.sections }); }).catch(() => {});
  // Run an action against the server, say what happened in the same words as the button, and show the new state at once.
  App.run = async (path, body, done) => {
    try { const r = await J.post(path, body); if (done) toast(typeof done === "function" ? done(r) : done); fresh(); return r; }
    catch (e) { toast(e.message, "err"); return null; }
  };
  App.say = (text) => { J.send(text); openTalk(); };

  // ---------------------------------------------------------------- pages and addresses
  const ROUTES = { "": "testing", testing: "testing", team: "team", work: "work", money: "money", brain: "brain", system: "system" };
  const loading = {}, scrolls = {};
  let opening = false;
  const parse = (pathname) => {
    const [first, ...rest] = pathname.replace(/^\/+|\/+$/g, "").split("/");
    return { page: ROUTES[first || ""] || "testing", arg: decodeURIComponent(rest.join("/")) };
  };
  const load = (name) => App.pages[name] ? Promise.resolve() : (loading[name] ||= new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = `/page-${name}.js`; s.onload = res; s.onerror = () => { delete loading[name]; rej(new Error("That page didn't load. Check the dashboard is running.")); };
    document.head.append(s);
  }));
  App.register = (name, page) => { App.pages[name] = page; };
  App.go = async (path, push = true) => {
    const { page, arg } = parse(path);
    if (push && location.pathname !== path) history.pushState(null, "", path);
    $$("#nav a[data-page]").forEach((a) => a.classList.toggle("on", a.dataset.page === page));
    const main = $("#main");
    if (App.page && App.page !== page) scrolls[App.page] = main.scrollTop;
    const moved = App.page !== page;
    App.page = page; App.arg = arg;
    $("#app").classList.remove("talk-open");
    try { await load(page); } catch (e) { toast(e.message, "err"); return; }
    if (App.page !== page) return;   // the owner moved on while it loaded
    let el = $(`#page-${page}`);
    if (!el) {
      el = document.createElement("section");
      el.className = "page"; el.id = `page-${page}`;
      main.append(el);
    }
    $$(".page", main).forEach((p) => { p.hidden = p !== el; });
    document.title = `${App.pages[page].title} | JARVIS`;
    draw();
    // A deep link opens its record once the page has something to open it in: on a cold load that is the first state.
    if (S().kpis) App.pages[page].open?.(arg, el); else opening = true;
    if (moved) main.scrollTop = scrolls[page] || 0;
  };
  function draw() {
    const p = App.pages[App.page], el = $(`#page-${App.page}`);
    if (p && el && S().kpis) p.render(el, S());
  }
  App.draw = draw;
  addEventListener("popstate", () => App.go(location.pathname, false));

  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href^='/']");
    if (a && !e.ctrlKey && !e.metaKey && !e.shiftKey && a.target !== "_blank" && !a.getAttribute("href").startsWith("/api/")) {
      e.preventDefault(); App.go(a.getAttribute("href")); return;
    }
    const r = e.target.closest("[data-ref]");
    if (r) { e.preventDefault(); App.record(r.dataset.ref); return; }
    const f = e.target.closest("[data-file]");
    if (f) { e.preventDefault(); App.file(f.dataset.file); return; }
    const act = e.target.closest("[data-act]");
    if (act && App.acts[act.dataset.act]) { e.preventDefault(); App.acts[act.dataset.act](act, e); return; }
    const c = e.target.closest("[data-close]");
    if (c) c.closest(".drawer").hidden = true;
  });
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") { $$(".drawer").forEach((d) => { d.hidden = true; }); $("#app").classList.remove("talk-open"); }
    if (e.key === "/" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); openTalk(); $("#talkInput").focus(); }
  });

  // ---------------------------------------------------------------- the status strip and navigation counts
  function strip() {
    const k = S().kpis;
    if (!k) return;
    const stale = k.live ? "" : " stale", be = k.breakeven;
    const roasCls = k.roas_week == null || be == null ? "" : k.roas_week >= be ? "good" : "bad";
    const week = k.weekly_target_aud ? k.profit_week_aud / k.weekly_target_aud : 0;
    const cap = k.capacity_used_pct / 100, ceil = k.capacity_ceiling_pct / 100;
    block($("#kpis"), `
      <div class="kpi${stale}"><span class="k">Sales today</span><span class="v">${F.aud(k.sales_aud)}<small>${F.plural(k.sales_count, "order")}</small></span></div>
      <div class="kpi${stale}"><span class="k">Ad spend today</span><span class="v">${F.aud(k.spend_aud)}</span></div>
      <div class="kpi${stale}"><span class="k">Profit today</span><span class="v ${k.profit_today_aud < 0 ? "bad" : k.profit_today_aud > 0 ? "good" : ""}">${F.aud(k.profit_today_aud)}</span></div>
      <div class="kpi${stale}"><span class="k">ROAS, 7 days</span><span class="v ${roasCls}">${F.x(k.roas_week)}<small>${
        k.roas_week == null ? "no ad spend" : be ? `breakeven ${F.x(be)}` : "no breakeven set"}</small></span></div>
      <div class="kpi"><span class="k">Week against ${F.aud(k.weekly_target_aud, 0)}</span><span class="v">${F.aud(k.profit_week_aud)}</span>${bar(week, week >= 1 ? "good" : "")}</div>
      <div class="kpi" title="Weekly Claude allowance used; the line is the ceiling that keeps a reserve for you"><span class="k">Claude capacity</span><span class="v">${
        F.num(k.capacity_used_pct)}%<small>of ${F.num(k.capacity_ceiling_pct)}%</small></span>${bar(cap, cap >= ceil ? "bad" : cap >= ceil * .85 ? "amber" : "", ceil)}</div>
      ${k.live ? "" : `<span class="nolive" title="${esc(k.why_not_live)}">No live data</span>`}`);
    const live = S().live || { feed: [], count: 0 }, last = live.feed[live.feed.length - 1];
    block($("#ticker"), live.count ? `<b>${live.count} working</b> ${last ? esc(`${last.who}: ${last.text}`) : ""}` : "No agents working");
    const n = (S().inbox || []).length;
    $("#inboxN").textContent = n;
    $("#inboxBtn").classList.toggle("hot", n > 0);
    const set = (id, text, cls) => { const el = $(id); el.textContent = text || ""; el.className = cls || ""; };
    const tests = (S().tests || []).filter((t) => t.live).length;
    set("#nTesting", tests ? `${tests} live` : "", "run");
    set("#nTeam", live.count || "", "run");
    set("#nWork", n || "", "hot");
    const failed = ((S().system || {}).routines?.routines || []).filter((r) => r.last_run && !r.ok).length;
    set("#nSystem", failed || "", "hot");
    block($("#navFoot"), `<span class="dot ${J.connected ? "on" : "bad"}"></span> ${J.connected ? "Live" : "Reconnecting"}`);
    if (!$("#inbox").hidden) block($("#inboxBody"), App.inboxHTML(S().inbox || []));
  }

  // ---------------------------------------------------------------- inbox: everything that waits for the owner
  const GATES = { spend: "Spend", launch: "Launch", budget: "Budget change", shopify_live: "Live store change", price: "Price change",
    supplier: "Supplier contact", delete: "Delete", other: "Approval", tool: "Permission" };
  function itemHTML(i) {
    let actions = "", meta = [];
    if (i.kind === "approval") {
      meta = [GATES[i.gate] || "Approval", i.amount != null ? `${i.currency || "AUD"} ${F.num(i.amount, 2)}` : "", i.product || "", F.ago(i.ts)];
      actions = `<button class="btn ok sm" data-act="approve" data-id="${i.approval}">Approve A${i.approval}</button>
        <button class="btn no sm" data-act="reject" data-id="${i.approval}">Reject</button>${i.task ? ` ${ref(i.task)}` : ""}`;
    } else if (i.kind === "permission") {
      meta = ["JARVIS is waiting on this to carry on", i.approval ? `A${i.approval}` : ""];
      actions = `<button class="btn ok sm" data-act="permit" data-id="${esc(i.id)}" data-yes="1">Allow</button>
        <button class="btn no sm" data-act="permit" data-id="${esc(i.id)}">Refuse</button>`;
    } else if (i.kind === "question") {
      actions = i.questions.map((q) => `<div class="q" data-q="${esc(q.question)}"><div class="d">${esc(q.question)}</div><div class="row">${
        q.options.map((o) => `<button class="btn sm" data-act="pick" data-v="${esc(o)}">${esc(o)}</button>`).join("")}
        <input class="field" placeholder="Or type an answer" style="flex:1 1 140px"></div></div>`).join("")
        + `<div class="row"><button class="btn primary sm" data-act="answer" data-id="${esc(i.id)}">Send answer</button></div>`;
    } else if (i.kind === "worker") {
      actions = `<button class="btn sm" data-act="worker-resume" data-id="${i.id}">Resume ${i.id}</button> <a class="btn sm ghost" href="/work/${i.id}">Open its log</a>`;
    } else if (i.kind === "job") {
      actions = `<button class="btn sm" data-act="say" data-text="Desktop job ${i.id} failed. Look at why and tell me what you suggest.">Ask JARVIS about it</button>`;
    } else if (i.kind === "jobs") {
      actions = `<a class="btn sm" href="/work">See the jobs</a> <button class="btn sm" data-act="say" data-text="Several desktop jobs failed. Tell me why and which ones still need doing.">Ask JARVIS about them</button>`;
    } else if (i.kind === "kill") {
      actions = `<button class="btn sm" data-act="say" data-text="My kill rule is hit. Raise the approval to pause the test and tell me what Blake would do next.">Ask JARVIS to raise the pause</button>
        <button class="btn sm" data-act="debate" data-kind="kill" data-question="The kill rule is hit. Kill the test, or continue?">Put it to the council</button>`;
    }
    const d = [meta.filter(Boolean).join(", "), i.detail || ""].filter(Boolean).map(esc).join("<br>");
    return `<div class="item" data-key="${esc(i.key)}"><div class="t">${link(i.title)}</div>${d ? `<div class="d">${d}</div>` : ""}<div class="row">${actions}</div></div>`;
  }
  App.inboxHTML = (items, limit) => items.length ? items.slice(0, limit || 99).map(itemHTML).join("")
    : `<div class="empty">Nothing is waiting for you.</div>`;

  Object.assign(App.acts, {
    approve: (el) => J.decide(+el.dataset.id, true).then(() => { toast(`A${el.dataset.id} approved`); fresh(); }).catch((e) => toast(e.message, "err")),
    reject: (el) => J.decide(+el.dataset.id, false).then(() => { toast(`A${el.dataset.id} rejected`); fresh(); }).catch((e) => toast(e.message, "err")),
    permit: (el) => App.run("/api/control/permission", { id: el.dataset.id, approve: !!el.dataset.yes }, el.dataset.yes ? "Allowed" : "Refused"),
    pick: (el) => { const q = el.closest(".q"); $$("button", q).forEach((b) => b.classList.toggle("primary", b === el)); q.dataset.picked = el.dataset.v; },
    answer: (el) => {
      const answers = {};
      $$(".q", el.closest(".item")).forEach((q) => { const v = $("input", q).value.trim() || q.dataset.picked; if (v) answers[q.dataset.q] = v; });
      if (!Object.keys(answers).length) return toast("Pick or type an answer first.", "err");
      App.run("/api/control/permission", { id: el.dataset.id, answers }, "Answer sent");
    },
    say: (el) => { App.say(el.dataset.text); toast("Sent to JARVIS"); },
    "worker-resume": (el) => App.run("/api/control/worker", { action: "resume", id: el.dataset.id }, (r) => r.resumed.length ? `${el.dataset.id} resumed` : `${el.dataset.id} had nothing to resume`),
    "worker-stop": (el) => App.run("/api/control/worker", { action: "stop", id: el.dataset.id }, `${el.dataset.id} stopped`),
    "job-cancel": (el) => App.run("/api/control/job", { cancel: el.dataset.id }, `${el.dataset.id} cancelled`),
    debate: (el) => debateDialog(el.dataset),
  });
  $("#inboxBtn").onclick = () => { const d = $("#inbox"); d.hidden = !d.hidden; if (!d.hidden) block($("#inboxBody"), App.inboxHTML(S().inbox || [])); };

  // ---------------------------------------------------------------- debate mode (marketing-council, as a durable worker)
  function debateDialog(pre = {}) {
    const d = $("#dialog"), products = ((S().pipeline || {}).products || []);
    d.innerHTML = `<form method="dialog"><h2>Put it to the council</h2>
      <p class="dim">A worker runs the marketing-council skill on the question: each seat argues its case from your records, then one
      recommendation with the dissent. The outcome is recorded as a decision. It recommends only; spending and launching still need your approval.</p>
      <label>The call to make<textarea class="field" name="question" required minlength="8" placeholder="Retest the brush with the new statics, or drop it and move to the next product?">${esc(pre.question || "")}</textarea></label>
      <div class="row"><label>Kind <select class="field" name="kind">${[["product", "Product pick"], ["kill", "Kill or continue"], ["scale", "Scale"], ["other", "Other"]]
        .map(([v, l]) => `<option value="${v}"${pre.kind === v ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Product <select class="field" name="product"><option value="">None</option>${products.map((p) => `<option value="${esc(p.slug)}"${pre.product === p.slug ? " selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
      <label>Model <select class="field" name="model">${modelOptions("")}</select></label></div>
      <div class="row"><span class="grow"></span><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn primary" value="go">Start the debate</button></div></form>`;
    d.onclose = async () => {
      if (d.returnValue !== "go") return;
      const f = new FormData($("form", d));
      const r = await App.run("/api/debate", Object.fromEntries(f), (x) => `Debate started: ${x.task} on ${x.worker}`);
      if (r) App.go(`/work/${r.worker}`);
    };
    d.returnValue = ""; d.showModal();
  }
  const modelOptions = App.modelOptions = (current, first) => {
    const def = first || `Default (${(S().work || {}).worker_model || "opus"})`;
    return [["", def], ["haiku", "Haiku"], ["sonnet", "Sonnet"], ["opus", "Opus"], ["claude-fable-5-2", "Fable 5.2 (your call only)"]]
      .map(([v, l]) => `<option value="${v}"${current === v ? " selected" : ""}>${l}</option>`).join("");
  };

  // ---------------------------------------------------------------- the record drawer
  const kv = (rows) => `<table>${rows.filter((r) => r[1] != null && r[1] !== "").map(([k, v]) => `<tr><td class="dim" style="width:110px">${esc(k)}</td><td>${v}</td></tr>`).join("")}</table>`;
  const sec = (title, html) => html ? `<div class="sec">${title ? `<h3>${esc(title)}</h3>` : ""}${html}</div>` : "";
  const taskLine = (t) => `<div class="row" style="flex-wrap:nowrap">${ref(`T${t.id}`)}<span class="clip grow">${esc(t.title)}</span>${t.agent ? `<span class="dim">${esc(t.agent)}</span>` : ""}${chip(t.status)}${
    t.deliverable ? `<a class="ref" data-file="${esc(t.deliverable)}">file</a>` : ""}</div>`;
  const fileLink = App.fileLink = (path) => path ? `<a class="ref" data-file="${esc(path)}" title="${esc(path)}">${esc(path.split("/").pop())}</a>` : "";
  App.record = async (id) => {
    const d = $("#record"), body = $("#recordBody");
    d.hidden = false; $("#recordTitle").textContent = id; body.innerHTML = `<div class="empty">Loading ${esc(id)}</div>`;
    let r;
    try { r = await App.get(`/api/record?id=${encodeURIComponent(id)}`); }
    catch (e) { body.innerHTML = `<div class="empty">There is no record ${esc(id)} in the brain.</div>`; return; }
    $("#recordTitle").textContent = `${id}  ${r.title || ""}`;
    const w = r.row || {};
    let h = "";
    if (r.kind === "memory") {
      const m = r.meta;
      h = sec("", kv([["Category", esc(m.category)], ["From", esc(m.provenance)], ["Status", chip(m.status === "active" ? "done" : "pending", m.status)], ["Product", esc(m.product)],
        ["Source", link(String(m.source || ""))], ["Updated", esc(String(m.updated || "").slice(0, 16).replace("T", " "))]])) + sec("", `<div class="md">${link(r.body)}</div>`);
    } else if (r.kind === "task") {
      h = sec("", kv([["Status", chip(w.status)], ["Agent", esc(w.agent)], ["Product", esc(w.product)], ["Parent", w.parent_id ? ref(`T${w.parent_id}`) : ""],
        ["Deliverable", fileLink(w.deliverable)], ["Updated", esc(String(w.updated || "").slice(0, 16).replace("T", " "))]]))
        + sec("What", w.description ? `<div class="md">${link(w.description)}</div>` : "") + sec("Result", w.summary ? `<div class="md">${link(w.summary)}</div>` : "")
        + sec(`Plan, ${F.plural(r.subtasks.length, "step")}`, r.subtasks.map(taskLine).join(""))
        + sec("History", r.events.map((e) => `<div><span class="faint num">${esc(e.ts.slice(5, 16).replace("T", " "))}</span> ${esc(e.kind)} ${link(e.note || "")}</div>`).join(""))
        + sec("", `<a class="btn sm" href="/work/${esc(id)}">Open in Work</a> <button class="btn sm" data-act="say" data-text="Where is ${esc(id)} up to, and what is the next step?">Ask JARVIS</button>`);
    } else if (r.kind === "decision") {
      h = sec("", `<div class="md">${link(w.decision)}</div>`) + sec("Why", w.rationale ? `<div class="md">${link(w.rationale)}</div>` : "")
        + sec("", kv([["Decided by", esc(w.decided_by)], ["When", esc(String(w.ts).slice(0, 16).replace("T", " "))], ["Task", w.task_id ? ref(`T${w.task_id}`) : ""], ["Product", esc(w.product)]]));
    } else if (r.kind === "approval") {
      h = sec("", `<div class="md">${link(w.summary)}</div>`) + sec("", kv([["Gate", esc(GATES[w.kind] || w.kind)], ["Status", chip(w.status)],
        ["Amount", w.amount != null ? esc(`${w.currency || "AUD"} ${F.num(w.amount, 2)}`) : ""], ["Task", w.task_id ? ref(`T${w.task_id}`) : ""],
        ["Asked", esc(String(w.ts).slice(0, 16).replace("T", " "))], ["Decided", esc(String(w.decided_ts || "").slice(0, 16).replace("T", " "))], ["Note", esc(w.decision_note)]]))
        + sec("Detail", w.detail ? `<div class="md">${link(w.detail)}</div>` : "")
        + (w.status === "pending" ? sec("", `<button class="btn ok" data-act="approve" data-id="${w.id}">Approve A${w.id}</button> <button class="btn no" data-act="reject" data-id="${w.id}">Reject</button>`) : "");
    } else if (r.kind === "test") {
      h = sec("", kv([["Product", esc(w.product)], ["Status", chip(w.status)], ["Budget", esc(F.usd(w.budget_usd, 0))], ["Days planned", esc(w.planned_days)],
        ["Started", esc(String(w.started || "").slice(0, 16).replace("T", " "))], ["Ended", esc(String(w.ended || "").slice(0, 16).replace("T", " "))]]))
        + sec("Hypothesis", `<div class="md">${link(w.hypothesis || "")}</div>`) + sec("Angle", w.angle ? `<div class="md">${link(w.angle)}</div>` : "")
        + sec("Success and kill criteria", w.criteria ? `<div class="md">${link(w.criteria)}</div>` : "") + sec("Verdict", w.verdict ? `<div class="md">${link(w.verdict)}</div>` : "")
        + sec("Lesson", w.lesson ? `<div class="md">${link(w.lesson)}</div>` : "");
    } else if (r.kind === "product") {
      h = sec("", kv([["Stage on record", esc(w.stage)], ["Notes", link(w.notes || "")], ["Memory entries", esc(r.memory)]])) + sec("Tasks", r.tasks.map(taskLine).join(""));
    } else if (r.kind === "agent") {
      h = sec("", `<div class="md">${esc(w.role || "")}</div>`) + sec("", kv([["Model", esc(w.model)], ["Finished tasks", esc(r.done)]])) + sec("Open tasks", r.tasks.map(taskLine).join(""))
        + sec("", `<a class="btn sm" href="/team/${esc(w.name)}">Open on the team page</a>`);
    } else if (r.kind === "category" || r.kind === "file") {
      h = (r.path ? sec("", fileLink(r.path)) : "") + sec("Tasks", (r.tasks || []).map(taskLine).join(""))
        + sec("Entries", (r.recent || []).map((m) => `<div>${ref(m.id)} ${esc(m.title)}</div>`).join(""));
    }
    body.innerHTML = h || `<div class="empty">Nothing more is recorded for ${esc(id)}.</div>`;
  };
  App.file = async (path) => {
    const d = $("#record"), body = $("#recordBody");
    d.hidden = false; $("#recordTitle").textContent = path.split("/").pop();
    const url = `/api/file?path=${encodeURIComponent(path)}`;
    const r = await fetch(url);
    if (r.status !== 200) { body.innerHTML = `<div class="empty">${esc(path)} can't be previewed here (moved, too large, or not a text or image file).</div>`; return; }
    body.innerHTML = (r.headers.get("Content-Type") || "").startsWith("image/")
      ? `<div class="sec"><img src="${url}" alt="" style="max-width:100%"></div>`
      : `<div class="sec"><div class="faint" style="margin-bottom:8px">${esc(path)}</div><div class="md">${link(await r.text())}</div></div>`;
  };

  // ---------------------------------------------------------------- the talk panel
  const log = $("#talkLog"), input = $("#talkInput");
  let cur = null, steps = null, pending = null, images = [], files = [], seeded = false;
  const nearEnd = () => log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  const toEnd = (force) => { if (force || nearEnd()) log.scrollTop = log.scrollHeight; };
  function add(cls, html) {
    const stick = nearEnd();
    const el = document.createElement("div");
    el.className = `msg ${cls}`; el.innerHTML = html || "";
    pending && pending.isConnected ? pending.before(el) : log.append(el);
    if (stick) toEnd(true);
    return el;
  }
  const clean = (t) => t.replace(/\[\[[^\]]*\]\]/g, "").replace(/\[\[[^\]]*$/, "");
  function para() { if (!cur) { cur = add("jarvis"); cur._raw = ""; steps = null; } return cur; }
  function step(cls, text, id) {
    if (!steps || !steps.isConnected) { steps = add("steps"); steps.classList.remove("msg"); cur = null; }
    const el = document.createElement("div");
    el.className = cls; el.textContent = text; if (id) el.dataset.id = id;
    steps.append(el); toEnd();
    return el;
  }
  function finish() {
    cur = null; steps = null;
    pending?.remove(); pending = null;
    $$(".steps .run", log).forEach((el) => el.classList.replace("run", "ok"));
  }
  function openTalk() {
    const app = $("#app");
    if (matchMedia("(max-width: 1080px)").matches) app.classList.add("talk-open");
    else { app.classList.remove("talk-off"); J.pref("talk", "1"); }
  }
  App.openTalk = openTalk;
  $("#talkBtn").onclick = () => {
    const app = $("#app");
    if (matchMedia("(max-width: 1080px)").matches) app.classList.toggle("talk-open");
    else J.pref("talk", app.classList.toggle("talk-off") ? "0" : "1");
  };
  $("#talkClose").onclick = () => $("#app").classList.remove("talk-open");
  if (J.pref("talk") === "0") $("#app").classList.add("talk-off");

  const WORDS = { idle: "Standing by", thinking: "Thinking", delegating: "Handing out work", speaking: "Speaking", listening: "Listening" };
  function talkState() {
    const s = J.shown();
    $("#talkStateText").textContent = WORDS[s] || s;
    $("#talkState .dot").className = `dot ${s === "idle" ? (J.connected ? "on" : "bad") : "run"}`;
    $("#talkStop").hidden = s === "idle";
    $("#talkMic").classList.toggle("on", J.listening);
    $("#mark").className = `mark ${J.connected ? "live" : ""} ${s === "idle" ? "" : "busy"}`;
  }
  J.on("status", (e) => {
    if (e.status !== "idle" && !pending) { pending = document.createElement("div"); pending.className = "msg jarvis thinking"; log.append(pending); toEnd(true); }
    if (e.status === "idle") finish();
    talkState();
  });
  J.on("owner", (e) => {
    finish();
    add("owner", esc(e.text || "") + (e.images || []).map((i) => i.url ? `<img src="${i.url}" alt="">` : "").join("")
      + ((e.files || []).length ? `<div class="faint">${e.files.map((f) => esc(f.name)).join(", ")}</div>` : ""));
    toEnd(true);
  });
  J.on("say_delta", (e) => { const p = para(); p._raw += e.text; p.innerHTML = link(clean(p._raw)); toEnd(); });
  J.on("say", (e) => { const p = para(); p.innerHTML = e.slash ? `<pre>${esc(e.text)}</pre>` : link(e.text); cur = null; toEnd(); });
  J.on("error", (e) => { add("err", esc(e.text)); if (e.mic) toast(e.text, "err"); });
  J.on("queued", (e) => add("note", `Queued behind the current reply (${e.waiting} waiting)`));
  J.on("jarvis_tool", (e) => step("run", e.summary, e.id));
  J.on("jarvis_tool_done", (e) => { const el = e.id && $(`.steps [data-id="${CSS.escape(e.id)}"]`, log); if (el) el.className = e.ok ? "ok" : "fail"; });
  J.on("dispatch", (e) => step("run agent-line", `${e.agent}: ${e.description || "a job"}`, e.id).dataset.agent = e.agent);
  J.on("agent_activity", (e) => { const el = $$(`.steps [data-agent="${CSS.escape(e.agent)}"]`, log).pop(); if (el) el.title = e.line; });
  J.on("agent_done", (e) => { const el = e.id && $(`.steps [data-id="${CSS.escape(e.id)}"]`, log); if (el) el.classList.replace("run", "ok"); });
  J.on("permission", (e) => { toast(e.question ? "JARVIS has a question for you" : `Waiting for you: ${e.summary}`, "ask", 9000); fresh(); });
  J.on("permission_done", fresh);
  J.on("notice", (e) => toast(e.text));
  J.on("reset", () => { log.innerHTML = ""; finish(); add("note", "New conversation"); });
  J.on("session", () => { log.innerHTML = ""; finish(); add("note", "Switched conversation"); });
  J.on("ears", talkState);
  J.on("awake", () => { openTalk(); talkState(); });
  J.on("interim", (e) => { input.placeholder = e.text || "Message JARVIS. Paste or drop an image."; });
  J.on("connection", () => { talkState(); strip(); });

  function chips() {
    $("#talkChips").innerHTML = images.map((i, n) => `<span><img src="${i.url}" alt="">image ${n + 1}<button type="button" data-drop="i${n}" aria-label="Remove">x</button></span>`).join("")
      + files.map((f, n) => `<span>${esc(f.name)}<button type="button" data-drop="f${n}" aria-label="Remove">x</button></span>`).join("");
  }
  $("#talkChips").onclick = (e) => {
    const k = e.target.dataset.drop; if (!k) return;
    (k[0] === "i" ? images : files).splice(+k.slice(1), 1); chips();
  };
  async function take(list) {
    for (const f of list) {
      try {
        if (/^image\/(png|jpeg|gif|webp)$/.test(f.type) && images.length < 4) images.push(await J.readImage(f));
        else if (f.size > 15 * 1024 * 1024) throw new Error(`${f.name} is over 15 MB.`);
        else if (files.length >= 4) throw new Error("At most 4 files per message.");
        else files.push({ name: f.name, data: await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",")[1]); r.onerror = () => rej(new Error(`${f.name} couldn't be read.`)); r.readAsDataURL(f); }) });
      } catch (e) { toast(e.message, "err"); }
    }
    chips();
  }
  input.addEventListener("paste", (e) => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); take(fs); } });
  const talk = $("#talk");
  talk.addEventListener("dragover", (e) => { e.preventDefault(); talk.classList.add("drop"); });
  talk.addEventListener("dragleave", () => talk.classList.remove("drop"));
  talk.addEventListener("drop", (e) => { e.preventDefault(); talk.classList.remove("drop"); take([...e.dataTransfer.files]); });
  $("#talkAttach").onclick = () => $("#talkFile").click();
  $("#talkFile").onchange = (e) => { take([...e.target.files]); e.target.value = ""; };
  const grow = () => { input.style.height = "auto"; input.style.height = Math.min(160, input.scrollHeight + 2) + "px"; };
  input.addEventListener("input", grow);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#talkForm").requestSubmit(); } });
  $("#talkForm").onsubmit = (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text && !images.length && !files.length) return;
    J.send(text, images, files);
    input.value = ""; images = []; files = []; chips(); grow();
  };
  $("#talkMic").onclick = () => J.listen();
  $("#talkStop").onclick = () => { J.stop(); finish(); talkState(); };
  $("#talkNew").onclick = () => J.newConversation();
  const speakBtn = $("#talkSpeak");
  const speakLabel = () => { speakBtn.textContent = J.settings.speak ? "Voice on" : "Voice off"; };
  speakBtn.onclick = () => { J.settings.speak = !J.settings.speak; speakLabel(); };
  speakLabel();

  // ---------------------------------------------------------------- agents at work (used by Testing and Team)
  // The list of who is working, and the log of what they just did. The log only ever appends, so a new line can be
  // seen arriving; an agent's row lights for a moment when one of its lines lands. Nothing moves otherwise.
  App.agentsHTML = (agents) => agents.length ? agents.map((a) => {
    const p = a.progress;
    return `<div class="agent ${a.running ? "run" : ""}" data-key="${esc(a.key)}"><span class="dot ${a.running ? "run" : a.status === "done" ? "on" : "bad"}"></span>
      <span class="who">${link(a.label.replace(" · ", " "))}</span><span class="doing">${link(a.doing || a.title || "")}</span>
      <span class="meta">${[a.task ? ref(a.task) : "", p ? `${p.done} of ${p.total} ${p.source === "plan" ? "plan steps" : "subtasks"}` : "", a.actions ? F.plural(a.actions, "action") : "",
        a.last ? F.ago(a.last) : "", a.running ? "" : esc(a.status)].filter(Boolean).join(", ")}${p ? bar(p.done / p.total, "good") : ""}</span></div>`;
  }).join("") : `<div class="empty">No agent is working right now. Give JARVIS a job in the talk panel, or start a worker on the Work page.</div>`;
  const feedRow = (f, cls = "") => `<div class="${cls}"><time>${esc(F.hhmm(f.at))}</time><span><b>${esc(f.who)}</b> ${link(f.text)}</span></div>`;
  App.feed = (el, feed, max = 40, scope = document) => {
    const stick = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    const seen = el._t || 0, arrived = feed.filter((f) => f.t > seen);
    if (!el._t) el.innerHTML = feed.length ? feed.slice(-max).map((f) => feedRow(f)).join("") : `<div class="empty" style="display:block">No actions in the last 15 minutes.</div>`;
    else if (arrived.length) {
      $(".empty", el)?.remove();
      el.insertAdjacentHTML("beforeend", arrived.map((f) => feedRow(f, "new")).join(""));
      while (el.children.length > max) el.firstElementChild.remove();
    }
    if (feed.length) el._t = feed[feed.length - 1].t;
    if (stick) el.scrollTop = el.scrollHeight;
    if (seen) new Set(arrived.map((f) => f.agent)).forEach((key) => {
      scope.querySelectorAll(`[data-key="${CSS.escape(key)}"]`).forEach((row) => { row.classList.add("hit"); setTimeout(() => row.classList.remove("hit"), 900); });
    });
  };

  // ---------------------------------------------------------------- state in, pages out
  J.on("state", () => {
    if (!seeded && S().brain) {   // the last few lines of the conversation, so the panel isn't blank after a reload
      seeded = true;
      const short = (t) => { t = t.replace(/^\(Sent from the owner's phone[^)]*\)\s*/, ""); return t.length > 260 ? t.slice(0, 260).trimEnd() + "…" : t; };
      const lines = S().brain.journal.filter((j) => j.role === "owner" || j.role === "jarvis").sort((a, b) => a.ts < b.ts ? -1 : 1).slice(-6);
      if (lines.length && !log.children.length) {
        add("note", "Earlier, from the journal");
        lines.forEach((j) => add(j.role === "owner" ? "owner" : "jarvis", link(short(j.text))));
        toEnd(true);
      }
    }
    strip(); draw(); talkState();
    if (opening && App.pages[App.page]) { opening = false; App.pages[App.page].open?.(App.arg, $(`#page-${App.page}`)); }
  });
  document.addEventListener("DOMContentLoaded", () => App.go(location.pathname, false));
})();
