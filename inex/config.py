import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

from inex.errors import InexError

REPO_ROOT = Path(__file__).resolve().parents[1]
THINKING_LEVELS = ("minimal", "low", "medium", "high")
THINKING_OFF = "none"
THINKING_SETTINGS = ("thinking", "judge_thinking")


@dataclass(frozen=True)
class Settings:
    database: Path
    github_token: str | None
    orchestrator: str
    classifier: str
    judge: str | None
    thinking: str | None
    judge_thinking: str | None
    temperature: float
    classify_workers: int
    classify_retries: int
    profile: Path
    system_prompt: Path
    exports_dir: Path
    npm_cache_dir: Path
    checkpoints: Path
    transcripts: Path
    logging_config: Path
    ollama_base: str
    num_ctx: int
    openrouter_api_key: str | None
    openrouter_base: str


DEFAULTS = {
    "database": Path("db/database.db"),
    "github_token": None,
    "orchestrator": "google_genai:gemini-2.5-flash",
    "classifier": "google_genai:gemini-3-flash-preview",
    "judge": None,
    "thinking": "medium",
    "judge_thinking": "low",
    "temperature": 0.2,
    "classify_workers": 8,
    "classify_retries": 5,
    "profile": REPO_ROOT / "profiles" / "inex-staged.yaml",
    "system_prompt": REPO_ROOT / "prompts" / "system_prompt.md",
    "exports_dir": Path("exports"),
    "npm_cache_dir": Path(".npm_doc_cache"),
    "checkpoints": Path("db/checkpoints.db"),
    "transcripts": Path("logs/chat"),
    "logging_config": REPO_ROOT / "loggingConfigs" / "config.json",
    "ollama_base": "http://localhost:11434",
    "num_ctx": 131072,
    "openrouter_api_key": None,
    "openrouter_base": "https://openrouter.ai/api/v1",
}

ENVIRONMENT = {
    "database": "DATABASE_URL",
    "github_token": "GITHUB_TOKEN",
    "orchestrator": "INEX_ORCHESTRATOR",
    "classifier": "INEX_CLASSIFIER",
    "judge": "INEX_JUDGE",
    "thinking": "INEX_THINKING",
    "judge_thinking": "INEX_JUDGE_THINKING",
    "temperature": "INEX_TEMPERATURE",
    "classify_workers": "INEX_WORKERS",
    "classify_retries": "INEX_RETRIES",
    "profile": "INEX_PROFILE",
    "ollama_base": "OLLAMA_BASE",
    "num_ctx": "INEX_NUM_CTX",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "openrouter_base": "OPENROUTER_BASE",
}

CASTS = {
    "database": Path,
    "temperature": float,
    "classify_workers": int,
    "classify_retries": int,
    "profile": Path,
    "system_prompt": Path,
    "exports_dir": Path,
    "npm_cache_dir": Path,
    "checkpoints": Path,
    "transcripts": Path,
    "logging_config": Path,
    "num_ctx": int,
}


def _thinking(name, value, source):
    level = str(value).strip().lower()
    if level == THINKING_OFF:
        return None
    if level not in THINKING_LEVELS:
        raise InexError(
            "invalid_setting",
            f"{source} must be one of minimal, low, medium, high or none, got {value!r}",
            setting=name,
        )
    return level


def _cast(name, value, source):
    if name in THINKING_SETTINGS and value is not None:
        return _thinking(name, value, source)
    cast = CASTS.get(name)
    if cast is None or value is None:
        return value
    try:
        return cast(value)
    except (TypeError, ValueError) as error:
        raise InexError(
            "invalid_setting", f"{source} must be a number, got {value!r}", setting=name
        ) from error


def load_settings(env_file: Path | None = Path(".env"), **overrides) -> Settings:
    if env_file is not None and Path(env_file).is_file():
        load_dotenv(env_file, override=False)
    values = dict(DEFAULTS)
    for name, variable in ENVIRONMENT.items():
        raw = os.environ.get(variable)
        if raw not in (None, ""):
            values[name] = _cast(name, raw, variable)
    known = {field.name for field in fields(Settings)}
    for name, value in overrides.items():
        if name not in known:
            raise InexError("invalid_setting", f"unknown setting {name!r}", setting=name)
        if value is not None:
            values[name] = _cast(name, value, name)
    return Settings(**values)
