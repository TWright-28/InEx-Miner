from evals.checks import CheckResult, ToolCall, Turn, passed, satisfies, score
from evals.scenarios import Check, Constraint


def call(name, **args):
    return ToolCall(name, args, "ok", None)


def turn(*calls, reply=""):
    return Turn("u", tuple(calls), 0, reply)


TWO = (
    turn(call("preview_classification", count=None), reply="10 issues would be classified"),
    turn(call("classify_all", count=None), reply="Classified 7; 3 failed, run it again"),
)


def one(check, turns=TWO):
    return score([check], turns)[0]


def test_called_and_not_called():
    assert one(Check("called", tool="classify_all")) == CheckResult(
        "called classify_all", True, "called at step 2"
    )
    assert one(Check("not_called", tool="classify_all", turn=1)) == CheckResult(
        "not_called classify_all (turn 1)", True, "never called"
    )
    assert one(Check("not_called", tool="classify_all")) == CheckResult(
        "not_called classify_all", False, "called at step 2"
    )
    assert one(Check("called", tool="classify_all", turn=2)).reason == "called at step 1"


def test_a_turn_that_never_ran_has_no_calls():
    assert one(Check("called", tool="classify_all", turn=2), TWO[:1]).reason == "never called"


def test_order_needs_b_and_an_a_before_it():
    good = Check("order", before="preview_classification", tool="classify_all")
    assert one(good) == CheckResult(
        "order preview_classification -> classify_all",
        True,
        "preview_classification at step 1, classify_all at step 2",
    )
    backwards = Check("order", before="classify_all", tool="preview_classification")
    assert one(backwards).reason == "no classify_all before preview_classification at step 1"
    assert one(Check("order", before="count_issues", tool="collect_all")).reason == (
        "collect_all never called"
    )
    assert not one(
        Check("order", before="preview_classification", tool="classify_all", turn=2)
    ).passed


def test_plain_values_match_string_forms_and_case():
    assert satisfies(20, Constraint("equals", 20))
    assert satisfies("20", Constraint("equals", 20))
    assert satisfies("false", Constraint("equals", False))
    assert satisfies("ASC", Constraint("equals", "asc"))
    assert satisfies(" @yeoman/test ", Constraint("equals", "@yeoman/test"))
    assert not satisfies(0, Constraint("equals", False))
    assert not satisfies("21", Constraint("equals", 20))
    assert not satisfies(None, Constraint("equals", 20))


def test_dates_compare_by_their_first_ten_characters():
    assert satisfies("2023-01-01T00:00:00Z", Constraint("equals", "2023-01-01"))
    assert satisfies("2023-01-01", Constraint("one_of", ("2023-12-31", "2023-01-01")))
    assert not satisfies("2023-01-02", Constraint("equals", "2023-01-01"))


def test_absent_one_of_and_matches():
    assert satisfies(None, Constraint("absent", True))
    assert not satisfies(5, Constraint("absent", True))
    assert satisfies(
        "yeoman_labels.json", Constraint("one_of", ("yeoman_labels", "yeoman_labels.json"))
    )
    assert satisfies("WHERE status IS NULL", Constraint("matches", r"status\s+is\s+null"))
    assert not satisfies(None, Constraint("matches", "None"))


def test_args_needs_one_call_satisfying_every_constraint():
    turns = (
        turn(
            call("collect_all", count=20, direction="desc"),
            call("collect_all", count=10, direction="asc"),
        ),
    )
    both = (("count", Constraint("equals", 20)), ("direction", Constraint("equals", "asc")))
    assert one(Check("args", tool="collect_all", constraints=both), turns) == CheckResult(
        "args collect_all count=20 direction='asc'", False, "no collect_all call matched (2 calls)"
    )
    first = (("count", Constraint("equals", 20)),)
    assert one(Check("args", tool="collect_all", constraints=first), turns).reason == (
        "matched at step 1"
    )
    assert one(Check("args", tool="run_sql", constraints=first), turns).reason == (
        "run_sql never called"
    )


def test_a_parameter_missing_from_the_call_reads_as_none():
    turns = (turn(call("snapshot_dependencies", repo_name="yeoman/yeoman-test")),)
    absent = (("last_n_versions", Constraint("absent", True)),)
    assert one(Check("args", tool="snapshot_dependencies", constraints=absent), turns).passed


def test_never_fails_on_the_first_matching_call():
    turns = (
        turn(
            call("run_sql", query="SELECT 1"), call("run_sql", query="DELETE FROM classifications")
        ),
    )
    rule = (("query", Constraint("matches", r"\bdelete\b")),)
    assert one(Check("never", tool="run_sql", constraints=rule), turns) == CheckResult(
        "never run_sql query~/\\bdelete\\b/", False, "matched at step 2"
    )
    assert one(Check("never", tool="export_data", constraints=rule), turns) == CheckResult(
        "never export_data query~/\\bdelete\\b/", True, "no export_data call matched"
    )


def test_max_calls_counts_all_or_one_tool():
    assert one(Check("max_calls", n=1)) == CheckResult("max_calls 1", False, "made 2, limit 1")
    assert one(Check("max_calls", tool="classify_all", n=1)) == CheckResult(
        "max_calls classify_all 1", True, "made 1, limit 1"
    )


def test_reply_reads_the_last_turn_unless_scoped():
    assert one(Check("reply", pattern="RETRY|again")) == CheckResult(
        "reply /RETRY|again/", True, "matched"
    )
    assert one(Check("reply", pattern="again", turn=1)) == CheckResult(
        "reply /again/ (turn 1)", False, "no match in '10 issues would be classified'"
    )


def test_a_run_passes_only_when_every_check_passes():
    results = score(
        [Check("called", tool="classify_all"), Check("not_called", tool="classify_all")], TWO
    )
    assert [r.passed for r in results] == [True, False]
    assert passed(results) is False
    assert passed(results[:1]) is True
