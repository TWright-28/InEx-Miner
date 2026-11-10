import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from inex.errors import InexError
from inex.store.schema import TABLE_NAMES, ensure_schema

SUCCEEDED = "(c.status IS NULL OR c.status = 'ok')"

DONE_JOIN = """LEFT JOIN classifications c
                   ON c.issue_id = i.id
                  AND c.model = ? AND c.prompt_version = ? AND c.temperature = ?
                  AND (c.status IS NULL OR c.status = 'ok')"""

ISSUE_COLUMNS = (
    "i.id, i.issue_number, i.title, i.body, i.state, i.state_reason, "
    "i.created_at, i.closed_at, i.raw_data"
)

RAW_DATA_KEYS = (
    "timestamp_metrics",
    "participant_metrics",
    "reopen_metrics",
    "author",
    "closed_by",
    "labels",
    "assignees",
    "milestone",
    "comments",
    "comments_md",
    "comments_text",
    "maintainer_comments",
    "closing_pr",
    "closing_commit",
)


def _window(query, params, start, end):
    if start:
        query += " AND i.created_at >= ?"
        params.append(start)
    if end:
        query += " AND i.created_at <= ?"
        params.append(end)
    return query, params


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._write_lock = threading.Lock()
        try:
            with self._write_lock:
                ensure_schema(self._connection)
        except BaseException:
            self._connection.close()
            raise
        self._readonly = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False
        )

    def close(self):
        self._readonly.close()
        self._connection.close()

    def _write(self, sql, params=()):
        with self._write_lock:
            cursor = self._connection.execute(sql, params)
            self._connection.commit()
            return cursor

    def _one(self, sql, params=()):
        return self._connection.execute(sql, params).fetchone()

    def _all(self, sql, params=()):
        return self._connection.execute(sql, params).fetchall()

    def save_project(self, owner, repo):
        self._write(
            "INSERT OR IGNORE INTO projects(owner, repo, added_at) VALUES(?,?,?)",
            (owner, repo, datetime.now().isoformat()),
        )
        return self.project_id(owner, repo)

    def project_id(self, owner, repo):
        row = self._one("SELECT id FROM projects WHERE owner = ? AND repo = ?", (owner, repo))
        return row[0] if row else None

    def save_issue(self, project_id, issue_data):
        raw_data = json.dumps({key: issue_data.get(key) for key in RAW_DATA_KEYS})
        self._write(
            "INSERT OR IGNORE INTO issues(project_id, issue_number, github_id, url, title, body, "
            "state, state_reason, locked, created_at, closed_at, updated_at, comments_count, "
            "raw_data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                issue_data.get("number"),
                issue_data.get("id"),
                issue_data.get("url"),
                issue_data.get("title"),
                issue_data.get("body"),
                issue_data.get("state"),
                issue_data.get("state_reason"),
                issue_data.get("locked"),
                issue_data.get("created_at"),
                issue_data.get("closed_at"),
                issue_data.get("updated_at"),
                issue_data.get("comments_count"),
                raw_data,
            ),
        )
        return self.issue_id(project_id, issue_data.get("number"))

    def issue_id(self, project_id, number):
        row = self._one(
            "SELECT id FROM issues WHERE project_id = ? AND issue_number = ?", (project_id, number)
        )
        return row[0] if row else None

    def summary_counts(self, model, prompt_version, temperature):
        projects = self._all(
            "SELECT p.owner, p.repo, COUNT(i.id), COUNT(i.version_id) FROM projects p "
            "LEFT JOIN issues i ON i.project_id = p.id GROUP BY p.id ORDER BY p.owner, p.repo",
            (),
        )
        labels = self._all(
            "SELECT p.owner, p.repo, c.classification, COUNT(*) FROM classifications c "
            "JOIN issues i ON i.id = c.issue_id JOIN projects p ON p.id = i.project_id "
            "WHERE c.model = ? AND c.prompt_version = ? AND c.temperature = ? "
            f"AND {SUCCEEDED} GROUP BY p.id, c.classification",
            (model, prompt_version, temperature),
        )
        return [tuple(row) for row in projects], [tuple(row) for row in labels]

    def overview(self):
        projects = self._one("SELECT COUNT(*) FROM projects")[0]
        issues = self._one("SELECT COUNT(*) FROM issues")[0]
        labelled = self._one(
            f"SELECT COUNT(DISTINCT c.issue_id) FROM classifications c WHERE {SUCCEEDED}"
        )[0]
        return projects, issues, labelled

    def issue_count(self, project_id):
        return self._one("SELECT COUNT(*) FROM issues WHERE project_id = ?", (project_id,))[0]

    def count_existing_issues(self, project_id, numbers):
        if not numbers:
            return 0
        placeholders = ",".join("?" * len(numbers))
        return self._one(
            f"SELECT COUNT(*) FROM issues WHERE project_id = ? AND issue_number IN ({placeholders})",
            (project_id, *numbers),
        )[0]

    def save_classification(
        self, issue_id, classification_data, model, prompt, temp, classified_at
    ):
        classification = classification_data.get("classification")
        status = classification_data.get("status", "ok")
        if status != "ok" and classification is not None:
            raise ValueError(
                f"refusing to store classification {classification!r} with status {status!r}"
            )
        flags = classification_data.get("validation_flags")
        stages = classification_data.get("stages")
        params = (
            issue_id,
            classification,
            json.dumps(classification_data.get("classification_probabilities")),
            classification_data.get("classification_raw_response"),
            model,
            prompt,
            temp,
            classified_at,
            status,
            classification_data.get("error"),
            json.dumps(flags) if flags else None,
            classification_data.get("argmax_label"),
            classification_data.get("attempts"),
            json.dumps(stages) if stages is not None else None,
        )
        with self._write_lock:
            self._connection.execute(
                """INSERT INTO classifications(
                    issue_id, classification, classification_probabilities,
                    classification_raw_response, model, prompt_version, temperature,
                    classified_at, status, error, validation_flags, argmax_label, attempts,
                    stages)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(issue_id, model, prompt_version, temperature) DO UPDATE SET
                    classification=excluded.classification,
                    classification_probabilities=excluded.classification_probabilities,
                    classification_raw_response=excluded.classification_raw_response,
                    classified_at=excluded.classified_at,
                    status=excluded.status,
                    error=excluded.error,
                    validation_flags=excluded.validation_flags,
                    argmax_label=excluded.argmax_label,
                    attempts=excluded.attempts,
                    stages=excluded.stages""",
                params,
            )
            self._connection.commit()
            return self._one(
                "SELECT id FROM classifications "
                "WHERE issue_id=? AND model=? AND prompt_version=? AND temperature=?",
                (issue_id, model, prompt, temp),
            )[0]

    def has_classification(self, issue_id, model, prompt, temp):
        row = self._one(
            "SELECT 1 FROM classifications c WHERE c.issue_id = ? AND c.model = ? "
            f"AND c.prompt_version = ? AND c.temperature = ? AND {SUCCEEDED}",
            (issue_id, model, prompt, temp),
        )
        return row is not None

    def set_package_name(self, project_id, package_name):
        self._write("UPDATE projects SET package_name = ? WHERE id = ?", (package_name, project_id))

    def package_name(self, project_id):
        row = self._one("SELECT package_name FROM projects WHERE id = ?", (project_id,))
        return row[0] if row else None

    def project_has_classifications(self, project_id):
        row = self._one(
            "SELECT 1 FROM classifications c INNER JOIN issues i ON i.id = c.issue_id "
            f"WHERE i.project_id = ? AND {SUCCEEDED} LIMIT 1",
            (project_id,),
        )
        return row is not None

    def classified_issue_window(self, project_id, start=None, end=None):
        query, params = _window(
            "SELECT DISTINCT i.id, i.issue_number, i.created_at, i.version_id FROM issues i "
            "INNER JOIN classifications c ON c.issue_id = i.id "
            f"WHERE i.project_id = ? AND {SUCCEEDED}",
            [project_id],
            start,
            end,
        )
        return self._all(query + " ORDER BY i.created_at", params)

    def version_id(self, project_id, version):
        row = self._one(
            "SELECT id FROM versions WHERE project_id = ? AND version = ?", (project_id, version)
        )
        return row[0] if row else None

    def save_version(self, project_id, package_name, version, published_at, counts, raw_manifest):
        self._write(
            "INSERT OR IGNORE INTO versions(project_id, package_name, version, published_at, "
            "direct_count, peer_count, dev_count, raw_manifest, snapshotted_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                project_id,
                package_name,
                version,
                published_at,
                counts.get("direct"),
                counts.get("peer"),
                counts.get("dev"),
                json.dumps(raw_manifest) if raw_manifest else None,
                datetime.now().isoformat(),
            ),
        )
        return self.version_id(project_id, version)

    def save_dependencies(self, version_id, rows):
        with self._write_lock:
            self._connection.executemany(
                "INSERT INTO version_dependencies(version_id, dep_name, dep_kind, "
                "dep_version_range, resolved_version, depth, root_dep) VALUES (?,?,?,?,?,?,?)",
                [(version_id, *row) for row in rows],
            )
            self._connection.commit()

    def link_issue_to_version(self, issue_id, version_id):
        self._write("UPDATE issues SET version_id = ? WHERE id = ?", (version_id, issue_id))

    def unclassified_by_ids(self, issue_ids, model, prompt_version, temperature):
        if not issue_ids:
            return []
        placeholders = ",".join("?" * len(issue_ids))
        return self._all(
            f"SELECT {ISSUE_COLUMNS} FROM issues i {DONE_JOIN} "
            f"WHERE i.id IN ({placeholders}) AND c.id IS NULL",
            [model, prompt_version, temperature, *issue_ids],
        )

    def count_unclassified(
        self, project_id, model, prompt_version, temperature, start=None, end=None
    ):
        query, params = _window(
            f"SELECT COUNT(*) FROM issues i {DONE_JOIN} WHERE i.project_id = ? AND c.id IS NULL",
            [model, prompt_version, temperature, project_id],
            start,
            end,
        )
        return self._one(query, params)[0]

    def unclassified_in_window(
        self,
        project_id,
        model,
        prompt_version,
        temperature,
        start=None,
        end=None,
        direction="desc",
        limit=None,
    ):
        query, params = _window(
            f"SELECT {ISSUE_COLUMNS} FROM issues i {DONE_JOIN} "
            "WHERE i.project_id = ? AND c.id IS NULL",
            [model, prompt_version, temperature, project_id],
            start,
            end,
        )
        order = "ASC" if str(direction).lower() == "asc" else "DESC"
        query += f" ORDER BY i.created_at {order}"
        if limit:
            query += " LIMIT ?"
            params.append(int(limit))
        return self._all(query, params)

    def run_readonly(self, sql, limit):
        try:
            cursor = self._readonly.execute(sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return columns, cursor.fetchmany(limit)
        except sqlite3.Error as error:
            raise InexError("sql_error", str(error)) from error

    def fetch_readonly(self, sql):
        try:
            cursor = self._readonly.execute(sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return columns, cursor.fetchall()
        except sqlite3.Error as error:
            raise InexError("sql_error", str(error)) from error

    def reset(self):
        with self._write_lock:
            for table in TABLE_NAMES:
                self._connection.execute(f"DROP TABLE IF EXISTS {table}")
            self._connection.commit()
            ensure_schema(self._connection)

    def clear(self):
        with self._write_lock:
            for table in TABLE_NAMES:
                self._connection.execute(f"DELETE FROM {table}")
            self._connection.execute("DELETE FROM sqlite_sequence")
            self._connection.commit()
