# Security model

JARVIS treats the dashboard as an untrusted command surface and the Electron main process as the enforcement boundary.

## Guardrails

- The renderer uses context isolation, sandboxing, and no Node integration.
- The preload bridge exposes a small typed IPC API only.
- Commands map to explicit operations; user text is never passed to a shell.
- Applications are selected from a static allowlist.
- Filesystem targets are canonicalised and checked against allowed roots.
- Browser origins are checked before Playwright navigation.
- Deletes, paid actions, publishing, and high-impact changes require one-time approval.
- Emergency stop aborts active tasks and closes the automation browser.
- Audit events record task, action, result, error, and timestamp.
- Playwright uses a dedicated local browser profile and does not accept downloads.

## Operator guidance

Run JARVIS as a standard Windows user. Keep allowed roots narrow. Use a dedicated browser profile. Never store passwords, cookies, payment details, recovery codes, or API secrets in workflow files.

This is an operator-controlled local agent, not endpoint-management or sandbox software. Website flows can change, so verify each proposed publish or spend action in the target service before approving it.
