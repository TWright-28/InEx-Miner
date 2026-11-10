import json
from pathlib import Path

import pytest

from inex.config import REPO_ROOT
from inex.errors import InexError
from inex.services.profiles import (
    build_schema,
    count_word,
    load_profile,
    match_label,
    match_probabilities,
    slugify,
)

INEX = REPO_ROOT / "profiles" / "inex.yaml"
SEVERITY = REPO_ROOT / "profiles" / "example-severity.yaml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

VALID = """name: tiny
version: 1
guide: guide.md
labels:
  - name: Accept
    description: It is.
  - name: Reject
    description: It is not.
reasoning:
  - field: evidence
    title: Evidence
    description: What supports the answer
"""


def write_profile(tmp_path, text, guide=True):
    if guide:
        (tmp_path / "guide.md").write_text("Guide text.", encoding="utf-8")
    path = tmp_path / "profile.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def rejects(tmp_path, text, *expected, guide=True):
    with pytest.raises(InexError) as e:
        load_profile(write_profile(tmp_path, text, guide))
    assert e.value.code == "profile_invalid"
    for fragment in expected:
        assert fragment in e.value.message


def test_golden_schema_is_byte_identical():
    generated = json.dumps(build_schema(load_profile(INEX)).model_json_schema(), indent=2)
    assert generated == (FIXTURES / "inex_schema.json").read_text(encoding="utf-8")


def test_reasoning_fields_precede_probabilities_and_answer():
    assert list(build_schema(load_profile(INEX)).model_fields) == [
        "definition_check",
        "maintainer_signal",
        "pr_commit_evidence",
        "temporal_signals",
        "contract_rule",
        "information_completeness",
        "decision",
        "confidence",
        "probabilities",
        "final_answer",
    ]


def test_profile_without_confidence_has_no_confidence_field(tmp_path):
    schema = build_schema(load_profile(write_profile(tmp_path, VALID)))
    assert list(schema.model_fields) == ["evidence", "probabilities", "final_answer"]


@pytest.mark.parametrize(
    "name, slug",
    [("Not a Bug", "not_a_bug"), ("Intrinsic", "intrinsic"), ("  P1 - Critical ", "p1_critical")],
)
def test_slugify(name, slug):
    assert slugify(name) == slug


@pytest.mark.parametrize(
    "text, label",
    [
        ("**Final Answer:** Not a Bug", "Not a Bug"),
        ("**Final Answer:** not-a-bug", "Not a Bug"),
        ("**Final Answer:**  NOT A BUG", "Not a Bug"),
        ("**Final Answer:** Extrinsic", "Extrinsic"),
        ("no answer here", None),
    ],
)
def test_match_label(text, label):
    assert match_label(load_profile(INEX), text) == label


def test_match_probabilities():
    text = "- Intrinsic: 0.9\n- Not a Bug: 0.05\n- Extrinsic: .05\n- Bogus: 0.3"
    assert match_probabilities(load_profile(INEX), text) == {
        "Intrinsic": 0.9,
        "Not a Bug": 0.05,
        "Extrinsic": 0.05,
    }


def test_example_profile_loads_with_three_labels():
    assert [label.name for label in load_profile(SEVERITY).labels] == [
        "Critical",
        "Major",
        "Minor",
    ]


def test_inex_profile_carries_its_guide_and_bytes():
    profile = load_profile(INEX)
    assert profile.name == "inex"
    assert profile.guide_text.startswith(
        (REPO_ROOT / "profiles" / "inex_guide.md").read_text(encoding="utf-8")[:40]
    )
    assert profile.raw_bytes == INEX.read_bytes()


def test_count_word():
    assert (count_word(4), count_word(3), count_word(11)) == ("four", "three", "11")


def test_valid_profile_loads(tmp_path):
    assert load_profile(write_profile(tmp_path, VALID)).name == "tiny"


def test_fewer_than_two_labels(tmp_path):
    text = VALID.replace("  - name: Reject\n    description: It is not.\n", "")
    rejects(tmp_path, text, "two labels")


def test_duplicate_label_names(tmp_path):
    rejects(tmp_path, VALID.replace("name: Reject", "name: Accept"), "Accept")


def test_colliding_slugs_name_both_labels(tmp_path):
    text = VALID.replace("name: Accept", "name: Not a Bug").replace(
        "name: Reject", "name: not-a-bug"
    )
    rejects(tmp_path, text, "Not a Bug", "not-a-bug")


def test_no_reasoning_steps(tmp_path):
    rejects(tmp_path, VALID.split("reasoning:")[0] + "reasoning: []\n", "reasoning")


def test_duplicate_reasoning_field(tmp_path):
    step = "  - field: evidence\n    title: Again\n    description: Again\n"
    rejects(tmp_path, VALID + step, "evidence")


def test_non_identifier_field(tmp_path):
    rejects(tmp_path, VALID.replace("field: evidence", "field: 2bad"), "2bad")


@pytest.mark.parametrize("reserved", ["probabilities", "final_answer", "confidence"])
def test_reserved_field_names(tmp_path, reserved):
    rejects(tmp_path, VALID.replace("field: evidence", f"field: {reserved}"), reserved)


def test_missing_guide(tmp_path):
    rejects(tmp_path, VALID, "guide.md", guide=False)


def test_empty_confidence(tmp_path):
    rejects(tmp_path, VALID + "confidence: []\n", "confidence")


def test_invalid_yaml(tmp_path):
    rejects(tmp_path, "name: [unclosed\n", "YAML")


def test_unquoted_yaml_booleans_are_rejected_as_label_names(tmp_path):
    text = VALID.replace("name: Accept", "name: Yes").replace("name: Reject", "name: No")
    rejects(tmp_path, text, "quotes")


MALFORMED = [
    (
        VALID.split("labels:")[0]
        + "labels: [Bug, Feature]\nreasoning:"
        + VALID.split("reasoning:")[1],
        "label",
    ),
    (VALID.replace("version: 1", "version: v2"), "version"),
    (VALID.replace("guide: guide.md", "guide: [guide.md]"), "guide"),
    (VALID.replace("name: Accept", "name: Model Config"), "schema"),
    (VALID.replace("field: evidence", "field: model_config"), "schema"),
    (VALID + "confidence: High, Medium, Low\n", "confidence"),
    (VALID.replace("field: evidence", "field: _private"), "_private"),
    (VALID.replace("name: Accept", "name: Ошибка"), "Ошибка"),
]


@pytest.mark.parametrize("text, fragment", MALFORMED)
def test_malformed_profiles_are_rejected_with_a_reason(tmp_path, text, fragment):
    rejects(tmp_path, text, fragment)


def test_missing_profile_file(tmp_path):
    with pytest.raises(InexError) as e:
        load_profile(tmp_path / "nope.yaml")
    assert e.value.code == "profile_invalid"
