import asyncio

from app.config import Settings
from app.knowledge.query_planner import (
    QueryPlanner,
    RetrievalPlan,
    expanded_query,
    is_identifier_query,
)
from app.providers.models import ModelResponse


def test_plan_validation_and_expansion():
    plan = RetrievalPlan(original_query="where are results?", search_terms=["persist", "persist"], identifiers=["run", "run"])
    assert plan.search_terms == ["persist"]
    assert plan.identifiers == ["run"]
    assert expanded_query(plan).startswith("where are results?\n")


def test_valid_excess_values_are_truncated_after_safety_validation():
    plan = RetrievalPlan(original_query="q", search_terms=["a", "b", "c", "d", "e", "f"],
        identifiers=["one", "two", "three", "four"])
    assert plan.search_terms == ["a", "b", "c", "d", "e"]
    assert plan.identifiers == ["one", "two", "three"]
    assert plan.truncated is True


def test_identifier_bypass_requires_no_provider():
    class Never:
        def get(self, name):
            raise AssertionError
    plan = asyncio.run(QueryPlanner(Settings(), Never()).plan("derive_indexability"))
    assert plan.original_query == "derive_indexability"
    assert is_identifier_query("arbitrary_function_name")


def test_invalid_plan_falls_back_without_exception():
    plan = RetrievalPlan(original_query="q", status="fallback", safe_reason="planner_failed")
    assert expanded_query(plan) == "q"


def test_markdown_json_and_concepts_alias_are_accepted():
    class Provider:
        async def generate(self, request):
            return ModelResponse(provider="ollama", model="m", content="```json\n{\"concepts\":[\"crawl decision\"]}\n```")
    class Registry:
        def get(self, name):
            return Provider()
    plan = asyncio.run(QueryPlanner(Settings(), Registry()).plan("where does a crawler decide"))
    assert plan.search_terms == ["crawl decision"]


def test_invalid_identifier_has_safe_reason():
    class Provider:
        async def generate(self, request):
            return ModelResponse(provider="ollama", model="m", content='{"identifiers":["not an identifier"]}')
    class Registry:
        def get(self, name):
            return Provider()
    plan = asyncio.run(QueryPlanner(Settings(), Registry()).plan("where is it"))
    assert plan.status == "fallback"
    assert plan.safe_reason == "invalid_identifier"


def test_prompt_distinguishes_concepts_and_identifiers():
    seen = []
    class Provider:
        async def generate(self, request):
            seen.append(request.messages[0].content)
            return ModelResponse(provider="ollama", model="m", content='{"search_terms":["page indexability"],"identifiers":["derive_indexability"]}')
    class Registry:
        def get(self, name):
            return Provider()
    plan = asyncio.run(QueryPlanner(Settings(), Registry()).plan("where is it"))
    assert plan.status == "planned"
    assert "search_terms" in seen[0]
    assert "identifiers must match ^[A-Za-z_][A-Za-z0-9_]*$" in seen[0]
    assert "If unsure, put the value in search_terms" in seen[0]
    assert "derive_indexability" not in seen[0]
    assert "Google" not in seen[0]
