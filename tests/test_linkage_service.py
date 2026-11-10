import pytest

from inex.errors import InexError
from inex.services.linkage import LinkageService, LinkResult


class FakeNpm:
    def __init__(self, doc):
        self.doc = doc
        self.asked = []

    def package_document(self, name):
        self.asked.append(name)
        return self.doc


DOC = {
    "versions": {
        "1.0.0": {"dependencies": {"a": "^1"}, "devDependencies": {"b": "^2"}},
        "2.0.0": {"dependencies": {"a": "^1", "c": "^3"}},
    },
    "time": {"1.0.0": "2023-01-15T00:00:00.000Z", "2.0.0": "2023-02-15T00:00:00.000Z"},
}


def code_of(call):
    with pytest.raises(InexError) as e:
        call()
    return e.value.code


def test_links_each_classified_issue_to_the_live_release(seeded_classified):
    store, pid, ids = seeded_classified
    result = LinkageService(store, FakeNpm(DOC)).link("acme", "widget", "widget")
    assert result == LinkResult(
        package_name="widget",
        issue_versions_snapshotted=2,
        slice_versions_snapshotted=0,
        linked_issues=2,
        already_linked=0,
        unlinkable_pre_release=1,
    )
    versions = store.fetch_readonly(
        "SELECT i.issue_number, v.version FROM issues i JOIN versions v ON v.id = i.version_id "
        "ORDER BY i.issue_number"
    )[1]
    assert versions == [(102, "1.0.0"), (103, "2.0.0")]


def test_second_run_reports_already_linked(seeded_classified):
    store, _, _ = seeded_classified
    service = LinkageService(store, FakeNpm(DOC))
    service.link("acme", "widget", "widget")
    again = service.link("acme", "widget", "widget")
    assert (again.already_linked, again.linked_issues) == (2, 0)


def test_unknown_project(db):
    assert code_of(lambda: LinkageService(db, FakeNpm(DOC)).link("nope", "nope", "x")) == (
        "project_not_found"
    )


def test_project_without_ok_classifications(seeded):
    db, _, _ = seeded
    assert code_of(lambda: LinkageService(db, FakeNpm(DOC)).link("acme", "widget", "widget")) == (
        "no_classifications"
    )


def test_missing_npm_package(seeded_classified):
    store, _, _ = seeded_classified
    assert code_of(lambda: LinkageService(store, FakeNpm(None)).link("acme", "widget", "x")) == (
        "npm_package_not_found"
    )


def test_version_range_with_no_match(seeded_classified):
    store, _, _ = seeded_classified
    service = LinkageService(store, FakeNpm(DOC))
    assert code_of(lambda: service.link("acme", "widget", "widget", version_range="9.x")) == (
        "no_versions_match_range"
    )
