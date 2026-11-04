# JARVIS dashboard

`bin/jarvis dashboard` serves these pages on http://127.0.0.1:8765 (`core/jarvis/dashboard.py`).

| Route | File | What it is |
|---|---|---|
| `/` (also `/hq`) | `hq.html` + `hq.js` + `hq-scene.js` | The app: status board, talk bar, teams and the 3D HQ |
| `/chat` | `chat.html` | The full chat app (also the phone-sized window) |
| `/office` | `office.html` | The earlier isometric office |
| `/console`, `/classic` | `console.html`, `index.html` | Earlier designs, kept |

All pages share `jarvis.js` (`window.J`): the live connection (`/api/events`), `/api/state`, sending messages, the mic, the wake word and the Kokoro voice. Don't open a second event stream in a page; listen with `J.on(type, fn)`.

## The app, piece by piece

**`hq.html`**: the layout and all the CSS. Colour, type and spacing tokens are on `:root`. Desktop order: header, 3D HQ, board, teams. Phone (860px wide or less): header, board as four tabs, 3D HQ, teams, with the talk bar fixed at the bottom.

**`hq.js`**: everything except the 3D view. Its sections, in order:
- *Data adapter* (`Feed`): the only place data comes in. It combines `J.state` (`/api/state`), `/api/hq` and live events (`dispatch`, `agent_activity`, `agent_done`, `status`) into one model, and works out each agent's status. When the activity log (`core/jarvis/activity.py`) is installed, `/api/hq` carries it and it takes priority. To add a data source, add it here and give it a field on the model.
- *Header*: live counts (tap one to jump to that board column), the next milestone, profit this week against the weekly target.
- *Board*: Now, Needs you (one-tap Approve and Reject), Done, To do. Every item is tagged with its team and opens its task.
- *Teams and allowance*: the eight teams with each agent's model, and the Claude Max weekly pace panel.
- *Panels*: team, agent, task and JARVIS. Anything with `data-team`, `data-agent`, `data-task` or `data-ask` opens the matching panel, or puts text in the talk bar.
- *Talk bar*: typed messages go to `J.send`, the mic to `J.listen`; replies show as a caption and are spoken by `jarvis.js`.

**`hq-scene.js`**: the three.js world (three@0.160.0 from jsdelivr, through the import map in `hq.html`). `startScene(opts)` returns `update(model)`, `dispatch(agent)`, `focus(teamId)` and `demo(on)`. It draws only what the model says; the city, yachts and cars are scenery. If three.js can't load, the rest of the page still works.

**`core/jarvis/hq.py`** (server side, `GET /api/hq`): teams (T54 plan, section 7), each agent's tools, connections, skills and model from `.claude/agents/*.md`, the board (all open tasks, plus tasks done in the last 14 days), this week's profit against `weekly_profit_target_aud`, and the activity log when it exists. `GET /api/hq/file?path=` previews a deliverable: brain files only, never `secrets/`.

## Settings the page reads (brain/config.json)

- `hq_milestone`: `{"title": "Brush launch", "when": "2026-11-05T20:00"}`, shown in the header.
- `max_allowance`: `{"used_pct": 42, "resets": "2026-11-10T09:00+10:30", "source": "..."}`, the Claude Max weekly pace panel. Until something fills it in, the panel says "Not connected yet".

## Rules for changes

- Real data only. If something isn't known or isn't built, say so on the page. Demo content must be labelled as demo where it shows.
- Keep the board readable at a glance on a phone. The 3D view must never hide the status.
- Run `python -m unittest discover -s core/tests` (tests in `core/tests/test_hq.py` and `test_dashboard.py`), then check the page in a browser at desktop and phone width with no console errors.
