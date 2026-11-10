// The app's line to the server: one live event stream that carries the state and every event, sending messages,
// the mic, the wake word and the Kokoro voice. app.js draws the pages; this file has no markup in it.
// The page sets window.JARVIS_TOKEN, then listens with J.on(type, fn).
(function () {
  const handlers = {};
  const J = {
    // sections: the cockpit's state by section (kpis, pipeline, tests, inbox, work, live, money, capacity, brain,
    // system), kept current by "state" events on the stream. Nothing polls.
    sections: {}, status: "idle", listening: false, speaking: false, connected: false,
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
  // The page's key can go out of date: pick up the current one, so the request can be resent.
  async function refreshToken() {
    const m = (await (await fetch(location.pathname)).text()).match(/JARVIS_TOKEN = "([^"]+)"/);
    if (!m || m[1] === window.JARVIS_TOKEN) return false;
    window.JARVIS_TOKEN = m[1];
    return true;
  }
  async function post(path, body, retried) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis-Token": window.JARVIS_TOKEN },
      body: JSON.stringify(body || {}) });
    if (r.status === 403 && !retried && await refreshToken()) return post(path, body, true);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }
  J.post = post;
  const sys = () => J.sections.system || {};
  function merge(sections, full) {
    Object.assign(J.sections, sections);
    J.emit("state", { sections, full: !!full });
  }
  // The stream brings the state on its own; this is for a window that wants it now (after an action, say).
  J.refresh = async () => {
    const r = await fetch("/api/app");
    if (r.ok) merge((await r.json()).sections, true);
  };
  J.memory = async (q, c) => (await fetch(`/api/memory?q=${encodeURIComponent(q || "")}&c=${encodeURIComponent(c || "")}`)).json();
  J.memoryEntry = async (id) => (await fetch(`/api/memory/${encodeURIComponent(id)}`)).json();
  J.decide = (id, approve) => post("/api/approval", { id, approve });
  J.stop = () => { cancelSpeech(); if (J.listening) rec?.stop(); post("/api/stop").catch(() => {}); };
  J.newConversation = () => post("/api/new").catch((e) => J.emit("error", { text: e.message }));

  let saidThisTurn = false, failedTurn = false, voiceTurn = false, listeningFor = "request";
  // images: [{ media_type: "image/png", data: "<base64>" , url?: "<object URL for showing it>" }]
  // files: [{ name: "brief.pdf", data: "<base64>" }], any kind but programs, saved in the brain for JARVIS to read.
  // While JARVIS is busy the message waits its turn on the server ("queued").
  J.send = async (text, images, files) => {
    text = (text || "").trim();
    images = images || []; files = files || [];
    if (!text && !images.length && !files.length) return;
    saidThisTurn = false; failedTurn = false; voiceTurn = false;
    J.emit("owner", { text, images, files });
    try {
      const r = await post("/api/chat", { text, images: images.map(({ media_type, data }) => ({ media_type, data })), files });
      if (r.queued) J.emit("queued", r);
      if (r.answered) J.emit("notice", { text: `A${r.answered.approval || ""} ${r.answered.status}` });
    }
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
  function shown() {
    return J.speaking ? "speaking" : J.listening ? "listening" : J.ears === "thinking" ? "thinking" : J.status;
  }
  // The PC can transcribe locally (faster word-for-word, no internet, doesn't cut you off at a pause).
  const localEars = () => !!(sys().wake && sys().wake.stt);
  // A short rising two-note tone: JARVIS is listening. Quieter and quicker than speaking a greeting.
  let audioCtx = null;
  function chime() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const t = audioCtx.currentTime;
      [[660, 0], [990, 0.09]].forEach(([f, at]) => {
        const o = audioCtx.createOscillator(), g = audioCtx.createGain();
        o.frequency.value = f; o.type = "sine";
        g.gain.setValueAtTime(0.0001, t + at); g.gain.exponentialRampToValueAtTime(0.18, t + at + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.16);
        o.connect(g).connect(audioCtx.destination); o.start(t + at); o.stop(t + at + 0.18);
      });
    } catch (e) { /* no sound is fine */ }
  }
  J.shown = shown;

  function onEvent(e) {
    switch (e.type) {
      case "state": merge(e.sections, e.full); return;
      case "status": J.status = e.status; J.emit("status", { ...e, shown: shown() }); if (e.status === "idle") afterTurn(); return;
      // Replies stream in a sentence at a time (say_chunk) and are spoken as they arrive;
      // "say" then carries the whole paragraph for display, already spoken if it was streamed.
      case "say_chunk": saidThisTurn = true; J.emit("say_chunk", e); say(e.text); return;
      // A slash command's output (/context, /cost …) is shown, not read aloud.
      case "say": saidThisTurn = true; J.emit("say", e); if (!e.streamed && !e.slash) say(e.text); return;
      case "reply":
        if (!saidThisTurn && e.text && !e.is_error) { J.emit("say", { text: e.text, slash: e.slash }); if (!e.slash) say(e.text); }
        if (e.is_error) {
          failedTurn = true;
          J.emit("error", { text: e.text || "JARVIS hit an error.", login: !!e.login });
          if (e.login) say(e.text);  // the owner may be talking hands-free, so say it too
        }
        return;
      case "error": failedTurn = true; J.emit("error", e); return;
      case "wake": J.wake(); return;
      // The local ears (core/jarvis/wake_worker.py) report what they're doing: listening, thinking
      // (transcribing), heard, nospeech. What was heard arrives as an "owner" event with via "voice".
      case "ears":
        J.ears = e.state;
        J.listening = e.state === "listening";
        if (e.state === "listening") chime();
        if (e.state === "nospeech" && listeningFor === "request") { voiceTurn = false; say("Sorry sir, I didn't catch that."); }
        if (e.state !== "listening" && e.state !== "thinking") listeningFor = "request";
        setStatus(J.status);
        J.emit("ears", e);
        return;
      case "owner":
        voiceTurn = e.via === "voice";  // a phone or typed message doesn't reopen the mic afterwards
        if (voiceTurn) { saidThisTurn = false; failedTurn = false; }
        J.emit("owner", e);
        return;
      default: J.emit(e.type, e);
    }
  }

  function connect() {
    const es = new EventSource("/api/events");
    es.onopen = () => { J.connected = true; J.emit("connection", { live: true }); };
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
  J.listen = async (followup) => {
    if (localEars()) {
      if (J.listening) return post("/api/listen", { cancel: true }).catch(() => {});
      cancelSpeech();
      failedTurn = false; listeningFor = followup ? "followup" : "request";
      try { return await post("/api/listen", { followup: !!followup }); } catch (e) { /* fall back to the browser */ }
    }
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
  const kokoroOn = () => !!sys().tts?.available;
  J.voiceOptions = () => [
    ...(kokoroOn() ? sys().tts.voices.map((v) => ({ id: "kokoro:" + v.id, label: `JARVIS (Kokoro), ${v.name}` })) : []),
    ...(kokoroOn() ? [] : J.voices().map((v) => ({ id: v.name, label: v.name }))),  // only without the JARVIS voice
  ];
  // George was the default before the JARVIS-tuned voice existed: move people who never chose otherwise.
  if (J.pref("speechVoice") === "kokoro:bm_george" && J.pref("voiceUpgrade") !== "1") J.pref("speechVoice", "kokoro:jarvis");
  J.pref("voiceUpgrade", "1");
  J.currentVoice = () => {
    const saved = J.settings.voice, opts = J.voiceOptions();
    if (saved && opts.some((o) => o.id === saved)) return saved;
    if (kokoroOn()) return "kokoro:" + sys().tts.voice;
    return J.bestVoice()?.name || "";
  };
  if (window.speechSynthesis) speechSynthesis.onvoiceschanged = () => J.emit("voices", J.voices());
  let ttsSeen = null;
  J.on("state", () => { const k = JSON.stringify(sys().tts || null); if (k !== ttsSeen) { ttsSeen = k; J.emit("voices", J.voices()); } });

  let speakQueue = 0, chain = Promise.resolve(), gen = 0, audio = null;
  function setSpeaking(v) { J.speaking = v; setStatus(J.status); }
  function cancelSpeech() {
    gen++; speakQueue = 0;
    window.speechSynthesis?.cancel();
    if (audio) { const a = audio; audio = null; a.pause(); }
    setSpeaking(false);
  }
  J.cancelSpeech = cancelSpeech;
  J.spoken = (t) => t.replace(/\[\[[^\]]*\]\]/g, " ").replace(/```[\s\S]*?```/g, " ").replace(/`([^`]+)`/g, "$1").replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_#>|]/g, "").replace(/\bT(\d+)\b/g, "task $1").replace(/A\$/g, "A dollars ").replace(/\s+/g, " ").trim();
  function chunks(text) {
    const out = [];
    for (const s of text.match(/[^.!?]+[.!?]*\s*/g) || [text]) {
      if (out.length && (out[out.length - 1] + s).length < 180) out[out.length - 1] += s; else out.push(s);
    }
    return out.map((c) => c.trim()).filter(Boolean);
  }
  // J.replyVoice: a voice id ("kokoro:voice_shopify") the next replies are spoken in, when the owner is talking to a
  // team or agent rather than JARVIS; null means JARVIS's own voice. J.say speaks a line now, in any voice.
  J.replyVoice = null;
  J.say = (text, voice) => say(text, voice);
  function say(text, voiceOverride) {
    const all = J.spoken(text);
    if (!J.settings.speak || !all) { J.emit("caption", { text: all, done: true }); return; }
    const voice = voiceOverride || J.replyVoice || J.currentVoice(), my = gen;
    // The JARVIS voice always, unless it isn't installed. A failed sentence stays silent (the text is on
    // screen) rather than dropping to the browser's robotic voice mid-reply.
    const kokoro = !J.sections.system || kokoroOn();
    const voiceId = voice.startsWith("kokoro:") ? voice.slice(7) : "";
    chunks(all).forEach((c) => {
      speakQueue++; setSpeaking(true);
      const wav = kokoro ? fetchWav(c, voiceId) : null;  // fetched ahead while earlier sentences play
      wav?.catch(() => {});
      chain = chain
        .then(() => (my !== gen ? null : wav ? playWav(c, wav, my) : playBrowser(c)))
        .catch(() => {})
        .then(() => {
          if (my !== gen) return;
          speakQueue = Math.max(0, speakQueue - 1);
          J.emit("caption", { text: c, done: true });
          if (!speakQueue) { setSpeaking(false); afterTurn(); }
        });
    });
  }
  async function fetchWav(text, voice, retried) {
    const r = await fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json", "X-Jarvis-Token": window.JARVIS_TOKEN },
      body: JSON.stringify({ text, voice }) });
    if (r.status === 403 && !retried && await refreshToken()) return fetchWav(text, voice, true);
    if (!r.ok) throw new Error("tts " + r.status);
    return URL.createObjectURL(await r.blob());
  }
  function playWav(text, wavPromise, my) {
    return wavPromise.then((url) => new Promise((resolve, reject) => {
      if (my !== gen) { URL.revokeObjectURL(url); return resolve(); }
      const a = audio = new Audio(url);
      tapLevel(a);
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
  // The voice's loudness, 0..1, for the orb (T86). Kokoro audio runs through an analyser; the browser's own voice
  // can't be measured, so J.level() gives a gentle synthetic pulse while it speaks.
  // Audio routed into a suspended context plays silently, so the context is only started by a tap or key press
  // (browsers allow nothing else), and a sentence is only routed through it once it is running.
  let levelCtx = null, analyser = null, levelBuf = null, freqBuf = null, tapped = null;
  const unlockAudio = () => {
    try {
      levelCtx = levelCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (levelCtx.state === "suspended") levelCtx.resume().catch(() => {});
    } catch (e) { /* no Web Audio */ }
  };
  ["pointerdown", "keydown"].forEach((t) => document.addEventListener(t, unlockAudio, { capture: true, passive: true }));
  function tapLevel(a) {
    tapped = null;
    try {
      if (!levelCtx || levelCtx.state !== "running") return;
      if (!analyser) {
        analyser = levelCtx.createAnalyser(); analyser.fftSize = 512; analyser.smoothingTimeConstant = 0.6;
        analyser.connect(levelCtx.destination);
        levelBuf = new Uint8Array(analyser.fftSize); freqBuf = new Uint8Array(analyser.frequencyBinCount);
      }
      levelCtx.createMediaElementSource(a).connect(analyser);
      tapped = a;
    } catch (e) { /* no Web Audio: the orb falls back to the synthetic pulse */ }
  }
  // The voice's spectrum (0..255 per band, low to high), or null when it can't be measured.
  J.spectrum = () => {
    if (!J.speaking || !audio || audio !== tapped || audio.paused) return null;
    analyser.getByteFrequencyData(freqBuf);
    return freqBuf;
  };
  J.level = () => {
    if (!J.speaking) return 0;
    if (audio && audio === tapped && !audio.paused) {
      analyser.getByteTimeDomainData(levelBuf);
      let sum = 0;
      for (let i = 0; i < levelBuf.length; i++) { const v = (levelBuf[i] - 128) / 128; sum += v * v; }
      return Math.min(1, Math.sqrt(sum / levelBuf.length) * 4);
    }
    const t = performance.now() / 1000;
    return 0.35 + 0.25 * Math.abs(Math.sin(t * 7.3) * Math.sin(t * 2.1));
  };
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
  // Conversation: after JARVIS answers something you said, he listens for a follow-up (no wake word
  // needed) when "Hands-free" is on, or always after a "Hey JARVIS" turn. Silence ends it.
  function afterTurn() {
    if (failedTurn) return;  // don't keep reopening the mic after an error
    const converse = J.settings.handsfree || (voiceTurn && localEars());
    if (converse && J.status === "idle" && !J.speaking && !J.listening) setTimeout(() => {
      if (J.status === "idle" && !J.speaking && !J.listening) J.listen(true);
    }, 300);
  }

  // ---------------------------------------------------------- "Hey JARVIS"
  // The dashboard server runs a local wake-word listener (openWakeWord, core/jarvis/wakeword.py) that
  // only listens for "Hey JARVIS": nothing is recorded or transcribed until it hears it. It sends a
  // "wake" event; JARVIS greets the owner like the film's JARVIS, gives a one-line status, then
  // listens for the request. The listener is paused while JARVIS is speaking or already listening.
  let lastPause = null;
  function syncWakePause() {
    const paused = J.speaking;  // while JARVIS talks, only a clear "Hey JARVIS" interrupts him
    if (paused !== lastPause) { lastPause = paused; post("/api/wake", { paused }).catch(() => {}); }
  }
  J.on("status", syncWakePause);
  J.greeting = () => {
    const k = J.sections.kpis, h = new Date().getHours();
    const part = h < 5 ? "evening" : h < 12 ? "morning" : h < 18 ? "afternoon" : "evening";
    const open = [`Good ${part}, sir.`, `Welcome home, sir.`, `At your service, sir.`, `Good ${part}, sir. All systems are online.`];
    const bits = [open[Math.floor(Math.random() * open.length)]];
    if (k) {
      if (k.inbox) bits.push(`${k.inbox} thing${k.inbox === 1 ? " is" : "s are"} waiting for your decision.`);
      bits.push(k.agents_live ? `${k.agents_live} of the team ${k.agents_live === 1 ? "is" : "are"} at work.` : "The team is standing by.");
    }
    bits.push("What can I do for you?");
    return bits.join(" ");
  };
  J.wake = () => {
    if (localEars()) {  // the PC is already recording the request; stop talking and show we're listening
      cancelSpeech();
      voiceTurn = true;
      J.emit("awake", {});
      return;
    }
    if (J.speaking || J.listening || J.status !== "idle") return;
    J.emit("awake", {});
    say("Yes, sir?");
    if (!J.canListen) return;
    const t0 = Date.now();
    const wait = setInterval(() => {  // listen as soon as the greeting has been spoken
      if (!J.speaking || Date.now() - t0 > 20000) { clearInterval(wait); if (!J.listening && J.status === "idle") J.listen(); }
    }, 250);
  };
  Object.defineProperty(J.settings, "wake", {
    get() { return !!(sys().wake && sys().wake.enabled); },
    set(v) { post("/api/wake", { on: !!v }).then(() => J.refresh()).catch((e) => J.emit("error", { text: e.message })); },
  });

  document.addEventListener("DOMContentLoaded", connect);
})();
