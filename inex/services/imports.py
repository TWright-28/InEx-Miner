import csv
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from inex.errors import InexError

logger = logging.getLogger(__name__)

ISSUE_REF_RE = re.compile(r"^\s*([^/\s#]+)/([^/\s#]+)#(\d+)\s*$")
UNPARSEABLE_SAMPLE_SIZE = 5


@dataclass(frozen=True)
class RepoCount:
    repo: str
    issues: int
    already_in_db: int


@dataclass(frozen=True)
class ListPreview:
    total_issues: int
    distinct_repos: int
    already_in_db: int
    to_collect: int
    repos: tuple
    unparseable_count: int
    unparseable_sample: tuple


@dataclass(frozen=True)
class RepoImport:
    repo: str
    collected: int
    skipped: int
    failed: int
    classified: int


@dataclass(frozen=True)
class ImportResult:
    collected: int
    skipped: int
    failed: int
    classified: int
    repos: tuple


@dataclass(frozen=True)
class LoadResult:
    files: int
    issues: int
    classifications: int


def parse_issue_list(csv_path, column=None):
    entries = []
    unparseable = []
    try:
        handle = open(csv_path, newline="", encoding="utf-8-sig")
    except FileNotFoundError as error:
        raise InexError(
            "file_not_found", f"No issue list at {csv_path}.", path=str(csv_path)
        ) from error
    with handle:
        reader = csv.DictReader(handle)
        name = column or (reader.fieldnames or [None])[0]
        for row in reader:
            raw = (row.get(name) or "").strip()
            if not raw:
                continue
            match = ISSUE_REF_RE.match(raw)
            if not match:
                unparseable.append(raw)
                continue
            entries.append((match.group(1), match.group(2), int(match.group(3))))
    return entries, unparseable


def _group(entries):
    by_repo = defaultdict(list)
    for owner, repo, number in entries:
        by_repo[(owner, repo)].append(number)
    return sorted(by_repo.items())


def _jsonl_files(path):
    path = Path(path)
    if path.is_dir():
        return sorted(path.rglob("*.jsonl"))
    return [path] if path.is_file() else []


class ImportService:
    def __init__(self, store, collection, classification):
        self.store = store
        self.collection = collection
        self.classification = classification

    def preview_list(self, csv_path, column=None) -> ListPreview:
        entries, unparseable = parse_issue_list(csv_path, column)
        repos = []
        for (owner, repo), numbers in _group(entries):
            project_id = self.store.project_id(owner, repo)
            already = self.store.count_existing_issues(project_id, numbers) if project_id else 0
            repos.append(RepoCount(f"{owner}/{repo}", len(numbers), already))
        already_total = sum(r.already_in_db for r in repos)
        return ListPreview(
            total_issues=len(entries),
            distinct_repos=len(repos),
            already_in_db=already_total,
            to_collect=len(entries) - already_total,
            repos=tuple(repos),
            unparseable_count=len(unparseable),
            unparseable_sample=tuple(unparseable[:UNPARSEABLE_SAMPLE_SIZE]),
        )

    def import_list(self, csv_path, column=None, classify=True) -> ImportResult:
        entries, unparseable = parse_issue_list(csv_path, column)
        if not entries:
            raise InexError(
                "no_valid_entries",
                f"No owner/repo#number references found in {csv_path}.",
                unparseable_sample=unparseable[:UNPARSEABLE_SAMPLE_SIZE],
            )
        repos = []
        for (owner, repo), numbers in _group(entries):
            specific = self.collection.collect_specific(owner, repo, numbers)
            classified = 0
            if classify and specific.issue_ids:
                classified = self.classification.classify_ids(
                    owner, repo, list(specific.issue_ids)
                ).classified
            repos.append(
                RepoImport(
                    f"{owner}/{repo}",
                    specific.collected,
                    specific.skipped,
                    specific.failed,
                    classified,
                )
            )
        return ImportResult(
            collected=sum(r.collected for r in repos),
            skipped=sum(r.skipped for r in repos),
            failed=sum(r.failed for r in repos),
            classified=sum(r.classified for r in repos),
            repos=tuple(repos),
        )

    def load_jsonl(self, path, model, prompt_version, temperature) -> LoadResult:
        files = _jsonl_files(path)
        if not files:
            raise InexError("file_not_found", f"No .jsonl files found at {path}.", path=str(path))
        issues = classifications = 0
        for file in files:
            logger.info("Loading %s", file)
            with open(file, encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as error:
                        logger.warning("Skipping unreadable line in %s: %s", file, error)
                        continue
                    if not (record.get("owner") and record.get("repo")):
                        logger.warning("Skipping line without owner and repo in %s", file)
                        continue
                    project_id = self.store.save_project(record["owner"], record["repo"])
                    issue_id = self.store.save_issue(project_id, record)
                    issues += 1
                    if record.get("classification") and not self.store.has_classification(
                        issue_id, model, prompt_version, temperature
                    ):
                        self.store.save_classification(
                            issue_id,
                            record,
                            model,
                            prompt_version,
                            temperature,
                            record.get("classified_at") or datetime.now().isoformat(),
                        )
                        classifications += 1
        return LoadResult(files=len(files), issues=issues, classifications=classifications)
