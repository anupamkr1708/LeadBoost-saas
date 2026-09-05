"""
P0-C: AI evaluation / regression dataset.

Deliberately reuses application/evaluation/evaluators.py's existing
deterministic checks (grounding, completeness, consistency) rather than
building a second evaluation platform -- this package adds only what
those don't already cover: schema validity, forbidden-claim detection,
exact-label matching, and confidence-range checks (see
deterministic_checks.py), plus the dataset format and runner that drive
the real agent code (not mocks) through its deterministic-fallback path
against hand-crafted, synthetic cases -- no live LLM calls, no external
API quota spent. See run_eval.py's module docstring for the full design
rationale and README.md for usage.
"""
