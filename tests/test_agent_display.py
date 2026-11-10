import json

import pytest

from inex.agent.display import call_line, duration, result_line, result_summary, tool_result


def test_call_line_puts_the_target_first_and_skips_empty_arguments():
    args = {"count": 44, "repo_name": "ember-cli/ember-cli", "start_date": None}
    assert call_line("collect_all", args) == "  > collect_all ember-cli/ember-cli count=44"


def test_call_line_shortens_long_arguments_and_flattens_newlines():
    query = (
        "SELECT p.owner,\n  p.repo, COUNT(*) FROM issues i JOIN projects p ON p.id = i.project_id"
    )
    line = call_line("run_sql", {"query": query})
    assert line.startswith("  > run_sql SELECT p.owner, p.repo, COUNT(*)")
    assert line.endswith("...")
    assert "\n" not in line


@pytest.mark.parametrize(
    "name, result, summary",
    [
        (
            "count_issues",
            {"issue_count": 2190, "stored": 2156},
            "2,190 issues on GitHub, 2,156 stored",
        ),
        (
            "collect_all",
            {"collected": 44, "skipped": 2156, "total": 2200},
            "44 collected, 2,156 already stored, 2,200 in total",
        ),
        (
            "preview_classification",
            {"would_classify": 50, "total_remaining": 1156, "model": "m"},
            "50 of 1,156 unclassified would be classified with m",
        ),
        (
            "classify_all",
            {"classified": 48, "failed": {"api_error": 1, "parse_error": 1}},
            "48 classified, 2 failed",
        ),
        (
            "snapshot_dependencies",
            {"package_name": "axios", "linked_issues": 41, "issue_versions_snapshotted": 12},
            "41 issues linked to axios releases, 12 versions saved",
        ),
        ("run_sql", {"rows": [[1], [2]], "truncated": False}, "2 rows"),
        ("run_sql", {"rows": [[1]] * 50, "truncated": True}, "50 rows (more not shown)"),
        (
            "export_data",
            {"rows_exported": 1216, "path": "exports/labels.csv"},
            "1,216 rows written to exports/labels.csv",
        ),
        (
            "preview_issue_list",
            {"total_issues": 216, "distinct_repos": 5, "already_in_db": 0, "to_collect": 216},
            "216 issues across 5 repositories, 0 already stored, 216 to collect",
        ),
        (
            "import_issue_list",
            {"collected": 210, "skipped": 6, "failed": 0, "classified": 216},
            "210 collected, 6 already stored, 0 failed, 216 classified",
        ),
    ],
)
def test_results_are_summarised_per_tool(name, result, summary):
    assert result_summary(name, {"status": "ok", **result}) == summary


def test_an_error_result_shows_its_code_and_message():
    result = {"status": "error", "code": "repo_not_found", "message": "nope/nope was not found."}
    assert result_summary("count_issues", result) == (
        "error repo_not_found: nope/nope was not found."
    )


def test_an_unknown_tool_or_a_malformed_result_says_done():
    assert result_summary("new_tool", {"status": "ok"}) == "done"
    assert result_summary("count_issues", {"status": "ok"}) == "done"
    assert result_summary("count_issues", "plain text") == "plain text"


def test_result_line_adds_the_time_taken():
    result = {"status": "ok", "issue_count": 21, "stored": 21}
    assert result_line("count_issues", result, 0.54) == (
        "    21 issues on GitHub, 21 stored (0.5s)"
    )


@pytest.mark.parametrize(
    "seconds, shown",
    [(0.04, "0.0s"), (12.34, "12.3s"), (192, "3m 12s"), (3725, "1h 02m")],
)
def test_durations(seconds, shown):
    assert duration(seconds) == shown


def test_tool_results_are_read_from_json_text():
    assert tool_result(json.dumps({"status": "ok"})) == {"status": "ok"}
    assert tool_result("not json") == "not json"
    assert tool_result({"status": "ok"}) == {"status": "ok"}
