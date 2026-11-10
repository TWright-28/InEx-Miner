import json
import sqlite3

import pytest
from conftest import FULL_PROBS

from inex.errors import InexError
from inex.store.database import Store

RUN = dict(model="google_genai:gemini-2.5-pro", prompt="inex@abc123+t2", temp=0.2)
OTHER = dict(model="qwen3:30b", prompt="inex@abc123+t2", temp=0.2)

LEGACY_CLASSIFICATIONS = """CREATE TABLE classifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER NOT NULL, classification TEXT,
    classification_probabilities TEXT, classification_raw_response TEXT, model TEXT,
    prompt_version TEXT, temperature REAL, classified_at TEXT)"""


def save(db, issue_id, label=None, status="ok", probs=None, **over):
    kw = dict(RUN)
    kw.update(over)
    return db.save_classification(
        issue_id,
        {
            "classification": label,
            "classification_probabilities": probs
            if probs is not None
            else (FULL_PROBS if label else {}),
            "classification_raw_response": "{}",
            "status": status,
        },
        classified_at="2026-10-26T00:00:00",
        **kw,
    )


def rows(db, sql):
    return db.fetch_readonly(sql)[1]


def _keys(run):
    return {"model": run["model"], "prompt_version": run["prompt"], "temperature": run["temp"]}


class TestSchema:
    def test_outcome_columns_exist(self, db):
        cols = [r[1] for r in rows(db, "PRAGMA table_info(classifications)")]
        for c in ("status", "error", "validation_flags", "argmax_label", "attempts"):
            assert c in cols

    def test_opening_twice_is_harmless(self, db_path):
        Store(db_path).close()
        Store(db_path).close()

    def test_legacy_database_gains_columns_and_unique_index_without_row_changes(self, tmp_path):
        path = tmp_path / "legacy.db"
        raw = sqlite3.connect(path)
        raw.execute(
            "CREATE TABLE projects(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, "
            "repo TEXT NOT NULL, added_at TEXT, UNIQUE(owner, repo))"
        )
        raw.execute(LEGACY_CLASSIFICATIONS)
        raw.execute(
            "INSERT INTO classifications(issue_id, classification, model, prompt_version, temperature) "
            "VALUES (1, 'Intrinsic', 'qwen3:30b', 'p', 0.2)"
        )
        raw.commit()
        raw.close()
        store = Store(path)
        cols = {r[1] for r in rows(store, "PRAGMA table_info(classifications)")}
        assert {"status", "error", "validation_flags", "argmax_label", "attempts"} <= cols
        indexes = {r[1] for r in rows(store, "PRAGMA index_list(classifications)")}
        assert "ux_classifications_run" in indexes
        assert rows(
            store,
            "SELECT issue_id, classification, model, prompt_version, temperature, status "
            "FROM classifications",
        ) == [(1, "Intrinsic", "qwen3:30b", "p", 0.2, None)]
        first = store.save_classification(
            2,
            {"classification": "Extrinsic", "classification_probabilities": {}},
            "m",
            "p",
            0.2,
            "2026-01-01",
        )
        again = store.save_classification(
            2,
            {"classification": "Intrinsic", "classification_probabilities": {}},
            "m",
            "p",
            0.2,
            "2026-01-02",
        )
        assert first == again
        store.close()

    def test_legacy_duplicates_are_refused_without_touching_the_file(self, tmp_path):
        path = tmp_path / "dupes.db"
        raw = sqlite3.connect(path)
        raw.execute(LEGACY_CLASSIFICATIONS)
        for _ in range(2):
            raw.execute(
                "INSERT INTO classifications(issue_id, classification, model, prompt_version, "
                "temperature) VALUES (1, 'Intrinsic', 'm', 'p', 0.2)"
            )
        raw.commit()
        raw.close()
        with pytest.raises(InexError) as e:
            Store(path)
        assert e.value.code == "duplicate_classifications"
        raw = sqlite3.connect(path)
        columns = {r[1] for r in raw.execute("PRAGMA table_info(classifications)")}
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        raw.close()
        assert "status" not in columns
        assert "projects" not in tables
        path.unlink()

    def test_paths_with_spaces_and_hash_open_both_ways(self, tmp_path):
        store = Store(tmp_path / "My Project (2)" / "db #1.db")
        store.save_project("acme", "widget")
        assert store.run_readonly("SELECT owner FROM projects", 50)[1] == [("acme",)]
        store.close()


class TestLookups:
    def test_project_and_issue_ids_are_none_when_absent(self, seeded):
        db, pid, _ = seeded
        assert db.project_id("acme", "widget") == pid
        assert db.project_id("nope", "nope") is None
        assert db.issue_id(pid, 101) is not None
        assert db.issue_id(pid, 999) is None

    def test_count_existing_issues(self, seeded):
        db, pid, _ = seeded
        assert db.count_existing_issues(pid, [101, 102, 999]) == 2
        assert db.count_existing_issues(pid, []) == 0

    def test_issue_count_is_per_project(self, seeded):
        db, pid, ids = seeded
        other = db.save_project("acme", "other")
        assert db.issue_count(pid) == len(ids)
        assert db.issue_count(other) == 0

    def test_has_classification(self, seeded):
        db, _, ids = seeded
        assert db.has_classification(ids[0], **RUN) is False
        save(db, ids[0], label="Intrinsic")
        assert db.has_classification(ids[0], **RUN) is True

    def test_has_classification_is_per_run(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label="Intrinsic")
        assert db.has_classification(ids[0], **OTHER) is False
        assert db.has_classification(ids[0], **{**RUN, "prompt": "severity@def456+t2"}) is False
        assert db.has_classification(ids[0], **{**RUN, "temp": 0.0}) is False

    def test_a_failed_attempt_is_not_a_classification(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label=None, status="parse_error")
        assert db.has_classification(ids[0], **RUN) is False


class TestReadOnly:
    def test_run_readonly_caps_rows(self, db):
        pid = db.save_project("acme", "widget")
        for n in range(60):
            db.save_issue(pid, {"number": n, "title": str(n)})
        columns, result = db.run_readonly("SELECT issue_number FROM issues", 50)
        assert columns == ["issue_number"]
        assert len(result) == 50

    def test_writes_through_the_readonly_connection_fail(self, db):
        with pytest.raises(InexError) as e:
            db.run_readonly("INSERT INTO projects(owner, repo) VALUES ('a', 'b')", 50)
        assert e.value.code == "sql_error"


class TestMaintenance:
    def test_reset_drops_rows_and_keeps_schema(self, seeded):
        db, _, _ = seeded
        db.reset()
        assert rows(db, "SELECT COUNT(*) FROM projects") == [(0,)]
        assert "status" in [r[1] for r in rows(db, "PRAGMA table_info(classifications)")]

    def test_clear_empties_tables_and_sequences(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label="Intrinsic")
        db.clear()
        for table in ("projects", "issues", "classifications"):
            assert rows(db, f"SELECT COUNT(*) FROM {table}") == [(0,)]
        assert db.save_project("new", "one") == 1


class TestFailuresAreNotLabels:
    def test_failure_row_has_null_classification(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label=None, status="api_error")
        assert rows(
            db, f"SELECT classification, status FROM classifications WHERE issue_id={ids[0]}"
        ) == [(None, "api_error")]

    def test_storing_a_label_with_a_failed_status_is_refused(self, seeded):
        db, _, ids = seeded
        with pytest.raises(ValueError):
            save(db, ids[0], label="Unknown", status="parse_error")

    def test_invariant_holds_across_mixed_outcomes(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label="Intrinsic")
        save(db, ids[1], label=None, status="truncated")
        save(db, ids[2], label=None, status="empty_response")
        assert rows(
            db,
            "SELECT COUNT(*) FROM classifications "
            "WHERE status IS NOT NULL AND status<>'ok' AND classification IS NOT NULL",
        ) == [(0,)]


class TestResumeIsRunScoped:
    def test_other_model_sees_everything_as_unclassified(self, seeded):
        db, pid, ids = seeded
        for i in ids:
            save(db, i, label="Intrinsic")
        assert db.unclassified_in_window(pid, **_keys(RUN)) == []
        assert len(db.unclassified_in_window(pid, **_keys(OTHER))) == 3

    def test_failed_rows_are_retried(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label=None, status="api_error")
        remaining = [r[0] for r in db.unclassified_in_window(pid, **_keys(RUN))]
        assert ids[0] in remaining

    def test_success_removes_it_from_the_queue(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label=None, status="api_error")
        save(db, ids[0], label="Intrinsic")
        remaining = [r[0] for r in db.unclassified_in_window(pid, **_keys(RUN))]
        assert ids[0] not in remaining

    def test_legacy_null_status_counts_as_done(self, seeded):
        db, pid, ids = seeded
        raw = sqlite3.connect(db.path)
        raw.execute(
            "INSERT INTO classifications(issue_id, classification, model, prompt_version, temperature) "
            "VALUES(?,?,?,?,?)",
            (ids[0], "Intrinsic", RUN["model"], RUN["prompt"], RUN["temp"]),
        )
        raw.commit()
        raw.close()
        remaining = [r[0] for r in db.unclassified_in_window(pid, **_keys(RUN))]
        assert ids[0] not in remaining

    def test_unclassified_by_ids_is_scoped_too(self, seeded):
        db, _, ids = seeded
        for i in ids:
            save(db, i, label="Intrinsic")
        assert db.unclassified_by_ids(ids, **_keys(RUN)) == []
        assert len(db.unclassified_by_ids(ids, **_keys(OTHER))) == 3

    def test_count_matches_the_listing(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label="Intrinsic")
        assert db.count_unclassified(pid, **_keys(RUN)) == 2


class TestUpsert:
    def test_row_id_is_stable_across_resave(self, seeded):
        db, _, ids = seeded
        first = save(db, ids[0], label=None, status="api_error")
        second = save(db, ids[0], label="Intrinsic")
        assert first == second

    def test_resave_does_not_duplicate(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label=None, status="api_error")
        save(db, ids[0], label="Intrinsic")
        assert rows(db, f"SELECT COUNT(*) FROM classifications WHERE issue_id={ids[0]}") == [(1,)]

    def test_different_prompt_version_adds_a_row(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label="Intrinsic")
        save(db, ids[0], label="Extrinsic", prompt="inex@DIFFERENT+t2")
        assert rows(db, f"SELECT COUNT(*) FROM classifications WHERE issue_id={ids[0]}") == [(2,)]

    def test_returned_id_is_for_the_right_run(self, seeded):
        db, _, ids = seeded
        a = save(db, ids[0], label="Intrinsic")
        b = save(db, ids[0], label="Extrinsic", **{"model": "qwen3:30b"})
        assert a != b


class TestAggregatesExcludeFailures:
    def test_failure_only_project_has_no_classifications(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label=None, status="parse_error")
        assert db.project_has_classifications(pid) is False
        assert db.classified_issue_window(pid) == []

    def test_one_success_is_enough(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label=None, status="parse_error")
        save(db, ids[1], label="Intrinsic")
        assert db.project_has_classifications(pid) is True
        assert len(db.classified_issue_window(pid)) == 1

    def test_two_prompt_versions_do_not_double_count_issues(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label="Intrinsic")
        save(db, ids[0], label="Extrinsic", prompt="inex@DIFFERENT+t2")
        assert len(db.classified_issue_window(pid)) == 1


STAGE_RECORDS = [
    {
        "stage": "validity",
        "model": "m",
        "verdict": "Bug",
        "confidence": "High",
        "probabilities": None,
        "response": "**Final Answer:** Bug",
    }
]


def _stages(db):
    return rows(db, "SELECT stages FROM classifications ORDER BY id")


def test_stages_are_saved_as_json(seeded):
    db, _, ids = seeded
    save(db, ids[0], label="Intrinsic")
    db.save_classification(
        ids[1],
        {
            "classification": "Extrinsic",
            "classification_probabilities": {},
            "stages": STAGE_RECORDS,
        },
        classified_at="2026-11-03T00:00:00",
        **RUN,
    )
    first, second = _stages(db)
    assert first == (None,)
    assert json.loads(second[0]) == STAGE_RECORDS


def test_resaving_a_run_replaces_its_stages(seeded):
    db, _, ids = seeded
    for records in (STAGE_RECORDS, STAGE_RECORDS * 2):
        db.save_classification(
            ids[0],
            {"classification": "Intrinsic", "classification_probabilities": {}, "stages": records},
            classified_at="2026-11-03T00:00:00",
            **RUN,
        )
    (only,) = _stages(db)
    assert len(json.loads(only[0])) == 2


def test_older_database_gains_the_stages_column(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.execute(LEGACY_CLASSIFICATIONS)
    raw.commit()
    raw.close()
    store = Store(path)
    cols = {r[1] for r in rows(store, "PRAGMA table_info(classifications)")}
    assert "stages" in cols
