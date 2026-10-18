# JARVIS — local computer control

JARVIS is a local-first Windows control agent with a live Electron dashboard. It combines fast local command parsing, an optional OpenAI conversational brain, Playwright browser automation, restricted filesystem access, Windows Start-menu application discovery, and approval gates for consequential actions.

## Capabilities

- Live Brain, Files, Skills, Creative Studio, Meta Ads, and Connections views
- Natural text commands plus bounded push-to-talk transcription and spoken Windows replies
- Playwright-powered browser navigation using a persistent local profile
- Launch normal apps discovered from the Windows Start menu
- Close running apps by name, with protected-process blocks and confirmation for arbitrary apps
- Restricted file listing, folder creation, organisation, move, and delete
- Real-time task stages, elapsed time, progress, results, and errors
- Persistent local audit history that survives restarts
- Approval queue for deletes, publishing, paid actions, and significant changes
- Emergency stop that aborts active tasks and closes the automation browser
- Dry-run-first ecommerce workflows
- Automatic update checks and approval-gated one-click updates from the trusted Git checkout

JARVIS never silently publishes, spends money, deletes files, or bypasses access controls.

## Requirements

- Windows 10/11
- Node.js 20+

## Run

```powershell
npm install --legacy-peer-deps
npx playwright install chromium
Copy-Item .env.example .env
npm run dev
```

Set `JARVIS_ALLOWED_ROOTS` in `.env` to semicolon-separated absolute folders JARVIS may access. Set `JARVIS_ALLOWED_ORIGINS` to restrict browser navigation.

## Example commands

```text
open notepad
please open Spotify
close notepad
could you close Spotify for me
open https://github.com
list files in C:\Users\you\Documents\JarvisWorkspace
create folder C:\Users\you\Documents\JarvisWorkspace\Campaigns
organise files in C:\Users\you\Documents\JarvisWorkspace\Inbox
delete C:\Users\you\Documents\JarvisWorkspace\old.txt
run workflow product-listing-draft
run workflow meta-ads-review
check for updates
```

## Safety and budget

The renderer is isolated, user text is never passed to a shell, app names are resolved through the Windows Start menu or a small built-in map, protected processes cannot be closed, paths are canonicalised against configured roots, and browser origins are checked before navigation. High-impact actions require a one-time human approval.

The optional brain uses `gpt-6-luna` with short responses and a local A$45 monthly AI pause, leaving headroom inside the overall A$50 target. API billing remains controlled by your OpenAI account, so also set an account-side usage limit. Enter the key in Settings; JARVIS encrypts it with Windows secure storage and never displays it again.

Voice recordings are capped at 30 seconds, sent directly through the encrypted backend for `gpt-transcribe`, and not saved by JARVIS. The result is passed through the same task engine and approval gates as typed commands.

## Honest boundaries

JARVIS can launch installed apps, operate its controlled browser, work inside authorised folders, interpret conversational commands, and run the included review-first ecommerce workflows. The Connections page opens GitHub, Shopify, Meta, and Google in the persistent controlled-browser profile so you can sign in without putting passwords in JARVIS.

Those browser connections are not yet deep service API adapters. JARVIS does not claim that a store, ad account, email account, payment system, or social account is connected until a dedicated adapter has been implemented and verified. It will not bypass CAPTCHAs, multi-factor authentication, Windows privacy controls, administrator prompts, or the approval gates for publishing, spending, deletion, and significant changes.

Run `npm test` and `npm run build` to verify the project.

