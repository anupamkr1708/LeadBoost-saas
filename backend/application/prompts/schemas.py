"""
Prompt template schema.

Every prompt is declared with a version, description, its expected input
variables, and its expected output JSON schema, so prompts are self
-documenting, reusable, and validated at render time rather than agents
hand-building prompt strings inline.
"""

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class PromptMessage(BaseModel):
    role: str  # "system" | "human"
    template: str


class PromptTemplate(BaseModel):
    name: str
    version: str
    description: str
    variables: List[str] = Field(default_factory=list)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    messages: List[PromptMessage] = Field(default_factory=list)

    def as_langchain_messages(self) -> List[tuple]:
        return [(m.role, m.template) for m in self.messages]
