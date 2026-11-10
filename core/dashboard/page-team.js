// Team: the org chart with JARVIS as the boss (T109, D73), the six departments under him, and what each agent is
// doing this minute. A line down to a department runs only while someone in it is working; an agent's box lights
// when one of its actions lands. Click a box for its panel: current job, open tasks, skills, model, and an ask box
// that goes to that specialist through JARVIS. Data: /api/teammap (who they are; fetched when the page opens) and
// the live section (what they are doing). /team/<agent> opens that agent.
(function () {
  const { F, link, ref, chip, block } = App, esc = J.esc;
  const $ = (s, el = document) => el.querySelector(s);
  let map = null, sel = "jarvis", skills = {}, pulling = false, pulled = 0;

  async function pull(force) {
    if (pulling || (!force && Date.now() - pulled < 15000)) return;
    pulling = true;
    try { map = await App.get("/api/teammap"); pulled = Date.now(); App.draw(); }
    catch (e) { App.toast(e.message, "err"); }
    finally { pulling = false; }
  }
  async function pullSkills(name) {
    if (skills[name] || name === "jarvis") return;
    skills[name] = { loading: true };
    try { skills[name] = (await App.get(`/api/skills?agent=${encodeURIComponent(name)}`)).agents[0] || {}; } catch (e) { skills[name] = {}; }
    App.draw();
  }

  // What an agent is doing: live work first, then its next open task, then the last thing it finished.
  function now(name, live) {
    const mine = live.agents.filter((a) => a.name === name && a.running);
    const a = map.agents[name];
    if (mine.length) return { state: "run", line: mine[0].doing || mine[0].title, keys: mine.map((m) => m.key), mine };
    const waiting = a.open.find((t) => t.status === "blocked" || t.status === "waiting_approval");
    if (waiting) return { state: "wait", line: `${waiting.id} ${waiting.status.replace("_", " ")}: ${waiting.title}`, keys: [], mine };
    const going = a.open.find((t) => t.status === "in_progress");
    if (going) return { state: "", line: `${going.id} in progress: ${going.title}`, keys: [], mine };
    if (a.open.length) return { state: "", line: `Next: ${a.open[0].id} ${a.open[0].title}`, keys: [], mine };
    return { state: "", line: a.last_done ? `Last finished ${a.last_done.id}, ${F.ago(a.last_done.completed)}` : "No tasks yet", keys: [], mine };
  }

  function chartHTML(S) {
    const live = S.live, j = live.jarvis;
    const loose = live.agents.filter((a) => a.running && !map.agents[a.name]);   // general-purpose workers answer to JARVIS
    const boss = `<button class="node boss ${j.status !== "idle" || loose.length ? "run" : ""} ${sel === "jarvis" ? "sel" : ""}" data-act="team-pick" data-name="jarvis" ${loose.map((a) => `data-key="${esc(a.key)}"`)[0] || ""}>
      <span class="nm"><span class="dot ${j.status !== "idle" ? "run" : "on"}"></span>JARVIS</span>
      <span class="ln">${esc(j.status === "thinking" ? `Thinking: ${j.request}` : j.status === "delegating" ? "Handing out work" : loose.length ? `${F.plural(loose.length, "worker")} out: ${loose[0].doing || loose[0].title}` : "Standing by")}</span>
      <span class="mt">${F.plural(map.jarvis.open_projects, "open project")}, ${map.jarvis.approvals_pending} waiting for you</span></button>`;
    const depts = map.departments.map((d) => {
      let any = false;
      const nodes = d.agents.map((name) => {
        const a = map.agents[name], n = now(name, live);
        if (n.state === "run") any = true;
        return `<button class="node ${n.state} ${sel === name ? "sel" : ""}" data-act="team-pick" data-name="${esc(name)}" ${n.keys.length ? `data-key="${esc(n.keys[0])}"` : ""}>
          <span class="nm"><span class="dot ${n.state}"></span><span class="clip">${esc(a.label)}</span></span>
          <span class="ln">${esc(n.line)}</span>
          <span class="mt">${a.model === "inherit" ? "JARVIS's model" : esc(a.model)}, ${F.plural(a.skills.length + a.playbooks.length, "skill")}${a.open.length ? `, ${a.open.length} open` : ""}</span></button>`;
      }).join("");
      return `<div class="dept ${any ? "run" : ""}"><h3 title="${esc(d.job)}">${esc(d.name)}</h3>${nodes}</div>`;
    }).join("");
    return `<div class="org"><div class="org-top">${boss}</div><div class="depts">${depts}</div></div>`;
  }

  const tasksHTML = (ts) => ts.map((t) => `<div class="row" style="flex-wrap:nowrap;padding:2px 0">${ref(t.id)}<span class="clip grow" title="${esc(t.title)}">${esc(t.title)}</span>${chip(t.status)}</div>`).join("");
  function panelHTML(S) {
    if (sel === "jarvis") {
      const out = S.live.agents.filter((a) => a.running);
      return `<h2>JARVIS <small>the boss</small></h2><div class="body stack" style="gap:10px">
        <p class="dim">${esc(map.jarvis.role)}</p>
        <div><b>${F.plural(out.length, "agent")} at work</b>${out.map((a) => `<div class="dim">${link(a.label)}: ${link(a.doing || a.title || "")}</div>`).join("") || `<div class="dim">Nobody is out on a job.</div>`}</div>
        <div class="row"><a class="btn sm" href="/work">Open projects (${map.jarvis.open_projects})</a><span class="dim">${map.memory.total} memory entries</span></div>
        <div><b>Hand-offs this week</b>${map.handoffs.slice(-6).reverse().map((h) => `<div class="dim">${esc(h.from)} to ${esc(h.to)}: ${ref(h.task)} <span class="faint">${F.ago(h.at)}</span></div>`).join("") || `<div class="dim">None recorded.</div>`}</div>
        <form class="stack" style="gap:6px" onsubmit="return false"><textarea class="field" name="q" placeholder="Ask JARVIS anything about the team" aria-label="Ask JARVIS"></textarea>
        <div><button class="btn primary sm" data-act="team-ask" type="button">Ask JARVIS</button></div></form></div>`;
    }
    const a = map.agents[sel], n = now(sel, S.live), sk = skills[sel] || {};
    const levels = Object.fromEntries((sk.skills || []).map((s) => [s.name, s]));
    const skill = (name) => { const l = levels[name]; return `<span class="chip" title="${l ? `${l.label}, used ${l.uses} times` : ""}">${esc(name)}${l ? ` <b class="num ice">L${l.level}</b>` : ""}</span>`; };
    return `<h2>${esc(a.label)} <small>${esc(sel)}</small></h2><div class="body stack" style="gap:10px">
      <p class="dim">${esc(a.description)}</p>
      <div><b>Now</b><div class="${n.state === "run" ? "good" : "dim"}">${link(n.line)}</div>${n.mine.map((m) => `<div class="faint">${link(m.label)}${m.progress ? `, ${m.progress.done} of ${m.progress.total} steps` : ""}, ${F.plural(m.actions || 0, "action")}</div>${
        m.made && m.made.url ? `<img src="${esc(m.made.url)}" alt="" style="max-width:100%;border-radius:4px;margin-top:4px">` : ""}`).join("")}</div>
      ${a.open.length ? `<div><b>Open tasks</b>${tasksHTML(a.open)}</div>` : ""}
      ${a.last_done ? `<div><b>Last finished</b><div class="dim">${ref(a.last_done.id)} ${esc(a.last_done.title)} ${App.fileLink(a.last_done.deliverable)}</div></div>` : ""}
      <div><b>Skills</b><div class="row" style="gap:4px;margin-top:4px">${[...a.skills, ...a.playbooks].map(skill).join("") || `<span class="dim">None listed in its file.</span>`}</div>
        <div class="faint" style="margin-top:4px">${a.playbook_notes} mentor notes filed for it${sk.level ? `, average level ${sk.level}` : ""}. Tools: ${esc(a.connections.join(", ") || "none")}.</div></div>
      <label class="row dim">Model <select class="field" data-agent-model="${esc(sel)}">${["inherit", "haiku", "sonnet", "opus", "fable"].map((m) => `<option value="${m}"${a.model === m ? " selected" : ""}>${m === "inherit" ? "JARVIS's model" : m === "fable" ? "fable (your call only)" : m}</option>`).join("")}</select></label>
      <form class="stack" style="gap:6px" onsubmit="return false"><textarea class="field" name="q" placeholder="Ask ${esc(a.label)} something" aria-label="Ask this agent"></textarea>
        <div class="row"><button class="btn primary sm" data-act="team-ask" type="button">Ask this agent</button><span class="faint">Goes through JARVIS; the answer arrives in the talk panel.</span></div></form>
      ${a.sop.map((s) => `<details><summary>${esc(s.title)}</summary><div class="md dim" style="margin-top:6px">${link(s.text || s.body || "")}</div></details>`).join("")}</div>`;
  }

  Object.assign(App.acts, {
    "team-pick": (el) => { sel = el.dataset.name; history.replaceState(null, "", sel === "jarvis" ? "/team" : `/team/${sel}`); pullSkills(sel); App.draw();
      document.getElementById("tmPanel")?.scrollIntoView({ block: "nearest", behavior: "smooth" }); },
    "team-ask": (el) => {
      const box = el.closest("form").elements.q, q = box.value.trim();
      if (!q) return App.toast("Type the question first.", "err");
      App.say(sel === "jarvis" ? q : `Ask the ${sel} agent, from the team page: ${q}`);
      box.value = "";
    },
  });

  App.register("team", {
    title: "Team",
    render(el, S) {
      if (!el._built) {
        el._built = true;
        el.innerHTML = `<header><h1>Team</h1><p id="tmSum"></p></header>
          <div class="stack">
            <section class="panel"><h2>Who reports to whom <small>a line runs while that department is working</small></h2><div id="tmChart"><div class="empty">Loading the team</div></div></section>
            <div class="cols even"><section class="panel" id="tmPanel"></section>
            <section class="panel"><h2>Activity <small>every action, as it happens</small></h2><div id="tmAgents"></div><div class="feed" id="tmFeed" style="border-top:1px solid var(--line)"></div></section></div>
          </div>`;
        el.addEventListener("change", (e) => { const a = e.target.dataset.agentModel; if (a) App.run("/api/agent-model", { agent: a, model: e.target.value }, `${a} now runs on ${e.target.value === "inherit" ? "JARVIS's model" : e.target.value}`).then(() => pull(true)); });
      }
      pull();
      const live = S.live;
      $("#tmSum", el).textContent = live.count ? `${F.plural(live.count, "agent")} working` : "Nobody is working right now";
      if (map) {
        if (!map.agents[sel]) sel = "jarvis";
        block($("#tmChart", el), chartHTML(S));
        // The ask box is being typed in: leave the panel alone until the owner is done.
        if (!$("#tmPanel textarea:focus", el) && !$("#tmPanel select:focus", el)) block($("#tmPanel", el), panelHTML(S));
      }
      block($("#tmAgents", el), App.agentsHTML(live.agents));
      App.feed($("#tmFeed", el), live.feed, 60, el);
    },
    open(arg) { if (arg) { sel = arg; pullSkills(arg); App.draw(); } },
  });
})();
