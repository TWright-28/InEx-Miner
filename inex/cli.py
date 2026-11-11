import argparse
import importlib
import json
import logging
import logging.config
import re
import sys
import warnings
from dataclasses import asdict
from pathlib import Path

from tqdm import tqdm

from inex.app import build_app
from inex.config import load_settings
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

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130
STDOUT_STREAM = "ext://sys.stdout"
STDERR_STREAM = "ext://sys.stderr"
CHAT_CONSOLE_LEVEL = logging.WARNING
PROGRESS_LOGGER = "inex.progress"
BAR_FORMAT = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} issues [{elapsed}<{remaining}]"
COUNT_FORMAT = "{desc}: {n_fmt} issues [{elapsed}]"
SUMMARY_COLUMNS = ("issues", "labelled", "linked")
COLUMN_GAP = "  "
KNOWN_WARNINGS = (
    "Core Pydantic V1 functionality isn't compatible with Python 3.14",
    "The default value of `allowed_objects` will change",
)
OUTPUT_ENCODING = "utf-8"


def configure_logging(path):
    path = Path(path)
    if not path.is_file():
        return None
    config = json.loads(path.read_text(encoding="utf-8"))
    log_file = None
    for handler in config.get("handlers", {}).values():
        if handler.get("stream") == STDOUT_STREAM:
            handler["stream"] = STDERR_STREAM
        filename = handler.get("filename")
        if filename:
            handler.setdefault("encoding", OUTPUT_ENCODING)
            Path(filename).parent.mkdir(parents=True, exist_ok=True)
            log_file = log_file or filename
    logging.config.dictConfig(config)
    return log_file


def _window(args):
    return {
        "count": parse_count(args.count),
        "start": parse_start(args.since),
        "end": parse_end(args.until),
        "order": parse_order("asc" if args.oldest_first else "desc"),
    }


def _closed_word(closed):
    return "closed " if closed else ""


def _count(app, args):
    result = app.collection.count(*parse_repo(args.repo), closed=args.closed)
    kind = _closed_word(args.closed)
    return result, f"{result.owner}/{result.repo} has {result.issue_count} {kind}issues."


def _collect(app, args):
    owner, repo = parse_repo(args.repo)
    result = app.collection.collect(owner, repo, **_window(args), closed=args.closed)
    return result, (
        f"Collected {result.collected} {_closed_word(args.closed)}issues from {owner}/{repo} "
        f"({result.skipped} already in the database, {result.total} in total)."
    )


def _classify(app, args):
    owner, repo = parse_repo(args.repo)
    if args.preview:
        p = app.classification.preview(owner, repo, **_window(args))
        summary = (
            f"{p.would_classify} of {p.total_remaining} unclassified issues in {owner}/{repo} "
            f"would be classified with {p.model} ({p.prompt_version})."
        )
        if p.judge:
            summary += f" Recover judge: {p.judge}."
        return p, summary
    result = app.classification.classify(owner, repo, **_window(args))
    summary = f"Classified {result.classified} issues from {owner}/{repo}."
    if result.failed:
        total = sum(result.failed.values())
        breakdown = ", ".join(f"{status}={n}" for status, n in sorted(result.failed.items()))
        summary += f" {total} failed and were not labelled ({breakdown}); re-run to retry them."
    return result, summary


def _link(app, args):
    owner, repo = parse_repo(args.repo)
    result = app.linkage.link(
        owner,
        repo,
        args.package,
        start=parse_start(args.since),
        end=parse_end(args.until),
        version_range=args.version_range,
        last_n_versions=parse_count(args.last_versions),
        version_start=parse_start(args.versions_from),
        version_end=parse_end(args.versions_to),
    )
    return result, (
        f"Linked {result.linked_issues} issues in {owner}/{repo} to {result.package_name} "
        f"releases; {result.unlinkable_pre_release} predate the first release."
    )


def _import(app, args):
    if args.preview:
        p = app.imports.preview_list(args.csv_path, args.column)
        return p, (
            f"{p.total_issues} issues across {p.distinct_repos} repositories: "
            f"{p.already_in_db} already in the database, {p.to_collect} to collect, "
            f"{p.unparseable_count} unreadable rows."
        )
    r = app.imports.import_list(args.csv_path, args.column, classify=not args.collect_only)
    return r, (
        f"Collected {r.collected}, skipped {r.skipped}, failed {r.failed}, "
        f"classified {r.classified}."
    )


def _load(app, args):
    temperature = app.settings.temperature if args.temperature is None else args.temperature
    r = app.imports.load_jsonl(args.path, args.model, args.prompt_version, temperature)
    return r, (
        f"Loaded {r.issues} issues and {r.classifications} classifications from {r.files} files."
    )


def _query(app, args):
    result = app.queries.run(args.sql)
    lines = ["\t".join(result.columns)]
    lines += ["\t".join("" if v is None else str(v) for v in row) for row in result.rows]
    if result.truncated:
        lines.append(f"(truncated at {app.queries.max_rows} rows)")
    return result, "\n".join(lines)


def _export(app, args):
    result = app.queries.export(args.sql, parse_format(args.format), parse_filename(args.name))
    return result, f"Exported {result.rows_exported} rows to {result.path}."


def _table(rows):
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return [
        COLUMN_GAP.join(
            [
                row[0].ljust(widths[0]),
                *(cell.rjust(width) for cell, width in zip(row[1:], widths[1:], strict=True)),
            ]
        )
        for row in rows
    ]


def _summary(app, args):
    result = app.classification.summary()
    if not result.projects:
        return result, "No projects in the database yet."
    names = result.labels

    def cells(issues, labelled, linked, labels):
        return [f"{n:,}" for n in (issues, labelled, linked, *(labels[name] for name in names))]

    projects = result.projects
    rows = [["project", *SUMMARY_COLUMNS, *names]]
    rows += [
        [f"{p.owner}/{p.repo}", *cells(p.issues, p.labelled, p.linked, p.labels)] for p in projects
    ]
    totals = {name: sum(p.labels[name] for p in projects) for name in names}
    rows.append(
        [
            "total",
            *cells(
                sum(p.issues for p in projects),
                sum(p.labelled for p in projects),
                sum(p.linked for p in projects),
                totals,
            ),
        ]
    )
    heading = (
        f"Labels by {result.model} ({result.prompt_version}, temperature {result.temperature})"
    )
    return result, "\n".join([heading, "", *_table(rows)])


class ProgressBars(logging.Handler):
    def __init__(self, stream=None, enabled=None):
        super().__init__(logging.DEBUG)
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled
        self.task = None
        self.bar = None

    def emit(self, record):
        task = getattr(record, "task", None)
        if task is None:
            return
        if task != self.task:
            self.close_bar()
            self.task = task
            self.bar = tqdm(
                desc=task,
                total=record.total,
                bar_format=COUNT_FORMAT if record.total is None else BAR_FORMAT,
                file=self.stream,
                disable=None if self.enabled is None else not self.enabled,
                dynamic_ncols=True,
                leave=True,
            )
        self.bar.n = record.done
        self.bar.refresh()
        if record.finished:
            self.close_bar()

    def close_bar(self):
        if self.bar is not None:
            self.bar.close()
        self.bar = None
        self.task = None

    def close(self):
        self.close_bar()
        super().close()


def quiet_console():
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.setLevel(CHAT_CONSOLE_LEVEL)
    progress = logging.getLogger(PROGRESS_LOGGER)
    progress.setLevel(logging.DEBUG)
    if not any(isinstance(handler, ProgressBars) for handler in progress.handlers):
        progress.addHandler(ProgressBars())


def _chat(app, args):
    chat = import_quietly("inex.agent.chat")
    chat.run_chat(app)
    return None, None


DB_MESSAGES = {
    "init": "Database ready at {path}.",
    "reset": "Database reset at {path}.",
    "clear": "Database cleared at {path}.",
}


def _db(app, args):
    result = getattr(app.maintenance, args.action)()
    return result, DB_MESSAGES[args.action].format(path=result.path)


def _add_repo_window(parser):
    parser.add_argument("repo", metavar="OWNER/REPO")
    parser.add_argument("--count", type=int)
    parser.add_argument("--since", metavar="DATE")
    parser.add_argument("--until", metavar="DATE")
    parser.add_argument("--oldest-first", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m inex",
        description="Collect, classify and link GitHub issues from npm projects.",
    )
    parser.add_argument("--db", type=Path, help="SQLite database to use")
    parser.add_argument("--classifier", help="classifier model, e.g. qwen3:30b")
    parser.add_argument("--judge", help="Recover judge model for staged profiles")
    parser.add_argument("--profile", type=Path, help="classification profile YAML")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    count = commands.add_parser("count", help="count a repository's issues on GitHub")
    count.add_argument("repo", metavar="OWNER/REPO")
    count.add_argument("--closed", action="store_true", help="count closed issues only")
    count.set_defaults(handler=_count)

    collect = commands.add_parser("collect", help="collect issues into the database")
    _add_repo_window(collect)
    collect.add_argument("--closed", action="store_true", help="collect closed issues only")
    collect.set_defaults(handler=_collect)

    classify = commands.add_parser("classify", help="classify collected issues")
    _add_repo_window(classify)
    classify.add_argument("--preview", action="store_true")
    classify.set_defaults(handler=_classify)

    link = commands.add_parser("link", help="link issues to the npm release live when filed")
    link.add_argument("repo", metavar="OWNER/REPO")
    link.add_argument("--package", required=True)
    link.add_argument("--since", metavar="DATE")
    link.add_argument("--until", metavar="DATE")
    link.add_argument("--version-range")
    link.add_argument("--last-versions", type=int)
    link.add_argument("--versions-from", metavar="DATE")
    link.add_argument("--versions-to", metavar="DATE")
    link.set_defaults(handler=_link)

    issue_list = commands.add_parser("import", help="collect and classify a CSV list of issues")
    issue_list.add_argument("csv_path", metavar="LIST.csv")
    issue_list.add_argument("--column")
    issue_list.add_argument("--collect-only", action="store_true")
    issue_list.add_argument("--preview", action="store_true")
    issue_list.set_defaults(handler=_import)

    load = commands.add_parser("load", help="load issues and labels from JSONL files")
    load.add_argument("path", metavar="FILE_OR_DIR")
    load.add_argument("--model", required=True)
    load.add_argument("--prompt-version", required=True)
    load.add_argument("--temperature", type=float)
    load.set_defaults(handler=_load)

    query = commands.add_parser("query", help="run a read-only SELECT")
    query.add_argument("sql", metavar="SQL")
    query.set_defaults(handler=_query)

    export = commands.add_parser("export", help="export a SELECT to CSV or JSON")
    export.add_argument("sql", metavar="SQL")
    export.add_argument("--format", default="csv")
    export.add_argument("--name")
    export.set_defaults(handler=_export)

    summary = commands.add_parser("summary", help="count issues, labels and links per project")
    summary.set_defaults(handler=_summary)

    chat = commands.add_parser("chat", help="talk to the agent")
    chat.set_defaults(handler=_chat)

    db = commands.add_parser("db", help="create, reset or clear the database")
    actions = db.add_subparsers(dest="action", required=True, metavar="ACTION")
    actions.add_parser("init", help="create the schema")
    for action in ("reset", "clear"):
        sub = actions.add_parser(action, help=f"{action} every table")
        sub.add_argument("--yes", action="store_true", required=True)
    db.set_defaults(handler=_db)
    return parser


def _report_error(args, code, message):
    if args.json:
        print(json.dumps({"status": "error", "code": code, "message": message}))
    else:
        print(f"error: {message}", file=sys.stderr)


def _use_utf8_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding=OUTPUT_ENCODING, errors="replace")


def quiet_known_warnings():
    for message in KNOWN_WARNINGS:
        warnings.filterwarnings("ignore", message=re.escape(message))


def import_quietly(name):
    with warnings.catch_warnings(record=True) as caught:
        module = importlib.import_module(name)
    for warning in caught:
        if not str(warning.message).startswith(KNOWN_WARNINGS):
            warnings.showwarning(
                warning.message, warning.category, warning.filename, warning.lineno
            )
    return module


def main(argv=None, build=build_app) -> int:
    _use_utf8_output()
    quiet_known_warnings()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return exit_.code
    log_file = None
    app = None
    try:
        settings = load_settings(
            database=args.db, classifier=args.classifier, judge=args.judge, profile=args.profile
        )
        log_file = configure_logging(settings.logging_config)
        quiet_console()
        app = build(settings)
        result, summary = args.handler(app, args)
    except InexError as error:
        _report_error(args, error.code, error.message)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception:
        logger.exception("Command %s failed", args.command)
        hint = f"see {log_file}" if log_file else "see the log for details"
        _report_error(args, "internal_error", f"unexpected failure; {hint}.")
        return EXIT_ERROR
    finally:
        if app is not None:
            app.close()
    if result is not None:
        if args.json:
            print(json.dumps({"status": "ok", **asdict(result)}, default=str))
        else:
            print(summary)
    return EXIT_OK
