import json

STEP_PREFIX = "  > "
RESULT_PREFIX = "    "
POSITIONAL = ("repo_name", "csv_path", "query")
MAX_ARGUMENT_CHARS = 60
ELLIPSIS = "..."
DONE = "done"
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


def _failed(result):
    return sum(result["failed"].values()) if isinstance(result["failed"], dict) else 0


def _rows(result):
    more = " (more not shown)" if result.get("truncated") else ""
    return f"{len(result['rows']):,} rows{more}"


SUMMARIES = {
    "count_issues": lambda r: f"{r['issue_count']:,} issues on GitHub, {r['stored']:,} stored",
    "collect_all": lambda r: (
        f"{r['collected']:,} collected, {r['skipped']:,} already stored, {r['total']:,} in total"
    ),
    "preview_classification": lambda r: (
        f"{r['would_classify']:,} of {r['total_remaining']:,} unclassified would be classified "
        f"with {r['model']}"
    ),
    "classify_all": lambda r: f"{r['classified']:,} classified, {_failed(r):,} failed",
    "snapshot_dependencies": lambda r: (
        f"{r['linked_issues']:,} issues linked to {r['package_name']} releases, "
        f"{r['issue_versions_snapshotted']:,} versions saved"
    ),
    "run_sql": _rows,
    "export_data": lambda r: f"{r['rows_exported']:,} rows written to {r['path']}",
    "preview_issue_list": lambda r: (
        f"{r['total_issues']:,} issues across {r['distinct_repos']:,} repositories, "
        f"{r['already_in_db']:,} already stored, {r['to_collect']:,} to collect"
    ),
    "import_issue_list": lambda r: (
        f"{r['collected']:,} collected, {r['skipped']:,} already stored, "
        f"{r['failed']:,} failed, {r['classified']:,} classified"
    ),
}


def _argument(value):
    text = " ".join(str(value).split())
    if len(text) <= MAX_ARGUMENT_CHARS:
        return text
    return text[: MAX_ARGUMENT_CHARS - len(ELLIPSIS)] + ELLIPSIS


def call_line(name, args):
    shown = [_argument(args[key]) for key in POSITIONAL if args.get(key) is not None]
    shown += [
        f"{key}={_argument(value)}"
        for key, value in args.items()
        if key not in POSITIONAL and value is not None
    ]
    return " ".join([f"{STEP_PREFIX}{name}", *shown])


def tool_result(content):
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except ValueError:
        return content


def result_summary(name, result):
    if not isinstance(result, dict):
        return _argument(result)
    if result.get("status") == "error":
        return f"error {result.get('code')}: {result.get('message')}"
    summary = SUMMARIES.get(name)
    if summary is None:
        return DONE
    try:
        return summary(result)
    except (KeyError, TypeError, ValueError):
        return DONE


def duration(seconds):
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds:.1f}s"
    whole = int(seconds)
    if whole < SECONDS_PER_HOUR:
        return f"{whole // SECONDS_PER_MINUTE}m {whole % SECONDS_PER_MINUTE:02d}s"
    minutes = whole % SECONDS_PER_HOUR // SECONDS_PER_MINUTE
    return f"{whole // SECONDS_PER_HOUR}h {minutes:02d}m"


def result_line(name, result, seconds):
    return f"{RESULT_PREFIX}{result_summary(name, result)} ({duration(seconds)})"
