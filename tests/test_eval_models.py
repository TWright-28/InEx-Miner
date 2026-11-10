from dataclasses import replace

import pytest
from fakes import (
    PRICE_PAYLOAD,
    FakeResponse,
    ScriptedChatModel,
    ai_reply,
    run_main,
    scripted_builder,
)

from evals.models import (
    Price,
    cost_of,
    estimate,
    fetch_prices,
    model_price,
    parse_prices,
    preflight,
    reproject,
)
from evals.scenarios import Scenario, WorldSpec
from inex.errors import InexError

HAIKU = Price(1e-7, 5e-7)


class RefusedError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def builder(outcome):
    def build(settings):
        if isinstance(outcome, InexError):
            raise outcome
        return ScriptedChatModel(script=[outcome])

    return build


def test_prices_are_read_from_the_price_list():
    assert parse_prices(PRICE_PAYLOAD) == {"anthropic/claude-haiku-5.5": HAIKU}
    seen = []

    def get(url, timeout):
        seen.append(url)
        return FakeResponse(PRICE_PAYLOAD)

    assert fetch_prices(get) == {"anthropic/claude-haiku-5.5": HAIKU}
    assert seen == ["https://openrouter.ai/api/v1/models"]


def test_only_openrouter_models_are_priced():
    prices = parse_prices(PRICE_PAYLOAD)
    assert model_price("openrouter:anthropic/claude-haiku-5.5", prices) == HAIKU
    assert model_price("ollama:llama3.2:3b", prices) is None
    assert cost_of(1_000_000, 100_000, HAIKU) == pytest.approx(0.15)
    assert cost_of(1_000_000, 100_000, None) is None


def test_the_estimate_before_the_first_call():
    scenarios = [
        Scenario("a", "routing", "r", ("one",), WorldSpec(), ()),
        Scenario("b", "gates", "r", ("one", "two"), WorldSpec(), ()),
    ]
    est = estimate(20_000, scenarios, 3, Price(1e-6, 5e-6))
    assert (est.input_tokens, est.output_tokens) == (135_000, 8_100)
    assert est.cost == pytest.approx(0.1755)
    assert estimate(20_000, scenarios, 3, None).cost is None
    assert reproject(0.30, 10, 150) == pytest.approx(4.5)


@pytest.mark.parametrize(
    ("model", "outcome", "fix"),
    [
        (
            "openrouter:anthropic/claude-haiku-5.5",
            InexError("missing_package", "Install it with: pip install langchain-openai"),
            "pip install langchain-openai",
        ),
        (
            "openrouter:anthropic/claude-haiku-5.5",
            InexError("missing_api_key", "OPENROUTER_API_KEY is not set; add it to .env"),
            "OPENROUTER_API_KEY",
        ),
        ("openai:gpt-6.1-terra", RefusedError(401), "set OPENAI_API_KEY in .env"),
        ("ollama:llama9:70b", RefusedError(404), "ollama pull llama9:70b"),
        ("google_genai:gemini-9", RefusedError(404), "check the model id"),
        (
            "ollama:llama3.2:3b",
            ConnectionError("Failed to connect to Ollama"),
            "start Ollama, or set OLLAMA_BASE",
        ),
    ],
)
def test_each_preflight_failure_names_its_fix(settings, model, outcome, fix):
    result = preflight(
        replace(settings, orchestrator=model), parse_prices(PRICE_PAYLOAD), build=builder(outcome)
    )
    assert result.llm is None
    assert fix in result.problem


def test_openrouter_needs_a_price(settings):
    ok = builder(ai_reply("OK"))
    haiku = replace(settings, orchestrator="openrouter:anthropic/claude-haiku-5.5")
    assert preflight(haiku, None, build=ok).problem == (
        "prices unavailable; the budget cannot be enforced"
    )
    unknown = replace(settings, orchestrator="openrouter:nobody/nothing")
    assert preflight(unknown, parse_prices(PRICE_PAYLOAD), build=ok).problem == (
        "nobody/nothing is not in the OpenRouter price list; check the model id"
    )
    good = preflight(haiku, parse_prices(PRICE_PAYLOAD), build=ok)
    assert (good.problem, good.price) == (None, HAIKU) and good.llm is not None


def test_a_failed_preflight_skips_only_that_model(tmp_path, monkeypatch, capsys):
    missing = InexError("missing_package", "Install it with: pip install langchain-openai")
    payload = {"data": [{"id": "x/y", "pricing": {"prompt": "0", "completion": "0"}}]}
    code, results = run_main(
        tmp_path,
        monkeypatch,
        ["--models", "openrouter:x/y", "ollama:fake", "--only", "route-count", "--runs", "1"],
        build=scripted_builder(fail=("openrouter:x/y", missing)),
        get=lambda url, timeout: FakeResponse(payload),
    )
    assert code == 1
    assert "pip install langchain-openai" in capsys.readouterr().out
    assert results.manifest["status"]["openrouter:x/y"].startswith("skipped: ")
    assert results.manifest["status"]["ollama:fake"] == "complete"
    assert [r["model"] for r in results.records()] == ["ollama:fake"]


def test_estimate_calls_no_model_and_writes_nothing(tmp_path, monkeypatch, capsys):
    code, results = run_main(
        tmp_path,
        monkeypatch,
        ["--models", "ollama:fake", "--estimate"],
        build=lambda s: pytest.fail("built a model"),
    )
    assert (code, results) == (0, None)
    out = capsys.readouterr().out
    assert "ollama:fake: estimated" in out and "not priced" in out


def test_a_provider_that_fails_to_build_is_skipped_with_its_key(settings):
    def build(s):
        raise ValueError("API key required for Gemini Developer API.")

    gemini = replace(settings, orchestrator="google_genai:gemini-2.5-flash")
    result = preflight(gemini, None, build=build)
    assert result.llm is None
    assert "set GOOGLE_API_KEY in .env" in result.problem


def test_a_model_that_cannot_be_built_does_not_stop_the_others(tmp_path, monkeypatch):
    unknown = ValueError("Unable to infer model provider for model='qwen3:4b'")
    code, results = run_main(
        tmp_path,
        monkeypatch,
        ["--models", "qwen3:4b", "ollama:fake", "--only", "route-count", "--runs", "1"],
        build=scripted_builder(fail=("qwen3:4b", unknown)),
    )
    assert code == 1
    assert results.manifest["status"]["qwen3:4b"].startswith("skipped: ")
    assert results.manifest["status"]["ollama:fake"] == "complete"
