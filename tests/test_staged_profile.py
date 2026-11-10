from pathlib import Path

import pytest

from inex.errors import InexError
from inex.services.profiles import load_profile

REPO_ROOT = Path(__file__).resolve().parents[1]

LABELS = """labels:
  - name: Intrinsic
    description: inside
  - name: Extrinsic
    description: outside
  - name: Not a Bug
    description: no defect
"""

VALIDITY = """  validity:
    guide: v.md
    labels: [Bug, Not a Bug]
"""

RECOVER = """  recover:
    guide: r.md
"""

ORIGIN = """  origin:
    guide: o.md
    labels: [Intrinsic, Extrinsic]
"""


def _write(tmp_path, stages, extra="", labels=LABELS):
    for name in ("v.md", "r.md", "o.md"):
        (tmp_path / name).write_text(f"# guide {name}\n", encoding="utf-8")
    path = tmp_path / "staged.yaml"
    path.write_text(f"name: staged\n{extra}{labels}stages:\n{stages}", encoding="utf-8")
    return path


def _rejected(path):
    with pytest.raises(InexError) as caught:
        load_profile(path)
    assert caught.value.code == "profile_invalid"
    return caught.value.message


def test_bundled_staged_profile_loads():
    p = load_profile(REPO_ROOT / "profiles" / "inex-staged.yaml")
    assert [label.name for label in p.labels] == ["Intrinsic", "Extrinsic", "Not a Bug"]
    assert p.stages.validity.labels == ("Bug", "Not a Bug")
    assert p.stages.recover.labels == ("Bug", "Not a Bug")
    assert p.stages.origin.labels == ("Intrinsic", "Extrinsic")
    assert p.stages.validity.guide_text.startswith("# BUG CLASSIFICATION GUIDE - STAGE 1")
    assert p.stages.recover.guide_text.startswith("# BUG RE-VERIFICATION")
    assert p.stages.origin.guide_text.startswith("# INTRINSIC vs EXTRINSIC")
    assert p.guide_text == "" and p.guide_path is None and p.reasoning == ()


def test_single_call_profiles_have_no_stages():
    assert load_profile(REPO_ROOT / "profiles" / "inex.yaml").stages is None


def test_recover_is_optional(tmp_path):
    p = load_profile(_write(tmp_path, VALIDITY + ORIGIN))
    assert p.stages.recover is None
    assert p.stages.origin.guide_text == "# guide o.md\n"


def test_missing_origin_is_rejected(tmp_path):
    assert "origin" in _rejected(_write(tmp_path, VALIDITY + RECOVER))


def test_unknown_stage_is_rejected(tmp_path):
    guard = "  guard:\n    guide: r.md\n"
    assert "guard" in _rejected(_write(tmp_path, VALIDITY + RECOVER + ORIGIN + guard))


def test_unreadable_stage_guide_is_rejected(tmp_path):
    path = _write(tmp_path, VALIDITY + RECOVER + ORIGIN)
    (tmp_path / "r.md").unlink()
    assert "r.md" in _rejected(path)


def test_validity_needs_two_labels(tmp_path):
    one = "  validity:\n    guide: v.md\n    labels: [Bug]\n"
    assert "validity" in _rejected(_write(tmp_path, one + ORIGIN))


def test_stage_labels_must_cover_the_profile_labels(tmp_path):
    short = "  origin:\n    guide: o.md\n    labels: [Intrinsic]\n"
    assert "origin" in _rejected(_write(tmp_path, VALIDITY + short))


def test_not_a_bug_label_must_be_a_profile_label(tmp_path):
    odd = "  validity:\n    guide: v.md\n    labels: [Bug, Invalid]\n"
    assert "validity" in _rejected(_write(tmp_path, odd + ORIGIN))


def test_staged_profile_rejects_a_top_level_guide(tmp_path):
    message = _rejected(_write(tmp_path, VALIDITY + ORIGIN, extra="guide: v.md\n"))
    assert "guide" in message


def test_staged_profile_rejects_reasoning(tmp_path):
    reasoning = "reasoning:\n  - field: decision\n"
    assert "reasoning" in _rejected(_write(tmp_path, VALIDITY + ORIGIN, extra=reasoning))
