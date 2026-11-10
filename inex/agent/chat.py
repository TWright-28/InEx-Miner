import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from langchain.agents import create_agent
from langgraph.checkpoint.sqlite import SqliteSaver

from inex.agent.display import call_line, result_line, tool_result
from inex.agent.tools import build_tools
from inex.errors import InexError

QUIT = "q"
TAXONOMY_PLACEHOLDER = "{{TAXONOMY}}"
THREAD_LABEL_CHARS = 8
INSTALL_COMMAND = re.compile(r"pip install [\w.\[\]-]+")
PROVIDER_PACKAGES = {"openrouter": "langchain-openai"}
OLLAMA_MAX_OUTPUT_TOKENS = 8192
MAX_ERROR_CHARS = 200
PROGRESS_LOGGER = "inex.progress"
PROGRESS_FORMAT = "    %(message)s"
BANNER_LABEL_WIDTH = 14
TRANSCRIPT_STAMP = "%Y-%m-%d-%H%M%S"
ISSUES_URL = "https://github.com/TWright-28/InEx-Miner/issues"
WELCOME_EXAMPLES = (
    ("count", "How many issues does axios/axios have?"),
    ("collect", "Collect the 100 most recent issues from axios/axios"),
    ("classify", "Classify them as {labels}"),
    ("link", "Link them to the npm releases they were filed against"),
    ("import", "Collect and classify the issues listed in issues.csv"),
    ("analyse", "Which label is most common in axios/axios?"),
    ("export", "Export the labels to CSV"),
)
WELCOME_WIDTH = 11

logger = logging.getLogger(__name__)


def render_system_prompt(template, profile):
    taxonomy = "\n".join(f"  - {label.name}: {label.description}" for label in profile.labels)
    return template.replace(TAXONOMY_PLACEHOLDER, taxonomy)


def _missing_package(settings, provider, error):
    found = INSTALL_COMMAND.search(str(error))
    package = PROVIDER_PACKAGES.get(provider, f"langchain-{provider}")
    command = found.group(0) if found else f"pip install {package}"
    return InexError(
        "missing_package",
        f"{settings.orchestrator} needs a package that is not installed. "
        f"Install it with: {command}",
    )


def build_llm(settings):
    provider, _, model_name = settings.orchestrator.partition(":")
    try:
        if provider == "ollama":
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=model_name,
                base_url=settings.ollama_base,
                temperature=settings.temperature,
                num_ctx=settings.num_ctx,
                num_predict=OLLAMA_MAX_OUTPUT_TOKENS,
            )
        if provider == "openrouter":
            if not settings.openrouter_api_key:
                raise InexError("missing_api_key", "OPENROUTER_API_KEY is not set; add it to .env")
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=model_name,
                base_url=settings.openrouter_base,
                api_key=settings.openrouter_api_key,
                temperature=settings.temperature,
            )
        from langchain.chat_models import init_chat_model

        return init_chat_model(settings.orchestrator, temperature=settings.temperature)
    except ImportError as error:
        raise _missing_package(settings, provider, error) from error


def build_agent(app, checkpointer, llm=None):
    return create_agent(
        model=llm if llm is not None else build_llm(app.settings),
        tools=build_tools(app),
        system_prompt=render_system_prompt(
            app.settings.system_prompt.read_text(encoding="utf-8"), app.profile
        ),
        checkpointer=checkpointer,
    )


def reply_text(message) -> str:
    return message.text


def _brief(error):
    first_line = (str(error).strip().splitlines() or [""])[0][:MAX_ERROR_CHARS]
    return f"{type(error).__name__}: {first_line}" if first_line else type(error).__name__


def _messages(update):
    for value in update.values():
        if isinstance(value, dict):
            yield from value.get("messages", [])


def _step(message, started, show, clock):
    if message.type == "tool":
        now = clock()
        seconds = now - started.pop(message.tool_call_id, now)
        show(result_line(message.name, tool_result(message.content), seconds))
        return None
    if message.type != "ai":
        return None
    for call in message.tool_calls:
        started[call["id"]] = clock()
        show(call_line(call["name"], call["args"]))
    return None if message.tool_calls else reply_text(message)


def ask(agent, question, config, show=print, clock=time.monotonic) -> str:
    payload = {"messages": [{"role": "user", "content": question}]}
    started = {}
    reply = ""
    try:
        for update in agent.stream(payload, config=config, stream_mode="updates"):
            for message in _messages(update):
                text = _step(message, started, show, clock)
                reply = reply if text is None else text
    except Exception as error:
        logger.exception("The orchestrator call failed")
        return (
            f"The model call failed ({_brief(error)}). "
            "The conversation is kept; send your message again."
        )
    return reply


class Transcript:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def write(self, line):
        self._file.write(f"{line}\n")
        self._file.flush()

    def show(self, line):
        print(line, flush=True)
        self.write(line)

    def close(self):
        self._file.close()


class _TranscriptHandler(logging.Handler):
    def __init__(self, transcript):
        super().__init__(logging.INFO)
        self.transcript = transcript
        self.setFormatter(logging.Formatter(PROGRESS_FORMAT))

    def emit(self, record):
        self.transcript.write(self.format(record))


def _shown(path):
    path = Path(path)
    try:
        return path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return path


def _log_files():
    root = logging.getLogger()
    return [Path(h.baseFilename) for h in root.handlers if isinstance(h, logging.FileHandler)]


def counted(count, word):
    return f"{count:,} {word}" if count == 1 else f"{count:,} {word}s"


def banner(app, thread_id, transcript_path):
    overview = app.queries.overview()
    rows = [
        (
            "database",
            f"{app.settings.database} ({counted(overview.projects, 'project')}, "
            f"{counted(overview.issues, 'issue')}, {overview.labelled:,} labelled)",
        ),
        ("orchestrator", app.settings.orchestrator),
        ("classifier", app.settings.classifier),
    ]
    if app.classification.judge:
        rows.append(("judge", app.classification.judge))
    rows.append(("profile", app.profile.name))
    rows += [("log", _shown(path)) for path in _log_files()]
    rows.append(("transcript", _shown(transcript_path)))
    title = f"InEx-Miner chat - thread {thread_id[:THREAD_LABEL_CHARS]} - type '{QUIT}' to quit"
    return [title, *(f"  {label:<{BANNER_LABEL_WIDTH}}{value}" for label, value in rows), ""]


def _either(names):
    names = list(names)
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


def welcome(labels):
    named = _either(labels)
    examples = [
        f"  {verb:<{WELCOME_WIDTH}}{text.format(labels=named)}" for verb, text in WELCOME_EXAMPLES
    ]
    return [
        "What I can do (just ask in plain words):",
        *examples,
        "I check with you before slow or costly steps.",
        f"Report a problem: {ISSUES_URL}",
        "",
    ]


def _transcript_path(app, thread_id):
    stamp = datetime.now().strftime(TRANSCRIPT_STAMP)
    return app.settings.transcripts / f"{stamp}-{thread_id[:THREAD_LABEL_CHARS]}.log"


def _converse(app, thread_id, transcript):
    app.settings.checkpoints.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(app.settings.checkpoints)) as checkpointer:
        agent = build_agent(app, checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        for line in banner(app, thread_id, transcript.path):
            transcript.show(line)
        for line in welcome(label.name for label in app.profile.labels):
            transcript.show(line)
        while True:
            question = _read_question()
            if question is None:
                return
            if not question.strip():
                continue
            transcript.write(f"You: {question}")
            if question.strip() == QUIT:
                return
            transcript.show(f"Agent: {ask(agent, question, config, show=transcript.show)}")


def _read_question():
    try:
        return input("You: ")
    except EOFError:
        print()
        return None


def run_chat(app) -> None:
    thread_id = str(uuid.uuid4())
    transcript = Transcript(_transcript_path(app, thread_id))
    progress = logging.getLogger(PROGRESS_LOGGER)
    handler = _TranscriptHandler(transcript)
    level = progress.level
    progress.setLevel(logging.DEBUG)
    progress.addHandler(handler)
    try:
        _converse(app, thread_id, transcript)
    finally:
        progress.removeHandler(handler)
        progress.setLevel(level)
        transcript.close()
        print(f"Transcript saved to {_shown(transcript.path)}")
