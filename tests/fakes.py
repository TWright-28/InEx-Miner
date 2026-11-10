import uuid
from dataclasses import replace

import requests
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from evals.__main__ import main
from evals.results import Results
from inex.app import build_app
from inex.errors import InexError
from inex.services.classification import ClassifyPreview, ClassifyResult
from inex.services.collection import CollectResult, CountResult, SpecificResult
from inex.services.imports import ImportService
from inex.services.linkage import LinkResult

PRICE_PAYLOAD = {
    "data": [
        {
            "id": "anthropic/claude-haiku-5.5",
            "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
        },
        {"id": "broken/model", "pricing": {}},
    ]
}


class FakeCollection:
    def __init__(self, count=14779, error=None):
        self.calls = []
        self.issue_count = count
        self.error = error

    def count(self, owner, repo):
        self.calls.append(("count", owner, repo))
        if (owner, repo) == ("nope", "nope"):
            raise InexError("repo_not_found", "nope/nope was not found on GitHub.")
        return CountResult(owner, repo, self.issue_count, stored=0)

    def collect(self, owner, repo, **kwargs):
        self.calls.append(("collect", owner, repo, kwargs))
        if self.error:
            raise self.error
        return CollectResult(collected=10, skipped=0, total=10)

    def collect_specific(self, owner, repo, numbers):
        self.calls.append(("collect_specific", owner, repo, list(numbers)))
        return SpecificResult(1, tuple(range(1, len(numbers) + 1)), len(numbers), 0, 0)


class FakeClassification:
    def __init__(self):
        self.calls = []
        self.judge = None

    def preview(self, owner, repo, **kwargs):
        self.calls.append(("preview", owner, repo, kwargs))
        return ClassifyPreview(owner, repo, 10, 10, "m", "p", None, None, ())

    def classify(self, owner, repo, **kwargs):
        self.calls.append(("classify", owner, repo, kwargs))
        return ClassifyResult(owner, repo, classified=10, failed={})

    def classify_ids(self, owner, repo, issue_ids):
        self.calls.append(("classify_ids", owner, repo, list(issue_ids)))
        return ClassifyResult(owner, repo, classified=len(issue_ids), failed={})


class FakeLinkage:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def link(self, owner, repo, package, **kwargs):
        self.calls.append(("link", owner, repo, package, kwargs))
        if self.error:
            raise self.error
        return LinkResult(package, 1, 0, 1, 0, 0)


def fake_app(settings, **services):
    defaults = {
        "collection": FakeCollection(),
        "classification": FakeClassification(),
        "linkage": FakeLinkage(),
    }
    defaults.update(services)
    app = replace(build_app(settings), **defaults)
    imports = ImportService(app.queries.store, app.collection, app.classification)
    return replace(app, imports=imports)


def _usage(input_tokens, output_tokens):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def ai_call(*calls, input_tokens=100, output_tokens=10):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": uuid.uuid4().hex, "type": "tool_call"}
            for name, args in calls
        ],
        usage_metadata=_usage(input_tokens, output_tokens),
    )


def ai_reply(text, input_tokens=100, output_tokens=10):
    return AIMessage(content=text, usage_metadata=_usage(input_tokens, output_tokens))


class ScriptedChatModel(BaseChatModel):
    script: list

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def scripted_builder(replies=20, fail=None):
    def build(settings):
        if fail is not None and settings.orchestrator == fail[0]:
            raise fail[1]
        return ScriptedChatModel(
            script=[ai_reply("OK")] + [ai_reply("done") for _ in range(replies)]
        )

    return build


def run_main(tmp_path, monkeypatch, argv, **kwargs):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "results"
    code = main(argv, results_root=root, wait=lambda seconds: None, **kwargs)
    folders = sorted(root.iterdir()) if root.exists() else []
    return code, Results.open(folders[-1]) if folders else None
