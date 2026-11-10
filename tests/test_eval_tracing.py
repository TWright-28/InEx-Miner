import os

import pytest
from fakes import ScriptedChatModel, ai_reply, run_main, scripted_builder
from langsmith.utils import tracing_is_enabled

from evals.runner import Trace
from evals.scenarios import Scenario, WorldSpec
from evals.tracing import LangSmithTracer, make_tracer

SCENARIO = Scenario("route-count", "routing", "r", ("hi",), WorldSpec(), ())


class FakeClient:
    def __init__(self, fail=False):
        self.feedback = []
        self.fail = fail

    def create_feedback(self, **kwargs):
        if self.fail:
            raise RuntimeError("401 invalid api key")
        self.feedback.append(kwargs)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def outcome(value):
    return Trace("m", "route-count", "routing", 1, value, None, (), (), 0, 0, 0, None, 0.0)


def test_tracing_is_off_without_langsmith_even_when_env_enables_it(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING", "true")
    with make_tracer(False, "p").session():
        assert tracing_is_enabled() is False


def test_feedback_marks_the_scenario_root():
    client = FakeClient()
    tracer = LangSmithTracer(client, "inex-eval-20261027-090501")
    with tracer.session(), tracer.scenario("ollama:fake", SCENARIO, 2) as handle:
        pass
    tracer.feedback(handle, outcome("failed"))
    assert client.feedback == [
        {"run_id": handle.id, "key": "passed", "score": 0, "comment": "failed"}
    ]
    assert handle.session_name == "inex-eval-20261027-090501"
    assert set(handle.tags) >= {"ollama:fake", "route-count", "routing", "run-2"}


def test_a_rejected_key_warns_once_and_continues(capsys):
    tracer = LangSmithTracer(FakeClient(fail=True), "p")
    for _ in range(2):
        with tracer.session(), tracer.scenario("m", SCENARIO, 1) as handle:
            pass
        tracer.feedback(handle, outcome("passed"))
    assert capsys.readouterr().err.count("LangSmith rejected the feedback") == 1


@pytest.fixture
def restored_environment():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_with_langsmith_the_client_is_created_after_env_is_loaded(
    tmp_path, monkeypatch, restored_environment
):
    seen = {}

    class RecordingClient(FakeClient):
        def __init__(self):
            super().__init__()
            seen["key"] = os.environ.get("LANGCHAIN_API_KEY")

    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    (tmp_path / ".env").write_text("LANGCHAIN_API_KEY=lsv2_test_key\n", encoding="utf-8")
    monkeypatch.setattr("evals.tracing.Client", RecordingClient)
    code, _ = run_main(
        tmp_path,
        monkeypatch,
        ["--langsmith", "--models", "ollama:fake", "--only", "route-count", "--runs", "1"],
        build=scripted_builder(),
    )
    assert code == 0
    assert seen["key"] == "lsv2_test_key"


def test_a_model_runs_untraced_when_env_sets_the_legacy_tracing_flag(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING", "true")
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    with make_tracer(False, "p").session():
        assert ScriptedChatModel(script=[ai_reply("hi")]).invoke("x").content == "hi"
    assert os.environ["LANGCHAIN_TRACING"] == "true"
