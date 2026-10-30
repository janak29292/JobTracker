"""
LangGraph nodes for the auto-apply agent (v2).

Architecture: Observe → Reason → Plan → Execute
Each node is a pure function: state in → state update out.
Non-serializable resources (Playwright page, LLM client) come from ApplyContext.
"""
import json
import logging
import os
import re
import time
import shutil
import tempfile
import hashlib
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field

from django.utils import timezone

from job.models import Job, ApplicationRun, StepRecord, PlatformAccount, ActionSnippet, PortalCredential
from services.apply_agent.state import ApplyState
from services.apply_agent.context import get_context
from services.apply_agent.signature import (
    extract_url_platform, strip_url_ids, compute_structural_signature,
)
from services.apply_agent.executor import _resolve_locator, _is_visible
from services.apply_agent.gesture_engine import (
    human_click, human_type, human_slider_drag
)
from services.apply_agent.field_mapper import (
    resolve_profile_value, resolve_by_question_id,
    track_encountered_question, lookup_qa_bank,
)
from services.apply_agent.credentials import (
    extract_domain, get_portal_credential, get_or_create_portal_credential,
    generate_portal_password,
)

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'media', 'apply_screenshots'
)


# ═══════════════════════════════════════════════════════════════════
# Pydantic Schemas for LLM Reasoning & Planning
# ═══════════════════════════════════════════════════════════════════

class ReasonOutput(BaseModel):
    page_type: Literal[
        "CLOSED",
        "CAPTCHA",
        "LISTING_GATEWAY",
        "ACCOUNT_GATE",
        "APPLICATION_FORM",
        "CONFIRMATION",
        "ERROR_BLOCKED"
    ]
    explanation: str
    is_closed_on_portal: bool = False
    closed_snippet_text: Optional[str] = None
    alternative_apply_found: bool = False
    alternative_apply_method: Optional[Literal["email", "external_url", "none"]] = "none"
    alternative_apply_details: Optional[str] = None
    captcha_type: Optional[str] = None
    apply_trigger_hint: Optional[str] = None


class PageTask(BaseModel):
    task_id: int
    task_description: str
    target_hint: str
    task_type: Literal["fill", "select", "check", "uncheck", "upload", "click"]
    is_completed: bool = False
    error_message: Optional[str] = None


class MacroPlanOutput(BaseModel):
    reasoning: str
    tasks: List[PageTask] = Field(default_factory=list)


class MicroActionOutput(BaseModel):
    reasoning: str
    action_type: Literal["fill", "select", "check", "uncheck", "upload", "click"]
    selector_strategy: Literal["css", "id", "role", "label", "text", "placeholder"]
    selector_value: str
    value: Optional[str] = Field(default=None, description="Exact value directly resolved from applicant bundle, credentials, or question catalog")
    is_required: bool = False
    missing_required_info: bool = False
    question_text_if_missing: Optional[str] = None
    matched_question_id: Optional[int] = None


class PageAuditOutput(BaseModel):
    audit_passed: bool
    explanation: str
    unfilled_required_fields: List[str] = Field(default_factory=list)
    visible_error_messages: List[str] = Field(default_factory=list)
    corrective_tasks: List[PageTask] = Field(default_factory=list)
    ready_to_submit: bool = False


class NextMoveOutput(BaseModel):
    action_label: str = Field(description="Description of action, e.g. 'Click Next Button' or 'Submit Application'")
    button_text: str = Field(description="Visible text of button, e.g. 'Next', 'Review', 'Continue', 'Submit application'")
    selector_strategy: Literal["id", "role", "css", "text"]
    selector_value: str
    is_terminal_submission: bool = Field(default=False, description="True ONLY if this action actually submits the entire application, False if it is an intermediate step like Next or Review")
    explanation: str


class ButtonScrutinyOutput(BaseModel):
    is_legitimate_submit_button: bool
    is_social_or_trap_link: bool
    explanation: str
    selector_strategy: Literal["css", "id", "role", "text"]
    selector_value: str


class PlannedAction(BaseModel):
    target_label: str
    action_type: Literal["fill", "select", "check", "uncheck", "upload", "click"]
    selector_strategy: Literal["css", "id", "role", "label", "text", "placeholder"]
    selector_value: str
    value: Optional[str] = None
    value_source: Optional[Literal["profile_attr", "question_catalog", "constant", "navigation"]] = "constant"
    profile_attr: Optional[str] = None
    matched_question_id: Optional[int] = None
    constant_value: Optional[str] = None
    is_required: bool = False
    missing_required_info: bool = False
    missing_optional_info: bool = False
    question_text_if_missing: Optional[str] = None


class PlanOutput(BaseModel):
    reasoning: str
    actions: List[PlannedAction] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# Extraction & Observation Helpers
# ═══════════════════════════════════════════════════════════════════

def _skip(run: ApplicationRun, reason: str) -> dict:
    """Mark a run as SKIPPED and return the terminal state update."""
    run.status = 'SKIPPED'
    run.skip_reason = reason
    run.save()
    logger.info(f"[apply run={run.id}] SKIPPED: {reason}")
    return {'status': 'SKIPPED', 'escalation_reason': reason}


def _take_screenshot(page, run_id: int, step: int) -> Optional[str]:
    """Take a screenshot and return the path, or None on failure."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    filename = f"run_{run_id}_step_{step}.png"
    path = os.path.join(SCREENSHOT_DIR, filename)
    try:
        page.screenshot(path=path, full_page=False)
        return path
    except Exception as e:
        logger.warning(f"Screenshot failed: {e}")
        return None


def _extract_visible_text(page) -> str:
    """Extract visible body innerText, prioritizing any active application modal/drawer."""
    try:
        data = page.evaluate(r"""() => {
            const dialog = document.querySelector('dialog[open], [role="dialog"], [aria-modal="true"], div[data-test-modal], .jobs-easy-apply-modal, ._chatBotContainer, .modal.show');
            let activeModal = null;
            if (dialog) {
                const rect = dialog.getBoundingClientRect();
                const style = window.getComputedStyle(dialog);
                if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden') {
                    activeModal = dialog;
                }
            }
            if (!activeModal) {
                const overlay = document.querySelector('.chatbot_Overlay.show, .modal-backdrop.show, .overlay.show');
                if (overlay) {
                    activeModal = document.querySelector('._chatBotContainer') || overlay.parentElement;
                }
            }

            const modalText = activeModal ? (activeModal.innerText || '').trim() : '';
            const bodyText = document.body ? (document.body.innerText || '').trim() : '';
            return { modalText, bodyText };
        }""")
        if not data:
            return ''
        modal_text = data.get('modalText', '')
        body_text = data.get('bodyText', '')
        if modal_text:
            combined = f"=== ACTIVE APPLICATION DIALOG / CHATBOT DRAWER ===\n{modal_text}\n\n=== BACKGROUND PAGE CONTEXT ===\n{body_text[:5000]}"
        else:
            combined = body_text[:15000]
        return re.sub(r'\n{3,}', '\n\n', combined).strip()
    except Exception as e:
        logger.warning(f"Visible text extraction failed: {e}")
        return ''


def _extract_visible_dom(page) -> str:
    """Extract cleaned, trimmed HTML from the visible page area for signature hashing."""
    try:
        dom = page.evaluate(r"""() => {
            const body = document.body;
            if (!body) return '';
            const clone = body.cloneNode(true);
            clone.querySelectorAll('script, style, noscript, [hidden], [aria-hidden="true"]')
                .forEach(el => el.remove());
            return clone.innerHTML.substring(0, 50000);
        }""")
        return dom or ''
    except Exception as e:
        logger.warning(f"DOM extraction failed: {e}")
        return ''


def _extract_page_errors(page) -> list[str]:
    """
    Extract visible validation errors, alert banners, and form feedback messages.
    Covers standard HTML5, Bootstrap, SAP SuccessFactors, Workday, Taleo, and custom styles.
    """
    try:
        errors = page.evaluate(r"""() => {
            const found = new Set();
            const alertSelectors = [
                '[role="alert"]',
                '.alert-danger',
                '.alert-error',
                '.alert-warning',
                '.errorMessage',
                '.error-message',
                '.error_message',
                '.error-text',
                '.errortext',
                '.invalid-feedback',
                '.form-error',
                '[class*="error-message" i]',
                '[class*="field-error" i]',
                '[class*="validation-error" i]',
                'span[id*="error" i]',
                'div[id*="error" i]',
                'span[id*="err" i]',
                'div[class*="status-msg" i]'
            ];
            for (const sel of alertSelectors) {
                document.querySelectorAll(sel).forEach(el => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden') {
                        const txt = (el.textContent || '').trim();
                        if (txt && txt.length < 300 && !txt.startsWith('http')) {
                            found.add(txt);
                        }
                    }
                });
            }

            // Elements with aria-invalid="true"
            document.querySelectorAll('[aria-invalid="true"]').forEach(el => {
                const descId = el.getAttribute('aria-describedby');
                if (descId) {
                    const descEl = document.getElementById(descId);
                    if (descEl) {
                        const txt = (descEl.textContent || '').trim();
                        if (txt && txt.length < 300) found.add(txt);
                    }
                }
                const parent = el.closest('tr, [class*="group" i], [class*="row" i], div');
                if (parent) {
                    const errEl = parent.querySelector('[class*="error" i], [class*="alert" i], span, small');
                    if (errEl && errEl !== el) {
                        const txt = (errEl.textContent || '').trim();
                        if (txt && txt.length < 200 && /required|invalid|must|enter|valid|match/i.test(txt)) {
                            found.add(txt);
                        }
                    }
                }
            });

            return Array.from(found).slice(0, 10);
        }""")
        return [re.sub(r'\s+', ' ', e).strip() for e in (errors or []) if e.strip()]
    except Exception as e:
        logger.warning(f"Error extraction failed: {e}")
        return []


def _extract_form_fields(page) -> list[dict]:
    """
    Extract visible form inputs, dropdowns, and textareas with their labels, IDs, and options.
    Scopes to active modal/drawer if present, and preserves file upload inputs even if visually hidden.
    """
    try:
        fields = page.evaluate(r"""() => {
            const dialog = document.querySelector('dialog[open], [role="dialog"], [aria-modal="true"], div[data-test-modal], .jobs-easy-apply-modal, ._chatBotContainer, .modal.show');
            let root = null;
            if (dialog) {
                const rect = dialog.getBoundingClientRect();
                const style = window.getComputedStyle(dialog);
                if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden') {
                    root = dialog;
                }
            }
            if (!root) {
                const overlay = document.querySelector('.chatbot_Overlay.show, .modal-backdrop.show, .overlay.show');
                if (overlay) {
                    root = document.querySelector('._chatBotContainer') || overlay.parentElement;
                }
            }
            if (!root) root = document;

            const results = [];
            const inputs = root.querySelectorAll(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), ' +
                'input[type="file"], select, textarea'
            );
            for (const el of inputs) {
                const isFile = (el.tagName.toLowerCase() === 'input' && el.type === 'file');
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);

                if (!isFile) {
                    if (rect.width === 0 || rect.height === 0) continue;
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                }

                let label = '';
                // 1. label[for="id"]
                if (el.id) {
                    const labelEl = document.querySelector(`label[for="${el.id}"]`);
                    if (labelEl) label = labelEl.textContent.trim();
                }
                // 2. aria-labelledby
                if (!label) {
                    const labelledby = el.getAttribute('aria-labelledby');
                    if (labelledby) {
                        const parts = labelledby.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean);
                        if (parts.length > 0) label = parts.map(p => p.textContent.trim()).join(' ');
                    }
                }
                // 3. closest label
                if (!label) {
                    const parent = el.closest('label');
                    if (parent) label = parent.textContent.trim();
                }
                // 4. parent row/cell label (e.g. table in SAP SuccessFactors, form-group)
                if (!label) {
                    const row = el.closest('tr, [class*="row" i], [class*="form-group" i], [class*="field" i]');
                    if (row) {
                        const rowLabel = row.querySelector('label, [class*="label" i], th, td.promptCell, td:first-child');
                        if (rowLabel && rowLabel !== el) {
                            label = rowLabel.textContent.trim();
                        }
                    }
                }
                // 5. file upload container text
                if (!label && isFile) {
                    const uploadParent = el.closest('[class*="upload"], [id*="upload"], .chatbot_Message, div');
                    if (uploadParent) {
                        const text = uploadParent.textContent.trim().replace(/\s+/g, ' ');
                        if (text && text.length < 150) label = text;
                    }
                    if (!label) label = 'Upload Resume';
                }
                // 6. aria-label / placeholder / name
                if (!label) label = el.getAttribute('aria-label') || '';
                if (!label) label = el.getAttribute('placeholder') || '';
                if (!label) label = el.getAttribute('name') || '';
                if (!label && isFile) label = 'Upload Resume';

                // Extract current value, filled status, and autocomplete capability
                let currentValue = '';
                if (el.tagName.toLowerCase() === 'select') {
                    if (el.selectedIndex >= 0 && el.options[el.selectedIndex]) {
                        currentValue = (el.options[el.selectedIndex].textContent || el.options[el.selectedIndex].value || '').trim();
                    }
                } else if (el.type === 'checkbox' || el.type === 'radio') {
                    currentValue = el.checked ? 'true' : 'false';
                } else if (!isFile) {
                    currentValue = (el.value || '').trim();
                }

                const cleanLabel = label.replace(/\s+/g, ' ').trim();
                const isRequired = isFile ? true : (
                    el.hasAttribute('required') ||
                    el.getAttribute('aria-required') === 'true' ||
                    /\*/.test(cleanLabel) ||
                    /required/i.test(cleanLabel)
                );

                const isFilled = Boolean(
                    currentValue &&
                    currentValue !== 'false' &&
                    currentValue !== '--' &&
                    !/^select\b/i.test(currentValue)
                );

                const isAutocomplete = Boolean(
                    el.getAttribute('role') === 'combobox' ||
                    el.getAttribute('aria-autocomplete') === 'list' ||
                    (el.className && typeof el.className === 'string' && el.className.toLowerCase().includes('typeahead')) ||
                    /city|location|address/i.test(cleanLabel)
                );

                // Extract ALL options if select (no arbitrary truncation)
                let options = [];
                if (el.tagName.toLowerCase() === 'select') {
                    for (const opt of el.options) {
                        const optText = (opt.textContent || opt.value || '').trim();
                        if (optText) options.push(optText);
                    }
                }

                results.push({
                    label: cleanLabel.substring(0, 200),
                    type: isFile ? 'file' : (el.tagName.toLowerCase() === 'select' ? 'select' :
                          el.tagName.toLowerCase() === 'textarea' ? 'textarea' :
                          (el.getAttribute('type') || 'text')),
                    name: el.getAttribute('name') || '',
                    id: el.id || '',
                    required: isRequired,
                    current_value: currentValue.substring(0, 200),
                    is_filled: isFilled,
                    is_autocomplete: isAutocomplete,
                    placeholder: el.getAttribute('placeholder') || '',
                    options: options,
                });
            }
            return results;
        }""")
        return fields or []
    except Exception as e:
        logger.warning(f"Field extraction failed: {e}")
        return []


def _detect_buttons(page) -> dict:
    """Detect key buttons on the page, prioritizing active modals and collecting action chips."""
    try:
        return page.evaluate(r"""() => {
            const dialog = document.querySelector('dialog[open], [role="dialog"], [aria-modal="true"], div[data-test-modal], .jobs-easy-apply-modal, ._chatBotContainer, .modal.show');
            let root = null;
            if (dialog) {
                const rect = dialog.getBoundingClientRect();
                const style = window.getComputedStyle(dialog);
                if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden') {
                    root = dialog;
                }
            }
            if (!root) {
                const overlay = document.querySelector('.chatbot_Overlay.show, .modal-backdrop.show, .overlay.show');
                if (overlay) {
                    root = document.querySelector('._chatBotContainer') || overlay.parentElement;
                }
            }
            const isModal = Boolean(root);
            if (!root) root = document;

            const result = {
                apply: null, next: null, submit: null, continue_btn: null,
                sso_google: null, sso_linkedin: null, create_account: null, sign_in: null,
                modal_active: isModal, action_chips: []
            };

            const buttons = root.querySelectorAll('button, a, input[type="submit"], div[role="button"], span[role="button"], .chip, [class*="chip"]');
            for (const btn of buttons) {
                const text = (btn.textContent || btn.value || '').trim();
                const lower = text.toLowerCase();
                const rect = btn.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;

                if (/easy apply|apply now|^apply$/i.test(lower) && !result.apply) {
                    result.apply = text;
                }
                if (/^next$|^continue$|^proceed$/i.test(lower) && !result.next) {
                    result.next = text;
                }
                if (/submit|send application|^finish$|create account|register/i.test(lower) && !result.submit) {
                    result.submit = text;
                }
                if (/sign in with google|continue with google|apply with google/i.test(lower) && !result.sso_google) {
                    result.sso_google = text;
                }
                if (/apply with linkedin|sign in with linkedin|continue with linkedin/i.test(lower) && !result.sso_linkedin) {
                    result.sso_linkedin = text;
                }
                if (/create (an )?account|^register$|sign up/i.test(lower) && !result.create_account) {
                    result.create_account = text;
                }
                if (/^sign in$|^login$|^log in$/i.test(lower) && !result.sign_in) {
                    result.sign_in = text;
                }
                if (isModal && text.length > 0 && text.length < 50 && !result.action_chips.includes(text)) {
                    result.action_chips.push(text);
                }
            }
            return result;
        }""")
    except Exception:
        return {
            'apply': None, 'next': None, 'submit': None, 'continue_btn': None,
            'sso_google': None, 'sso_linkedin': None, 'create_account': None, 'sign_in': None,
            'modal_active': False, 'action_chips': []
        }


# ═══════════════════════════════════════════════════════════════════
# NODE: pre_check
# ═══════════════════════════════════════════════════════════════════

def pre_check(state: ApplyState) -> dict:
    """
    Deterministic pre-flight checks before entering the apply loop.
    Checks: already applied, exact-URL duplicate, platform account health.
    """
    logger.info(f"[apply run={state['run_id']}] entering pre_check")

    job = Job.objects.get(id=state['job_id'])
    run = ApplicationRun.objects.get(id=state['run_id'])
    apply_url = state['apply_url']

    # 1. Already applied?
    if job.status == 'AF':
        return _skip(run, 'Already applied')

    # 2. Exact-URL duplicate
    if Job.objects.filter(
        apply_url=apply_url, status='AF'
    ).exclude(id=job.id).exists():
        return _skip(run, 'Duplicate — same apply URL already submitted')

    # 3. Platform account health
    platform = extract_url_platform(apply_url)
    try:
        account = PlatformAccount.objects.get(platform=platform)
        if not account.is_healthy:
            return _skip(run, f'Platform account unhealthy: {platform}')
        if account.daily_count >= account.daily_limit:
            return _skip(run, f'Daily limit reached for {platform} ({account.daily_limit})')
    except PlatformAccount.DoesNotExist:
        pass

    # All checks passed
    run.status = 'IN_PROGRESS'
    run.started_at = timezone.now()
    run.save()

    return {'status': 'IN_PROGRESS', 'step_number': 0, 'had_fresh_decisions': False}


# ═══════════════════════════════════════════════════════════════════
# NODE: classify_link
# ═══════════════════════════════════════════════════════════════════

def classify_link(state: ApplyState) -> dict:
    """
    Navigate to the normalized apply URL and classify the destination.
    """
    logger.info(f"[apply run={state['run_id']}] entering classify_link")

    ctx = get_context()
    apply_url = state['apply_url']

    try:
        ctx.page.goto(apply_url, wait_until='domcontentloaded', timeout=30000)
        ctx.page.wait_for_timeout(2000)
    except Exception as e:
        logger.error(f"Failed to navigate to {apply_url}: {e}")
        run = ApplicationRun.objects.get(id=state['run_id'])
        return _skip(run, f'Navigation failed: {e}')

    final_url = ctx.page.url
    run = ApplicationRun.objects.get(id=state['run_id'])

    # Email-only detection
    if final_url.startswith('mailto:') or re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', final_url):
        run.apply_type = 'email_only'
        run.status = 'ESCALATED'
        run.escalation_reason = 'Email-only application — needs manual send'
        run.final_url = final_url
        run.save()
        return {
            'status': 'ESCALATED',
            'apply_type': 'email_only',
            'escalation_reason': run.escalation_reason,
            'current_url': final_url,
        }

    # Cross-platform duplicate check
    run.final_url = final_url
    stripped_final = strip_url_ids(final_url)
    if ApplicationRun.objects.filter(
        final_url__icontains=stripped_final, status='SUBMITTED'
    ).exclude(job_id=state['job_id']).exists():
        return _skip(run, 'Duplicate — same destination already submitted via another source')

    # Classify platform
    platform = extract_url_platform(final_url)
    PLATFORM_TYPE_MAP = {
        'linkedin': 'native_linkedin', 'naukri': 'native_naukri',
        'greenhouse': 'ats_greenhouse', 'lever': 'ats_lever', 'workday': 'ats_workday',
        'icims': 'ats_other', 'smartrecruiters': 'ats_other',
        'ashbyhq': 'ats_other', 'bamboohr': 'ats_other', 'jobvite': 'ats_other',
    }
    apply_type = PLATFORM_TYPE_MAP.get(platform, 'custom_site')
    run.apply_type = apply_type
    run.save()

    return {'apply_type': apply_type, 'current_url': final_url}


# ═══════════════════════════════════════════════════════════════════
# NODE: observe
# ═══════════════════════════════════════════════════════════════════

def observe(state: ApplyState) -> dict:
    """
    Pure observation — captures URL, visible innerText (JD), form metadata, and screenshot.
    Seamlessly synchronizes with ctx.page (which switches on multi-tab ATS opens).
    """
    logger.info(f"[apply run={state['run_id']}] entering observe (step {state.get('step_number', 0) + 1})")

    ctx = get_context()

    # If the browser context opened a new tab (e.g. redirected to external company ATS),
    # ensure ctx.page immediately targets the active frontmost tab
    if ctx.browser_context and ctx.browser_context.pages:
        newest_page = ctx.browser_context.pages[-1]
        if ctx.page != newest_page:
            logger.info(f"[observe] Switching active page to newest tab: {newest_page.url}")
            ctx.page = newest_page
            try:
                ctx.page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass

    page = ctx.page
    step = state.get('step_number', 0) + 1

    # If an application drawer or chatbot is open, wait for any typing/loading animations to finish
    try:
        if page.locator('._chatBotContainer').count() > 0:
            page.wait_for_timeout(600)
            page.wait_for_selector(
                '._chatBotContainer [class*="typing"], ._chatBotContainer [class*="dot"], ._chatBotContainer [class*="loader"]',
                state='detached',
                timeout=7000
            )
            page.wait_for_timeout(1000)
    except Exception:
        pass
    page.wait_for_timeout(1000)

    screenshot_path = _take_screenshot(page, state['run_id'], step)
    current_url = page.url
    page_text = _extract_visible_text(page)
    page_errors = _extract_page_errors(page)
    dom_snapshot = _extract_visible_dom(page)
    dom_signature = compute_structural_signature(current_url, dom_snapshot)

    if page_errors:
        logger.info(f"[observe] Detected {len(page_errors)} page errors/validation warnings: {page_errors}")

    return {
        'step_number': step,
        'current_url': current_url,
        'screenshot_path': screenshot_path,
        'page_text': page_text,
        'page_errors': page_errors,
        'dom_snapshot': dom_snapshot,
        'dom_signature': dom_signature,
    }


# ═══════════════════════════════════════════════════════════════════
# NODE: reason
# ═══════════════════════════════════════════════════════════════════

def reason(state: ApplyState) -> dict:
    """
    LLM reasoning engine for page classification.
    Zero hardcoded string checks in code.
    If portal indicates closed, inspects full JD text for recruiter emails/links before marking 'AC'.
    """
    logger.info(f"[apply run={state['run_id']}] entering reason (step {state.get('step_number', 0)})")

    ctx = get_context()
    url = state.get('current_url', '')
    page_text = state.get('page_text', '') or ''
    platform = extract_url_platform(url)

    # ── Cache Fast-Path Check for Learned Closed Indicators ──
    cached_closed = ActionSnippet.objects.filter(
        platform=platform,
        snippet_type='closed_text'
    ).first()
    if cached_closed:
        closed_phrase = cached_closed.action.get('text', '')
        if closed_phrase and closed_phrase.lower() in page_text.lower():
            logger.info(f"[reason] Cache HIT: learned closed phrase '{closed_phrase}' found on page.")
            run = ApplicationRun.objects.get(id=state['run_id'])
            run.status = 'SKIPPED'
            run.skip_reason = f"Learned closed indicator: {closed_phrase}"
            run.save()
            job = Job.objects.get(id=state['job_id'])
            job.status = 'AC'
            job.save()
            return {
                'page_type': 'CLOSED',
                'status': 'SKIPPED',
                'escalation_reason': run.skip_reason,
                'page_analysis': f"Closed via learned pattern: {closed_phrase}"
            }

    # ── LLM Structured Reasoning ──
    prompt = f"""You are an expert autonomous job application reasoning agent.
Inspect the visible text and URL of the current webpage to understand its state. Do NOT rely on pre-assumed keywords. Read the visible page text and the full Job Description carefully.

Current URL: {url}

VISIBLE PAGE TEXT (including Job Description):
{page_text[:12000]}

Classify the page into one of:
1. CLOSED: The job posting is closed, expired, filled, or no longer accepting applications on the portal.
   - CRITICAL INSTRUCTION: Even if the portal states 'No longer accepting applications', carefully read the entire Job Description (JD). Often recruiters write: 'Applications on LinkedIn are closed, but send resume directly to careers@company.com' or 'Apply at company.com/careers'.
   - If an alternative direct apply method is found in the JD: set alternative_apply_found = true, alternative_apply_method = 'email' (or 'external_url'), and extract the contact in alternative_apply_details.
   - If NO alternative contact or apply route exists anywhere in the JD, set is_closed_on_portal = true and closed_snippet_text = <exact closed banner text>.
2. CAPTCHA: The page is interrupted by Cloudflare Turnstile, hCaptcha, reCAPTCHA, or a bot verification challenge.
3. LISTING_GATEWAY: The job posting is active and displays an apply button ('Easy Apply', 'Apply on company website', 'Apply Now').
4. ACCOUNT_GATE: The page is a corporate ATS sign-in, login, or registration gate (e.g. 'Career Opportunities: Sign In', 'Already have an account?', 'Sign in to apply', 'Create an account to apply', 'Sign in with Google').
5. APPLICATION_FORM: An active application modal, form, or ATS wizard with input fields to fill (personal details, experience, resume upload, screening questions).
6. CONFIRMATION: The application has been submitted (shows 'Application submitted', 'Thank you for applying', 'Application sent', 'Successfully applied', 'profile has been updated', 'applied on', 'application successfully sent').
   - CRITICAL NEGATIVE CONSTRAINT: Notices stating that the applicant was redirected to an external or company website (e.g. 'You were redirected to the company website for completing your job application process', 'Redirected to employer site', 'Apply on employer website', 'Application initiated externally') are NOT CONFIRMATION. They mean the application has NOT been submitted yet! NEVER classify external redirect or off-portal notices as CONFIRMATION.
7. ERROR_BLOCKED: A fatal HTTP 500, access denied, or unrecoverable error."""

    try:
        output, _ = ctx.client.call_model_structured(prompt, ReasonOutput, method=ctx.method)
    except Exception as e:
        logger.error(f"[reason] LLM reasoning call failed: {e}")
        output = ReasonOutput(page_type="ERROR_BLOCKED", explanation=f"Reasoning failure: {e}")

    # ── Deterministic Guard Against False CONFIRMATION on Redirect Notices ──
    is_external_redirect = bool(re.search(
        r'you were redirected to (the )?company website|redirected to (the )?employer|continue on company site',
        page_text,
        re.I
    ))
    if output.page_type == 'CONFIRMATION' and is_external_redirect:
        logger.warning("[reason] Guard triggered: LLM misclassified external redirect notice as CONFIRMATION. Overriding to LISTING_GATEWAY.")
        output.page_type = 'LISTING_GATEWAY'
        output.explanation = "Notice states applicant was redirected to external company website; application is not yet submitted."

    logger.info(f"[reason] LLM classified page as: {output.page_type} — {output.explanation}")

    # ── Branch Handling ──
    if output.page_type == 'CLOSED':
        run = ApplicationRun.objects.get(id=state['run_id'])
        job = Job.objects.get(id=state['job_id'])

        if output.alternative_apply_found:
            reason_msg = f"Portal closed, but JD specifies apply via {output.alternative_apply_method}: {output.alternative_apply_details}"
            run.status = 'PENDING_REVIEW'
            run.escalation_reason = reason_msg
            run.save()
            job.status = 'PR'
            job.save()
            return {
                'page_type': 'CLOSED',
                'status': 'PENDING_REVIEW',
                'escalation_reason': reason_msg,
                'page_analysis': output.explanation,
                'alternative_apply_method': output.alternative_apply_method,
                'alternative_apply_details': output.alternative_apply_details,
            }
        else:
            run.status = 'SKIPPED'
            run.skip_reason = output.explanation
            run.save()
            job.status = 'AC'
            job.save()
            return {
                'page_type': 'CLOSED',
                'status': 'SKIPPED',
                'escalation_reason': output.explanation,
                'page_analysis': output.explanation,
            }

    if output.page_type == 'CONFIRMATION':
        run = ApplicationRun.objects.get(id=state['run_id'])
        run.status = 'SUBMITTED'
        run.completed_at = timezone.now()
        run.save()
        job = Job.objects.get(id=state['job_id'])
        job.status = 'AF'
        job.applied_on = timezone.now().date()
        job.save()
        return {
            'page_type': 'CONFIRMATION',
            'status': 'SUBMITTED',
            'page_analysis': output.explanation,
        }

    if output.page_type == 'ERROR_BLOCKED':
        run = ApplicationRun.objects.get(id=state['run_id'])
        run.status = 'ESCALATED'
        run.escalation_reason = output.explanation
        run.save()
        return {
            'page_type': 'ERROR_BLOCKED',
            'status': 'ESCALATED',
            'escalation_reason': output.explanation,
            'page_analysis': output.explanation,
        }

    return {
        'page_type': output.page_type,
        'page_analysis': output.explanation,
    }


# ═══════════════════════════════════════════════════════════════════
# NODE: captcha_bypass
# ═══════════════════════════════════════════════════════════════════

def captcha_bypass(state: ApplyState) -> dict:
    """
    Active challenge handling — attempts to resolve Cloudflare Turnstile or checkbox challenges.
    """
    logger.info(f"[apply run={state['run_id']}] entering captcha_bypass (attempt {state.get('captcha_attempts', 0) + 1})")
    ctx = get_context()
    page = ctx.page
    attempts = state.get('captcha_attempts', 0) + 1

    if attempts > 2:
        run = ApplicationRun.objects.get(id=state['run_id'])
        run.status = 'PENDING_REVIEW'
        run.escalation_reason = "CAPTCHA / bot challenge persisted after 2 bypass attempts"
        run.save()
        job = Job.objects.get(id=state['job_id'])
        job.status = 'PR'
        job.save()
        return {
            'status': 'PENDING_REVIEW',
            'escalation_reason': run.escalation_reason,
            'captcha_attempts': attempts,
        }

    # Locate interactive checkbox or slider inside challenge frames or on page
    clicked = False
    try:
        for frame in page.frames:
            try:
                checkbox = frame.locator('input[type="checkbox"], #challenge-stage input, .cf-turnstile-wrapper')
                if checkbox.count() > 0 and checkbox.first.is_visible(timeout=1000):
                    time.sleep(0.2)
                    human_click(page, checkbox.first, platform='cloudflare')
                    clicked = True
                    logger.info("[captcha_bypass] Clicked challenge checkbox inside iframe via human_click.")
                    break
            except Exception:
                continue

        if not clicked:
            challenge = page.locator('#challenge-stage, iframe[src*="turnstile"], iframe[src*="recaptcha"]').first
            if challenge.is_visible(timeout=1000):
                time.sleep(0.2)
                human_click(page, challenge, platform='cloudflare')
                clicked = True
                logger.info("[captcha_bypass] Clicked challenge container on page via human_click.")
    except Exception as e:
        logger.warning(f"CAPTCHA bypass interaction error: {e}")

    # Wait for clearance
    page.wait_for_timeout(3000)

    return {'captcha_attempts': attempts}


# ═══════════════════════════════════════════════════════════════════
# NODE: plan
# ═══════════════════════════════════════════════════════════════════

def plan(state: ApplyState) -> dict:
    """
    Macro Planning Node (Hierarchical v2):
    Inspects the screen and decomposes the page into an ordered checklist of concrete PageTasks.
    Does NOT hardcode container selectors, form field keys, or regex mappings.
    """
    logger.info(f"[apply run={state['run_id']}] entering plan (page_type={state.get('page_type')})")

    ctx = get_context()
    page = ctx.page
    page_type = state.get('page_type', 'unknown')

    # Extract interactive elements
    fields = _extract_form_fields(page)
    buttons = _detect_buttons(page)

    # 1. Gateway listing page without active modal
    if page_type == 'LISTING_GATEWAY' and not buttons.get('modal_active'):
        gateway_actions = _plan_gateway_actions(page, state)
        if gateway_actions:
            tasks = []
            for i, act in enumerate(gateway_actions, 1):
                tasks.append({
                    'task_id': i,
                    'task_description': f"Click {act.get('target_label', 'Apply')}",
                    'target_hint': act.get('target_label', 'Apply'),
                    'task_type': act.get('action_type', 'click'),
                    'is_completed': False,
                    'error_message': None,
                    '_preplanned_action': act,
                })
            return {
                'page_tasks': tasks,
                'current_task_index': 0,
                'page_audited': False,
                'macro_reasoning': "Listing gateway: locate and click apply trigger to start application.",
                'checklist': gateway_actions,
                'checklist_index': 0,
            }

    # 2. Corporate ATS Account Gates (SSO / Stored Login / Create Account)
    current_url_lower = (page.url or '').lower()
    is_account_gate = (page_type == 'ACCOUNT_GATE')
    if not is_account_gate and 'linkedin.com' not in current_url_lower and 'naukri.com' not in current_url_lower:
        is_account_gate = bool(
            re.search(r'career opportunities:\s*sign in|already have an account|create an account to apply', state.get('page_text', '') or '', re.I)
        )
    if is_account_gate:
        account_gate_actions = _plan_account_gate_actions(page, state)
        if account_gate_actions:
            tasks = []
            for i, act in enumerate(account_gate_actions, 1):
                tasks.append({
                    'task_id': i,
                    'task_description': f"{act.get('action_type', 'click')} {act.get('target_label', '')}",
                    'target_hint': act.get('target_label', ''),
                    'task_type': act.get('action_type', 'click'),
                    'is_completed': False,
                    'error_message': None,
                    '_preplanned_action': act,
                })
            return {
                'page_tasks': tasks,
                'current_task_index': 0,
                'page_audited': False,
                'macro_reasoning': "Account gate: prioritized login/gateway action.",
                'checklist': account_gate_actions,
                'checklist_index': 0,
            }

    # 3. Macro Task Decomposition for Form Filling / Dynamic Questioning
    domain = extract_domain(page.url)
    email = ctx.profile.email if ctx.profile else ""
    portal_password = None
    has_password_field = any(f.get('type') == 'password' or 'password' in f.get('label', '').lower() for f in fields)
    if has_password_field or is_account_gate:
        portal_password, _ = get_or_create_portal_credential(domain=domain, email=email)

    page_errors = state.get('page_errors', []) or []
    recent_actions = state.get('recent_actions', []) or []
    last_error = state.get('last_action_error')

    credentials_block = ""
    if portal_password:
        credentials_block = f"""
ACCOUNT CREDENTIALS FOR THIS DOMAIN ({domain}):
- Email: {email}
- Portal Password: {portal_password}
"""

    prompt = f"""You are the Macro Planning Agent.
Analyze the current page and decompose the required actions into an ordered checklist of concrete PageTasks before this page can advance or submit.

CURRENT URL: {page.url}
ACTIVE MODAL / DIALOG DETECTED: {buttons.get('modal_active', False)}

FORM FIELDS FOUND ON SCREEN:
{json.dumps(fields, indent=2)}

ACTION BUTTONS / CHIPS DETECTED:
{json.dumps(buttons, indent=2)}

VISIBLE FORM VALIDATION ERRORS / FEEDBACK FROM PREVIOUS STEP:
{json.dumps(page_errors, indent=2) if page_errors else "None"}
{f"LAST ACTION ERROR: {last_error}" if last_error else ""}

RECENT EXECUTED ACTIONS IN THIS RUN:
{json.dumps(recent_actions[-5:], indent=2)}

APPLICANT DATA BUNDLE:
{json.dumps(ctx.applicant_bundle, indent=2)}
{credentials_block}
LIGHTWEIGHT QUESTION CATALOG (ID + Question Text):
{json.dumps(ctx.question_catalog, indent=2)}

MACRO DECOMPOSITION RULES:
1. Examine all interactive inputs, dropdowns, checkboxes/radios, file uploads, or chatbot prompts visible on this screen.
2. PRE-FILLED FIELD AUDIT RULE:
   - For every field that has a `current_value` (is_filled is true):
     VERIFY IT AGAINST THE APPLICANT DATA BUNDLE!
     a) If current_value ACCURATELY MATCHES the applicant data (e.g. First name matches profile first name, email matches email, mobile matches phone number, location matches current location):
        -> It is verified. DO NOT generate a task for it.
     b) If current_value IS MISMATCHED, OUTDATED, OR DEFAULTED (e.g. Phone Country Code defaulted to +1 US or wrong country, while applicant is located in India with Indian phone; or incorrect name/email):
        -> You MUST generate a task to CORRECT this field with the applicant's true data!
     c) If a prefilled field contains data that CANNOT be verified against the applicant bundle or question catalog, DO NOT guess:
        -> Generate a task or roadblock to park in PENDING_REVIEW.
3. EMPTY FIELDS & SCREENING QUESTIONS:
   - Generate tasks to fill fields that are empty (is_filled is false or current_value is "").
   - For required checkboxes / consent toggles that are unchecked, generate a 'check' task.
   - For file/resume uploads, generate an 'upload' task if needed.
4. For multi-field forms:
   - DO NOT include the final submit/next button in your task list if there are form fields! Whole-page audit and submit button selection will be performed automatically after all tasks are completed and verified.
5. For dynamic 1-by-1 flows (e.g. chatbot question, single prompt, or modal with 1 question/choice):
   - Generate EXACTLY ONE task for that active question or choice.
6. If VISIBLE FORM VALIDATION ERRORS exist, prioritize tasks to correct the invalid or empty fields.
7. Each task must specify:
   - task_id (1, 2, ...)
   - task_description (e.g. "Fill Email Address", "Retype Password", "Select Country of Residence")
   - target_hint (e.g. "Email Address", "Retype Password", "Country/Region")
   - task_type: "fill" | "select" | "check" | "uncheck" | "upload" | "click"
8. Do NOT hardcode arbitrary CSS selectors here. Only specify the task what and target hint; the micro executor resolves exact locators and values."""

    try:
        plan_output, _ = ctx.client.call_model_structured(prompt, MacroPlanOutput, method=ctx.method)
    except Exception as e:
        logger.error(f"[plan] LLM macro plan call failed: {e}")
        plan_output = MacroPlanOutput(reasoning=f"LLM macro plan error: {e}", tasks=[])

    # ── Auto-track any unmapped optional questions encountered on the page ──
    planned_targets = {t.target_hint.lower() for t in plan_output.tasks}
    for f in fields:
        if not f.get('required') and not f.get('is_filled'):
            label = f.get('label') or f.get('name') or ''
            if label and label.lower() not in planned_targets and len(label) > 2:
                track_encountered_question(
                    profile=ctx.profile,
                    question_text=label,
                    is_optional=True,
                    job_id=state.get('job_id')
                )

    # Loop & Repetition Detection Guards
    page_text = state.get('page_text', '') or ''
    upload_error_detected = bool(re.search(r'upload was unsuccessful|failed to upload|please try again', page_text, re.I))
    upload_attempts = sum(1 for a in recent_actions if a.get('action_type') == 'upload')
    if upload_error_detected and upload_attempts >= 2:
        reason_msg = f"Resume upload repeatedly rejected by portal ('File upload was unsuccessful') after {upload_attempts} attempts."
        logger.warning(f"[plan] Loop detected: {reason_msg} -> Escalating to PENDING_REVIEW")
        run = ApplicationRun.objects.get(id=state['run_id'])
        run.status = 'PENDING_REVIEW'
        run.escalation_reason = reason_msg
        run.save()
        job = Job.objects.get(id=state['job_id'])
        job.status = 'PR'
        job.save()
        return {
            'status': 'PENDING_REVIEW',
            'escalation_reason': reason_msg,
            'page_tasks': [],
            'current_task_index': 0,
            'checklist': [],
            'checklist_index': 0,
        }

    for task in plan_output.tasks:
        repeated_count = sum(
            1 for a in recent_actions[-4:]
            if a.get('action_type') == task.task_type and a.get('target') == task.target_hint
        )
        if repeated_count >= 2:
            reason_msg = f"Task '{task.task_type}' on '{task.target_hint}' repeated {repeated_count} times without advancing page."
            logger.warning(f"[plan] Loop detected: {reason_msg} -> Escalating to PENDING_REVIEW")
            run = ApplicationRun.objects.get(id=state['run_id'])
            run.status = 'PENDING_REVIEW'
            run.escalation_reason = reason_msg
            run.save()
            job = Job.objects.get(id=state['job_id'])
            job.status = 'PR'
            job.save()
            return {
                'status': 'PENDING_REVIEW',
                'escalation_reason': reason_msg,
                'page_tasks': [],
                'current_task_index': 0,
                'checklist': [],
                'checklist_index': 0,
            }

    tasks = [t.dict() for t in plan_output.tasks]
    logger.info(f"[apply run={state['run_id']}] macro plan produced {len(tasks)} tasks: {[t['task_description'] for t in tasks]}")

    return {
        'page_tasks': tasks,
        'current_task_index': 0,
        'page_audited': False,
        'macro_reasoning': plan_output.reasoning,
        'checklist': tasks,
        'checklist_index': 0,
    }


def _plan_gateway_actions(page, state: ApplyState) -> list[dict]:
    """Plan actions for an active listing page — locate and click the apply trigger."""
    buttons = _detect_buttons(page)
    if buttons.get('modal_active'):
        logger.info("[_plan_gateway_actions] Active modal/drawer detected; skipping gateway click.")
        return []

    # Check for Easy Apply on LinkedIn
    easy_apply = page.locator('button.jobs-apply-button')
    if easy_apply.count() > 0 and _is_visible(easy_apply.first):
        return [{
            'target_label': 'Easy Apply Button',
            'action_type': 'click',
            'selector_strategy': 'css',
            'selector_value': 'button.jobs-apply-button',
            'value_source': 'navigation',
        }]

    # General Apply button
    apply_btn = page.get_by_role("button", name=re.compile(r"apply", re.I)).first
    if _is_visible(apply_btn):
        return [{
            'target_label': 'Apply Button',
            'action_type': 'click',
            'selector_strategy': 'role',
            'selector_value': 'button:Apply',
            'value_source': 'navigation',
        }]

    # General Apply link
    apply_link = page.get_by_role("link", name=re.compile(r"apply", re.I)).first
    if _is_visible(apply_link):
        return [{
            'target_label': 'Apply Link',
            'action_type': 'click',
            'selector_strategy': 'role',
            'selector_value': 'link:Apply',
            'value_source': 'navigation',
        }]

    return []


def _is_social_media_link(elem) -> bool:
    """Check if an element is a footer or sidebar social media follow link."""
    try:
        href = (elem.get_attribute('href') or '').lower()
        if any(p in href for p in ('/company/', '/in/', '/school/', 'facebook.com', 'twitter.com', 'x.com', 'youtube.com', 'instagram.com')):
            return True
        # Check if inside a footer or social container
        is_in_social = elem.evaluate(r"""el => {
            return Boolean(el.closest('footer, [class*="footer" i], [id*="footer" i], [class*="social" i]'));
        }""")
        return bool(is_in_social)
    except Exception:
        return False


def _plan_account_gate_actions(page, state: ApplyState) -> list[dict]:
    """
    Plan actions for an account sign-in or gateway:
    1. Check if the page is an active registration/account creation form -> if so, hand off to LLM planner.
    2. PRIORITY 1: Check for explicit SSO (Google / LinkedIn).
    3. PRIORITY 2: If saved credentials exist in PortalCredential, sign in.
    4. PRIORITY 3: If no saved credentials, click 'Create an account' to reach the registration form.
    """
    ctx = get_context()
    domain = extract_domain(page.url)
    email = ctx.profile.email if ctx.profile else ""
    buttons = _detect_buttons(page)
    fields = _extract_form_fields(page)
    page_text = state.get('page_text', '') or ''

    # 0. Check if page is an active registration / account creation form
    # Differentiates sign-in forms from registration forms
    is_registration = (
        any(re.search(r'retype|confirm|choose\s*password|create\s*password', f.get('label', '') + ' ' + f.get('name', ''), re.I) for f in fields)
        or sum(1 for f in fields if f.get('type') == 'password') > 1
        or (any(f.get('type') == 'password' for f in fields) and any(re.search(r'first\s*name|last\s*name', f.get('label', '') + ' ' + f.get('name', ''), re.I) for f in fields))
        or bool(re.search(r'create (an )?account|new user registration|register to apply', page_text[:2000], re.I) and not re.search(r'career opportunities:\s*sign in|sign in to your account', page_text[:1000], re.I))
    )
    if is_registration:
        logger.info("[_plan_account_gate_actions] Active registration/account creation form detected. Handing off to LLM planner.")
        return []

    # 1. PRIORITY 1: Check for SSO (Google / LinkedIn)
    # Strictly require explicit login/apply text and exclude social media follow links
    sso_google = page.locator(
        'button:has-text("Sign in with Google"), button:has-text("Continue with Google"), '
        'button:has-text("Apply with Google"), a:has-text("Sign in with Google"), '
        'a:has-text("Continue with Google"), a:has-text("Apply with Google"), '
        '[data-provider="google"]'
    ).first
    if _is_visible(sso_google) and not _is_social_media_link(sso_google):
        logger.info("[_plan_account_gate_actions] Located Google SSO trigger (Priority 1).")
        return [{
            'target_label': 'Sign in with Google',
            'action_type': 'click',
            'selector_strategy': 'css',
            'selector_value': 'button:has-text("Sign in with Google"), a:has-text("Sign in with Google"), [data-provider="google"]',
            'value_source': 'navigation',
        }]

    sso_linkedin = page.locator(
        'button:has-text("Apply with LinkedIn"), button:has-text("Sign in with LinkedIn"), '
        'button:has-text("Continue with LinkedIn"), a:has-text("Apply with LinkedIn"), '
        'a:has-text("Sign in with LinkedIn"), a:has-text("Continue with LinkedIn")'
    ).first
    if _is_visible(sso_linkedin) and not _is_social_media_link(sso_linkedin):
        logger.info("[_plan_account_gate_actions] Located LinkedIn SSO trigger (Priority 1).")
        return [{
            'target_label': 'Apply with LinkedIn',
            'action_type': 'click',
            'selector_strategy': 'css',
            'selector_value': 'button:has-text("Apply with LinkedIn"), a:has-text("Apply with LinkedIn")',
            'value_source': 'navigation',
        }]

    # 2. PRIORITY 2: Check for existing saved credentials
    saved_password = get_portal_credential(domain=domain, email=email)
    has_password_field = any(f.get('type') == 'password' or 'password' in f.get('label', '').lower() for f in fields)
    has_email_field = any(f.get('type') == 'email' or 'email' in f.get('label', '').lower() for f in fields)

    # Check if a recent sign-in attempt failed
    recent = state.get('recent_actions', [])
    page_errors = state.get('page_errors', []) or []
    has_failed_signin = any(
        a.get('target') in ('Sign In Button', 'Password', 'Email Address') and (not a.get('success') or page_errors)
        for a in recent[-3:]
    ) or any(re.search(r'incorrect|invalid|not recognize|no account|credentials', e, re.I) for e in page_errors)

    if saved_password and has_email_field and has_password_field and not has_failed_signin:
        logger.info(f"[_plan_account_gate_actions] Using saved PortalCredential for domain '{domain}'.")
        return [
            {
                'target_label': 'Email Address',
                'action_type': 'fill',
                'selector_strategy': 'css',
                'selector_value': 'input[type="email"], input[name*="email" i], input[id*="email" i]',
                'value_source': 'profile_attr',
                'profile_attr': 'email',
            },
            {
                'target_label': 'Password',
                'action_type': 'fill',
                'selector_strategy': 'css',
                'selector_value': 'input[type="password"]',
                'value_source': 'constant',
                'constant_value': saved_password,
            },
            {
                'target_label': 'Sign In Button',
                'action_type': 'click',
                'selector_strategy': 'css',
                'selector_value': 'button:has-text("Sign In"), input[type="submit"][value*="Sign In" i], button[type="submit"], input[type="submit"]',
                'value_source': 'navigation',
            }
        ]

    # 3. PRIORITY 3: If no saved credentials (or login failed), click "Create an account"
    create_acct = page.locator('a:has-text("Create an account"), button:has-text("Create an account"), a:has-text("Register"), a:has-text("Create Account")').first
    if _is_visible(create_acct):
        logger.info("[_plan_account_gate_actions] Clicking 'Create an account' link.")
        return [{
            'target_label': 'Create an account',
            'action_type': 'click',
            'selector_strategy': 'css',
            'selector_value': 'a:has-text("Create an account"), button:has-text("Create an account"), a:has-text("Register"), a:has-text("Create Account")',
            'value_source': 'navigation',
        }]

    # Fall back to LLM planning for any other cases
    return []


# ═══════════════════════════════════════════════════════════════════
# Micro-Task Execution, Whole-Page Audit, and Button Scrutiny
# ═══════════════════════════════════════════════════════════════════

def _call_micro_llm(page, current_task: dict, state: ApplyState, ctx) -> MicroActionOutput:
    """
    Call reasoning LLM to resolve the exact Playwright locator and value for a single micro task.
    Directly resolves value from applicant data bundle, domain credentials, or question catalog.
    """
    fields = _extract_form_fields(page)
    domain = extract_domain(page.url)
    email = ctx.profile.email if ctx.profile else ""
    portal_password = get_portal_credential(domain=domain, email=email)
    page_errors = state.get('page_errors', []) or []

    credentials_block = ""
    if portal_password:
        credentials_block = f"""
ACCOUNT CREDENTIALS FOR THIS DOMAIN ({domain}):
- Email: {email}
- Portal Password: {portal_password}
"""

    prompt = f"""You are the Micro-Task Executor.
Your task is to execute EXACTLY ONE specific action on the current page:
TASK: {current_task.get('task_description')}
TARGET HINT: {current_task.get('target_hint')}
EXPECTED ACTION TYPE: {current_task.get('task_type')}

CURRENT URL: {page.url}

FORM FIELDS ON SCREEN:
{json.dumps(fields, indent=2)}

VISIBLE FORM VALIDATION ERRORS:
{json.dumps(page_errors, indent=2) if page_errors else "None"}

APPLICANT DATA BUNDLE:
{json.dumps(ctx.applicant_bundle, indent=2)}
{credentials_block}
LIGHTWEIGHT QUESTION CATALOG:
{json.dumps(ctx.question_catalog, indent=2)}

EXECUTION RULES:
1. Identify the exact matching field/element on the page for TARGET HINT: '{current_task.get('target_hint')}'.
2. Provide a robust Playwright selector:
   - Prefer 'id' if ID is stable and present (e.g. strategy='id', selector='firstName')
   - Or 'label' (e.g. strategy='label', selector='First Name')
   - Or 'placeholder' (e.g. strategy='placeholder', selector='Enter your password')
   - Or 'role' (e.g. strategy='role', selector='button:Apply')
   - Or 'css' (e.g. strategy='css', selector='input[name="firstName"]')
   - NEVER use pseudo-selectors like ':has-text()' in CSS.
3. DIRECTLY RESOLVE AND OUTPUT THE EXACT VALUE TO FILL OR SELECT:
   - Output the literal resolved text string deduced directly from APPLICANT DATA BUNDLE, ACCOUNT CREDENTIALS, or Question Catalog.
   - For country and phone country code dropdowns: deduce the country and international dialing code strictly from the applicant's phone number, current_location, and currency in the bundle. Match one of the visible options for that dropdown.
   - CRITICAL GUARDRAIL: If the applicant's true country or dialing code does not match any dropdown option, or if you are uncertain, set missing_required_info = true and DO NOT pick a foreign default (like +1) under any circumstances!
   - For location / city / address fields: use the applicant's location details from the bundle.
   - For dropdown select actions: provide the exact visible option text or value matching one of the options listed in FORM FIELDS ON SCREEN.
   - For file upload actions: set value = 'resume'.
4. REQUIRED/OPTIONAL QUESTION HANDLING:
   - If this field is mandatory/required, and the answer CANNOT be found in the applicant data bundle or question catalog, or if there is any ambiguity:
     set missing_required_info = true, question_text_if_missing = <field prompt>. DO NOT GUESS OR HALLUCINATE UNDER ANY CIRCUMSTANCES.
   - If this is optional and information is missing, set missing_required_info = false, value = None."""

    try:
        output, _ = ctx.client.call_model_structured(prompt, MicroActionOutput, method=ctx.method)
        return output
    except Exception as e:
        logger.error(f"[_call_micro_llm] LLM call failed: {e}")
        target = current_task.get('target_hint', '')
        t_type = current_task.get('task_type', 'fill')
        return MicroActionOutput(
            reasoning=f"Micro LLM error fallback: {e}",
            action_type=t_type,
            selector_strategy="label",
            selector_value=target,
            value=None,
        )


def _audit_whole_page(page, state: ApplyState, ctx) -> tuple[bool, list[dict], str]:
    """
    Perform whole-page pre-submit audit:
    Inspects all required fields and visible validation errors before submitting.
    Returns (audit_passed: bool, corrective_tasks: list[dict], explanation: str).
    """
    fields = _extract_form_fields(page)
    errors = _extract_page_errors(page)

    prompt = f"""You are the Whole-Page Pre-Submit Form Auditor.
Review the completed form on this page before submission or advancing to the next step.

CURRENT URL: {page.url}

CURRENT FORM FIELDS AND VALUES ON PAGE:
{json.dumps(fields, indent=2)}

VISIBLE FORM VALIDATION ERRORS / BANNERS:
{json.dumps(errors, indent=2) if errors else "None"}

APPLICANT DATA BUNDLE:
{json.dumps(ctx.applicant_bundle, indent=2)}

AUDIT RULES:
1. Check every mandatory/required field (required=True or containing '*' in label).
   - Are any mandatory fields blank, unselected, or missing valid values?
   - If ANY mandatory field is empty or missing, audit_passed MUST be false and ready_to_submit MUST be false.
2. CHECK DATA ACCURACY AGAINST APPLICANT BUNDLE:
   - Carefully verify that every filled field's value accurately matches the applicant's profile data!
   - If ANY field contains incorrect, mismatched, or defaulted data (e.g. wrong phone country code, wrong name, wrong email/location), audit_passed MUST be false and ready_to_submit MUST be false.
3. Check VISIBLE FORM VALIDATION ERRORS:
   - If there are red error messages or validation banners, audit_passed MUST be false and ready_to_submit MUST be false.
4. If audit_passed is false:
   - List unfilled_required_fields and visible_error_messages.
   - Generate concrete 'corrective_tasks' (as PageTask items) for each unfilled field or error.
5. If ALL mandatory fields have valid values, match applicant data, and there are NO validation errors:
   - Set audit_passed = true and ready_to_submit = true.
   - corrective_tasks should be empty."""

    try:
        output, _ = ctx.client.call_model_structured(prompt, PageAuditOutput, method=ctx.method)
        passed = bool(output.audit_passed or output.ready_to_submit)
        corrective = [t.dict() for t in output.corrective_tasks]
        return passed, corrective, output.explanation
    except Exception as e:
        logger.error(f"[_audit_whole_page] LLM audit failed: {e}")
        return False, [], f"Audit failed with error: {e}"


def _resolve_and_execute_next_move(page, state: ApplyState, ctx) -> tuple[bool, Optional[str]]:
    """
    Resolve and execute the forward progression action (e.g. Next, Review, Submit)
    after all form fields on the current view have been completed and verified.
    Strictly scopes to the active modal/drawer if present, preventing background clicks.
    """
    data = page.evaluate(r"""() => {
        const dialog = document.querySelector('dialog[open], [role="dialog"], [aria-modal="true"], div[data-test-modal], .jobs-easy-apply-modal, ._chatBotContainer, .modal.show');
        let root = null;
        if (dialog) {
            const rect = dialog.getBoundingClientRect();
            const style = window.getComputedStyle(dialog);
            if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden') {
                root = dialog;
            }
        }
        if (!root) {
            const overlay = document.querySelector('.chatbot_Overlay.show, .modal-backdrop.show, .overlay.show');
            if (overlay) {
                root = document.querySelector('._chatBotContainer') || overlay.parentElement;
            }
        }
        const hasActiveModal = Boolean(root);
        if (!root) root = document;

        const list = [];
        const elements = root.querySelectorAll(
            'button, input[type="submit"], input[type="button"], a[role="button"], [role="button"]'
        );
        for (const el of elements) {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
                const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
                const tag = el.tagName.toLowerCase();
                const type = el.getAttribute('type') || '';
                const id = el.id || '';
                const ariaLabel = (el.getAttribute('aria-label') || '').trim();
                const inFooter = Boolean(el.closest('footer, [class*="footer" i], [id*="footer" i]'));

                if (text || ariaLabel || type === 'submit') {
                    list.push({
                        tag,
                        type,
                        text: text.substring(0, 100),
                        aria_label: ariaLabel.substring(0, 100),
                        id,
                        in_modal: hasActiveModal,
                        in_footer: inFooter
                    });
                }
            }
        }
        return { hasActiveModal, candidates: list.slice(0, 30) };
    }""")

    has_active_modal = data.get('hasActiveModal', False)
    candidates = data.get('candidates', [])

    prompt = f"""You are the Next-Move Progression Agent.
All required form fields on the current step have been filled and verified.
Your objective is to identify the single correct button to advance to the next step or submit the application.

CURRENT URL: {page.url}
ACTIVE MODAL / DIALOG IN PROGRESS: {has_active_modal}

CANDIDATE BUTTONS ON ACTIVE CONTAINER:
{json.dumps(candidates, indent=2)}

PROGRESSION RULES:
1. SELECT THE FORWARD PROGRESSION BUTTON:
   - For multi-step forms (e.g. Step 1 of 6): Look for buttons like 'Next', 'Continue', 'Proceed', or 'Review'.
   - For the final step of a form: Look for 'Submit application', 'Submit', 'Finish', or 'Create Account'.
2. NEVER SELECT EXIT, DISMISS, OR REVERSE BUTTONS:
   - NEVER select 'Cancel', 'Close', 'Dismiss', 'Back', 'Save and exit', or 'X'.
3. NEVER SELECT BACKGROUND OR LISTING TRIGGERS:
   - When a form modal is open, NEVER click background listing buttons like 'Easy Apply'.
4. Provide selector_strategy and selector_value:
   - Prefer 'role' (e.g. strategy='role', selector='button:Next' or 'button:Continue') or 'text' (e.g. strategy='text', selector='Next')
   - Or 'id' if stable and unique
   - Or 'css' (e.g. strategy='css', selector='button[aria-label="Continue to next step"]')
   - NEVER use pseudo-selectors like ':has-text()' in CSS.
5. Set is_terminal_submission = true ONLY if this action actually submits the entire application (e.g. 'Submit application'). If it is an intermediate step like 'Next' or 'Review', set is_terminal_submission = false."""

    strategy = None
    selector = None
    target_name = None
    next_move = None
    try:
        next_move, _ = ctx.client.call_model_structured(prompt, NextMoveOutput, method=ctx.method)
        strategy = next_move.selector_strategy
        selector = next_move.selector_value
        target_name = next_move.button_text or next_move.action_label
        logger.info(f"[_resolve_and_execute_next_move] Resolved next move: '{next_move.action_label}' via {strategy}='{selector}' (terminal={next_move.is_terminal_submission}) — {next_move.explanation}")
    except Exception as e:
        logger.error(f"[_resolve_and_execute_next_move] Next move LLM call failed: {e}")

    # Fallback to safe forward progression selector if LLM couldn't provide one
    if not selector:
        if has_active_modal:
            modal = page.locator('dialog[open], [role="dialog"], [aria-modal="true"], div[data-test-modal], .jobs-easy-apply-modal, ._chatBotContainer').first
            for name_pat in (r"^Next$", r"^Review$", r"^Continue$", r"^Submit"):
                cand = modal.get_by_role("button", name=re.compile(name_pat, re.I)).first
                if _is_visible(cand):
                    strategy = 'role'
                    selector = f"button:{cand.inner_text().strip()}"
                    target_name = cand.inner_text().strip()
                    logger.info(f"[_resolve_and_execute_next_move] Fallback matched modal button: {selector}")
                    break
        if not selector:
            safe_submit = page.locator('button[type="submit"]:not(footer *), input[type="submit"]:not(footer *)').first
            if _is_visible(safe_submit) and not _is_social_media_link(safe_submit):
                strategy = 'css'
                selector = 'button[type="submit"]:not(footer *), input[type="submit"]:not(footer *)'
                target_name = 'Submit'
                logger.info("[_resolve_and_execute_next_move] Fallback matched page submit button.")

    if not selector:
        return False, "Could not resolve a legitimate forward progression button on this view."

    success, err = execute_browser_action(
        page, strategy, selector, 'click', None, target_name or 'Forward Progression Button',
        platform=extract_url_platform(page.url)
    )

    try:
        StepRecord.objects.create(
            run_id=state['run_id'],
            step_number=state.get('total_actions_executed', 0) + 1,
            page_url=page.url,
            dom_signature=state.get('dom_signature', ''),
            checklist_item={
                'task_description': next_move.action_label if next_move else f'Click {target_name}',
                'target_hint': target_name or 'Next',
                'task_type': 'click'
            },
            source='llm_decision',
            action_type='click',
            action_detail={
                'target': target_name,
                'strategy': strategy,
                'selector': selector,
                'is_terminal': next_move.is_terminal_submission if next_move else False,
            },
            success=success,
            post_action_url=page.url,
            error_message=err,
        )
    except Exception as e:
        logger.warning(f"Failed to record StepRecord for next move: {e}")

    return success, err


# Alias for backward compatibility
_scrutinize_and_find_submit_button = _resolve_and_execute_next_move


# ═══════════════════════════════════════════════════════════════════
# NODE: execute
# ═══════════════════════════════════════════════════════════════════

def execute(state: ApplyState) -> dict:
    """
    Micro-Task Executor & Verification Loop (Hierarchical v2).
    Executes tasks one by one with immediate closed-loop verification,
    followed by Whole-Page Pre-Submit Audit and Submit Button Scrutiny.
    """
    logger.info(f"[apply run={state['run_id']}] entering execute "
                f"(current_task_index={state.get('current_task_index', 0)})")

    ctx = get_context()
    page = ctx.page
    page_tasks = list(state.get('page_tasks', []))
    task_idx = state.get('current_task_index', 0)
    page_audited = state.get('page_audited', False)
    platform = extract_url_platform(state.get('current_url', ''))

    # Fallback if page_tasks is empty but legacy checklist is present
    if not page_tasks and state.get('checklist'):
        checklist = state.get('checklist', [])
        idx = state.get('checklist_index', 0)
        for i, item in enumerate(checklist):
            page_tasks.append({
                'task_id': i + 1,
                'task_description': f"{item.get('action_type', 'interact')} {item.get('target_label', '')}",
                'target_hint': item.get('target_label', ''),
                'task_type': item.get('action_type', 'fill'),
                'is_completed': False,
                'error_message': None,
                '_preplanned_action': item,
            })
        task_idx = idx

    # ── CASE B: ALL TASKS COMPLETED FOR THIS PAGE ──
    if task_idx >= len(page_tasks):
        # 1. Single-task pages (gateway click, dynamic chatbot question) do not need whole-page audit
        if len(page_tasks) <= 1 or page_audited:
            logger.info(f"[apply run={state['run_id']}] All page tasks completed — re-observing view.")
            return {
                'page_tasks': page_tasks,
                'current_task_index': task_idx,
                'page_audited': True,
                'last_action_success': True,
                'page_changed_after_action': True,
                'total_actions_executed': state.get('total_actions_executed', 0),
            }

        # 2. Multi-field static forms: Whole-Page Pre-Submit Audit
        logger.info(f"[apply run={state['run_id']}] All {len(page_tasks)} tasks completed. Running Whole-Page Pre-Submit Audit...")
        audit_passed, corrective_tasks, audit_reason = _audit_whole_page(page, state, ctx)

        if not audit_passed and corrective_tasks:
            logger.warning(f"[apply run={state['run_id']}] Audit failed ({audit_reason}). Adding {len(corrective_tasks)} corrective tasks.")
            start_id = len(page_tasks) + 1
            for i, ct in enumerate(corrective_tasks):
                ct['task_id'] = start_id + i
                ct['is_completed'] = False
                page_tasks.append(ct)
            return {
                'page_tasks': page_tasks,
                'current_task_index': task_idx,
                'page_audited': False,
                'page_changed_after_action': False,  # stay in execute loop to run corrective tasks
                'last_action_success': True,
            }

        # 3. Whole-Page Audit passed -> Next-Move Resolution (Next / Review / Submit)
        logger.info(f"[apply run={state['run_id']}] Whole-page audit passed! Resolving next progression move...")
        move_success, move_err = _resolve_and_execute_next_move(page, state, ctx)

        # Wait for navigation or post-action DOM update
        page.wait_for_timeout(3000)

        return {
            'page_tasks': page_tasks,
            'current_task_index': task_idx,
            'page_audited': True,
            'last_action_success': move_success,
            'last_action_error': move_err,
            'page_changed_after_action': True,  # route back to observe to check confirmation or next page
            'total_actions_executed': state.get('total_actions_executed', 0) + 1,
        }

    # ── CASE A: EXECUTE CURRENT MICRO TASK ──
    current_task = page_tasks[task_idx]
    logger.info(f"[apply run={state['run_id']}] execute task [{task_idx + 1}/{len(page_tasks)}]: "
                f"{current_task.get('task_description')} (target: {current_task.get('target_hint')})")

    preplanned = current_task.get('_preplanned_action')
    action_type = current_task.get('task_type', 'fill')
    target = current_task.get('target_hint', '')
    strategy = None
    selector = None
    resolved_value = None

    if preplanned:
        action_type = preplanned.get('action_type', action_type)
        strategy = preplanned.get('selector_strategy', 'css')
        selector = preplanned.get('selector_value', '')
        val_source = preplanned.get('value_source')
        if val_source == 'profile_attr':
            resolved_value = resolve_profile_value(ctx.profile, preplanned.get('profile_attr') or target)
        elif val_source == 'constant':
            resolved_value = preplanned.get('constant_value')
        else:
            resolved_value = preplanned.get('value')
    else:
        micro_output = _call_micro_llm(page, current_task, state, ctx)

        if micro_output.missing_required_info:
            question_text = micro_output.question_text_if_missing or target
            reason_msg = f"Missing answer for mandatory question/field: '{question_text}'"
            logger.info(f"[execute] Roadblock: {reason_msg} -> Parking in PENDING_REVIEW")
            run = ApplicationRun.objects.get(id=state['run_id'])
            run.status = 'PENDING_REVIEW'
            run.escalation_reason = reason_msg
            run.save()
            job = Job.objects.get(id=state['job_id'])
            job.status = 'PR'
            job.save()
            return {
                'status': 'PENDING_REVIEW',
                'escalation_reason': reason_msg,
                'page_tasks': page_tasks,
                'current_task_index': task_idx,
            }

        action_type = micro_output.action_type
        strategy = micro_output.selector_strategy
        selector = micro_output.selector_value
        resolved_value = micro_output.value

    # Fallback for upload: if resolving failed, default to master_resume
    if action_type == 'upload' and not resolved_value:
        if ctx.profile and ctx.profile.master_resume:
            resolved_value = ctx.profile.master_resume.path

    # Fallback for password: if field is a password field and value wasn't resolved, fetch domain credential
    if not resolved_value and any(w in (target or '').lower() for w in ('password', 'pwd')):
        domain = extract_domain(page.url)
        email = ctx.profile.email if ctx.profile else ""
        resolved_value = get_portal_credential(domain=domain, email=email)

    # ── Closed-loop Pre-Check: Skip overwriting if already populated with expected value ──
    already_satisfied = False
    try:
        cand_loc = _resolve_locator(page, strategy, selector) if (strategy and selector) else None
        if not cand_loc or not _is_visible(cand_loc):
            clean_t = re.sub(r'[:*]', '', target).strip()
            cand_loc = page.get_by_label(clean_t).first

        if cand_loc and _is_visible(cand_loc):
            if action_type == 'fill':
                current_dom_val = (cand_loc.input_value(timeout=1000) or '').strip()
                target_str = str(resolved_value or '').strip()
                if current_dom_val and target_str:
                    if target_str.lower() in current_dom_val.lower() or current_dom_val.lower() in target_str.lower():
                        logger.info(f"[execute] Field '{target}' is already populated with valid value '{current_dom_val}'. Skipping overwrite.")
                        already_satisfied = True
            elif action_type == 'select':
                selected_text = cand_loc.evaluate(r"""el => {
                    if (el.tagName.toLowerCase() !== 'select') return '';
                    return el.selectedIndex >= 0 && el.options[el.selectedIndex] ? (el.options[el.selectedIndex].textContent || el.options[el.selectedIndex].value) : '';
                }""") or ''
                selected_text = selected_text.strip()
                target_str = str(resolved_value or '').strip()
                if selected_text and target_str:
                    if target_str.lower() in selected_text.lower() or selected_text.lower() in target_str.lower():
                        logger.info(f"[execute] Dropdown '{target}' already has matching selection '{selected_text}'. Skipping overwrite.")
                        already_satisfied = True
    except Exception as e:
        logger.debug(f"[execute] Pre-check error: {e}")

    if already_satisfied:
        success = True
        error_msg = None
    else:
        recent_actions = list(state.get('recent_actions', []))
        attempt_count = sum(
            1 for a in recent_actions
            if a.get('action_type') == action_type and a.get('target') == target
        )

        logger.info(f"[execute] Action: {action_type} → {target} via {strategy}='{selector}' (val={str(resolved_value)[:30]})")

        success, error_msg = execute_browser_action(
            page, strategy, selector, action_type, resolved_value, target,
            platform=platform, attempt_count=attempt_count
        )

    # Immediate closed-loop verification
    if success and not already_satisfied and action_type == 'fill':
        try:
            cand_loc = _resolve_locator(page, strategy, selector)
            if not cand_loc or not _is_visible(cand_loc):
                clean_t = re.sub(r'[:*]', '', target).strip()
                cand_loc = page.get_by_label(clean_t).first
            if cand_loc and _is_visible(cand_loc):
                current_val = cand_loc.input_value(timeout=1000)
                if not current_val and resolved_value:
                    logger.warning(f"[execute] Closed-loop verification: field '{target}' is still empty. Retrying fill...")
                    cand_loc.fill(str(resolved_value))
                    current_val = cand_loc.input_value(timeout=1000)
                    if not current_val:
                        logger.warning(f"[execute] Verification: field '{target}' remained empty after fill retry.")
        except Exception as e:
            logger.debug(f"[execute] Verification check skipped/error: {e}")

    current_task['is_completed'] = success
    current_task['error_message'] = error_msg if not success else None
    page_tasks[task_idx] = current_task

    # Record StepRecord
    try:
        StepRecord.objects.create(
            run_id=state['run_id'],
            step_number=state.get('total_actions_executed', 0) + 1,
            page_url=page.url,
            dom_signature=state.get('dom_signature', ''),
            checklist_item=current_task,
            source='llm_decision',
            action_type=action_type,
            action_detail={
                'target': target,
                'resolved_value': str(resolved_value)[:200] if resolved_value else None,
                'strategy': strategy,
                'selector': selector,
            },
            success=success,
            post_action_url=page.url,
            error_message=error_msg,
        )
    except Exception as e:
        logger.warning(f"Failed to record StepRecord: {e}")

    page.wait_for_timeout(600)
    if ctx.page != page:
        page = ctx.page
        url_changed = True
    else:
        url_changed = (page.url != state.get('current_url', ''))

    if len(page_tasks) == 1:
        page_changed = True
    else:
        page_changed = url_changed

    recent_actions.append({
        'action_type': action_type,
        'target': target,
        'selector_strategy': strategy,
        'selector_value': selector,
        'success': success,
        'error': error_msg
    })
    recent_actions = recent_actions[-10:]

    return {
        'page_tasks': page_tasks,
        'current_task_index': task_idx + 1,
        'checklist_index': task_idx + 1,
        'last_action_success': success,
        'last_action_error': error_msg if not success else None,
        'page_changed_after_action': page_changed,
        'consecutive_action_failures': 0 if success else state.get('consecutive_action_failures', 0) + 1,
        'total_actions_executed': state.get('total_actions_executed', 0) + 1,
        'had_fresh_decisions': True,
        'recent_actions': recent_actions,
    }


def execute_browser_action(
    page, strategy: str, selector: str, action_type: str, value: Any, target: str,
    platform: str = "default", attempt_count: int = 0
) -> tuple[bool, Optional[str]]:
    """Execute action directly using native Playwright Locator and Gesture Kinematics."""
    try:
        # 1. SPECIAL CASE: File Upload (Manual Native File Picker Approach)
        is_upload = (action_type == 'upload') or (
            action_type == 'click' and bool(re.search(r'upload\s*resume|upload\s*cv|upload\s*file', target, re.I))
        )
        if is_upload:
            file_path = str(value or '')
            ctx = get_context()
            if not file_path or not os.path.exists(file_path):
                if ctx.profile and ctx.profile.master_resume and os.path.exists(ctx.profile.master_resume.path):
                    file_path = ctx.profile.master_resume.path
                else:
                    return False, f"Upload failed: File path does not exist on disk: '{file_path}'"

            # Wait for any typing animation/loading indicator in chatbot drawer to complete
            try:
                page.wait_for_selector(
                    '._chatBotContainer [class*="typing"], ._chatBotContainer [class*="dot"], ._chatBotContainer [class*="loader"]',
                    state='detached',
                    timeout=5000
                )
            except Exception:
                pass
            page.wait_for_timeout(1000)

            # Build clean, professional filename on disk (e.g. "Janak_Vashisth_Resume.pdf")
            # Strips double extensions (.docx.pdf) and random hashes that portals reject
            clean_filename = "Resume.pdf"
            if ctx.profile and ctx.profile.first_name:
                first = re.sub(r'[^a-zA-Z0-9]', '', ctx.profile.first_name)
                last = re.sub(r'[^a-zA-Z0-9]', '', ctx.profile.last_name or '')
                clean_filename = f"{first}_{last}_Resume.pdf" if last else f"{first}_Resume.pdf"

            clean_dir = os.path.join(tempfile.gettempdir(), 'jobtracker_resumes')
            os.makedirs(clean_dir, exist_ok=True)
            clean_file_path = os.path.join(clean_dir, clean_filename)
            shutil.copyfile(file_path, clean_file_path)

            logger.info(f"[execute_browser_action] Manual Approach: Clicking upload button and filling file picker with '{clean_filename}'")

            # 1. Locate the visible clickable upload trigger (button, chip, or label)
            trigger = None
            candidate_selectors = [
                '._chatBotContainer button:has-text("Upload Resume")',
                '._chatBotContainer .chip:has-text("Upload Resume")',
                '._chatBotContainer [role="button"]:has-text("Upload Resume")',
                '._chatBotContainer label:has-text("Upload Resume")',
                '._chatBotContainer [class*="chip"]:has-text("Upload Resume")',
                'button:has-text("Upload Resume")',
                '.chip:has-text("Upload Resume")',
                '[role="button"]:has-text("Upload Resume")',
                'label:has-text("Upload Resume")',
                'button.upload-btn',
            ]
            for sel in candidate_selectors:
                cand = page.locator(sel)
                if cand.count() > 0 and _is_visible(cand.first):
                    trigger = cand.first
                    logger.info(f"[execute_browser_action] Located exact upload trigger via selector: '{sel}'")
                    break

            if not trigger:
                cand = page.locator('._chatBotContainer').get_by_text(re.compile(r"^upload\s*resume$", re.I)).first
                if _is_visible(cand):
                    trigger = cand
                    logger.info("[execute_browser_action] Located upload trigger via get_by_text('Upload Resume')")

            if not trigger:
                cand = page.get_by_text(re.compile(r"^upload\s*resume$", re.I)).first
                if _is_visible(cand):
                    trigger = cand
                    logger.info("[execute_browser_action] Located page-wide upload trigger via get_by_text('Upload Resume')")

            if not trigger and strategy and selector:
                cand = _resolve_locator(page, strategy, selector)
                if cand and _is_visible(cand):
                    trigger = cand

            if not trigger:
                cand = page.get_by_role("button", name=re.compile(r"upload", re.I)).first
                if _is_visible(cand):
                    trigger = cand

            # 2. PRIMARY: Click trigger and set file via file chooser event
            if trigger:
                try:
                    logger.info("[execute_browser_action] Triggering file picker via click...")
                    with page.expect_file_chooser(timeout=7000) as fc_info:
                        try:
                            trigger.click(timeout=3000)
                        except Exception:
                            human_click(page, trigger, platform=platform)
                    file_chooser = fc_info.value
                    file_chooser.set_files(clean_file_path)
                    logger.info(f"[execute_browser_action] File picker uploaded '{clean_filename}'. Waiting for upload to settle...")
                    page.wait_for_timeout(4000)
                    return True, None
                except Exception as e:
                    logger.warning(f"[execute_browser_action] File picker listener timed out / failed: {e}")

            # 3. FALLBACK: If trigger wasn't clickable or expect_file_chooser timed out, set input tag directly
            logger.info("[execute_browser_action] Fallback: Direct input tag set with clean file path")
            file_input = page.locator('._chatBotContainer input[type="file"], [role="dialog"] input[type="file"], input[type="file"]').first
            if file_input and file_input.count() > 0:
                file_input.set_input_files(clean_file_path)
                try:
                    file_input.dispatch_event('change')
                    file_input.dispatch_event('input')
                except Exception:
                    pass
                page.wait_for_timeout(3000)
                return True, None

            return False, f"Could not find upload button or file input to upload '{target}'"

        # 2. LOCATE ELEMENT FOR NON-UPLOAD ACTIONS
        locator = None
        modal = page.locator('dialog[open], [role="dialog"], [aria-modal="true"], div[data-test-modal], .jobs-easy-apply-modal, ._chatBotContainer').first
        has_modal = _is_visible(modal)
        if strategy and selector:
            if has_modal:
                locator = _resolve_locator(modal, strategy, selector)
            if not locator or not _is_visible(locator):
                locator = _resolve_locator(page, strategy, selector)

        if not locator or not _is_visible(locator):
            clean_target = re.sub(r'[:*]', '', target).strip()
            scope = modal if has_modal else page
            # Smart fallback attempts
            if action_type == 'click':
                locator = scope.get_by_role("button", name=re.compile(re.escape(target), re.I)).first
                if not _is_visible(locator):
                    locator = scope.get_by_text(target).first
                if not _is_visible(locator) and clean_target != target:
                    locator = scope.get_by_role("button", name=re.compile(re.escape(clean_target), re.I)).first
            elif action_type == 'fill':
                locator = scope.get_by_label(target).first
                if not _is_visible(locator) and clean_target != target:
                    locator = scope.get_by_label(re.compile(re.escape(clean_target), re.I)).first
                if not _is_visible(locator):
                    locator = scope.get_by_placeholder(target).first
            elif action_type == 'select':
                locator = scope.get_by_label(target).first
                if not _is_visible(locator) and clean_target != target:
                    locator = scope.get_by_label(re.compile(re.escape(clean_target), re.I)).first
                if not _is_visible(locator):
                    locator = scope.locator(f'select[name*="{clean_target.lower()}" i], select[id*="{clean_target.lower()}" i]').first

        if not locator or not _is_visible(locator):
            return False, f"Could not find visible element for: '{target}' ({strategy}={selector})"

        # 3. PERFORM ACTION USING GESTURE KINEMATICS
        if action_type == 'fill':
            val_str = str(value or '')

            # 1. Snapshot visible DOM elements before typing (to detect newly appeared popups/suggestions)
            try:
                page.evaluate(r"""() => {
                    window.__pre_type_elements = new Set();
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        if (el.children.length <= 2) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                window.__pre_type_elements.add(el);
                            }
                        }
                    }
                }""")
            except Exception:
                pass

            # 2. Type value using humanized gesture kinematics
            success = human_type(page, locator, val_str, platform=platform)
            if not success:
                return False, f"Failed to type into {target}"

            # 3. Dynamic Post-Type DOM Check: detect any newly appeared elements without hardcoding classes
            page.wait_for_timeout(500)
            try:
                result = page.evaluate(r"""(valToMatch) => {
                    if (!window.__pre_type_elements) return { appeared: false };

                    const newlyAppeared = [];
                    let bestMatchEl = null;
                    const all = document.querySelectorAll('*');

                    for (const el of all) {
                        if (el.children.length <= 3) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) {
                                if (!window.__pre_type_elements.has(el)) {
                                    const txt = (el.innerText || el.textContent || '').trim();
                                    if (txt && txt.length < 150) {
                                        newlyAppeared.push(txt);
                                        if (valToMatch && (txt.toLowerCase().includes(valToMatch.toLowerCase()) || valToMatch.toLowerCase().includes(txt.toLowerCase()))) {
                                            if (!bestMatchEl || txt.length < (bestMatchEl.innerText || '').length) {
                                                bestMatchEl = el;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    delete window.__pre_type_elements;

                    if (bestMatchEl) {
                        bestMatchEl.scrollIntoView({ block: 'nearest' });
                        bestMatchEl.click();
                        return { appeared: true, clicked: true, text: (bestMatchEl.innerText || '').trim() };
                    }
                    return { appeared: newlyAppeared.length > 0, clicked: false, sample: newlyAppeared.slice(0, 5) };
                }""", val_str)

                if result and result.get('clicked'):
                    logger.info(f"[execute_browser_action] Newly appeared suggestion detected and selected: '{result.get('text')}'")
                    page.wait_for_timeout(300)
                elif result and result.get('appeared'):
                    logger.info(f"[execute_browser_action] New DOM elements appeared after typing: {result.get('sample')}. Pressing Enter fallback.")
                    page.keyboard.press("ArrowDown")
                    page.wait_for_timeout(150)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(300)
            except Exception as e:
                logger.debug(f"[execute_browser_action] Post-type DOM check error: {e}")

            return True, None
        elif action_type == 'click':
            # Check if this click triggers an SSO OAuth popup (Google / LinkedIn)
            is_sso = bool(re.search(r'google|linkedin|sso|oauth', target, re.I))
            if is_sso:
                logger.info(f"[execute_browser_action] Triggering SSO trigger '{target}' and watching for popup...")
                try:
                    with page.expect_popup(timeout=8000) as popup_info:
                        try:
                            locator.click(timeout=3000)
                        except Exception:
                            human_click(page, locator, platform=platform)
                    popup = popup_info.value
                    popup.wait_for_load_state("domcontentloaded", timeout=10000)
                    logger.info(f"[execute_browser_action] Intercepted SSO popup: {popup.url}")

                    # 1. Google account chooser handling
                    ctx = get_context()
                    user_email = ctx.profile.email if ctx.profile else ""
                    if "accounts.google.com" in popup.url and user_email:
                        account_item = popup.locator(f'div[data-identifier*="{user_email}"], div:has-text("{user_email}")').first
                        if _is_visible(account_item):
                            human_click(popup, account_item)
                            logger.info(f"[execute_browser_action] Selected Google account '{user_email}' in popup.")
                            popup.wait_for_timeout(2000)

                    # 2. OAuth permissions / consent handling
                    consent_btn = popup.locator('button:has-text("Continue"), button:has-text("Allow"), input[value="Allow"], #submit_approve_access').first
                    if _is_visible(consent_btn):
                        human_click(popup, consent_btn)
                        logger.info("[execute_browser_action] Accepted OAuth consent in popup.")
                        popup.wait_for_timeout(3000)

                    page.wait_for_timeout(3000)
                    return True, None
                except Exception as e:
                    logger.warning(f"[execute_browser_action] SSO popup listener timed out or completed inline: {e}")
                    return True, None

            success = human_click(page, locator, platform=platform)
            return success, None if success else f"Failed to click {target}"
        elif action_type == 'select':
            val_str = str(value or '').strip()
            # 1. Exact label match
            try:
                locator.select_option(label=val_str, timeout=2000)
                return True, None
            except Exception:
                pass
            # 2. Exact value match
            try:
                locator.select_option(value=val_str, timeout=2000)
                return True, None
            except Exception:
                pass
            # 3. Fuzzy option text match (case-insensitive)
            try:
                opts = locator.locator('option')
                for i in range(opts.count()):
                    o = opts.nth(i)
                    txt = (o.inner_text() or '').strip()
                    val = o.get_attribute('value') or ''
                    if val_str.lower() == txt.lower() or val_str.lower() == val.lower():
                        locator.select_option(value=val, timeout=2000)
                        return True, None
                    if val_str.lower() in txt.lower():
                        locator.select_option(value=val, timeout=2000)
                        return True, None
            except Exception:
                pass
            # 4. Substring match without parentheses e.g. "India (+91)" -> "India"
            sub_val = re.sub(r'\(.*?\)', '', val_str).strip()
            if sub_val and sub_val != val_str:
                try:
                    opts = locator.locator('option')
                    for i in range(opts.count()):
                        o = opts.nth(i)
                        txt = (o.inner_text() or '').strip()
                        val = o.get_attribute('value') or ''
                        if sub_val.lower() in txt.lower():
                            locator.select_option(value=val, timeout=2000)
                            return True, None
                except Exception:
                    pass
            # 5. Dial code match: if val_str contains "+91"
            dial_match = re.search(r'\+\d+', val_str)
            if dial_match:
                dial_code = dial_match.group(0)
                try:
                    opts = locator.locator('option')
                    for i in range(opts.count()):
                        o = opts.nth(i)
                        txt = (o.inner_text() or '').strip()
                        val = o.get_attribute('value') or ''
                        if dial_code in txt or dial_code in val:
                            locator.select_option(value=val, timeout=2000)
                            return True, None
                except Exception:
                    pass
            return False, f"Could not select '{val_str}' in dropdown {target}"
        elif action_type == 'check':
            locator.check()
            return True, None
        elif action_type == 'uncheck':
            locator.uncheck()
            return True, None
        else:
            return False, f"Unknown action type: {action_type}"

    except Exception as e:
        logger.warning(f"Browser action error ({action_type} on {target}): {e}")
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════
# NODE: trust_check
# ═══════════════════════════════════════════════════════════════════

def trust_check(state: ApplyState) -> dict:
    """Park the application run for human review in PENDING_REVIEW (PR)."""
    logger.info(f"[apply run={state['run_id']}] entering trust_check -> PENDING_REVIEW")

    run = ApplicationRun.objects.get(id=state['run_id'])
    run.status = 'PENDING_REVIEW'
    run.had_fresh_decisions = state.get('had_fresh_decisions', False)
    if state.get('escalation_reason'):
        run.escalation_reason = state['escalation_reason']
    run.save()

    job = Job.objects.get(id=state['job_id'])
    job.status = 'PR'
    job.save()

    return {'status': 'PENDING_REVIEW'}


# ═══════════════════════════════════════════════════════════════════
# NODE: submit
# ═══════════════════════════════════════════════════════════════════

def submit(state: ApplyState) -> dict:
    """Record verified application submission (AF)."""
    logger.info(f"[apply run={state['run_id']}] entering submit -> SUBMITTED (AF)")

    run = ApplicationRun.objects.get(id=state['run_id'])
    job = Job.objects.get(id=state['job_id'])

    run.status = 'SUBMITTED'
    run.completed_at = timezone.now()
    run.save()

    job.status = 'AF'
    job.applied_on = timezone.now().date()
    job.save()

    logger.info(f"[apply run={state['run_id']}] SUBMITTED — {job.company} {job.position}")
    return {'status': 'SUBMITTED'}
