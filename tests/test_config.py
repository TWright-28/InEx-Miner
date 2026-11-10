import os
from pathlib import Path

import pytest
from conftest import ENV_VARS

from inex.config import REPO_ROOT, load_settings
from inex.errors import InexError


@pytest.fixture
def clean_env(monkeypatch):
    saved = dict(os.environ)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_env_example_works_from_any_folder(clean_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    (tmp_path / ".env").write_text(example, encoding="utf-8")
    settings = load_settings()
    assert settings.profile == REPO_ROOT / "profiles" / "inex-staged.yaml"
    assert settings.profile.is_file()


def test_defaults(clean_env):
    s = load_settings(env_file=None)
    assert s.database == Path("db/database.db")
    assert s.github_token is None
    assert s.classifier == "google_genai:gemini-3-flash-preview"
    assert s.judge is None
    assert (s.thinking, s.judge_thinking) == ("medium", "low")
    assert s.orchestrator == "google_genai:gemini-2.5-flash"
    assert (s.temperature, s.classify_workers, s.classify_retries, s.num_ctx) == (0.2, 8, 5, 131072)
    assert s.ollama_base == "http://localhost:11434"
    assert s.exports_dir == Path("exports")
    assert s.npm_cache_dir == Path(".npm_doc_cache")
    assert s.checkpoints == Path("db/checkpoints.db")


def test_bundled_files_resolve_from_the_repo_root_not_the_cwd(clean_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(env_file=None)
    assert s.system_prompt == REPO_ROOT / "prompts" / "system_prompt.md"
    assert s.profile == REPO_ROOT / "profiles" / "inex-staged.yaml"
    assert s.logging_config == REPO_ROOT / "loggingConfigs" / "config.json"
    assert s.system_prompt.is_file()


def test_environment_overrides_defaults(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_WORKERS", "3")
    monkeypatch.setenv("DATABASE_URL", "db/other.db")
    s = load_settings(env_file=None)
    assert (s.classify_workers, s.database) == (3, Path("db/other.db"))


def test_keyword_overrides_beat_environment(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_CLASSIFIER", "qwen3:30b")
    assert load_settings(env_file=None, classifier="x:y").classifier == "x:y"


def test_none_override_keeps_the_environment_value(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_CLASSIFIER", "qwen3:30b")
    assert load_settings(env_file=None, classifier=None).classifier == "qwen3:30b"


def test_env_file_is_read(clean_env, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("INEX_RETRIES=2\n", encoding="utf-8")
    monkeypatch.delenv("INEX_RETRIES", raising=False)
    assert load_settings(env_file=env_file).classify_retries == 2


def test_malformed_number_is_an_invalid_setting(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_WORKERS", "eight")
    with pytest.raises(InexError) as e:
        load_settings(env_file=None)
    assert e.value.code == "invalid_setting"


def test_loading_settings_creates_nothing(clean_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_settings(env_file=tmp_path / ".env")
    assert list(tmp_path.iterdir()) == []


def test_judge_and_thinking_come_from_the_environment(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_JUDGE", "x:y")
    monkeypatch.setenv("INEX_THINKING", "high")
    monkeypatch.setenv("INEX_JUDGE_THINKING", "minimal")
    s = load_settings(env_file=None)
    assert (s.judge, s.thinking, s.judge_thinking) == ("x:y", "high", "minimal")


def test_thinking_none_turns_it_off(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_THINKING", "None")
    assert load_settings(env_file=None).thinking is None


def test_unknown_thinking_level_is_rejected(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_JUDGE_THINKING", "hgih")
    with pytest.raises(InexError) as e:
        load_settings(env_file=None)
    assert e.value.code == "invalid_setting"
    assert "INEX_JUDGE_THINKING" in e.value.message
