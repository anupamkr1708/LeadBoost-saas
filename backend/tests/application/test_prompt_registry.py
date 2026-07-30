"""
Tests for application.prompts.registry.PromptRegistry.
"""

import pytest

from application.exceptions.errors import PromptError
from application.prompts.registry import PromptRegistry, get_prompt_registry


def test_loads_all_bundled_templates():
    registry = PromptRegistry()
    for name in ("company_intelligence", "decision", "messaging"):
        template = registry.get(name)
        assert template.name == name
        assert template.version == "v1"
        assert template.description
        assert template.variables
        assert template.messages


def test_get_latest_resolves_highest_version():
    registry = PromptRegistry()
    latest = registry.get("company_intelligence", version="latest")
    explicit = registry.get("company_intelligence", version="v1")
    assert latest.version == explicit.version == "v1"


def test_get_unknown_template_raises_prompt_error():
    registry = PromptRegistry()
    with pytest.raises(PromptError):
        registry.get("does_not_exist")


def test_render_returns_langchain_message_tuples():
    registry = PromptRegistry()
    messages = registry.render(
        "company_intelligence",
        company_name="Acme",
        website="https://acme.com",
        industry="Robotics",
        employees="11-50",
        about_text="We build robots.",
        evidence_summary="Organization type: Company (confidence 0.90, source: json_ld)",
    )
    assert isinstance(messages, list)
    assert all(isinstance(m, tuple) and len(m) == 2 for m in messages)
    roles = [role for role, _ in messages]
    assert "system" in roles
    assert "human" in roles


def test_render_missing_variable_raises_prompt_error():
    registry = PromptRegistry()
    with pytest.raises(PromptError):
        # Missing all required variables for the "decision" template
        registry.render("decision")


def test_output_schema_declared_for_every_template():
    registry = PromptRegistry()
    for name in ("company_intelligence", "decision", "messaging"):
        template = registry.get(name)
        assert template.output_schema.get("type") == "object"
        assert "properties" in template.output_schema


def test_singleton_accessor_returns_same_instance():
    a = get_prompt_registry()
    b = get_prompt_registry()
    assert a is b
