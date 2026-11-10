import io
import json
import logging
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from fakes import FakeClassification, FakeCollection, fake_app

from inex.cli import main

REPO = Path(__file__).resolve().parents[1]
COMMANDS = (
    "count",
    "collect",
    "classify",
    "link",
    "import",
    "load",
    "query",
    "export",
    "summary",
    "chat",
    "db",
)


@pytest.fixture(autouse=True)
def progress_logger():
    progress = logging.getLogger("inex.progress")
    saved_handlers, saved_level = progress.handlers[:], progress.level
    yield progress
    progress.handlers[:] = saved_handlers
    progress.setLevel(saved_level)


@pytest.fixture
def built():
    return []


@pytest.fixture
def cli(settings, tmp_path, monkeypatch, built):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("inex.cli.configure_logging", lambda path: None)
    services = {}

    def build(cli_settings):
        app = fake_app(settings, **services)
        built.append((cli_settings, app))
        return app

    def run(argv, **fakes):
        services.clear()
        services.update(fakes)
        return main(argv, build=build)

    return run


def test_count_prints_the_summary(cli, capsys):
    assert cli(["count", "facebook/react"]) == 0
    assert capsys.readouterr().out == "facebook/react has 14779 issues.\n"


def test_collect_summary(cli, capsys):
    assert cli(["collect", "acme/widget", "--count", "5"]) == 0
    assert capsys.readouterr().out == (
        "Collected 10 issues from acme/widget (0 already in the database, 10 in total).\n"
    )


def test_json_output_is_valid_json_for_every_command(cli, capsys):
    for argv in (
        ["count", "facebook/react"],
        ["export", "SELECT 1 AS x", "--name", "r"],
        ["db", "init"],
        ["query", "SELECT 1 AS x"],
    ):
        assert cli(["--json", *argv]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_json_error_shape(cli, capsys):
    assert cli(["--json", "count", "nope/nope"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "code": "repo_not_found",
        "message": "nope/nope was not found on GitHub.",
    }


def test_invalid_input_exits_1_with_message(cli, capsys, built):
    assert cli(["collect", "react"]) == 1
    assert capsys.readouterr().err.startswith("error: ")
    assert built[0][1].collection.calls == []


def test_preview_flag_calls_preview_not_classify(cli, capsys, built):
    assert cli(["classify", "acme/widget", "--preview", "--oldest-first"]) == 0
    calls = built[0][1].classification.calls
    assert [c[0] for c in calls] == ["preview"]
    assert calls[0][3]["order"] == "asc"
    assert capsys.readouterr().out == (
        "10 of 10 unclassified issues in acme/widget would be classified with m (p).\n"
    )


def test_query_prints_rows(cli, capsys):
    assert cli(["query", "SELECT 1 AS x, 'a' AS y"]) == 0
    assert capsys.readouterr().out == "x\ty\n1\ta\n"


def test_db_reset_requires_yes(cli):
    assert cli(["db", "reset"]) == 2


def test_db_flag_reaches_settings(cli, built):
    cli(["--db", "x/y.db", "db", "init"])
    assert built[0][0].database == Path("x/y.db")


def test_load_requires_model_and_prompt_version(cli):
    assert cli(["load", "x.jsonl"]) == 2


def test_ctrl_c_exits_130(cli, capsys):
    assert (
        cli(["collect", "acme/widget"], collection=FakeCollection(error=KeyboardInterrupt())) == 130
    )
    err = capsys.readouterr().err
    assert err == "interrupted\n"


def test_unexpected_error_exits_1_and_logs(cli, capsys, caplog):
    with caplog.at_level(logging.ERROR):
        code = cli(
            ["collect", "acme/widget"], collection=FakeCollection(error=RuntimeError("boom"))
        )
    assert code == 1
    assert capsys.readouterr().err.startswith("error: unexpected failure")
    assert "boom" in caplog.text


def test_module_entry_point_lists_every_command():
    result = subprocess.run(
        [sys.executable, "-m", "inex", "--help"], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0
    for command in COMMANDS:
        assert command in result.stdout


def test_logs_never_reach_stdout(tmp_path, capsys):
    from inex.cli import configure_logging

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "level": "INFO",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": "INFO", "handlers": ["stdout"]},
    }
    path = tmp_path / "logging.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging(path)
        logging.getLogger("inex.test").info("progress line")
        captured = capsys.readouterr()
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    assert "progress line" not in captured.out
    assert "progress line" in captured.err


def test_non_ascii_output_survives_a_legacy_console_encoding(cli, monkeypatch):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    text = "title " + chr(0x2192) + " " + chr(0x1F389) + " " + chr(0x6F22) + chr(0x5B57)
    assert cli(["query", f"SELECT '{text}' AS t"]) == 0
    stream.flush()
    assert text in buffer.getvalue().decode("utf-8")


def test_log_file_is_written_as_utf8(tmp_path, monkeypatch):
    from inex.cli import configure_logging

    monkeypatch.chdir(tmp_path)
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"file": {"class": "logging.FileHandler", "filename": "logs/t.log"}},
        "root": {"level": "INFO", "handlers": ["file"]},
    }
    path = tmp_path / "logging.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    message = "title " + chr(0x2192) + " " + chr(0x6F22) + chr(0x5B57)
    try:
        configure_logging(path)
        logging.getLogger("inex.test").info(message)
    finally:
        for handler in root.handlers:
            if handler not in saved_handlers:
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    assert message in (tmp_path / "logs" / "t.log").read_text(encoding="utf-8")


class _Judged(FakeClassification):
    def preview(self, owner, repo, **kwargs):
        return replace(super().preview(owner, repo, **kwargs), judge="j")


def test_preview_names_the_judge(cli, capsys):
    assert cli(["classify", "acme/widget", "--preview"], classification=_Judged()) == 0
    assert capsys.readouterr().out == (
        "10 of 10 unclassified issues in acme/widget would be classified with m (p). "
        "Recover judge: j.\n"
    )


def test_judge_flag_reaches_settings(cli, built):
    cli(["--judge", "x:y", "db", "init"])
    assert built[0][0].judge == "x:y"


def _handlers(tmp_path):
    console = logging.StreamHandler(io.StringIO())
    console.setLevel(logging.INFO)
    log_file = logging.FileHandler(tmp_path / "t.log", encoding="utf-8")
    log_file.setLevel(logging.DEBUG)
    return console, log_file


def test_quiet_console_leaves_the_log_file_alone(tmp_path):
    from inex.cli import quiet_console

    root = logging.getLogger()
    saved = root.handlers[:]
    console, log_file = _handlers(tmp_path)
    root.handlers[:] = [console, log_file]
    try:
        quiet_console()
        assert console.level == logging.WARNING
        assert log_file.level == logging.DEBUG
    finally:
        log_file.close()
        root.handlers[:] = saved


def test_chat_runs_with_a_quiet_console(cli, tmp_path, monkeypatch):
    import inex.agent.chat

    root = logging.getLogger()
    saved = root.handlers[:]
    console, log_file = _handlers(tmp_path)
    root.handlers[:] = [console, log_file]
    levels = []
    monkeypatch.setattr(inex.agent.chat, "run_chat", lambda app: levels.append(console.level))
    try:
        assert cli(["chat"]) == 0
    finally:
        log_file.close()
        root.handlers[:] = saved
    assert levels == [logging.WARNING]


def test_quiet_console_draws_bars_instead_of_lines(tmp_path, capsys, progress_logger):
    from inex.cli import ProgressBars, quiet_console

    root = logging.getLogger()
    saved = root.handlers[:]
    console, log_file = _handlers(tmp_path)
    root.handlers[:] = [console, log_file]
    try:
        quiet_console()
        quiet_console()
        progress_logger.info("acme/widget: 10 of 20 issues classified")
    finally:
        log_file.close()
        root.handlers[:] = saved
    bars = [h for h in progress_logger.handlers if isinstance(h, ProgressBars)]
    assert len(bars) == 1
    assert progress_logger.level == logging.DEBUG
    assert capsys.readouterr().err == ""
    assert console.stream.getvalue() == ""
    assert "10 of 20 issues classified" in (tmp_path / "t.log").read_text(encoding="utf-8")


def test_every_command_runs_with_a_quiet_console(cli, tmp_path):
    root = logging.getLogger()
    saved = root.handlers[:]
    console, log_file = _handlers(tmp_path)
    root.handlers[:] = [console, log_file]
    try:
        assert cli(["db", "init"]) == 0
        assert console.level == logging.WARNING
    finally:
        log_file.close()
        root.handlers[:] = saved


def _event(logger, done, total, finished=False, task="Classifying acme/widget"):
    extra = {"task": task, "done": done, "total": total, "finished": finished}
    logger.debug("%s: %d", task, done, extra=extra)


def _bars(progress_logger, enabled=True):
    from inex.cli import ProgressBars

    stream = io.StringIO()
    bars = ProgressBars(stream, enabled=enabled)
    progress_logger.handlers[:] = [bars]
    progress_logger.setLevel(logging.DEBUG)
    return bars, stream


def test_progress_bar_draws_and_closes(progress_logger):
    bars, stream = _bars(progress_logger)
    _event(progress_logger, 1, 2)
    _event(progress_logger, 2, 2)
    _event(progress_logger, 2, 2, finished=True)
    text = stream.getvalue()
    assert "Classifying acme/widget" in text
    assert "2/2" in text
    assert text.endswith("\n")
    assert bars.bar is None


def test_progress_bar_without_a_total_counts_up(progress_logger):
    bars, stream = _bars(progress_logger)
    _event(progress_logger, 3, None, task="Collecting acme/widget")
    _event(progress_logger, 3, None, finished=True, task="Collecting acme/widget")
    assert "Collecting acme/widget: 3 issues" in stream.getvalue()


def test_a_new_task_closes_the_previous_bar(progress_logger):
    bars, stream = _bars(progress_logger)
    _event(progress_logger, 1, 3, task="Collecting acme/widget")
    _event(progress_logger, 1, 2)
    text = stream.getvalue()
    assert "Collecting acme/widget" in text and "Classifying acme/widget" in text
    assert bars.task == "Classifying acme/widget"


def test_progress_bars_ignore_plain_lines(progress_logger):
    bars, stream = _bars(progress_logger)
    progress_logger.info("acme/widget: 10 of 20 issues classified")
    assert stream.getvalue() == ""


def test_progress_bars_stay_off_when_not_a_terminal(progress_logger):
    bars, stream = _bars(progress_logger, enabled=None)
    _event(progress_logger, 1, 2)
    _event(progress_logger, 2, 2, finished=True)
    assert stream.getvalue() == ""


def test_known_third_party_warnings_are_hidden():
    import warnings

    from inex.cli import quiet_known_warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        quiet_known_warnings()
        warnings.warn(
            "Core Pydantic V1 functionality isn't compatible with Python 3.14 or greater.",
            UserWarning,
            stacklevel=1,
        )
        warnings.warn(
            "The default value of `allowed_objects` will change in a future version.",
            PendingDeprecationWarning,
            stacklevel=1,
        )
        warnings.warn("something else", UserWarning, stacklevel=1)
    assert [str(w.message) for w in caught] == ["something else"]


def test_the_chat_module_imports_without_warnings():
    code = (
        "from inex.cli import quiet_known_warnings, import_quietly; "
        "quiet_known_warnings(); import_quietly('inex.agent.chat')"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True)
    assert result.returncode == 0
    assert "Warning" not in result.stderr


def test_importing_quietly_still_shows_other_warnings(tmp_path, monkeypatch):
    import warnings

    from inex.cli import import_quietly

    (tmp_path / "noisy_module.py").write_text(
        "import warnings\n"
        "warnings.warn('The default value of `allowed_objects` will change soon.', "
        "PendingDeprecationWarning, stacklevel=1)\n"
        "warnings.warn('something else', UserWarning, stacklevel=1)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import_quietly("noisy_module")
    finally:
        sys.modules.pop("noisy_module", None)
    assert [str(w.message) for w in caught] == ["something else"]


def test_every_command_hides_known_warnings(cli, monkeypatch):
    calls = []
    monkeypatch.setattr("inex.cli.quiet_known_warnings", lambda: calls.append(True))
    assert cli(["db", "init"]) == 0
    assert calls == [True]


class _Summarised(FakeClassification):
    def __init__(self, projects):
        super().__init__()
        self.projects = projects

    def summary(self):
        from inex.services.classification import DatasetSummary

        return DatasetSummary("m", "p", 0.2, ("Intrinsic", "Extrinsic", "Not a Bug"), self.projects)


def _project(name, issues, labelled, linked, counts):
    from inex.services.classification import ProjectSummary

    owner, repo = name.split("/")
    labels = dict(zip(("Intrinsic", "Extrinsic", "Not a Bug"), counts, strict=True))
    return ProjectSummary(owner, repo, issues, labelled, linked, labels)


def test_summary_prints_a_table_with_totals(cli, capsys):
    projects = (
        _project("axios/axios", 41, 41, 41, (20, 5, 16)),
        _project("ember-cli/ember-cli", 2156, 1000, 1000, (100, 200, 700)),
    )
    assert cli(["summary"], classification=_Summarised(projects)) == 0
    assert capsys.readouterr().out.splitlines() == [
        "Labels by m (p, temperature 0.2)",
        "",
        "project" + " " * 14 + "issues  labelled  linked  Intrinsic  Extrinsic  Not a Bug",
        "axios/axios"
        + " " * 14
        + "41"
        + " " * 8
        + "41"
        + " " * 6
        + "41"
        + " " * 9
        + "20"
        + " " * 10
        + "5"
        + " " * 9
        + "16",
        "ember-cli/ember-cli"
        + " " * 3
        + "2,156"
        + " " * 5
        + "1,000"
        + " " * 3
        + "1,000"
        + " " * 8
        + "100"
        + " " * 8
        + "200"
        + " " * 8
        + "700",
        "total"
        + " " * 17
        + "2,197"
        + " " * 5
        + "1,041"
        + " " * 3
        + "1,041"
        + " " * 8
        + "120"
        + " " * 8
        + "205"
        + " " * 8
        + "716",
    ]


def test_summary_of_an_empty_database(cli, capsys):
    assert cli(["summary"], classification=_Summarised(())) == 0
    assert capsys.readouterr().out == "No projects in the database yet.\n"


def test_summary_json_carries_every_count(cli, capsys):
    projects = (_project("axios/axios", 41, 41, 41, (20, 5, 16)),)
    assert cli(["--json", "summary"], classification=_Summarised(projects)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["projects"][0]["labels"] == {"Intrinsic": 20, "Extrinsic": 5, "Not a Bug": 16}
