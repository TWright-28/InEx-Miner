import json

import pytest

from inex.errors import InexError
from inex.services.classification import ClassifyResult
from inex.services.collection import SpecificResult
from inex.services.imports import (
    ImportResult,
    ImportService,
    LoadResult,
    RepoImport,
    parse_issue_list,
)


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


class FakeCollection:
    def __init__(self, store):
        self.store = store

    def collect_specific(self, owner, repo, numbers):
        project_id = self.store.save_project(owner, repo)
        ids = [self.store.save_issue(project_id, {"number": n, "title": str(n)}) for n in numbers]
        return SpecificResult(project_id, tuple(ids), len(numbers), 0, 0)


class FakeClassification:
    def __init__(self):
        self.calls = []

    def classify_ids(self, owner, repo, issue_ids):
        self.calls.append((owner, repo, list(issue_ids)))
        return ClassifyResult(owner, repo, classified=len(issue_ids), failed={})


def code_of(call):
    with pytest.raises(InexError) as e:
        call()
    return e.value.code


def test_excel_csv_parses_like_a_clean_one(tmp_path):
    clean, excel = tmp_path / "clean.csv", tmp_path / "excel.csv"
    write(clean, "ref\nacme/widget#1\nacme/other#2\n")
    write(excel, "﻿ref\r\n  acme/widget#1  \r\nacme/other#2\r\n")
    expected = ([("acme", "widget", 1), ("acme", "other", 2)], [])
    assert parse_issue_list(excel) == parse_issue_list(clean) == expected


def test_named_column(tmp_path):
    write(tmp_path / "l.csv", "note,ref\nx,acme/widget#7\n")
    assert parse_issue_list(tmp_path / "l.csv", "ref") == ([("acme", "widget", 7)], [])


def test_preview_list(seeded, tmp_path):
    db, _, _ = seeded
    write(tmp_path / "l.csv", "ref\nacme/widget#101\nacme/widget#900\nzed/z#1\nnot a ref\n")
    p = ImportService(db, None, None).preview_list(tmp_path / "l.csv")
    assert (p.total_issues, p.distinct_repos, p.already_in_db, p.to_collect) == (3, 2, 1, 2)
    assert (p.unparseable_count, p.unparseable_sample) == (1, ("not a ref",))


def test_import_list_collects_and_classifies_per_repo(db, tmp_path):
    write(tmp_path / "l.csv", "ref\nacme/widget#1\nacme/widget#2\nzed/z#3\n")
    classification = FakeClassification()
    result = ImportService(db, FakeCollection(db), classification).import_list(tmp_path / "l.csv")
    assert result == ImportResult(
        collected=3,
        skipped=0,
        failed=0,
        classified=3,
        repos=(RepoImport("acme/widget", 2, 0, 0, 2), RepoImport("zed/z", 1, 0, 0, 1)),
    )
    assert [c[:2] for c in classification.calls] == [("acme", "widget"), ("zed", "z")]


def test_import_list_can_skip_classification(db, tmp_path):
    write(tmp_path / "l.csv", "ref\nacme/widget#1\n")
    classification = FakeClassification()
    result = ImportService(db, FakeCollection(db), classification).import_list(
        tmp_path / "l.csv", classify=False
    )
    assert (result.collected, result.classified, classification.calls) == (1, 0, [])


def test_import_list_errors(db, tmp_path):
    service = ImportService(db, FakeCollection(db), FakeClassification())
    assert code_of(lambda: service.import_list(tmp_path / "missing.csv")) == "file_not_found"
    write(tmp_path / "bad.csv", "ref\nnot a ref\n")
    assert code_of(lambda: service.import_list(tmp_path / "bad.csv")) == "no_valid_entries"
    assert code_of(lambda: service.preview_list(tmp_path / "missing.csv")) == "file_not_found"


def jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


ISSUES = [
    {"owner": "acme", "repo": "widget", "number": 1, "title": "one"},
    {
        "owner": "acme",
        "repo": "widget",
        "number": 2,
        "title": "two",
        "classification": "Intrinsic",
        "classification_probabilities": {"Intrinsic": 1.0},
    },
]


def test_load_jsonl_loads_issues_and_classifications_once(db, tmp_path):
    jsonl(tmp_path / "a.jsonl", ISSUES)
    service = ImportService(db, None, None)
    first = service.load_jsonl(tmp_path / "a.jsonl", "qwen3:30b", "p", 0.2)
    again = service.load_jsonl(tmp_path / "a.jsonl", "qwen3:30b", "p", 0.2)
    assert first == LoadResult(files=1, issues=2, classifications=1)
    assert again == LoadResult(files=1, issues=2, classifications=0)
    rows = db.fetch_readonly(
        "SELECT model, prompt_version, temperature, classification FROM classifications"
    )
    assert rows[1] == [("qwen3:30b", "p", 0.2, "Intrinsic")]


def test_load_jsonl_keeps_labels_from_another_profile(db, tmp_path):
    jsonl(tmp_path / "a.jsonl", ISSUES)
    service = ImportService(db, None, None)
    service.load_jsonl(tmp_path / "a.jsonl", "qwen3:30b", "inex@abc+t2", 0.2)
    jsonl(tmp_path / "b.jsonl", [{**ISSUES[1], "classification": "Major"}])
    result = service.load_jsonl(tmp_path / "b.jsonl", "qwen3:30b", "severity@def+t2", 0.2)
    assert result == LoadResult(files=1, issues=1, classifications=1)
    rows = db.fetch_readonly(
        "SELECT prompt_version, classification FROM classifications ORDER BY prompt_version"
    )
    assert rows[1] == [("inex@abc+t2", "Intrinsic"), ("severity@def+t2", "Major")]


def test_load_jsonl_replaces_a_failed_attempt(db, tmp_path):
    project_id = db.save_project("acme", "widget")
    issue_id = db.save_issue(project_id, {"number": 2, "title": "two"})
    db.save_classification(
        issue_id,
        {"classification": None, "status": "parse_error"},
        "qwen3:30b",
        "p",
        0.2,
        "2026-10-26T00:00:00",
    )
    jsonl(tmp_path / "a.jsonl", ISSUES[1:])
    result = ImportService(db, None, None).load_jsonl(tmp_path / "a.jsonl", "qwen3:30b", "p", 0.2)
    assert result == LoadResult(files=1, issues=1, classifications=1)
    rows = db.fetch_readonly("SELECT classification, status FROM classifications")
    assert rows[1] == [("Intrinsic", "ok")]


def test_load_jsonl_searches_directories_and_skips_bad_lines(db, tmp_path):
    jsonl(tmp_path / "data" / "nested" / "b.jsonl", ISSUES[:1])
    (tmp_path / "data" / "c.jsonl").write_text(
        "{not json\n" + json.dumps(ISSUES[1]) + "\n", encoding="utf-8"
    )
    result = ImportService(db, None, None).load_jsonl(tmp_path / "data", "m", "p", 0.2)
    assert result == LoadResult(files=2, issues=2, classifications=1)


def test_load_jsonl_missing_path(db, tmp_path):
    service = ImportService(db, None, None)
    assert code_of(lambda: service.load_jsonl(tmp_path / "nope", "m", "p", 0.2)) == "file_not_found"
    (tmp_path / "empty").mkdir()
    assert (
        code_of(lambda: service.load_jsonl(tmp_path / "empty", "m", "p", 0.2)) == "file_not_found"
    )
