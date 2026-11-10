import json
from pathlib import Path

import pytest
from fakes import run_main, scripted_builder

from evals.report import SUMMARY_COLUMNS, latex_escape, summarize, table_tex, write_reports

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MODELS = ["openrouter:anthropic/claude-haiku-5.5", "ollama:llama3.2:3b"]


def records():
    text = (FIXTURES / "eval_traces.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def rows():
    return {(r["model"], r["category"]): r for r in summarize(records(), MODELS, 3)}


def test_overall_rows():
    assert rows()[(MODELS[0], "overall")] == {
        "model": MODELS[0],
        "category": "overall",
        "scenarios": 3,
        "runs": 9,
        "passed": 7,
        "failed": 1,
        "errored": 1,
        "pass_rate": 0.875,
        "pass_hat_k": 0.3333,
        "rejected_calls": 1,
        "input_tokens": 9000,
        "output_tokens": 900,
        "cost": 0.09,
        "median_seconds": 2.0,
    }
    b = rows()[(MODELS[1], "overall")]
    assert (b["pass_rate"], b["pass_hat_k"], b["cost"]) == (0.5556, 0.3333, None)


def test_errored_runs_are_left_out_of_pass_rate_but_fail_pass_hat_k():
    errors = rows()[(MODELS[0], "errors")]
    assert (errors["passed"], errors["failed"], errors["errored"]) == (2, 0, 1)
    assert (errors["pass_rate"], errors["pass_hat_k"]) == (1.0, 0.0)
    assert rows()[(MODELS[0], "routing")]["pass_rate"] == 0.6667


def test_rows_follow_model_then_category_order():
    order = [(r["model"], r["category"]) for r in summarize(records(), MODELS, 3)]
    assert order == [(m, c) for m in MODELS for c in ("routing", "gates", "errors", "overall")]


def test_table_matches_the_golden_file():
    table = table_tex(summarize(records(), MODELS, 3), MODELS, 3)
    assert table == (FIXTURES / "eval_table.tex").read_text(encoding="utf-8")


def test_latex_escape():
    assert latex_escape("google_genai:gemini-2.5-flash") == r"google\_genai:gemini-2.5-flash"
    assert latex_escape("a&b%c$d#e{f}g~h^i\\j") == (
        r"a\&b\%c\$d\#e\{f\}g\textasciitilde{}h\textasciicircum{}i\textbackslash{}j"
    )


def test_write_reports_is_repeatable(tmp_path):
    manifest = {"models": MODELS, "runs": 3}
    names = ("summary.csv", "summary.json", "table.tex")
    write_reports(tmp_path, manifest, records())
    first = {n: (tmp_path / n).read_bytes() for n in names}
    write_reports(tmp_path, manifest, records())
    assert first == {n: (tmp_path / n).read_bytes() for n in names}
    header = (tmp_path / "summary.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(SUMMARY_COLUMNS)


def test_report_rebuilds_identical_files_without_a_model(tmp_path, monkeypatch):
    argv = ["--models", "ollama:fake", "--only", "route-count", "--runs", "1"]
    _, results = run_main(tmp_path, monkeypatch, argv, build=scripted_builder())
    names = ("summary.csv", "summary.json", "table.tex")
    before = {n: (results.path / n).read_bytes() for n in names}
    for n in names:
        (results.path / n).unlink()

    def never(settings):
        pytest.fail("built a model")

    code, _ = run_main(tmp_path, monkeypatch, ["--report", str(results.path)], build=never)
    assert code == 0
    assert before == {n: (results.path / n).read_bytes() for n in names}
    code, _ = run_main(tmp_path, monkeypatch, ["--report", str(tmp_path / "nope")], build=never)
    assert code == 2
