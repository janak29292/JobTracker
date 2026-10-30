"""
LangGraph state schema for the auto-apply agent.

This TypedDict defines the full state that flows through every node
in the apply graph. LangGraph persists this for checkpointing.

v2 architecture: Observe → Reason → Plan → Execute loop with
dynamic checklist re-evaluation on page changes.
"""
from typing import TypedDict, Optional, List


class ApplyState(TypedDict):
    # ── Inputs (set once at start) ──
    job_id: int
    apply_url: str
    run_id: int  # ApplicationRun.id

    # ── Observation (updated every observe cycle) ──
    current_url: str
    step_number: int                        # incremented per observe call
    screenshot_path: Optional[str]
    page_text: Optional[str]               # sanitized visible body text
    dom_snapshot: Optional[str]             # cleaned visible HTML / form metadata
    dom_signature: str                      # STRUCTURAL hash (no input values)
    page_errors: Optional[List[str]]        # visible validation errors or alert text extracted from page

    # ── Reasoning (output of the reason node) ──
    page_type: Optional[str]                # CLOSED | CAPTCHA | LISTING_GATEWAY |
                                            # APPLICATION_FORM | CONFIRMATION | ERROR_BLOCKED
    page_analysis: Optional[str]            # LLM's free-text reasoning (audit trail)
    alternative_apply_method: Optional[str] # e.g. "email", "external_url", or None
    alternative_apply_details: Optional[str] # Recruiter email address or URL found in JD
    captcha_attempts: int                  # Track challenge bypass retries

    # ── Hierarchical Planning & Task Execution (Macro ↔ Micro) ──
    page_tasks: List[dict]                  # dynamic checklist of tasks decomposed for this screen
    current_task_index: int                 # index of task currently being executed (0-based)
    page_audited: bool                      # has the whole-page pre-submit check passed?
    macro_reasoning: Optional[str]          # macro agent's plan for this screen

    # ── Legacy/Flat Action checklist (maintained for backward compatibility) ──
    checklist: List[dict]                   # ordered actions for this page
    checklist_index: int                    # which item we're executing (0-based)

    # ── Execution tracking ──
    last_action_success: Optional[bool]     # did the last execute succeed?
    last_action_error: Optional[str]        # error message if it failed
    page_changed_after_action: Optional[bool]   # did dom_signature change after execute?
    consecutive_action_failures: int        # reset on success, escalate at 3
    total_actions_executed: int             # running count across all pages
    recent_actions: List[dict]              # history of recently executed actions in this run

    # ── Decision state ──
    apply_type: Optional[str]               # native_linkedin | ats_greenhouse | etc.
    had_fresh_decisions: bool               # True if any action needed LLM (not cached)

    # ── Termination ──
    status: str                             # maps to ApplicationRun status
    escalation_reason: Optional[str]
    error_log: Optional[str]
