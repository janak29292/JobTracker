"""
Runtime context for the auto-apply agent.

Holds non-serializable resources (Playwright page, OllamaClient) that
can't travel through LangGraph state but are needed by every node.

The entry point (auto_apply.py) sets up the context before invoking
the graph, and nodes access it via get_context().

This is a thread-local singleton — safe for Celery workers since each
worker processes one task at a time.
"""
import threading
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import Page

from services.ollama_client import OllamaClient
from user.models import ApplicantProfile


@dataclass
class ApplyContext:
    """Non-serializable resources shared across all nodes in a single run."""
    page: Optional[Page] = None
    client: Optional[OllamaClient] = None
    profile: Optional[ApplicantProfile] = None
    method: str = 'instructor'  # LLM method: 'instructor' or 'raw_generate'


# Thread-local storage — each Celery worker gets its own context
_thread_local = threading.local()


def set_context(ctx: ApplyContext):
    """Set the runtime context for the current thread/worker."""
    _thread_local.apply_context = ctx


def get_context() -> ApplyContext:
    """Get the runtime context for the current thread/worker."""
    ctx = getattr(_thread_local, 'apply_context', None)
    if ctx is None:
        raise RuntimeError(
            "ApplyContext not initialized. "
            "Call set_context() before invoking the graph."
        )
    return ctx


def clear_context():
    """Clear the runtime context (called after the graph finishes)."""
    _thread_local.apply_context = None
