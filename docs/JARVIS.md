# JARVIS core: design notes and build checklist

## How it fits together

```
You ──► Claude Code session = JARVIS (CLAUDE.md)
          │  SessionStart hook injects the brief (rules, open tasks, approvals, tests)
          ├─► Agent tool ──► 9 specialists (.claude/agents/*.md), run in parallel when independent
          │                    └─ all read/write the brain via bin/jarvis (skill: jarvis-memory)
          ├─► PreToolUse hook: protects brain data; forces a prompt on any non-read MCP action
          └─► PostToolUse / Subagent hooks: action log + agent-run log
brain/  (git-ignored) memory/*.md ⇄ jarvis.db (FTS5 index + tasks, decisions, approvals, tests, costs)
```

**Decisions**
- **Claude Code only.** Subagents, skills, hooks, permissions and MCP connectors cover orchestration. n8n, Hermes and the like add nothing needed yet.
- **Markdown is the source of truth for knowledge.** It's readable and editable by hand. SQLite FTS5 keeps an index that rebuilds itself from file mtimes. Structured records (tasks, approvals, tests, costs) live in SQLite.
- **Python standard library only.** Nothing to install. It runs wherever Claude Code runs (bash wrappers work on Linux, macOS and Git Bash on Windows; `bin/jarvis.cmd` covers cmd/PowerShell).
- **Token economy.** Agents get a short brief, then search (top 5 snippets) and fetch only the entries they use. Large content moves between agents as file paths, never pasted.
- **Provenance on every entry** (`mentor | user | evidence | agent`). Mentor entries must cite a lesson and timestamp. This is how mentor methods stay separate from AI suggestions.
- **Safety.** Specialists can't call mutating tools. JARVIS asks you, records `approval request/approve`, and only then acts. The guard hook also blocks deleting brain data, raw SQL writes and committing `brain/`. Memory is archived, never deleted. A daily zip backup is taken at session start.
- **Model tiers.** Ops and testing agents run on Haiku; research, Shopify and Meta agents on Sonnet; mentor and creative-director inherit the main model. All of them draw on your Claude subscription's usage limits, not a separate bill.

## What Claude Code can and can't do here

| need | status |
|---|---|
| Orchestration, parallel subagents, persistent memory | **Works** (verified in headless Claude Code sessions) |
| Looking at video frames | Works: ffmpeg extracts frames, Claude reads images. Install ffmpeg locally (`winget install ffmpeg`) |
| Audio transcription | **Missing.** Claude can't hear audio. Plan: local **faster-whisper** (free, CPU). Paid fallback: OpenAI transcription, about US$0.003–0.006 per minute |
| Shopify | Shopify connector tools are present in this cloud session. Still to check: that they reach Urban Supplied, and from your local Claude Code |
| Meta Ads | **No connector.** Phase 4: Meta Marketing API with a read-only system-user token (free) via a small MCP server or script. Until then, CSV exports |
| GetHookd | No known public API. Use manual exports or screenshots in `brain/files/inspiration/` |
| Images | ChatGPT by hand from the briefs (existing subscription). A Higgsfield connector is visible but uses paid credits, so evaluate it later and only with approval |
| Google Drive | Connector available; not wired in yet |
| Your plan's usage limits | Can't be seen from here. Run `/status` or `/usage` in Claude Code on your machine |

## Build checklist

**Phase 1 — Foundation** ✅
- [x] JARVIS orchestrator (`CLAUDE.md`), 9 specialist agents, `jarvis-memory` skill
- [x] Brain: 12 categories, Markdown + FTS5 search, provenance/source/timestamp, duplicate refusal, supersede, archive, hand-edit re-indexing
- [x] Records: products, tasks + subtasks + event history + mandatory deliverables, decisions, approvals, product tests + metrics with an insufficient-data guard, costs vs the A$50 budget
- [x] Hooks: session brief, daily backup, data guard, MCP approval gate, action and agent-run logs
- [x] Tests: 32 unit/integration tests (`python3 -m unittest discover -s core/tests -t core/tests`) plus CI on Linux and Windows
- [x] Live check: real sessions delegated to a specialist, saved deliverables, and recalled a stored preference in a later session

**Phase 2 — Visual mentor learning** (next)
- [ ] `bin/jarvis mentor ingest <video>`: ffprobe metadata → faster-whisper timestamped transcript → scene-change + dense-interval frame sampling → align frames to transcript segments
- [ ] Mentor agent analyses frame batches → lesson entry + procedure entries with timestamp and screenshot citations; gaps marked needs-review
- [ ] Test on a real Meta Ads Manager or Shopify screen-recording lesson; answer "how does my mentor research winning ads?" with citations

**Phase 3 — Research and creative**
- [ ] Dossier, customer-research and creative-pack templates; Creative Inspiration Library import (GetHookd exports, Ad Library screenshots)
- [ ] ChatGPT brief → image import → review loop (`brain/files/<product>/ads/`)

**Phase 4 — Shopify and Meta**
- [ ] Verify the Shopify connector against the store (read-only), then draft products behind the approval gate
- [ ] Meta read-only insights integration → `test metric` automatically; campaign draft export

**Phase 5 — Dashboard**
- [ ] Local web dashboard over `brain/` (Home, Agents, Mentor Academy, Creative Studio, Tests, Brain, Approvals, Costs). It could replace or absorb the existing Electron UI

## Known limits / open items
- Windows hook execution via Git Bash hasn't been tested on your machine yet. CI covers the wrappers on Windows.
- The first time you open the repo in Claude Code, accept the workspace trust prompt, or the project allow-list in `.claude/settings.json` is ignored.
- This repository is **public**. `brain/` is git-ignored. Consider making the repo private anyway: the seed entries in `core/seed/` describe the business.
