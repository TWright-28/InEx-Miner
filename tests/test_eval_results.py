import json
from dataclasses import replace
from datetime import datetime

import pytest
from fakes import FakeResponse, ScriptedChatModel, ai_reply, run_main, scripted_builder

from evals.results import Results, resume_problems, text_hash
from evals.runner import Trace

DAY = datetime(2026, 10, 27, 9, 5, 1)


def trace(scenario="s", run=1, outcome="passed", cost=None, model="m"):
    return Trace(model, scenario, "routing", run, outcome, None, (), (), 0, 10, 1, cost, 1.0)


def test_create_names_the_folder_by_time_and_writes_the_manifest(tmp_path):
    results = Results.create(tmp_path, {"models": ["m"], "status": {}}, now=DAY)
    assert results.path == tmp_path / "20261027-090501"
    saved = json.loads((results.path / "manifest.json").read_text(encoding="utf-8"))
    assert saved["models"] == ["m"]
    again = Results.create(tmp_path, {"models": ["m"], "status": {}}, now=DAY)
    assert again.path == tmp_path / "20261027-090501-2"


def test_newest_record_wins_and_errored_runs_are_not_finished(tmp_path):
    results = Results.create(tmp_path, {"status": {}}, now=DAY)
    results.append(trace("a", outcome="errored", cost=0.25))
    results.append(trace("a", outcome="passed", cost=0.5))
    results.append(trace("b", outcome="errored", cost=0.25))
    results.append(trace("c", outcome="failed"))
    assert Results.open(results.path).latest()[("m", "a", 1)]["outcome"] == "passed"
    assert results.finished("m") == {("a", 1), ("c", 1)}
    assert results.spent("m") == pytest.approx(1.0)
    assert results.spent("other") == 0


def test_a_cut_off_last_line_is_skipped_and_the_next_append_starts_fresh(tmp_path):
    results = Results.create(tmp_path, {"status": {}}, now=DAY)
    results.append(trace("a"))
    with open(results.path / "traces.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"model": "m", "scenario": "b", "ru')
    assert [r["scenario"] for r in results.records()] == ["a"]
    assert results.finished("m") == {("a", 1)}
    results.append(trace("b"))
    assert [r["scenario"] for r in results.records()] == ["a", "b"]


def test_set_status_saves_the_manifest(tmp_path):
    results = Results.create(tmp_path, {"status": {}}, now=DAY)
    results.set_status("m", "stopped: budget")
    assert Results.open(results.path).manifest["status"] == {"m": "stopped: budget"}


def test_hashes_ignore_line_endings():
    assert text_hash("a\r\nb\r\n") == text_hash("a\nb\n")
    assert text_hash("a\nb\n") != text_hash("a\nc\n")


def test_resume_problems_name_what_changed():
    manifest = {
        "hashes": {"scenarios": "1", "system_prompt": "2", "tools": "3"},
        "temperature": 0.2,
    }
    same = {"scenarios": "1", "system_prompt": "2", "tools": "3"}
    assert resume_problems(manifest, same, 0.2) == []
    assert resume_problems(
        manifest, {"scenarios": "9", "system_prompt": "2", "tools": "8"}, 0.7
    ) == [
        "scenarios.yaml has changed since this run started",
        "the tool descriptions have changed since this run started",
        "the temperature is 0.7, the run used 0.2",
    ]


def test_a_line_cut_inside_a_character_is_skipped(tmp_path):
    results = Results.create(tmp_path, {"status": {}}, now=DAY)
    results.append(trace("a"))
    with open(results.path / "traces.jsonl", "ab") as handle:
        handle.write(b'{"model": "m", "reply": "caf' + "é".encode()[:1])
    assert [r["scenario"] for r in results.records()] == ["a"]


def test_a_model_stops_at_max_cost(tmp_path, monkeypatch):
    payload = {"data": [{"id": "x/y", "pricing": {"prompt": "0.001", "completion": "0.002"}}]}

    def build(settings):
        return ScriptedChatModel(
            script=[ai_reply("OK")] + [ai_reply("done", input_tokens=1000) for _ in range(5)]
        )

    code, results = run_main(
        tmp_path,
        monkeypatch,
        ["--models", "openrouter:x/y", "--only", "route-count", "route-export", "--runs", "1"],
        build=build,
        get=lambda url, timeout: FakeResponse(payload),
    )
    assert code == 1
    assert results.manifest["status"]["openrouter:x/y"] == "stopped: budget"
    assert len(results.records()) == 1
    assert results.records()[0]["cost"] == pytest.approx(1.02)


def test_a_complete_run_exits_zero_with_reports(tmp_path, monkeypatch):
    code, results = run_main(
        tmp_path,
        monkeypatch,
        ["--models", "ollama:fake", "--only", "routing", "--runs", "2"],
        build=scripted_builder(replies=40),
    )
    assert code == 0
    assert len(results.records()) == 20
    assert results.manifest["status"] == {"ollama:fake": "complete"}
    assert set(results.manifest["hashes"]) == {"scenarios", "system_prompt", "tools"}
    names = {p.name for p in results.path.iterdir()}
    assert {"manifest.json", "traces.jsonl", "summary.csv", "summary.json", "table.tex"} <= names


def test_an_invalid_scenario_file_exits_two(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 2\nscenarios: []\n", encoding="utf-8")
    monkeypatch.setattr("evals.__main__.SCENARIOS_PATH", bad)
    code, results = run_main(
        tmp_path,
        monkeypatch,
        ["--models", "ollama:fake"],
        build=lambda s: pytest.fail("built a model"),
    )
    assert (code, results) == (2, None)
    assert "version must be 1" in capsys.readouterr().err


def test_bad_arguments_exit_two():
    from evals.__main__ import main

    with pytest.raises(SystemExit) as e:
        main(["--runs", "0"])
    assert e.value.code == 2


def test_resume_skips_finished_runs_and_reruns_errored_ones(tmp_path, monkeypatch):
    argv = ["--models", "ollama:fake", "--only", "route-count", "route-export", "--runs", "1"]
    code, results = run_main(tmp_path, monkeypatch, argv, build=scripted_builder())
    assert code == 0
    errored = dict(
        results.records()[1], outcome="errored", error={"kind": "TimeoutError", "message": "x"}
    )
    with open(results.path / "traces.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(errored) + "\n")
    code, _ = run_main(
        tmp_path, monkeypatch, ["--resume", str(results.path)], build=scripted_builder()
    )
    assert code == 0
    records = Results.open(results.path).records()
    assert [r["scenario"] for r in records] == [
        "route-count",
        "route-export",
        "route-export",
        "route-export",
    ]
    assert records[-1]["outcome"] != "errored"


def test_resume_refuses_a_changed_evaluation(tmp_path, monkeypatch, capsys):
    argv = ["--models", "ollama:fake", "--only", "route-count", "--runs", "1"]
    _, results = run_main(tmp_path, monkeypatch, argv, build=scripted_builder())
    results.manifest["hashes"]["system_prompt"] = "0" * 64
    results.save()
    code, _ = run_main(
        tmp_path,
        monkeypatch,
        ["--resume", str(results.path)],
        build=lambda s: pytest.fail("built a model"),
    )
    assert code == 2
    assert "the rendered system prompt has changed" in capsys.readouterr().err


def test_an_interrupt_keeps_finished_traces_and_marks_the_manifest(tmp_path, monkeypatch):
    def build(settings):
        return ScriptedChatModel(script=[ai_reply("OK"), ai_reply("45"), KeyboardInterrupt()])

    code, results = run_main(
        tmp_path,
        monkeypatch,
        ["--models", "ollama:fake", "--only", "route-count", "route-export", "--runs", "1"],
        build=build,
    )
    assert code == 130
    assert [r["scenario"] for r in results.records()] == ["route-count"]
    assert results.manifest["state"] == "interrupted"
    assert results.manifest["status"]["ollama:fake"] == "interrupted"


def test_a_reply_with_a_unicode_line_separator_is_kept(tmp_path):
    results = Results.create(tmp_path, {"status": {}}, now=DAY)
    results.append(trace("a"))
    odd = Trace("m", "b", "routing", 1, "passed", None, (), (), 0, 10, 1, 0.5, 1.0)
    results.append(replace(odd, error={"kind": "x", "message": "one two\u0085three"}))
    assert [r["scenario"] for r in results.records()] == ["a", "b"]
    assert results.spent("m") == pytest.approx(0.5)
