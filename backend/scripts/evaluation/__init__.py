"""
Full-Pipeline Evaluation Framework.

    metrics.py          -- pure, reusable metric computation
    generate_report.py  -- pure I/O: writes the five report artifacts

Orchestration (running Discovery + the downstream pipeline stages) lives
in `scripts/run_full_pipeline_benchmark.py` and `scripts/test_pipeline_sample.py`,
one level up -- deliberately kept out of this package, which contains
no network calls and no pipeline-stage invocations.
"""
