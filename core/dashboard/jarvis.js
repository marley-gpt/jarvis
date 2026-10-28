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
    "store-qa":          { icon: "🛠️", layer: "Store QA", color: "#f472b6", blurb: "Clicks through the store like a customer, finds bugs and prepares tested fixes." },
    "meta-ads":          { icon: "📣", layer: "Campaign layer", color: "#3b82f6", blurb: "Prepares campaign drafts and analyses ad performance." },
    "meta-ads-auditor":  { icon: "🩺", layer: "Account audit", color: "#06b6d4", blurb: "Read-only deep audit of the Meta ad account: signals, creative, structure, audiences." },
    "accountant":        { icon: "🧾", layer: "Money", color: "#facc15", blurb: "Tracks every cost and sale, and reports profit and loss for each week and each product test." },
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
  J.stop = () => { cancelSpeech(); if (J.listening) rec?.stop(); post("/api/stop").catch(() => {}); };
  J.newConversation = () => post("/api/new").catch((e) => J.emit("error", { text: e.message }));

  let saidThisTurn = false, failedTurn = false;
  // images: [{ media_type: "image/png", data: "<base64>" , url?: "<object URL for showing it>" }]
  J.send = async (text, images) => {
    text = (text || "").trim();
    images = images || [];
    if (!text && !images.length) return;
    saidThisTurn = false; failedTurn = false;
    J.emit("owner", { text, images });
    try { await post("/api/chat", { text, images: images.map(({ media_type, data }) => ({ media_type, data })) }); }
    catch (e) { failedTurn = true; J.emit("error", { text: e.message }); }
  };
  // Read a pasted, dropped or chosen image file into what J.send expects.
  J.readImage = (file) => new Promise((resolve, reject) => {
    if (!/^image\/(png|jpeg|gif|webp)$/.test(file.type)) return reject(new Error("Only PNG, JPEG, GIF or WebP images."));
    if (file.size > 5 * 1024 * 1024) return reject(new Error("Images must be under 5 MB."));
    const r = new FileReader();
    r.onload = () => resolve({ media_type: file.type, data: String(r.result).split(",")[1], url: URL.createObjectURL(file) });
    r.onerror = () => reject(new Error("That image couldn't be read."));
    r.readAsDataURL(file);
  });

  function setStatus(s) { J.status = s; J.emit("status", { status: s, shown: shown() }); }
  function shown() { return J.speaking ? "speaking" : J.listening ? "listening" : J.status; }
  J.shown = shown;

  let refreshTimer = null;
  function onEvent(e) {
    switch (e.type) {
      case "changed": clearTimeout(refreshTimer); refreshTimer = setTimeout(J.refresh, 150); return;
      case "status": J.status = e.status; J.emit("status", { ...e, shown: shown() }); if (e.status === "idle") afterTurn(); return;
      // Replies stream in a sentence at a time (say_chunk) and are spoken as they arrive;
      // "say" then carries the whole paragraph for display, already spoken if it was streamed.
      case "say_chunk": saidThisTurn = true; J.emit("say_chunk", e); say(e.text); return;
      case "say": saidThisTurn = true; J.emit("say", e); if (!e.streamed) say(e.text); return;
      case "reply":
        if (!saidThisTurn && e.text && !e.is_error) { J.emit("say", { text: e.text }); say(e.text); }
        if (e.is_error) {
          failedTurn = true;
          J.emit("error", { text: e.text || "JARVIS hit an error.", login: !!e.login });
          if (e.login) say(e.text);  // the owner may be talking hands-free, so say it too
        }
        return;
      case "error": failedTurn = true; J.emit("error", e); return;
      case "wake": J.wake(); return;
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
    const MIC_ERRORS = {
      "not-allowed": "Microphone access is blocked. Click the lock icon in the address bar, allow the microphone, then try again.",
      "service-not-allowed": "This browser won't allow speech recognition here. Open the dashboard in Chrome or Edge.",
      "audio-capture": "No microphone was found. Check one is plugged in and selected in Windows sound settings.",
      "network": "Voice recognition needs an internet connection (Chrome and Edge use an online speech service).",
      "no-speech": "I didn't hear anything. Tap the mic and try again.",
    };
    rec.onerror = (ev) => {
      if (ev.error === "aborted") return;
      failedTurn = ev.error !== "no-speech" || failedTurn;
      J.emit("error", { text: MIC_ERRORS[ev.error] || `Voice input error: ${ev.error}`, mic: true });
      if (["not-allowed", "service-not-allowed", "audio-capture"].includes(ev.error)) J.settings.handsfree = false;
    };
    rec.onend = () => { J.listening = false; setStatus(J.status); const t = finalText.trim(); if (t) J.send(t); };
  }
  J.listen = () => {
    if (!rec) return J.emit("error", { text: "Voice input needs Chrome or Edge. You can still type." });
    if (J.listening) return rec.stop();
    failedTurn = false;
    cancelSpeech();
    try { rec.start(); } catch (e) { /* already starting */ }
  };

  // ---------------------------------------------------------- voice out
  // JARVIS speaks with Kokoro (a local neural voice served by /api/tts) when it is installed,
  // otherwise with the browser's own voice. Voice ids: "kokoro:jarvis" (a tuned preset), "kokoro:bm_george",
  // or a browser voice name.
  J.settings = {
    get handsfree() { return J.pref("handsfree") === "1"; }, set handsfree(v) { J.pref("handsfree", v ? "1" : "0"); if (v) { failedTurn = false; afterTurn(); } },
    get speak() { return J.pref("speak") !== "0"; }, set speak(v) { J.pref("speak", v ? "1" : "0"); if (!v) cancelSpeech(); },
    get voice() { return J.pref("speechVoice"); }, set voice(v) { J.pref("speechVoice", v); },
  };
  J.voices = () => (window.speechSynthesis ? speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en")) : []);
  J.bestVoice = () => {
    const vs = J.voices();
    return vs.find((v) => v.name === J.settings.voice) || vs.find((v) => /natural|neural|online/i.test(v.name) && /en-(GB|AU)/.test(v.lang))
      || vs.find((v) => /natural|neural|online/i.test(v.name)) || vs[0];
  };
  const kokoroOn = () => !!J.state?.tts?.available;
  J.voiceOptions = () => [
    ...(kokoroOn() ? J.state.tts.voices.map((v) => ({ id: "kokoro:" + v.id, label: `JARVIS (Kokoro) · ${v.name}` })) : []),
    ...J.voices().map((v) => ({ id: v.name, label: v.name })),
  ];
  // George was the default before the JARVIS-tuned voice existed: move people who never chose otherwise.
  if (J.pref("speechVoice") === "kokoro:bm_george" && J.pref("voiceUpgrade") !== "1") J.pref("speechVoice", "kokoro:jarvis");
  J.pref("voiceUpgrade", "1");
  J.currentVoice = () => {
    const saved = J.settings.voice, opts = J.voiceOptions();
    if (saved && opts.some((o) => o.id === saved)) return saved;
    if (kokoroOn()) return "kokoro:" + J.state.tts.voice;
    return J.bestVoice()?.name || "";
  };
  if (window.speechSynthesis) speechSynthesis.onvoiceschanged = () => J.emit("voices", J.voices());
  let ttsSeen = null;
  J.on("state", (s) => { const k = JSON.stringify(s.tts || null); if (k !== ttsSeen) { ttsSeen = k; J.emit("voices", J.voices()); } });

  let speakQueue = 0, chain = Promise.resolve(), gen = 0, audio = null;
  function setSpeaking(v) { J.speaking = v; setStatus(J.status); }
  function cancelSpeech() {
    gen++; speakQueue = 0;
    window.speechSynthesis?.cancel();
    if (audio) { const a = audio; audio = null; a.pause(); }
    setSpeaking(false);
  }
  J.cancelSpeech = cancelSpeech;
  J.spoken = (t) => t.replace(/```[\s\S]*?```/g, " ").replace(/`([^`]+)`/g, "$1").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_#>|]/g, "").replace(/\bT(\d+)\b/g, "task $1").replace(/A\$/g, "A dollars ").replace(/\s+/g, " ").trim();
  function chunks(text) {
    const out = [];
    for (const s of text.match(/[^.!?]+[.!?]*\s*/g) || [text]) {
      if (out.length && (out[out.length - 1] + s).length < 180) out[out.length - 1] += s; else out.push(s);
    }
    return out.map((c) => c.trim()).filter(Boolean);
  }
  function say(text) {
    const all = J.spoken(text);
    if (!J.settings.speak || !all) { J.emit("caption", { text: all, done: true }); return; }
    const voice = J.currentVoice(), my = gen;
    const kokoro = voice.startsWith("kokoro:") && kokoroOn();
    chunks(all).forEach((c) => {
      speakQueue++; setSpeaking(true);
      const wav = kokoro ? fetchWav(c, voice.slice(7)) : null;  // fetched ahead while earlier sentences play
      wav?.catch(() => {});
      chain = chain
        .then(() => (my !== gen ? null : wav ? playWav(c, wav, my).catch(() => my === gen && playBrowser(c)) : playBrowser(c)))
        .catch(() => {})
        .then(() => {
          if (my !== gen) return;
          speakQueue = Math.max(0, speakQueue - 1);
          J.emit("caption", { text: c, done: true });
          if (!speakQueue) { setSpeaking(false); afterTurn(); }
        });
    });
  }
  async function fetchWav(text, voice) {
    const r = await fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis-Token": window.JARVIS_TOKEN },
      body: JSON.stringify({ text, voice }) });
    if (!r.ok) throw new Error("tts " + r.status);
    return URL.createObjectURL(await r.blob());
  }
  function playWav(text, wavPromise, my) {
    return wavPromise.then((url) => new Promise((resolve, reject) => {
      if (my !== gen) { URL.revokeObjectURL(url); return resolve(); }
      const a = audio = new Audio(url);
      let raf = 0, settled = false;
      const done = (ok) => {
        if (settled) return; settled = true;
        cancelAnimationFrame(raf); URL.revokeObjectURL(url);
        if (audio === a) audio = null;
        ok ? resolve() : reject(new Error("playback failed"));
      };
      // Kokoro gives no word timings, so the caption follows the audio's progress through the sentence.
      let last = -1;
      const tick = () => {
        const at = a.duration ? Math.floor(text.length * a.currentTime / a.duration) : 0;
        if (at !== last) { last = at; J.emit("caption", { text, at }); }
        raf = requestAnimationFrame(tick);
      };
      a.onplay = () => { J.emit("caption", { text, at: 0 }); raf = requestAnimationFrame(tick); };
      a.onended = () => done(true);
      a.onerror = () => done(false);
      a.onpause = () => { if (my !== gen) done(true); };  // stopped by the owner
      a.play().catch(() => done(false));
    }));
  }
  function playBrowser(clean) {
    return new Promise((resolve) => {
      if (!window.speechSynthesis) return resolve();
      const u = new SpeechSynthesisUtterance(clean);
      const v = J.voices().find((x) => x.name === J.currentVoice()) || J.bestVoice();
      if (v) u.voice = v;
      u.rate = 1.03;
      u.onstart = () => J.emit("caption", { text: clean, at: 0 });
      u.onboundary = (ev) => { if (ev.name === "word" || ev.name === undefined) J.emit("caption", { text: clean, at: ev.charIndex }); };
      u.onend = u.onerror = () => resolve();
      speechSynthesis.speak(u);
    });
  }
  J.preview = () => { cancelSpeech(); say("Very good. This is how I shall sound from now on."); };
  J.say = say;
  function afterTurn() {
    if (failedTurn) return;  // don't keep reopening the mic after an error
    if (J.settings.handsfree && J.status === "idle" && !J.speaking && !J.listening) setTimeout(() => {
      if (J.status === "idle" && !J.speaking && !J.listening) J.listen();
    }, 350);
  }

  // ---------------------------------------------------------- "Hey JARVIS"
  // The dashboard server runs a local wake-word listener (openWakeWord, core/jarvis/wakeword.py) that
  // only listens for "Hey JARVIS": nothing is recorded or transcribed until it hears it. It sends a
  // "wake" event; JARVIS greets the owner like the film's JARVIS, gives a one-line status, then
  // listens for the request. The listener is paused while JARVIS is speaking or already listening.
  let lastPause = null;
  function syncWakePause() {
    const paused = J.speaking || J.listening;
    if (paused !== lastPause) { lastPause = paused; post("/api/wake", { paused }).catch(() => {}); }
  }
  J.on("status", syncWakePause);
  J.greeting = () => {
    const s = J.state, h = new Date().getHours();
    const part = h < 5 ? "evening" : h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
    const open = [`Good ${part}, sir.`, `Welcome home, sir.`, `At your service, sir.`, `Good ${part}, sir. All systems are online.`];
    const bits = [open[Math.floor(Math.random() * open.length)]];
    if (s) {
      const working = s.agents.filter((a) => a.status === "working").length;
      const ap = s.approvals.length;
      if (ap) bits.push(`You have ${ap} approval${ap === 1 ? "" : "s"} awaiting your decision.`);
      bits.push(working ? `${working} of the team ${working === 1 ? "is" : "are"} at work.` : "The team is standing by.");
    }
    bits.push("What can I do for you?");
    return bits.join(" ");
  };
  J.wake = () => {
    if (J.speaking || J.listening || J.status !== "idle") return;
    J.emit("awake", {});
    say(J.greeting());
    if (!J.canListen) return;
    const t0 = Date.now();
    const wait = setInterval(() => {  // listen as soon as the greeting has been spoken
      if (!J.speaking || Date.now() - t0 > 20000) { clearInterval(wait); if (!J.listening && J.status === "idle") J.listen(); }
    }, 250);
  };
  Object.defineProperty(J.settings, "wake", {
    get() { return !!(J.state && J.state.wake && J.state.wake.enabled); },
    set(v) { post("/api/wake", { on: !!v }).then(() => J.refresh()).catch((e) => J.emit("error", { text: e.message })); },
  });

  document.addEventListener("DOMContentLoaded", connect);
})();
