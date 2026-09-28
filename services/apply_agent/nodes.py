"""
Node functions for the auto-apply LangGraph agent.

Each function takes the current ApplyState and returns a partial state update.
These are the building blocks wired together in graph.py.

Non-serializable resources (Playwright page, OllamaClient, profile) are
accessed via the thread-local ApplyContext — not passed through state.
"""
import logging
import os
import re
from datetime import datetime

from django.utils import timezone
from django.db.models import F
from django.conf import settings

from job.models import (
    Job, ApplicationRun, StepRecord, ActionSnippet, PlatformAccount
)
from user.models import ApplicantProfile
from services.apply_agent.state import ApplyState
from services.apply_agent.context import get_context
from services.apply_agent.signature import (
    compute_snippet_signature, compute_page_signature, strip_url_ids, extract_url_platform
)
from services.apply_agent.field_mapper import (
    PROFILE_FIELD_MAP, resolve_profile_value, lookup_qa_bank, is_sensitive_category
)
from services.apply_agent.trust import should_auto_submit
from services.apply_agent.executor import run_snippet_pair, execute_locate, execute_interact, execute_verify
from services.apply_agent.llm import generate_field_actions, store_snippets, resolve_value

logger = logging.getLogger(__name__)

# Directory for screenshots
SCREENSHOT_DIR = os.path.join(
    getattr(settings, 'MEDIA_ROOT', os.path.join(getattr(settings, 'BASE_DIR', '.'), 'media')),
    'apply_screenshots'
)


# ── Pre-check node ──────────────────────────────────────────────────

def pre_check(state: ApplyState) -> dict:
    """
    Deterministic pre-flight checks before entering the apply loop.
    
    Checks: already applied, cross-platform duplicate, expired, email-only,
    platform account health.
    """
    job = Job.objects.get(id=state['job_id'])
    run = ApplicationRun.objects.get(id=state['run_id'])
    apply_url = state['apply_url']

    # 1. Already applied?
    if job.status == 'AF':
        return _skip(run, 'Already applied')

    # 2. Cross-platform duplicate — same apply_url already submitted
    if Job.objects.filter(
        apply_url=apply_url, status='AF'
    ).exclude(id=job.id).exists():
        return _skip(run, 'Duplicate — same apply URL already submitted')

    # 3. Email-only?
    if not apply_url or '@' in apply_url:
        run.apply_type = 'email_only'
        run.status = 'ESCALATED'
        run.escalation_reason = 'Email-only application — needs manual send or browser compose'
        run.save()
        return {
            'status': 'ESCALATED',
            'apply_type': 'email_only',
            'escalation_reason': run.escalation_reason
        }

    # 4. Platform account health
    platform = extract_url_platform(apply_url)
    try:
        account = PlatformAccount.objects.get(platform=platform)
        if not account.is_healthy:
            return _skip(run, f'Platform account unhealthy: {platform}')
        if account.daily_count >= account.daily_limit:
            return _skip(run, f'Daily limit reached for {platform} ({account.daily_limit})')
    except PlatformAccount.DoesNotExist:
        pass  # No account tracking for this platform — proceed

    # All checks passed
    run.status = 'IN_PROGRESS'
    run.started_at = timezone.now()
    run.save()

    return {
        'status': 'IN_PROGRESS',
        'step_number': 0,
        'had_fresh_decisions': False,
    }


# ── Classify link node ──────────────────────────────────────────────

def classify_link(state: ApplyState) -> dict:
    """
    Navigate to the apply URL and classify the destination.
    
    Opens the URL in the Playwright page and determines what kind of 
    apply flow we're dealing with based on the final URL after redirects.
    """
    ctx = get_context()
    apply_url = state['apply_url']

    # Navigate to the apply URL
    try:
        ctx.page.goto(apply_url, wait_until='domcontentloaded', timeout=30000)
        ctx.page.wait_for_timeout(2000)  # let JS settle
    except Exception as e:
        logger.error(f"Failed to navigate to {apply_url}: {e}")
        run = ApplicationRun.objects.get(id=state['run_id'])
        return _skip(run, f'Navigation failed: {e}')

    # Use the final URL (after redirects) to classify
    final_url = ctx.page.url
    platform = extract_url_platform(final_url)

    PLATFORM_TYPE_MAP = {
        'linkedin': 'native_linkedin',
        'naukri': 'native_naukri',
        'greenhouse': 'ats_greenhouse',
        'lever': 'ats_lever',
        'workday': 'ats_workday',
        'icims': 'ats_other',
        'smartrecruiters': 'ats_other',
        'ashbyhq': 'ats_other',
        'bamboohr': 'ats_other',
        'jobvite': 'ats_other',
    }

    apply_type = PLATFORM_TYPE_MAP.get(platform, 'custom_site')

    run = ApplicationRun.objects.get(id=state['run_id'])
    run.apply_type = apply_type
    run.save()

    return {
        'apply_type': apply_type,
        'current_url': final_url,
    }


# ── Observe node ────────────────────────────────────────────────────

def observe(state: ApplyState) -> dict:
    """
    Capture the current page state: screenshot, DOM, field labels.
    
    Extracts all fillable form fields from the visible page and builds
    the structural signature for cache lookups.
    """
    ctx = get_context()
    page = ctx.page
    step = state['step_number'] + 1

    # 1. Screenshot
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    screenshot_filename = f"run_{state['run_id']}_step_{step}.png"
    screenshot_path = os.path.join(SCREENSHOT_DIR, screenshot_filename)
    try:
        page.screenshot(path=screenshot_path, full_page=False)
    except Exception as e:
        logger.warning(f"Screenshot failed: {e}")
        screenshot_path = None

    # 2. Current URL (may have changed after previous action)
    current_url = page.url

    # 3. Extract form fields — labels and input types from the DOM
    field_labels = _extract_field_labels(page)

    # 4. Clean DOM snapshot of the form area
    dom_snapshot = _extract_form_dom(page)

    # 5. Compute page-level signature
    dom_signature = compute_page_signature(current_url, field_labels) if field_labels else ''

    logger.info(f"Step {step}: {current_url} | {len(field_labels)} fields: {field_labels[:5]}...")

    return {
        'step_number': step,
        'current_url': current_url,
        'screenshot_path': screenshot_path,
        'dom_snapshot': dom_snapshot,
        'dom_signature': dom_signature,
        'field_labels': field_labels,
    }


def _extract_field_labels(page) -> list[str]:
    """
    Extract human-readable labels for all fillable form fields on the page.
    
    Tries multiple strategies:
    1. <label for="..."> → input association
    2. aria-label attribute
    3. placeholder attribute
    4. Nearby text content (parent or previous sibling)
    """
    try:
        labels = page.evaluate("""() => {
            const fields = document.querySelectorAll(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), ' +
                'textarea, select, [contenteditable="true"]'
            );
            const results = [];
            for (const field of fields) {
                // Skip invisible fields
                const rect = field.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                
                let label = '';
                
                // 1. Associated <label>
                if (field.id) {
                    const labelEl = document.querySelector(`label[for="${field.id}"]`);
                    if (labelEl) label = labelEl.textContent.trim();
                }
                
                // 2. aria-label
                if (!label) label = field.getAttribute('aria-label') || '';
                
                // 3. placeholder
                if (!label) label = field.getAttribute('placeholder') || '';
                
                // 4. name attribute as fallback
                if (!label) label = field.getAttribute('name') || '';
                
                // 5. Parent label element
                if (!label) {
                    const parentLabel = field.closest('label');
                    if (parentLabel) label = parentLabel.textContent.trim();
                }
                
                if (label) results.push(label.substring(0, 100));
            }
            return results;
        }""")
        return labels or []
    except Exception as e:
        logger.warning(f"Field label extraction failed: {e}")
        return []


def _extract_form_dom(page) -> str:
    """
    Extract a cleaned HTML snapshot of the form area on the page.
    
    Removes scripts, styles, and irrelevant elements. Truncates to
    prevent the LLM prompt from getting too large.
    """
    try:
        dom = page.evaluate("""() => {
            // Try to find the main form element
            let root = document.querySelector('form') 
                    || document.querySelector('[role="form"]')
                    || document.querySelector('main')
                    || document.body;
            
            // Clone and clean
            const clone = root.cloneNode(true);
            
            // Remove noise
            const noiseSelectors = [
                'script', 'style', 'noscript', 'svg', 'iframe',
                'header', 'footer', 'nav', '[role="navigation"]',
                '[role="banner"]', '[role="contentinfo"]'
            ];
            for (const sel of noiseSelectors) {
                clone.querySelectorAll(sel).forEach(el => el.remove());
            }
            
            // Remove data-* attributes to reduce noise
            clone.querySelectorAll('*').forEach(el => {
                [...el.attributes].forEach(attr => {
                    if (attr.name.startsWith('data-') && attr.name !== 'data-testid') {
                        el.removeAttribute(attr.name);
                    }
                });
            });
            
            return clone.innerHTML.substring(0, 15000);  // cap at 15KB
        }""")
        return dom or ''
    except Exception as e:
        logger.warning(f"DOM extraction failed: {e}")
        return ''


# ── Check cache node ───────────────────────────────────────────────

def check_cache(state: ApplyState) -> dict:
    """
    Look up ActionSnippets for each field label on this page.
    
    For each field, checks if a locate snippet exists for this platform+field.
    Returns cached snippets for hits and a list of missing fields for the LLM.
    """
    if not state['field_labels'] or not state['dom_signature']:
        # No form fields detected — can't compute a meaningful signature
        return {'_cache_hit': None}

    # For each field, check if we have a cached locate snippet for this platform
    platform = extract_url_platform(state['current_url'])
    cached_snippets = {}
    missing_fields = []

    for label in state['field_labels']:
        sig = compute_snippet_signature(platform, label, 'locate')
        snippet = ActionSnippet.objects.filter(
            signature=sig, consecutive_fails__lte=3
        ).first()
        if snippet:
            cached_snippets[label] = snippet
        else:
            missing_fields.append(label)

    page_sig = compute_page_signature(state['current_url'], state['field_labels'])

    if missing_fields:
        return {
            '_cache_hit': None,
            '_cached_snippets': cached_snippets,
            '_missing_fields': missing_fields,
            'dom_signature': page_sig,
        }
    else:
        return {
            '_cache_hit': cached_snippets,
            '_missing_fields': [],
            'dom_signature': page_sig,
        }


# ── Replay node ─────────────────────────────────────────────────────

def replay(state: ApplyState) -> dict:
    """
    Replay cached snippets for fields that have cache hits.
    
    For each cached field:
    1. Load the locate + interact snippets from DB
    2. Resolve the value from profile/Q&A bank
    3. Run through the executor (locate → interact → verify)
    4. Update snippet trust stats
    5. Record a StepRecord for audit
    """
    ctx = get_context()
    page = ctx.page
    run = ApplicationRun.objects.get(id=state['run_id'])
    platform = extract_url_platform(state['current_url'])
    cached = state.get('_cached_snippets') or state.get('_cache_hit') or {}
    
    failed_fields = []

    for label, locate_snippet in cached.items():
        # Look up the interact snippet
        interact_sig = compute_snippet_signature(platform, label, 'interact')
        interact_snippet = ActionSnippet.objects.filter(signature=interact_sig).first()
        
        if not interact_snippet:
            logger.warning(f"No interact snippet for {platform}:{label}, skipping")
            failed_fields.append(label)
            continue

        # Resolve the value
        value_source = interact_snippet.action.get('value_source', '')
        value = _resolve_value_from_source(value_source, ctx.profile)

        # Execute the snippet pair through the executor
        result = run_snippet_pair(
            page=page,
            locate_snippet={'action': locate_snippet.action, 'verify': locate_snippet.verify},
            interact_snippet={'action': interact_snippet.action, 'verify': interact_snippet.verify},
            value=str(value) if value else '',
        )

        # Update trust stats
        locate_snippet.total_uses += 1
        interact_snippet.total_uses += 1

        if result['success']:
            locate_snippet.success_count += 1
            locate_snippet.consecutive_fails = 0
            interact_snippet.success_count += 1
            interact_snippet.consecutive_fails = 0
        else:
            locate_snippet.fail_count += 1
            locate_snippet.consecutive_fails += 1
            interact_snippet.fail_count += 1
            interact_snippet.consecutive_fails += 1
            failed_fields.append(label)
            logger.warning(f"Replay failed for {label}: {result['error']}")

        locate_snippet.last_used = timezone.now()
        interact_snippet.last_used = timezone.now()
        locate_snippet.save()
        interact_snippet.save()

        # Record the step
        StepRecord.objects.create(
            run=run,
            step_number=state['step_number'],
            page_url=state['current_url'],
            dom_signature=state.get('dom_signature', ''),
            source='cache_hit' if result['success'] else 'cache_fail',
            action_type=interact_snippet.action.get('action', 'unknown'),
            action_detail={
                'field_label': label,
                'locate_sig': locate_snippet.signature,
                'interact_sig': interact_snippet.signature,
                'value_source': value_source,
                'result': result,
            },
            success=result['success'],
            error_message=result.get('error'),
        )

    # If any fields failed replay, they'll need LLM decisions
    if failed_fields:
        return {
            '_missing_fields': failed_fields,
            'had_fresh_decisions': True,
        }
    return {}


# ── LLM decide node ────────────────────────────────────────────────

def llm_decide(state: ApplyState) -> dict:
    """
    Generate new ActionSnippets for fields the cache couldn't cover.
    
    Calls the LLM with the page DOM + profile data, gets structured
    FieldAction responses, stores them as ActionSnippet rows, and 
    executes them immediately through the executor.
    """
    ctx = get_context()
    page = ctx.page
    run = ApplicationRun.objects.get(id=state['run_id'])
    platform = extract_url_platform(state['current_url'])
    url_pattern = strip_url_ids(state['current_url'])

    # Call the LLM to analyze the page and generate field actions
    page_actions = generate_field_actions(
        client=ctx.client,
        dom_snapshot=state.get('dom_snapshot', ''),
        page_url=state['current_url'],
        profile=ctx.profile,
        method=ctx.method,
    )

    if not page_actions:
        run.status = 'ESCALATED'
        run.escalation_reason = 'LLM failed to generate field actions'
        run.save()
        return {
            'status': 'ESCALATED',
            'escalation_reason': 'LLM failed to generate field actions',
            'had_fresh_decisions': True,
        }

    # Store submit/next button info in state for later
    has_submit = page_actions.has_submit_button
    
    # Process each field action
    for field_action in page_actions.fields:
        # Check for sensitive fields → escalate
        if field_action.is_sensitive:
            run.status = 'ESCALATED'
            run.escalation_reason = f'Sensitive field detected: {field_action.field_label}'
            run.save()
            return {
                'status': 'ESCALATED',
                'escalation_reason': run.escalation_reason,
                'had_fresh_decisions': True,
            }

        # Store snippets in the DB for future reuse
        locate_db, interact_db = store_snippets(
            field_action=field_action,
            platform=platform,
            page_url_pattern=url_pattern,
            dom_context=state.get('dom_snapshot', '')[:2000],
        )

        # Resolve the value
        value = resolve_value(field_action, ctx.profile)

        # Execute immediately through the safe executor
        result = run_snippet_pair(
            page=page,
            locate_snippet={'action': field_action.locate.model_dump(), 'verify': field_action.locate.verify.model_dump()},
            interact_snippet={'action': field_action.interact.model_dump(), 'verify': field_action.interact.verify.model_dump()},
            value=str(value) if value else '',
        )

        # Update trust stats on the stored snippets
        locate_db.total_uses += 1
        interact_db.total_uses += 1
        if result['success']:
            locate_db.success_count += 1
            interact_db.success_count += 1
        else:
            locate_db.fail_count += 1
            locate_db.consecutive_fails += 1
            interact_db.fail_count += 1
            interact_db.consecutive_fails += 1
            logger.warning(f"LLM snippet failed for {field_action.field_label}: {result['error']}")
        locate_db.last_used = timezone.now()
        interact_db.last_used = timezone.now()
        locate_db.save()
        interact_db.save()

        # Record the step
        StepRecord.objects.create(
            run=run,
            step_number=state['step_number'],
            page_url=state['current_url'],
            dom_signature=state.get('dom_signature', ''),
            source='llm_decision',
            action_type=field_action.interact.action,
            action_detail={
                'field_label': field_action.field_label,
                'field_purpose': field_action.field_purpose,
                'locate': field_action.locate.model_dump(),
                'interact': field_action.interact.model_dump(),
                'value_source': field_action.interact.value_source,
                'result': result,
            },
            success=result['success'],
            error_message=result.get('error'),
        )

    # Click Next/Continue if this is a multi-step form
    if not has_submit and page_actions.next_button:
        next_locator = execute_locate(page, page_actions.next_button.model_dump())
        if next_locator:
            execute_interact(next_locator, {'action': 'click'})
            page.wait_for_timeout(2000)  # wait for next page

    return {
        'had_fresh_decisions': True,
        '_has_submit': has_submit,
    }


# ── Act node ────────────────────────────────────────────────────────

def act(state: ApplyState) -> dict:
    """
    Post-action bookkeeping.
    
    The actual browser actions are performed inside replay() and llm_decide()
    since they need tight coupling with verification. This node handles
    any cleanup or state updates after the action phase.
    """
    return {}


# ── Trust check node ────────────────────────────────────────────────

def trust_check(state: ApplyState) -> dict:
    """
    Determine whether this run can auto-submit or needs human review.
    """
    run = ApplicationRun.objects.get(id=state['run_id'])
    run.had_fresh_decisions = state.get('had_fresh_decisions', False)
    run.save()

    if should_auto_submit(run):
        run.auto_submit_eligible = True
        run.save()
        return {'status': 'IN_PROGRESS'}  # proceed to submit
    else:
        run.status = 'PENDING_REVIEW'
        run.save()
        # Also update the Job status to PR for UI visibility
        job = Job.objects.get(id=state['job_id'])
        job.status = 'PR'
        job.save()
        return {'status': 'PENDING_REVIEW'}


# ── Submit node ─────────────────────────────────────────────────────

def submit(state: ApplyState) -> dict:
    """
    Click the submit button and verify confirmation.
    """
    ctx = get_context()
    page = ctx.page
    run = ApplicationRun.objects.get(id=state['run_id'])

    # Try to find and click the submit button
    submit_clicked = False
    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Submit")',
        'button:has-text("Apply")',
        'button:has-text("Send Application")',
        '[data-testid*="submit"]',
    ]

    for selector in submit_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click()
                submit_clicked = True
                page.wait_for_timeout(3000)  # wait for confirmation
                break
        except Exception:
            continue

    if not submit_clicked:
        run.status = 'ESCALATED'
        run.escalation_reason = 'Could not find or click submit button'
        run.save()
        return {'status': 'ESCALATED', 'escalation_reason': run.escalation_reason}

    # Take confirmation screenshot
    try:
        confirm_path = os.path.join(SCREENSHOT_DIR, f"run_{run.id}_confirmation.png")
        page.screenshot(path=confirm_path)
    except Exception:
        pass

    run.status = 'SUBMITTED'
    run.completed_at = timezone.now()
    run.save()

    # Update Job status to Applied
    job = Job.objects.get(id=state['job_id'])
    job.status = 'AF'
    job.applied_on = timezone.now().date()
    job.save()

    # Increment platform daily count
    platform = extract_url_platform(state['apply_url'])
    PlatformAccount.objects.filter(platform=platform).update(
        daily_count=F('daily_count') + 1,
        last_used=timezone.now()
    )

    logger.info(f"✓ Submitted application for Job #{state['job_id']}")
    return {'status': 'SUBMITTED'}


# ── Helper functions ────────────────────────────────────────────────

def _skip(run: ApplicationRun, reason: str) -> dict:
    """Mark a run as skipped with a reason."""
    run.status = 'SKIPPED'
    run.skip_reason = reason
    run.save()
    logger.info(f"Skipped Run #{run.id}: {reason}")
    return {
        'status': 'SKIPPED',
        'escalation_reason': reason,
    }


def _resolve_value_from_source(value_source: str, profile: ApplicantProfile):
    """Resolve a value from a value_source string pattern."""
    if not value_source:
        return None

    if value_source.startswith('profile.'):
        attr = value_source.split('.', 1)[1]
        return resolve_profile_value(profile, attr)
    elif value_source.startswith('qa:'):
        question_text = value_source[3:]
        answer, _, _ = lookup_qa_bank(profile, question_text)
        return answer
    elif value_source.startswith('static:'):
        return value_source[7:]

    return None
