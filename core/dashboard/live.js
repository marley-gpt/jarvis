// Live updates (core/jarvis/live.py): the server adds this to every page. When the dashboard's files change,
// or JARVIS restarts onto new code, the window reloads itself, but never while the owner is typing,
// talking or waiting on a reply.
(() => {
  let seen = null;
  const busy = () => {
    const j = window.J;
    if (j && (j.speaking || j.listening || (j.status && j.status !== "idle"))) return true;
    const el = document.activeElement;
    return !!(el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT") && el.value);
  };
  async function check() {
    try {
      const r = await fetch("/api/live", { cache: "no-store" });
      if (r.ok) {
        const v = await r.json(), now = `${v.boot}/${v.ui}`;
        if (seen === null) seen = now;
        else if (now !== seen && !busy()) return location.reload();
      }
    } catch (_) { /* the server is restarting; keep asking */ }
    setTimeout(check, 1500);
  }
  check();
})();
