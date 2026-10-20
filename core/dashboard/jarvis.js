// Shared client for every JARVIS dashboard design: live state, chat, voice in and out.
// A page sets window.JARVIS_TOKEN, then listens with J.on(type, fn).
(function () {
  const AGENT_META = {
    "mentor-learning":   { icon: "🎓", layer: "Knowledge layer", color: "#a78bfa", blurb: "Watches the mentor's lessons and turns them into cited procedures every agent follows." },
    "product-research":  { icon: "🔎", layer: "Opportunity scan", color: "#38bdf8", blurb: "Finds and vets products: demand, competition, suppliers and margins." },
    "market-research":   { icon: "💬", layer: "Customer voice", color: "#2dd4bf", blurb: "Mines reviews and Reddit for pain points, objections and angles." },
    "creative-research": { icon: "🧲", layer: "Ad intelligence", color: "#fb7185", blurb: "Studies competitor ads and builds the inspiration library." },
    "creative-director": { icon: "🎨", layer: "Creative studio", color: "#f59e0b", blurb: "Develops angles, image briefs and copy for static ads." },
    "shopify":           { icon: "🛍️", layer: "Storefront", color: "#22c55e", blurb: "Drafts product pages and reviews the store, read-only." },
    "meta-ads":          { icon: "📣", layer: "Campaign layer", color: "#3b82f6", blurb: "Prepares campaign drafts and analyses ad performance." },
    "product-testing":   { icon: "🧪", layer: "Validation", color: "#a3e635", blurb: "Records every test, judges results and captures lessons." },
    "operations":        { icon: "📊", layer: "Signal layer", color: "#818cf8", blurb: "Tracks tasks, bottlenecks and costs, and writes the daily overview." },
  };
  const DEFAULT_META = { icon: "🤖", layer: "Specialist", color: "#94a3b8", blurb: "" };

  const handlers = {};
  const J = {
    state: null, status: "idle", listening: false, speaking: false, connected: false,
    meta: (name) => AGENT_META[name] || DEFAULT_META,
    label: (name) => (J.state?.agents.find((a) => a.name === name) || {}).label || name,
    on(type, fn) { (handlers[type] ||= []).push(fn); return J; },
    emit(type, data) { (handlers[type] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } }); },
    esc: (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
    hhmm: (ts) => (ts || "").slice(11, 16),
    pref(k, v) {
      try { if (v === undefined) return localStorage.getItem("jarvis." + k); localStorage.setItem("jarvis." + k, v); }
      catch (e) { return null; }
    },
  };
  window.J = J;

  // ---------------------------------------------------------- server
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis-Token": window.JARVIS_TOKEN },
      body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }
  J.post = post;
  J.refresh = async () => {
    const r = await fetch("/api/state");
    if (!r.ok) return;
    J.state = await r.json();
    J.emit("state", J.state);
  };
  J.memory = async (q, c) => (await fetch(`/api/memory?q=${encodeURIComponent(q || "")}&c=${encodeURIComponent(c || "")}`)).json();
  J.memoryEntry = async (id) => (await fetch(`/api/memory/${encodeURIComponent(id)}`)).json();
  J.decide = async (id, approve) => { await post("/api/approval", { id, approve }); J.refresh(); };
  J.stop = () => { speechSynthesis?.cancel(); speakQueue = 0; setSpeaking(false); if (J.listening) rec?.stop(); post("/api/stop").catch(() => {}); };
  J.newConversation = () => post("/api/new").catch((e) => J.emit("error", { text: e.message }));

  let saidThisTurn = false;
  J.send = async (text) => {
    text = (text || "").trim();
    if (!text) return;
    saidThisTurn = false;
    J.emit("owner", { text });
    try { await post("/api/chat", { text }); }
    catch (e) { J.emit("error", { text: e.message }); }
  };

  function setStatus(s) { J.status = s; J.emit("status", { status: s, shown: shown() }); }
  function shown() { return J.speaking ? "speaking" : J.listening ? "listening" : J.status; }
  J.shown = shown;

  let refreshTimer = null;
  function onEvent(e) {
    switch (e.type) {
      case "changed": clearTimeout(refreshTimer); refreshTimer = setTimeout(J.refresh, 150); return;
      case "status": J.status = e.status; J.emit("status", { ...e, shown: shown() }); if (e.status === "idle") afterTurn(); return;
      case "say": saidThisTurn = true; J.emit("say", e); say(e.text); return;
      case "reply":
        if (!saidThisTurn && e.text && !e.is_error) { J.emit("say", { text: e.text }); say(e.text); }
        if (e.is_error) J.emit("error", { text: e.text || "JARVIS hit an error." });
        return;
      default: J.emit(e.type, e);
    }
  }

  function connect() {
    const es = new EventSource("/api/events");
    es.onopen = () => { J.connected = true; J.emit("connection", { live: true }); J.refresh(); };
    es.onerror = () => { J.connected = false; J.emit("connection", { live: false }); };
    es.onmessage = (m) => onEvent(JSON.parse(m.data));
  }

  // ---------------------------------------------------------- voice in
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null;
  J.canListen = !!Rec;
  if (Rec) {
    rec = new Rec();
    rec.lang = navigator.language || "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    let finalText = "";
    rec.onstart = () => { J.listening = true; finalText = ""; setStatus(J.status); };
    rec.onresult = (ev) => {
      let interim = "";
      for (const r of ev.results) (r.isFinal ? (finalText += r[0].transcript) : (interim += r[0].transcript));
      J.emit("interim", { text: finalText + interim });
    };
    rec.onerror = (ev) => { if (ev.error === "not-allowed") J.emit("error", { text: "Microphone permission is blocked for this page." }); };
    rec.onend = () => { J.listening = false; setStatus(J.status); const t = finalText.trim(); if (t) J.send(t); };
  }
  J.listen = () => {
    if (!rec) return J.emit("error", { text: "Voice input needs Chrome or Edge. You can still type." });
    if (J.listening) return rec.stop();
    speechSynthesis?.cancel(); speakQueue = 0; setSpeaking(false);
    try { rec.start(); } catch (e) { /* already starting */ }
  };

  // ---------------------------------------------------------- voice out
  J.settings = {
    get handsfree() { return J.pref("handsfree") === "1"; }, set handsfree(v) { J.pref("handsfree", v ? "1" : "0"); if (v) afterTurn(); },
    get speak() { return J.pref("speak") !== "0"; }, set speak(v) { J.pref("speak", v ? "1" : "0"); if (!v) speechSynthesis?.cancel(); },
    get voice() { return J.pref("voice"); }, set voice(v) { J.pref("voice", v); },
  };
  J.voices = () => (window.speechSynthesis ? speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en")) : []);
  J.bestVoice = () => {
    const vs = J.voices();
    return vs.find((v) => v.name === J.settings.voice) || vs.find((v) => /natural|neural|online/i.test(v.name) && /en-(GB|AU)/.test(v.lang))
      || vs.find((v) => /natural|neural|online/i.test(v.name)) || vs[0];
  };
  if (window.speechSynthesis) speechSynthesis.onvoiceschanged = () => J.emit("voices", J.voices());

  let speakQueue = 0;
  function setSpeaking(v) { J.speaking = v; setStatus(J.status); }
  J.spoken = (t) => t.replace(/```[\s\S]*?```/g, " ").replace(/`([^`]+)`/g, "$1").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_#>|]/g, "").replace(/\bT(\d+)\b/g, "task $1").replace(/A\$/g, "A dollars ").replace(/\s+/g, " ").trim();
  function say(text) {
    const clean = J.spoken(text);
    if (!J.settings.speak || !window.speechSynthesis || !clean) { J.emit("caption", { text: clean, done: true }); return; }
    const u = new SpeechSynthesisUtterance(clean);
    const v = J.bestVoice();
    if (v) u.voice = v;
    u.rate = 1.03;
    u.onstart = () => J.emit("caption", { text: clean, at: 0 });
    u.onboundary = (ev) => { if (ev.name === "word" || ev.name === undefined) J.emit("caption", { text: clean, at: ev.charIndex }); };
    u.onend = u.onerror = () => {
      speakQueue = Math.max(0, speakQueue - 1);
      J.emit("caption", { text: clean, done: true });
      if (!speakQueue) { setSpeaking(false); afterTurn(); }
    };
    speakQueue++; setSpeaking(true);
    speechSynthesis.speak(u);
  }
  J.say = say;
  function afterTurn() {
    if (J.settings.handsfree && J.status === "idle" && !J.speaking && !J.listening) setTimeout(() => {
      if (J.status === "idle" && !J.speaking && !J.listening) J.listen();
    }, 350);
  }

  document.addEventListener("DOMContentLoaded", connect);
})();
