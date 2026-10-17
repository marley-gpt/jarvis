# JARVIS — local computer control

JARVIS is a local-first Windows control agent with a live Electron dashboard. It combines a deliberately small command engine, Playwright browser automation, restricted filesystem access, an allowlisted Windows application launcher, and approval gates for consequential actions.

## Capabilities

- Live Brain, Files, Skills, Creative Studio, and Meta Ads views
- Text commands and opt-in browser speech recognition
- Playwright-powered browser navigation using a persistent local profile
- Allowlisted Windows application launch
- Restricted file listing, folder creation, organisation, move, and delete
- Real-time task status, action log, results, and errors
- Approval queue for deletes, publishing, paid actions, and significant changes
- Emergency stop that aborts active tasks and closes the automation browser
- Dry-run-first ecommerce workflows

JARVIS never silently publishes, spends money, deletes files, or bypasses access controls.

## Requirements

- Windows 10/11
- Node.js 20+

## Run

```powershell
npm install
npx playwright install chromium
Copy-Item .env.example .env
npm run dev
```

Set `JARVIS_ALLOWED_ROOTS` in `.env` to semicolon-separated absolute folders JARVIS may access. Set `JARVIS_ALLOWED_ORIGINS` to restrict browser navigation.

## Example commands

```text
open notepad
open https://github.com
list files in C:\Users\you\Documents\JarvisWorkspace
create folder C:\Users\you\Documents\JarvisWorkspace\Campaigns
organise files in C:\Users\you\Documents\JarvisWorkspace\Inbox
delete C:\Users\you\Documents\JarvisWorkspace\old.txt
run workflow product-listing-draft
run workflow meta-ads-review
```

## Safety and budget

The renderer is isolated, user text is never passed to a shell, apps are selected from a fixed allowlist, paths are canonicalised against configured roots, and browser origins are checked before navigation. High-impact actions require a one-time human approval. The core runtime is local and open source, with no recurring software charge. Optional external adapters remain disabled by default so the system can stay inside an A$50/month ceiling.

Run `npm test` and `npm run build` to verify the project.