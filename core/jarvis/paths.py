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
    "default_test_budget_usd": 50.0,
    "default_test_days": 3,
    "max_parallel_agents": 4,
    "backup_keep": 20,
    "duplicate_threshold": 0.82,
    # Below these, a test result is reported as "insufficient data" and never as a win.
    "min_spend_for_verdict_usd": 30.0,
    "min_clicks_for_verdict": 50,
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


def scratch_dir() -> Path:
    return home() / "scratch"


def config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    path = home() / "config.json"
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    return cfg


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def rel(path: Path) -> str:
    """Path relative to the repo when possible, for compact display."""
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()
