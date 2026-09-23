from __future__ import annotations

from pydantic import BaseModel

from app.config import Settings
from app.project_state import ProjectState, ProjectStateService
from app.providers.factory import get_default_model
from app.providers.models import ModelMessage, ModelRequest
from app.providers.registry import ProviderRegistry


class ProjectBriefing(BaseModel):
    workspace: str
    scope: str
    status: str
    summary: str
    context: str
    source_authorities: list[str]


class ProjectBriefingService:
    def __init__(self, state: ProjectStateService, providers: ProviderRegistry | None = None, settings: Settings | None = None) -> None:
        self._state, self._settings = state, settings
        self._providers = providers

    def context(self, project: ProjectState) -> str:
        git = "unavailable"
        if project.branch or project.head or project.clean is not None:
            git = f"branch={project.branch or 'unavailable'}; head={project.head or 'unavailable'}; clean={project.clean}"
        lines = [f"Workspace: {project.workspace}", f"Scope: {project.scope}", f"Git (live): {git}"]
        changed = ", ".join(project.changed_files) if project.changed_files else "none"
        lines.append(f"Changed files (live): {changed}")
        plans = ", ".join(str(item.get("goal", "")) for item in project.active_plans) or "none"
        lines.append(f"Active plans (PlanService): {plans}")
        steps = ", ".join(str(item["step"].get("title", "")) for item in project.next_steps) or "none"
        lines.append(f"Next steps (PlanService): {steps}")
        tasks = ", ".join(str(item.get("content", "")) for item in project.unresolved_tasks) or "none"
        lines.append(f"Unresolved tasks (MemoryService): {tasks}")
        traces = ", ".join(f"{item.tool}:{item.status}" for item in project.recent_traces) or "none"
        lines.append(f"Recent execution (ActionJournal): {traces}")
        return "\n".join(lines)

    def build(self, workspace: str, scope: str) -> ProjectBriefing:
        project = self._state.get(workspace, scope)
        context = self.context(project)
        return ProjectBriefing(workspace=workspace, scope=scope, status="fallback", summary=context, context=context, source_authorities=["ProjectState", "live Git", "PlanService", "MemoryService", "ActionJournal"])

    async def generate(self, workspace: str, scope: str, provider_name: str | None = None, model: str | None = None) -> ProjectBriefing:
        briefing = self.build(workspace, scope)
        if not provider_name or self._providers is None:
            return briefing
        try:
            provider = self._providers.get(provider_name)  # type: ignore[arg-type]
            if model is None and self._settings is None:
                return briefing
            selected_model = model or get_default_model(self._settings, provider_name)
            response = await provider.generate(ModelRequest(model=selected_model, messages=[ModelMessage(role="user", content=briefing.context)]))
            return briefing.model_copy(update={"status": "generated", "summary": response.content[:4000]})
        except Exception:  # noqa: BLE001 - provider boundary returns safe fallback
            return briefing
