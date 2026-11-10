# Architecture and standards

How InEx-Miner is put together, and the rules that keep it that way. Most rules are checked by
`python check.py`, so breaking one fails the check instead of relying on someone
remembering it. This document is the source of truth: a change that alters a rule updates
this file in the same commit.

## The shape

InEx-Miner is one Python package, `inex/`, with two front ends over the same services: a
command line (`python -m inex`) and a chat agent (`python -m inex chat`). A request travels
down this list and the answer travels back up. A layer may only import from layers below it.

```
cli.py, agent/   Front ends. Check input, call one service, format the reply.
app.py           build_app(settings): builds the store, the clients and every service.
services/        Every rule and decision. No SQL, no HTTP, no LangChain.
store/           SQLite. The only code that runs SQL.
clients/         Outside APIs (the npm registry). Return the API's data lightly tidied.
```

Around those:

- `config.py` is the only file that reads environment variables. `load_settings()` reads
  `.env` when an entry point asks; nothing happens at import.
- `errors.py` defines `InexError(code, message, **details)`, the one error type services
  raise. Only `cli.py` and the wrapper in `agent/tools.py` turn an error into output.
- `validators.py` checks user input once, at the front end, for both front ends.
- `profiles/` holds classification profiles: a YAML file naming the labels and reasoning
  steps, and the guide it points at. `prompts/system_prompt.md` is the agent's prompt.

### Import rules

| Layer | May import (first party) | May not import |
|---|---|---|
| `cli` | `app`, `validators`, `errors`, `config`, `agent/chat` | `store`, `clients`, `services` |
| `agent/` | `app`, `validators`, `errors`, `config` | `cli`, `store`, `clients`, `services` |
| `app` | `config`, `services`, `store`, `clients`, `errors` | `cli`, `agent` |
| `validators` | `errors` | everything else |
| `services/` | `store`, `clients`, `errors`, other `services` | `cli`, `agent`, `app` |
| `store/` | `errors` | everything else |
| `clients/` | `errors` | everything else |
| `config`, `errors` | `errors` only | everything else |

Across all of `inex/`: `sqlite3` only in `store/`, `requests` only in `clients/`,
`langchain*` and `langgraph` only in `agent/`, `dotenv` and `os.environ`/`os.getenv` only in
`config.py`. Relative imports are not allowed.

### Where does new code go?

| You are adding... | Put it in... |
|---|---|
| A command | `cli.py`, calling a service method |
| An agent tool | `agent/tools.py`, calling a service method |
| A rule, a calculation, a decision | `services/` |
| A check on what the user typed | `validators.py` |
| A SQL query | `store/database.py` |
| A table or column | `store/schema.py` (`TABLES`, and `ADDED_COLUMNS` for existing databases) |
| A call to another website's API | `clients/` |
| A setting or secret | `config.py` and `.env.example` |
| A taxonomy | `profiles/`: a YAML profile and its guide |

## Exceptions

Two files still make their own outside calls. They are listed in `EXCEPTIONS` in
`tests/test_architecture.py`, and that list is how you know the split is done.

| File | May also import | Ends when |
|---|---|---|
| `services/collection.py` | `requests` | GitHub REST and GraphQL move to `clients/github.py` |
| `services/classification.py` | `requests`, `langchain*` | Model backends move to `clients/`; prompt rendering, validation and the batch runner become separate modules |

Ruff's naming rules (`N`) are switched off for those two files, whose internal methods are
still camelCase, and `N802` is off for `tests/test_concurrency.py`, whose test subclasses
override those methods. All three entries in `ruff.toml` go when the files are split.

## Standards

**Errors.** Raise `InexError` with a stable snake_case `code` and a message written for the
person reading it. Never catch an error just to log it. Unexpected errors are logged in full
and the user sees a generic message. The one deliberate exception: a failed classification
is recorded with its status, not raised, so one bad issue cannot stop a batch.

**Validation.** Input is checked once, in `validators.py`, before any work starts.

**Naming.** PEP 8: snake_case functions, methods and variables; PascalCase classes. The
internals of the two excepted files follow it when they are split.

**No unexplained numbers.** Name them as constants at the top of the module.

**No comments or docstrings** in any Python file, tests included. Reasoning that is not
visible in the code goes in "Things that are not obvious" below. `# noqa` is the only
permitted comment.

**No work at import time.** Importing a module never opens a file, a connection or a
client. Entry points build everything through `build_app`.

**Tests.** `pytest` runs with `--disable-socket`, so a test that reaches the network fails.
Each test sets up its own data. Tests sit in `tests/`, shared fakes in `tests/fakes.py`.

**Formatting and lint.** Ruff decides formatting and catches mistakes. Neither is up for
debate in review.

**Dependencies.** Add one only when the alternative is writing and maintaining a meaningful
amount of code.

## Things that are not obvious

### Classification

- **Field order in the response schema matters.** Gemini emits properties in schema order,
  so the reasoning fields come before `probabilities` and `final_answer`; otherwise the
  model commits to an answer before it reasons. `build_schema` keeps the model names
  `ClassificationResult` and `_Probabilities` and adds no field descriptions, so the InEx
  schema is byte-identical to the hand-written one it replaced
  (`tests/fixtures/inex_schema.json`).
- **What `prompt_version` identifies.** It is
  `<profile>@<sha256(profile text + guide text)[:12]>+t<TEMPLATE_VERSION>`, with line
  endings normalised to LF first so a checkout that converts them (`core.autocrlf`) does
  not change the run key; `.gitattributes` also keeps `profiles/` at LF. The prompt
  actually sent is the guide plus the issue template in `formatIssueData`, so bump
  `TEMPLATE_VERSION` whenever that template changes; hashing only the files would let the
  template drift silently. Rows classified before profiles existed carry a path-based
  version and read as unclassified under the new scheme.
- **A failed attempt is never a label.** `AttemptFailure` carries the status to record if
  retries run out, so a failure is never collapsed into a taxonomy label such as
  `Unknown`. Failed rows are stored with a NULL classification and a non-ok `status`, and
  are retried on the next run because the resume query only counts `status = 'ok'` (or a
  NULL legacy status) as done. Before this rule, 1,063 rows of an earlier 71,640-issue corpus were
  parse failures stored as `Unknown`.
- **Which errors are retried.** Transient faults (timeouts, connection errors, 429 and 5xx)
  and replies that are empty or cannot be read. Auth, schema and context-overflow errors
  fail fast. With langchain-google-genai
  4.2.3 a 429 arrives as `ChatGoogleGenerativeAIError` whose `__cause__` is a
  `google.genai.errors.ClientError`; `google.api_core.exceptions.ResourceExhausted` is never
  raised. `google.genai` `ServerError` propagates uncaught by LangChain. Context overflow is
  a `ClientError` subclass and is never worth retrying.
- **One retry controller per call.** A tenacity `Retrying` object carries per-run state, so
  worker threads must not share one.
- **Attempt counts survive hard failures.** When an exception escapes `callModel`, the
  attempt count rides along on the exception so the `attempts` column reports the retries
  that actually happened.
- **The Gemini client is built once.** Double-checked locking guards construction, and the
  batch runner builds it before the thread pool starts so workers do not race to build it.
  `max_retries=3` is explicit because 0 means "use the SDK default of 5"; the timeout is
  explicit because httpx otherwise has none.
- **Structured output settings.** `json_schema` is the default and only native mode in
  langchain-google-genai 4.2.3 (`json_mode` is a deprecated alias, `function_calling` is
  discouraged). `include_raw=True` keeps the raw response for auditing and turns a parse
  failure into a value instead of an exception. If `include_thoughts` is ever enabled the
  content becomes a list of blocks; it is JSON-encoded so the raw-response column stays a
  string.
- **Validation of a result.** Every label in the profile must have a probability: a dict
  was once accepted if even one label matched, which let 379 single-key results through.
  Each value must lie in [0, 1]. A sum off by more than 0.02 is flagged (`prob_sum_off`),
  not failed. When the highest probability disagrees with the stated label, the
  disagreement is recorded in `argmax_label` and never overrides what the model said.
- **Prompt construction.** Issue fields use `or` defaults because the keys often exist with
  value None. An absent closing PR or commit is stated as "None found", because the guide
  leans on PR evidence and an empty string cannot be told apart from missing data. Stored
  `raw_data` is spread before the explicit columns so it cannot override title, body or
  state.
- **Which models are cloud models.** `provider:model` with a provider in `openai`,
  `anthropic`, `google_genai` or `google_vertexai` goes through LangChain with structured
  output. Anything else, including tags with a colon such as `qwen3:30b`, is an Ollama
  model.
- **Ctrl+C stops paid work.** On an interrupt the batch runner cancels every issue that has
  not started; at most one in-flight issue per worker finishes and is saved. A plain
  `with ThreadPoolExecutor` would wait for the whole queue, sending every remaining issue
  to the model.
- **One bad issue cannot stop a batch.** Each issue is classified and saved inside a guard
  that catches every `Exception` (never `KeyboardInterrupt`) and records `api_error`. If the
  save itself fails, the issue stays unclassified and the next run picks it up, which is
  the same contract as a failure.
- **Staged profiles run a domain-informed pipeline.** A profile with `stages` runs the
  validity, recover and origin stages. `services/staged.py` holds its issue formatter,
  prompt assembly, answer reading and routing, and receives model calls as a function, so
  it imports no model library; `ClassificationService` supplies the calls for the stage
  model and the judge.
- **Staged prompts follow the original pipeline byte for byte.** The guides in
  `profiles/inex-staged/` are the pipeline's own, and `format_issue` and
  `upstream_context` reproduce its script; `tests/fixtures/staged/` holds prompts that
  script generated for a sample issue. Only null values are handled differently, because
  the script crashed or printed "None" on them. The single-call template's later changes
  ("None found", the Closed Reason line, contributors counted as maintainers, the comment
  cap) are not applied, so the staged pipeline is the one that was originally evaluated.
  Its answer pattern is the script's too: `**Final Answer**: Bug`, with the colon outside
  the bold, is unreadable and retried.
- **A staged label has no probability distribution.** Stage 2 asks for none, so
  `classification_probabilities` is `{}` and the single-call probability checks do not
  apply. Each stage's verdict, confidence, probabilities and reply are in
  `classifications.stages`.
- **Recover uses an independent judge.** A model re-checking its own verdict tends to
  re-endorse it, so with a hosted classifier the judge defaults to Gemini 3.1 Pro. With a
  local classifier and no `INEX_JUDGE`, the judge is the classifier itself, so a local setup
  never sends issues to a hosted model it did not name; set `--judge` to another local model
  for an independent recheck. The preview names the judge.
- **Thinking levels apply to Gemini 3 and later only.** `thinking_level` is a Gemini 3
  setting, so it is not sent to Gemini 2.5 or to other providers, and only a level that is
  actually sent counts in the run key. `INEX_THINKING=none` turns it off; any value other
  than minimal, low, medium, high or none is rejected when settings load.
- **The staged run key covers the judge.** `prompt_version` is
  `<profile>@<hash>+s<STAGED_TEMPLATE_VERSION>`, where the hash covers the profile, its
  three guides, the judge model and both thinking levels, so changing any of them starts a
  new run. Single-call run keys do not change with these settings.
- **Any stage can fail the issue.** A reply that is empty or unreadable is retried; when
  retries run out the issue is stored as failed with no label, Recover included, where the
  script silently kept the Stage 1 verdict. A hosted reply that is empty with a stop reason
  (`MAX_TOKENS`, `SAFETY`) fails without a retry.
- **Profile YAML and booleans.** Unquoted `Yes`, `No`, `On` and `Off` are booleans in YAML.
  The loader rejects label names and reasoning fields that are not text instead of turning
  them into `True` and `False`.

### Storage

- **Every count filters failures.** Failed attempts live in `classifications` too, so every
  aggregate filters `status IS NULL OR status = 'ok'`. NULL marks a legacy row from before
  the column existed and counts as done.
- **`summary` counts labels for the current run.** Its label columns use the configured
  model, prompt version and temperature, the same run key `classify` works against, so an
  issue labelled by two runs is never counted twice and the numbers match what `classify`
  would leave alone. Issue and link counts do not depend on the run. It is a command only,
  not an agent tool, so the agent's tool set and its evaluation are unchanged. The method
  lives on `ClassificationService` because the run key does.
- **The unclassified query keeps its run filters in the `ON` clause.** Moving the model,
  prompt-version and temperature predicates to `WHERE` turns the `LEFT JOIN` into an inner
  join and matches nothing.
- **Writes take a lock instead of using WAL.** The read-only connection cannot create the
  shared-memory file WAL needs. A write takes microseconds against a minute-long model
  call, so the lock costs nothing in practice.
- **Upsert, not `INSERT OR REPLACE`.** Replace deletes and reinserts, which changes
  `classifications.id` on every re-save.
- **Older databases get a unique index.** Databases from before this tool (the original
  research corpus among them) predate the unique key on
  `classifications(issue_id, model, prompt_version, temperature)`, which the upsert needs.
  Opening any database adds missing columns and `ux_classifications_run`; rows never
  change. Keep the archived corpus byte-for-byte and point the tool at a copy.
- **The upgrade is all or nothing.** A legacy database holding the same run twice cannot
  take the unique index, so it is refused with `duplicate_classifications` before anything
  changes; otherwise the whole upgrade runs in one transaction.
- **The read-only connection uses a file URI** built with `Path.as_uri()`, so paths with
  spaces, parentheses or `#` work.
- **`load` skips a label only when the same run already has one.** "Has a classification"
  means a successful row for the same issue, model, prompt version and temperature. A
  label from another profile or model must not block the load, and a failed attempt is not
  a label, so a loaded label replaces it through the upsert.

### Collection

- **GitHub failures have names.** A 404 is `repo_not_found`, 401 and 403 are
  `github_auth_failed`, and a dropped connection is `network_error`. The project row is
  saved only after the first page of issues arrives, so a mistyped repository leaves
  nothing behind.
- **Network failures are retried.** A dropped connection, a timeout or a 5xx response is
  retried up to four times, waiting 2, 4 and 8 seconds; a client error such as 404 is not.
  One dropped connection once ended a 1,000-issue collection at issue 283. If a failure
  outlasts the retries mid-collection, the issues already saved stay saved and the error
  says so, since a rerun skips them.
- **A nested GitHub object can be null.** A review by a deleted account comes back with
  `"user": null`, not a missing key, so `.get("user", {})` returns None. The collector
  reads every nested object through `_object`, which treats null like a missing key; a
  review by a deleted account once stopped the ember-cli collection. A deleted commenter
  appears as `unknown` in the comment text the classifier sees.
- **A count means new issues.** `collect --count N` and the tool's `count` collect N
  issues not yet stored; stored ones are skipped and do not count. `count_issues` reports
  `stored` beside the GitHub count, and a collection reports the repo's `total` afterwards,
  so the agent can turn "1000 in total" into the difference. Without that, the agent once
  read "1000 total" as 1,000 more and took ember-cli from 1,156 issues to 2,156.
- **Issue counts use GraphQL.** GitHub search answers 422 for some public repositories,
  facebook/react among them, and the GraphQL count is exact.
- **The list request carries the requested direction.** Ascending collection with an end
  date or a count depends on GitHub returning the oldest issues first.
- **Fix attribution.** A closing PR is taken from the close event when it was merged
  within a day of the close (`CLOSE_EVENT_WINDOW_SECONDS`), otherwise from cross-referenced
  or referenced PRs merged within a week (`REFERENCE_WINDOW_SECONDS`), otherwise from the
  closing commit.

### Linkage

- **An issue links to the release that was live when it was filed:** the most recently
  published stable release at or before the filing date. Semver order is not chronological
  (a 1.9.5 backport can ship after 2.0.0). A prerelease is used only when no stable release
  qualifies, because a prerelease is never npm's `latest` tag and is not what a reporter
  installed. A naive "highest semver at or before the date" rule disagreed with this on 35%
  of a 53,000-issue corpus, almost all of it prereleases.
- **Timestamps are parsed, not compared as strings.** npm reports sub-second precision and
  GitHub does not; as strings `.` sorts before `Z` and a release looked earlier than it
  was. Parsing is cached because the same strings are compared millions of times.

### Front ends

- **The tool runs from the clone.** `config.py` reads the profiles, the system prompt and
  the logging configuration relative to the repository, so there is no package install:
  `pip install -r requirements.txt`, then `python -m inex`. `requirements.txt` is the one
  pinned list, development tools included.
- **Logs never go to stdout.** The CLI moves any logging handler that targets stdout to
  stderr, so `--json` output stays valid JSON.
- **The console shows warnings, progress bars and results.** Every command raises console
  handlers to WARNING, so HTTP and per-issue lines go only to the log file. Collection and
  classification report each issue on their own logger, `inex.progress`, through
  `services/progress.py`: a DEBUG record carrying the task, issues done and the total (none
  for an open-ended collection), and a last one marked finished, sent from a `finally` so a
  failed or interrupted run still closes its bar. The CLI's `ProgressBars` handler draws
  them with tqdm on stderr, for commands and chat alike, and only when stderr is a
  terminal; the services never import tqdm. Milestone lines (collection every 10 issues,
  classification every 25 and at the end) stay at INFO for the log file and the chat
  transcript, whose handler ignores the per-issue records. Without any progress a
  1,000-issue collection looked frozen. Replies are printed with the
  message's `.text`, because Gemini can return its answer as a list of parts carrying a
  signature. A failed orchestrator call (after the provider's own retries) is logged and
  reported as the reply, so the chat and its conversation survive it instead of exiting.
- **The chat shows its steps.** `run_chat` streams the agent's updates instead of waiting
  for the final answer, so each tool call is printed (`  > tool target key=value`) before
  it runs and its result after, summarised in one line by `agent/display.py` with the time
  taken. The banner's counts come from `QueryService.overview`, which counts only
  successful labels. A fixed welcome follows the banner, one example request per tool
  group with the profile's own labels and the issue tracker's address; it is plain text,
  not a model call, so starting the chat costs nothing. Every session is written to
  `logs/chat/<time>-<thread>.log` with the banner, messages, steps and progress lines, so
  a run can be shown or attached later; `logs/` is not committed. The evaluation still calls `invoke`, so none of this changes
  what it measures. Blank lines are not sent to the model, end of input (Ctrl+Z or a closed
  pipe) quits like `q`, and quitting prints where the transcript was saved.
- **Two third-party warnings are hidden by message.** Every chat start printed a Pydantic V1
  warning on Python 3.14 and a LangChain notice about `allowed_objects` raised while
  LangGraph imports; neither is something the user can act on. `main` ignores both
  messages, and `_chat` imports the chat module through `import_quietly`, which records
  warnings during the import and re-shows all but those two. The second one needs that:
  importing `langchain_core` installs its own "default" filter for LangChain warning
  categories ahead of any filter set earlier. Restoring the filters after the import also
  drops that LangChain filter, so later LangChain deprecation notices follow Python's
  defaults during a chat.
- **Output is UTF-8.** Issue titles routinely contain arrows, emoji and CJK text. The CLI
  switches stdout and stderr to UTF-8 (unencodable characters are replaced) and log files
  default to UTF-8, so redirected output on Windows does not crash on them.
- **`.env` is read from the current folder.** `.env.example` leaves `INEX_PROFILE` empty
  so the bundled profile is used wherever the tool runs.
- **Agent tools keep their names.** The nine tool names match `prompts/system_prompt.md`
  and the checks in `evals/scenarios.yaml`; renaming a tool means changing all three.
- **Ollama output is capped per call.** Ollama's default `num_predict` is unlimited, and
  when a reply fills the context window it shifts the window and keeps generating. In the
  evaluation, llama3.2:3b once generated for an hour inside a single call, with no error to
  stop it. `build_llm` passes `num_predict` of 8192 tokens, well above the roughly 1,000 a
  whole scenario normally produces, so a runaway reply ends in a few minutes.
- **A "null" string is an omitted argument.** Some models (Llama 3.3 70B on OpenRouter, in
  316 of its 570 evaluation calls) send `"null"`, `"None"` or an empty string for every
  optional argument instead of leaving it out. The agent tools read those as omitted, so
  the default applies. This is done with pydantic `BeforeValidator`s inside `Annotated`
  types, which leave the JSON schema the model sees byte-for-byte unchanged; the eval's
  tool hash therefore does not change, and the manifest's git commit is what records it.
  Required arguments are not relaxed: a `"null"` repository is still rejected.
- **Three kinds of model string.** `build_llm` sends `ollama:<tag>` to `ChatOllama` (with
  `OLLAMA_BASE` and `INEX_NUM_CTX`), `openrouter:<vendor>/<model>` to `ChatOpenAI` pointed
  at OpenRouter, and anything else to LangChain's `init_chat_model`. A missing provider
  package or key becomes an `InexError` that names the fix.

## Evaluation

`python -m evals` runs the scenarios in `evals/scenarios.yaml` through the real agent,
against any model string, and writes the results to `evals/results/<timestamp>/`.

- **Pure modules and wiring.** `scenarios.py` (loading and validation), `checks.py`
  (scoring) and `report.py` (summaries and the LaTeX table) import only the standard
  library and `yaml`; `tests/test_architecture.py` enforces it. Everything that touches
  LangChain, the store or the network lives in `world.py`, `runner.py`, `models.py`,
  `results.py`, `tracing.py` and `__main__.py`. The scoring rules can therefore be read
  and tested without a model.
- **Checks are deterministic.** Each check reads the recorded trace (tool calls with their
  effective arguments, and replies), not a judge model. The same trace always scores the
  same, and `--report DIR` rebuilds every summary from `traces.jsonl` without spending.
- **Errored is not failed, except in pass^k.** A run that could not be judged (a provider
  exception after two reruns, or an Ollama prompt longer than its context) is `errored`
  and stays out of the pass rate. pass^k counts it as a failure, as τ-bench does, since
  the scenario was not completed in every run. The recursion limit is `failed`: a looping
  agent is the model's behaviour.
- **The world fakes only the network.** `world.py` replaces GitHub (`count`, `get` and
  `collectIssue` on a `CollectionService` subclass), the classifier (`classifyOne`) and
  the npm client, and runs the real services over the seeded database. An earlier version
  faked the services themselves; a real model saw their reports contradict the database
  (45 collected, 3 in the table) and spent its turns investigating. Seed issues are
  bulk-inserted because the store commits per row, which would make a 20,000-issue
  backlog take about 40 seconds to build.
- **Rejected calls exclude forced errors.** The world counts the errors it forces,
  including `no_classifications` in a `classified: false` world, so an error the scenario
  set up is not held against the model, while a bad argument, an unknown tool or a
  repository that does not exist is. Some scenarios set up an error the real services
  raise on their own, such as a package name the user mistyped or a missing CSV; they list
  its code in the world's `expected_errors`, and the runner leaves calls with that code
  out of the count. A code the world already forces may not be listed, so no error is
  subtracted twice.
- **Tracing is off by default.** `.env` may set `LANGCHAIN_TRACING=true` for the chat
  agent, so the whole run executes inside `tracing_context(enabled=False)`. `--langsmith` opts
  in; its client is created after `load_settings` has read `.env`.
- **The legacy tracing flag is hidden while untraced.** `LANGCHAIN_TRACING` is LangChain's
  retired V1 switch. LangSmith honours it as V2, but `langchain_core` refuses any model
  call that sees it while V2 tracing is off, which is exactly the untraced state. The
  untraced session removes it (and `LANGCHAIN_HANDLER`) from the process environment for
  its duration and restores it afterwards; `.env` is not touched.
- **Ollama runs get their own context size.** Ollama silently drops the start of a prompt
  longer than `num_ctx`, which is the system prompt. The tool's default of 131072 does not
  fit a 6 GB card, so the eval passes `--num-ctx` (16384) and marks a run errored when its
  largest prompt, estimated at 3 characters per token, would not fit.
- **The budget is per model and counts everything.** Spend is summed over every record
  in the results folder, superseded attempts included, and checked before each scenario.
  An OpenRouter model whose price cannot be looked up is skipped, because its budget could
  not be enforced.
- **Resume refuses a changed evaluation.** The manifest keeps hashes of `scenarios.yaml`,
  the rendered system prompt and the tool schemas (line endings normalised), and the
  temperature; resuming with any of them changed would mix two evaluations.

## Commands

| Command | What it does |
|---|---|
| `python check.py` | Format check, lint and tests: run before every commit |
| `python -m pytest` | All tests |
| `python -m ruff format .` | Reformat every file |
| `python -m inex --help` | The command line |
| `python -m inex chat` | The chat agent |
| `python -m evals` | Agent evaluation; results in `evals/results/` |

## Decisions still open

- **Splitting the two excepted files**, as described above.
