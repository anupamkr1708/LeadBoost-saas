"""Dataset case + result shapes for the P0-C evaluation harness."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EvalCase:
    case_id: str
    agent: str  # "company_intelligence" | "qualification" | "decision" | "messaging"
    input_context: Dict[str, Any]
    evidence: List[str] = field(default_factory=list)
    expected_properties: Dict[str, Any] = field(default_factory=dict)
    must_not_claim: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EvalCase":
        return EvalCase(
            case_id=d["case_id"],
            agent=d["agent"],
            input_context=d.get("input_context", {}),
            evidence=d.get("evidence", []),
            expected_properties=d.get("expected_properties", {}),
            must_not_claim=d.get("must_not_claim", []),
            tags=d.get("tags", []),
        )


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalCaseResult:
    case_id: str
    agent: str
    tags: List[str]
    checks: List[CheckResult]
    raw_output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        if self.error is not None:
            return False
        return all(c.passed for c in self.checks)
