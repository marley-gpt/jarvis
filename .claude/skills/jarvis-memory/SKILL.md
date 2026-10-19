---
name: jarvis-memory
description: How every JARVIS agent reads and writes the shared brain (bin/jarvis) — search before working, cite sources, save deliverables, record findings without duplicates. Use before and after any JARVIS business task.
---

# JARVIS memory protocol

The brain lives in `brain/` and is reached through `bin/jarvis` (run it from the repo root; `bin/jarvis <cmd> --help` lists the options).

**Quoting:** put free text (`--summary`, `--body`, `--title`, notes) in **single quotes**. Inside double quotes, `$` expands (`"A$50"` becomes `A0`). For long or multi-line text, write a scratch file and pass `--body-file`. Run each `bin/jarvis` command on its own: no `&&` chains, `$(...)` or variables.

## 1. Before working: retrieve only what's relevant

```bash
bin/jarvis mem search "<what you need>" [-c <category>] [-p <product>] [--provenance mentor] [-n 5]
bin/jarvis mem get <id> [<id>...]        # full entry, only for hits you will actually use
bin/jarvis product show <slug>           # index of everything known about one product
bin/jarvis task show T<n>                # your assignment and its history
```

Always search `-c mentor` (or `--provenance mentor`) for a method that covers your task. If one exists, follow it and cite it. If none exists, say "No mentor method found" and label your own approach **JARVIS recommendation**.

Categories: business, preferences, mentor, product-research, customer-research, competitor-ads, creative-inspiration, shopify, meta-ads, testing, decisions, procedures.

## 2. While working

```bash
bin/jarvis task start T<n>
bin/jarvis task note T<n> "found 3 suppliers, checking shipping times"   # progress the owner can see
```

Temporary working files go in `brain/scratch/<agent>/`. Media (screenshots, ad images) goes in `brain/files/<product>/...`.

## 3. Finishing: save the deliverable, then close the task

The deliverable file is required output that the owner asked for. It overrides any general guidance against writing report files.

```bash
bin/jarvis task path T<n>     # prints the deliverable path, e.g. brain/work/<product>/T7-....md
# write the deliverable there (Write tool), then:
bin/jarvis task done T<n> --summary "<2-3 lines: result + recommendation>" --deliverable <path>
```

## 4. Record durable findings (not the whole report)

Save only knowledge that future tasks will reuse: key facts, verified numbers, angles, rules, lessons. One idea per entry, written so it stands alone.

```bash
bin/jarvis mem add --title "<specific title>" -c <category> --provenance <mentor|user|evidence|agent> \
  [-p <product>] [--tags a,b] [--confidence confirmed|likely|speculative] \
  [--source "<lesson / URL / deliverable path>"] [--ref "<timestamp or section>"] \
  --body "<markdown>"            # or --body-file <path> for longer text
```

- **provenance**: `mentor` = taught in a lesson (needs `--source` and `--ref`); `user` = the owner said it; `evidence` = observed in data or sources you can cite; `agent` = your own inference or recommendation.
- If `mem add` says the entry looks like existing knowledge, don't force it. Use `bin/jarvis mem supersede <old-id> --title ... --body ...` when it updates or corrects that entry, and `--force` only when the two really are different.
- If `mem add` lists related entries, check they don't contradict yours. If they do, flag it in your result instead of guessing.
- Unclear or unverified knowledge: `--status needs-review`.
- Never delete. Retire knowledge with `bin/jarvis mem status <id> archived --note "<why>"`.

## 5. Return to JARVIS

Reply in under 150 words: task id, deliverable path, the key findings, mentor methods used (ids), open questions, and any approvals needed. JARVIS reads the file for the details.

## Never

- Execute consequential actions (spend, publish, launch, edit live store, change prices, contact suppliers). Prepare drafts and say an approval is needed.
- Fabricate quotes, reviews, metrics or mentor instructions.
- Commit anything under `brain/`. The repository is public.
