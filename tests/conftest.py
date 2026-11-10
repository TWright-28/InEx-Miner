import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INEX_PROFILE_PATH = ROOT / "profiles" / "inex.yaml"
SEVERITY_PROFILE_PATH = ROOT / "profiles" / "example-severity.yaml"
STAGED_PROFILE_PATH = ROOT / "profiles" / "inex-staged.yaml"

ENV_VARS = [
    "DATABASE_URL",
    "GITHUB_TOKEN",
    "INEX_ORCHESTRATOR",
    "INEX_CLASSIFIER",
    "INEX_JUDGE",
    "INEX_THINKING",
    "INEX_JUDGE_THINKING",
    "INEX_TEMPERATURE",
    "INEX_WORKERS",
    "INEX_RETRIES",
    "INEX_PROFILE",
    "OLLAMA_BASE",
    "INEX_NUM_CTX",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE",
]

FULL_PROBS = {"Intrinsic": 0.90, "Extrinsic": 0.03, "Not a Bug": 0.04, "Unknown": 0.03}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def db(db_path):
    from inex.store.database import Store

    return Store(db_path)


@pytest.fixture
def seeded(db):
    pid = db.save_project("acme", "widget")
    ids = []
    for n in (101, 102, 103):
        ids.append(
            db.save_issue(
                pid,
                {
                    "number": n,
                    "title": f"issue {n}",
                    "body": "body",
                    "state": "closed",
                    "state_reason": "completed",
                    "created_at": f"2023-0{n - 100}-01T00:00:00Z",
                },
            )
        )
    return db, pid, ids


@pytest.fixture
def seeded_classified(seeded):
    db, pid, ids = seeded
    for issue_id in ids:
        db.save_classification(
            issue_id,
            {"classification": "Intrinsic", "classification_probabilities": FULL_PROBS},
            "qwen3:30b",
            "inex@test+t2",
            0.2,
            "2026-10-26T00:00:00",
        )
    return db, pid, ids


@pytest.fixture
def settings(tmp_path):
    from inex.config import load_settings

    return load_settings(
        env_file=None,
        database=tmp_path / "t.db",
        exports_dir=tmp_path / "exports",
        npm_cache_dir=tmp_path / "npm",
        checkpoints=tmp_path / "c.db",
        transcripts=tmp_path / "chat",
        github_token="t",
    )


def load_inex_profile():
    from inex.services.profiles import load_profile

    return load_profile(INEX_PROFILE_PATH)


@pytest.fixture
def inex_profile():
    return load_inex_profile()


@pytest.fixture
def severity_profile():
    from inex.services.profiles import load_profile

    return load_profile(SEVERITY_PROFILE_PATH)


@pytest.fixture
def staged_profile():
    from inex.services.profiles import load_profile

    return load_profile(STAGED_PROFILE_PATH)


@pytest.fixture
def settings_with_bad_profile(settings, tmp_path):
    from dataclasses import replace

    bad = tmp_path / "bad.yaml"
    bad.write_text("name: bad\nguide: missing.md\nlabels: []\n", encoding="utf-8")
    return replace(settings, profile=bad)


@pytest.fixture
def classifier():
    from inex.services.classification import ClassificationService

    cl = ClassificationService(None, load_inex_profile(), "qwen3:30b", 0.2, workers=1)
    cl.retry_wait = lambda *a, **k: 0
    return cl


@pytest.fixture
def cloud_classifier():
    from inex.services.classification import ClassificationService

    os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-used")
    cl = ClassificationService(
        None, load_inex_profile(), "google_genai:gemini-2.5-pro", 0.2, workers=1
    )
    cl.retry_wait = lambda *a, **k: 0
    return cl


def row_for(issue_id, number=42, state_reason=None, raw_data=None):
    return (
        issue_id,
        number,
        "a title",
        "a body",
        "closed",
        state_reason,
        "2023-01-01T00:00:00Z",
        "2023-02-01T00:00:00Z",
        raw_data,
    )
