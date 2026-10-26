---
name: jarvis-daily-brief
description: JARVIS's morning brief (8am) and evening summary (9pm) for the owner - yesterday's or today's real numbers, approvals waiting, live tests, what the team finished, whether the owner is on track for their profit goal, and the next priorities. Use when the owner asks for a brief, summary, update or "how are we doing", and for the scheduled daily runs.
---

# Daily brief and evening summary

The owner wants a morning brief and an evening summary every day (2026-10-26). Both are written to `brain/reports/` and end with a short spoken version in the jarvis-voice style, which is what the owner sees in the JARVIS chat app. Read only: never launch, spend, edit the store or ads, or message anyone while doing this. Never invent a number; write "no data" instead.

## Morning brief
1. Run, each on its own: `bin/jarvis brief`, `bin/jarvis task list`, `bin/jarvis approval list`, `bin/jarvis test list`, `bin/jarvis feedback digest`, `bin/jarvis cost check 0`, `bin/jarvis journal --previous`.
2. Read yesterday's orders and sales (Shopify connector) and yesterday's spend and results for active campaigns (Meta Ads connector), read-only, if they're connected.
3. Write `brain/reports/<date>-morning.md`: yesterday's numbers, approvals waiting, live tests and their day 1/2/3 status, what each agent finished, blockers, and the top 3 priorities for today ranked by what gets to revenue soonest.
4. Spoken version: 4–6 short sentences, no lists, starting "Good morning, sir."

## Evening summary
1. Run, each on its own: `bin/jarvis journal` (today), `bin/jarvis task list`, `bin/jarvis approval list`, `bin/jarvis test list`, `bin/jarvis feedback list -n 15`, `bin/jarvis mem search "goal profit target" -c business`.
2. Read today's orders, spend and results read-only if the connectors are there.
3. If a product test is live, have product-testing run the end-of-day review (jarvis-feedback-loop section 2b) and include its verdicts.
4. Write `brain/reports/<date>-evening.md`: what was actually done today (tasks, deliverables, decisions), what slipped and why, today's numbers, whether the owner is on track for their profit goal (say plainly if the goal or the data is missing), and tomorrow's first job.
5. Spoken version: 4–6 short sentences, no lists, starting "Good evening, sir." If it's very late, suggest rest, briefly.

## Scheduling
Both run as Claude Code desktop scheduled tasks (`jarvis-morning-brief` at 0 8 * * *, `jarvis-evening-summary` at 0 21 * * *, local time), once the owner allows the scheduled-tasks tool. Each scheduled prompt says: work in the jarvis repo, follow CLAUDE.md, and follow this skill.
