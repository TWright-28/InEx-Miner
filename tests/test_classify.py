import pytest
import requests
from conftest import FULL_PROBS, row_for


class TestValidateResult:
    def _r(self, cl, label, probs):
        from inex.services.classification import ParsedResult

        return cl.validateResult(ParsedResult(label=label, probs=probs))

    def test_complete_result_passes_clean(self, classifier):
        r = self._r(classifier, "Intrinsic", dict(FULL_PROBS))
        assert r.ok and r.flags == []

    def test_partial_probabilities_are_fatal(self, classifier):
        r = self._r(classifier, "Intrinsic", {"Intrinsic": 0.9})
        assert r.status == "invalid_result"
        assert r.label is None

    def test_out_of_range_probability_is_fatal(self, classifier):
        r = self._r(classifier, "Intrinsic", {**FULL_PROBS, "Unknown": 7.0})
        assert r.status == "invalid_result" and r.label is None

    def test_unknown_label_is_fatal(self, classifier):
        r = self._r(classifier, "Nonsense", dict(FULL_PROBS))
        assert r.status == "invalid_result" and r.label is None

    def test_bad_sum_is_flagged_but_not_fatal(self, classifier):
        r = self._r(classifier, "Intrinsic", {k: 0.9 for k in FULL_PROBS})
        assert r.ok and "prob_sum_off" in r.flags

    def test_argmax_mismatch_is_recorded_never_overridden(self, classifier):
        r = self._r(classifier, "Unknown", dict(FULL_PROBS))
        assert r.ok
        assert "argmax_mismatch" in r.flags
        assert r.label == "Unknown", "the model's stated label must win"
        assert r.argmax_label == "Intrinsic"

    def test_tie_is_not_a_mismatch(self, classifier):
        probs = {"Intrinsic": 0.5, "Extrinsic": 0.5, "Not a Bug": 0.0, "Unknown": 0.0}
        r = self._r(classifier, "Intrinsic", probs)
        assert "argmax_tie" in r.flags and "argmax_mismatch" not in r.flags


class TestRetryPolicy:
    def _http(self, code):
        class Resp:
            status_code = code

        e = requests.exceptions.HTTPError()
        e.response = Resp()
        return e

    @pytest.mark.parametrize(
        "code,expected",
        [(429, True), (500, True), (503, True), (400, False), (401, False), (404, False)],
    )
    def test_http_status_codes(self, classifier, code, expected):
        assert classifier._isRetryable(self._http(code)) is expected

    def test_network_errors_retry(self, classifier):
        assert classifier._isRetryable(requests.exceptions.Timeout())
        assert classifier._isRetryable(requests.exceptions.ConnectionError())

    def test_google_server_error_retries(self, classifier):
        from google.genai import errors as ge

        e = ge.ServerError.__new__(ge.ServerError)
        Exception.__init__(e, "503 UNAVAILABLE")
        assert classifier._isRetryable(e)

    def test_quota_error_retries_via_cause_code(self, classifier):
        class ChatGoogleGenerativeAIError(Exception):
            pass

        class ClientError(Exception):
            def __init__(self, code):
                self.code = code

        e = ChatGoogleGenerativeAIError("quota")
        e.__cause__ = ClientError(429)
        assert classifier._isRetryable(e)

    def test_permission_error_does_not_retry(self, classifier):
        class ChatGoogleGenerativeAIError(Exception):
            pass

        class ClientError(Exception):
            def __init__(self, code):
                self.code = code

        e = ChatGoogleGenerativeAIError("denied")
        e.__cause__ = ClientError(403)
        assert classifier._isRetryable(e) is False

    def test_context_overflow_does_not_retry(self, classifier):
        class GoogleContextOverflowError(Exception):
            pass

        assert classifier._isRetryable(GoogleContextOverflowError()) is False


class TestRetryLoop:
    def test_transient_failure_recovers_and_counts_attempts(self, classifier):
        from inex.services.classification import AttemptFailure, ParsedResult

        calls = {"n": 0}

        def flaky(_prompt):
            calls["n"] += 1
            if calls["n"] < 3:
                raise AttemptFailure("parse_error", "transient", raw_text="p", retryable=True)
            return ParsedResult(label="Intrinsic", probs=dict(FULL_PROBS), raw_text="ok")

        classifier.callOllama = flaky
        r = classifier.callModel("x")
        assert r.status == "ok" and r.attempts == 3

    def test_exhausted_retries_record_a_failure_not_a_label(self, classifier):
        from inex.services.classification import AttemptFailure

        classifier.retry_attempts = 3
        classifier.callOllama = lambda _p: (_ for _ in ()).throw(
            AttemptFailure("parse_error", "never parses", raw_text="junk", retryable=True)
        )
        r = classifier.callModel("x")
        assert r.status == "parse_error"
        assert r.label is None
        assert r.raw_text == "junk", "raw response must survive for auditing"
        assert r.attempts == 3

    def test_non_retryable_fails_fast(self, classifier):
        from inex.services.classification import AttemptFailure

        calls = {"n": 0}

        def truncated(_p):
            calls["n"] += 1
            raise AttemptFailure("truncated", "MAX_TOKENS", raw_text="cut", retryable=False)

        classifier.callOllama = truncated
        r = classifier.callModel("x")
        assert r.status == "truncated" and calls["n"] == 1

    def test_hard_exception_carries_its_attempt_count(self, classifier):
        from google.genai import errors as ge

        classifier.retry_attempts = 4
        calls = {"n": 0}

        def boom(_p):
            calls["n"] += 1
            e = ge.ServerError.__new__(ge.ServerError)
            Exception.__init__(e, "503 UNAVAILABLE")
            raise e

        classifier.callOllama = boom
        with pytest.raises(Exception) as exc:
            classifier.callModel("x")
        assert calls["n"] == 4
        assert getattr(exc.value, "classify_attempts", None) == 4

    def test_empty_ollama_response_is_a_failure_not_a_label(self, classifier, monkeypatch):
        import inex.services.classification as mod

        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"response": "   "}

        monkeypatch.setattr(mod.requests, "post", lambda *a, **k: Resp())
        r = classifier.callModel("x")
        assert r.status == "empty_response" and r.label is None


class TestPromptConstruction:
    ISSUE = {
        "owner": "acme",
        "repo": "widget",
        "number": 1,
        "title": None,
        "body": None,
        "state_reason": "not_planned",
        "comments": [],
    }

    def test_state_reason_is_passed_to_the_model(self, classifier):
        assert "**Closed Reason:** not_planned" in classifier.formatIssueData(self.ISSUE)

    def test_absent_pr_and_commit_are_explicit(self, classifier):
        out = classifier.formatIssueData(self.ISSUE)
        assert "**Closing PR:** None found" in out
        assert "**Closing Commit:** None found" in out

    def test_null_title_and_body_do_not_crash(self, classifier):
        out = classifier.formatIssueData(self.ISSUE)
        assert "No title" in out and "No description provided" in out

    def test_ollama_path_keeps_the_markdown_contract(self, classifier):
        out = classifier.formatIssueData(self.ISSUE)
        assert "**Final Answer:**" in out and "Probability Distribution" in out

    def test_cloud_path_drops_the_markdown_contract(self, cloud_classifier):
        out = cloud_classifier.formatIssueData(self.ISSUE)
        assert "**Final Answer:**" not in out
        assert "response schema" in out

    def test_raw_data_cannot_override_explicit_columns(self, classifier):
        import json

        raw = json.dumps({"title": "EVIL", "state": "EVIL", "labels": []})
        _id, number, issue = classifier._prepareIssue("acme", "widget", row_for(1, raw_data=raw))
        assert issue["title"] == "a title"
        assert issue["state"] == "closed"


class TestIdentity:
    def test_prompt_version_is_content_addressed(self, classifier):
        assert classifier.prompt_version.startswith("inex@")
        assert classifier.prompt_version.endswith("+t2")

    def test_same_prompt_gives_a_stable_version(self, classifier):
        from conftest import load_inex_profile

        from inex.services.classification import ClassificationService

        again = ClassificationService(None, load_inex_profile(), "qwen3:30b", 0.2)
        assert again.prompt_version == classifier.prompt_version


class TestStructuredSchema:
    def test_reasoning_precedes_the_answer(self, inex_profile):
        from inex.services.profiles import build_schema

        order = list(build_schema(inex_profile).model_json_schema()["properties"])
        assert order.index("definition_check") < order.index("probabilities")
        assert order.index("probabilities") < order.index("final_answer")

    def test_every_field_is_required(self, inex_profile):
        from inex.services.profiles import build_schema

        js = build_schema(inex_profile).model_json_schema()
        assert len(js["required"]) == len(js["properties"])

    def test_final_answer_enumerates_the_taxonomy(self, inex_profile):
        from inex.services.profiles import build_schema

        js = build_schema(inex_profile).model_json_schema()
        assert set(js["properties"]["final_answer"]["enum"]) == {
            "Intrinsic",
            "Extrinsic",
            "Not a Bug",
            "Unknown",
        }

    @pytest.mark.parametrize(
        "profile_fixture, slugs, label",
        [
            ("inex_profile", {"intrinsic", "extrinsic", "not_a_bug", "unknown"}, "Not a Bug"),
            ("severity_profile", {"critical", "major", "minor"}, "Major"),
        ],
    )
    def test_schema_survives_conversion_for_gemini(self, request, profile_fixture, slugs, label):
        from langchain_google_genai._function_utils import _dict_to_gapic_schema

        from inex.services.profiles import build_schema

        profile = request.getfixturevalue(profile_fixture)
        gapic = _dict_to_gapic_schema(build_schema(profile).model_json_schema())
        probs = gapic.properties["probabilities"]
        assert set(probs.properties) == slugs
        assert set(probs.required) == set(probs.properties)
        assert label in list(gapic.properties["final_answer"].enum)


class TestRouting:
    def test_cloud_model_uses_structured_output(self, cloud_classifier):
        assert cloud_classifier.useStructured is True

    def test_bare_ollama_tag_does_not(self, classifier):
        assert classifier.useStructured is False

    def test_colon_in_an_ollama_tag_is_not_a_provider(self):
        from conftest import load_inex_profile

        from inex.services.classification import ClassificationService

        assert (
            ClassificationService(None, load_inex_profile(), "qwen3:30b", 0.2).useStructured
            is False
        )
