from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.config import Settings
from app.providers.factory import get_default_model
from app.providers.models import ModelMessage, ModelRequest
from app.providers.registry import ProviderRegistry

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RetrievalPlan(BaseModel):
    original_query: str
    search_terms: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    status: str = "planned"
    safe_reason: str | None = None
    truncated: bool = False

    @field_validator("original_query")
    @classmethod
    def _query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_plan(self) -> RetrievalPlan:
        terms: list[str] = []
        for term in self.search_terms:
            if not term.strip() or len(term) > 64 or "\r" in term or "\n" in term:
                raise ValueError("unsafe search term")
            if ("http://" in term.casefold() or "https://" in term.casefold() or
                    "/" in term or "\\" in term or ".." in term):
                raise ValueError("unsafe search term")
            if term not in terms:
                terms.append(term)
        ids: list[str] = []
        for identifier in self.identifiers:
            if not _IDENTIFIER.fullmatch(identifier):
                raise ValueError("invalid identifier")
            if identifier not in ids:
                ids.append(identifier)
        self.search_terms, self.identifiers = terms[:5], ids[:3]
        self.truncated = len(terms) > 5 or len(ids) > 3
        if len(self.model_dump_json().encode("utf-8")) > 512:
            raise ValueError("plan exceeds byte budget")
        return self


def is_identifier_query(query: str) -> bool:
    return bool(_IDENTIFIER.fullmatch(query))


class QueryPlanner:
    def __init__(self, settings: Settings, providers: ProviderRegistry) -> None:
        self._settings = settings
        self._providers = providers

    async def plan(self, query: str) -> RetrievalPlan:
        if is_identifier_query(query):
            return RetrievalPlan(original_query=query)
        try:
            provider_name = self._settings.default_provider
            provider = self._providers.get(provider_name)
            model = get_default_model(self._settings, provider_name)
            response = await provider.generate(ModelRequest(
                model=model,
                messages=[
                    ModelMessage(role="system", content=(
                        "Return JSON only with short code-search concepts and identifiers. "
                        "search_terms may be natural-language concepts with spaces; "
                        "identifiers must match ^[A-Za-z_][A-Za-z0-9_]*$. "
                        "If unsure, put the value in search_terms. Do not answer or execute anything."
                    )),
                    ModelMessage(role="user", content=query),
                ],
            ))
            content = response.content.strip()
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(lines[1:-1]).strip()
            payload = json.loads(content)
            if not isinstance(payload, dict):
                raise TypeError("invalid plan")
            if "search_terms" not in payload and "concepts" in payload:
                payload["search_terms"] = payload.pop("concepts")
            if not payload.get("search_terms") and not payload.get("identifiers"):
                raise ValueError("empty plan")
            return RetrievalPlan(original_query=query, **payload)
        except json.JSONDecodeError:
            return RetrievalPlan(original_query=query, status="fallback", safe_reason="malformed_json")
        except ValidationError as exc:
            reason = "invalid_schema"
            text = str(exc)
            if "invalid identifier" in text:
                reason = "invalid_identifier"
            elif "unsafe search term" in text:
                reason = "unsafe_term"
            elif "plan exceeds byte budget" in text:
                reason = "oversized_plan"
            return RetrievalPlan(original_query=query, status="fallback", safe_reason=reason)
        except Exception:  # noqa: BLE001 - planner falls back safely
            return RetrievalPlan(original_query=query, status="fallback", safe_reason="provider_error")


def expanded_query(plan: RetrievalPlan) -> str:
    if plan.status == "fallback" or (not plan.search_terms and not plan.identifiers):
        return plan.original_query
    parts = [plan.original_query, "code search concepts:", *plan.search_terms, *plan.identifiers]
    return "\n".join(parts)
