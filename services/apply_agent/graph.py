"""
LangGraph StateGraph definition for the auto-apply agent.

This wires the node functions from nodes.py into the state machine
defined in the plan. The graph runs inside a single Celery task.

Checkpoint persistence uses SQLite (via langgraph-checkpoint-sqlite)
to support pause/resume — e.g. when an application is parked for
human review and later approved.
"""
import os
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from django.conf import settings

from services.apply_agent.state import ApplyState
from services.apply_agent.nodes import (
    pre_check,
    classify_link,
    observe,
    check_cache,
    replay,
    llm_decide,
    trust_check,
    submit,
)


# ── Checkpoint storage ──────────────────────────────────────────────
# Store LangGraph checkpoints in the project's base directory
CHECKPOINT_DB_PATH = os.path.join(
    getattr(settings, 'BASE_DIR', '.'),
    'apply_checkpoints.db'
)


def get_checkpointer():
    """Get the SQLite checkpointer for LangGraph state persistence."""
    return SqliteSaver.from_conn_string(CHECKPOINT_DB_PATH)


# ── Router functions ────────────────────────────────────────────────

def route_pre_check(state: ApplyState) -> str:
    """Route after pre_check: proceed to classify or terminate."""
    if state['status'] in ('SKIPPED', 'ESCALATED'):
        return END
    return 'classify_link'


def route_cache(state: ApplyState) -> str:
    """Route after cache check: replay if hit, LLM if miss."""
    if state.get('_cache_hit'):
        return 'replay'
    return 'llm_decide'


def route_verify(state: ApplyState) -> str:
    """Route after replay: if verification passed continue, else fall through to LLM."""
    # TODO: implement verification logic — for now always continue to observe
    return 'observe'


def route_trust(state: ApplyState) -> str:
    """Route after trust check: auto-submit or park for review."""
    if state['status'] == 'PENDING_REVIEW':
        return END  # Celery task ends, resumes later via resume_application
    return 'submit'


def route_submit(state: ApplyState) -> str:
    """After submit, always end."""
    return END


def is_at_submit(state: ApplyState) -> str:
    """
    After observe: detect if we're at a submit page, an empty page,
    or if there are more fields to process.
    
    Routes to:
    - 'trust_check': submit page detected (no more fields, or _has_submit flag)
    - 'check_cache': more fields to process
    """
    # Safety valve: don't loop forever
    if state.get('step_number', 0) > 20:
        return 'trust_check'

    # Escalation from a previous node
    if state.get('status') in ('ESCALATED', 'SKIPPED'):
        return END

    # If the LLM already told us there's a submit button, trust that
    if state.get('_has_submit'):
        return 'trust_check'

    # No fields means we're on a confirmation/thank-you page or a page
    # without a form — either way, we're done filling
    if not state.get('field_labels'):
        return 'trust_check'

    # More fields to process
    return 'check_cache'


def route_llm(state: ApplyState) -> str:
    """Route after llm_decide: if escalated, end; otherwise re-observe."""
    if state.get('status') in ('ESCALATED', 'SKIPPED'):
        return END
    return 'observe'


# ── Graph definition ────────────────────────────────────────────────

def build_apply_graph() -> StateGraph:
    """
    Build and compile the auto-apply LangGraph.
    
    State Machine:
        pre_check → classify_link → observe ↔ (check_cache → replay/llm_decide) → trust_check → submit
    
    Note: 'act' is no longer a separate node — actual browser actions
    happen inside replay() and llm_decide() for tight coupling with
    verification. The loop is:
    
        observe → check_cache → replay (cache hit) or llm_decide (cache miss) → observe → ...
    """
    graph = StateGraph(ApplyState)

    # Add nodes
    graph.add_node('pre_check', pre_check)
    graph.add_node('classify_link', classify_link)
    graph.add_node('observe', observe)
    graph.add_node('check_cache', check_cache)
    graph.add_node('replay', replay)
    graph.add_node('llm_decide', llm_decide)
    graph.add_node('trust_check', trust_check)
    graph.add_node('submit', submit)

    # Entry point
    graph.set_entry_point('pre_check')

    # Edges
    graph.add_conditional_edges('pre_check', route_pre_check)
    graph.add_edge('classify_link', 'observe')
    graph.add_conditional_edges('observe', is_at_submit)
    graph.add_conditional_edges('check_cache', route_cache)
    graph.add_edge('replay', 'observe')  # after replay, re-observe the page
    graph.add_conditional_edges('llm_decide', route_llm)  # can escalate or re-observe
    graph.add_conditional_edges('trust_check', route_trust)
    graph.add_edge('submit', END)

    return graph


def get_compiled_graph():
    """Get the compiled graph with checkpointing enabled."""
    graph = build_apply_graph()
    checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)

