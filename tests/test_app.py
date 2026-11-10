import pytest

from inex.app import build_app
from inex.errors import InexError


def test_build_app_wires_one_store(settings):
    app = build_app(settings)
    assert settings.database.is_file()
    assert app.queries.max_rows == 50
    assert app.classification.store is app.collection.store is app.imports.store
    assert app.linkage.store is app.queries.store is app.maintenance.store
    app.close()


def test_services_receive_settings(settings):
    app = build_app(settings)
    assert app.classification.model_config["model_name"] == settings.classifier
    assert app.classification.workers == settings.classify_workers
    assert app.classification.retry_attempts == settings.classify_retries
    assert app.classification.ollama_base == settings.ollama_base
    assert app.collection.github_token == "t"
    assert app.queries.exports_dir == settings.exports_dir
    assert app.linkage.npm.cache_dir == settings.npm_cache_dir
    assert app.settings is settings
    app.close()


def test_unknown_project_flows_through_as_inex_error(settings):
    app = build_app(settings)
    with pytest.raises(InexError) as e:
        app.classification.preview("nope", "nope")
    assert e.value.code == "project_not_found"
    app.close()


def test_default_app_classifies_with_the_staged_profile(settings):
    app = build_app(settings)
    assert app.profile.name == "inex-staged"
    assert app.classification.judge == "google_genai:gemini-3.1-pro-preview"
    assert app.classification.thinking == {
        "stage": settings.thinking,
        "judge": settings.judge_thinking,
    }
    app.close()


def test_a_local_classifier_keeps_the_judge_local(settings):
    from dataclasses import replace

    app = build_app(replace(settings, classifier="qwen3:30b"))
    assert app.classification.judge == "qwen3:30b"
    app.close()
