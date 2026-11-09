// JARVIS speaking visuals (T86), the page at /. A central orb that moves with the voice, and a stage where what
// JARVIS is talking about appears as cards, timed to the sentence he is saying (the JARVIS of reels M-261109-ced6,
// M-261108-ede3 and M-261109-738f). Cards come from /api/visuals (core/jarvis/visuals.py): screen hints in the
// reply ([[count 412 applications]]) or what the sentence says (T/A/D/M ids, products, money, counts, lists, days,
// files), resolved through the real records. Uses only window.J (jarvis.js); no second event stream.
(() => {
  const J = window.J;
  if (!J) return;
  const esc = J.esc;
  const $ = (id) => document.getElementById(id);
  const norm = (s) => J.spoken(s || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
  // A sentence, with any screen hint that follows it.
  const SENT = /(?:[^.!?\[]|[.!?](?=[\w$])|\[(?!\[))*[.!?]*(?:\s*\[\[[^\]]*\]\])*\s*/g;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let app, stage, line;

  // ------------------------------------------------------------ orb
  // Rings like the reel's screen: a tick ring, rotating arcs and a radial band of the voice's spectrum round a
  // glowing core. Speaking: the core and the band follow the audio. Thinking: the arcs spin. Listening: brighter.
  function startOrb(canvas) {
    const g = canvas.getContext("2d");
    let W = 0, lvl = 0, spin = 0, t0 = performance.now();
    const bands = new Float32Array(72);
    const fit = () => {
      const r = canvas.getBoundingClientRect(), d = Math.min(2, devicePixelRatio || 1);
      W = Math.max(1, Math.round(r.width * d)); canvas.width = canvas.height = W;
    };
    new ResizeObserver(fit).observe(canvas); fit();
    function frame(now) {
      requestAnimationFrame(frame);
      if (document.hidden || !W) return;
      const t = (now - t0) / 1000, shown = J.shown(), c = W / 2, R = W / 2;
      const busy = shown === "thinking" || shown === "delegating", listening = shown === "listening";
      lvl += ((J.level ? J.level() : 0) - lvl) * 0.22;
      spin += (reduced ? 0.002 : 0.004) + (busy ? 0.03 : 0) + lvl * 0.02;
      const spec = J.spectrum ? J.spectrum() : null;
      for (let i = 0; i < bands.length; i++) {
        const k = i < 36 ? i : 71 - i;  // mirrored, so the band is symmetrical left to right
        let v;
        if (spec) v = spec[2 + Math.floor(k * 1.6)] / 255;
        else v = J.speaking ? lvl * (0.45 + 0.55 * Math.abs(Math.sin(t * 6 + k * 0.7) * Math.cos(t * 2.3 + k * 0.31))) : 0;
        bands[i] += (v - bands[i]) * 0.35;
      }
      g.clearRect(0, 0, W, W);
      const core = R * (0.26 + lvl * 0.05 + (listening ? 0.015 * Math.sin(t * 4) : 0));

      const halo = g.createRadialGradient(c, c, core * 0.4, c, c, R);
      halo.addColorStop(0, `rgba(46,134,255,${0.32 + lvl * 0.4 + (listening ? 0.12 : 0)})`);
      halo.addColorStop(0.5, "rgba(46,134,255,.07)"); halo.addColorStop(1, "rgba(46,134,255,0)");
      g.fillStyle = halo; g.fillRect(0, 0, W, W);

      g.save(); g.translate(c, c);
      // tick ring
      g.rotate(spin * 0.25);
      for (let i = 0; i < 120; i++) {
        const a = (i / 120) * Math.PI * 2, long = i % 10 === 0;
        g.strokeStyle = `rgba(169,219,255,${long ? 0.6 : 0.22})`; g.lineWidth = Math.max(1, W / 500);
        g.beginPath(); g.moveTo(Math.cos(a) * R * 0.86, Math.sin(a) * R * 0.86);
        g.lineTo(Math.cos(a) * R * (long ? 0.81 : 0.835), Math.sin(a) * R * (long ? 0.81 : 0.835)); g.stroke();
      }
      g.rotate(-spin * 0.25);
      // arcs
      g.lineCap = "round";
      [[0.74, 1, 0.9, 3], [0.67, -1.6, 0.45, 2], [0.62, 2.4, 1.3, 1.2]].forEach(([rr, dir, len, w], k) => {
        const a0 = spin * dir + k * 2.1;
        for (let s = 0; s < 3; s++) {
          g.beginPath(); g.arc(0, 0, R * rr, a0 + s * 2.094, a0 + s * 2.094 + len);
          g.strokeStyle = k === 1 ? `rgba(169,219,255,${0.5 + lvl * 0.4})` : `rgba(46,134,255,${0.7 + lvl * 0.3})`;
          g.lineWidth = (W / 300) * w; g.stroke();
        }
      });
      // the voice band
      const inner = core * 1.22;
      for (let i = 0; i < bands.length; i++) {
        const a = (i / bands.length) * Math.PI * 2 - Math.PI / 2, len = R * 0.02 + bands[i] * R * 0.2;
        g.strokeStyle = `rgba(169,219,255,${0.35 + bands[i] * 0.65})`; g.lineWidth = Math.max(1.5, W / 260);
        g.beginPath(); g.moveTo(Math.cos(a) * inner, Math.sin(a) * inner);
        g.lineTo(Math.cos(a) * (inner + len), Math.sin(a) * (inner + len)); g.stroke();
      }
      // core
      g.beginPath();
      for (let a = 0; a <= Math.PI * 2 + 0.01; a += Math.PI / 90) {
        const wob = 1 + lvl * 0.06 * Math.sin(a * 5 + t * 7) + 0.012 * Math.sin(a * 3 - t * 1.5);
        const x = Math.cos(a) * core * wob, y = Math.sin(a) * core * wob;
        a === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
      }
      const fill = g.createRadialGradient(-core * 0.25, -core * 0.3, core * 0.05, 0, 0, core);
      fill.addColorStop(0, "#f2f8ff"); fill.addColorStop(0.3, "#8fc6ff"); fill.addColorStop(0.75, "#2e86ff"); fill.addColorStop(1, "#0b2c6e");
      g.shadowColor = "rgba(46,134,255,.9)"; g.shadowBlur = W / 14 + lvl * W / 10;
      g.fillStyle = fill; g.fill(); g.shadowBlur = 0;
      g.restore();
    }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------ cards
  const money = (v, cur) => `${cur === "USD" ? "US$" : "A$"}${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  const fileUrl = (p) => `/api/hq/file?path=${encodeURIComponent(p)}`;
  const status = (s) => {
    const txt = String(s || "").replace(/_/g, " ");
    const cls = /pending|blocked|review|waiting/.test(txt) ? "warn" : /done|approved|live|launched/.test(txt) ? "ok" : "";
    return txt ? `<span class="chip ${cls}">${esc(txt)}</span>` : "";
  };
  function card(c) {
    switch (c.kind) {
      case "count": return [`<div class="figure" data-to="${+c.value}">0</div><div class="label">${esc(c.label)}</div>`];
      case "percent": return [`<div class="figure" data-to="${+c.value}" data-suffix="%">0%</div><div class="label">${esc(c.label)}</div>`];
      case "money": return [`<div class="figure" data-to="${+c.value}" data-cur="${esc(c.currency)}">${money(0, c.currency)}</div><div class="label">${esc(c.label)}</div>`];
      case "list": return [`<ol class="rank">${c.items.map((it, i) => `<li style="animation-delay:${180 + i * 260}ms"><span>${i + 1}</span>${esc(it)}</li>`).join("")}</ol>`, "wide"];
      case "case": return [`<div class="kind">Proposal</div><div class="title">${esc(c.title)}</div>${(c.items || []).length ? `<ul class="reasons">${c.items.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
        <div class="acts"><button class="btn go" data-say="Yes, go ahead.">Go ahead</button><button class="btn" data-say="No, leave that with me for now.">Not now</button></div>`, "wide needs"];
      case "date": return [`<div class="when"><div class="cal"><i>${esc((c.day || "Date").slice(0, 3))}</i><b>${esc(c.time ? c.time.replace(/\s/g, "") : "—")}</b></div><div><div class="kind">Calendar</div><div class="title">${esc(c.label)}</div></div></div>`];
      case "task": return [`<div class="kind"><span class="id">${esc(c.id)}</span>Task ${status(c.status)}</div><div class="title">${esc(c.title)}</div>
        <div class="detail">${c.agent ? esc(c.agent) : "JARVIS"}${c.product ? `, ${esc(c.product)}` : ""}${c.file ? ` · <a href="${fileUrl(c.file)}" target="_blank" rel="noopener">Open the deliverable</a>` : ""}</div>`];
      case "approval": {
        const pend = c.status === "pending";
        return [`<div class="kind"><span class="id">${esc(c.id)}</span>Approval, ${esc(c.type)} ${status(c.status)}</div><div class="title">${esc(c.title)}</div>
          ${c.amount ? `<div class="detail">${esc(c.currency || "")} ${esc(c.amount)}${c.task ? ` for ${esc(c.task)}` : ""}</div>` : ""}
          ${pend ? `<div class="acts"><button class="btn go" data-approve="${esc(c.id.slice(1))}" data-yes="1">Approve</button><button class="btn" data-approve="${esc(c.id.slice(1))}" data-yes="0">Reject</button></div>` : ""}`, pend ? "needs" : ""];
      }
      case "decision": return [`<div class="kind"><span class="id">${esc(c.id)}</span>Decision</div><div class="title">${esc(c.title)}</div>${c.detail ? `<div class="detail">${esc(c.detail)}</div>` : ""}`];
      case "memory": return [`<div class="kind"><span class="id">${esc(c.id)}</span>${esc(c.category || "Memory")}</div><div class="title">${esc(c.title)}</div><div class="detail">${esc(c.detail)}</div>`];
      case "product": return [`<div class="kind">Product ${status(c.status)}</div><div class="title">${esc(c.title)}</div>`];
      case "image": return [`<div class="kind">Image</div><div class="title">${esc(c.title)}</div><a href="${fileUrl(c.path)}" target="_blank" rel="noopener"><img class="shot" alt="${esc(c.title)}" src="${fileUrl(c.path)}"></a>`];
      case "file": return [`<div class="kind">File</div><div class="title">${esc(c.title)}</div><div class="detail"><a href="${fileUrl(c.path)}" target="_blank" rel="noopener">Open ${esc(c.title)}</a></div>`];
      default: return null;
    }
  }
  // The brain-graph node a card stands for: the main page lights it while it is said and opens it on a tap.
  const ref = (c) => ({ task: c.id, approval: c.id, decision: c.id, memory: c.id, product: c.id && `product:${c.id}`,
    file: c.path && `file:${c.path}`, image: c.path && `file:${c.path}` })[c.kind] || "";
  function countUp(el) {
    const to = +el.dataset.to, start = performance.now(), dur = reduced ? 1 : 1100, cur = el.dataset.cur;
    const dp = Number.isInteger(to) ? 0 : 1;
    const fmt = (v) => cur ? money(v, cur) : v.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp }) + (el.dataset.suffix || "");
    const step = (now) => {
      const k = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmt(k < 1 ? (dp ? Math.round(to * e * 10) / 10 : Math.round(to * e)) : to);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  let isDemo = false;
  function head(text) {
    if (stage.querySelector(".stage-head")) return;
    stage.insertAdjacentHTML("afterbegin", `<div class="stage-head"><h2>${esc(text)}</h2>${isDemo ? `<span class="demo-tag">Demo with sample data, not your records</span>` : ""}</div>`);
  }
  // One spoken sentence's cards: a beat. Earlier beats of the reply stay, dimmed, so the reply builds up.
  function present(cards, sentence) {
    const built = cards.map((c) => { const b = card(c); return b && [b[0], b[1], ref(c)]; }).filter(Boolean);
    if (!built.length) return;
    // The main page (app.js) lights the records this beat names in the brain graph behind the cards.
    window.dispatchEvent(new CustomEvent("jarvis:beat", { detail: { refs: built.map((b) => b[2]).filter(Boolean), sentence, demo: isDemo } }));
    if (stage.dataset.mode !== "reply") { stage.innerHTML = ""; stage.dataset.mode = "reply"; }
    head("On screen now");
    stage.querySelectorAll(".beat:not(.past)").forEach((b) => b.classList.add("past"));
    const beat = document.createElement("div");
    beat.className = "beat";
    // figures and dates stay compact; text cards take the full width on a phone ("txt")
    beat.innerHTML = built.map(([html, cls, r], i) => `<article class="card ${cls || ""} ${/class="(figure|when)"/.test(html) ? "" : "txt"}"${r ? ` data-ref="${esc(r)}"` : ""} style="animation-delay:${i * 140}ms">${html}</article>`).join("")
      + (sentence ? `<p class="cue">${esc(sentence.replace(/\[\[[^\]]*\]\]/g, " ").replace(/[*_`#>|]/g, "").trim())}</p>` : "");
    stage.appendChild(beat);
    while (stage.querySelectorAll(".beat").length > 6) stage.querySelector(".beat").remove();
    beat.querySelectorAll("[data-to]").forEach(countUp);
    app.classList.add("busy");
    requestAnimationFrame(() => beat.scrollIntoView({ block: "end", behavior: reduced ? "auto" : "smooth" }));
  }

  // When nothing is being said: what is waiting on the owner and who is working, from the live state.
  function renderBoard() {
    if (stage.dataset.mode === "reply" || !J.state) return;
    stage.dataset.mode = "board";
    const s = J.state, approvals = s.approvals || [], working = (s.agents || []).filter((a) => a.status === "working");
    const cards = [
      ...approvals.slice(0, 4).map((a) => card({ kind: "approval", id: `A${a.id}`, title: a.summary, status: a.status, type: a.kind,
        amount: a.amount, currency: a.currency, task: a.task_id ? `T${a.task_id}` : null })),
      ...working.slice(0, 4).map((a) => [`<div class="kind">${esc(a.label || a.name)} ${status("working")}</div><div class="title">${esc(a.now || "Working")}</div>`]),
    ];
    const open = s.counts?.open_tasks ?? 0;
    stage.innerHTML = `<div class="stage-head"><h2>On the board</h2><span>${open} open task${open === 1 ? "" : "s"}</span></div>`
      + (cards.length ? `<div class="beat">${cards.map(([h, cls]) => `<article class="card ${cls || ""}">${h}</article>`).join("")}</div>`
        : `<div class="empty"><p>Nothing needs you and nobody is mid-job.</p><p>Ask JARVIS anything. What he talks about shows here as he says it: figures, shortlists, tasks, approvals, dates and files.</p></div>`);
  }

  // ------------------------------------------------------------ sync with the voice
  // Each sentence of the reply is fetched as cards when it arrives. jarvis.js speaks a few sentences per audio
  // clip and reports progress through it as caption events ({text, at}); a sentence's cards go up when the
  // voice reaches it. With the voice off, the whole reply's cards go up at once.
  let pending = [];  // {key, raw, cards, shown, want}
  function enqueue(raw) {
    for (const s of String(raw || "").match(SENT) || []) {
      if (!s.trim()) continue;
      const key = norm(s);
      const last = pending[pending.length - 1];
      if (!key && last) {  // a screen hint on its own belongs to the sentence before it
        last.raw += " " + s.trim();
        fetchCards(last, true);
        continue;
      }
      const item = { key, raw: s, cards: null, shown: false, want: false };
      pending.push(item);
      if (pending.length > 40) pending.shift();
      fetchCards(item);
    }
  }
  function fetchCards(item, again) {
    const ask = item.raw;
    J.post("/api/visuals", { text: ask }).then((r) => {
      if (item.raw !== ask) return;
      const cards = r.cards || [];
      if (again && item.shown) {  // a late hint: add what's new
        const seen = new Set((item.cards || []).map((c) => JSON.stringify(c)));
        const extra = cards.filter((c) => !seen.has(JSON.stringify(c)));
        item.cards = cards;
        if (extra.length) present(extra, "");
        return;
      }
      item.cards = cards;
      if (item.want || !item.key) fire(item);
    }).catch(() => { item.cards = item.cards || []; });
  }
  // Beats go up in the order they were said: one still being fetched holds back the ones after it.
  function fire(item) {
    if (!item.shown) item.want = true;
    for (const p of pending) {
      if (p.shown || !p.want) continue;
      if (p.cards === null) return;
      p.shown = true;
      present(p.cards, p.raw);
    }
  }
  function onCaption(e) {
    const text = e.text || "";
    const k = norm(text);
    if (!k) return;
    const frac = e.done ? 1 : text.length ? (e.at || 0) / text.length : 0;
    showLine(text, frac);
    for (const p of pending) {
      if (p.shown || !p.key) continue;
      const at = k.indexOf(p.key.slice(0, 60));
      const inside = at >= 0 || p.key.includes(k.slice(0, 60));
      if (inside && (e.done || at <= 0 || at / k.length <= frac + 0.03)) fire(p);
    }
  }
  // The caption: only the sentence being spoken (a clip can hold several), lit up to where the voice is.
  function showLine(text, frac) {
    const clean = J.spoken(text), cut = Math.round(clean.length * Math.min(1, frac));
    let start = 0, sentence = clean;
    for (const s of clean.match(SENT) || [clean]) {
      if (start + s.length > cut || start + s.length >= clean.length) { sentence = s; break; }
      start += s.length;
    }
    const sp = sentence.indexOf(" ", cut - start);
    const split = frac >= 1 || sp < 0 ? sentence.length : sp;
    line.innerHTML = `<span class="said">${esc(sentence.slice(0, split))}</span><span class="tosay">${esc(sentence.slice(split))}</span>`;
  }

  // ------------------------------------------------------------ what JARVIS and the team are doing
  const doing = [];
  const hhmm = (d) => d.toTimeString().slice(0, 5);
  function note(html) { doing.unshift({ html, at: new Date() }); doing.length = Math.min(doing.length, 6); renderTicker(); }
  function renderTicker() {
    const live = (J.state?.agents || []).filter((a) => a.status === "working");
    if (!$("ticker")) return;
    $("ticker").innerHTML = [
      ...live.slice(0, 3).map((a) => `<li class="live"><span><b>${esc(a.label || a.name)}</b> ${esc(a.now || "working")}</span></li>`),
      ...doing.slice(0, 5 - Math.min(3, live.length)).map((d) => `<li><time>${hhmm(d.at)}</time><span>${d.html}</span></li>`),
    ].join("");
  }

  // ------------------------------------------------------------ demo (labelled; sample data only)
  const DEMO = [
    "Four hundred and twelve applications came in for the support role, sir. [[count 412 applications]]",
    "I checked every test task against your criteria and kept three: Maya Reid, Daniel Cho and Priya Nair. [[list Maya Reid; Daniel Cho; Priya Nair]]",
    "Maya's first call is booked for Thursday at 3pm. [[date Thursday 3pm intro call with Maya]]",
    "Profit so far is two hundred and fourteen dollars against the three hundred and eighty weekly target. [[money 214 profit this week]]",
  ];
  function runDemo() {
    J.cancelSpeech?.();
    isDemo = true; pending = [];
    window.dispatchEvent(new CustomEvent("jarvis:clear"));
    stage.innerHTML = ""; stage.dataset.mode = "reply";
    DEMO.forEach(enqueue);
    J.say(DEMO.join(" "));
  }

  // ------------------------------------------------------------ wiring
  const STATE = { idle: "Standing by", thinking: "Thinking", delegating: "Handing out work", listening: "Listening", speaking: "Speaking" };
  function toast(text) {
    const t = $("toast"); t.textContent = text; t.classList.add("on");
    clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.remove("on"), 6000);
  }
  function boot() {
    app = $("app"); stage = $("stage"); line = $("line");
    startOrb($("orb"));
    renderBoard();

    $("talk").addEventListener("submit", (e) => {
      e.preventDefault();
      const v = $("text").value.trim();
      if (!v) return;
      $("text").value = "";
      J.send(v);
    });
    $("mic").onclick = () => (J.listening ? J.stop() : J.listen());
    const speakBtn = $("speak");
    const syncSpeak = () => { const on = J.settings.speak; speakBtn.classList.toggle("off", !on); speakBtn.setAttribute("aria-pressed", String(on)); speakBtn.setAttribute("aria-label", on ? "Voice on" : "Voice off"); };
    speakBtn.onclick = () => { J.settings.speak = !J.settings.speak; syncSpeak(); };
    syncSpeak();
    $("demo").onclick = runDemo;

    stage.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.say) { J.send(b.dataset.say); b.parentElement.innerHTML = `<span class="detail">You said: ${esc(b.dataset.say)}</span>`; }
      if (b.dataset.approve) {
        const yes = b.dataset.yes === "1", box = b.parentElement;
        box.querySelectorAll("button").forEach((x) => { x.disabled = true; });
        J.decide(+b.dataset.approve, yes)
          .then(() => { box.innerHTML = `<span class="detail">${yes ? "Approved" : "Rejected"}</span>`; })
          .catch((err) => { box.querySelectorAll("button").forEach((x) => { x.disabled = false; }); toast(err.message); });
      }
    });

    J.on("connection", ({ live }) => { $("conn").classList.toggle("live", live); $("conn").lastElementChild.textContent = live ? "Live" : "Reconnecting"; });
    J.on("state", () => { renderTicker(); renderBoard(); });
    J.on("status", ({ shown }) => {
      $("state").textContent = STATE[shown] || shown;
      $("mic").classList.toggle("hot", shown === "listening");
      if (shown === "idle" && !J.speaking) {
        setTimeout(() => { if (!J.speaking) pending.forEach(fire); }, 1500);  // anything the voice never reached
      }
    });
    J.on("interim", ({ text }) => { line.innerHTML = `<span class="tosay">${esc(text)}</span>`; });
    J.on("owner", (e) => {
      isDemo = false; pending = [];
      window.dispatchEvent(new CustomEvent("jarvis:clear"));
      stage.innerHTML = ""; stage.dataset.mode = "reply"; app.classList.remove("busy");
      line.innerHTML = `<span class="tosay">${esc(e.text || "(image)")}</span>`;
      note(`You: ${esc((e.text || "(image)").slice(0, 70))}`);
    });
    J.on("say_chunk", (e) => enqueue(e.raw || e.text));
    J.on("say", (e) => { if (!e.streamed) enqueue(e.raw || e.text); });
    J.on("caption", onCaption);
    J.on("dispatch", (e) => note(`Handed <b>${esc(e.agent || "a specialist")}</b> ${esc((e.description || e.task || "").slice(0, 80))}`));
    J.on("agent_activity", (e) => note(`<b>${esc(e.agent || "")}</b> ${esc((e.text || e.note || "").slice(0, 80))}`));
    J.on("agent_done", (e) => note(`<b>${esc(e.agent || "")}</b> finished`));
    J.on("error", (e) => toast(e.text || "Something went wrong."));
  }
  document.addEventListener("DOMContentLoaded", boot);

  window.JStage = { present, enqueue, demo: runDemo };
})();
