"""
Prompt builders for the two-phase review generation.

Implements [structured-review-output:FR-001] - Two-phase review generation prompts
"""

from src.agent.prompts.report_prompt import build_report_prompt
from src.agent.prompts.structure_prompt import build_structure_prompt

__all__ = ["build_structure_prompt", "build_report_prompt"]
