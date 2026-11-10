import os
import sys
from contextlib import contextmanager

import langsmith
from langsmith import Client

FEEDBACK_KEY = "passed"
LEGACY_TRACING = ("LANGCHAIN_TRACING", "LANGCHAIN_HANDLER")


class Untraced:
    @contextmanager
    def session(self):
        hidden = {name: os.environ.pop(name) for name in LEGACY_TRACING if name in os.environ}
        try:
            with langsmith.tracing_context(enabled=False):
                yield
        finally:
            os.environ.update(hidden)

    @contextmanager
    def scenario(self, model, scenario, run):
        yield None

    def feedback(self, handle, trace) -> None:
        return None


class LangSmithTracer:
    def __init__(self, client, project):
        self.client = client
        self.project = project
        self.rejected = False

    def session(self):
        return langsmith.tracing_context(
            enabled=True, project_name=self.project, client=self.client
        )

    def scenario(self, model, scenario, run):
        return langsmith.trace(
            name=f"{scenario.id} #{run}",
            project_name=self.project,
            tags=[model, scenario.id, scenario.category, f"run-{run}"],
            metadata={
                "model": model,
                "scenario": scenario.id,
                "category": scenario.category,
                "run": run,
            },
            client=self.client,
        )

    def feedback(self, handle, trace) -> None:
        if self.rejected or handle is None:
            return
        try:
            self.client.create_feedback(
                run_id=handle.id,
                key=FEEDBACK_KEY,
                score=int(trace.outcome == "passed"),
                comment=trace.outcome,
            )
        except Exception as error:
            self.rejected = True
            print(f"LangSmith rejected the feedback ({error}); continuing locally", file=sys.stderr)


def make_tracer(enabled, project):
    if enabled:
        return LangSmithTracer(Client(), project)
    return Untraced()
