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
};

function displayTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function errorMessage(reason: unknown): string {
  const message = reason instanceof Error ? reason.message : String(reason);
  return message.replace(/^Error invoking remote method '[^']+': Error:\s*/, "");
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
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const [clock, setClock] = useState(new Date());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const recognition = useRef<{ stop(): void } | null>(null);

  useEffect(() => {
    let mounted = true;
    void window.jarvis.getSnapshot().then((value) => mounted && setSnapshot(value)).catch((reason) => setError(String(reason)));
    const unsubscribe = window.jarvis.onSnapshot(setSnapshot);
    const timer = window.setInterval(() => setClock(new Date()), 1_000);
    return () => {
      mounted = false;
      unsubscribe();
      window.clearInterval(timer);
      recognition.current?.stop();
    };
  }, []);

  const currentTask = useMemo(
    () => snapshot.tasks.find((task) => ["running", "waiting_approval", "queued"].includes(task.status)) ?? snapshot.tasks[0],
    [snapshot.tasks],
  );

  async function submit(nextCommand = command) {
    const clean = nextCommand.trim();
    if (!clean) return;
    setError("");
    try {
      await window.jarvis.submit(clean);
      setCommand("");
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  function toggleVoice() {
    if (listening) {
      recognition.current?.stop();
      return;
    }
    const SpeechRecognition = window.webkitSpeechRecognition;
    if (!SpeechRecognition || !snapshot.system.voiceAvailable) {
      setError("Voice is disabled until VITE_ENABLE_VOICE=true and a supported Windows speech service is available.");
      return;
    }
    const next = new SpeechRecognition();
    next.continuous = false;
    next.interimResults = false;
    next.lang = "en-AU";
    next.onresult = (event) => {
      const transcript = event.results[0]?.[0]?.transcript ?? "";
      setCommand(transcript);
      if (transcript) void submit(transcript);
    };
    next.onerror = (event) => setError("Voice error: " + event.error);
    next.onend = () => setListening(false);
    recognition.current = next;
    setListening(true);
    next.start();
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
  const headline = listening
    ? "VOICE CHANNEL ACTIVE"
    : snapshot.system.status === "active"
      ? "EXECUTING TASK"
      : snapshot.system.status === "awaiting_approval"
        ? "HUMAN DECISION REQUIRED"
        : snapshot.system.status === "stopped"
          ? "ALL CONTROL PAUSED"
          : "READY FOR INSTRUCTION";

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
            <strong>{listening ? "Listening…" : currentTask?.title ?? "Standing by"}</strong>
          </div>
        </section>

        <section className="task-panel">
          <div className="section-heading">
            <div><span>01</span><div><small>CURRENT OPERATION</small><h2>{currentTask ? currentTask.title : "No active task"}</h2></div></div>
            {currentTask && <b className={"status status--" + currentTask.status}>{currentTask.status.replace("_", " ")}</b>}
          </div>
          <div className="progress"><i style={{ width: String(currentTask?.progress ?? 0) + "%" }}/></div>
          <div className="task-panel__body">
            <div><span className="metric-label">PROGRESS</span><strong className="metric-value">{currentTask?.progress ?? 0}%</strong></div>
            <div className="task-result"><span className="metric-label">LATEST RESULT</span><p>{currentTask?.error ?? currentTask?.result ?? currentTask?.actions[0]?.label ?? "Waiting for a command."}</p></div>
          </div>
        </section>

        <section className="command-grid">
          {quickCommands[section].map((item, index) => (
            <button key={item.command} onClick={() => void submit(item.command)} disabled={snapshot.system.emergencyStop}>
              <span>0{index + 1}</span>
              <div><strong>{item.label}</strong><small>{item.note}</small></div>
              <em>↗</em>
            </button>
          ))}
        </section>

        <form className="command-bar" onSubmit={onSubmit}>
          <button type="button" className={listening ? "voice voice--active" : "voice"} onClick={toggleVoice} aria-label="Toggle voice command">◉</button>
          <div>
            <label htmlFor="command">ASK JARVIS</label>
            <input id="command" value={command} onChange={(event) => setCommand(event.target.value)} placeholder="Tell me what you want, e.g. “close Spotify”" disabled={snapshot.system.emergencyStop} autoFocus/>
          </div>
          <button type="submit" disabled={!command.trim() || snapshot.system.emergencyStop}>EXECUTE <span>↗</span></button>
        </form>
        {error && <div className="error-banner"><span>!</span>{error}<button onClick={() => setError("")}>×</button></div>}
      </main>

      <aside className="activity-panel">
        <div className="activity-panel__header">
          <div><small>REAL-TIME FEED</small><h2>Computer activity</h2></div>
          <span className="live-badge">LIVE</span>
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

