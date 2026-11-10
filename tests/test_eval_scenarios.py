from pathlib import Path

import pytest
import yaml
from fakes import fake_app

from evals.checks import ToolCall, Turn, passed, score
from evals.scenarios import CATEGORIES, Constraint, ScenarioError, load_scenarios, select
from inex.agent.tools import TOOL_NAMES, build_tools

SHIPPED = Path(__file__).resolve().parents[1] / "evals" / "scenarios.yaml"
COUNTS = {
    "routing": 10,
    "gates": 10,
    "arguments": 10,
    "workflows": 10,
    "errors": 10,
    "paraphrases": 8,
}
VALID = """\
version: 1
scenarios:
  - id: base
    category: gates
    rule: "Principle 5"
    turns: ["classify the issues for yeoman/yeoman-test", "yes"]
    world: {backlog: 20}
    checks:
      - {not_called: classify_all, turn: 1}
      - {order: [preview_classification, classify_all]}
      - {args: {tool: collect_all, count: 20, end_date: 2023-12-31, start_date: {absent: true}}}
      - {never: {tool: run_sql, query: {matches: '\\bdelete\\b'}}}
      - {max_calls: {tool: count_issues, n: 2}}
      - {reply: '20,?000', turn: 2}
  - id: para
    category: paraphrases
    rule: "Principle 5"
    paraphrase_of: base
    turns: ["label them", "sure"]
"""


def load_text(tmp_path, text):
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")
    return load_scenarios(path, TOOL_NAMES)


def test_the_shipped_file_has_the_spec_counts():
    scenarios = load_scenarios(SHIPPED, TOOL_NAMES)
    assert {c: sum(s.category == c for s in scenarios) for c in CATEGORIES} == COUNTS
    assert len({s.id for s in scenarios}) == 58


def test_every_shipped_scenario_names_a_rule_and_known_tools():
    for s in load_scenarios(SHIPPED, TOOL_NAMES):
        assert s.rule.strip(), s.id
        for check in s.checks:
            assert {check.tool, check.before} - {None} <= set(TOOL_NAMES), s.id


def test_shipped_argument_names_are_real_parameters(settings):
    app = fake_app(settings)
    params = {t.name: set(t.args) for t in build_tools(app)}
    app.close()
    for s in load_scenarios(SHIPPED, TOOL_NAMES):
        for check in s.checks:
            for name, _ in check.constraints:
                assert name in params[check.tool], (s.id, check.tool, name)


def test_the_original_ten_keep_their_requests():
    by_id = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}
    assert by_id["route-count"].turns == ("how many issues does yeoman/yeoman-test have?",)
    assert by_id["gate-classify-decline"].turns == (
        "classify the issues for yeoman/yeoman-test",
        "no, don't classify them",
    )
    assert by_id["gate-collect-large"].world.issue_count == 5000


def test_checks_and_constraints_are_parsed(tmp_path):
    base = load_text(tmp_path, VALID)[0]
    assert (base.world.backlog, base.world.issue_count) == (20, 45)
    assert [(c.kind, c.turn, c.tool, c.before) for c in base.checks] == [
        ("not_called", 1, "classify_all", None),
        ("order", None, "classify_all", "preview_classification"),
        ("args", None, "collect_all", None),
        ("never", None, "run_sql", None),
        ("max_calls", None, "count_issues", None),
        ("reply", 2, None, None),
    ]
    assert dict(base.checks[2].constraints) == {
        "count": Constraint("equals", 20),
        "end_date": Constraint("equals", "2023-12-31"),
        "start_date": Constraint("absent", True),
    }
    assert base.checks[3].constraints == (("query", Constraint("matches", r"\bdelete\b")),)
    assert base.checks[4].n == 2
    assert base.checks[5].pattern == "20,?000"


def test_expected_errors_are_parsed_as_a_tuple(tmp_path):
    text = VALID.replace("world: {backlog: 20}", "world: {expected_errors: [file_not_found]}")
    base = load_text(tmp_path, text)[0]
    assert (base.world.expected_errors, base.world.backlog) == (("file_not_found",), 10)


def test_scenarios_that_set_up_an_error_expect_its_code():
    by_id = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}
    assert by_id["err-npm-package-not-found"].world.expected_errors == ("npm_package_not_found",)
    assert by_id["err-list-not-found"].world.expected_errors == ("file_not_found",)
    assert by_id["route-count"].world.expected_errors == ()


def test_paraphrases_inherit_world_and_checks(tmp_path):
    base, para = load_text(tmp_path, VALID)
    assert para.turns == ("label them", "sure")
    assert (para.world, para.checks, para.paraphrase_of) == (base.world, base.checks, "base")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda s: s[0].update(colour="red"), "base: unknown key 'colour'"),
        (lambda s: s[0].update(category="misc"), "base: unknown category 'misc'"),
        (
            lambda s: s[0]["checks"].append({"calls": "collect_all"}),
            "base: unknown check form 'calls'",
        ),
        (
            lambda s: s[0]["checks"].append({"called": "collect_everything"}),
            "base: unknown tool 'collect_everything'",
        ),
        (
            lambda s: s[0]["checks"].append({"called": "collect_all", "turn": 3}),
            "base: turn 3 is beyond the 2 turns",
        ),
        (lambda s: s[0]["world"].update(weather="rain"), "base: unknown world key 'weather'"),
        (
            lambda s: s[0]["world"].update(errors={"run_sql": "sql_error"}),
            "base: errors cannot name 'run_sql'",
        ),
        (lambda s: s[0].pop("turns"), "base: missing 'turns'"),
        (lambda s: s[0].pop("checks"), "base: missing 'checks'"),
        (lambda s: s.append(dict(s[0])), "base: duplicate id"),
        (
            lambda s: s[1].update(paraphrase_of="nope"),
            "para: paraphrase_of names a missing scenario 'nope'",
        ),
    ],
)
def test_a_malformed_file_fails_with_the_scenario_id(tmp_path, mutate, message):
    data = yaml.safe_load(VALID)
    mutate(data["scenarios"])
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ScenarioError) as e:
        load_scenarios(path, TOOL_NAMES)
    assert str(e.value) == message


def test_the_version_must_be_one(tmp_path):
    with pytest.raises(ScenarioError) as e:
        load_text(tmp_path, VALID.replace("version: 1", "version: 2"))
    assert str(e.value) == "s.yaml: version must be 1"


def test_select_by_category_and_id(tmp_path):
    scenarios = load_text(tmp_path, VALID)
    assert [s.id for s in select(scenarios, None)] == ["base", "para"]
    assert [s.id for s in select(scenarios, ["paraphrases"])] == ["para"]
    assert [s.id for s in select(scenarios, ["para", "base"])] == ["base", "para"]
    with pytest.raises(ScenarioError) as e:
        select(scenarios, ["nope"])
    assert str(e.value) == "--only: no category or scenario named 'nope'"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda s: s[0]["checks"][2]["args"].update(count={"oneof": [1, 2]}),
            "base: unknown constraint 'oneof'",
        ),
        (
            lambda s: s[0]["checks"][2]["args"].update(start_date={"absent": False}),
            "base: absent must be true",
        ),
        (
            lambda s: s[0]["checks"][2]["args"].update(count=[20, 30]),
            "base: a list of values needs one_of",
        ),
        (
            lambda s: s[0]["world"].update(backlog="abc"),
            "base: world backlog must be a whole number",
        ),
        (
            lambda s: s[0]["world"].update(classified="false"),
            "base: world classified must be true or false",
        ),
        (
            lambda s: s[0]["world"].update(classify_failed={"api_error": "3"}),
            "base: world classify_failed must map error codes to whole numbers",
        ),
        (
            lambda s: s[0]["world"].update(expected_errors="file_not_found"),
            "base: world expected_errors must be a list of error codes",
        ),
        (
            lambda s: s[0]["world"].update(expected_errors=[3]),
            "base: world expected_errors must be a list of error codes",
        ),
        (
            lambda s: s[0]["world"].update(
                errors={"count_issues": "repo_not_found"}, expected_errors=["repo_not_found"]
            ),
            "base: world expected_errors repeats 'repo_not_found', which the world already forces",
        ),
        (
            lambda s: s[0]["world"].update(
                classified=False, expected_errors=["no_classifications"]
            ),
            "base: world expected_errors repeats 'no_classifications', "
            "which the world already forces",
        ),
        (
            lambda s: s[0].update(paraphrase_of="para"),
            "base: paraphrase_of is only for the paraphrases category",
        ),
        (
            lambda s: s[0]["world"].update(backlog=50),
            "base: world issue_count 45 must cover the 3 seeded issues and the backlog of 50",
        ),
    ],
)
def test_a_mistyped_value_fails_with_the_scenario_id(tmp_path, mutate, message):
    data = yaml.safe_load(VALID)
    mutate(data["scenarios"])
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ScenarioError) as e:
        load_scenarios(path, TOOL_NAMES)
    assert str(e.value) == message


def test_route_breakdown_needs_a_query_over_the_labels():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}["route-breakdown"].checks
    count_only = "SELECT COUNT(*) FROM issues WHERE repo = 'yeoman-test'"
    breakdown = (
        "SELECT c.classification, COUNT(*) FROM classifications c JOIN issues i "
        "ON i.id = c.issue_id GROUP BY c.classification"
    )

    def run(query):
        call = ToolCall("run_sql", {"query": query}, "ok", None)
        return passed(score(checks, (Turn("u", (call,), 0, "done"),)))

    assert not run(count_only)
    assert run(breakdown)


def test_args_no_history_fails_any_call_that_asks_for_history():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}["args-no-history"].checks
    clean = {"repo_name": "yeoman/yeoman-test", "npm_package": "yeoman-test"}
    blank = {"last_n_versions": None, "version_start": None, "version_end": None}

    def run(*calls):
        made = tuple(
            ToolCall("snapshot_dependencies", {**clean, **blank, **c}, "ok", None) for c in calls
        )
        return passed(score(checks, (Turn("u", made, 0, "done"),)))

    assert run({})
    assert not run({"last_n_versions": 5}, {})
    assert not run({"version_start": "8.0.0"})
    assert not run({"version_end": "9.0.0"})
    assert not run()


def test_flow_snapshot_needs_labels_accepts_checking_the_database_first():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}[
        "flow-snapshot-needs-labels"
    ].checks

    def call(name):
        return ToolCall(name, {}, "ok", None)

    asked = Turn("u", (call("run_sql"), call("preview_classification")), 0, "classify first?")
    done = Turn("u", (call("classify_all"), call("snapshot_dependencies")), 0, "done")
    eager = Turn("u", (call("classify_all"), call("snapshot_dependencies")), 0, "done")
    assert passed(score(checks, (asked, done)))
    assert not passed(score(checks, (eager, done)))


def test_owner_and_typo_scenarios_name_nothing_the_seed_can_resolve():
    by_id = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}
    for sid in ("err-missing-owner", "para-missing-owner", "err-repo-not-found"):
        assert "yeoman-test" not in by_id[sid].turns[0], sid
        assert "yeoman-tset" not in by_id[sid].turns[0], sid


def test_err_repo_not_found_accepts_any_plain_not_found_explanation():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}["err-repo-not-found"].checks
    failed = ToolCall("count_issues", {}, "error", "repo_not_found")

    def run(reply):
        return passed(score(checks, (Turn("u", (failed,), 0, reply),)))

    assert run("GitHub returns repo not found for that name.")
    assert run("GitHub reports no repository by that name (`repo_not_found`).")
    assert run("GitHub reports that the repository doesn’t exist.")
    assert run("I was unable to find the repository `yeoman/generator-mobile-kit` on GitHub.")
    assert not run("Collected 0 issues.")


def test_flow_classify_batches_needs_the_next_batch_oldest_first():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}["flow-classify-batches"].checks

    def run(direction):
        first = ToolCall("classify_all", {"count": 10, "direction": "asc"}, "ok", None)
        second = ToolCall("classify_all", {"count": 15, "direction": direction}, "ok", None)
        turns = (Turn("u", (first,), 0, "done"), Turn("u", (second,), 0, "done"))
        return passed(score(checks, turns))

    assert run("asc")
    assert not run("desc")


def test_flow_compare_projects_accepts_going_ahead_and_rejects_stopping_to_ask():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}["flow-compare-projects"].checks
    generator = {"repo_name": "yeoman/generator", "count": 40}
    labels = "SELECT p.repo, c.classification, COUNT(*) FROM classifications c GROUP BY 1, 2"

    def run(*calls, reply):
        made = tuple(ToolCall(name, args, "ok", None) for name, args in calls)
        return passed(score(checks, (Turn("u", made, 0, reply),)))

    went_ahead = (
        ("collect_all", generator),
        ("preview_classification", generator),
        ("run_sql", {"query": labels}),
        ("classify_all", generator),
        ("run_sql", {"query": labels}),
    )
    compared = "yeoman/generator splits evenly; yeoman/yeoman-test has only 3 labels."
    assert run(*went_ahead, reply=compared)
    assert not run(*went_ahead[:2], reply="Classify 40 yeoman/generator issues, then compare?")
    assert not run(*went_ahead, reply="yeoman/generator splits evenly across the four labels.")


def test_err_npm_package_not_found_needs_the_users_package_tried_first():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}[
        "err-npm-package-not-found"
    ].checks

    def run(package, status, reply):
        call = ToolCall("snapshot_dependencies", {"npm_package": package}, status, None)
        return passed(score(checks, (Turn("u", (call,), 0, reply),)))

    assert run("yeoman-testing", "error", "The npm registry has no package named yeoman-testing.")
    assert run("yeoman-testing", "error", "I couldn't find yeoman-testing on npm.")
    assert run(
        "yeoman-testing",
        "error",
        "The npm registry does not have a package named 'yeoman-testing'.",
    )
    assert not run("yeoman-test", "ok", "Snapshotted 2 versions of yeoman-test.")


def test_err_list_not_found_accepts_any_plain_missing_file_explanation():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}["err-list-not-found"].checks
    missing = ToolCall("preview_issue_list", {}, "error", "file_not_found")

    def run(reply):
        return passed(score(checks, (Turn("u", (missing,), 0, reply),)))

    assert run("I wasn't able to find the file `evals/fixtures/bug_list.csv`.")
    assert run("No issue list exists at that path, so nothing was imported.")
    assert not run("Imported 4 issues.")


def test_err_classify_failures_accepts_retried_as_a_retry():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}["err-classify-failures"].checks
    asked = Turn("u", (ToolCall("preview_classification", {}, "ok", None),), 0, "Classify 10?")
    ran = (ToolCall("classify_all", {}, "ok", None),)

    def run(reply):
        return passed(score(checks, (asked, Turn("u", ran, 0, reply))))

    assert run("7 classified; 3 failed with an API error and can be retried in a subsequent run.")
    assert run("3 failed. Run classify_all again to retry them.")
    assert not run("3 failed with an API error.")


def test_err_export_path_names_the_full_repo():
    turns = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}["err-export-path"].turns
    assert "yeoman/yeoman-test" in turns[0]


def test_err_snapshot_fails_midway_needs_the_failure_named_with_the_snapshot():
    checks = {s.id: s for s in load_scenarios(SHIPPED, TOOL_NAMES)}[
        "err-snapshot-fails-midway"
    ].checks
    calls = (
        ToolCall("preview_classification", {}, "ok", None),
        ToolCall("classify_all", {}, "ok", None),
        ToolCall("snapshot_dependencies", {}, "error", "npm_doc_fetch_failed"),
        ToolCall("snapshot_dependencies", {}, "error", "npm_doc_fetch_failed"),
    )

    def run(reply):
        return passed(score(checks, (Turn("u", calls, 0, reply),)))

    assert run("Classification is done, but the dependency snapshot failed twice.")
    assert run("Classified 10 issues. The dependency snapshot failed: npm could not be reached.")
    assert run("Classified 10 issues, but I couldn't snapshot the dependencies.")
    assert not run("Classified 10 issues, 0 failed. Dependencies are linked to their versions.")
