// Work: what waits for the owner, the plan of every open project (steps, who, status, deliverables), the durable
// workers with the model each runs on, and the desktop jobs. Data: sections inbox and work.
// /work/T110 opens that plan; /work/W36 opens that worker's log.
(function () {
  const { F, link, ref, chip, bar, block } = App, esc = J.esc;
  const $ = (s, el = document) => el.querySelector(s);
  const open = new Set();   // the plans the owner has opened, so a state update doesn't fold them
  let logTimer = null;

  const taskModels = (cur) => [["", "Agent's own"], ["haiku", "Haiku"], ["sonnet", "Sonnet"], ["opus", "Opus"], ["fable", "Fable (your call only)"]]
    .map(([v, l]) => `<option value="${v}"${cur === v ? " selected" : ""}>${l}</option>`).join("");

  function planHTML(p) {
    const sub = p.subtasks, running = p.workers.filter((w) => w.status === "running");
    const steps = p.steps.length ? `<div class="scroll-x"><table class="tight"><thead><tr><th>Step</th><th></th><th>Agent</th><th>Status</th><th>Model</th><th>Deliverable</th></tr></thead><tbody>${
      p.steps.map((s) => `<tr><td>${ref(s.id)}</td><td>${esc(s.title)}${s.summary ? `<div class="faint">${link(s.summary)}</div>` : ""}</td><td class="dim">${esc(s.agent || "JARVIS")}</td>
        <td>${s.worker ? `<a class="chip run" href="/work/${s.worker}"><span class="dot run"></span>${s.worker}</a>` : chip(s.status)}</td>
        <td class="dim">${esc(s.model || "")}</td><td>${App.fileLink(s.deliverable)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty">No steps yet: JARVIS hasn't broken this down into subtasks.</div>`;
    return `<details class="plan" data-id="${p.id}" ${open.has(p.id) ? "open" : ""} id="plan-${p.id}">
      <summary class="row" style="padding:9px 12px;flex-wrap:nowrap;color:var(--ink)">${ref(p.id)}<span class="clip grow">${esc(p.title)}</span>
        ${p.product ? `<span class="chip">${esc(p.product)}</span>` : ""}
        ${running.map((w) => `<span class="chip run"><span class="dot run"></span>${w.id}</span>`).join("")}
        ${p.blockers.length ? `<span class="chip wait">${F.plural(p.blockers.length, "blocker")}</span>` : ""}
        ${p.deadline ? `<span class="chip ${p.days_left < 0 ? "bad" : p.days_left <= 3 ? "wait" : ""}">due ${esc(p.deadline.slice(5))}</span>` : ""}
        <span class="num dim" style="width:44px;text-align:right">${sub.total ? `${sub.done}/${sub.total}` : ""}</span><span style="width:70px">${bar(p.progress, "good")}</span></summary>
      <div style="border-top:1px solid var(--line)">
        ${p.description ? `<div class="body dim" style="padding:8px 12px">${link(p.description)}</div>` : ""}
        ${p.blockers.length ? `<ul class="blockers" style="padding:0 12px 6px">${p.blockers.map((b) => `<li>${link(b)}</li>`).join("")}</ul>` : ""}
        ${steps}
        <div class="row" style="padding:8px 12px;border-top:1px solid var(--line)">
          <button class="btn sm" data-act="say" data-text="Carry on with ${p.id}. Tell me what is blocking it and the next step, and start that step if nothing needs me.">Ask JARVIS to move it on</button>
          <button class="btn sm" data-act="worker-form" data-task="${p.id}">Start a worker on it</button>
          ${p.deliverable ? App.fileLink(p.deliverable) : ""}<span class="grow"></span>
          <label class="dim">Model for this task <select class="field" data-task-model="${p.id}">${taskModels(p.model || "")}</select></label>
        </div></div></details>`;
  }

  function workersHTML(w) {
    if (!w.workers.length) return `<div class="empty">No workers yet. A worker is a job that keeps running when this window or the phone session closes.</div>`;
    return `<div class="scroll-x"><table class="tight"><thead><tr><th>Worker</th><th>Agent</th><th>Model</th><th>Task</th><th>Status</th><th>Doing</th><th></th></tr></thead><tbody>${
      w.workers.map((x) => `<tr data-key="${x.id}"><td><a class="ref" href="/work/${x.id}">${x.id}</a></td><td>${esc(x.agent)}</td>
        <td class="num" title="${x.model_default ? "the default worker model" : "chosen for this worker"}">${esc(x.model)}</td><td>${x.task ? ref(x.task) : ""}</td>
        <td>${x.status === "running" ? `<span class="chip run"><span class="dot run"></span>running</span>` : chip(x.status)}</td>
        <td style="min-width:220px"><div>${link(x.status === "running" ? (x.doing || x.title) : x.title)}</div>
          ${x.progress ? `<div class="row" style="flex-wrap:nowrap"><span style="flex:1">${bar(x.progress.done / x.progress.total, "good")}</span><span class="faint num">${x.progress.done}/${x.progress.total}</span></div>` : ""}
          <div class="faint">started ${F.ago(x.started)}${x.actions ? `, ${F.plural(x.actions, "action")}` : ""}${x.attempts > 1 ? `, attempt ${x.attempts}` : ""}</div></td>
        <td style="white-space:nowrap"><a class="btn sm ghost" href="/work/${x.id}">Log</a>${
          x.status === "running" ? `<button class="btn sm no" data-act="worker-stop" data-id="${x.id}">Stop</button>`
          : x.status === "stopped" ? `<button class="btn sm" data-act="worker-resume" data-id="${x.id}">Resume</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function jobsHTML(w) {
    const head = `<div class="row" style="padding:8px 12px;border-bottom:1px solid var(--line)"><span class="dot ${w.job_runner ? "run" : "bad"}"></span>
      <span>${w.job_runner ? "The job runner is on" : "The job runner is off: jobs wait until its session is started in the Claude desktop app on the PC"}</span></div>
      <form class="row" style="padding:8px 12px;border-bottom:1px solid var(--line);flex-wrap:nowrap" onsubmit="return false">
        <input class="field" name="text" placeholder="A job that needs the screen, for example: open Kalodata and pull this week's top sellers" aria-label="Desktop job">
        <button class="btn" data-act="job-add" type="button">Queue the job</button></form>`;
    return head + (w.jobs.length ? `<ul class="list">${w.jobs.map((j) => `<li class="row" style="flex-wrap:nowrap;align-items:flex-start"><span class="num dim">${j.id}</span>
      <span class="grow" style="min-width:0"><span class="clip" style="display:block" title="${esc(j.text)}">${esc(j.text)}</span>${j.summary ? `<span class="faint">${link(j.summary)}</span>` : ""}</span>
      ${chip(j.status)}${j.status === "queued" ? `<button class="btn sm ghost" data-act="job-cancel" data-id="${j.id}">Cancel</button>` : ""}</li>`).join("")}</ul>`
      : `<div class="empty">No desktop jobs.</div>`);
  }

  async function workerLog(id) {
    clearInterval(logTimer);
    const d = $("#record"), body = $("#recordBody");
    d.hidden = false; $("#recordTitle").textContent = `${id} log`;
    const pull = async () => {
      if (d.hidden || !$("#recordTitle").textContent.startsWith(id)) return clearInterval(logTimer);
      let r;
      try { r = await App.get(`/api/control/worker?id=${encodeURIComponent(id)}&n=160`); }
      catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return clearInterval(logTimer); }
      const stick = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
      block(body, `<div class="sec"><div class="row">${chip(r.status)}<span class="dim">${esc(r.agent || "general-purpose")}</span>${r.task ? ref(r.task) : ""}
          <span class="dim">started ${F.ago(r.started)}</span><span class="grow"></span>${r.status === "running" ? `<button class="btn sm no" data-act="worker-stop" data-id="W${r.id}">Stop W${r.id}</button>`
          : r.status === "stopped" ? `<button class="btn sm" data-act="worker-resume" data-id="W${r.id}">Resume W${r.id}</button>` : ""}</div>
          <details style="margin-top:8px"><summary>Brief</summary><div class="md">${link(r.brief)}</div></details></div>
        <div class="sec"><div class="pre">${r.lines.map((l) => l.startsWith("> ") ? `<span class="faint">${esc(l)}</span>` : link(l)).join("\n") || "Nothing logged yet."}</div></div>`);
      if (stick) body.scrollTop = body.scrollHeight;
      if (r.status !== "running") clearInterval(logTimer);
    };
    await pull();
    logTimer = setInterval(pull, 3000);
  }

  function form(title, fields, submit, onDone) {
    const d = $("#dialog");
    d.innerHTML = `<form method="dialog"><h2>${esc(title)}</h2>${fields}
      <div class="row"><span class="grow"></span><button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn primary" value="go">${esc(submit)}</button></div></form>`;
    d.onclose = () => { if (d.returnValue === "go") onDone(Object.fromEntries(new FormData($("form", d)))); };
    d.returnValue = ""; d.showModal();
  }
  const agentOptions = (none) => `<option value="">${esc(none)}</option>` + ((J.sections.system || {}).agents || []).map((a) => `<option>${esc(a)}</option>`).join("");
  const productOptions = () => `<option value="">None</option>` + ((J.sections.pipeline || {}).products || []).map((p) => `<option value="${esc(p.slug)}">${esc(p.name)}</option>`).join("");

  Object.assign(App.acts, {
    "worker-form": (el) => form("Start a worker", `
        <p class="dim">A worker is its own Claude Code run. It survives restarts and shows up here and on the Team page as it goes.</p>
        <label>Brief<textarea class="field" name="brief" required rows="5" placeholder="What to do, the deliverable and where to save it"></textarea></label>
        <div class="row"><label>Task <input class="field" name="task" value="${esc(el.dataset.task || "")}" placeholder="T110" style="width:90px"></label>
        <label>Agent <select class="field" name="agent">${agentOptions("General purpose")}</select></label>
        <label>Model <select class="field" name="model">${App.modelOptions("")}</select></label></div>`, "Start the worker",
      async (f) => { const r = await App.run("/api/control/worker", { action: "start", ...f }, (x) => `W${x.id} started`); if (r) App.go(`/work/W${r.id}`); }),
    "task-form": () => form("Add a task", `
        <label>What needs doing<input class="field" name="title" required></label>
        <div class="row"><label>Agent <select class="field" name="agent">${agentOptions("JARVIS decides")}</select></label>
        <label>Product <select class="field" name="product">${productOptions()}</select></label>
        <label>Model <select class="field" name="model">${taskModels("")}</select></label></div>`, "Add the task",
      (f) => App.run("/api/task", f, (x) => `T${x.id} added`)),
    "job-add": (el) => { const i = el.closest("form").elements.text; if (i.value.trim()) App.run("/api/control/job", { text: i.value }, (x) => `J${x.id} queued`).then((r) => { if (r) i.value = ""; }); },
  });

  App.register("work", {
    title: "Work",
    render(el, S) {
      if (!el._built) {
        el._built = true;
        el.innerHTML = `<header><h1>Work</h1><p id="wSum"></p><span class="grow"></span>
            <button class="btn" data-act="task-form">Add a task</button><button class="btn" data-act="worker-form">Start a worker</button>
            <button class="btn" data-act="debate">Put a call to the council</button></header>
          <div class="stack">
            <section class="panel"><h2>Waiting for you <small id="wInboxN"></small></h2><div id="wInbox"></div></section>
            <section class="panel"><h2>Plans <small>every open project and its steps</small></h2><div id="wPlans"></div></section>
            <section class="panel"><h2>Workers <small id="wQueue"></small></h2><div id="wWorkers"></div></section>
            <div class="cols even"><section class="panel"><h2>Desktop jobs <small>work that needs the PC's screen</small></h2><div id="wJobs"></div></section>
            <section class="panel"><h2>Recently decided</h2><div id="wDecided"></div></section></div>
          </div>`;
        el.addEventListener("toggle", (e) => { const d = e.target.closest?.("details.plan"); if (d) d.open ? open.add(d.dataset.id) : open.delete(d.dataset.id); }, true);
        el.addEventListener("change", (e) => { const t = e.target.dataset.taskModel; if (t) App.run("/api/task-model", { task: t, model: e.target.value || "inherit" }, `Model for ${t} saved`); });
      }
      const w = S.work, c = w.counts, running = w.workers.filter((x) => x.status === "running").length;
      $("#wSum", el).textContent = `${F.plural(c.open, "open project")}, ${c.blocked} with blockers, ${F.plural(running, "worker")} running`;
      $("#wInboxN", el).textContent = S.inbox.length || "";
      block($("#wInbox", el), App.inboxHTML(S.inbox));
      block($("#wPlans", el), w.plans.length ? w.plans.map(planHTML).join("") : `<div class="empty">No open projects. Add a task, or give JARVIS a goal in the talk panel.</div>`);
      $("#wQueue", el).textContent = w.queue.length ? `${F.plural(w.queue.length, "message")} waiting for JARVIS` : "";
      block($("#wWorkers", el), workersHTML(w));
      block($("#wJobs", el), jobsHTML(w));
      block($("#wDecided", el), w.decided.length ? `<ul class="list">${w.decided.map((a) => `<li class="row" style="flex-wrap:nowrap">${ref(`A${a.id}`)}<span class="clip grow">${esc(a.summary)}</span>${chip(a.status)}<span class="faint">${F.ago(a.decided_ts)}</span></li>`).join("")}</ul>`
        : `<div class="empty">No approvals decided yet.</div>`);
    },
    open(arg, el) {
      if (/^W\d+$/i.test(arg)) return workerLog(arg.toUpperCase());
      if (!/^T\d+$/i.test(arg)) return;
      const id = arg.toUpperCase(), plan = el.querySelector(`#plan-${id}`);
      if (!plan) return App.record(id);   // a step, or a finished project: show the record
      open.add(id); plan.open = true; plan.scrollIntoView({ block: "start" });
    },
  });
})();
