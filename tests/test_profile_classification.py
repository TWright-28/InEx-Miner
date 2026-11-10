import json
from pathlib import Path

import pytest
from conftest import FULL_PROBS

from inex.agent.chat import render_system_prompt
from inex.app import build_app
from inex.errors import InexError
from inex.services.classification import ClassificationService, ParsedResult

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_inex_prompts_are_unchanged(inex_profile):
    issue = json.loads((FIXTURES / "sample_issue.json").read_text(encoding="utf-8"))
    for model, fixture in (
        ("qwen3:30b", "inex_prompt_ollama.txt"),
        ("google_genai:gemini-2.5-pro", "inex_prompt_cloud.txt"),
    ):
        cl = ClassificationService(None, inex_profile, model, 0.2)
        rendered = cl.base_prompt + "\n" + cl.formatIssueData(issue)
        assert rendered == (FIXTURES / fixture).read_text(encoding="utf-8")


def test_prompt_version_names_the_profile(inex_profile):
    version = ClassificationService(None, inex_profile, "qwen3:30b", 0.2).prompt_version
    assert version.startswith("inex@")
    assert version.endswith("+t2")


def test_prompt_version_ignores_line_endings(tmp_path, inex_profile):
    from inex.config import REPO_ROOT
    from inex.services.profiles import load_profile

    for name in ("inex.yaml", "inex_guide.md"):
        text = (REPO_ROOT / "profiles" / name).read_text(encoding="utf-8")
        (tmp_path / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    crlf = load_profile(tmp_path / "inex.yaml")
    lf_version = ClassificationService(None, inex_profile, "qwen3:30b", 0.2).prompt_version
    crlf_version = ClassificationService(None, crlf, "qwen3:30b", 0.2).prompt_version
    assert crlf_version == lf_version


def test_profiles_get_distinct_prompt_versions(inex_profile, severity_profile):
    a = ClassificationService(None, inex_profile, "qwen3:30b", 0.2).prompt_version
    b = ClassificationService(None, severity_profile, "qwen3:30b", 0.2).prompt_version
    assert a != b and b.startswith("example-severity@")


def test_validation_uses_a_three_label_profile(severity_profile):
    cl = ClassificationService(None, severity_profile, "qwen3:30b", 0.2)
    good = cl.validateResult(
        ParsedResult(label="Major", probs={"Critical": 0.1, "Major": 0.8, "Minor": 0.1})
    )
    assert good.status == "ok"
    inex_label = cl.validateResult(ParsedResult(label="Intrinsic", probs=dict(FULL_PROBS)))
    assert inex_label.status == "invalid_result"


def test_severity_markdown_task_lists_its_own_labels_and_steps(severity_profile):
    cl = ClassificationService(None, severity_profile, "qwen3:30b", 0.2)
    task = cl.formatIssueData({"owner": "a", "repo": "b", "number": 1, "comments": []})
    assert "**Impact** - Describe what breaks for the user and how badly" in task
    assert "- Critical: 0.XX\n- Major: 0.XX\n- Minor: 0.XX" in task
    assert "**Final Answer:** [One of: Critical / Major / Minor]" in task
    assert "Intrinsic" not in task


def test_ollama_answer_is_read_with_the_profile(severity_profile):
    cl = ClassificationService(None, severity_profile, "qwen3:30b", 0.2)
    text = "- Critical: 0.1\n- Major: 0.7\n- Minor: 0.2\n**Final Answer:** major"
    assert cl.extractLabel(text) == "Major"
    assert cl.extractProbabilities(text) == {"Critical": 0.1, "Major": 0.7, "Minor": 0.2}


def test_a_bad_profile_fails_at_build_time(settings_with_bad_profile):
    with pytest.raises(InexError) as e:
        build_app(settings_with_bad_profile)
    assert e.value.code == "profile_invalid"


def test_app_carries_the_profile(settings):
    app = build_app(settings)
    assert app.profile.name == "inex-staged"
    assert app.classification.profile is app.profile
    app.close()


def test_system_prompt_lists_the_profile_labels(inex_profile):
    out = render_system_prompt("before\n{{TAXONOMY}}\nafter", inex_profile)
    assert "  - Not a Bug: Not a defect in production code." in out
    assert "{{TAXONOMY}}" not in out
    assert out.startswith("before\n") and out.endswith("\nafter")


def test_shipped_system_prompt_has_the_placeholder(settings, inex_profile):
    template = settings.system_prompt.read_text(encoding="utf-8")
    assert "{{TAXONOMY}}" in template
    assert "Intrinsic" not in template
