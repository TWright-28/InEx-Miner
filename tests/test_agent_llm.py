import sys
from dataclasses import replace

import pytest
from fakes import ScriptedChatModel, ai_reply, fake_app
from langgraph.checkpoint.memory import InMemorySaver

from inex.agent import chat
from inex.config import load_settings
from inex.errors import InexError


def openrouter(settings, key="sk-or-test"):
    return replace(
        settings,
        orchestrator="openrouter:anthropic/claude-haiku-5.5",
        openrouter_api_key=key,
        openrouter_base="https://openrouter.test/api/v1",
    )


def test_openrouter_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    monkeypatch.delenv("OPENROUTER_BASE", raising=False)
    settings = load_settings(env_file=None)
    assert settings.openrouter_api_key == "sk-or-env"
    assert settings.openrouter_base == "https://openrouter.ai/api/v1"


def test_openrouter_builds_chat_openai(settings):
    llm = chat.build_llm(openrouter(settings))
    assert type(llm).__name__ == "ChatOpenAI"
    assert llm.model_name == "anthropic/claude-haiku-5.5"
    assert llm.openai_api_base == "https://openrouter.test/api/v1"
    assert llm.openai_api_key.get_secret_value() == "sk-or-test"
    assert llm.temperature == 0.2


def test_openrouter_without_a_key_names_the_variable(settings):
    with pytest.raises(InexError) as e:
        chat.build_llm(openrouter(settings, key=None))
    assert e.value.code == "missing_api_key"
    assert e.value.message == "OPENROUTER_API_KEY is not set; add it to .env"


def test_missing_openrouter_package_names_the_install(settings, monkeypatch):
    monkeypatch.setitem(sys.modules, "langchain_openai", None)
    with pytest.raises(InexError) as e:
        chat.build_llm(openrouter(settings))
    assert e.value.code == "missing_package"
    assert "pip install langchain-openai" in e.value.message


def test_missing_provider_package_uses_langchains_install_command(settings, monkeypatch):
    def refuse(*args, **kwargs):
        raise ImportError(
            "Initializing ChatMistralAI requires the langchain-mistralai package. "
            "Please install it with `pip install langchain-mistralai`"
        )

    monkeypatch.setattr("langchain.chat_models.init_chat_model", refuse)
    with pytest.raises(InexError) as e:
        chat.build_llm(replace(settings, orchestrator="mistralai:mistral-small"))
    assert e.value.code == "missing_package"
    assert "pip install langchain-mistralai" in e.value.message


def test_build_agent_uses_the_given_llm(settings, monkeypatch):
    monkeypatch.setattr(chat, "build_llm", lambda s: pytest.fail("build_llm was called"))
    app = fake_app(settings)
    llm = ScriptedChatModel(script=[ai_reply("hello")])
    agent = chat.build_agent(app, InMemorySaver(), llm=llm)
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        config={"configurable": {"thread_id": "t"}},
    )
    assert out["messages"][-1].content == "hello"
    app.close()


def test_ollama_uses_the_configured_base(settings):
    llm = chat.build_llm(
        replace(settings, orchestrator="ollama:llama3.2:3b", ollama_base="http://gpu-box:11434")
    )
    assert llm.model == "llama3.2:3b"
    assert llm.base_url == "http://gpu-box:11434"


def test_ollama_output_is_capped_per_call(settings):
    llm = chat.build_llm(replace(settings, orchestrator="ollama:llama3.2:3b"))
    assert llm.num_predict == chat.OLLAMA_MAX_OUTPUT_TOKENS == 8192


def test_reply_text_joins_gemini_parts():
    from langchain_core.messages import AIMessage

    from inex.agent.chat import reply_text

    parts = [
        {"type": "text", "text": "There are 21 issues.", "extras": {"signature": "abc"}},
        {"type": "text", "text": " Proceed?"},
    ]
    assert reply_text(AIMessage(content=parts)) == "There are 21 issues. Proceed?"
    assert reply_text(AIMessage(content="plain")) == "plain"


COUNT_RESULT = {"status": "ok", "owner": "axios", "repo": "axios", "issue_count": 21, "stored": 21}


def count_steps(reply):
    import json

    from langchain_core.messages import AIMessage, ToolMessage

    call = {"name": "count_issues", "args": {"repo_name": "axios/axios"}, "id": "c1"}
    return [
        {"model": {"messages": [AIMessage(content="", tool_calls=[call])]}},
        {
            "tools": {
                "messages": [
                    ToolMessage(
                        content=json.dumps(COUNT_RESULT), name="count_issues", tool_call_id="c1"
                    )
                ]
            }
        },
        {"model": {"messages": [AIMessage(content=reply)]}},
    ]


class FlakyAgent:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.questions = []

    def stream(self, payload, config=None, stream_mode=None):
        from langchain_core.messages import AIMessage

        assert stream_mode == "updates"
        self.questions.append(payload["messages"][0]["content"])
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, str):
            outcome = [{"model": {"messages": [AIMessage(content=outcome)]}}]
        yield from outcome


def test_a_failed_model_call_is_reported_not_raised(caplog):
    agent = FlakyAgent([RuntimeError("503 UNAVAILABLE\nretry later")])
    reply = chat.ask(agent, "hi", {}, show=lambda line: None)
    assert reply == (
        "The model call failed (RuntimeError: 503 UNAVAILABLE). "
        "The conversation is kept; send your message again."
    )
    assert "503 UNAVAILABLE" in caplog.text


def test_each_tool_step_is_shown_before_the_reply():
    shown = []
    clock = iter([10.0, 10.5]).__next__
    agent = FlakyAgent([count_steps("There are 21 issues.")])
    reply = chat.ask(agent, "count axios", {}, show=shown.append, clock=clock)
    assert shown == [
        "  > count_issues axios/axios",
        "    21 issues on GitHub, 21 stored (0.5s)",
    ]
    assert reply == "There are 21 issues."


def run_session(settings, monkeypatch, outcomes, answers):
    agent = FlakyAgent(outcomes)
    monkeypatch.setattr(chat, "build_agent", lambda app, checkpointer: agent)
    typed = iter(answers)

    def type_next(prompt):
        answer = next(typed)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", type_next)
    app = fake_app(settings)
    try:
        chat.run_chat(app)
    finally:
        app.close()
    return agent


def test_blank_lines_are_not_sent_to_the_model(settings, monkeypatch):
    agent = run_session(
        settings, monkeypatch, ["There are 21 issues."], ["", "   ", "count axios", "q"]
    )
    assert agent.questions == ["count axios"]


@pytest.mark.parametrize("last", ["q", EOFError()])
def test_leaving_the_chat_says_where_the_transcript_is(settings, monkeypatch, capsys, last):
    run_session(settings, monkeypatch, ["There are 21 issues."], ["count axios", last])
    (saved,) = settings.transcripts.glob("*.log")
    assert capsys.readouterr().out.splitlines()[-1] == f"Transcript saved to {saved}"


def test_the_chat_continues_after_a_failed_model_call(settings, monkeypatch, capsys):
    outcomes = [RuntimeError("503 UNAVAILABLE"), "There are 21 issues."]
    run_session(settings, monkeypatch, outcomes, ["count axios", "count axios", "q"])
    replies = [line for line in capsys.readouterr().out.splitlines() if line.startswith("Agent:")]
    assert replies == [
        "Agent: The model call failed (RuntimeError: 503 UNAVAILABLE). "
        "The conversation is kept; send your message again.",
        "Agent: There are 21 issues.",
    ]


def test_the_banner_names_the_database_models_and_transcript(settings, monkeypatch, capsys):
    run_session(settings, monkeypatch, [], ["q"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("InEx-Miner chat - thread ")
    assert lines[0].endswith(" - type 'q' to quit")
    assert f"  database      {settings.database} (0 projects, 0 issues, 0 labelled)" in lines
    assert f"  orchestrator  {settings.orchestrator}" in lines
    assert f"  classifier    {settings.classifier}" in lines
    assert "  profile       inex-staged" in lines
    transcript = [line for line in lines if line.startswith("  transcript    ")]
    assert len(transcript) == 1
    assert transcript[0].endswith(".log")


@pytest.mark.parametrize(
    "count, word, shown",
    [(0, "project", "0 projects"), (1, "project", "1 project"), (2156, "issue", "2,156 issues")],
)
def test_banner_counts_read_naturally(count, word, shown):
    assert chat.counted(count, word) == shown


def test_the_welcome_says_what_the_agent_can_do(settings, monkeypatch, capsys):
    run_session(settings, monkeypatch, [], ["q"])
    out = capsys.readouterr().out
    assert "What I can do (just ask in plain words):" in out
    assert "  classify   Classify them as Intrinsic, Extrinsic or Not a Bug" in out
    assert f"Report a problem: {chat.ISSUES_URL}" in out
    (saved,) = settings.transcripts.glob("*.log")
    assert "What I can do (just ask in plain words):" in saved.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "labels, phrase",
    [
        (("Critical", "Major", "Minor"), "Critical, Major or Minor"),
        (("Bug", "Not a Bug"), "Bug or Not a Bug"),
    ],
)
def test_the_welcome_names_the_profile_labels(labels, phrase):
    assert f"  classify   Classify them as {phrase}" in chat.welcome(labels)


def test_each_session_is_saved_as_a_transcript(settings, monkeypatch, capsys):
    import logging

    enabled = []

    def steps_with_progress():
        progress = logging.getLogger("inex.progress")
        enabled.append(progress.isEnabledFor(logging.DEBUG))
        extra = {"task": "Collecting axios/axios", "done": 10, "total": 21, "finished": False}
        progress.debug("Collecting axios/axios: 10 of 21", extra=extra)
        progress.info("axios/axios: 10 of 21 issues collected")
        yield from count_steps("There are 21 issues.")

    run_session(settings, monkeypatch, [steps_with_progress()], ["count axios", "q"])
    assert enabled == [True]
    files = list(settings.transcripts.glob("*.log"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "  orchestrator  " in text
    assert "You: count axios\n" in text
    assert "  > count_issues axios/axios\n" in text
    assert "    axios/axios: 10 of 21 issues collected\n" in text
    assert "Collecting axios/axios" not in text
    assert "Agent: There are 21 issues.\n" in text
    assert text.endswith("You: q\n")
    assert not logging.getLogger("inex.progress").handlers


def test_steps_are_read_from_a_real_agent_stream(settings):
    from fakes import ai_call

    app = fake_app(settings)
    llm = ScriptedChatModel(
        script=[ai_call(("count_issues", {"repo_name": "facebook/react"})), ai_reply("Done.")]
    )
    agent = chat.build_agent(app, InMemorySaver(), llm=llm)
    shown = []
    try:
        reply = chat.ask(
            agent, "count react", {"configurable": {"thread_id": "t"}}, show=shown.append
        )
    finally:
        app.close()
    assert shown[0] == "  > count_issues facebook/react"
    assert shown[1].startswith("    14,779 issues on GitHub, 0 stored (")
    assert reply == "Done."
