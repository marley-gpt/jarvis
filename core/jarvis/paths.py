"""Locations and configuration.

All business data lives under JARVIS_HOME (default: <repo>/brain). That folder is
git-ignored apart from its README, because this repository is public.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Windows CREATE_NO_WINDOW for helper processes. The dashboard runs under pythonw (no console), so
# without it every console program JARVIS runs (PowerShell, bash, python) pops up its own window;
# with it they share one hidden console instead.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CORE_DIR = REPO_ROOT / "core"

# Twelve memory categories. Folder name -> human label.
CATEGORIES: dict[str, str] = {
    "business": "Business Information",
    "preferences": "Personal Working Preferences",
    "mentor": "Mentor Training",
    "product-research": "Product Research",
    "customer-research": "Customer Research",
    "competitor-ads": "Competitor Advertising",
    "creative-inspiration": "Creative Inspiration",
    "shopify": "Shopify Information",
    "meta-ads": "Meta Ads Performance",
    "testing": "Product Testing History",
    "decisions": "Decisions and Lessons",
    "procedures": "Agent Skills and Procedures",
}

# Who a piece of knowledge came from. Agents must never present `agent` knowledge as the mentor's.
PROVENANCES = ("mentor", "user", "evidence", "agent")

DEFAULT_CONFIG = {
    "monthly_ai_budget_aud": 50.0,
    "weekly_profit_target_aud": 380.0,  # covers Claude Max 5x, Higgsfield and 2 tests a week at worst (M-261028-d357)
    "usd_aud": 1.52,  # used when an amount is entered in USD without its AUD value
    "default_test_budget_usd": 50.0,
    "default_test_days": 3,
    "max_parallel_agents": 4,
    "backup_keep": 20,
    "duplicate_threshold": 0.82,
    # Memory and journal hits injected with each owner message (UserPromptSubmit hook).
    "recall_memory_hits": 4,
    "recall_journal_hits": 3,
    # Below these, a test result is reported as "insufficient data" and never as a win.
    "min_spend_for_verdict_usd": 30.0,
    "min_clicks_for_verdict": 50,
    # Dashboard voice: a Kokoro voice id or preset (see core/jarvis/tts.py) and speaking speed.
    "tts_voice": "jarvis",
    "tts_speed": 1.0,
    # The dashboard conversation's model and effort: low effort keeps spoken replies quick;
    # specialists keep their own models.
    "dashboard_model": "opus",
    "dashboard_effort": "low",
    # Pacing background agent work against the plan's weekly allowance (bin/jarvis pace): keep this much of the
    # week for urgent owner work, pause when the 5-hour window is this full, the estimated % of the weekly limit
    # one agent run costs (recalibrated from readings), the most runs started per check, and checks' spacing.
    "usage_reserve_pct": 10.0,
    "usage_5h_ceiling_pct": 85.0,
    "pace_pct_per_run": 0.5,
    "pace_max_batch": 4,
    "pace_interval_hours": 2,
    # Skill learning (bin/jarvis skills): how often one agent may learn (the pacer sets how many run), the size
    # of each run, and when notes get promoted or trimmed.
    "skill_min_hours_between_runs": 12,
    "skill_notes_per_run": 3,
    "skill_candidates_per_run": 3,
    "skill_note_max_chars": 1200,
    "skill_promote_helped": 3,
    "skill_trim_days": 30,
    "store_url": "https://urbansupplied.com",  # watched by the dashboard; the owner is alerted if it stops loading
}


def home() -> Path:
    return Path(os.environ.get("JARVIS_HOME") or REPO_ROOT / "brain").resolve()


def db_path() -> Path:
    return home() / "jarvis.db"


def memory_dir() -> Path:
    return home() / "memory"


def work_dir() -> Path:
    return home() / "work"


def files_dir() -> Path:
    return home() / "files"


def backups_dir() -> Path:
    return home() / "backups"


def journal_dir() -> Path:
    return home() / "journal"


def scratch_dir() -> Path:
    return home() / "scratch"


# ---------------------------------------------------------------- machine role (T105)
# Only one machine answers the phone: the primary runs the Telegram bridge, the routines tick and the
# call server; a standby (the laptop) keeps a copied brain and stays quiet until it takes over.
# The role is kept per machine, outside brain/, because brain/ is copied between machines.

ROLES = ("primary", "standby")


def role_file() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "JARVIS" / "role"


def machine_role() -> str:
    role = (os.environ.get("JARVIS_ROLE") or "").strip().lower()
    if not role:
        try:
            role = role_file().read_text(encoding="utf-8").strip().lower()
        except OSError:
            role = ""
    return role if role in ROLES else "primary"


def set_machine_role(role: str) -> Path:
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    path = role_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(role + "\n", encoding="utf-8")
    return path


def standby() -> bool:
    return machine_role() == "standby"


def config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    path = home() / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


def set_config(key: str, value) -> None:
    """Persist one setting in brain/config.json (keeps the owner's other settings)."""
    path = home() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    """Path relative to the repo when possible, for compact display."""
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()
