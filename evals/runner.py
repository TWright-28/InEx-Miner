import json
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

from evals.checks import ToolCall, Turn, passed, score
from evals.world import build_world
from inex.agent.chat import build_agent, render_system_prompt
from inex.agent.tools import build_tools

RECURSION_LIMIT = 25
RETRY_WAITS = (5, 20)
CHARS_PER_TOKEN = 3


@dataclass(frozen=True)
class Trace:
    model: str
    scenario: str
    category: str
    run: int
    outcome: str
    error: dict | None
    turns: tuple
    checks: tuple
    rejected_calls: int
    input_tokens: int
    output_tokens: int
    cost: float | None
    seconds: float


@dataclass(frozen=True)
class _Finished:
    turns: list
    messages: list
    recursion: GraphRecursionError | None
    forced: list
    system: str
    schemas: list


class _AttemptError(Exception):
    def __init__(self, error, usage):
        super().__init__(str(error))
        self.error = error
        self.usage = usage


def agent_prompt(app) -> tuple[str, list[dict]]:
    template = app.settings.system_prompt.read_text(encoding="utf-8")
    system = render_system_prompt(template, app.profile)
    return system, [convert_to_openai_tool(t) for t in build_tools(app)]


def _text(content):
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
    return "".join(parts)


def _result(message):
    if message is None:
        return None, None
    data = None
    if isinstance(message.content, str):
        try:
            data = json.loads(message.content)
        except ValueError:
            data = None
    if isinstance(data, dict) and "status" in data:
        return data["status"], data.get("code")
    if message.status == "error":
        return "error", "tool_error"
    return "ok", None


def _defaults(app):
    return {
        t.name: {param: spec.get("default") for param, spec in t.args.items()}
        for t in build_tools(app)
    }


def _turn(user, messages, defaults):
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    calls = []
    invalid = 0
    reply = ""
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            args = {**defaults.get(call["name"], {}), **(call.get("args") or {})}
            status, code = _result(results.get(call["id"]))
            calls.append(ToolCall(call["name"], args, status, code))
        invalid += len(message.invalid_tool_calls)
        reply = _text(message.content)
    return Turn(user, tuple(calls), invalid, reply)


def _usage(messages):
    spent_in = spent_out = 0
    for message in messages:
        usage = getattr(message, "usage_metadata", None) or {}
        spent_in += usage.get("input_tokens") or 0
        spent_out += usage.get("output_tokens") or 0
    return spent_in, spent_out


def _prompt_tokens(system, schemas, messages):
    chars = len(system) + len(json.dumps(schemas))
    for message in messages:
        chars += len(_text(message.content))
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            chars += len(json.dumps(tool_calls, default=str))
    return chars // CHARS_PER_TOKEN


def _messages(agent, config):
    return agent.get_state(config).values.get("messages", [])


def _attempt(scenario, llm, settings, root):
    world = build_world(scenario.world, root, settings)
    try:
        agent = build_agent(world.app, InMemorySaver(), llm=llm)
        config = {
            "configurable": {"thread_id": uuid.uuid4().hex},
            "recursion_limit": RECURSION_LIMIT,
        }
        defaults = _defaults(world.app)
        system, schemas = agent_prompt(world.app)
        turns = []
        messages = []
        recursion = None
        for user in scenario.turns:
            try:
                agent.invoke({"messages": [{"role": "user", "content": user}]}, config=config)
            except GraphRecursionError as error:
                recursion = error
            except Exception as error:
                raise _AttemptError(error, _usage(_messages(agent, config))) from error
            seen = len(messages)
            messages = _messages(agent, config)
            turns.append(_turn(user, messages[seen:], defaults))
            if recursion is not None:
                break
        return _Finished(turns, messages, recursion, list(world.forced), system, schemas)
    finally:
        world.close()


def _outcome(finished, results, num_ctx):
    if num_ctx is not None:
        tokens = _prompt_tokens(finished.system, finished.schemas, finished.messages)
        if tokens > num_ctx:
            message = f"about {tokens} tokens, more than num_ctx {num_ctx}"
            return "errored", {"kind": "context_overflow", "message": message}
    if finished.recursion is not None:
        return "failed", {"kind": "recursion_limit", "message": str(finished.recursion)}
    return ("passed" if passed(results) else "failed"), None


def run_scenario(
    scenario, *, model, run, llm, settings, num_ctx=None, retries=2, wait=time.sleep
) -> Trace:
    abandoned_in = abandoned_out = 0
    seconds = 0.0
    last = None
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
                finished = _attempt(scenario, llm, settings, Path(folder))
        except _AttemptError as failed:
            seconds = time.monotonic() - started
            abandoned_in += failed.usage[0]
            abandoned_out += failed.usage[1]
            last = failed.error
            if attempt < retries:
                wait(RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)])
            continue
        seconds = time.monotonic() - started
        results = score(scenario.checks, finished.turns)
        outcome, error = _outcome(finished, results, num_ctx)
        expected = scenario.world.expected_errors
        errors = sum(
            1
            for t in finished.turns
            for c in t.tool_calls
            if c.status == "error" and c.code not in expected
        )
        invalid = sum(t.invalid_tool_calls for t in finished.turns)
        spent_in, spent_out = _usage(finished.messages)
        return Trace(
            model=model,
            scenario=scenario.id,
            category=scenario.category,
            run=run,
            outcome=outcome,
            error=error,
            turns=tuple(finished.turns),
            checks=results,
            rejected_calls=max(0, errors - len(finished.forced)) + invalid,
            input_tokens=spent_in + abandoned_in,
            output_tokens=spent_out + abandoned_out,
            cost=None,
            seconds=round(seconds, 3),
        )
    return Trace(
        model=model,
        scenario=scenario.id,
        category=scenario.category,
        run=run,
        outcome="errored",
        error={"kind": type(last).__name__, "message": str(last)},
        turns=(),
        checks=(),
        rejected_calls=0,
        input_tokens=abandoned_in,
        output_tokens=abandoned_out,
        cost=None,
        seconds=round(seconds, 3),
    )
