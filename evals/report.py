import csv
import json
import statistics
from pathlib import Path

from evals.scenarios import CATEGORIES

SUMMARY_COLUMNS = (
    "model",
    "category",
    "scenarios",
    "runs",
    "passed",
    "failed",
    "errored",
    "pass_rate",
    "pass_hat_k",
    "rejected_calls",
    "input_tokens",
    "output_tokens",
    "cost",
    "median_seconds",
)
OVERALL = "overall"
MISSING = "--"
LATEX_SPECIALS = {
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
}


def _key(record):
    return (record["model"], record["scenario"], record["run"])


def latest(records) -> list[dict]:
    newest = {}
    for record in records:
        newest[_key(record)] = record
    return list(newest.values())


def _rate(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def _pass_hat_k(records, runs):
    by_scenario = {}
    for record in records:
        by_scenario.setdefault(record["scenario"], []).append(record["outcome"])
    eligible = [outcomes for outcomes in by_scenario.values() if len(outcomes) >= runs]
    clean = sum(1 for outcomes in eligible if all(o == "passed" for o in outcomes))
    return _rate(clean, len(eligible))


def _row(model, category, records, runs):
    outcomes = [r["outcome"] for r in records]
    passed = outcomes.count("passed")
    failed = outcomes.count("failed")
    costs = [r["cost"] for r in records if r.get("cost") is not None]
    return {
        "model": model,
        "category": category,
        "scenarios": len({r["scenario"] for r in records}),
        "runs": len(records),
        "passed": passed,
        "failed": failed,
        "errored": outcomes.count("errored"),
        "pass_rate": _rate(passed, passed + failed),
        "pass_hat_k": _pass_hat_k(records, runs),
        "rejected_calls": sum(r.get("rejected_calls") or 0 for r in records),
        "input_tokens": sum(r.get("input_tokens") or 0 for r in records),
        "output_tokens": sum(r.get("output_tokens") or 0 for r in records),
        "cost": round(sum(costs), 6) if costs else None,
        "median_seconds": round(statistics.median(r["seconds"] for r in records), 2),
    }


def summarize(records, models, runs) -> list[dict]:
    current = latest(records)
    rows = []
    for model in models:
        mine = [r for r in current if r["model"] == model]
        if not mine:
            continue
        for category in CATEGORIES:
            group = [r for r in mine if r["category"] == category]
            if group:
                rows.append(_row(model, category, group, runs))
        rows.append(_row(model, OVERALL, mine, runs))
    return rows


def latex_escape(text) -> str:
    return "".join(LATEX_SPECIALS.get(char, char) for char in str(text))


def _percent(rate):
    return MISSING if rate is None else str(int(rate * 100 + 0.5))


def table_tex(rows, models, runs) -> str:
    by_key = {(r["model"], r["category"]): r for r in rows}
    headers = [c.capitalize() for c in CATEGORIES]
    header = " & ".join(["Model", *headers, "Overall", f"pass\\textsuperscript{{{runs}}}", "Cost"])
    lines = [
        "\\begin{tabular}{l" + "r" * (len(CATEGORIES) + 3) + "}",
        "\\toprule",
        header + " \\\\",
        "\\midrule",
    ]
    for model in models:
        overall = by_key.get((model, OVERALL))
        if overall is None:
            continue
        cells = [latex_escape(model)]
        for category in CATEGORIES:
            row = by_key.get((model, category))
            cells.append(_percent(row["pass_rate"] if row else None))
        cells.append(_percent(overall["pass_rate"]))
        cells.append(_percent(overall["pass_hat_k"]))
        cost = overall["cost"]
        cells.append(MISSING if cost is None else f"\\${cost:.2f}")
        lines.append(" & ".join(cells) + " \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def write_reports(folder, manifest, records) -> None:
    folder = Path(folder)
    rows = summarize(records, manifest["models"], manifest["runs"])
    with open(folder / "summary.csv", "w", encoding="utf-8", newline="\n") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with open(folder / "summary.json", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(rows, indent=2) + "\n")
    with open(folder / "table.tex", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(table_tex(rows, manifest["models"], manifest["runs"]))
