"""
Review Agent.

Determines whether the Decision Agent's output should proceed
automatically, be flagged, or be sent for human review, based on the
(deterministic) Confidence Evaluation stage's `overall` score.

  High confidence      -> auto_approved -> continue to Message Generation
  Borderline confidence -> flagged      -> continue, but marked for spot-check
  Low confidence        -> human_review -> skip Message Generation

Pure thresholding logic. Zero LLM calls, by design -- review routing must
be fast, deterministic, and auditable.
"""

import os

from application.agents.base import BaseAgent
from application.dto.models import EvaluationReport, ReviewOutput


class ReviewAgent(BaseAgent):
    name = "review_agent"

    def __init__(self) -> None:
        self.auto_approve_threshold = float(
            os.getenv("REVIEW_AUTO_APPROVE_THRESHOLD", "0.75")
        )
        self.human_review_threshold = float(
            os.getenv("REVIEW_HUMAN_REVIEW_THRESHOLD", "0.45")
        )

    def run(self, evaluation: EvaluationReport) -> ReviewOutput:
        score = evaluation.overall

        if score >= self.auto_approve_threshold:
            return ReviewOutput(
                decision="auto_approved",
                reason=f"Overall confidence {score:.2f} >= {self.auto_approve_threshold:.2f}",
                threshold_used=self.auto_approve_threshold,
            )

        if score < self.human_review_threshold:
            return ReviewOutput(
                decision="human_review",
                reason=f"Overall confidence {score:.2f} < {self.human_review_threshold:.2f}",
                threshold_used=self.human_review_threshold,
            )

        return ReviewOutput(
            decision="flagged",
            reason=(
                f"Overall confidence {score:.2f} is between "
                f"{self.human_review_threshold:.2f} and {self.auto_approve_threshold:.2f}"
            ),
            threshold_used=self.auto_approve_threshold,
        )
