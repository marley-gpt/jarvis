// The control centre (T98): everything the owner used to open the Claude Code window for, on any page that
// loads this after jarvis.js. A "Control" button opens a drawer (a full-screen sheet on a phone) with:
//   Live      what JARVIS is running now, step by step with results; messages waiting their turn; Stop
//   Chat      a composer with any-file attach, slash commands and skills (type "/")
//   Sessions  every Claude Code conversation of this repo: read one back, switch to it, start a new one
//   PC        the screen, open apps / folders / files / pages, the desktop job runner, durable workers
// Permission prompts and JARVIS's questions show as a banner over the page wherever the drawer is, are
// spoken, and can be answered by voice ("yes" / "no"), on the phone (Telegram buttons) or here.
(function () {
  const J = window.J;
  if (!J || window.JARVIS_CONTROL) return;
  window.JARVIS_CONTROL = true;
  const $ = (s, el = document) => el.querySelector(s);
  const esc = J.esc;
  const C = { open: false, tab: J.pref("ctlTab") || "live", data: null, sessions: [], reading: null, worker: null,
              files: [], images: [], steps: [], screenOn: false };

  const css = `
  #ctl-btn{position:fixed;right:14px;top:14px;z-index:60;background:#0e1726ee;color:#cfe8ff;border:1px solid #2a4a6b;
    border-radius:20px;padding:7px 13px;font:600 13px system-ui,sans-serif;cursor:pointer;display:flex;gap:6px;align-items:center}
  .ctl-b{display:inline-flex;gap:6px;align-items:center} .ctl-b .dot{width:8px;height:8px;border-radius:50%;background:#3d5a78}
  .ctl-b.busy .dot{background:#38bdf8;box-shadow:0 0 8px #38bdf8} .ctl-b.ask .dot{background:#f59e0b;box-shadow:0 0 8px #f59e0b}
  .ctl-b .n{background:#f59e0b;color:#111;border-radius:9px;padding:0 6px;font-size:11px;display:none} .ctl-b.ask .n{display:inline}
  #ctl{position:fixed;top:0;right:0;bottom:0;width:min(560px,100vw);z-index:70;background:#070d16f7;color:#d8e6f5;
    border-left:1px solid #1d3550;display:none;flex-direction:column;font:14px/1.4 system-ui,sans-serif;backdrop-filter:blur(8px)}
  #ctl.on{display:flex}
  #ctl .ctl-head{display:flex;align-items:center;gap:6px;padding:10px 10px 0}
  #ctl .ctl-head b{font-size:15px;margin-right:auto;color:#9fd3ff;letter-spacing:.08em}
  #ctl .ctl-tabs{display:flex;gap:2px;padding:8px 10px 0;border-bottom:1px solid #1d3550}
  #ctl .ctl-tabs button{flex:1;background:none;border:0;color:#7f9bb8;padding:8px 4px;font:600 13px system-ui;cursor:pointer;border-bottom:2px solid transparent}
  #ctl .ctl-tabs button.on{color:#e6f3ff;border-color:#38bdf8}
  #ctl .body{flex:1;overflow-y:auto;overflow-x:hidden;padding:10px 12px 90px}
  #ctl .row>input{flex:1 1 0;width:auto}
  #ctl h4{margin:14px 0 6px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#6f8cab}
  #ctl .row{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  #ctl .b,#ctl-ask .b{background:#12253a;color:#d8e6f5;border:1px solid #27476a;border-radius:7px;padding:6px 10px;font:600 13px system-ui;cursor:pointer}
  #ctl button.b:hover,#ctl-ask button.b:hover{border-color:#38bdf8} #ctl button.ok,#ctl-ask button.ok{background:#0f3d2a;border-color:#1f7a50} #ctl button.no,#ctl-ask button.no{background:#3d1414;border-color:#7a2a2a}
  #ctl input,#ctl textarea,#ctl select{background:#0b1522;color:#e6f3ff;border:1px solid #27476a;border-radius:7px;padding:7px 9px;font:14px system-ui;min-width:0}
  #ctl textarea{width:100%;box-sizing:border-box;resize:vertical;min-height:64px}
  .ctl-step{display:flex;gap:8px;padding:5px 0;border-bottom:1px solid #102236;font-size:13px}
  .ctl-step .k{flex:none;width:18px;text-align:center} .ctl-step .t{flex:1;min-width:0;word-break:break-word}
  .ctl-step .out{display:block;color:#7f9bb8;font:12px ui-monospace,Consolas,monospace;white-space:pre-wrap;max-height:120px;overflow:auto;margin-top:2px}
  .ctl-step.err .t{color:#fca5a5} .ctl-step.run .k{animation:ctlspin 1s linear infinite}
  @keyframes ctlspin{to{transform:rotate(360deg)}}
  .ctl-card{background:#0c1828;border:1px solid #1d3550;border-radius:9px;padding:8px 10px;margin:6px 0}
  .ctl-card .meta{color:#6f8cab;font-size:12px} .ctl-card.cur{border-color:#38bdf8}
  .ctl-msg{margin:6px 0;padding:6px 9px;border-radius:8px;white-space:pre-wrap;word-break:break-word;font-size:13px}
  .ctl-msg.owner{background:#13304d;margin-left:20%} .ctl-msg.jarvis{background:#0c1828} .ctl-msg.tool{color:#6f8cab;font:12px ui-monospace,Consolas,monospace;padding:2px 9px}
  #ctl .screen{width:100%;border:1px solid #1d3550;border-radius:8px;background:#000;min-height:120px;display:block}
  #ctl .log{font:12px ui-monospace,Consolas,monospace;white-space:pre-wrap;background:#050a11;border:1px solid #1d3550;border-radius:8px;padding:8px;max-height:46vh;overflow:auto}
  #ctl .cmds{max-height:180px;overflow:auto;border:1px solid #1d3550;border-radius:8px;margin-top:4px;display:none}
  #ctl .cmds div{padding:5px 9px;cursor:pointer;font-size:13px} #ctl .cmds div:hover,#ctl .cmds div.sel{background:#13304d}
  #ctl .chips span{display:inline-flex;gap:4px;align-items:center;background:#13304d;border-radius:12px;padding:2px 8px;font-size:12px;margin:2px}
  #ctl .chips span b{cursor:pointer}
  #ctl-ask{position:fixed;left:50%;transform:translateX(-50%);top:12px;z-index:80;width:min(620px,calc(100vw - 24px));display:flex;flex-direction:column;gap:6px}
  #ctl-ask .ctl-card{background:#1a1406f5;border-color:#a16207;color:#fde68a;box-shadow:0 6px 30px #0008;font:14px system-ui}
  #ctl-ask .ctl-card .what{color:#fff;margin:4px 0 8px;word-break:break-word}
  #ctl-ask button{margin-right:6px} #ctl-ask input{background:#0b1522;color:#e6f3ff;border:1px solid #27476a;border-radius:7px;padding:7px 9px;font:14px system-ui}
  body.ctl-open #ctl-ask{left:calc((100vw - min(560px,100vw)) / 2);width:min(560px,calc(100vw - min(560px,100vw) - 24px))}
  .ctl-muted{color:#6f8cab;font-size:13px}
  @media (max-width:700px){#ctl{width:100vw;border:0} #ctl-btn{top:auto;bottom:86px;right:12px}
    body.ctl-open #ctl-ask,#ctl-ask{top:auto;bottom:12px;left:50%;width:calc(100vw - 24px)}}
  `;
  document.head.insertAdjacentHTML("beforeend", `<style>${css}</style>`);
  // A page can give the button a place of its own (#ctlBtn in its header); otherwise it floats top right.
  const own = document.getElementById("ctlBtn");
  document.body.insertAdjacentHTML("beforeend", `
    ${own ? "" : `<button id="ctl-btn" title="Control centre (Ctrl+K)"><span class="dot"></span>Control<span class="n">0</span></button>`}
    <div id="ctl-ask"></div>
    <aside id="ctl" aria-label="Control centre">
      <div class="ctl-head"><b>CONTROL</b>
        <button class="b" data-a="stop" title="Stop JARVIS">Stop</button>
        <button class="b" data-a="new" title="Start a new conversation">New</button>
        <button class="b" data-a="close" title="Close (Esc)">✕</button></div>
      <div class="ctl-tabs" role="tablist">${["live", "chat", "sessions", "pc"].map((t) => `<button data-tab="${t}">${{ live: "Live", chat: "Chat", sessions: "Sessions", pc: "PC" }[t]}</button>`).join("")}</div>
      <div class="body"></div>
    </aside>`);
  const panel = $("#ctl"), body = $("#ctl .body"), btn = own || $("#ctl-btn"), ask = $("#ctl-ask");
  btn.classList.add("ctl-b");

  async function get(path) {
    const r = await fetch(path);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || r.statusText);
    return d;
  }
  const post = (path, b) => J.post(path, b);
  const fail = (e) => J.emit("error", { text: e.message || String(e) });
  const ago = (t) => { const s = Math.max(0, Date.now() / 1000 - t); return s < 90 ? `${Math.round(s)}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 129600 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };

  // ---------------------------------------------------------------- data
  async function load() {
    try { C.data = await get("/api/control/state"); } catch (e) { return; }
    C.steps = C.data.steps || [];
    renderAsk(); renderBtn();
    // Live redraws as things happen; the other tabs only when the owner isn't typing there (or watching the screen).
    const typing = body.contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
    if (C.open && (C.tab === "live" || (C.tab === "pc" && !typing && !C.screenOn && !C.worker))) render();
    else if (C.open && !body.innerHTML) render();
  }
  let loadTimer = null;
  const soon = (ms = 200) => { clearTimeout(loadTimer); loadTimer = setTimeout(load, ms); };
  ["changed", "status", "permission", "permission_done", "queued", "jarvis_tool", "jarvis_tool_done", "dispatch", "agent_done", "reset", "session"]
    .forEach((t) => J.on(t, () => soon(t === "changed" ? 400 : 120)));
  J.on("agent_activity", () => { if (C.open && C.tab === "live") soon(400); });
  J.on("permission", (p) => {
    if (!J.settings.speak) return;
    J.say(p.question ? `I have a question for you, sir. ${(p.input.questions || []).map((q) => q.question).join(" ")}`
      : `I need your approval, sir: ${p.summary.replace(/^Let JARVIS /, "")}. Shall I go ahead?`);
  });
  setInterval(() => { if (!document.hidden && (C.open || (C.data && C.data.busy))) load(); }, 5000);

  function renderBtn() {
    const d = C.data || {}, n = (d.permissions || []).length;
    btn.classList.toggle("busy", !!d.busy); btn.classList.toggle("ask", n > 0);
    $(".n", btn).textContent = n;
  }

  // ---------------------------------------------------------------- permission prompts and questions
  function renderAsk() {
    const ps = (C.data && C.data.permissions) || [];
    const sig = ps.map((p) => p.id).join(",");
    if (sig === renderAsk.sig) return;  // unchanged: keep anything typed into an answer
    renderAsk.sig = sig;
    ask.innerHTML =ps.map((p) => p.question ? `
      <div class="ctl-card" data-id="${esc(p.id)}"><b>JARVIS asks</b>
        ${(p.input.questions || []).map((q, i) => `<div class="what">${esc(q.question)}</div><div class="row">
          ${(q.options || []).map((o) => `<button class="b" data-q="${i}" data-v="${esc(o.label)}">${esc(o.label)}</button>`).join("")}
          <input data-qi="${i}" placeholder="Or type an answer" style="flex:1"></div>`).join("")}
        <div class="row" style="margin-top:6px"><button class="b ok" data-send>Send answer</button></div></div>` : `
      <div class="ctl-card" data-id="${esc(p.id)}"><b>Approval needed${p.approval ? ` · A${p.approval}` : ""}</b>
        <div class="what">${esc(p.summary)}</div>
        <div class="row"><button class="b ok" data-yes>Approve</button><button class="b no" data-no>Reject</button>
        <span class="ctl-muted">or say "yes" / "no" · also on your phone</span></div></div>`).join("");
  }
  ask.addEventListener("click", (e) => {
    const card = e.target.closest(".ctl-card"); if (!card) return;
    const id = card.dataset.id;
    if (e.target.matches("[data-yes],[data-no]")) {
      post("/api/control/permission", { id, approve: e.target.matches("[data-yes]") }).then(load).catch(fail);
    } else if (e.target.matches("[data-q]")) {
      card.querySelector(`[data-qi="${e.target.dataset.q}"]`).value = e.target.dataset.v;
    } else if (e.target.matches("[data-send]")) {
      const p = (C.data.permissions || []).find((x) => x.id === id); if (!p) return;
      const answers = {};
      (p.input.questions || []).forEach((q, i) => { const v = card.querySelector(`[data-qi="${i}"]`).value.trim(); if (v) answers[q.question] = v; });
      if (!Object.keys(answers).length) return fail(new Error("Pick or type an answer first."));
      post("/api/control/permission", { id, answers }).then(load).catch(fail);
    }
  });

  // ---------------------------------------------------------------- the drawer
  function show(on, tab) {
    C.open = on; if (tab) { C.tab = tab; J.pref("ctlTab", tab); }
    panel.classList.toggle("on", on); document.body.classList.toggle("ctl-open", on);
    if (on) { load(); if (C.tab === "sessions") loadSessions(); } else stopScreen();
  }
  btn.onclick = () => show(!C.open);
  J.control = { show, open: (tab) => show(true, tab) };
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); show(!C.open, C.open ? null : "chat"); setTimeout(() => $("#ctl-text")?.focus(), 30); }
    if (e.key === "Escape" && C.open) show(false);
  });
  $("#ctl .ctl-tabs").onclick = (e) => {
    const t = e.target.dataset.tab; if (!t) return;
    C.tab = t; J.pref("ctlTab", t); C.reading = null; if (t !== "pc") stopScreen();
    if (t === "sessions") loadSessions();
    render();
  };
  $("#ctl .ctl-head").onclick = (e) => {
    const a = e.target.dataset.a;
    if (a === "close") show(false);
    if (a === "stop") J.stop();
    if (a === "new") post("/api/new").then(() => { C.reading = null; load(); loadSessions(); }).catch(fail);
  };

  function render() {
    panel.querySelectorAll(".ctl-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === C.tab));
    ({ live: renderLive, chat: renderChat, sessions: renderSessions, pc: renderPc })[C.tab]();
  }

  // ---------------------------------------------------------------- Live
  const ICON = { tool: "⚙", agent: "➜", agent_line: "·", done: "✓", error: "✕", owner: "🗣", say: "💬" };
  function stepHtml(s) {
    const cls = s.state === "error" ? "err" : s.state === "running" ? "run" : "";
    const icon = s.state === "running" ? "◌" : s.state === "error" ? "✕" : ICON[s.kind] || "·";
    return `<div class="ctl-step ${cls}"><span class="k">${icon}</span><span class="t">${esc(s.text)}${s.out ? `<span class="out">${esc(s.out)}</span>` : ""}</span>
      <span class="ctl-muted">${esc((s.ts || "").slice(11, 19))}</span></div>`;
  }
  function renderLive() {
    const d = C.data || {};
    const q = d.queue || [];
    body.innerHTML = `
      <div class="row"><b>${d.busy ? `JARVIS is ${esc(d.status)}` : "JARVIS is free"}</b>
        ${d.busy ? `<button class="b no" data-stop>Stop</button>` : ""}
        <span class="ctl-muted">${d.session ? `conversation ${esc(d.session.slice(0, 8))}` : "new conversation"}</span></div>
      ${q.length ? `<h4>Waiting their turn (${q.length})</h4>${q.map((m, i) => `<div class="ctl-card row"><span style="flex:1">${esc(m.text)}</span>
        <button class="b" data-unq="${i}">Remove</button></div>`).join("")}` : ""}
      <h4>What JARVIS is running</h4>
      ${C.steps.length ? C.steps.slice().reverse().map(stepHtml).join("") : `<p class="ctl-muted">Nothing yet in this session. Tool steps appear here live.</p>`}`;
    body.onclick = (e) => {
      if (e.target.matches("[data-stop]")) J.stop();
      if (e.target.dataset.unq) post("/api/control/unqueue", { index: +e.target.dataset.unq }).then(load).catch(fail);
    };
  }

  // ---------------------------------------------------------------- Chat: composer, files, slash commands
  function renderChat() {
    const cmds = (C.data && C.data.commands) || { slash: [], skills: [] };
    body.innerHTML = `
      <h4>Message JARVIS</h4>
      <textarea id="ctl-text" placeholder="Type a message. / for commands and skills. Enter sends, Shift+Enter for a new line."></textarea>
      <div class="cmds" id="ctl-cmds"></div>
      <div class="chips" id="ctl-chips"></div>
      <div class="row" style="margin-top:6px">
        <button class="b ok" data-send>Send</button>
        <label class="b" style="cursor:pointer">Attach<input type="file" multiple hidden id="ctl-file"></label>
        <button class="b" data-mic>🎙 Talk</button>
        <span class="ctl-muted">Drop or paste files here. ${C.data && C.data.busy ? "JARVIS is busy: this waits its turn." : ""}</span>
      </div>
      <h4>Commands and skills (${cmds.slash.length})</h4>
      <div class="row">${cmds.slash.length ? cmds.slash.map((c) => `<button class="b" data-cmd="${esc(c)}">/${esc(c)}</button>`).join("")
        : `<span class="ctl-muted">The list arrives with JARVIS's first reply in this session.</span>`}</div>
      <p class="ctl-muted">Window-only commands (/login, /config, /doctor) don't run here. New, Stop and the model are on this page.</p>`;
    const ta = $("#ctl-text"), list = $("#ctl-cmds");
    drawChips();
    let sel = 0, matches = [];
    const suggest = () => {
      const m = ta.value.match(/^\/(\S*)$/);
      matches = m ? cmds.slash.filter((c) => c.startsWith(m[1])).slice(0, 30) : [];
      sel = Math.min(sel, Math.max(0, matches.length - 1));
      list.style.display = matches.length ? "block" : "none";
      list.innerHTML = matches.map((c, i) => `<div class="${i === sel ? "sel" : ""}" data-pick="${esc(c)}">/${esc(c)}${cmds.skills.includes(c) ? ' <span class="ctl-muted">skill</span>' : ""}</div>`).join("");
    };
    const pick = (c) => { ta.value = `/${c} `; matches = []; list.style.display = "none"; ta.focus(); };
    ta.oninput = suggest;
    ta.onkeydown = (e) => {
      if (matches.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length; suggest(); return; }
      if (matches.length && e.key === "Tab") { e.preventDefault(); pick(matches[sel]); return; }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    };
    ta.onpaste = (e) => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); addFiles(fs); } };
    body.ondragover = (e) => e.preventDefault();
    body.ondrop = (e) => { e.preventDefault(); addFiles([...(e.dataTransfer?.files || [])]); };
    $("#ctl-file").onchange = (e) => { addFiles([...e.target.files]); e.target.value = ""; };
    body.onclick = (e) => {
      if (e.target.dataset.pick) pick(e.target.dataset.pick);
      if (e.target.dataset.cmd) pick(e.target.dataset.cmd);
      if (e.target.matches("[data-send]")) send();
      if (e.target.matches("[data-mic]")) J.listen();
      if (e.target.dataset.rm) { const [k, i] = e.target.dataset.rm.split(":"); C[k].splice(+i, 1); drawChips(); }
    };
    function send() {
      const text = ta.value.trim();
      if (!text && !C.files.length && !C.images.length) return;
      J.send(text, C.images.slice(), C.files.slice());
      ta.value = ""; C.files = []; C.images = []; drawChips(); suggest();
      C.tab = "live"; J.pref("ctlTab", "live"); setTimeout(() => { render(); load(); }, 150);
    }
  }
  function drawChips() {
    const el = $("#ctl-chips"); if (!el) return;
    el.innerHTML = [...C.images.map((f, i) => `<span>🖼 image ${i + 1} <b data-rm="images:${i}">✕</b></span>`),
      ...C.files.map((f, i) => `<span>📎 ${esc(f.name)} <b data-rm="files:${i}">✕</b></span>`)].join("");
  }
  const readFile = (file) => new Promise((resolve, reject) => {
    if (file.size > 15 * 1024 * 1024) return reject(new Error(`${file.name}: files must be under 15 MB.`));
    const r = new FileReader();
    r.onload = () => resolve({ name: file.name, data: String(r.result).split(",")[1] || "" });
    r.onerror = () => reject(new Error(`${file.name} couldn't be read.`));
    r.readAsDataURL(file);
  });
  async function addFiles(list) {
    for (const f of list) {
      try {
        if (/^image\/(png|jpeg|gif|webp)$/.test(f.type) && f.size <= 5 * 1024 * 1024 && C.images.length < 4) C.images.push(await J.readImage(f));
        else if (C.files.length < 4) C.files.push(await readFile(f));
        else throw new Error("At most 4 files and 4 images per message.");
      } catch (e) { fail(e); }
    }
    drawChips();
  }

  // ---------------------------------------------------------------- Sessions
  async function loadSessions() {
    try { C.sessions = await get("/api/control/sessions?n=40"); } catch (e) { C.sessions = []; }
    if (C.open && C.tab === "sessions") render();
  }
  function renderSessions() {
    if (C.reading) {
      const r = C.reading;
      body.innerHTML = `<div class="row"><button class="b" data-back>← All conversations</button>
          <button class="b ok" data-switch="${esc(r.id)}">Carry on this one here</button></div>
        <h4>${esc(r.title)}</h4>${r.msgs ? r.msgs.map((m) => `<div class="ctl-msg ${m.role}">${esc(m.text)}</div>`).join("") || '<p class="ctl-muted">Nothing said yet.</p>' : '<p class="ctl-muted">Loading…</p>'}`;
    } else {
      body.innerHTML = `<p class="ctl-muted">Every Claude Code conversation in this project: this page's, the desktop window's, the workers'. Switch to one and your next message carries it on.</p>
        ${C.sessions.map((s) => `<div class="ctl-card ${s.current ? "cur" : ""}"><div>${esc(s.title)}</div>
          <div class="row"><span class="meta" style="flex:1">${s.current ? "<b>this page's conversation</b> · " : ""}${ago(s.updated)} · ${s.kb} KB · ${esc(s.id.slice(0, 8))}</span>
          <button class="b" data-read="${esc(s.id)}">Read</button>${s.current ? "" : `<button class="b" data-switch="${esc(s.id)}">Switch</button>`}</div></div>`).join("")
          || '<p class="ctl-muted">No conversations found.</p>'}`;
    }
    body.onclick = async (e) => {
      const t = e.target;
      if (t.matches("[data-back]")) { C.reading = null; render(); }
      if (t.dataset.read) {
        const s = C.sessions.find((x) => x.id === t.dataset.read) || { title: "" };
        C.reading = { id: t.dataset.read, title: s.title, msgs: null }; render();
        try { C.reading.msgs = await get(`/api/control/transcript?id=${encodeURIComponent(t.dataset.read)}`); } catch (err) { C.reading.msgs = []; fail(err); }
        render(); body.scrollTop = body.scrollHeight;
      }
      if (t.dataset.switch) {
        post("/api/control/session", { id: t.dataset.switch }).then(() => { C.reading = null; loadSessions(); load(); J.say("Very good. That conversation is open."); }).catch(fail);
      }
    };
  }

  // ---------------------------------------------------------------- PC
  let screenTimer = null;
  function stopScreen() { C.screenOn = false; clearTimeout(screenTimer); }
  function nextShot() {
    const img = $("#ctl-screen"); if (!img || !C.screenOn) return;
    img.src = `/api/control/screen?k=${encodeURIComponent(window.JARVIS_TOKEN)}&w=${Math.round(Math.min(1920, panel.clientWidth * (devicePixelRatio || 1)))}&t=${Date.now()}`;
  }
  function renderPc() {
    const d = C.data || {};
    const jobs = d.jobs || [], workers = d.workers || [];
    body.innerHTML = `
      <h4>Screen</h4>
      <div class="row"><button class="b" data-screen>${C.screenOn ? "Pause" : "Show the screen live"}</button>
        <span class="ctl-muted">${C.screenOn ? "Refreshes every 2 seconds while open." : "Off until you ask, so nothing is captured in the background."}</span></div>
      ${C.screenOn ? `<img id="ctl-screen" class="screen" alt="This PC's screen">` : ""}
      <h4>Open on the PC</h4>
      <div class="row"><input id="ctl-open" placeholder="An app, a folder, a file or a web address" style="flex:1"><button class="b ok" data-open>Open</button></div>
      <div class="row" style="margin-top:6px">${["explorer", "chrome", "dicloak", "vscode", "notepad", "terminal", "brain/work", "https://admin.shopify.com", "https://adsmanager.facebook.com"]
        .map((a) => `<button class="b" data-quick="${esc(a)}">${esc(a.replace(/^https:\/\//, ""))}</button>`).join("")}</div>
      <h4>Desktop jobs (screen work) · runner ${d.job_runner ? '<span style="color:#4ade80">on</span>' : '<span style="color:#f87171">off</span>'}</h4>
      ${d.job_runner ? "" : `<p class="ctl-muted">The job runner holds the computer-use grant, so it is started once at the PC in a Claude desktop session (master-key-tools skill). Jobs queue until it is on.</p>`}
      <div class="row"><input id="ctl-job" placeholder="What to do on screen, e.g. open Kalodata and list the top 20 beauty products" style="flex:1"><button class="b ok" data-job>Queue</button></div>
      ${jobs.map((j) => `<div class="ctl-card"><div class="row"><b>J${j.id}</b><span class="meta" style="flex:1">${esc(j.status)} · ${esc(J.hhmm(j.ts))} · ${esc(j.source || "")}</span>
        ${j.status === "queued" ? `<button class="b" data-jcancel="${j.id}">Cancel</button>` : ""}${j.status === "running" ? `<button class="b" data-screen-on>Watch</button>` : ""}</div>
        <div>${esc(j.text)}</div>${j.summary ? `<div class="meta">${esc(j.summary)}</div>` : ""}</div>`).join("")}
      <h4>Workers (long jobs that survive restarts)</h4>
      ${C.worker ? workerView() : `
      ${workers.map((w) => `<div class="ctl-card"><div class="row"><b>W${w.id}</b><span class="meta" style="flex:1">${esc(w.status)} · ${esc(w.task || "")} ${esc(w.agent || "")} · ${esc((w.started || "").slice(5, 16))}</span>
        <button class="b" data-wlog="${w.id}">Log</button>${w.status === "running" ? `<button class="b no" data-wstop="${w.id}">Stop</button>` : ""}
        ${w.status === "stopped" ? `<button class="b" data-wresume="${w.id}">Resume</button>` : ""}</div>
        <div class="meta">${esc((w.brief || "").slice(0, 160))}</div></div>`).join("") || '<p class="ctl-muted">No workers yet.</p>'}
      <details><summary class="ctl-muted" style="cursor:pointer">Start a worker</summary>
        <div class="row" style="margin:6px 0"><input id="ctl-wtask" placeholder="Task, e.g. T98" style="width:110px">
          <select id="ctl-wagent"><option value="">general</option>${(J.state?.agents || []).map((a) => `<option>${esc(a.name)}</option>`).join("")}</select></div>
        <textarea id="ctl-wbrief" placeholder="The brief: what to do, the deliverable and its path"></textarea>
        <button class="b ok" data-wstart style="margin-top:6px">Start worker</button></details>`}`;
    if (C.screenOn) { const img = $("#ctl-screen"); img.onload = img.onerror = () => { clearTimeout(screenTimer); screenTimer = setTimeout(nextShot, 2000); }; nextShot(); }
    const go = (target) => post("/api/control/open", { target }).then((r) => J.emit("notice", { text: `Opened ${r.opened}` })).catch(fail);
    $("#ctl-open").onkeydown = (e) => { if (e.key === "Enter") go(e.target.value.trim()); };
    $("#ctl-job").onkeydown = (e) => { if (e.key === "Enter") body.querySelector("[data-job]").click(); };
    body.onclick = (e) => {
      const t = e.target, ds = t.dataset;
      if (t.matches("[data-screen]")) { C.screenOn = !C.screenOn; if (!C.screenOn) stopScreen(); renderPc(); }
      if (t.matches("[data-screen-on]")) { C.screenOn = true; renderPc(); body.scrollTop = 0; }
      if (t.matches("[data-open]")) go($("#ctl-open").value.trim());
      if (ds.quick) go(ds.quick);
      if (t.matches("[data-job]")) {
        const text = $("#ctl-job").value.trim(); if (!text) return;
        post("/api/control/job", { text }).then((r) => { J.say(r.runner ? "Queued for the desktop runner." : "Queued. The desktop runner is off, so it will wait until it's started."); load(); }).catch(fail);
      }
      if (ds.jcancel) post("/api/control/job", { cancel: +ds.jcancel }).then(load).catch(fail);
      if (ds.wlog) openWorker(+ds.wlog);
      if (ds.wstop && confirm(`Stop worker W${ds.wstop}? It won't be resumed automatically.`)) post("/api/control/worker", { action: "stop", id: +ds.wstop }).then(load).catch(fail);
      if (ds.wresume) post("/api/control/worker", { action: "resume", id: +ds.wresume }).then(load).catch(fail);
      if (t.matches("[data-wstart]")) {
        const brief = $("#ctl-wbrief").value.trim(); if (!brief) return fail(new Error("Write the brief first."));
        post("/api/control/worker", { action: "start", brief, task: $("#ctl-wtask").value.trim(), agent: $("#ctl-wagent").value })
          .then((r) => { J.say(`Worker W${r.id} has started.`); openWorker(r.id); }).catch(fail);
      }
      if (t.matches("[data-wback]")) { C.worker = null; clearTimeout(workerTimer); renderPc(); }
    };
  }
  let workerTimer = null;
  function workerView() {
    const w = C.worker;
    return `<div class="row"><button class="b" data-wback>← All workers</button><b>W${w.id}</b><span class="ctl-muted">${esc(w.status || "")} ${esc(w.task || "")}</span></div>
      <div class="ctl-muted" style="margin:6px 0">${esc(w.brief || "")}</div>
      <div class="log" id="ctl-wlog">${esc((w.lines || []).join("\n")) || "Nothing logged yet."}</div>`;
  }
  async function openWorker(id) {
    clearTimeout(workerTimer);
    try { C.worker = await get(`/api/control/worker?id=${id}&n=200`); } catch (e) { return fail(e); }
    if (!C.open || C.tab !== "pc") return;
    const log = $("#ctl-wlog"), atEnd = !log || log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    if (log && C.worker) { log.textContent = (C.worker.lines || []).join("\n") || "Nothing logged yet."; } else renderPc();
    const l2 = $("#ctl-wlog"); if (l2 && atEnd) l2.scrollTop = l2.scrollHeight;
    if (C.worker.status === "running") workerTimer = setTimeout(() => C.worker && C.worker.id === id && openWorker(id), 3000);
  }

  // Files dropped or pasted on the page's own talk bar open the composer with them attached.
  const talk = document.getElementById("talk");
  if (talk) {
    talk.addEventListener("dragover", (e) => e.preventDefault());
    talk.addEventListener("drop", (e) => { const fs = [...(e.dataTransfer?.files || [])]; if (!fs.length) return; e.preventDefault(); e.stopPropagation(); show(true, "chat"); addFiles(fs); });
    talk.addEventListener("paste", (e) => { const fs = [...(e.clipboardData?.files || [])]; if (!fs.length) return; e.preventDefault(); show(true, "chat"); addFiles(fs); });
  }

  // /#control (or #control-pc, #control-sessions …) opens the drawer, so it can be linked or bookmarked.
  const fromHash = () => { const m = location.hash.match(/^#control(?:-(live|chat|sessions|pc))?$/); if (m) show(true, m[1] || C.tab); };
  window.addEventListener("hashchange", fromHash);
  fromHash();
  load();
})();
