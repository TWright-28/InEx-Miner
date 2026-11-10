import json
from pathlib import Path

import pytest

from inex.services.profiles import load_profile
from inex.services.staged import (
    RECOVER_NAME,
    VALIDITY_NAME,
    build_prompt,
    format_issue,
    read_confidence,
    read_probabilities,
    read_verdict,
    run_pipeline,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "staged"
V_REPLY = "VALIDITY REPLY\n**Final Answer:** Not a Bug"
R_REPLY = "RECOVER REPLY\n**Final Answer:** Bug"
MODELS = {"stage": "s", "judge": "j"}
MINIMAL = {"owner": "a", "repo": "b", "number": 1, "title": "t", "body": "b"}


def read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def scripted(verdicts):
    calls = []

    def ask(prompt, role, labels):
        calls.append((role, labels, prompt))
        verdict = verdicts[len(calls) - 1]
        if isinstance(verdict, Exception):
            raise verdict
        return f"reply {len(calls)}\n**Final Answer:** {verdict}", verdict

    return ask, calls


def test_prompts_match_the_study_byte_for_byte(staged_profile):
    issue = json.loads(read("issue.json"))
    ctx = format_issue(issue)
    v = (VALIDITY_NAME, "Not a Bug", V_REPLY)
    r = (RECOVER_NAME, "Bug", R_REPLY)
    st = staged_profile.stages
    assert build_prompt(st.validity.guide_text, ctx) == read("validity.txt")
    assert build_prompt(st.recover.guide_text, ctx, [v]) == read("recover.txt")
    assert build_prompt(st.origin.guide_text, ctx, [v, r]) == read("origin.txt")


def test_null_fields_take_the_study_defaults():
    issue = {
        "owner": "a",
        "repo": "b",
        "number": 1,
        "title": None,
        "body": None,
        "created_at": None,
        "closed_at": None,
        "comments": None,
        "closing_pr": None,
        "closing_commit": {"message": None},
        "labels": None,
        "author": None,
        "closed_by": None,
    }
    text = format_issue(issue)
    assert "**Title:** No title" in text
    assert "No description provided" in text
    assert "**Created:** Unknown" in text
    assert "**Closed:** Not closed" in text
    assert "No comments" in text
    assert "**Closing Commit:** No message" in text


def test_null_comment_body_and_date_do_not_crash():
    comments = [
        {"author": {"username": "x", "author_association": "OWNER"}, "body": None},
        {"author": None, "body": None, "created_at": None},
        {"author": {"username": "y", "author_association": "NONE"}, "created_at": None},
    ]
    text = format_issue({**MINIMAL, "comments": comments})
    assert "**Comment 1** by x [OWNER] ():\n" in text
    assert "**Comment 3**" in text


def test_null_pr_title_takes_the_default():
    text = format_issue({**MINIMAL, "closing_pr": {"title": None, "merged": True}})
    assert "- Title: No title" in text


def test_empty_strings_are_kept():
    assert "**Title:** \n" in format_issue({**MINIMAL, "title": ""})


def test_read_verdict_is_case_insensitive_and_canonical():
    assert read_verdict(("Bug", "Not a Bug"), "**final answer:**  not a bug") == "Not a Bug"
    assert read_verdict(("Intrinsic", "Extrinsic"), "**Final Answer:**EXTRINSIC") == "Extrinsic"


def test_spacing_inside_a_label_follows_the_study():
    assert read_verdict(("Bug", "Not a Bug"), "**Final Answer:** Not a  Bug") is None


def test_colon_outside_bold_is_unreadable():
    assert read_verdict(("Bug", "Not a Bug"), "**Final Answer**: Bug") is None


def test_no_answer_is_unreadable():
    assert read_verdict(("Bug", "Not a Bug"), "") is None
    assert read_verdict(("Bug", "Not a Bug"), "I think it is a Bug.") is None


def test_read_confidence_and_probabilities():
    text = "- Bug: 0.8\n- Not a Bug: 0.2\n**Confidence:** high\n**Final Answer:** Bug"
    assert read_confidence(text) == "High"
    assert read_confidence("no confidence here") is None
    assert read_probabilities(("Bug", "Not a Bug"), text) == {"Bug": 0.8, "Not a Bug": 0.2}


def test_bug_goes_to_origin(staged_profile):
    ask, calls = scripted(["Bug", "Extrinsic"])
    label, records = run_pipeline(staged_profile.stages, MINIMAL, ask, MODELS)
    assert label == "Extrinsic"
    assert [c[0] for c in calls] == ["stage", "stage"]
    assert [r["stage"] for r in records] == ["validity", "origin"]
    assert calls[1][1] == ("Intrinsic", "Extrinsic")
    assert f"### {VALIDITY_NAME}: **Bug**" in calls[1][2]


def test_not_a_bug_kept_by_recover(staged_profile):
    ask, calls = scripted(["Not a Bug", "Not a Bug"])
    label, records = run_pipeline(staged_profile.stages, MINIMAL, ask, MODELS)
    assert label == "Not a Bug"
    assert [c[0] for c in calls] == ["stage", "judge"]
    assert [r["stage"] for r in records] == ["validity", "recover"]


def test_recover_flip_then_origin(staged_profile):
    ask, calls = scripted(["Not a Bug", "Bug", "Intrinsic"])
    label, records = run_pipeline(staged_profile.stages, MINIMAL, ask, MODELS)
    assert label == "Intrinsic"
    assert [c[0] for c in calls] == ["stage", "judge", "stage"]
    assert [r["stage"] for r in records] == ["validity", "recover", "origin"]
    origin_prompt = calls[2][2]
    assert f"### {VALIDITY_NAME}: **Not a Bug**" in origin_prompt
    assert f"### {RECOVER_NAME}: **Bug**" in origin_prompt


def test_no_recover_stage_means_not_a_bug_is_final(tmp_path):
    for name in ("v.md", "o.md"):
        (tmp_path / name).write_text("# g\n", encoding="utf-8")
    (tmp_path / "p.yaml").write_text(
        "name: p\nlabels:\n  - name: In\n  - name: Ex\n  - name: Out\n"
        "stages:\n  validity:\n    guide: v.md\n    labels: [Defect, Out]\n"
        "  origin:\n    guide: o.md\n    labels: [In, Ex]\n",
        encoding="utf-8",
    )
    ask, calls = scripted(["Out"])
    label, records = run_pipeline(load_profile(tmp_path / "p.yaml").stages, MINIMAL, ask, MODELS)
    assert (label, len(records), len(calls)) == ("Out", 1, 1)


def test_records_name_the_model_and_read_the_reply(staged_profile):
    ask, _ = scripted(["Not a Bug", "Not a Bug"])
    _, records = run_pipeline(staged_profile.stages, MINIMAL, ask, MODELS)
    assert [r["model"] for r in records] == ["s", "j"]
    assert records[0]["verdict"] == "Not a Bug"
    assert records[0]["response"] == "reply 1\n**Final Answer:** Not a Bug"
    assert set(records[0]) == {
        "stage",
        "model",
        "verdict",
        "confidence",
        "probabilities",
        "response",
    }


def test_a_failing_stage_propagates(staged_profile):
    ask, _ = scripted(["Not a Bug", RuntimeError("judge down")])
    with pytest.raises(RuntimeError):
        run_pipeline(staged_profile.stages, MINIMAL, ask, MODELS)


def test_records_survive_a_failure(staged_profile):
    ask, _ = scripted(["Not a Bug", RuntimeError("judge down")])
    records = []
    with pytest.raises(RuntimeError):
        run_pipeline(staged_profile.stages, MINIMAL, ask, MODELS, records)
    assert [r["stage"] for r in records] == ["validity"]
