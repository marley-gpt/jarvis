import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ActivityEvent, JarvisTask, Snapshot } from "./types";

const emptySnapshot: Snapshot = {
  system: {
    status: "idle",
    emergencyStop: false,
    voiceAvailable: false,
    allowedRoots: [],
    allowedOrigins: [],
    budgetAud: 50,
    spentAud: 0,
    brainConfigured: false,
    brainVerified: false,
    brainModel: "gpt-6-luna",
    brainSpentAud: 0,
    brainMonthlyLimitAud: 45,
    update: { checking: false, available: false, canInstall: false, currentVersion: "0.2.0", message: "Checking updates…" },
  },
  tasks: [], approvals: [], activity: [],
};

const navItems = [
  { id: "brain", icon: "◉", label: "Brain" },
  { id: "files", icon: "⌑", label: "Files" },
  { id: "skills", icon: "◇", label: "Skills" },
  { id: "creative", icon: "✦", label: "Creative Studio" },
  { id: "meta", icon: "M", label: "Meta Ads" },
  { id: "connections", icon: "⌘", label: "Connections" },
] as const;

type Section = (typeof navItems)[number]["id"];

const quickCommands: Record<Section, { label: string; command: string; note: string }[]> = {
  brain: [
    { label: "System status", command: "status", note: "Check the control core and interlocks" },
    { label: "Open GitHub", command: "open https://github.com", note: "Launch the authorised browser" },
  ],
  files: [
    { label: "Inspect workspace", command: "list files in %USERPROFILE%\\Documents\\JarvisWorkspace", note: "Read-only file listing" },
    { label: "Organise inbox", command: "organise files in %USERPROFILE%\\Documents\\JarvisWorkspace\\Inbox", note: "Confirmation required before moving" },
  ],
  skills: [
    { label: "Open any app", command: "open notepad", note: "Try Chrome, Spotify, Excel, or another Start-menu app" },
    { label: "Close an app", command: "close notepad", note: "Unknown apps ask once to protect unsaved work" },
  ],
  creative: [
    { label: "Product listing draft", command: "run workflow product-listing-draft", note: "Review-only listing workflow" },
    { label: "Open Paint", command: "open paint", note: "Launch a local creative tool" },
  ],
  meta: [
    { label: "Review Meta Ads", command: "run workflow meta-ads-review", note: "No publishing or spend" },
    { label: "Open Ads Manager", command: "open https://business.facebook.com/adsmanager", note: "Authorised origin only" },
  ],
  connections: [
    { label: "Open GitHub", command: "open https://github.com", note: "Sign in inside the controlled browser" },
    { label: "Open Shopify", command: "open https://admin.shopify.com", note: "Sign in inside the controlled browser" },
  ],
};

type VoiceState = "idle" | "listening" | "transcribing" | "thinking" | "speaking";

function displayTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function errorMessage(reason: unknown): string {
  const message = reason instanceof Error ? reason.message : String(reason);
  return message.replace(/^Error invoking remote method '[^']+': Error:\s*/, "");
}

function elapsedLabel(startedAt: string, now: Date): string {
  const seconds = Math.max(0, Math.floor((now.getTime() - new Date(startedAt).getTime()) / 1_000));
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}m ${String(seconds % 60).padStart(2, "0")}s` : `${seconds}s`;
}

function Brain({ state, listening }: { state: Snapshot["system"]["status"]; listening: boolean }) {
  return (
    <div className={"brain brain--" + state + (listening ? " brain--listening" : "")} aria-label={"JARVIS brain: " + state}>
      <div className="brain__orbit brain__orbit--outer"><i/><i/><i/></div>
      <div className="brain__orbit brain__orbit--middle"><i/><i/></div>
      <div className="brain__core"><span className="brain__monogram">J</span><span className="brain__scan"/></div>
      <div className="brain__wave" aria-hidden="true">{Array.from({ length: 11 }, (_, index) => <b key={index}/>)}</div>
    </div>
  );
}

function ActivityRow({ item }: { item: ActivityEvent }) {
  return (
    <div className={"activity activity--" + item.level}>
      <span className="activity__rail"/>
      <div>
        <div className="activity__meta"><span>{item.source}</span><time>{displayTime(item.time)}</time></div>
        <p>{item.message}</p>
      </div>
    </div>
  );
}

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot>(emptySnapshot);
  const [section, setSection] = useState<Section>("brain");
  const [command, setCommand] = useState("");
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [voiceTaskId, setVoiceTaskId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(new Date());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const microphoneStream = useRef<MediaStream | null>(null);
  const voiceChunks = useRef<Blob[]>([]);
  const voiceStartedAt = useRef(0);
  const voiceTimeout = useRef<number | null>(null);
  const spokenUpdates = useRef(new Set<string>());

  useEffect(() => {
    let mounted = true;
    void window.jarvis.getSnapshot().then((value) => mounted && setSnapshot(value)).catch((reason) => setError(String(reason)));
    const unsubscribe = window.jarvis.onSnapshot(setSnapshot);
    const timer = window.setInterval(() => setClock(new Date()), 1_000);
    return () => {
      mounted = false;
      unsubscribe();
      window.clearInterval(timer);
      if (voiceTimeout.current) window.clearTimeout(voiceTimeout.current);
      if (recorder.current && recorder.current.state !== "inactive") {
        recorder.current.onstop = null;
        recorder.current.stop();
      }
      microphoneStream.current?.getTracks().forEach((track) => track.stop());
      window.speechSynthesis?.cancel();
    };
  }, []);

  const currentTask = useMemo(
    () => snapshot.tasks.find((task) => ["running", "waiting_approval", "queued"].includes(task.status)) ?? snapshot.tasks[0],
    [snapshot.tasks],
  );

  const currentAction = currentTask?.actions.find((action) => action.status === "running") ?? currentTask?.actions[0];
  const listening = voiceState === "listening";

  useEffect(() => {
    if (!voiceTaskId) return;
    const task = snapshot.tasks.find((item) => item.id === voiceTaskId);
    if (!task) return;

    if (task.status === "waiting_approval") {
      const key = `${task.id}:approval`;
      if (!spokenUpdates.current.has(key)) {
        spokenUpdates.current.add(key);
        speak("I need your approval before I continue.");
      }
      return;
    }

    if (["completed", "failed", "cancelled"].includes(task.status)) {
      const key = `${task.id}:finished`;
      if (spokenUpdates.current.has(key)) return;
      spokenUpdates.current.add(key);
      const message = task.status === "completed"
        ? task.result ?? "Task completed."
        : `Task ${task.status}. ${task.error ?? ""}`;
      speak(message);
      setVoiceTaskId(null);
    }
  }, [snapshot.tasks, voiceTaskId]);

  function speak(text: string) {
    const clean = text.replace(/[•*_#]/g, " ").replace(/\s+/g, " ").trim().slice(0, 500);
    if (!clean || !("speechSynthesis" in window)) {
      setVoiceState("idle");
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(clean);
    const voices = window.speechSynthesis.getVoices();
    utterance.voice = voices.find((voice) => voice.lang.toLowerCase() === "en-au")
      ?? voices.find((voice) => voice.lang.toLowerCase().startsWith("en"))
      ?? null;
    utterance.rate = 0.96;
    utterance.pitch = 0.94;
    utterance.onend = () => setVoiceState("idle");
    utterance.onerror = () => setVoiceState("idle");
    setVoiceState("speaking");
    window.speechSynthesis.speak(utterance);
  }

  async function submit(nextCommand = command, fromVoice = false): Promise<string | null> {
    const clean = nextCommand.trim();
    if (!clean) return null;
    setError("");
    try {
      const result = await window.jarvis.submit(clean);
      setCommand("");
      if (fromVoice) {
        setVoiceTaskId(result.taskId);
        setVoiceState("thinking");
      }
      return result.taskId;
    } catch (reason) {
      setError(errorMessage(reason));
      if (fromVoice) setVoiceState("idle");
      return null;
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  async function cancelCurrentTask() {
    if (!currentTask || !["queued", "running", "waiting_approval"].includes(currentTask.status)) return;
    setError("");
    try {
      await window.jarvis.cancelTask(currentTask.id);
      if (voiceTaskId === currentTask.id) {
        window.speechSynthesis.cancel();
        setVoiceTaskId(null);
        setVoiceState("idle");
      }
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  function stopVoiceCapture() {
    if (voiceTimeout.current) {
      window.clearTimeout(voiceTimeout.current);
      voiceTimeout.current = null;
    }
    if (recorder.current?.state === "recording") recorder.current.stop();
  }

  async function toggleVoice() {
    if (voiceState === "listening") {
      stopVoiceCapture();
      return;
    }
    if (voiceState === "speaking") {
      window.speechSynthesis.cancel();
      setVoiceState("idle");
      return;
    }
    if (voiceState !== "idle") return;
    if (!snapshot.system.brainVerified) {
      setSettingsError("Connect and test your OpenAI API key before using voice.");
      setSettingsOpen(true);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("This Windows microphone is not available to JARVIS.");
      return;
    }

    setError("");
    try {
      window.speechSynthesis.cancel();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      microphoneStream.current = stream;
      voiceChunks.current = [];
      const supportedType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
        .find((type) => MediaRecorder.isTypeSupported(type));
      const nextRecorder = new MediaRecorder(stream, supportedType ? { mimeType: supportedType } : undefined);
      recorder.current = nextRecorder;
      nextRecorder.ondataavailable = (event) => {
        if (event.data.size) voiceChunks.current.push(event.data);
      };
      nextRecorder.onerror = () => {
        microphoneStream.current?.getTracks().forEach((track) => track.stop());
        setVoiceState("idle");
        setError("Microphone recording failed. Check Windows microphone privacy settings.");
      };
      nextRecorder.onstop = async () => {
        if (voiceTimeout.current) window.clearTimeout(voiceTimeout.current);
        microphoneStream.current?.getTracks().forEach((track) => track.stop());
        const durationMs = Date.now() - voiceStartedAt.current;
        const blob = new Blob(voiceChunks.current, { type: nextRecorder.mimeType || "audio/webm" });
        if (durationMs < 300 || blob.size < 100) {
          setVoiceState("idle");
          setError("I did not catch any speech. Click the microphone, speak, then click it again.");
          return;
        }
        setVoiceState("transcribing");
        try {
          const transcript = await window.jarvis.transcribeAudio(await blob.arrayBuffer(), blob.type || "audio/webm", durationMs);
          setCommand(transcript);
          await submit(transcript, true);
        } catch (reason) {
          setVoiceState("idle");
          setError(errorMessage(reason));
        }
      };
      voiceStartedAt.current = Date.now();
      nextRecorder.start(250);
      setVoiceState("listening");
      voiceTimeout.current = window.setTimeout(stopVoiceCapture, 30_000);
    } catch (reason) {
      setVoiceState("idle");
      setError(reason instanceof DOMException && reason.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow microphone access for JARVIS in Windows Settings, then try again."
        : errorMessage(reason));
    }
  }

  async function toggleEmergencyStop() {
    setError("");
    try {
      if (snapshot.system.emergencyStop) await window.jarvis.resetEmergencyStop();
      else await window.jarvis.emergencyStop();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  async function saveBrain() {
    if (!apiKey.trim()) {
      setSettingsError("Paste your complete OpenAI API key first.");
      return;
    }
    setSavingSettings(true);
    setSettingsError("");
    try {
      const next = await window.jarvis.configureBrain(apiKey.trim());
      setSnapshot(next);
      setApiKey("");
      setSettingsOpen(false);
    } catch (reason) {
      setSettingsError(errorMessage(reason));
    } finally {
      setSavingSettings(false);
    }
  }

  async function testBrain() {
    setSavingSettings(true);
    setSettingsError("");
    try {
      const next = await window.jarvis.testBrain();
      setSnapshot(next);
    } catch (reason) {
      setSettingsError(errorMessage(reason));
    } finally {
      setSavingSettings(false);
    }
  }

  async function disconnectBrain() {
    setSavingSettings(true);
    setSettingsError("");
    try {
      const next = await window.jarvis.configureBrain(null);
      setSnapshot(next);
      setApiKey("");
    } catch (reason) {
      setSettingsError(errorMessage(reason));
    } finally {
      setSavingSettings(false);
    }
  }

  const sectionName = navItems.find((item) => item.id === section)?.label ?? "Brain";
  const headline = voiceState === "listening"
    ? "LISTENING — CLICK MIC TO FINISH"
    : voiceState === "transcribing"
      ? "TRANSCRIBING YOUR VOICE"
      : voiceState === "thinking"
        ? "VOICE TASK IN PROGRESS"
        : voiceState === "speaking"
          ? "JARVIS IS SPEAKING"
    : snapshot.system.status === "active"
      ? "EXECUTING TASK"
      : snapshot.system.status === "awaiting_approval"
        ? "HUMAN DECISION REQUIRED"
        : snapshot.system.status === "stopped"
          ? "ALL CONTROL PAUSED"
          : "READY FOR INSTRUCTION";
  const heroMessage = voiceState === "listening"
    ? "Speak naturally. Recording stops after 30 seconds."
    : voiceState === "transcribing"
      ? "Turning speech into a visible command…"
      : voiceState === "speaking"
        ? "Click the microphone to stop the voice."
        : currentAction?.label ?? currentTask?.title ?? "Standing by";
  const connections = [
    { id: "openai", name: "OpenAI brain", status: snapshot.system.brainVerified ? "CONNECTED" : "SETUP", detail: snapshot.system.brainVerified ? `${snapshot.system.brainModel} · encrypted key` : "Conversation and voice need a tested API key.", command: "" },
    { id: "windows", name: "Windows apps", status: "READY", detail: "Launch installed Start-menu apps; close actions use the safety layer.", command: "" },
    { id: "files", name: "Local files", status: "RESTRICTED", detail: `${snapshot.system.allowedRoots.length} authorised root${snapshot.system.allowedRoots.length === 1 ? "" : "s"} · no access elsewhere`, command: "" },
    { id: "browser", name: "Controlled browser", status: "READY", detail: `${snapshot.system.allowedOrigins.length} approved web origins · sign-ins stay in its local profile`, command: "" },
    { id: "github", name: "GitHub", status: "SIGN IN", detail: "Repository, code, and release workflows through the controlled browser.", command: "open https://github.com" },
    { id: "shopify", name: "Shopify", status: "SIGN IN", detail: "Store admin workflows; publishing still requires confirmation.", command: "open https://admin.shopify.com" },
    { id: "meta", name: "Meta Ads", status: "SIGN IN", detail: "Ads Manager review; publishing and spend remain confirmation-gated.", command: "open https://business.facebook.com/adsmanager" },
    { id: "google", name: "Google", status: "SIGN IN", detail: "Use the controlled browser for approved Google workflows.", command: "open https://www.google.com" },
  ];

  return (
    <div className={"app app--" + snapshot.system.status}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand__mark"><span>J</span></div>
          <div><strong>JARVIS</strong><small>CONTROL OS</small></div>
        </div>

        <div className="sidebar__label">SYSTEMS</div>
        <nav>
          {navItems.map((item) => (
            <button key={item.id} className={section === item.id ? "nav-item nav-item--active" : "nav-item"} onClick={() => setSection(item.id)}>
              <span className="nav-item__icon">{item.icon}</span>
              <span>{item.label}</span>
              {item.id === "brain" && <em className={snapshot.system.status === "idle" ? "" : "is-live"}/>}
            </button>
          ))}
        </nav>

        <div className="trust-card">
          <div className="trust-card__top"><span>AI BRAIN</span><strong>{snapshot.system.brainVerified ? "CONNECTED" : snapshot.system.brainConfigured ? "CHECK NEEDED" : "SETUP NEEDED"}</strong></div>
          <p>{snapshot.system.brainVerified ? snapshot.system.brainModel + " · tested and ready" : snapshot.system.brainConfigured ? "A key is saved. Test it in Settings." : "Basic commands work now. Connect OpenAI for natural conversation."}</p>
          <div className="budget"><span>Monthly ceiling</span><strong>{"A$" + snapshot.system.budgetAud}</strong></div>
          <div className="budget__track"><i style={{ width: String(Math.min(100, snapshot.system.spentAud / snapshot.system.budgetAud * 100)) + "%" }}/></div>
          <small>{"A$" + snapshot.system.spentAud.toFixed(2) + " recorded spend"}</small>
        </div>

        <button className={snapshot.system.emergencyStop ? "stop stop--reset" : "stop"} onClick={() => void toggleEmergencyStop()}>
          <span>{snapshot.system.emergencyStop ? "↻" : "■"}</span>
          {snapshot.system.emergencyStop ? "RESET INTERLOCK" : "EMERGENCY STOP"}
        </button>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <span className={"system-dot system-dot--" + snapshot.system.status}/>
            <strong>{snapshot.system.status === "stopped" ? "INTERLOCK ENGAGED" : "SYSTEM ONLINE"}</strong>
            <small>{snapshot.system.status.replace("_", " ")}</small>
          </div>
          <div className="topbar__clock">
            <strong>{clock.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</strong>
            <small>{clock.toLocaleDateString([], { weekday: "short", day: "2-digit", month: "short" }).toUpperCase()}</small>
          </div>
          <button className="settings-button" onClick={() => { setSettingsError(""); setSettingsOpen(true); }}>⚙ Settings</button>
        </header>

        {!snapshot.system.brainVerified && (
          <section className="setup-banner">
            <div><strong>{snapshot.system.brainConfigured ? "Verify the saved OpenAI connection" : "Make JARVIS conversational"}</strong><span>{snapshot.system.brainConfigured ? "The saved key has not passed a live API test yet." : "Add your OpenAI API key once. It is encrypted on this Windows account."}</span></div>
            <button onClick={() => { setSettingsError(""); setSettingsOpen(true); }}>{snapshot.system.brainConfigured ? "Test brain" : "Connect brain"}</button>
          </section>
        )}

        <section className="hero">
          <div className="hero__eyebrow"><span>{navItems.find((item) => item.id === section)?.icon}</span>{section === "brain" ? "NEURAL COMMAND CORE" : sectionName.toUpperCase()}</div>
          <Brain state={snapshot.system.status} listening={listening}/>
          <div className="hero__state">
            <small>{headline}</small>
            <strong>{heroMessage}</strong>
          </div>
        </section>

        <section className="task-panel">
          <div className="section-heading">
            <div><span>01</span><div><small>CURRENT OPERATION</small><h2>{currentTask ? currentTask.title : "No active task"}</h2></div></div>
            {currentTask && <div className="task-state-actions"><b className={"status status--" + currentTask.status}>{currentTask.status.replace("_", " ")}</b>{["queued", "running", "waiting_approval"].includes(currentTask.status) && <button onClick={() => void cancelCurrentTask()}>Cancel task</button>}</div>}
          </div>
          <div className={currentTask?.status === "running" ? "progress progress--live" : "progress"}><i style={{ width: String(currentTask?.progress ?? 0) + "%" }}/></div>
          <div className="task-panel__body">
            <div><span className="metric-label">PROGRESS</span><strong className="metric-value">{currentTask?.progress ?? 0}%</strong><small className="elapsed">{currentTask ? elapsedLabel(currentTask.createdAt, clock) : "0s"}</small></div>
            <div className="task-result">
              <span className="metric-label">{currentTask?.status === "running" ? "NOW WORKING ON" : "LATEST RESULT"}</span>
              <p>{currentTask?.error ?? currentTask?.result ?? currentAction?.label ?? "Waiting for a command."}</p>
              {currentTask?.status === "running" && <div className="working-signal"><i/><span>Live execution</span></div>}
            </div>
          </div>
          {currentTask && currentTask.actions.length > 0 && (
            <div className="action-timeline">
              {[...currentTask.actions].reverse().slice(-4).map((action) => (
                <div className={"action-step action-step--" + action.status} key={action.id}>
                  <span>{action.status === "completed" ? "✓" : action.status === "failed" ? "×" : "●"}</span>
                  <strong>{action.label}</strong>
                  <small>{action.status === "running" ? elapsedLabel(action.startedAt, clock) : action.status.replace("_", " ")}</small>
                </div>
              ))}
            </div>
          )}
        </section>

        {section === "connections" ? (
          <section className="connection-hub" aria-label="JARVIS connections">
            <div className="connection-hub__header"><div><small>CONTROLLED ACCESS</small><h2>Connections</h2></div><p>Browser sign-ins stay local. External publishing, spending, deletion, and significant changes still stop for approval.</p></div>
            <div className="connection-grid">
              {connections.map((item) => (
                <article className="connection-card" key={item.id}>
                  <div><span className={"connection-dot connection-dot--" + item.status.toLowerCase().replace(" ", "-")}/><strong>{item.name}</strong><b>{item.status}</b></div>
                  <p>{item.detail}</p>
                  {item.id === "openai"
                    ? <button onClick={() => { setSettingsError(""); setSettingsOpen(true); }}>{snapshot.system.brainVerified ? "Manage" : "Connect"}</button>
                    : item.command
                      ? <button onClick={() => void submit(item.command)} disabled={snapshot.system.emergencyStop}>Open / sign in</button>
                      : <button disabled>Protected and ready</button>}
                </article>
              ))}
            </div>
          </section>
        ) : (
          <section className="command-grid">
            {quickCommands[section].map((item, index) => (
              <button key={item.command} onClick={() => void submit(item.command)} disabled={snapshot.system.emergencyStop}>
                <span>0{index + 1}</span>
                <div><strong>{item.label}</strong><small>{item.note}</small></div>
                <em>↗</em>
              </button>
            ))}
          </section>
        )}

        <form className="command-bar" onSubmit={onSubmit}>
          <button type="button" className={"voice voice--" + voiceState} onClick={() => void toggleVoice()} disabled={["transcribing", "thinking"].includes(voiceState)} aria-label={listening ? "Stop and send voice command" : voiceState === "speaking" ? "Stop JARVIS speaking" : "Start voice command"}>{listening ? "■" : voiceState === "transcribing" ? "…" : voiceState === "thinking" ? "⌁" : voiceState === "speaking" ? ")))" : "●"}</button>
          <div>
            <label htmlFor="command">{voiceState === "idle" ? "ASK JARVIS · CLICK MIC TO TALK" : headline}</label>
            <input id="command" value={command} onChange={(event) => setCommand(event.target.value)} placeholder={listening ? "Listening… click the microphone when finished" : voiceState === "transcribing" ? "Transcribing…" : voiceState === "thinking" ? "JARVIS is working…" : "Tell me what you want, e.g. “close Spotify”"} disabled={snapshot.system.emergencyStop} autoFocus/>
          </div>
          <button type="submit" disabled={!command.trim() || snapshot.system.emergencyStop}>EXECUTE <span>↗</span></button>
        </form>
        {error && <div className="error-banner"><span>!</span>{error}<button onClick={() => setError("")}>×</button></div>}
      </main>

      <aside className="activity-panel">
        <div className="activity-panel__header">
          <div><small>REAL-TIME FEED</small><h2>Computer activity</h2></div>
          <span className="live-badge">LIVE + SAVED</span>
        </div>

        {snapshot.approvals.length > 0 && (
          <div className="approval-stack">
            {snapshot.approvals.map((approval) => (
              <article className="approval" key={approval.id}>
                <div className="approval__flag">CONFIRMATION REQUIRED</div>
                <h3>{approval.summary}</h3>
                <pre>{approval.detail}</pre>
                <div>
                  <button onClick={() => void window.jarvis.respondToApproval(approval.id, false)}>Decline</button>
                  <button className="approve" onClick={() => void window.jarvis.respondToApproval(approval.id, true)}>Approve once</button>
                </div>
              </article>
            ))}
          </div>
        )}

        <div className="activity-list">
          {snapshot.activity.length
            ? snapshot.activity.map((item) => <ActivityRow item={item} key={item.id}/>)
            : <div className="empty">No activity yet.</div>}
        </div>

        <div className="access-summary">
          <div><span>FILE ACCESS</span><strong>{snapshot.system.allowedRoots.length ? "RESTRICTED" : "LOCKED"}</strong></div>
          <div><span>APP CONTROL</span><strong>START MENU</strong></div>
          <div><span>PUBLISH / SPEND</span><strong>CONFIRM</strong></div>
          <div><span>AUDIT LOG</span><strong>LOCAL + ON</strong></div>
        </div>
      </aside>

      {settingsOpen && (
        <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setSettingsOpen(false)}>
          <section className="settings-modal" role="dialog" aria-modal="true" aria-label="JARVIS settings">
            <div className="settings-modal__header">
              <div><small>JARVIS SETUP</small><h2>Settings</h2></div>
              <button onClick={() => setSettingsOpen(false)} aria-label="Close settings">×</button>
            </div>

            <div className="settings-card">
              <div className="settings-card__title"><span className={snapshot.system.brainVerified ? "connected-dot" : "connected-dot off"}/><div><strong>OpenAI brain</strong><small>{snapshot.system.brainVerified ? "Tested · " + snapshot.system.brainModel : snapshot.system.brainConfigured ? "Saved, not tested" : "Not connected"}</small></div></div>
              <p>Your key stays encrypted on this Windows account. Connect & test makes one tiny API request before the key is saved. A ChatGPT subscription does not automatically include API credits.</p>
              <label htmlFor="api-key">OpenAI API key</label>
              <input id="api-key" type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={snapshot.system.brainConfigured ? "Key saved securely" : "sk-…"} autoComplete="off"/>
              {settingsError && <div className="settings-error"><span>!</span><p>{settingsError}</p></div>}
              <div className="settings-actions">
                {snapshot.system.brainConfigured && <button className="secondary" onClick={() => void disconnectBrain()} disabled={savingSettings}>Disconnect</button>}
                {snapshot.system.brainConfigured && <button className="secondary" onClick={() => void testBrain()} disabled={savingSettings}>Test saved key</button>}
                <button className="primary" onClick={() => void saveBrain()} disabled={savingSettings || !apiKey.trim()}>{savingSettings ? "Testing…" : "Connect & test"}</button>
              </div>
              <small className="cost-note">AI usage this month: A${snapshot.system.brainSpentAud.toFixed(4)} · JARVIS pauses AI at A${snapshot.system.brainMonthlyLimitAud.toFixed(0)}.</small>
            </div>

            <div className="settings-card">
              <div className="settings-card__title"><span className={snapshot.system.brainVerified ? "connected-dot" : "connected-dot off"}/><div><strong>Voice control</strong><small>{snapshot.system.brainVerified ? "Push-to-talk ready" : "Waiting for OpenAI connection"}</small></div></div>
              <p>Click the microphone once to listen and again to send. Recordings are capped at 30 seconds, transcribed through the encrypted API connection, and never saved by JARVIS. Spoken replies use a synthetic Windows voice.</p>
              <small className="cost-note">Transcription is tracked in the same A$45 AI safety budget. Current API pricing is approximately A$0.0075 per recorded minute.</small>
            </div>

            <div className="settings-card">
              <div className="settings-card__title"><span className="connected-dot"/><div><strong>Safety interlocks</strong><small>Always active</small></div></div>
              <p>Publishing, ad spend, deletion, updates, unknown app closure, and significant file changes pause for approval. File access remains limited to the authorised roots shown in Connections.</p>
            </div>

            <div className="settings-card">
              <div className="settings-card__title"><span className={snapshot.system.update.available ? "update-dot ready" : "update-dot"}/><div><strong>Automatic updates</strong><small>Current version {snapshot.system.update.currentVersion}</small></div></div>
              <p>{snapshot.system.update.message}</p>
              <div className="settings-actions">
                <button className="secondary" onClick={() => void window.jarvis.checkForUpdates()} disabled={snapshot.system.update.checking}>{snapshot.system.update.checking ? "Checking…" : "Check now"}</button>
                {snapshot.system.update.available && snapshot.system.update.canInstall && <button className="primary" onClick={() => { setSettingsOpen(false); void submit("install update"); }}>Install update</button>}
              </div>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

