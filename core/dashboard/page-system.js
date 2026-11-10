// System: is everything that runs the business while the owner is away actually running? The unattended routines,
// the connections (Claude Code, voice, "Hey JARVIS", the phone, the job runner, the connectors), Claude capacity,
// the conversation's model and sessions, the voice settings, the PC's screen, and the latest consequential actions.
// Data: sections system and capacity; sessions and the screen are fetched only when asked for.
(function () {
  const { F, link, chip, bar, block } = App, esc = J.esc;
  const $ = (s, el = document) => el.querySelector(s);
  let sessions = null, screenOn = false;

  const line = (ok, name, text, extra = "") => `<li class="row" style="flex-wrap:nowrap"><span class="dot ${ok === true ? "on" : ok === "run" ? "run" : ok === null ? "" : "bad"}"></span>
    <span style="width:132px;flex:none">${esc(name)}</span><span class="dim grow">${text}</span>${extra}</li>`;

  function connectionsHTML(s) {
    const w = s.wake || {}, t = s.tts || {}, tg = s.telegram || {}, c = s.connectors || {}, cv = s.conversation;
    const conn = c.servers ? c.servers.map((x) => `<span class="chip ${x.ok ? "done" : "bad"}" title="${esc(x.status)}">${esc(x.name)}</span>`).join(" ")
      : c.running ? "Checking" : "Not checked since the dashboard started";
    return `<ul class="list">
      ${line(s.login_needed ? false : s.claude, "Claude Code", s.login_needed ? "Signed out on this PC: run claude in a terminal and sign in" : s.claude ? (cv.warm ? "Running and warm: a reply starts without a cold start" : "Installed; it starts with the next message") : "Not found on this PC")}
      ${line(t.available, "Voice", t.available ? `Kokoro, ${esc(t.voice)}` : esc(t.error || "Kokoro isn't installed, so the browser's voice is used"))}
      ${line(w.enabled ? (w.on ? (w.ready ? true : "run") : false) : null, "Hey JARVIS", w.enabled ? (w.on ? (w.ready ? "Listening for the wake word" : "Starting") : esc(w.error || "Switched on, but the listener isn't running")) : "Switched off",
        `<button class="btn sm" data-act="wake-toggle">${w.enabled ? "Switch off" : "Switch on"}</button>`)}
      ${line(tg.running ? true : tg.configured ? false : null, "Phone (Telegram)", tg.running ? "Connected: messages, voice notes and approvals reach your phone" : tg.configured ? esc(tg.error || "Set up, but the bridge isn't running") : "Not set up")}
      ${line(s.job_runner, "Job runner", s.job_runner ? "On: desktop jobs run as they arrive" : "Off: desktop jobs wait until its session is started on the PC")}
      ${line(c.servers ? c.servers.every((x) => x.ok) : null, "Connectors", conn + (c.checked ? ` <span class="faint">${F.ago(c.checked)}</span>` : ""), `<button class="btn sm" data-act="connectors" ${c.running ? "disabled" : ""}>Check now</button>`)}
    </ul>`;
  }

  function routinesHTML(r) {
    const alive = r.minutes_since_tick != null && r.minutes_since_tick <= 20;
    return `<div class="row" style="padding:8px 12px;border-bottom:1px solid var(--line)"><span class="dot ${alive ? "run" : "bad"}"></span>
      <span>${alive ? `The 15-minute tick is alive (last ${r.minutes_since_tick} min ago)` : `The tick hasn't run for ${r.minutes_since_tick ?? "?"} minutes: check the "JARVIS routines" scheduled task`}</span></div>
      <div class="scroll-x"><table class="tight"><thead><tr><th>Routine</th><th>Schedule</th><th>Last run</th><th>Next</th><th>Result</th></tr></thead><tbody>${
      r.routines.map((x) => `<tr><td><b>${esc(x.name)}</b><div class="faint">${esc(x.what)}</div></td><td class="dim">${esc(x.schedule)}</td>
        <td class="num dim">${x.last_run ? esc(x.last_run.slice(5, 16).replace("T", " ")) : "never"}</td><td class="num">${esc(x.next || "")}</td>
        <td>${!x.enabled ? chip("", "off") : x.done_today ? chip("done", "done today") : !x.last_run ? chip("", "not run yet") : x.ok ? chip("done", "ok") : chip("failed")} <span class="dim">${link(x.done_today || x.note || "")}</span></td></tr>`).join("")}</tbody></table></div>`;
  }

  function capacityHTML(c) {
    const used = c.used_pct / 100, ceil = c.ceiling_pct / 100;
    return `<div class="gauge"><div class="row"><span class="num" style="font-size:21px">${F.num(c.used_pct, 1)}%</span><span class="dim">of the weekly Claude allowance used${c.estimate ? " (estimate)" : ""}</span></div>
      <div class="bar ${used >= ceil ? "bad" : c.used_pct > c.target_pct + 1 ? "amber" : ""}" style="height:6px;margin:8px 0 4px"><i style="width:${Math.min(100, c.used_pct)}%"></i><s style="left:${c.ceiling_pct}%"></s>
        <s style="left:${c.target_pct}%;background:var(--ice);opacity:.7"></s></div>
      <div class="dim">Even pace would be ${F.num(c.target_pct, 1)}% by now (blue mark). The white mark is the ${F.num(c.ceiling_pct)}% ceiling: ${F.num(c.reserve_pct)}% is kept for your own urgent work.</div>
      <div style="margin-top:6px">${esc(c.status[0].toUpperCase() + c.status.slice(1))}. Background runs that may start now: <b class="num">${c.allowed_runs}</b>.
        ${c.five_hour_pct != null ? `5-hour window at ${F.num(c.five_hour_pct)}%.` : ""}</div>
      <div class="faint" style="margin-top:4px">Resets ${esc(c.resets.slice(0, 16).replace("T", " "))}. Source: ${esc(c.source)}.</div></div>`;
  }

  function conversationHTML(s) {
    const cv = s.conversation, voices = J.voiceOptions(), cur = J.currentVoice();
    const opt = (list, val) => list.map(([v, l]) => `<option value="${esc(v)}"${val === v ? " selected" : ""}>${esc(l)}</option>`).join("");
    return `<div class="body stack" style="gap:10px">
      <div class="row"><label class="dim">Model <select class="field" data-conv="model">${opt([["", "Claude Code's default"], ["haiku", "Haiku (fastest)"], ["sonnet", "Sonnet"], ["opus", "Opus"], ["fable", "Fable (your call only)"]], cv.model)}</select></label>
        <label class="dim">Effort <select class="field" data-conv="effort">${opt([["", "Default"], ["low", "Low (quick replies)"], ["medium", "Medium"], ["high", "High"]], cv.effort)}</select></label></div>
      <div class="row"><label class="dim">Voice <select class="field" data-voice>${opt(voices.map((v) => [v.id, v.label]), cur)}</select></label>
        <button class="btn sm" data-act="voice-test">Hear it</button>
        <label class="row dim"><input type="checkbox" data-handsfree ${J.settings.handsfree ? "checked" : ""}> Keep listening after each reply</label></div>
      <div class="row"><button class="btn sm" data-act="sessions">${sessions ? "Refresh conversations" : "Show earlier conversations"}</button>
        <span class="faint">Current: ${esc((cv.session || "a new one").slice(0, 8))}</span></div>
      ${sessions ? `<ul class="list" style="border:1px solid var(--line);border-radius:6px">${sessions.map((x) => `<li class="row" style="flex-wrap:nowrap">
        <span class="clip grow" title="${esc(x.title)}">${esc(x.title || "Untitled")}</span><span class="faint">${F.ago(x.updated)}</span>
        ${x.current ? chip("done", "current") : `<button class="btn sm ghost" data-act="session-switch" data-id="${esc(x.id)}">Carry on here</button>`}</li>`).join("")}</ul>` : ""}</div>`;
  }

  Object.assign(App.acts, {
    "wake-toggle": () => { J.settings.wake = !J.settings.wake; },
    connectors: () => App.run("/api/connectors", {}, "Checking the connectors; this takes a minute"),
    "voice-test": () => J.preview(),
    sessions: async () => { try { sessions = await App.get("/api/control/sessions?n=12"); App.draw(); } catch (e) { App.toast(e.message, "err"); } },
    "session-switch": (el) => App.run("/api/control/session", { id: el.dataset.id }, "Conversation switched").then((r) => { if (r) App.acts.sessions(); }),
    screen: () => { screenOn = !screenOn; App.draw(); },
    "screen-refresh": () => { const i = $("#sysScreenImg"); if (i) i.src = `/api/control/screen?k=${encodeURIComponent(window.JARVIS_TOKEN)}&t=${Date.now()}`; },
    "pc-open": (el) => { const i = el.closest("form").elements.target; if (i.value.trim()) App.run("/api/control/open", { target: i.value.trim() }, (r) => `Opened ${r.opened}`); },
  });

  App.register("system", {
    title: "System",
    render(el, S) {
      if (!el._built) {
        el._built = true;
        el.innerHTML = `<header><h1>System</h1><p id="sSum"></p></header>
          <div class="cols"><div class="stack">
            <section class="panel"><h2>Routines <small>what runs while you are away</small></h2><div id="sRoutines"></div></section>
            <section class="panel"><h2>Connections</h2><div id="sConn"></div></section>
            <section class="panel"><h2>Latest store, ad and file actions <small>from the action log</small></h2><div id="sActions"></div></section>
            <section class="panel"><h2>This PC</h2><div id="sPc"></div></section>
          </div><div class="stack">
            <section class="panel"><h2>Claude capacity</h2><div id="sCap"></div></section>
            <section class="panel"><h2>Conversation and voice</h2><div id="sConv"></div></section>
          </div></div>`;
        el.addEventListener("change", (e) => {
          const t = e.target;
          if (t.dataset.conv) App.run("/api/conversation", { [t.dataset.conv]: t.value }, "Saved. The next reply uses it.");
          else if ("voice" in t.dataset) { J.settings.voice = t.value; J.preview(); }
          else if ("handsfree" in t.dataset) J.settings.handsfree = t.checked;
        });
      }
      const s = S.system, r = s.routines, failed = r.routines.filter((x) => x.last_run && !x.ok);
      $("#sSum", el).textContent = failed.length ? `${F.plural(failed.length, "routine")} failed on the last run` : "Routines are running";
      block($("#sRoutines", el), routinesHTML(r));
      block($("#sConn", el), connectionsHTML(s));
      block($("#sActions", el), s.actions.length ? `<ul class="list">${s.actions.map((a) => `<li class="row" style="flex-wrap:nowrap"><span class="faint num">${esc(a.ts.slice(5, 16).replace("T", " "))}</span>
        <span class="chip">${esc(a.service)}</span><span class="clip grow">${esc(a.tool)}${a.what ? `: ${esc(a.what)}` : ""}</span>${a.link ? `<a href="${esc(a.link)}" target="_blank" rel="noopener">Open</a>` : ""}</li>`).join("")}</ul>`
        : `<div class="empty">No store, ad or file changes have been logged.</div>`);
      block($("#sCap", el), capacityHTML(S.capacity));
      if (!el.querySelector("#sConv select:focus")) block($("#sConv", el), conversationHTML(s));
      block($("#sPc", el), `<div class="body stack" style="gap:8px"><form class="row" style="flex-wrap:nowrap" onsubmit="return false">
          <input class="field" name="target" placeholder="Open an app, folder, file or web page: chrome, dicloak, C:/Users/..., https://..." aria-label="Open on the PC">
          <button class="btn" data-act="pc-open" type="button">Open on the PC</button></form>
        <div class="row"><button class="btn sm" data-act="screen">${screenOn ? "Hide the screen" : "Show the PC's screen"}</button>${screenOn ? `<button class="btn sm ghost" data-act="screen-refresh">Refresh</button>` : ""}
          <span class="faint">Brain: ${esc(s.brain)}</span></div>
        ${screenOn ? `<img id="sysScreenImg" alt="The PC's screen" style="width:100%;border:1px solid var(--line);border-radius:6px" src="/api/control/screen?k=${encodeURIComponent(window.JARVIS_TOKEN)}">` : ""}</div>`);
    },
  });
})();
