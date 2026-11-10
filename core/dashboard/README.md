# The JARVIS app

`bin/jarvis dashboard` serves one app on http://127.0.0.1:8765 (`core/jarvis/dashboard.py`). It was rebuilt in T110
(D74, D75) as a product-testing cockpit: the owner's priority is testing products properly and quickly, so that is
the home page, and everything else is a page behind it.

## Pages

| Address | Script | What it is |
|---|---|---|
| `/` (`/testing`) | `page-testing.js` | The cockpit: every product on its rail of stages with its blockers and one next action, each test's numbers per day against Blake's day rules, the kill rule, who is working now, add a candidate |
| `/team` | `page-team.js` | The org chart with JARVIS as the boss (T109), what each agent is doing, an agent's panel (tasks, skills, model, ask box), the activity log |
| `/work` | `page-work.js` | What waits for the owner, the plan of every open project, workers and their models, desktop jobs, recent approvals |
| `/money` | `page-money.js` | Today, the week against A$380, ROAS against breakeven, 14 days of profit, the month by category, each test's profit and loss, the ledger |
| `/brain` | `page-brain.js` | Memory search and reader, latest decisions, the conversation journal |
| `/system` | `page-system.js` | Routines, connections, Claude capacity, conversation model and sessions, voice, the PC |

A deeper address opens a record on that page: `/work/T110` (that plan), `/work/W36` (that worker's log),
`/team/creative-director`, `/brain/M-261110-cdd9`, `/testing/X1`. `call.html` is the phone-call page; the call server
(`core/jarvis/call.py`) serves it, not the dashboard. The addresses of the removed pages (`/hq`, `/chat`, `/office`,
`/console`, `/stage`, `/classic`, `/app`) redirect to `/`.

## How it fits together

**One shell.** Every page address returns the same `app.html`. `app.js` is the frame that stays put: the navigation,
the status strip (sales, spend, profit, ROAS against breakeven, the week against the target, Claude capacity), the
inbox, the record drawer and the talk panel. A page is a script that calls `App.register(name, { title, render(el, S),
open(arg, el) })`; it is loaded the first time it is opened and draws into `<main>`. Moving between pages changes the
address with the History API and never reloads, so the talk panel, a reply in progress and the voice carry on.

**One state.** `core/jarvis/cockpit.py` builds everything the pages show in one pass (`state()`, about 30 ms on the
live brain): `kpis`, `pipeline`, `tests`, `inbox`, `work`, `live`, `money`, `capacity`, `brain`, `system`.
`cockpit.Feed` keeps the last build, rebuilds when the brain's fingerprint changes (and every 2 s while agents are
working, 10 s otherwise), and publishes only the sections whose content changed.

**One stream.** `jarvis.js` (`window.J`) holds the only connection, `/api/events`. Its first message is the whole
state (`{"type": "state", "full": true, "sections": {...}}`); after that come `state` events with the changed
sections, and the conversation's events (`status`, `say_delta`, `say_chunk`, `say`, `jarvis_tool`, `dispatch`,
`agent_activity`, `permission`, `ears`, `wake` and so on). `J.sections` is always current; listen with
`J.on(type, fn)`. Nothing polls. Don't open a second event stream and don't add a timer that fetches state.
`GET /api/app` returns the same sections for anything that wants them once (`?fresh=1` forces a rebuild).

Fetched only when asked for: `/api/teammap` (who the agents are, when the Team page opens), `/api/skills?agent=`
(skill levels, when an agent is picked), `/api/memory?q=&c=` (a search), `/api/record?id=` (the record drawer:
`T12`, `A3`, `D7`, `X1`, `M-…`, `product:<slug>`, `agent:<name>`, `cat:<name>`, `file:brain/...`), `/api/file?path=`
(a deliverable's preview: brain files only, never `secrets/`), `/api/control/worker?id=` (a worker's log),
`/api/control/sessions`, `/api/control/screen`.

Markup the shell acts on anywhere: `data-ref="T12"` opens the record drawer, `data-file="brain/..."` previews a
deliverable, `data-act="<name>"` runs `App.acts[name](element)`, and any `a[href="/..."]` navigates in place. Record
ids written in text (`App.link`) become links by themselves. `App.block(el, html)` sets a block's markup only when it
changed, so scrolling, focus and half-typed input survive the state arriving; build a page as a fixed skeleton with
blocks inside it (see `page-testing.js`).

## The testing cockpit (`cockpit.py`)

- **Pipeline** (`pipeline`, `_card`): each product on the rail candidate, researched, creatives, product page,
  campaign draft, your approval, live test, verdict. A stage's state comes from evidence, never from a typed status:
  the tasks of the agents that own it (`STAGE_AGENTS`), pending and approved gate approvals, and the product's tests.
  A finished test closes a round; what was done before it shows as "from the last round". `at` is where the product
  is now, `blockers` are worked out (blocked tasks, approvals waiting, stopped workers, queued work nobody started),
  and `next` is the one action: approve, read the test, start research, start a task, watch, or put it to the council.
  Open product-research work with no product yet is listed as the hunt for the next candidate.
- **Tests** (`tests_view`, `day_call`): the reads per day (a day read several times keeps its last read, and
  `records.evaluate` now counts it once too), totals, CPC, CTR, CPA, ROAS against the breakeven line, and Blake's
  cut-or-continue call per day judged on the totals to date. His counts are examples, not thresholds, so the rule only
  separates none from some. Under `min_spend_for_verdict_usd` or `min_clicks_for_verdict` the page says
  **Insufficient data** and shows the rule's reading beside it, never a win. Reads labelled two ways (day numbers and
  dates) are flagged, because the totals may count spend twice. Per-creative figures are not stored yet (T112); the
  written per-ad reads from the feedback loop are listed instead.
- **Kill rule**: ad spend since the last sale against `sales_kill_aud`, from the ledger; hitting it also puts a line
  in the inbox.
- **Inbox** (`inbox`): approvals, permission prompts and questions from JARVIS's own Claude Code
  (`control.Permissions`), stopped or failed workers, failed desktop jobs (several at once become one line), the kill
  rule, a queue with the job runner off, and Claude Code signed out. Each has its one-tap action.

One-tap actions that only record a decision go straight to the server (`/api/approval`, `/api/control/permission`,
`/api/control/worker`, `/api/control/job`, `/api/task`, `/api/task-model`, `/api/agent-model`). Actions that start
work (research a candidate, run the pipeline, start a task, ask an agent) are sent to JARVIS as a message
(`App.say`), so the approval gates, the mentor checks and the task records all still apply.

## Talking to JARVIS

The talk panel is docked on every page (a sheet on a phone; Ctrl+/ focuses it). Typed messages, pasted or dropped
images (up to 4) and attached files (up to 4, 15 MB each, no programs) go to `POST /api/chat`: the same headless Claude
Code session as before, with the same connectors, skills, memory, hooks and approval gates. Messages sent while JARVIS
is busy wait their turn on the server.

- **Speed.** The reply's words are published as they are written (`say_delta`) and shown at once; sentences go to
  the Kokoro voice as each one completes (`say_chunk`), fetched ahead while the previous one plays. Claude Code is
  started at server start and primed with `/cost` (`Runner._prime`: answered by Claude Code itself, no usage, nothing
  added to the conversation) so the first message doesn't pay for its connectors loading; it is started again at once
  after Stop, and within 30 s whenever it has ended. `python core/tests/latency.py` measures all of it on a copy of
  the brain.
- **Voice.** "Hey JARVIS" (the local wake word, `wakeword.py`), the mic button, hands-free follow-ups and the British
  Kokoro voice work as before; they live in `jarvis.js` and the settings are on the System page.
- **Permission prompts.** The dashboard's Claude Code runs with `--permission-prompt-tool stdio`. Read-only tools
  pass; anything else becomes an approval record that shows in the inbox, as a toast, on the phone and can be answered
  by saying or typing yes or no. `AskUserQuestion` shows its options in the inbox.

## Upgrades that came with the rebuild

- **Model per worker**: `bin/jarvis work start --model`, the Start a worker form, and the Workers table show the
  model each run uses (the default is `worker_model`). Fable is offered only as the owner's own choice (D77).
- **Plan view**: every open parent task with its steps, agent, status, model, deliverable link and running worker.
- **Ask this agent**: on an agent's panel; the question goes through JARVIS (`Ask the <agent> agent, from the team
  page: …`) and the answer arrives in the talk panel.
- **Debate mode**: `POST /api/debate` starts a durable worker that runs the `marketing-council` skill on a big call
  (product pick, kill or continue, scale), writes its deliverable, and records the outcome with `bin/jarvis decide
  --by jarvis`. It recommends; spending and launching still go through approval.
- **Capacity meter**: `bin/jarvis pace` in the strip and on the System page: used, even pace, the ceiling that keeps
  a reserve for the owner.

## Look

Slate surfaces with hairline borders; the JARVIS arc blue and ice for anything that is JARVIS or data
(M-261110-cdd9); amber only for what waits on the owner; green only for work running or a figure above breakeven; red
only for a cut or a figure below it. Figures are set in a DIN face (Bahnschrift on Windows, DIN Alternate on the
iPhone), text in the system face, no downloaded fonts. Layouts follow the room the page has (container queries on
`.main`), so they hold with the talk panel open or closed and down to a phone, where the navigation becomes a bottom
bar. Motion only where something is happening: a dot pulses while an agent runs, a rail stop rings while its stage is
active, a department's line runs while someone in it works, a log line is lit as it arrives, and nothing else moves
(all off with reduced motion).

## Rules for changes

- Real data only. If something isn't known or isn't built, say so on the page. No control that does nothing.
- A test with too little data is "Insufficient data", never a win (the owner's honesty rule).
- New data goes into a section of `cockpit.state()`; a page never fetches its own copy of what the state carries.
- A new page is one script plus its name in `dashboard.APP_PAGES`, the `ROUTES` table in `app.js` and the navigation
  in `app.html`. No second HTML page: `test_app.py` checks that every address serves the one shell.
- Run `python -m unittest discover -s core/tests` (`test_app.py`, `test_dashboard.py`, `test_roster.py`,
  `test_swarm.py`, `test_control.py`), then look at every page at desktop and phone width with no console errors.
