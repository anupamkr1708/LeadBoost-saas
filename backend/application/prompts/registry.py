"""
Prompt Registry.

Loads versioned prompt templates from application/prompts/templates/*.yaml
so prompts are never hardcoded inline inside agents. Each template declares
its own variables and expected output schema (see prompts/schemas.py),
and `render()` validates that every declared variable was supplied before
handing back langchain-ready (role, text) message tuples.
"""

from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from application.exceptions.errors import PromptError
from application.prompts.schemas import PromptTemplate

_TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptRegistry:
    """Loads and caches prompt templates by (name, version)."""

    def __init__(self, templates_dir: Path = _TEMPLATES_DIR):
        self._templates_dir = templates_dir
        self._cache: Dict[Tuple[str, str], PromptTemplate] = {}
        self._latest: Dict[str, str] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self._templates_dir.exists():
            raise PromptError(f"Prompt templates directory not found: {self._templates_dir}")

        for path in sorted(self._templates_dir.glob("*.yaml")):
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            try:
                template = PromptTemplate(**raw)
            except Exception as e:
                raise PromptError(f"Invalid prompt template {path.name}: {e}") from e

            key = (template.name, template.version)
            self._cache[key] = template
            # Track the highest version seen per name as "latest" (simple
            # lexicographic compare is sufficient for v1, v2, ... naming)
            if template.name not in self._latest or template.version > self._latest[template.name]:
                self._latest[template.name] = template.version

    def get(self, name: str, version: str = "latest") -> PromptTemplate:
        if version == "latest":
            version = self._latest.get(name, "")
        key = (name, version)
        if key not in self._cache:
            raise PromptError(f"Prompt template not found: {name}@{version}")
        return self._cache[key]

    def render(
        self, name: str, version: str = "latest", **kwargs: str
    ) -> List[Tuple[str, str]]:
        template = self.get(name, version)

        missing = [v for v in template.variables if v not in kwargs]
        if missing:
            raise PromptError(
                f"Missing variables for prompt '{name}@{template.version}': {missing}"
            )

        # langchain's ChatPromptTemplate performs its own {var} substitution
        # from `inputs`, so we simply return the raw (role, template) pairs;
        # the variable-presence check above is what actually enforces the
        # "declared variables must be supplied" contract.
        return template.as_langchain_messages()


_registry_singleton: "PromptRegistry | None" = None


def get_prompt_registry() -> PromptRegistry:
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = PromptRegistry()
    return _registry_singleton
