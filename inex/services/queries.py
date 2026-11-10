import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from inex.errors import InexError

MAX_QUERY_ROWS = 50
FORMATS = ("csv", "json")


@dataclass(frozen=True)
class DatabaseOverview:
    projects: int
    issues: int
    labelled: int


@dataclass(frozen=True)
class QueryResult:
    columns: list
    rows: list
    truncated: bool


@dataclass(frozen=True)
class ExportResult:
    path: str
    rows_exported: int
    columns: list
    format: str


def _checked_select(sql):
    query = sql.strip().rstrip(";").strip()
    lowered = query.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        raise InexError("not_a_select", "Only SELECT queries are allowed.")
    if ";" in query:
        raise InexError("multiple_statements", "Only one statement is allowed.")
    return query


class QueryService:
    def __init__(self, store, exports_dir: Path):
        self.store = store
        self.exports_dir = Path(exports_dir)
        self.max_rows = MAX_QUERY_ROWS

    def overview(self) -> DatabaseOverview:
        return DatabaseOverview(*self.store.overview())

    def run(self, sql) -> QueryResult:
        columns, rows = self.store.run_readonly(_checked_select(sql), self.max_rows + 1)
        return QueryResult(
            columns=columns,
            rows=[list(row) for row in rows[: self.max_rows]],
            truncated=len(rows) > self.max_rows,
        )

    def export(self, sql, fmt="csv", filename=None) -> ExportResult:
        query = _checked_select(sql)
        if fmt not in FORMATS:
            raise InexError("invalid_format", "Format must be 'csv' or 'json'.")
        if filename and (
            os.path.basename(filename) != filename or "/" in filename or "\\" in filename
        ):
            raise InexError(
                "invalid_filename",
                "File name must be a plain name; exports are always written to the exports folder.",
            )
        columns, rows = self.store.fetch_readonly(query)
        if not filename:
            filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}"
        elif not filename.endswith(f".{fmt}"):
            filename = f"{filename}.{fmt}"
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        path = self.exports_dir / filename
        if fmt == "csv":
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                writer.writerows(rows)
        else:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([dict(zip(columns, row, strict=True)) for row in rows], handle, indent=2)
        return ExportResult(path=str(path), rows_exported=len(rows), columns=columns, format=fmt)
