import json
import sqlite3

import pytest

from inex.errors import InexError
from inex.services.queries import ExportResult, QueryResult, QueryService
from inex.store.database import Store


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "q.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE t(x INTEGER)")
    raw.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(1, 61)])
    raw.commit()
    raw.close()
    store = Store(path)
    yield store
    store.close()


@pytest.fixture
def service(store, tmp_path):
    return QueryService(store, tmp_path / "exports")


def code_of(call):
    with pytest.raises(InexError) as e:
        call()
    return e.value.code


def test_plain_filename_is_written_inside_exports(service, tmp_path):
    result = service.export("SELECT x FROM t WHERE x <= 2", "csv", "results")
    target = tmp_path / "exports" / "results.csv"
    assert result == ExportResult(path=str(target), rows_exported=2, columns=["x"], format="csv")
    assert target.read_text(encoding="utf-8").split() == ["x", "1", "2"]


def test_json_export(service, tmp_path):
    service.export("SELECT x FROM t WHERE x <= 2", "json", "rows")
    rows = json.loads((tmp_path / "exports" / "rows.json").read_text(encoding="utf-8"))
    assert rows == [{"x": 1}, {"x": 2}]


def test_relative_path_cannot_leave_exports(service, tmp_path):
    assert code_of(lambda: service.export("SELECT x FROM t", "csv", "../escaped")) == (
        "invalid_filename"
    )
    assert not (tmp_path / "escaped.csv").exists()


def test_absolute_path_is_rejected(service, tmp_path):
    target = tmp_path / "absolute"
    assert code_of(lambda: service.export("SELECT x FROM t", "csv", str(target))) == (
        "invalid_filename"
    )
    assert not (tmp_path / "absolute.csv").exists()


def test_invalid_format(service):
    assert code_of(lambda: service.export("SELECT x FROM t", "xml")) == "invalid_format"


def test_run_caps_at_max_rows(service):
    result = service.run("SELECT x FROM t")
    assert (result.columns, len(result.rows), result.truncated) == (["x"], 50, True)
    assert result.rows[0] == [1]


def test_run_small_result_is_not_truncated(service):
    assert service.run("SELECT x FROM t WHERE x <= 2") == QueryResult(["x"], [[1], [2]], False)


def test_run_rejects_writes_and_bad_sql(service):
    assert code_of(lambda: service.run("DELETE FROM t")) == "not_a_select"
    assert code_of(lambda: service.run("SELECT 1; SELECT 2")) == "multiple_statements"
    assert code_of(lambda: service.run("SELECT nope FROM t")) == "sql_error"


def test_max_rows(service):
    assert service.max_rows == 50


def test_overview_counts_projects_issues_and_labelled_issues(seeded, tmp_path):
    from inex.services.queries import DatabaseOverview

    db, pid, ids = seeded
    db.save_project("acme", "empty")
    db.save_classification(
        ids[0], {"classification": "Intrinsic"}, "m", "p@1", 0.2, "2026-11-09T00:00:00"
    )
    db.save_classification(
        ids[0], {"classification": "Extrinsic"}, "m2", "p@1", 0.2, "2026-11-09T00:00:00"
    )
    db.save_classification(
        ids[1],
        {"classification": None, "status": "api_error", "error": "boom"},
        "m",
        "p@1",
        0.2,
        "2026-11-09T00:00:00",
    )
    overview = QueryService(db, tmp_path / "exports").overview()
    assert overview == DatabaseOverview(projects=2, issues=3, labelled=1)
