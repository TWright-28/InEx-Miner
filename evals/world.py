import json
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from evals.scenarios import SEEDED_ISSUES
from inex.app import build_app
from inex.config import REPO_ROOT
from inex.errors import InexError
from inex.services.classification import ClassificationService, ParsedResult
from inex.services.collection import PER_PAGE, CollectionService, CountResult
from inex.services.imports import ImportService
from inex.services.linkage import LinkageService
from inex.store.database import RAW_DATA_KEYS, Store

DATABASE_NAME = "eval.db"
CLASSIFIER = "qwen3:30b"
GITHUB_TOKEN = "eval-token"
OWNER = "yeoman"
REPO = "yeoman-test"
PACKAGE = "yeoman-test"
PACKAGE_NAMES = ("yeoman-test", "@yeoman/test")
FIRST_NUMBER = 101
SEED_TIME = "2024-01-01T00:00:00"
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SEEDED = (
    (101, "Test runner fails on async", "Fails with async/await patterns", "2023-06-01T10:00:00Z"),
    (
        102,
        "Breaks after sinon upgrade",
        "After upgrading sinon 14 to 15 it breaks",
        "2023-08-01T10:00:00Z",
    ),
    (103, "How do I mock ES modules?", "Usage question about mocking", "2023-09-01T10:00:00Z"),
)
GENERATED_START = datetime(2023, 9, 1, 10, 0, 0)
OTHER_REPOSITORIES = {("yeoman", "generator"): (1, 1600, datetime(2020, 1, 1))}
TITLES = (
    "Assertion helpers fail on Windows paths",
    "run() resolves before the generator finishes",
    "TypeError when mocking a prompt with defaults",
    "Docs: example for testing composed generators",
    "Timeout running tests on Node 20",
    "withOptions is ignored by composeWith",
    "Question: how to test the conflicter",
    "Flaky test when writing files in parallel",
)
GENERATED_BODY = "Steps to reproduce are in the linked example repository."
NOT_FOUND = 404
FAILED_CALL = "The model call failed."
AUTH_MESSAGE = (
    "GitHub refused the request (HTTP 401). "
    "Check that GITHUB_TOKEN is valid and can read this repository."
)
ISSUE_INSERT = (
    "INSERT OR IGNORE INTO issues(project_id, issue_number, github_id, url, title, body, "
    "state, state_reason, locked, created_at, closed_at, updated_at, comments_count, "
    "raw_data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _release(direct, peer, dev):
    return {"dependencies": direct, "peerDependencies": peer, "devDependencies": dev}


RELEASES = {
    "7.0.0": (
        "2022-11-01T00:00:00Z",
        _release(
            {"sinon": "^13.0.0", "mocha": "^9.0.0", "lodash": "^4.17.0"},
            {"yeoman-generator": "^5.0.0"},
            {"eslint": "^8.0.0", "prettier": "^2.0.0"},
        ),
    ),
    "7.1.0": (
        "2023-02-01T00:00:00Z",
        _release(
            {"sinon": "^13.0.0", "mocha": "^9.0.0", "lodash": "^4.17.0", "inquirer": "^8.0.0"},
            {"yeoman-generator": "^5.0.0"},
            {"eslint": "^8.0.0", "prettier": "^2.0.0"},
        ),
    ),
    "8.0.0": (
        "2023-05-01T00:00:00Z",
        _release(
            {"sinon": "^14.0.0", "mocha": "^10.0.0", "lodash": "^4.17.0"},
            {"yeoman-generator": "^5.0.0"},
            {"eslint": "^8.0.0", "prettier": "^2.0.0", "c8": "^7.0.0"},
        ),
    ),
    "9.0.0": (
        "2023-07-01T00:00:00Z",
        _release(
            {"sinon": "^15.0.0", "mocha": "^10.0.0"},
            {"yeoman-generator": "^6.0.0", "yeoman-environment": "^3.0.0"},
            {"eslint": "^8.0.0", "prettier": "^3.0.0"},
        ),
    ),
    "9.1.0": (
        "2023-10-01T00:00:00Z",
        _release(
            {"sinon": "^15.0.0", "mocha": "^10.0.0", "type-fest": "^3.0.0"},
            {"yeoman-generator": "^6.0.0", "yeoman-environment": "^3.0.0"},
            {"eslint": "^8.0.0", "prettier": "^3.0.0"},
        ),
    ),
}
PACKAGE_DOCUMENT = {
    "name": PACKAGE,
    "versions": {version: manifest for version, (_, manifest) in RELEASES.items()},
    "time": {version: published for version, (published, _) in RELEASES.items()},
}


def _message(code, owner, repo):
    if code == "repo_not_found":
        return f"{owner}/{repo} was not found on GitHub."
    if code == "github_auth_failed":
        return AUTH_MESSAGE
    return f"The request failed ({code})."


def _key(owner, repo):
    return (owner.lower(), repo.lower())


def _seeded(owner, repo, number):
    if _key(owner, repo) != (OWNER, REPO):
        return None
    return next((s for s in SEEDED if s[0] == number), None)


def _created(owner, repo, number):
    seeded = _seeded(owner, repo, number)
    if seeded is not None:
        return seeded[3]
    if _key(owner, repo) == (OWNER, REPO):
        moment = GENERATED_START + timedelta(hours=number - SEEDED[-1][0])
    else:
        moment = OTHER_REPOSITORIES[_key(owner, repo)][2] + timedelta(hours=number)
    return moment.strftime(TIME_FORMAT)


def _issue(owner, repo, number):
    seeded = _seeded(owner, repo, number)
    title, body = (
        (seeded[1], seeded[2]) if seeded else (TITLES[number % len(TITLES)], GENERATED_BODY)
    )
    created = _created(owner, repo, number)
    closed = (datetime.strptime(created, TIME_FORMAT) + timedelta(days=1)).strftime(TIME_FORMAT)
    return {
        "owner": owner,
        "repo": repo,
        "number": number,
        "id": 900000 + number,
        "url": f"https://github.com/{owner}/{repo}/issues/{number}",
        "title": title,
        "body": body,
        "state": "closed",
        "state_reason": "completed",
        "locked": False,
        "created_at": created,
        "closed_at": closed,
        "updated_at": closed,
        "comments_count": 0,
    }


def _issue_values(project_id, issue):
    raw_data = json.dumps({key: issue.get(key) for key in RAW_DATA_KEYS})
    columns = ("number", "id", "url", "title", "body", "state", "state_reason", "locked")
    times = ("created_at", "closed_at", "updated_at", "comments_count")
    return (project_id, *(issue[c] for c in columns), *(issue[c] for c in times), raw_data)


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


def _not_found():
    return requests.HTTPError(f"HTTP {NOT_FOUND}", response=_Response(NOT_FOUND))


@dataclass
class _State:
    spec: object
    forced: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    armed: bool = False

    def maybe_fail(self, tool, owner, repo):
        code = self.spec.errors.get(tool) if self.armed else None
        if code is not None:
            self.forced.append(tool)
            raise InexError(code, _message(code, owner, repo))

    def numbers(self, owner, repo):
        if _key(owner, repo) == (OWNER, REPO):
            return range(FIRST_NUMBER, FIRST_NUMBER + self.spec.issue_count)
        found = OTHER_REPOSITORIES.get(_key(owner, repo))
        if found is None:
            return None
        return range(found[0], found[0] + found[1])


class _Collection(CollectionService):
    def __init__(self, store, state):
        super().__init__(store, GITHUB_TOKEN)
        self.state = state

    def count(self, owner, repo):
        self.state.maybe_fail("count_issues", owner, repo)
        numbers = self.state.numbers(owner, repo)
        if numbers is None:
            raise InexError("repo_not_found", _message("repo_not_found", owner, repo))
        return CountResult(owner, repo, len(numbers), self._stored(owner, repo))

    def collect(self, owner, repo, **kwargs):
        self.state.maybe_fail("collect_all", owner, repo)
        return super().collect(owner, repo, **kwargs)

    def get(self, url, hdrs=None):
        parts = urlparse(url)
        owner, repo = parts.path.split("/")[2:4]
        numbers = self.state.numbers(owner, repo)
        if numbers is None:
            raise _not_found()
        query = parse_qs(parts.query)
        if query.get("direction", ["desc"])[0] == "desc":
            numbers = numbers[::-1]
        size = int(query.get("per_page", [PER_PAGE])[0])
        page = int(query.get("page", ["1"])[0])
        chosen = numbers[(page - 1) * size : page * size]
        return _Response(
            200, [{"number": n, "created_at": _created(owner, repo, n)} for n in chosen]
        )

    def collectIssue(self, owner, repo, num):  # noqa: N802
        numbers = self.state.numbers(owner, repo)
        if numbers is None or num not in numbers:
            raise _not_found()
        return _issue(owner, repo, num)


class _Classification(ClassificationService):
    def __init__(self, store, profile, temperature, state):
        super().__init__(store, profile, CLASSIFIER, temperature, workers=1, retries=1)
        self.state = state

    def preview(self, owner, repo, **kwargs):
        self.state.maybe_fail("preview_classification", owner, repo)
        return super().preview(owner, repo, **kwargs)

    def classify(self, owner, repo, **kwargs):
        self.state.maybe_fail("classify_all", owner, repo)
        return super().classify(owner, repo, **kwargs)

    def classifyOne(self, issue):  # noqa: N802
        if self.state.failures:
            return ParsedResult(status=self.state.failures.pop(0), error=FAILED_CALL)
        label = self.Labels[(issue["number"] - FIRST_NUMBER) % len(self.Labels)]
        return ParsedResult(
            label=label,
            probs={name: float(name == label) for name in self.Labels},
            raw_text=json.dumps({"classification": label}),
            argmax_label=label,
        )


class _Npm:
    def package_document(self, name):
        return PACKAGE_DOCUMENT if name in PACKAGE_NAMES else None


class _Linkage(LinkageService):
    def __init__(self, store, state):
        super().__init__(store, _Npm())
        self.state = state

    def link(self, owner, repo, package, **kwargs):
        self.state.maybe_fail("snapshot_dependencies", owner, repo)
        try:
            return super().link(owner, repo, package, **kwargs)
        except InexError as error:
            if error.code == "no_classifications" and self.state.armed:
                self.state.forced.append("snapshot_dependencies")
            raise


class _RepoRelativeImports:
    def __init__(self, service):
        self.service = service

    def preview_list(self, csv_path, column=None):
        return self.service.preview_list(REPO_ROOT / csv_path, column)

    def import_list(self, csv_path, column=None, classify=True):
        return self.service.import_list(REPO_ROOT / csv_path, column, classify)


class World:
    def __init__(self, app, root, forced):
        self.app = app
        self.root = root
        self.forced = forced
        self._closed = False

    def close(self):
        if not self._closed:
            self._closed = True
            self.app.close()


def _seed_issues(path, spec):
    Store(path).close()
    numbers = range(FIRST_NUMBER, FIRST_NUMBER + SEEDED_ISSUES + spec.backlog)
    connection = sqlite3.connect(path)
    try:
        with connection:
            project_id = connection.execute(
                "INSERT INTO projects(owner, repo, added_at) VALUES (?, ?, ?)",
                (OWNER, REPO, SEED_TIME),
            ).lastrowid
            connection.executemany(
                ISSUE_INSERT, [_issue_values(project_id, _issue(OWNER, REPO, n)) for n in numbers]
            )
    finally:
        connection.close()


def build_world(spec, root: Path, settings) -> World:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    database = root / DATABASE_NAME
    _seed_issues(database, spec)
    world_settings = replace(
        settings,
        database=database,
        exports_dir=root / "exports",
        npm_cache_dir=root / "npm",
        checkpoints=root / "checkpoints.db",
        classifier=CLASSIFIER,
    )
    app = build_app(world_settings)
    store = app.queries.store
    state = _State(spec)
    collection = _Collection(store, state)
    classification = _Classification(store, app.profile, world_settings.temperature, state)
    linkage = _Linkage(store, state)
    imports = _RepoRelativeImports(ImportService(store, collection, classification))
    app = replace(
        app,
        collection=collection,
        classification=classification,
        linkage=linkage,
        imports=imports,
    )
    if spec.classified:
        project_id = store.project_id(OWNER, REPO)
        seeded = [store.issue_id(project_id, number) for number, *_ in SEEDED]
        classification.classify_ids(OWNER, REPO, seeded)
        linkage.link(OWNER, REPO, PACKAGE)
    state.failures = [code for code, n in spec.classify_failed.items() for _ in range(n)]
    state.armed = True
    return World(app, root, state.forced)
