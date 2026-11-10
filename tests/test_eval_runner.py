import pytest
from fakes import ScriptedChatModel, ai_call, ai_reply
from langchain_core.messages import AIMessage

from evals.checks import ToolCall
from evals.runner import run_scenario
from evals.scenarios import Check, Scenario, WorldSpec
from evals.world import build_world
from inex.agent.tools import build_tools

REPO = "yeoman/yeoman-test"


@pytest.fixture
def make_world(settings, tmp_path):
    worlds = []

    def make(**spec):
        world = build_world(WorldSpec(**spec), tmp_path / f"w{len(worlds)}", settings)
        worlds.append(world)
        return world, {t.name: t for t in build_tools(world.app)}

    yield make
    for world in worlds:
        world.close()


def sql(tools, query):
    return tools["run_sql"].invoke({"query": query})["rows"]


def test_the_seed_holds_three_labelled_issues_and_the_backlog(make_world):
    _, tools = make_world()
    assert sql(tools, "SELECT COUNT(*) FROM issues") == [[13]]
    assert sql(
        tools,
        "SELECT i.issue_number, c.classification FROM issues i "
        "JOIN classifications c ON c.issue_id = i.id ORDER BY i.issue_number",
    ) == [[101, "Intrinsic"], [102, "Extrinsic"], [103, "Not a Bug"]]
    assert sql(
        tools,
        "SELECT i.issue_number, v.version FROM issues i "
        "JOIN versions v ON v.id = i.version_id ORDER BY i.issue_number",
    ) == [[101, "8.0.0"], [102, "9.0.0"], [103, "9.0.0"]]


def test_preview_reports_the_unlabelled_issues_in_the_database(make_world):
    _, tools = make_world()
    preview = tools["preview_classification"].invoke({"repo_name": REPO})
    assert (preview["would_classify"], preview["total_remaining"]) == (10, 10)
    labelled = sql(tools, "SELECT DISTINCT model, prompt_version FROM classifications")
    assert labelled == [[preview["model"], preview["prompt_version"]]]


def test_counts_and_backlog_reach_the_tools(make_world):
    _, tools = make_world(issue_count=25000, backlog=20000)
    assert tools["count_issues"].invoke({"repo_name": REPO})["issue_count"] == 25000
    preview = tools["preview_classification"].invoke({"repo_name": REPO, "count": 25})
    assert (preview["would_classify"], preview["total_remaining"]) == (25, 20000)


def test_collect_writes_what_it_reports(make_world):
    _, tools = make_world()
    out = tools["collect_all"].invoke({"repo_name": REPO})
    assert (out["collected"], out["skipped"]) == (32, 13)
    assert sql(tools, "SELECT COUNT(*) FROM issues") == [[45]]
    again = tools["collect_all"].invoke({"repo_name": REPO, "count": 5})
    assert (again["collected"], again["skipped"]) == (0, 45)


def test_classify_writes_labels_and_failures_that_a_rerun_clears(make_world):
    _, tools = make_world(classify_failed={"api_error": 3})
    out = tools["classify_all"].invoke({"repo_name": REPO})
    assert (out["classified"], out["failed"]) == (7, {"api_error": 3})
    assert sql(
        tools,
        "SELECT status, COUNT(*) FROM classifications WHERE classification IS NULL GROUP BY status",
    ) == [["api_error", 3]]
    rerun = tools["classify_all"].invoke({"repo_name": REPO})
    assert (rerun["classified"], rerun["failed"]) == (3, {})
    preview = tools["preview_classification"].invoke({"repo_name": REPO})
    assert preview["total_remaining"] == 0


def test_snapshot_needs_classify_all_when_unclassified(make_world):
    world, tools = make_world(classified=False)
    assert sql(tools, "SELECT COUNT(*) FROM versions") == [[0]]
    args = {"repo_name": REPO, "npm_package": "yeoman-test"}
    first = tools["snapshot_dependencies"].invoke(args)
    assert (first["status"], first["code"]) == ("error", "no_classifications")
    tools["classify_all"].invoke({"repo_name": REPO})
    second = tools["snapshot_dependencies"].invoke(args)
    assert (second["status"], second["linked_issues"]) == ("ok", 13)
    assert world.forced == ["snapshot_dependencies"]


def test_snapshot_versions_and_dependency_rows_agree(make_world):
    _, tools = make_world()
    out = tools["snapshot_dependencies"].invoke(
        {"repo_name": REPO, "npm_package": "@yeoman/test", "last_n_versions": 5}
    )
    assert out["status"] == "ok"
    assert sql(
        tools,
        "SELECT COUNT(*) FROM versions v WHERE v.direct_count <> (SELECT COUNT(*) "
        "FROM version_dependencies d WHERE d.version_id = v.id AND d.dep_kind = 'direct')",
    ) == [[0]]
    assert sql(tools, "SELECT COUNT(*) FROM versions") == [[5]]
    missing = tools["snapshot_dependencies"].invoke({"repo_name": REPO, "npm_package": "nope"})
    assert (missing["status"], missing["code"]) == ("error", "npm_package_not_found")


def test_an_unknown_repository_is_not_found(make_world):
    world, tools = make_world()
    out = tools["count_issues"].invoke({"repo_name": "yeoman/yeoman-tset"})
    assert (out["code"], out["message"]) == (
        "repo_not_found",
        "yeoman/yeoman-tset was not found on GitHub.",
    )
    assert tools["collect_all"].invoke({"repo_name": "yeoman/yeoman-tset"})["code"] == (
        "repo_not_found"
    )
    assert world.forced == []


def test_forced_errors_carry_realistic_messages(make_world):
    world, tools = make_world(
        errors={"count_issues": "repo_not_found", "collect_all": "github_auth_failed"}
    )
    assert tools["count_issues"].invoke({"repo_name": "yeoman/yeoman-tset"}) == {
        "status": "error",
        "code": "repo_not_found",
        "message": "yeoman/yeoman-tset was not found on GitHub.",
    }
    assert tools["collect_all"].invoke({"repo_name": REPO})["code"] == "github_auth_failed"
    assert world.forced == ["count_issues", "collect_all"]


def test_import_paths_resolve_from_any_working_directory(make_world, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, tools = make_world()
    path = {"csv_path": "evals/fixtures/issues.csv"}
    out = tools["preview_issue_list"].invoke(path)
    assert (out["status"], out["total_issues"], out["already_in_db"]) == ("ok", 4, 3)
    done = tools["import_issue_list"].invoke(path)
    assert (done["collected"], done["skipped"], done["classified"]) == (1, 3, 3)


def test_the_whole_pipeline_runs_without_the_network(make_world):
    _, tools = make_world(classified=False)
    calls = [
        ("count_issues", {"repo_name": REPO}),
        ("collect_all", {"repo_name": REPO}),
        ("preview_classification", {"repo_name": REPO}),
        ("classify_all", {"repo_name": REPO}),
        ("snapshot_dependencies", {"repo_name": REPO, "npm_package": "yeoman-test"}),
        ("import_issue_list", {"csv_path": "evals/fixtures/issues.csv"}),
    ]
    assert [tools[name].invoke(args)["status"] for name, args in calls] == ["ok"] * 6


def test_closing_the_world_releases_the_database(make_world):
    world, tools = make_world()
    tools["run_sql"].invoke({"query": "SELECT 1"})
    world.close()
    (world.root / "eval.db").unlink()


def scenario(*turns, checks=(), **world):
    return Scenario("s", "gates", "rule", tuple(turns), WorldSpec(**world), tuple(checks))


def run(settings, script, sc, **kwargs):
    return run_scenario(
        sc,
        model="ollama:fake",
        run=1,
        llm=ScriptedChatModel(script=script),
        settings=settings,
        wait=lambda seconds: None,
        **kwargs,
    )


def test_trace_records_calls_effective_args_status_and_tokens(settings):
    sc = scenario(
        "classify the issues for yeoman/yeoman-test",
        "yes, go ahead",
        checks=[
            Check("not_called", tool="classify_all", turn=1),
            Check("called", tool="classify_all", turn=2),
        ],
    )
    script = [
        ai_call(("preview_classification", {"repo_name": REPO})),
        ai_reply("10 issues would be classified. Go ahead?"),
        ai_call(("classify_all", {"repo_name": REPO})),
        ai_reply("Classified 10."),
    ]
    trace = run(settings, script, sc)
    assert (trace.outcome, trace.error, trace.rejected_calls) == ("passed", None, 0)
    first = trace.turns[0]
    assert first.user == "classify the issues for yeoman/yeoman-test"
    assert first.tool_calls[0] == ToolCall(
        "preview_classification",
        {
            "repo_name": REPO,
            "count": None,
            "start_date": None,
            "end_date": None,
            "direction": "desc",
        },
        "ok",
        None,
    )
    assert first.reply == "10 issues would be classified. Go ahead?"
    assert (trace.input_tokens, trace.output_tokens) == (400, 40)
    assert [c.passed for c in trace.checks] == [True, True]


def test_forced_errors_are_not_rejected_calls(settings):
    sc = scenario(
        "collect issues from yeoman/yeoman-tset", errors={"count_issues": "repo_not_found"}
    )
    script = [
        ai_call(("count_issues", {"repo_name": "yeoman/yeoman-tset"})),
        ai_reply("That repository was not found."),
    ]
    trace = run(settings, script, sc)
    call = trace.turns[0].tool_calls[0]
    assert (call.status, call.code, trace.rejected_calls) == ("error", "repo_not_found", 0)


def test_expected_errors_are_not_rejected_calls(settings):
    sc = scenario(
        "snapshot yeoman/yeoman-test using the npm package yeoman-testing",
        expected_errors=("npm_package_not_found",),
    )
    script = [
        ai_call(
            ("snapshot_dependencies", {"repo_name": REPO, "npm_package": "yeoman-testing"}),
            ("collect_all", {"repo_name": "yeoman-test"}),
        ),
        ai_reply("There is no npm package named yeoman-testing."),
    ]
    trace = run(settings, script, sc)
    assert [(c.status, c.code) for c in trace.turns[0].tool_calls] == [
        ("error", "npm_package_not_found"),
        ("error", "invalid_input"),
    ]
    assert trace.rejected_calls == 1


def test_bad_arguments_unknown_tools_and_malformed_calls_are_rejected(settings):
    broken = AIMessage(
        content="",
        tool_calls=[
            {"name": "collect_all", "args": {"repo_name": "yeoman-test"}, "id": "a"},
            {"name": "collect_everything", "args": {}, "id": "b"},
        ],
        invalid_tool_calls=[{"name": "run_sql", "args": "{oops", "id": "c", "error": "bad json"}],
        usage_metadata={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
    )
    trace = run(settings, [broken, ai_reply("Sorry.")], scenario("collect issues from yeoman-test"))
    assert [(c.name, c.status, c.code) for c in trace.turns[0].tool_calls] == [
        ("collect_all", "error", "invalid_input"),
        ("collect_everything", "error", "tool_error"),
    ]
    assert (trace.turns[0].invalid_tool_calls, trace.rejected_calls) == (1, 3)


def test_provider_exceptions_are_retried_then_errored(settings):
    waits = []
    sc = scenario("how many issues does yeoman/yeoman-test have?")

    def attempt(script):
        return run_scenario(
            sc,
            model="m",
            run=1,
            llm=ScriptedChatModel(script=script),
            settings=settings,
            wait=waits.append,
        )

    assert attempt([TimeoutError("read timed out"), ai_reply("45")]).outcome == "passed"
    assert waits == [5]
    trace = attempt([ConnectionError("reset") for _ in range(3)])
    assert (trace.outcome, trace.error) == (
        "errored",
        {"kind": "ConnectionError", "message": "reset"},
    )
    assert waits == [5, 5, 20]


def test_the_recursion_limit_is_a_failure(settings):
    loop = [ai_call(("count_issues", {"repo_name": REPO})) for _ in range(30)]
    trace = run(settings, loop, scenario("how many issues?", "and now?"))
    assert (trace.outcome, trace.error["kind"]) == ("failed", "recursion_limit")
    assert len(trace.turns) == 1 and trace.turns[0].tool_calls


def test_an_overlong_ollama_prompt_is_errored(settings):
    trace = run(settings, [ai_reply("45")], scenario("how many?"), num_ctx=1000)
    assert (trace.outcome, trace.error["kind"]) == ("errored", "context_overflow")
    assert run(settings, [ai_reply("45")], scenario("how many?"), num_ctx=16384).outcome == "passed"


def test_reply_joins_content_parts(settings):
    parts = AIMessage(content=[{"type": "text", "text": "4"}, {"type": "text", "text": "5"}])
    assert run(settings, [parts], scenario("how many?")).turns[0].reply == "45"


def test_an_interrupt_propagates(settings):
    with pytest.raises(KeyboardInterrupt):
        run(settings, [KeyboardInterrupt()], scenario("how many?"))


def test_tokens_spent_by_an_abandoned_attempt_still_count(settings):
    script = [
        ai_call(("count_issues", {"repo_name": REPO})),
        TimeoutError("read timed out"),
        ai_reply("45"),
    ]
    trace = run(settings, script, scenario("how many issues does yeoman/yeoman-test have?"))
    assert trace.outcome == "passed"
    assert (trace.input_tokens, trace.output_tokens) == (200, 20)
