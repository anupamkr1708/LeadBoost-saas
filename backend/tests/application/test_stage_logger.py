"""
Tests for application.utils.stage_logger.stage_span.
"""

import pytest

from application.utils.stage_logger import StageTimer, stage_span


def test_stage_span_records_duration_and_timestamps():
    with stage_span("test_stage", lead_id=1, pipeline_id="pid-1") as timer:
        pass

    assert timer.duration_ms >= 0
    assert timer.started_at is not None
    assert timer.completed_at is not None
    assert timer.error is None
    assert timer.retry_count == 0


def test_stage_span_lets_caller_report_retry_count():
    with stage_span("test_stage", lead_id=1, pipeline_id="pid-1") as timer:
        timer.retry_count = 2

    assert timer.retry_count == 2


def test_stage_span_records_error_and_reraises():
    with pytest.raises(ValueError):
        with stage_span("test_stage", lead_id=1, pipeline_id="pid-1") as timer:
            raise ValueError("boom")

    # `timer` was assigned by the `with ... as timer` binding before the
    # exception, so its failure-path fields are still populated.
    assert timer.error == "boom"
    assert timer.duration_ms >= 0
    assert timer.completed_at is not None


def test_stage_timer_defaults():
    timer = StageTimer()
    assert timer.duration_ms == 0
    assert timer.retry_count == 0
    assert timer.error is None
    assert timer.started_at is None
    assert timer.completed_at is None
