import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import requests

from evals.models import (
    cost_of,
    estimate,
    fetch_prices,
    is_ollama,
    is_openrouter,
    model_price,
    preflight,
    reproject,
)
from evals.report import write_reports
from evals.results import MANIFEST, Results, git_state, resume_problems, text_hash
from evals.runner import agent_prompt, run_scenario
from evals.scenarios import ScenarioError, WorldSpec, load_scenarios, select
from evals.tracing import make_tracer
from evals.world import build_world
from inex.agent.chat import build_llm
from inex.agent.tools import TOOL_NAMES
from inex.config import REPO_ROOT, load_settings
from inex.errors import InexError

EVALS_DIR = Path(__file__).resolve().parent
SCENARIOS_PATH = EVALS_DIR / "scenarios.yaml"
RESULTS_ROOT = EVALS_DIR / "results"
DEFAULT_RUNS = 3
DEFAULT_MAX_COST = 1.00
DEFAULT_NUM_CTX = 16384
UNPRICED = "not priced; --max-cost does not apply"
COMPLETE = "complete"
STOPPED = "stopped: budget"
INTERRUPTED = "interrupted"
INTERRUPTED_EXIT = 130


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Run the InEx agent scenarios against one or more models.",
    )
    parser.add_argument("--models", nargs="+", metavar="M", help="model strings to evaluate")
    parser.add_argument(
        "--runs", type=int, default=DEFAULT_RUNS, help="repetitions of each scenario"
    )
    parser.add_argument("--only", nargs="+", metavar="X", help="categories or scenario ids")
    parser.add_argument(
        "--max-cost",
        type=float,
        default=DEFAULT_MAX_COST,
        metavar="D",
        help="dollars per model, over everything recorded for it in the results folder",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=DEFAULT_NUM_CTX,
        metavar="N",
        help="context size for Ollama models",
    )
    parser.add_argument("--estimate", action="store_true", help="print the projected cost and exit")
    folder = parser.add_mutually_exclusive_group()
    folder.add_argument("--resume", type=Path, metavar="DIR", help="continue the run in DIR")
    folder.add_argument(
        "--report", type=Path, metavar="DIR", help="rebuild DIR's summaries and table"
    )
    parser.add_argument(
        "--langsmith", action="store_true", help="trace to LangSmith with outcome feedback"
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    return args


def _prompt_facts(settings):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        world = build_world(WorldSpec(), Path(folder), settings)
        try:
            return agent_prompt(world.app)
        finally:
            world.close()


def _prices(models, get):
    if not any(is_openrouter(m) for m in models):
        return None
    try:
        return fetch_prices(get)
    except (requests.RequestException, ValueError, OSError) as error:
        print(f"OpenRouter prices could not be fetched ({error})", file=sys.stderr)
        return None


def _estimate_line(model, prompt_chars, scenarios, runs, price):
    projected = estimate(prompt_chars, scenarios, runs, price)
    cost = UNPRICED if projected.cost is None else f"about ${projected.cost:.2f}"
    return (
        f"{model}: estimated {projected.input_tokens:,} input and "
        f"{projected.output_tokens:,} output tokens, {cost}"
    )


def _over_budget(results, model, price, max_cost):
    return price is not None and results.spent(model) >= max_cost


def _run_model(model, plan, results, tracer, build, wait):
    settings = replace(plan["settings"], orchestrator=model)
    check = preflight(settings, plan["prices"], build=build)
    if check.problem:
        print(f"{model}: skipped: {check.problem}")
        results.set_status(model, f"skipped: {check.problem}")
        return
    price = check.price
    results.manifest.setdefault("prices", {})[model] = asdict(price) if price else None
    results.save()
    runs, scenarios, max_cost = plan["runs"], plan["scenarios"], plan["max_cost"]
    print(_estimate_line(model, plan["prompt_chars"], scenarios, runs, price))
    finished = results.finished(model)
    total = len(scenarios) * runs
    num_ctx = plan["num_ctx"] if is_ollama(model) else None
    for run in range(1, runs + 1):
        for scenario in scenarios:
            if (scenario.id, run) in finished:
                continue
            if _over_budget(results, model, price, max_cost):
                spent = results.spent(model)
                print(f"{model}: stopped at ${spent:.2f}, the limit is ${max_cost:.2f}")
                results.set_status(model, STOPPED)
                return
            with tracer.scenario(model, scenario, run) as handle:
                trace = run_scenario(
                    scenario,
                    model=model,
                    run=run,
                    llm=check.llm,
                    settings=settings,
                    num_ctx=num_ctx,
                    wait=wait,
                )
            trace = replace(trace, cost=cost_of(trace.input_tokens, trace.output_tokens, price))
            results.append(trace)
            tracer.feedback(handle, trace)
            line = f"{model} {scenario.id} #{run}: {trace.outcome} ({trace.seconds:.1f}s)"
            if price is not None:
                spent = results.spent(model)
                done = sum(1 for key in results.latest() if key[0] == model)
                projected = reproject(spent, done, total)
                line += f", spent ${spent:.2f} of ${max_cost:.2f}, projected ${projected:.2f}"
            print(line)
    results.set_status(model, COMPLETE)


def _existing(folder):
    if not (Path(folder) / MANIFEST).is_file():
        print(f"{folder}: no {MANIFEST}", file=sys.stderr)
        return None
    return Results.open(folder)


def _report(folder):
    results = _existing(folder)
    if results is None:
        return 2
    write_reports(results.path, results.manifest, results.records())
    print(f"Results: {results.path}")
    return 0


def _new_manifest(args, models, hashes, settings, started):
    commit, dirty = git_state(REPO_ROOT)
    return {
        "git_commit": commit,
        "git_dirty": dirty,
        "hashes": hashes,
        "models": list(models),
        "runs": args.runs,
        "temperature": settings.temperature,
        "num_ctx": args.num_ctx,
        "only": args.only,
        "prices": {},
        "started": started.isoformat(),
        "ended": None,
        "status": {},
        "state": "running",
    }


def _finish(results, state, now):
    results.manifest["ended"] = now().isoformat()
    results.manifest["state"] = state
    results.save()
    write_reports(results.path, results.manifest, results.records())
    print(f"Results: {results.path}")


def main(
    argv=None,
    *,
    results_root=RESULTS_ROOT,
    build=build_llm,
    get=requests.get,
    wait=time.sleep,
    now=datetime.now,
) -> int:
    args = parse_args(argv)
    resumed = None
    if args.resume is not None:
        resumed = _existing(args.resume)
        if resumed is None:
            return 2
        args.runs = resumed.manifest["runs"]
        args.only = resumed.manifest["only"]
        args.num_ctx = resumed.manifest["num_ctx"]
    try:
        settings = load_settings(num_ctx=args.num_ctx)
        scenarios = select(load_scenarios(SCENARIOS_PATH, TOOL_NAMES), args.only)
    except (InexError, ScenarioError) as error:
        print(getattr(error, "message", None) or error, file=sys.stderr)
        return 2
    if args.report is not None:
        return _report(args.report)
    system, schemas = _prompt_facts(settings)
    hashes = {
        "scenarios": text_hash(SCENARIOS_PATH.read_text(encoding="utf-8")),
        "system_prompt": text_hash(system),
        "tools": text_hash(json.dumps(schemas, sort_keys=True)),
    }
    prompt_chars = len(system) + len(json.dumps(schemas))
    if resumed is not None:
        problems = resume_problems(resumed.manifest, hashes, settings.temperature)
        for problem in problems:
            print(f"{args.resume}: {problem}", file=sys.stderr)
        if problems:
            return 2
        models = args.models or resumed.manifest["models"]
    else:
        models = args.models or [settings.orchestrator]
    prices = _prices(models, get)
    if args.estimate:
        for model in models:
            price = model_price(model, prices)
            print(_estimate_line(model, prompt_chars, scenarios, args.runs, price))
        return 0
    if resumed is not None:
        results = resumed
        known = results.manifest["models"]
        known.extend(m for m in models if m not in known)
        results.manifest["state"] = "running"
        results.save()
    else:
        started = now()
        manifest = _new_manifest(args, models, hashes, settings, started)
        results = Results.create(results_root, manifest, started)
    tracer = make_tracer(args.langsmith, f"inex-eval-{results.path.name}")
    plan = {
        "settings": settings,
        "prices": prices,
        "scenarios": scenarios,
        "runs": args.runs,
        "max_cost": args.max_cost,
        "num_ctx": args.num_ctx,
        "prompt_chars": prompt_chars,
    }
    current = None
    try:
        with tracer.session():
            for current in models:
                _run_model(current, plan, results, tracer, build, wait)
    except KeyboardInterrupt:
        if current is not None:
            results.set_status(current, INTERRUPTED)
        _finish(results, INTERRUPTED, now)
        return INTERRUPTED_EXIT
    _finish(results, "complete", now)
    statuses = results.manifest["status"]
    return 0 if all(statuses.get(m) == COMPLETE for m in models) else 1


if __name__ == "__main__":
    raise SystemExit(main())
