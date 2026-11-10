# InEx-Miner

Build datasets of **intrinsic and extrinsic bugs** from npm projects, by asking for them in
plain language.

InEx-Miner collects GitHub issues together with the evidence that explains them (comments,
maintainer replies, the pull request or commit that closed them), uses an LLM to label each
one as **Intrinsic** (the cause is in the project), **Extrinsic** (the cause is outside it:
a dependency, the runtime, the toolchain) or **Not a Bug**, and links every issue to the npm
release that was live when it was filed, with that release's dependencies. Everything lands
in a local SQLite database you can query and export.

You can use it two ways:

- **Talk to it.** `python -m inex chat` starts an agent that understands requests like
  *"collect the 100 newest issues from axios/axios and classify them"*.
- **Script it.** Every operation is also a plain command, for pipelines and notebooks.

**No code changes needed.** Models, labels, prompts and databases are all chosen in
configuration files and command-line options. [What you can change](#no-code-changes-needed)

**Contents:** [Quick start](#quick-start) · [No code changes needed](#no-code-changes-needed) ·
[The chat agent](#the-chat-agent) · [Commands, step by step](#commands-step-by-step) ·
[Your own labels](#your-own-labels) · [Choosing models](#choosing-models) ·
[Settings](#settings) · [Example dataset](#example-dataset) ·
[Evaluating the agent](#evaluating-the-agent) · [Command reference](#command-reference) ·
[How it works](#how-it-works) · [Limits](#limits) · [Development](#development)

---

## Quick start

### 1. Install

You need **Python 3.11 or later**.

```bash
git clone https://github.com/TWright-28/InEx-Miner.git
cd InEx-Miner
python -m venv venv
```

Activate the environment (`venv\Scripts\activate` on Windows, `source venv/bin/activate`
on macOS and Linux), then:

```bash
pip install -r requirements.txt
```

Run every command from this folder.

### 2. Add your keys

Copy `.env.example` to `.env` and fill in two values:

```
GITHUB_TOKEN=...       a GitHub personal access token, for collecting issues
GOOGLE_API_KEY=...     a Google AI Studio key, for the default Gemini models
```

Prefer to keep everything on your machine? Skip the Google key and use local models
through [Ollama](https://ollama.com/) instead; see [Choosing models](#choosing-models).

### 3. Start the chat

```bash
python -m inex chat
```

```
InEx-Miner chat - thread 89f4f91b - type 'q' to quit
  database      db/database.db (0 projects, 0 issues, 0 labelled)
  orchestrator  google_genai:gemini-2.5-flash
  classifier    google_genai:gemini-3-flash-preview
  judge         google_genai:gemini-3.1-pro-preview
  profile       inex-staged
  log           logs/tool.log
  transcript    logs/chat/2026-11-10-172716-89f4f91b.log

What I can do (just ask in plain words):
  count      How many issues does axios/axios have?
  collect    Collect the 100 most recent issues from axios/axios
  classify   Classify them as Intrinsic, Extrinsic or Not a Bug
  link       Link them to the npm releases they were filed against
  import     Collect and classify the issues listed in issues.csv
  analyse    Which label is most common in axios/axios?
  export     Export the labels to CSV
I check with you before slow or costly steps.
Report a problem: https://github.com/TWright-28/InEx-Miner/issues

You:
```

Then just ask. A first session on a small project might be:

```
You: How many issues does yeoman/yeoman-test have?
You: Collect the 20 most recent ones.
You: Classify them.
You: Link them to their npm releases.
You: Which label is most common, and how many dependencies did those releases have?
You: Export the labels to CSV.
```

### Or, the same with commands

```bash
python -m inex count yeoman/yeoman-test
python -m inex collect yeoman/yeoman-test --count 20
python -m inex classify yeoman/yeoman-test
python -m inex link yeoman/yeoman-test --package yeoman-test
python -m inex summary
```

---

## No code changes needed

Everything you are likely to want to change is a setting, a flag or a text file:

| You want to... | Change this | Where |
|---|---|---|
| Use another model to label issues | `INEX_CLASSIFIER`, or `--classifier` | `.env` or the command line |
| Use another model for the chat | `INEX_ORCHESTRATOR` | `.env` |
| Re-check "Not a Bug" verdicts with a different model | `INEX_JUDGE`, or `--judge` | `.env` or the command line |
| Run entirely on local models | Ollama model names in the three settings above | `.env` |
| Label issues with your own categories | a profile: a YAML file and a Markdown guide | `profiles/`, then `--profile` |
| Reword how issues are judged | the guide the profile points to | `profiles/*.md` |
| Skip the re-check stage | remove `recover:` from a copy of the staged profile | `profiles/` |
| Keep separate datasets | `--db path/to/other.db` | the command line |
| Pick which issues | `--count`, `--since`, `--until`, `--oldest-first`, or a CSV list | the command line |
| Trade speed for cost or rate limits | `INEX_WORKERS`, `INEX_RETRIES`, `INEX_THINKING` | `.env` |
| Change how the chat agent behaves | its instructions, in plain text | `prompts/system_prompt.md` |
| Ask your own questions of the data | any SQL `SELECT` | `query` and `export` |

Labels from different models, profiles or prompt wordings are stored side by side and never
overwrite each other, so you can compare setups on the same issues.

---

## The chat agent

The agent has the same operations as the commands, and chooses which to use from what you
ask. While it works it shows each step, the time it took, and a progress bar for long jobs:

```
You: collect 1000 issues in total from ember-cli/ember-cli
  > count_issues ember-cli/ember-cli
    4,236 issues on GitHub, 2,156 stored (0.8s)
Agent: ember-cli/ember-cli already has 2,156 issues stored, so nothing needs collecting.
```

- **It checks before expensive work.** It counts a repository before an open-ended
  collection and asks before collecting 1,000 or more, and it previews a classification
  (how many issues, which models) before running it.
- **"N in total" means what you think.** It subtracts the issues already stored.
- **It keeps going after errors.** If a model call fails, it says so and the conversation
  continues.
- **Everything is saved.** Each session is written to `logs/chat/`, and the conversation
  history to `db/checkpoints.db`.
- Type `q` (or press Ctrl+Z / Ctrl+D) to quit.

The checks before expensive work are instructions in `prompts/system_prompt.md`, not rules
in the code, so a weaker chat model may skip them. [Evaluating the agent](#evaluating-the-agent)
measures how often each model follows them.

---

## Commands, step by step

This takes one small project, `yeoman/yeoman-test`, through every command. The outputs are
from real runs; counts on GitHub will have moved on.

### 1. Create a database

```bash
python -m inex db init
```

```
Database ready at db/database.db.
```

Every command accepts `--db PATH` to work on another file.

### 2. Size up a repository

```bash
python -m inex count yeoman/yeoman-test
```

```
yeoman/yeoman-test has 77 issues.
```

Counts issues (not pull requests) on GitHub and stores nothing.

### 3. Collect issues

```bash
python -m inex collect yeoman/yeoman-test --count 20
```

```
Collecting yeoman/yeoman-test: 100%|██████████| 20/20 issues [00:40<00:00]
Collected 20 issues from yeoman/yeoman-test (0 already in the database, 20 in total).
```

Each issue is stored with its title, body, labels, comments, participants, maintainer
activity, timestamps and the pull request or commit that closed it.

- `--count N` collects N **new** issues; ones already stored are skipped, so running the
  same command again collects the next 20.
- `--since DATE` and `--until DATE` (`YYYY-MM-DD`) limit it to issues created in that window.
- `--oldest-first` starts from the repository's first issue.
- When GitHub's hourly rate limit runs out, it waits for the reset and carries on. Dropped
  connections are retried.

### 4. Preview, then classify

```bash
python -m inex classify yeoman/yeoman-test --preview
```

```
20 of 20 unclassified issues in yeoman/yeoman-test would be classified with google_genai:gemini-3-flash-preview (inex-staged@db6aae2e6399+s1). Recover judge: google_genai:gemini-3.1-pro-preview.
```

The preview calls no model, so you can check the size and settings first. Then:

```bash
python -m inex classify yeoman/yeoman-test
```

- Issues are classified in parallel (`INEX_WORKERS`), with a progress bar.
- It takes the same `--count`, `--since`, `--until` and `--oldest-first` options as `collect`.
- Only issues without a label for the current setup are sent, so a stopped run continues
  where it left off.
- A call that keeps failing is recorded as failed, never as a label, and is retried the next
  time you classify.

### 5. Link issues to npm releases

```bash
python -m inex link yeoman/yeoman-test --package yeoman-test
```

For each classified issue, this finds the latest stable npm release published before the
issue was filed (a prerelease only if no stable release existed yet), saves that release's
direct, peer and dev dependencies, and links the issue to it. The release history is
fetched once and cached in `.npm_doc_cache/`; delete a package's file there to pick up newer
releases.

| Option | Effect |
|---|---|
| `--since DATE`, `--until DATE` | Link only issues created in that window |
| `--version-range 2.x` or `--version-range 1.0.0..2.0.0` | Link only to releases in that range |
| `--last-versions N` | Also save the N newest releases, for comparison |
| `--versions-from DATE`, `--versions-to DATE` | Also save every release published in that window |

Scoped packages work too: `link babel/babel --package @babel/core`.

### 6. Look at the results

`summary` shows one row per project: issues collected, labelled and linked, and each label's
count for your current setup. On the database behind the
[example dataset](#example-dataset):

```bash
python -m inex summary
```

```
Labels by google_genai:gemini-3-flash-preview (inex-staged@db6aae2e6399+s1, temperature 0.2)

project              issues  labelled  linked  Intrinsic  Extrinsic  Not a Bug
axios/axios              41        41      41         22          6         13
babel/babel              25        25      25         21          0          4
ember-cli/ember-cli   2,156     1,000   1,000        191        217        592
eslint/eslint           100        25      25         10          1         14
expressjs/express       100       100     100          9         15         76
webpack/webpack          25        25      25         23          0          2
total                 2,447     1,216   1,216        276        239        701
```

`query` runs any read-only SQL and prints the first 50 rows; anything that would change the
data is refused:

```bash
python -m inex query "SELECT classification, COUNT(*) AS issues FROM classifications WHERE status IS NULL OR status = 'ok' GROUP BY classification"
```

```
classification	issues
Extrinsic	239
Intrinsic	276
Not a Bug	701
```

`export` writes the full result of a query to `exports/`, as CSV or JSON:

```bash
python -m inex export "SELECT p.repo, i.issue_number, c.classification, v.version, v.direct_count FROM classifications c JOIN issues i ON i.id = c.issue_id JOIN projects p ON p.id = i.project_id LEFT JOIN versions v ON v.id = i.version_id WHERE c.status IS NULL OR c.status = 'ok'" --name labels
```

```
Exported 1216 rows to exports/labels.csv.
```

When you write your own SQL, keep the filter `status IS NULL OR status = 'ok'` (failed
attempts are rows too), and filter or group by `model` and `prompt_version` if you have
labelled the same issues more than once.

### 7. Work from a list of issues

Put the issues you want in a CSV, one `owner/repo#number` per row, and `import` collects and
classifies exactly those, across any number of repositories:

```bash
python -m inex import examples/demo/issues.csv --preview
```

```
1216 issues across 6 repositories: 1216 already in the database, 0 to collect, 0 unreadable rows.
```

The first column is read unless you name one with `--column`; `--collect-only` collects
without classifying. Drop `--preview` to run it.

### 8. Try another model or taxonomy

```bash
python -m inex --classifier qwen3:30b classify yeoman/yeoman-test
python -m inex --profile profiles/example-severity.yaml classify yeoman/yeoman-test
```

Each combination is stored as its own run, next to the earlier ones, and `summary` and
`--preview` always describe the combination you are using.

### 9. Load labels made elsewhere

```bash
python -m inex load path/to/issues.jsonl --model my-model --prompt-version v1 --temperature 0
```

Reads a `.jsonl` file, or every one under a folder. Each line is an issue with at least
`owner`, `repo` and `number` (plus any of `title`, `body`, `state`, `created_at`,
`closed_at`, ...); a line with a `classification` also stores that label, so earlier
datasets can be summarised, queried and linked like any other.

### 10. Use it from scripts

Add `--json` to any command for machine-readable output:

```bash
python -m inex --json summary
```

Errors exit with code 1, usage errors with 2 and Ctrl+C with 130. Commands print only
warnings, progress bars and their result; the details go to `logs/tool.log`. To start over,
`db reset --yes` rebuilds every table and `db clear --yes` empties them.

---

## Your own labels

The labels come from a **profile**: a YAML file that names the labels, and a Markdown guide
the model reads. Three are included:

| Profile | What it does |
|---|---|
| `profiles/inex-staged.yaml` (default) | Intrinsic / Extrinsic / Not a Bug, in three steps (below) |
| `profiles/inex.yaml` | The same labels in one model call, plus **Unknown** for issues with too little information |
| `profiles/example-severity.yaml` | A different question entirely: Critical / Major / Minor |

The severity profile shows the format:

```yaml
name: example-severity
version: 1
description: Example profile that grades a bug report's severity instead of its root cause

guide: example-severity_guide.md

labels:
  - name: Critical
    description: Data loss, a security hole, or the tool is unusable for most users.
  - name: Major
    description: A core feature is broken but a workaround exists.
  - name: Minor
    description: Cosmetic, rare, or limited to an edge case.

reasoning:
  - field: impact
    title: Impact
    description: Describe what breaks for the user and how badly
  - field: workaround
    title: Workaround
    description: Note whether the report or the discussion gives a workaround
  - field: scope
    title: Scope
    description: Estimate how many users or configurations are affected

confidence: [High, Medium, Low]
```

`labels` are the allowed answers, `reasoning` lists what the model must work through before
answering, and the guide explains in plain words how to choose. For each issue the model
receives the guide and the issue (title, body, comments, closing pull request) and returns
its reasoning, a score for each label and its answer. An answer outside the list is a failed
call, not a label.

**To make your own:** copy `example-severity.yaml` and its guide, change the labels, the
reasoning steps and the wording, and run with `--profile profiles/your-profile.yaml` (or set
`INEX_PROFILE`). A broken profile is rejected when the tool starts, before any work. The
chat agent picks up the new labels automatically.

**The default, staged profile** asks three questions instead of one: *is this a bug?*
(validity), then, for "Not a Bug" answers, *is that right?* (recover, asked of a separate
judge model), then, for bugs, *is the cause inside or outside the project?* (origin). Each
step has its own guide in `profiles/inex-staged/`, and every step's answer is stored with
the label. To skip the re-check, remove `recover:` from a copy of the profile.

**Every label records what produced it.** It is stored with the model, the temperature and
a prompt version such as `inex-staged@db6aae2e6399+s1`: the profile's name and a hash of its
files. Edit a guide and the hash changes, so the issues show as unclassified for the new
version while the old labels stay.

---

## Choosing models

Three models do different jobs, and each can be hosted or local:

| Role | Setting | Default | Needs |
|---|---|---|---|
| Classifier: labels issues | `INEX_CLASSIFIER` | `google_genai:gemini-3-flash-preview` | |
| Judge: re-checks "Not a Bug" | `INEX_JUDGE` | `google_genai:gemini-3.1-pro-preview` for a hosted classifier, otherwise the classifier itself | |
| Orchestrator: runs the chat | `INEX_ORCHESTRATOR` | `google_genai:gemini-2.5-flash` | native tool calling |

Model names are `provider:model` for hosted models and the Ollama tag for local ones:

| Example | Runs on |
|---|---|
| `google_genai:gemini-3-flash-preview` | Google (`GOOGLE_API_KEY`) |
| `openai:gpt-4o` | OpenAI (`OPENAI_API_KEY`) |
| `openrouter:deepseek/deepseek-v4.1-flash` | OpenRouter (`OPENROUTER_API_KEY`) |
| `qwen3:30b` | Ollama, as the classifier (`ollama pull qwen3:30b` first) |
| `ollama:gpt-oss:20B` | Ollama, as the orchestrator |

**Fully local.** With a local classifier and no judge set, the classifier also runs the
re-check, so no issue text is sent to a hosted model. For example:

```
INEX_CLASSIFIER=qwen3:30b
INEX_ORCHESTRATOR=ollama:gpt-oss:20B
INEX_WORKERS=1
```

Only GitHub and the npm registry are contacted, to fetch issues and releases.

---

## Settings

All settings live in `.env` (copy `.env.example`). The database, classifier, judge and
profile can also be set per command with `--db`, `--classifier`, `--judge` and `--profile`.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `db/database.db` | SQLite database file |
| `GITHUB_TOKEN` | none | Required for `count`, `collect` and `import` |
| `INEX_CLASSIFIER` | `google_genai:gemini-3-flash-preview` | Model that labels issues |
| `INEX_JUDGE` | see [Choosing models](#choosing-models) | Model that re-checks "Not a Bug" |
| `INEX_ORCHESTRATOR` | `google_genai:gemini-2.5-flash` | Model behind the chat |
| `INEX_THINKING` | `medium` | Thinking level for a Gemini 3 classifier (`minimal`, `low`, `medium`, `high` or `none`) |
| `INEX_JUDGE_THINKING` | `low` | Thinking level for a Gemini 3 judge |
| `INEX_TEMPERATURE` | `0.2` | Sampling temperature |
| `INEX_WORKERS` | `8` | Issues classified in parallel (use 1 for a local model) |
| `INEX_RETRIES` | `5` | Attempts per issue before it is recorded as failed |
| `INEX_PROFILE` | `profiles/inex-staged.yaml` | Classification profile |
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama server |
| `INEX_NUM_CTX` | `131072` | Context window for a local orchestrator |
| `OPENROUTER_API_KEY` | none | Key for `openrouter:` models |

---

## Example dataset

`examples/demo/` holds a ready-made dataset: **1,216 issues from six npm projects** (axios,
express, eslint, webpack, babel and ember-cli), collected, classified with the default
profile (Gemini 3 Flash, with Gemini 3.1 Pro as the judge) and linked to their npm releases
between 2026-11-05 and 2026-11-08. No classification failed: 276 are Intrinsic, 239
Extrinsic and 701 Not a Bug.

| File | Holds |
|---|---|
| `labels.csv` | Each issue's label, linked release and its direct, peer and dev dependency counts, with the model and prompt version behind the label. Open it directly; no keys needed. |
| `issues.csv` | The 1,216 issues as `owner/repo#number`, ready for `import` |

To rebuild it yourself (needs `GITHUB_TOKEN` and `GOOGLE_API_KEY`; two or three model calls
per issue):

```bash
python -m inex --db db/example.db import examples/demo/issues.csv --preview
python -m inex --db db/example.db import examples/demo/issues.csv
python -m inex --db db/example.db link axios/axios --package axios
python -m inex --db db/example.db link expressjs/express --package express
python -m inex --db db/example.db link eslint/eslint --package eslint
python -m inex --db db/example.db link webpack/webpack --package webpack
python -m inex --db db/example.db link babel/babel --package @babel/core
python -m inex --db db/example.db link ember-cli/ember-cli --package ember-cli
python -m inex --db db/example.db summary
```

Your labels can differ a little from `labels.csv`: issues gain comments over time, and the
models sample at temperature 0.2.

---

## Evaluating the agent

Before relying on a chat model, you can test it. `python -m evals` runs 58 scenarios
(choosing the right tool, asking before expensive work, arguments, multi-step workflows,
errors and reworded requests) and checks which tools the agent called, with which
arguments, and what it replied. GitHub, npm and the classifier are simulated, so only the
chat model is called.

```bash
python -m evals --models ollama:llama3.2:3b --only routing --runs 1
python -m evals --models openrouter:deepseek/deepseek-v4.1-flash --estimate
python -m evals --models openrouter:deepseek/deepseek-v4.1-flash --max-cost 0.50
```

- `--estimate` prints the expected tokens and cost and calls nothing; run it first.
- `--max-cost` (default $1.00) stops a paid model when its spend reaches the limit.
- `--runs` (default 3) repeats each scenario; `--only` picks categories or scenario ids.
- `--resume DIR` continues a stopped run; `--report DIR` rebuilds the summaries without
  calling a model; `--langsmith` sends traces to LangSmith.
- Ollama models take `--num-ctx` for their context size (default 16384).

Each run writes a folder under `evals/results/` with a manifest (commit, hashes, models,
prices), every run's trace, pass rates per category and a LaTeX table. The results reported
in the paper are in `evals/results/20261027-231556/`.

---

## Command reference

```bash
python -m inex [--db PATH] [--classifier MODEL] [--judge MODEL] [--profile PATH] [--json] COMMAND
```

| Command | What it does |
|---|---|
| `count OWNER/REPO` | Count a repository's issues on GitHub |
| `collect OWNER/REPO [--count N] [--since DATE] [--until DATE] [--oldest-first]` | Collect issues and their evidence |
| `classify OWNER/REPO [same options] [--preview]` | Label collected issues |
| `link OWNER/REPO --package NAME [--since DATE] [--until DATE] [--version-range R] [--last-versions N] [--versions-from DATE] [--versions-to DATE]` | Link issues to the npm release live when they were filed |
| `import LIST.csv [--column NAME] [--collect-only] [--preview]` | Collect and classify a list of `owner/repo#number` |
| `load FILE_OR_DIR --model M --prompt-version P [--temperature T]` | Load issues and labels from JSONL |
| `query "SELECT ..."` | Run a read-only query (first 50 rows) |
| `export "SELECT ..." [--format csv\|json] [--name NAME]` | Save a query's results to `exports/` |
| `summary` | Issues, labels and links per project |
| `chat` | Talk to the agent |
| `db init` / `db reset --yes` / `db clear --yes` | Create, rebuild or empty the database |

---

## How it works

The database holds five kinds of record:

| Record | What it is |
|---|---|
| **Project** | A GitHub repository (`owner/repo`), with its npm package name once linked |
| **Issue** | One GitHub issue, with its comments, closing pull request or commit, and timing |
| **Classification** | One label for an issue, with the model's reasoning, scores and what produced it |
| **Version** | One npm release of a project, with the dependencies it declared |
| **Dependency** | A `direct`, `peer` or `dev` dependency of a version |

```
projects(id, owner, repo, package_name, added_at)
issues(id, project_id, issue_number, title, body, state, state_reason, created_at, closed_at, version_id, raw_data, ...)
classifications(id, issue_id, classification, classification_probabilities, model, prompt_version, temperature,
                classified_at, status, error, validation_flags, argmax_label, attempts, stages)
versions(id, project_id, package_name, version, published_at, direct_count, peer_count, dev_count, ...)
version_dependencies(version_id, dep_name, dep_kind, dep_version_range, ...)
```

When asked to "analyse" a project, the agent reports the count of each label and the
average direct, peer and dev dependencies per label, and offers issue-level detail and a
comparison across projects.

```
inex/
  cli.py, __main__.py   Command line (python -m inex)
  agent/                Chat agent: tools and orchestrator
  app.py                Wires the database, clients and services together
  config.py             Settings, read from .env and options
  validators.py         Input checks shared by both front ends
  services/             collection, classification, profiles, linkage, imports, queries, maintenance, progress
  store/                SQLite schema and every query
  clients/              npm registry client
profiles/               Classification profiles and their guides
prompts/                The chat agent's instructions
evals/                  Agent evaluation: scenarios, harness and results
examples/demo/          The example dataset
tests/                  Test suite
docs/architecture.md    Design, rules, and the reasoning behind non-obvious code
```

---

## Limits

- Only GitHub issues and npm packages are supported; other trackers or ecosystems would need
  new code.
- Hosted models need API keys and cost money in proportion to the number of issues (two or
  three calls per issue with the default profile). The default classifier and judge are
  preview models whose behaviour may change.
- Collection speed is bounded by GitHub's rate limits.
- A local model with a small context window may not see all of a long issue.

---

## Development

```bash
python check.py
```

runs the format check, lint and the full test suite. The design and its rules are in
[docs/architecture.md](docs/architecture.md).

## License

MIT; see [LICENSE](LICENSE).
