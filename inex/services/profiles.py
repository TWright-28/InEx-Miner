import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, create_model

from inex.errors import InexError

RESERVED_FIELDS = ("probabilities", "final_answer", "confidence")
MIN_LABELS = 2
STAGE_NAMES = ("validity", "recover", "origin")
VALIDITY_LABELS = 2
NUMBER_WORDS = {
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
}
SLUG_SEPARATOR = re.compile(r"[^a-z0-9]+")
WORD_GAP = r"[\s-]*"
FINAL_ANSWER = r"\*\*Final\s*Answer:\*\*\s*"
NUMBER = r"([0-9]*\.?[0-9]+)"


@dataclass(frozen=True)
class Label:
    name: str
    description: str
    slug: str


@dataclass(frozen=True)
class ReasoningStep:
    field: str
    title: str
    description: str


@dataclass(frozen=True)
class Stage:
    name: str
    guide_path: Path
    guide_text: str
    labels: tuple


@dataclass(frozen=True)
class Stages:
    validity: Stage
    recover: Stage | None
    origin: Stage


@dataclass(frozen=True)
class Profile:
    name: str
    version: int
    description: str
    guide_path: Path | None
    guide_text: str
    labels: tuple
    reasoning: tuple
    confidence: tuple | None
    raw_bytes: bytes
    stages: Stages | None = None


def slugify(name) -> str:
    return SLUG_SEPARATOR.sub("_", str(name).strip().lower()).strip("_")


def count_word(n) -> str:
    return NUMBER_WORDS.get(n, str(n))


def _invalid(path, message):
    raise InexError("profile_invalid", f"Profile {path}: {message}", path=str(path))


def _labels(path, raw):
    if not isinstance(raw, list) or len(raw) < MIN_LABELS:
        _invalid(path, f"needs at least two labels, found {len(raw or [])}.")
    labels = []
    seen_names = set()
    by_slug = {}
    for entry in raw:
        if not isinstance(entry, dict):
            _invalid(path, f"label {entry!r} must be a mapping with a name and a description.")
        raw_name = entry.get("name")
        if not isinstance(raw_name, str):
            _invalid(path, f"label name {raw_name!r} is not text; put it in quotes.")
        name = raw_name.strip()
        if not name:
            _invalid(path, "every label needs a name.")
        if name in seen_names:
            _invalid(path, f"label {name!r} is listed twice.")
        slug = slugify(name)
        if not slug:
            _invalid(path, f"label {name!r} needs at least one ASCII letter or digit.")
        if slug in by_slug:
            _invalid(path, f"labels {by_slug[slug]!r} and {name!r} collide as {slug!r}.")
        seen_names.add(name)
        by_slug[slug] = name
        labels.append(Label(name, str(entry.get("description", "")).strip(), slug))
    return tuple(labels)


def _reasoning(path, raw):
    if not isinstance(raw, list) or not raw:
        _invalid(path, "needs at least one reasoning step.")
    steps = []
    seen = set()
    for entry in raw:
        if not isinstance(entry, dict):
            _invalid(path, f"reasoning step {entry!r} must be a mapping with a field.")
        raw_field = entry.get("field")
        if not isinstance(raw_field, str):
            _invalid(path, f"reasoning field {raw_field!r} is not text; put it in quotes.")
        field = raw_field.strip()
        if not field.isidentifier():
            _invalid(path, f"reasoning field {field!r} is not a valid identifier.")
        if field.startswith("_"):
            _invalid(path, f"reasoning field {field!r} must not start with an underscore.")
        if field in RESERVED_FIELDS:
            _invalid(path, f"reasoning field {field!r} is reserved.")
        if field in seen:
            _invalid(path, f"reasoning field {field!r} is listed twice.")
        seen.add(field)
        title = str(entry.get("title") or field.replace("_", " ").title())
        steps.append(ReasoningStep(field, title, str(entry.get("description", "")).strip()))
    return tuple(steps)


def _stage_labels(path, name, raw):
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        _invalid(path, f"the {name} stage needs a list of labels.")
    return tuple(x.strip() for x in raw)


def _stage(path, name, raw, labels=None):
    if not isinstance(raw, dict):
        _invalid(path, f"the {name} stage must be a mapping with a guide.")
    guide_name = raw.get("guide")
    if not isinstance(guide_name, str) or not guide_name.strip():
        _invalid(path, f"the {name} stage needs a guide file name.")
    guide_path = path.parent / guide_name
    try:
        guide_text = guide_path.read_text(encoding="utf-8")
    except OSError:
        _invalid(path, f"the {name} stage guide {guide_name} cannot be read.")
    if labels is None:
        labels = _stage_labels(path, name, raw.get("labels"))
    return Stage(name, guide_path, guide_text, labels)


def _stages(path, raw, labels):
    if not isinstance(raw, dict):
        _invalid(path, "stages must be a mapping of validity, recover and origin.")
    unknown = [name for name in raw if name not in STAGE_NAMES]
    if unknown:
        _invalid(path, f"unknown stage {unknown[0]!r}; stages are validity, recover and origin.")
    for name in ("validity", "origin"):
        if name not in raw:
            _invalid(path, f"needs a {name} stage.")
    validity = _stage(path, "validity", raw["validity"])
    if len(validity.labels) != VALIDITY_LABELS:
        _invalid(path, "the validity stage needs two labels: the defect verdict, then the other.")
    names = [label.name for label in labels]
    if validity.labels[1] not in names:
        _invalid(
            path,
            f"the validity stage's second label {validity.labels[1]!r} must be a profile label.",
        )
    origin = _stage(path, "origin", raw["origin"])
    if sorted((*origin.labels, validity.labels[1])) != sorted(names):
        _invalid(
            path,
            f"the origin stage labels and {validity.labels[1]!r} must be exactly the profile labels.",
        )
    recover = None
    if "recover" in raw:
        recover = _stage(path, "recover", raw["recover"], labels=validity.labels)
    return Stages(validity, recover, origin)


def load_profile(path: Path) -> Profile:
    path = Path(path)
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        _invalid(path, "the file cannot be read.")
    try:
        document = yaml.safe_load(raw_bytes) or {}
    except yaml.YAMLError as error:
        _invalid(path, f"invalid YAML: {error}")
    if not isinstance(document, dict) or not document.get("name"):
        _invalid(path, "needs a name.")
    staged = "stages" in document
    if staged:
        if "guide" in document:
            _invalid(path, "a staged profile takes its guides from its stages; remove guide.")
        if "reasoning" in document:
            _invalid(path, "a staged profile has no reasoning steps; its guides set the format.")
        guide_path, guide_text = None, ""
    else:
        guide_name = document.get("guide")
        if not isinstance(guide_name, str) or not guide_name.strip():
            _invalid(path, "needs a guide file name.")
        guide_path = path.parent / guide_name
        try:
            guide_text = guide_path.read_text(encoding="utf-8")
        except OSError:
            _invalid(path, f"guide file {guide_name} cannot be read.")
    try:
        version = int(document.get("version", 1))
    except (TypeError, ValueError):
        _invalid(path, f"version must be a whole number, got {document.get('version')!r}.")
    confidence = None
    if "confidence" in document:
        levels = document["confidence"]
        if not isinstance(levels, list) or not all(isinstance(level, str) for level in levels):
            _invalid(path, "confidence must be a list of levels, like [High, Medium, Low].")
        confidence = tuple(levels)
        if not confidence:
            _invalid(path, "confidence is present but empty.")
    labels = _labels(path, document.get("labels"))
    profile = Profile(
        name=str(document["name"]),
        version=version,
        description=str(document.get("description", "")),
        guide_path=guide_path,
        guide_text=guide_text,
        labels=labels,
        reasoning=() if staged else _reasoning(path, document.get("reasoning")),
        confidence=confidence,
        raw_bytes=raw_bytes,
        stages=_stages(path, document["stages"], labels) if staged else None,
    )
    try:
        build_schema(profile)
    except Exception as error:
        _invalid(path, f"cannot build a response schema from it: {error}")
    return profile


def build_schema(profile):
    probabilities = create_model(
        "_Probabilities",
        **{label.slug: (float, Field(ge=0.0, le=1.0)) for label in profile.labels},
    )
    fields = {step.field: (str, ...) for step in profile.reasoning}
    if profile.confidence:
        fields["confidence"] = (Literal[profile.confidence], ...)
    fields["probabilities"] = (probabilities, ...)
    fields["final_answer"] = (Literal[tuple(label.name for label in profile.labels)], ...)
    return create_model("ClassificationResult", **fields)


def _alternatives(profile):
    names = sorted((label.name for label in profile.labels), key=len, reverse=True)
    return "|".join(WORD_GAP.join(re.escape(word) for word in name.split()) for name in names)


def _canonical(profile, text):
    slug = slugify(text)
    return next((label.name for label in profile.labels if label.slug == slug), None)


def match_label(profile, text):
    if not text:
        return None
    match = re.search(f"{FINAL_ANSWER}({_alternatives(profile)})", text, re.IGNORECASE)
    return _canonical(profile, match.group(1)) if match else None


def match_probabilities(profile, text):
    if not text:
        return {}
    pattern = re.compile(rf"-\s*({_alternatives(profile)})\s*:\s*{NUMBER}", re.IGNORECASE)
    probabilities = {}
    for name, value in pattern.findall(text):
        label = _canonical(profile, name)
        if label:
            probabilities[label] = float(value)
    return probabilities
