"""
LangGraph state schema for the auto-apply agent.

This TypedDict defines the full state that flows through every node
in the apply graph. LangGraph persists this for checkpointing.
"""
from typing import TypedDict, Optional, List


class ApplyState(TypedDict):
    # ── Inputs (set once at start) ──
    job_id: int
    apply_url: str
    run_id: int  # ApplicationRun.id

    # ── Loop state ──
    current_url: str
    step_number: int
    screenshot_path: Optional[str]
    dom_snapshot: Optional[str]  # cleaned HTML of the form area
    dom_signature: str  # hash of structural identity
    field_labels: List[str]  # extracted form field labels for cache signature

    # ── Decision state ──
    apply_type: Optional[str]  # classified link type after first navigation
    had_fresh_decisions: bool  # True if any step in this run was a cache miss

    # ── Termination ──
    status: str  # maps to ApplicationRun status
    escalation_reason: Optional[str]
    error_log: Optional[str]
