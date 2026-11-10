import logging
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from inex.errors import InexError
from inex.services.collection import CollectionService, CollectResult, CountResult

CREATED = {
    3: "2020-03-10T00:00:00Z",
    1: "2020-01-10T00:00:00Z",
    5: "2020-05-10T00:00:00Z",
    2: "2020-02-10T00:00:00Z",
    4: "2020-04-10T00:00:00Z",
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


def fake_issue_list(url, hdrs=None):
    params = parse_qs(urlparse(url).query)
    if params.get("page", ["1"])[0] != "1":
        return FakeResponse([])
    reverse = params["direction"][0] == "desc"
    ordered = sorted(CREATED.items(), key=lambda kv: kv[1], reverse=reverse)
    return FakeResponse(
        [
            {
                "number": n,
                "title": f"issue {n}",
                "state": "closed",
                "created_at": ts,
                "html_url": f"https://github.com/acme/widget/issues/{n}",
            }
            for n, ts in ordered
        ]
    )


def fake_collect_issue(owner, repo, num):
    return {
        "number": num,
        "title": f"issue {num}",
        "body": "body",
        "state": "closed",
        "state_reason": "completed",
        "created_at": CREATED[num],
    }


@pytest.fixture
def collector(monkeypatch, db):
    c = CollectionService(db, "test-token")
    monkeypatch.setattr(c, "get", fake_issue_list)
    monkeypatch.setattr(c, "collectIssue", fake_collect_issue)
    return c


def saved_in_order(db):
    return [r[0] for r in db.fetch_readonly("SELECT issue_number FROM issues ORDER BY id")[1]]


class TestCollectDirection:
    def test_ascending_collects_oldest_first_up_to_end_date(self, collector, db):
        collector.collect("acme", "widget", end="2020-03-31T23:59:59Z", order="asc")
        assert saved_in_order(db) == [1, 2, 3]

    def test_ascending_count_takes_the_oldest_issues(self, collector, db):
        collector.collect("acme", "widget", count=2, order="asc")
        assert saved_in_order(db) == [1, 2]

    def test_descending_stops_at_start_date(self, collector, db):
        collector.collect("acme", "widget", start="2020-03-01T00:00:00Z", order="desc")
        assert saved_in_order(db) == [5, 4, 3]


class TestCollectResult:
    def test_collect_reports_counts_and_skips_on_rerun(self, collector, db):
        assert collector.collect("acme", "widget") == CollectResult(5, skipped=0, total=5)
        assert collector.collect("acme", "widget") == CollectResult(0, skipped=5, total=5)

    def test_a_count_means_new_issues_and_the_total_says_what_is_stored(self, collector, db):
        assert collector.collect("acme", "widget", count=2) == CollectResult(2, 0, total=2)
        assert collector.collect("acme", "widget", count=2) == CollectResult(2, 2, total=4)

    def test_collect_without_a_token_raises(self, db):
        with pytest.raises(InexError) as e:
            CollectionService(db, None).collect("acme", "widget")
        assert e.value.code == "github_token_missing"


class TestCountIssues:
    @pytest.fixture
    def graphql(self, monkeypatch):
        sent = []

        def install(payload):
            def post(url, headers=None, json=None, timeout=None):
                sent.append({"url": url, "json": json})
                return FakeResponse(payload)

            monkeypatch.setattr("inex.services.collection.requests.post", post)
            return sent

        return install

    @pytest.fixture
    def search_refuses(self, monkeypatch):
        def refuse(self, url, hdrs=None):
            FakeResponse({"message": "Validation Failed"}, status=422).raise_for_status()

        monkeypatch.setattr(CollectionService, "get", refuse)

    def test_count_works_where_search_is_refused(self, graphql, search_refuses, db):
        sent = graphql({"data": {"repository": {"issues": {"totalCount": 14779}}}})
        assert CollectionService(db, "t").count("facebook", "react") == CountResult(
            "facebook", "react", 14779, stored=0
        )
        assert sent[0]["url"] == "https://api.github.com/graphql"
        assert sent[0]["json"]["variables"] == {"owner": "facebook", "name": "react"}

    def test_count_reports_how_many_are_already_stored(self, graphql, collector):
        graphql({"data": {"repository": {"issues": {"totalCount": 9}}}})
        collector.collect("acme", "widget", count=2)
        assert collector.count("acme", "widget") == CountResult("acme", "widget", 9, stored=2)

    def test_missing_repository_raises(self, graphql, search_refuses, db):
        graphql(
            {
                "data": {"repository": None},
                "errors": [
                    {
                        "type": "NOT_FOUND",
                        "path": ["repository"],
                        "message": "Could not resolve to a Repository with the name 'nope/nope'.",
                    }
                ],
            }
        )
        with pytest.raises(InexError) as e:
            CollectionService(db, "t").count("nope", "nope")
        assert e.value.code == "repo_not_found"

    def test_missing_token_raises_before_any_request(self, graphql, db):
        sent = graphql({})
        with pytest.raises(InexError) as e:
            CollectionService(db, None).count("facebook", "react")
        assert e.value.code == "github_token_missing"
        assert sent == []


def failing_get(status=None, error=None):
    def get(url, hdrs=None):
        if error is not None:
            raise error
        FakeResponse({}, status=status).raise_for_status()

    return get


class TestCollectErrors:
    @pytest.mark.parametrize(
        "status, code",
        [(404, "repo_not_found"), (401, "github_auth_failed"), (403, "github_auth_failed")],
    )
    def test_http_errors_are_named_and_leave_no_project(self, db, monkeypatch, status, code):
        service = CollectionService(db, "t")
        monkeypatch.setattr(service, "get", failing_get(status=status))
        with pytest.raises(InexError) as e:
            service.collect("acme", "nope")
        assert e.value.code == code
        assert db.project_id("acme", "nope") is None

    def test_network_failure_is_named(self, db, monkeypatch):
        service = CollectionService(db, "t")
        monkeypatch.setattr(service, "get", failing_get(error=requests.ConnectionError("down")))
        with pytest.raises(InexError) as e:
            service.collect("acme", "widget")
        assert e.value.code == "network_error"

    def test_count_names_network_and_auth_failures(self, db, monkeypatch):
        def offline(url, headers=None, json=None, timeout=None):
            raise requests.ConnectionError("down")

        monkeypatch.setattr("inex.services.collection.requests.post", offline)
        with pytest.raises(InexError) as e:
            CollectionService(db, "t").count("facebook", "react")
        assert e.value.code == "network_error"

        def unauthorized(url, headers=None, json=None, timeout=None):
            return FakeResponse({}, status=401)

        monkeypatch.setattr("inex.services.collection.requests.post", unauthorized)
        with pytest.raises(InexError) as e:
            CollectionService(db, "t").count("facebook", "react")
        assert e.value.code == "github_auth_failed"


def scripted_requests(monkeypatch, outcomes):
    import inex.services.collection as collection

    waits = []
    monkeypatch.setattr(collection.time, "sleep", waits.append)

    def fake_get(url, headers=None, timeout=None):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse({}, status=outcome)

    monkeypatch.setattr(collection.requests, "get", fake_get)
    return waits


class TestNetworkRetries:
    def test_a_dropped_connection_is_retried(self, db, monkeypatch):
        drop = requests.ConnectionError("Remote end closed connection without response")
        waits = scripted_requests(monkeypatch, [drop, drop, 200])
        assert CollectionService(db, "t").get("https://api.github.com/x").status_code == 200
        assert waits == [2, 4]

    def test_server_errors_and_timeouts_are_retried(self, db, monkeypatch):
        waits = scripted_requests(monkeypatch, [503, requests.Timeout("slow"), 200])
        assert CollectionService(db, "t").get("https://api.github.com/x").status_code == 200
        assert waits == [2, 4]

    def test_retries_stop_after_the_last_attempt(self, db, monkeypatch):
        drop = requests.ConnectionError("down")
        waits = scripted_requests(monkeypatch, [drop, drop, drop, drop])
        with pytest.raises(requests.ConnectionError):
            CollectionService(db, "t").get("https://api.github.com/x")
        assert waits == [2, 4, 8]

    def test_client_errors_are_not_retried(self, db, monkeypatch):
        waits = scripted_requests(monkeypatch, [404])
        with pytest.raises(requests.HTTPError):
            CollectionService(db, "t").get("https://api.github.com/x")
        assert waits == []

    def test_a_failure_mid_collection_is_named_and_keeps_what_was_saved(
        self, collector, db, monkeypatch
    ):
        calls = []

        def flaky(owner, repo, num):
            calls.append(num)
            if len(calls) == 2:
                raise requests.ConnectionError("down")
            return fake_collect_issue(owner, repo, num)

        monkeypatch.setattr(collector, "collectIssue", flaky)
        with pytest.raises(InexError) as e:
            collector.collect("acme", "widget")
        assert e.value.code == "network_error"
        assert "1 issues collected so far are saved" in e.value.message
        assert saved_in_order(db) == calls[:1]


DELETED_ACCOUNT_GITHUB = {
    "/repos/acme/widget/issues/7": {
        "number": 7,
        "state": "closed",
        "created_at": "2021-01-01T00:00:00Z",
        "closed_at": "2021-01-02T00:00:00Z",
        "user": None,
        "closed_by": None,
        "milestone": None,
        "labels": [],
        "assignees": [],
    },
    "/repos/acme/widget/issues/7/comments": [
        {
            "id": 1,
            "created_at": "2021-01-01T06:00:00Z",
            "user": None,
            "author_association": "NONE",
            "body": "same here",
        }
    ],
    "/repos/acme/widget/issues/7/timeline": [
        {
            "event": "cross-referenced",
            "created_at": "2021-01-01T12:00:00Z",
            "source": {"type": "issue", "issue": {"number": 8}},
        },
        {"event": "closed", "created_at": "2021-01-02T00:00:00Z"},
    ],
    "/repos/acme/widget/pulls/8": {
        "number": 8,
        "merged_at": "2021-01-02T00:00:00Z",
        "user": None,
        "merged_by": None,
        "head": {"ref": "fix", "sha": "abc"},
        "base": {"ref": "main"},
    },
    "/repos/acme/widget/pulls/8/reviews": [{"user": None, "state": "APPROVED"}],
}


class TestDeletedAccounts:
    def test_an_issue_touched_by_deleted_accounts_is_collected(self, db, monkeypatch):
        service = CollectionService(db, "t")

        def github(url, hdrs=None):
            return FakeResponse(DELETED_ACCOUNT_GITHUB[urlparse(url).path])

        monkeypatch.setattr(service, "get", github)
        issue = service.collectIssue("acme", "widget", 7)
        assert issue["author"]["username"] is None
        assert issue["comments"][0]["author"]["username"] is None
        assert "unknown:\nsame here" in issue["comments_text"]
        assert issue["closing_pr"]["number"] == 8
        assert issue["closing_pr"]["approved_count"] == 1
        assert issue["closing_pr"]["reviewer_usernames"] == []


def progress_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "inex.progress"]


class TestProgress:
    @pytest.fixture(autouse=True)
    def every_two(self, monkeypatch):
        import inex.services.collection as collection

        monkeypatch.setattr(collection, "PROGRESS_EVERY", 2)

    def test_progress_names_the_target(self, collector, caplog):
        with caplog.at_level(logging.INFO, logger="inex.progress"):
            collector.collect("acme", "widget", count=4)
        assert progress_lines(caplog) == [
            "acme/widget: 2 of 4 issues collected, 0 already saved",
            "acme/widget: 4 of 4 issues collected, 0 already saved",
        ]

    def test_progress_counts_issues_already_saved(self, collector, caplog):
        collector.collect("acme", "widget", count=2)
        with caplog.at_level(logging.INFO, logger="inex.progress"):
            collector.collect("acme", "widget")
        assert progress_lines(caplog) == ["acme/widget: 2 issues collected, 2 already saved"]

    def test_progress_for_a_listed_import(self, collector, caplog):
        with caplog.at_level(logging.INFO, logger="inex.progress"):
            collector.collect_specific("acme", "widget", [1, 2, 3])
        assert progress_lines(caplog) == ["acme/widget: 2 of 3 listed issues collected"]


def progress_events(caplog):
    return [
        (r.task, r.done, r.total, r.finished)
        for r in caplog.records
        if r.name == "inex.progress" and hasattr(r, "task")
    ]


TASK = "Collecting acme/widget"


class TestProgressEvents:
    def test_each_issue_reports_done_and_total(self, collector, caplog):
        with caplog.at_level(logging.DEBUG, logger="inex.progress"):
            collector.collect("acme", "widget", count=2)
        assert progress_events(caplog) == [
            (TASK, 1, 2, False),
            (TASK, 2, 2, False),
            (TASK, 2, 2, True),
        ]

    def test_an_open_ended_collection_has_no_total(self, collector, caplog):
        collector.collect("acme", "widget", count=2)
        with caplog.at_level(logging.DEBUG, logger="inex.progress"):
            collector.collect("acme", "widget")
        assert progress_events(caplog) == [
            (TASK, 1, None, False),
            (TASK, 2, None, False),
            (TASK, 3, None, False),
            (TASK, 3, None, True),
        ]

    def test_a_listed_import_counts_every_listed_issue(self, collector, caplog):
        collector.collect_specific("acme", "widget", [1])
        with caplog.at_level(logging.DEBUG, logger="inex.progress"):
            collector.collect_specific("acme", "widget", [1, 2, 3])
        assert progress_events(caplog) == [
            (TASK, 1, 3, False),
            (TASK, 2, 3, False),
            (TASK, 3, 3, False),
            (TASK, 3, 3, True),
        ]

    def test_a_failed_collection_still_finishes(self, db, monkeypatch, caplog):
        service = CollectionService(db, "t")
        monkeypatch.setattr(service, "get", failing_get(error=requests.ConnectionError("down")))
        with caplog.at_level(logging.DEBUG, logger="inex.progress"):
            with pytest.raises(InexError):
                service.collect("acme", "widget", count=5)
        assert progress_events(caplog) == [(TASK, 0, 5, True)]
