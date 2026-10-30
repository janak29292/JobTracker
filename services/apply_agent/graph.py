"""
LangGraph StateGraph definition for the auto-apply agent (v2).

Architecture: Observe → Reason → Plan → Execute

The graph runs inside a single Celery task. Checkpoint persistence
uses SQLite (via langgraph-checkpoint-sqlite) to support pause/resume
when an application is parked for human review.
"""
import os
import sqlite3
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from django.conf import settings
from services.apply_agent.state import ApplyState
from services.apply_agent.nodes import (
    pre_check,
    classify_link,
    observe,
    reason,
    captcha_bypass,
    plan,
    execute,
    trust_check,
    submit,
)


# ── Checkpoint storage ──────────────────────────────────────────────
CHECKPOINT_DB_PATH = os.path.join(
    getattr(settings, 'BASE_DIR', '.'),
    'apply_checkpoints.db'
)


def get_checkpointer():
    """Get the SQLite checkpointer for LangGraph state persistence."""
    conn = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()  # Ensure checkpoint tables are created
    return saver


# ── Router functions ────────────────────────────────────────────────

def route_pre_check(state: ApplyState) -> str:
    """Route after pre_check: proceed to classify or terminate."""
    if state['status'] in ('SKIPPED', 'ESCALATED'):
        return END
    return 'classify_link'


def route_reason(state: ApplyState) -> str:
    """After reasoning about the page, decide what to do."""
    # Safety valves
    if state.get('step_number', 0) > 50:
        return 'trust_check'
    if state.get('total_actions_executed', 0) > 100:
        return 'trust_check'

    if state.get('status') in ('ESCALATED', 'SKIPPED'):
        return END

    if state.get('status') == 'PENDING_REVIEW':
        return 'trust_check'

    page_type = state.get('page_type', 'unknown')

    # Confirmation → record success
    if page_type == 'CONFIRMATION':
        return 'submit'

    # CAPTCHA challenge → attempt bypass
    if page_type == 'CAPTCHA':
        return 'captcha_bypass'

    # All other actionable pages → build a plan
    return 'plan'


def route_plan(state: ApplyState) -> str:
    """After planning, check if roadmap stalled or is ready to execute."""
    if state.get('status') == 'PENDING_REVIEW':
        return 'trust_check'
    if state.get('status') in ('ESCALATED', 'SKIPPED'):
        return END

    page_tasks = state.get('page_tasks', [])
    checklist = state.get('checklist', [])
    if not page_tasks and not checklist:
        return 'observe'

    return 'execute'


def route_execute(state: ApplyState) -> str:
    """After executing one action, decide what to do next."""
    if state.get('status') in ('ESCALATED', 'SKIPPED'):
        return END

    if state.get('status') == 'PENDING_REVIEW':
        return 'trust_check'

    # Failed action → retry or escalate
    if not state.get('last_action_success', True):
        if state.get('consecutive_action_failures', 0) >= 3:
            return 'trust_check'  # park for human review
        return 'observe'  # re-observe, re-plan, retry differently

    # Page changed → re-observe (URL change, navigation, modal opened)
    if state.get('page_changed_after_action'):
        return 'observe'

    # Hierarchical page_tasks:
    page_tasks = state.get('page_tasks', [])
    task_idx = state.get('current_task_index', 0)
    if page_tasks:
        if task_idx < len(page_tasks) or not state.get('page_audited', False):
            return 'execute'
        return 'observe'

    # More items in legacy checklist → keep executing
    checklist = state.get('checklist', [])
    index = state.get('checklist_index', 0)
    if index < len(checklist):
        return 'execute'

    # Checklist complete, page didn't change → re-observe to see result
    return 'observe'


def route_trust(state: ApplyState) -> str:
    """Route after trust check: auto-submit or park for review."""
    if state['status'] == 'PENDING_REVIEW':
        return END  # Celery task ends, resumes later via resume_application
    return 'submit'


# ── Graph definition ────────────────────────────────────────────────

def build_apply_graph() -> StateGraph:
    """
    Build the auto-apply LangGraph (v2).

    State Machine:
        pre_check → classify_link → observe → reason ──→ plan ──→ execute ──→ ...
                                         ↑       │          │          |
                                         │       ├→ captcha ┘          │
                                         │       │                     │
                                         └───────┴─────────────────────┘
    """
    graph = StateGraph(ApplyState)

    # ── Nodes ──
    graph.add_node('pre_check', pre_check)
    graph.add_node('classify_link', classify_link)
    graph.add_node('observe', observe)
    graph.add_node('reason', reason)
    graph.add_node('captcha_bypass', captcha_bypass)
    graph.add_node('plan', plan)
    graph.add_node('execute', execute)
    graph.add_node('trust_check', trust_check)
    graph.add_node('submit', submit)

    # ── Entry ──
    graph.set_entry_point('pre_check')

    # ── Edges ──
    graph.add_conditional_edges('pre_check', route_pre_check)
    graph.add_edge('classify_link', 'observe')

    # Core loop: observe → reason → route
    graph.add_edge('observe', 'reason')
    graph.add_conditional_edges('reason', route_reason)

    # CAPTCHA bypass loops back to observe
    graph.add_edge('captcha_bypass', 'observe')

    # Plan → route (to execute or trust_check if roadblock)
    graph.add_conditional_edges('plan', route_plan)

    # Execute → route (to execute, observe, or trust_check)
    graph.add_conditional_edges('execute', route_execute)

    # Terminal
    graph.add_conditional_edges('trust_check', route_trust)
    graph.add_edge('submit', END)

    return graph


def get_compiled_graph():
    """Get the compiled graph with checkpointing enabled."""
    graph = build_apply_graph()
    checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)
