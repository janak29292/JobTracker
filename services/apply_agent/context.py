"""
Runtime context for the auto-apply agent.

Holds non-serializable resources (Playwright page, OllamaClient) that
can't travel through LangGraph state but are needed by every node.

The entry point (auto_apply.py) sets up the context before invoking
the graph, and nodes access it via get_context().

This is a thread-local singleton — safe for Celery workers since each
worker processes one task at a time.
"""
import logging
import threading
from dataclasses import dataclass, field
from typing import Optional, Any, List

from django.conf import settings
from playwright.sync_api import Page, BrowserContext

from services.ollama_client import OllamaClient
from user.models import ApplicantProfile

logger = logging.getLogger(__name__)


@dataclass
class ApplyContext:
    """Non-serializable resources shared across all nodes in a single run."""
    page: Optional[Page] = None
    browser_context: Optional[BrowserContext] = None
    client: Optional[OllamaClient] = None
    profile: Optional[ApplicantProfile] = None
    method: str = 'instructor'  # LLM method: 'instructor' or 'raw_generate'
    model_name: str = getattr(settings, 'OLLAMA_MODEL_REASONING', 'gemma4:26b')
    
    # Preloaded lightweight catalog: [{"id": q.id, "text": q.pattern}]
    question_catalog: List[dict[str, Any]] = field(default_factory=list)

    # Structured applicant data bundle (profile + work experiences + education + projects)
    applicant_bundle: dict[str, Any] = field(default_factory=dict)

    # Active multi-tab tracking
    all_pages: List[Page] = field(default_factory=list)

    def attach_tab_listener(self, browser_ctx: BrowserContext):
        """Listen for popups/external ATS tabs and switch active control automatically."""
        self.browser_context = browser_ctx
        if self.page and self.page not in self.all_pages:
            self.all_pages.append(self.page)

        def _on_new_page(new_page: Page):
            logger.info(f"[ApplyContext] New browser tab opened: {new_page.url}")
            self.all_pages.append(new_page)
            # Switch active page immediately so subsequent node invocations target the new tab right away
            self.page = new_page
            try:
                new_page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception as e:
                logger.warning(f"[ApplyContext] Timeout waiting for new tab load state: {e}")
            logger.info(f"[ApplyContext] Active page switched to new tab: {new_page.url}")

        browser_ctx.on("page", _on_new_page)


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
