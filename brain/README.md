# brain/ — everything JARVIS remembers

This folder holds the owner's private business data. It is **git-ignored** (the repository is public), so keep your own backups: `bin/jarvis backup` writes a zip to `brain/backups/` (a daily one is taken automatically at session start).

| path | contents | edit by hand? |
|---|---|---|
| `memory/<category>/[product/]*.md` | long-term knowledge, one Markdown file per entry, with front matter recording provenance and source | yes; changes are re-indexed automatically |
| `memory/_archive/` | archived (retired) entries; nothing is ever hard-deleted | — |
| `jarvis.db` | SQLite: search index, products, tasks + history, decisions, approvals, tests + metrics, costs, agent runs, action log | no, use `bin/jarvis` |
| `work/<product>/T<n>-*.md` | task deliverables (dossiers, research, creative packs, drafts, reports) | yes |
| `files/` | media: mentor videos/frames, inspiration screenshots, ad images (`files/<product>/ads/`) | yes |
| `scratch/<agent>/` | temporary agent working files (safe to clear) | yes |
| `backups/` | zipped snapshots | no |
| `config.json` | optional overrides (budgets, thresholds; see `core/jarvis/paths.py`) | yes |

Inspect from a terminal with `bin/jarvis brief`, `bin/jarvis mem stats`, `bin/jarvis mem list`, `bin/jarvis mem search "<query>"`, `bin/jarvis task list --all` and `bin/jarvis product show <slug>`. Set `JARVIS_HOME` to keep the brain somewhere else (for example a synced folder).
