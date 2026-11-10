import datetime
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

VERSION = 1
CATEGORIES = ("routing", "gates", "arguments", "workflows", "errors", "paraphrases")
FORCEABLE = (
    "count_issues",
    "collect_all",
    "preview_classification",
    "classify_all",
    "snapshot_dependencies",
)
SCENARIO_KEYS = ("id", "category", "rule", "turns", "world", "checks", "paraphrase_of")
REQUIRED = ("id", "category", "rule", "turns")
WORLD_TYPES = {
    "issue_count": int,
    "backlog": int,
    "classify_failed": dict,
    "classified": bool,
    "errors": dict,
    "expected_errors": list,
}
TYPE_NAMES = {
    int: "a whole number",
    bool: "true or false",
    dict: "a mapping",
    list: "a list of error codes",
}
CHECK_FORMS = ("called", "not_called", "order", "args", "never", "max_calls", "reply")
CONSTRAINT_KINDS = ("absent", "one_of", "matches")
SEEDED_ISSUES = 3


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Constraint:
    kind: str
    value: object


@dataclass(frozen=True)
class Check:
    kind: str
    turn: int | None = None
    tool: str | None = None
    before: str | None = None
    constraints: tuple = ()
    n: int | None = None
    pattern: str | None = None


@dataclass(frozen=True)
class WorldSpec:
    issue_count: int = 45
    backlog: int = 10
    classify_failed: dict = field(default_factory=dict)
    classified: bool = True
    errors: dict = field(default_factory=dict)
    expected_errors: tuple = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    rule: str
    turns: tuple
    world: WorldSpec
    checks: tuple
    paraphrase_of: str | None = None


def _plain(value):
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _pattern(sid, pattern):
    if not isinstance(pattern, str):
        raise ScenarioError(f"{sid}: a regular expression must be a string, got {pattern!r}")
    try:
        re.compile(pattern)
    except re.error as error:
        raise ScenarioError(f"{sid}: bad regular expression {pattern!r}: {error}") from error
    return pattern


def _constraint(sid, value):
    if isinstance(value, list):
        raise ScenarioError(f"{sid}: a list of values needs one_of")
    if not isinstance(value, dict):
        return Constraint("equals", value)
    if len(value) != 1 or next(iter(value)) not in CONSTRAINT_KINDS:
        raise ScenarioError(f"{sid}: unknown constraint {', '.join(map(repr, value))}")
    kind, inner = next(iter(value.items()))
    if kind == "one_of":
        if not isinstance(inner, list):
            raise ScenarioError(f"{sid}: one_of needs a list, got {inner!r}")
        return Constraint("one_of", tuple(inner))
    if kind == "matches":
        return Constraint("matches", _pattern(sid, inner))
    if inner is not True:
        raise ScenarioError(f"{sid}: absent must be true")
    return Constraint("absent", True)


def _tool_args(sid, form, value):
    if not isinstance(value, dict) or "tool" not in value:
        raise ScenarioError(f"{sid}: {form} needs a tool")
    constraints = tuple(
        (param, _constraint(sid, item)) for param, item in value.items() if param != "tool"
    )
    return {"tool": value["tool"], "constraints": constraints}


def _max_calls(sid, value):
    if not isinstance(value, dict) or set(value) - {"n", "tool"} or "n" not in value:
        raise ScenarioError(f"{sid}: max_calls takes n and an optional tool")
    if not isinstance(value["n"], int) or isinstance(value["n"], bool):
        raise ScenarioError(f"{sid}: max_calls n must be a whole number")
    return {"n": value["n"], "tool": value.get("tool")}


def _check_fields(sid, form, value):
    if form in ("called", "not_called"):
        return {"tool": value}
    if form == "order":
        if not isinstance(value, list) or len(value) != 2:
            raise ScenarioError(f"{sid}: order needs two tools")
        return {"before": value[0], "tool": value[1]}
    if form in ("args", "never"):
        return _tool_args(sid, form, value)
    if form == "max_calls":
        return _max_calls(sid, value)
    return {"pattern": _pattern(sid, value)}


def _check(sid, raw):
    if not isinstance(raw, dict):
        raise ScenarioError(f"{sid}: a check must be a mapping, got {raw!r}")
    forms = [key for key in raw if key != "turn"]
    for form in forms:
        if form not in CHECK_FORMS:
            raise ScenarioError(f"{sid}: unknown check form {form!r}")
    if len(forms) != 1:
        raise ScenarioError(f"{sid}: a check has exactly one form, got {forms}")
    turn = raw.get("turn")
    if turn is not None and (not isinstance(turn, int) or isinstance(turn, bool) or turn < 1):
        raise ScenarioError(f"{sid}: turn must be a whole number from 1, got {turn!r}")
    return Check(kind=forms[0], turn=turn, **_check_fields(sid, forms[0], raw[forms[0]]))


def _is_a(value, expected):
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, expected)


def _world(sid, raw):
    if raw is None:
        return WorldSpec()
    if not isinstance(raw, dict):
        raise ScenarioError(f"{sid}: world must be a mapping")
    for key, value in raw.items():
        expected = WORLD_TYPES.get(key)
        if expected is None:
            raise ScenarioError(f"{sid}: unknown world key {key!r}")
        if not _is_a(value, expected):
            raise ScenarioError(f"{sid}: world {key} must be {TYPE_NAMES[expected]}")
    issue_count = raw.get("issue_count", WorldSpec.issue_count)
    backlog = raw.get("backlog", WorldSpec.backlog)
    if issue_count < SEEDED_ISSUES + backlog:
        raise ScenarioError(
            f"{sid}: world issue_count {issue_count} must cover the {SEEDED_ISSUES} "
            f"seeded issues and the backlog of {backlog}"
        )
    for count in (raw.get("classify_failed") or {}).values():
        if not _is_a(count, int):
            raise ScenarioError(
                f"{sid}: world classify_failed must map error codes to whole numbers"
            )
    for tool in raw.get("errors") or {}:
        if tool not in FORCEABLE:
            raise ScenarioError(f"{sid}: errors cannot name {tool!r}")
    return WorldSpec(**{**raw, "expected_errors": _expected_errors(sid, raw)})


def _expected_errors(sid, raw):
    codes = raw.get("expected_errors") or []
    if not all(isinstance(code, str) and code.strip() for code in codes):
        raise ScenarioError(f"{sid}: world expected_errors must be {TYPE_NAMES[list]}")
    forced = set((raw.get("errors") or {}).values())
    if raw.get("classified") is False:
        forced.add("no_classifications")
    for code in codes:
        if code in forced:
            raise ScenarioError(
                f"{sid}: world expected_errors repeats {code!r}, which the world already forces"
            )
    return tuple(codes)


def _scenario(raw, index, seen, tool_names):
    if not isinstance(raw, dict):
        raise ScenarioError(f"scenario {index + 1}: must be a mapping")
    sid = raw.get("id", f"scenario {index + 1}")
    for key in raw:
        if key not in SCENARIO_KEYS:
            raise ScenarioError(f"{sid}: unknown key {key!r}")
    for key in REQUIRED:
        if key not in raw:
            raise ScenarioError(f"{sid}: missing {key!r}")
    if "checks" not in raw and "paraphrase_of" not in raw:
        raise ScenarioError(f"{sid}: missing 'checks'")
    if sid in seen:
        raise ScenarioError(f"{sid}: duplicate id")
    if raw["category"] not in CATEGORIES:
        raise ScenarioError(f"{sid}: unknown category {raw['category']!r}")
    if "paraphrase_of" in raw and raw["category"] != "paraphrases":
        raise ScenarioError(f"{sid}: paraphrase_of is only for the paraphrases category")
    turns = raw["turns"]
    if not isinstance(turns, list) or not turns or not all(isinstance(t, str) for t in turns):
        raise ScenarioError(f"{sid}: turns must be a list of messages")
    if not isinstance(raw["rule"], str) or not raw["rule"].strip():
        raise ScenarioError(f"{sid}: rule must be text")
    world = _world(sid, raw.get("world"))
    checks = tuple(_check(sid, item) for item in raw.get("checks") or [])
    for check in checks:
        for tool in (check.tool, check.before):
            if tool is not None and tool not in tool_names:
                raise ScenarioError(f"{sid}: unknown tool {tool!r}")
    return Scenario(
        id=sid,
        category=raw["category"],
        rule=raw["rule"],
        turns=tuple(turns),
        world=world,
        checks=checks,
        paraphrase_of=raw.get("paraphrase_of"),
    ), raw


def _inherit(scenario, raw, originals):
    if scenario.paraphrase_of is None:
        return scenario
    original = originals.get(scenario.paraphrase_of)
    if original is None:
        raise ScenarioError(
            f"{scenario.id}: paraphrase_of names a missing scenario {scenario.paraphrase_of!r}"
        )
    return replace(
        scenario,
        world=scenario.world if "world" in raw else original.world,
        checks=scenario.checks if "checks" in raw else original.checks,
    )


def _check_turns(scenario):
    for check in scenario.checks:
        if check.turn is not None and check.turn > len(scenario.turns):
            raise ScenarioError(
                f"{scenario.id}: turn {check.turn} is beyond the {len(scenario.turns)} turns"
            )


def load_scenarios(path: Path, tool_names) -> tuple[Scenario, ...]:
    path = Path(path)
    data = _plain(yaml.safe_load(path.read_text(encoding="utf-8")))
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ScenarioError(f"{path.name}: version must be {VERSION}")
    if not isinstance(data.get("scenarios"), list):
        raise ScenarioError(f"{path.name}: scenarios must be a list")
    parsed = []
    seen = set()
    for index, raw in enumerate(data["scenarios"]):
        scenario, source = _scenario(raw, index, seen, tool_names)
        seen.add(scenario.id)
        parsed.append((scenario, source))
    originals = {s.id: s for s, _ in parsed if s.paraphrase_of is None}
    scenarios = tuple(_inherit(scenario, source, originals) for scenario, source in parsed)
    for scenario in scenarios:
        _check_turns(scenario)
    return scenarios


def select(scenarios, only) -> tuple[Scenario, ...]:
    if not only:
        return tuple(scenarios)
    ids = {s.id for s in scenarios}
    for name in only:
        if name not in CATEGORIES and name not in ids:
            raise ScenarioError(f"--only: no category or scenario named {name!r}")
    wanted = set(only)
    return tuple(s for s in scenarios if s.category in wanted or s.id in wanted)
