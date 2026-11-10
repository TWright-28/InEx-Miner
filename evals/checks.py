import re
from dataclasses import dataclass

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
REPLY_PREVIEW_CHARS = 80


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict
    status: str | None
    code: str | None


@dataclass(frozen=True)
class Turn:
    user: str
    tool_calls: tuple
    invalid_tool_calls: int
    reply: str


@dataclass(frozen=True)
class CheckResult:
    check: str
    passed: bool
    reason: str


def _describe_constraint(param, constraint):
    if constraint.kind == "absent":
        return f"{param} absent"
    if constraint.kind == "one_of":
        return f"{param} in {list(constraint.value)}"
    if constraint.kind == "matches":
        return f"{param}~/{constraint.value}/"
    return f"{param}={constraint.value!r}"


def describe(check) -> str:
    if check.kind == "order":
        text = f"order {check.before} -> {check.tool}"
    elif check.kind in ("args", "never"):
        parts = [_describe_constraint(p, c) for p, c in check.constraints]
        text = " ".join([check.kind, check.tool, *parts])
    elif check.kind == "max_calls":
        text = " ".join(str(x) for x in ("max_calls", check.tool, check.n) if x is not None)
    elif check.kind == "reply":
        text = f"reply /{check.pattern}/"
    else:
        text = f"{check.kind} {check.tool}"
    if check.turn is not None:
        text += f" (turn {check.turn})"
    return text


def _equals(value, expected):
    if value is None:
        return False
    if isinstance(expected, bool):
        if isinstance(value, bool):
            return value is expected
        return isinstance(value, str) and value.strip().lower() == str(expected).lower()
    if isinstance(expected, int | float):
        if isinstance(value, int | float) and not isinstance(value, bool):
            return value == expected
        return isinstance(value, str) and value.strip() == str(expected)
    if isinstance(expected, str) and DATE.fullmatch(expected):
        return str(value)[:10] == expected
    return str(value).strip().casefold() == str(expected).strip().casefold()


def satisfies(value, constraint) -> bool:
    if constraint.kind == "absent":
        return value is None
    if constraint.kind == "one_of":
        return any(_equals(value, option) for option in constraint.value)
    if constraint.kind == "matches":
        text = "" if value is None else str(value)
        return re.search(constraint.value, text, re.IGNORECASE) is not None
    return _equals(value, constraint.value)


def _calls(check, turns):
    if check.turn is None:
        return [c for t in turns for c in t.tool_calls]
    if check.turn > len(turns):
        return []
    return list(turns[check.turn - 1].tool_calls)


def _reply(check, turns):
    if not turns:
        return ""
    if check.turn is None:
        return turns[-1].reply
    if check.turn > len(turns):
        return ""
    return turns[check.turn - 1].reply


def _first(calls, name):
    return next((i for i, c in enumerate(calls, 1) if c.name == name), None)


def _matches_all(call, constraints):
    return all(satisfies(call.args.get(p), c) for p, c in constraints)


def _judge(check, turns):
    calls = _calls(check, turns)
    if check.kind in ("called", "not_called"):
        step = _first(calls, check.tool)
        reason = "never called" if step is None else f"called at step {step}"
        return (step is not None) == (check.kind == "called"), reason
    if check.kind == "order":
        step = _first(calls, check.tool)
        if step is None:
            return False, f"{check.tool} never called"
        before = _first(calls[: step - 1], check.before)
        if before is None:
            return False, f"no {check.before} before {check.tool} at step {step}"
        return True, f"{check.before} at step {before}, {check.tool} at step {step}"
    if check.kind in ("args", "never"):
        mine = [(i, c) for i, c in enumerate(calls, 1) if c.name == check.tool]
        hit = next((i for i, c in mine if _matches_all(c, check.constraints)), None)
        if check.kind == "never":
            if hit is None:
                return True, f"no {check.tool} call matched"
            return False, f"matched at step {hit}"
        if not mine:
            return False, f"{check.tool} never called"
        if hit is None:
            return False, f"no {check.tool} call matched ({len(mine)} calls)"
        return True, f"matched at step {hit}"
    if check.kind == "max_calls":
        made = sum(1 for c in calls if check.tool is None or c.name == check.tool)
        return made <= check.n, f"made {made}, limit {check.n}"
    reply = _reply(check, turns)
    if re.search(check.pattern, reply, re.IGNORECASE):
        return True, "matched"
    return False, f"no match in {reply[:REPLY_PREVIEW_CHARS]!r}"


def score(checks, turns) -> tuple[CheckResult, ...]:
    results = []
    for check in checks:
        ok, reason = _judge(check, turns)
        results.append(CheckResult(describe(check), ok, reason))
    return tuple(results)


def passed(results) -> bool:
    return all(r.passed for r in results)
