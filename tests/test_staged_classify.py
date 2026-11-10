import os
import re

import pytest
from langchain_core.messages import AIMessage

from inex.services.classification import ClassificationService

STAGE = "google_genai:gemini-3-flash-preview"
JUDGE = "google_genai:gemini-3.1-pro-preview"
ISSUE = {"owner": "acme", "repo": "widget", "number": 1, "title": "t", "body": "b"}


def answer(label):
    return f"Reasoning here.\n**Confidence:** High\n**Final Answer:** {label}"


def build(profile, model=STAGE, **kwargs):
    os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-used")
    service = ClassificationService(None, profile, model, 0.2, **kwargs)
    service.retry_wait = lambda *a, **k: 0
    return service


@pytest.fixture
def staged(staged_profile):
    return build(staged_profile, judge=JUDGE, thinking="medium", judge_thinking="low")


@pytest.fixture
def staged_ollama(staged_profile):
    return build(staged_profile, model="qwen3:30b", judge="qwen3:8b")


def script(service, replies):
    calls = []

    def fake(prompt, role):
        calls.append(role)
        reply = replies[role].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    service._callText = fake
    return calls


def test_staged_issue_gets_the_final_label_and_stage_records(staged):
    script(staged, {"stage": [answer("Bug"), answer("Extrinsic")], "judge": []})
    r = staged.classifyOne(ISSUE)
    assert r.ok and r.label == "Extrinsic"
    assert r.probs == {} and r.attempts == 2
    assert r.raw_text == answer("Extrinsic")
    assert [s["stage"] for s in r.stages] == ["validity", "origin"]
    assert [s["model"] for s in r.stages] == [STAGE, STAGE]
    assert r.stages[0]["confidence"] == "High"


def test_only_recover_uses_the_judge(staged):
    calls = script(staged, {"stage": [answer("Not a Bug")], "judge": [answer("Not a Bug")]})
    r = staged.classifyOne(ISSUE)
    assert r.label == "Not a Bug"
    assert calls == ["stage", "judge"]
    assert r.stages[1]["model"] == JUDGE


def test_unreadable_reply_is_retried_then_succeeds(staged):
    script(staged, {"stage": ["no answer", answer("Bug"), answer("Intrinsic")], "judge": []})
    r = staged.classifyOne(ISSUE)
    assert r.ok and r.label == "Intrinsic" and r.attempts == 3


def test_unreadable_recover_after_retries_fails_without_a_label(staged):
    staged.retry_attempts = 2
    script(staged, {"stage": [answer("Not a Bug")], "judge": ["nothing", "still nothing"]})
    r = staged.classifyOne(ISSUE)
    assert r.status == "parse_error" and r.label is None
    assert r.raw_text == "still nothing"
    assert [s["stage"] for s in r.stages] == ["validity"]
    assert r.attempts == 3


def test_empty_reply_is_retryable(staged):
    script(staged, {"stage": ["  ", answer("Bug"), answer("Extrinsic")], "judge": []})
    r = staged.classifyOne(ISSUE)
    assert r.ok and r.attempts == 3


def test_other_errors_carry_the_attempt_count(staged):
    script(staged, {"stage": [answer("Bug"), ValueError("boom")], "judge": []})
    with pytest.raises(ValueError) as caught:
        staged.classifyOne(ISSUE)
    assert caught.value.classify_attempts == 2


def test_staged_prompt_version_has_the_staged_suffix(staged):
    assert re.fullmatch(r"inex-staged@[0-9a-f]{12}\+s1", staged.prompt_version)
    assert staged.judge == JUDGE


def test_judge_and_thinking_change_the_run_key(staged, staged_profile):
    same_model = build(staged_profile, judge=STAGE, thinking="medium", judge_thinking="low")
    higher = build(staged_profile, judge=JUDGE, thinking="high", judge_thinking="low")
    versions = {staged.prompt_version, same_model.prompt_version, higher.prompt_version}
    assert len(versions) == 3
    assert same_model.judge == STAGE


def test_missing_judge_falls_back_to_the_stage_model(staged_profile):
    assert build(staged_profile, model="qwen3:30b").judge == "qwen3:30b"


def test_single_call_prompt_version_ignores_staged_settings(inex_profile):
    a = build(inex_profile, model="qwen3:30b")
    b = build(inex_profile, model="qwen3:30b", judge="x:y", thinking="high", judge_thinking="high")
    assert a.prompt_version == b.prompt_version
    assert b.judge is None


class _Model:
    def __init__(self, message):
        self.message = message

    def invoke(self, prompt):
        return self.message


def test_cloud_text_reply_given_as_parts_is_joined(staged, monkeypatch):
    parts = [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
    monkeypatch.setattr(staged, "_textModel", lambda role: _Model(AIMessage(content=parts)))
    assert staged._callText("p", "stage") == "AB"


def test_blocked_empty_reply_is_not_retried(staged, monkeypatch):
    blocked = AIMessage(content="", response_metadata={"finish_reason": "SAFETY"})
    monkeypatch.setattr(staged, "_textModel", lambda role: _Model(blocked))
    r = staged.classifyOne(ISSUE)
    assert r.status == "api_error" and r.attempts == 1


def test_gemini_text_model_gets_thinking_and_output_limit(staged, monkeypatch):
    import langchain.chat_models

    seen = {}

    def fake_init(name, **kwargs):
        seen[name] = kwargs
        return object()

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", fake_init)
    staged._textModel("stage")
    staged._textModel("judge")
    staged._textModel("stage")
    assert seen[STAGE]["thinking_level"] == "medium"
    assert seen[JUDGE]["thinking_level"] == "low"
    assert seen[STAGE]["max_output_tokens"] == 8192
    assert seen[STAGE]["temperature"] == 0.2
    assert len(seen) == 2


def test_other_providers_get_max_tokens_and_no_thinking(staged_profile, monkeypatch):
    import langchain.chat_models

    seen = {}
    monkeypatch.setattr(
        langchain.chat_models, "init_chat_model", lambda name, **kw: seen.update(kw) or object()
    )
    service = build(staged_profile, model="openai:gpt-4o", thinking="medium")
    service._textModel("stage")
    assert seen["max_tokens"] == 8192
    assert "thinking_level" not in seen and "max_output_tokens" not in seen


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"response": self.text}


def test_ollama_stage_posts_plain_generate(staged_ollama, monkeypatch):
    import inex.services.classification as classification

    posted = []

    def fake_post(url, json, timeout):
        posted.append((url, json))
        return _Response(answer("Bug"))

    monkeypatch.setattr(classification.requests, "post", fake_post)
    assert staged_ollama._callText("prompt", "judge") == answer("Bug")
    url, payload = posted[0]
    assert url.endswith("/api/generate")
    assert payload["model"] == "qwen3:8b" and payload["prompt"] == "prompt"
    assert payload["stream"] is False


def test_staged_classify_saves_its_stage_records(seeded, staged_profile):
    import json

    db, _, ids = seeded
    service = ClassificationService(db, staged_profile, "qwen3:30b", 0.2, judge="qwen3:8b")
    service.retry_wait = lambda *a, **k: 0
    script(
        service,
        {
            "stage": [
                answer("Bug"),
                answer("Intrinsic"),
                answer("Not a Bug"),
                answer("Not a Bug"),
                answer("Extrinsic"),
            ],
            "judge": [answer("Not a Bug"), answer("Bug")],
        },
    )
    result = service.classify("acme", "widget", order="asc")
    assert result.classified == 3
    stored = db.fetch_readonly(
        "SELECT classification, model, prompt_version, stages FROM classifications ORDER BY id"
    )[1]
    assert {row[1] for row in stored} == {"qwen3:30b"}
    assert {row[2] for row in stored} == {service.prompt_version}
    assert sorted(len(json.loads(row[3])) for row in stored) == [2, 2, 3]


def test_a_hosted_classifier_gets_the_default_judge(staged_profile):
    assert build(staged_profile).judge == "google_genai:gemini-3.1-pro-preview"


def test_gemini_2_models_get_no_thinking_level(staged_profile, monkeypatch):
    import langchain.chat_models

    seen = {}
    monkeypatch.setattr(
        langchain.chat_models, "init_chat_model", lambda name, **kw: seen.update(kw) or object()
    )
    service = build(staged_profile, model="google_genai:gemini-2.5-pro", thinking="medium")
    service._textModel("stage")
    assert "thinking_level" not in seen
    assert seen["max_output_tokens"] == 8192


def test_thinking_a_model_ignores_does_not_change_the_run_key(staged_profile):
    medium = build(staged_profile, model="qwen3:30b", judge="qwen3:8b", thinking="medium")
    high = build(staged_profile, model="qwen3:30b", judge="qwen3:8b", thinking="high")
    assert medium.prompt_version == high.prompt_version
