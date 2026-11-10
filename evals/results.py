import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from evals import report

FOLDER_FORMAT = "%Y%m%d-%H%M%S"
MANIFEST = "manifest.json"
TRACES = "traces.jsonl"
HASH_NAMES = {
    "scenarios": "scenarios.yaml",
    "system_prompt": "the rendered system prompt",
    "tools": "the tool descriptions",
}
PLURAL_HASHES = {"tools"}
FINISHED_OUTCOMES = ("passed", "failed")


def text_hash(text) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def git_state(cwd) -> tuple[str | None, bool | None]:
    try:
        commit = _git(cwd, "rev-parse", "HEAD")
        dirty = bool(_git(cwd, "status", "--porcelain"))
    except (OSError, subprocess.SubprocessError):
        return None, None
    return commit, dirty


def resume_problems(manifest, hashes, temperature) -> list[str]:
    problems = []
    recorded = manifest.get("hashes") or {}
    for key, name in HASH_NAMES.items():
        if recorded.get(key) != hashes.get(key):
            verb = "have" if key in PLURAL_HASHES else "has"
            problems.append(f"{name} {verb} changed since this run started")
    if manifest.get("temperature") != temperature:
        problems.append(f"the temperature is {temperature}, the run used {manifest['temperature']}")
    return problems


class Results:
    def __init__(self, path, manifest):
        self.path = Path(path)
        self.manifest = manifest

    @classmethod
    def create(cls, root, manifest, now) -> "Results":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        base = now.strftime(FOLDER_FORMAT)
        suffix = 1
        while True:
            path = root / (base if suffix == 1 else f"{base}-{suffix}")
            try:
                path.mkdir()
                break
            except FileExistsError:
                suffix += 1
        results = cls(path, manifest)
        results.save()
        return results

    @classmethod
    def open(cls, path) -> "Results":
        path = Path(path)
        manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
        return cls(path, manifest)

    def save(self) -> None:
        target = self.path / MANIFEST
        temporary = self.path / (MANIFEST + ".tmp")
        temporary.write_text(json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)

    def set_status(self, model, status) -> None:
        self.manifest.setdefault("status", {})[model] = status
        self.save()

    def _ends_without_newline(self):
        traces = self.path / TRACES
        if not traces.exists() or traces.stat().st_size == 0:
            return False
        with open(traces, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"

    def append(self, trace) -> None:
        line = json.dumps(asdict(trace), ensure_ascii=False, default=str)
        prefix = "\n" if self._ends_without_newline() else ""
        with open(self.path / TRACES, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(prefix + line + "\n")

    def records(self) -> list[dict]:
        traces = self.path / TRACES
        if not traces.exists():
            return []
        records = []
        text = traces.read_text(encoding="utf-8", errors="replace")
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def latest(self) -> dict:
        return {(r["model"], r["scenario"], r["run"]): r for r in report.latest(self.records())}

    def finished(self, model) -> set:
        return {
            (scenario, run)
            for (owner, scenario, run), record in self.latest().items()
            if owner == model and record["outcome"] in FINISHED_OUTCOMES
        }

    def spent(self, model) -> float:
        return sum(r.get("cost") or 0 for r in self.records() if r.get("model") == model)
