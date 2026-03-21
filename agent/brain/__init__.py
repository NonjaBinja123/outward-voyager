"""
brain — LLM interaction layer, split into focused sub-modules.

Sub-modules:
  observation.py  — Observation class: packages + serializes game state
  prompts.py      — System prompt templates + build_system()
  parser.py       — parse(): raw LLM text → validated action plan dict
  core.py         — Brain class: orchestrates think() end-to-end

Test tool:
  python -m brain.test_prompt         # show prompt for a fake scenario
  python -m brain.test_prompt --live  # call the real LLM and show output

Backward-compatible re-exports (existing code doesn't need to change):
  from brain import Brain, Observation
"""
from brain.core import Brain
from brain.observation import Observation

__all__ = ["Brain", "Observation"]
