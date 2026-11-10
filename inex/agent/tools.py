import logging
from dataclasses import asdict
from typing import Annotated

from langchain.tools import tool
from pydantic import BeforeValidator

from inex.errors import InexError
from inex.validators import (
    parse_count,
    parse_end,
    parse_filename,
    parse_format,
    parse_order,
    parse_repo,
    parse_start,
)

logger = logging.getLogger(__name__)

TOOL_NAMES = (
    "count_issues",
    "collect_all",
    "classify_all",
    "preview_classification",
    "snapshot_dependencies",
    "run_sql",
    "export_data",
    "preview_issue_list",
    "import_issue_list",
)

INTERNAL_ERROR_MESSAGE = "Unexpected failure; see the log for details."

COUNT_ISSUES = (
    "Count the total number of issues in a GitHub repo without collecting them. Use this before "
    "collect_all to check if the repo is large. Input: repo_name as 'owner/repo'. Returns the "
    "exact count (issue_count) and how many of the repo's issues are already in the database "
    "(stored)."
)
COLLECT_ALL = (
    "Collect issues from a GitHub repo and save them to the database. Input: repo_name as "
    "'owner/repo'. Optional: count (how many NEW issues to collect; issues already in the "
    "database are skipped and do not count toward it), start_date and end_date (ISO "
    "'YYYY-MM-DD', filter by issue creation date), direction ('desc' for newest-first, 'asc' for "
    "oldest-first; default desc). When the user asks for a total ('1000 in total', 'until there "
    "are 1000'), call count_issues first and pass the total minus stored as count; if stored "
    "already reaches the total, collect nothing and say so. Returns collected, skipped (already "
    "stored) and total (issues stored for the repo afterwards). Long-running: multiple API "
    "calls per issue."
)
CLASSIFY_ALL = (
    "Classify unclassified issues for a repo already in the database. SLOW - each issue is an "
    "LLM call. Input: repo_name as 'owner/repo'. Optional: count (cap how many to classify in "
    "this run), start_date and end_date (ISO 'YYYY-MM-DD', filter by issue creation date), "
    "direction ('desc'/'asc'). 'Unclassified' means not yet classified BY THE CURRENT model, "
    "prompt and temperature, so changing any of those makes previously-done issues eligible "
    "again - preview_classification reports the true backlog as total_remaining, which can be "
    "very large. Failed attempts are retried automatically on a later run. Call "
    "preview_classification first, show the user total_remaining, and get explicit "
    "confirmation before calling this."
)
PREVIEW_CLASSIFICATION = (
    "Preview what classify_all would classify, WITHOUT classifying anything. Returns "
    "would_classify (capped by count), total_remaining (the FULL backlog, ignoring count), the "
    "model and prompt_version being previewed for, the date span, and a small sample. Always "
    "show the user total_remaining before classify_all - it can be tens of thousands. Input: "
    "repo_name as 'owner/repo'. Optional: count, start_date, end_date (ISO 'YYYY-MM-DD'), "
    "direction ('desc'/'asc')."
)
SNAPSHOT_DEPENDENCIES = (
    "Fetch npm dependency data for a project and save it to the database. This tool does "
    "everything in one step: it contacts the npm registry, finds the package version that was "
    "live when each classified issue was filed, saves that version and its direct/peer/dev "
    "dependencies to the database, and links each issue to its version. You do NOT need "
    "versions in the database beforehand; this tool creates them. Requires classifications to "
    "exist. Input: repo_name as 'owner/repo' and npm_package. DO NOT pass last_n_versions, "
    "version_start, or version_end unless the user explicitly asks for extra version history; "
    "by default only snapshot the versions that issues map to."
)
RUN_SQL = (
    "Run a READ-ONLY SQL SELECT against the database. For analysis questions not covered by "
    "other tools. Only SELECT allowed; writes are rejected. Use the schema in your instructions "
    "for table and column names. Results capped at {max_rows} rows; prefer COUNT/AVG/GROUP BY "
    "for large tables."
)
EXPORT_DATA = (
    "Export the results of a SQL SELECT query to a file. Use when the user wants to save data "
    "for external analysis. query: a valid SELECT statement. format: 'csv' or 'json' (default "
    "'csv'). filename: optional plain file name (no directories), auto-generated if omitted. No "
    "row limit; exports all matching rows. Files are written to the exports/ directory. Use the "
    "schema in your instructions for table and column names."
)
PREVIEW_ISSUE_LIST = (
    "Preview a CSV file of specific issues to import, WITHOUT collecting or classifying "
    "anything. The CSV must have a column of 'owner/repo#number' references (e.g. "
    "'astropy/astropy#12906'). Returns the total parsed, a per-repo breakdown, how many are "
    "already in the database, and any rows that could not be parsed. Read-only and fast. Call "
    "before import_issue_list and show the user to confirm. Input: csv_path. Optional: column "
    "(the column name; defaults to the first column)."
)
IMPORT_ISSUE_LIST = (
    "Collect and classify a specific list of GitHub issues from a CSV file. The CSV must have a "
    "column of 'owner/repo#number' references. For each issue: fetches it from GitHub (skipping "
    "ones already in the database), then classifies it. SLOW: one or more GitHub API calls plus "
    "one LLM call per issue. Call preview_issue_list first and confirm with the user before "
    "calling this. Input: csv_path. Optional: column (defaults to the first column); classify "
    "(default true; set false to only collect)."
)


NULL_WORDS = frozenset({"null", "none", ""})


def omitted(value):
    if isinstance(value, str) and value.strip().lower() in NULL_WORDS:
        return None
    return value


def or_default(default):
    def apply(value):
        value = omitted(value)
        return default if value is None else value

    return apply


OptionalInt = Annotated[int | None, BeforeValidator(omitted)]
OptionalText = Annotated[str | None, BeforeValidator(omitted)]
Direction = Annotated[str, BeforeValidator(or_default("desc"))]
ExportFormat = Annotated[str, BeforeValidator(or_default("csv"))]
Classify = Annotated[bool, BeforeValidator(or_default(True))]


def respond(call):
    try:
        result = call()
    except InexError as error:
        return {"status": "error", "code": error.code, "message": error.message}
    except Exception:
        logger.exception("Agent tool call failed")
        return {"status": "error", "code": "internal_error", "message": INTERNAL_ERROR_MESSAGE}
    return {"status": "ok", **asdict(result)}


def _window(count, start_date, end_date, direction):
    return {
        "count": parse_count(count),
        "start": parse_start(start_date),
        "end": parse_end(end_date),
        "order": parse_order(direction),
    }


def build_tools(app) -> list:
    @tool("count_issues", description=COUNT_ISSUES)
    def count_issues(repo_name: str) -> dict:
        return respond(lambda: app.collection.count(*parse_repo(repo_name)))

    @tool("collect_all", description=COLLECT_ALL)
    def collect_all(
        repo_name: str,
        count: OptionalInt = None,
        start_date: OptionalText = None,
        end_date: OptionalText = None,
        direction: Direction = "desc",
    ) -> dict:
        def call():
            owner, repo = parse_repo(repo_name)
            return app.collection.collect(
                owner, repo, **_window(count, start_date, end_date, direction)
            )

        return respond(call)

    @tool("classify_all", description=CLASSIFY_ALL)
    def classify_all(
        repo_name: str,
        count: OptionalInt = None,
        start_date: OptionalText = None,
        end_date: OptionalText = None,
        direction: Direction = "desc",
    ) -> dict:
        def call():
            owner, repo = parse_repo(repo_name)
            return app.classification.classify(
                owner, repo, **_window(count, start_date, end_date, direction)
            )

        return respond(call)

    @tool("preview_classification", description=PREVIEW_CLASSIFICATION)
    def preview_classification(
        repo_name: str,
        count: OptionalInt = None,
        start_date: OptionalText = None,
        end_date: OptionalText = None,
        direction: Direction = "desc",
    ) -> dict:
        def call():
            owner, repo = parse_repo(repo_name)
            return app.classification.preview(
                owner, repo, **_window(count, start_date, end_date, direction)
            )

        return respond(call)

    @tool("snapshot_dependencies", description=SNAPSHOT_DEPENDENCIES)
    def snapshot_dependencies(
        repo_name: str,
        npm_package: str,
        start_date: OptionalText = None,
        end_date: OptionalText = None,
        version_range: OptionalText = None,
        last_n_versions: OptionalInt = None,
        version_start: OptionalText = None,
        version_end: OptionalText = None,
    ) -> dict:
        def call():
            owner, repo = parse_repo(repo_name)
            return app.linkage.link(
                owner,
                repo,
                npm_package,
                start=parse_start(start_date),
                end=parse_end(end_date),
                version_range=version_range,
                last_n_versions=parse_count(last_n_versions),
                version_start=parse_start(version_start),
                version_end=parse_end(version_end),
            )

        return respond(call)

    @tool("run_sql", description=RUN_SQL.format(max_rows=app.queries.max_rows))
    def run_sql(query: str) -> dict:
        return respond(lambda: app.queries.run(query))

    @tool("export_data", description=EXPORT_DATA)
    def export_data(
        query: str, format: ExportFormat = "csv", filename: OptionalText = None
    ) -> dict:
        return respond(
            lambda: app.queries.export(query, parse_format(format), parse_filename(filename))
        )

    @tool("preview_issue_list", description=PREVIEW_ISSUE_LIST)
    def preview_issue_list(csv_path: str, column: OptionalText = None) -> dict:
        return respond(lambda: app.imports.preview_list(csv_path, column))

    @tool("import_issue_list", description=IMPORT_ISSUE_LIST)
    def import_issue_list(
        csv_path: str, column: OptionalText = None, classify: Classify = True
    ) -> dict:
        return respond(lambda: app.imports.import_list(csv_path, column, classify))

    return [
        count_issues,
        collect_all,
        classify_all,
        preview_classification,
        snapshot_dependencies,
        run_sql,
        export_data,
        preview_issue_list,
        import_issue_list,
    ]
