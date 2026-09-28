"""
LLM integration for the auto-apply agent.

Builds prompts for the LLM to generate ActionSnippets, and processes
the structured response into ActionSnippet rows in the database.

Uses OllamaClient.call_model_structured() with the PageFieldActions
Pydantic model via instructor — same pattern as the job matcher.
"""
import logging
from typing import Optional

from django.utils import timezone

from job.models import ActionSnippet
from services.apply_agent.field_mapper import lookup_qa_bank
from user.models import ApplicantProfile
from services.ollama_client import OllamaClient
from services.apply_agent.schemas import PageFieldActions, FieldAction
from services.apply_agent.signature import compute_snippet_signature, extract_url_platform
from services.apply_agent.field_mapper import resolve_profile_value

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Prompt construction
# ═══════════════════════════════════════════════════════════════════

def build_page_analysis_prompt(
    dom_snapshot: str,
    page_url: str,
    profile: ApplicantProfile,
    screenshot_description: str = '',
) -> str:
    """
    Build the prompt for the LLM to analyze a form page and produce
    structured action snippets for each field.
    """
    # Build a concise profile context (the LLM needs to know what data is available)
    profile_context = f"""AVAILABLE PROFILE DATA:
- full_name: {profile.full_name}
- first_name: {profile.first_name}
- last_name: {profile.last_name}
- email: {profile.email}
- phone_number: {profile.phone_number}
- linkedin_url: {profile.linkedin_url or 'not set'}
- github_url: {profile.github_url or 'not set'}
- portfolio_url: {profile.portfolio_url or 'not set'}
- current_location: {profile.current_location}
- years_of_experience: {profile.years_of_experience}
- notice_period_days: {profile.notice_period_days}
- expected_salary: {profile.expected_salary} {profile.currency}
- work_authorization: {profile.work_authorization}
- master_resume: {'available' if profile.master_resume else 'not uploaded'}
- designation: {profile.designation}
"""

    # Q&A bank entries the LLM can reference
    qa_entries = []
    for answer in profile.answers.select_related('question').all():
        qa_entries.append(f"  Q: {answer.question.pattern} → A: {answer.text} (category: {answer.question.category})")
    qa_section = "\n".join(qa_entries) if qa_entries else "  (no stored answers yet)"

    prompt = f"""You are a browser automation agent analyzing a job application form page.

Your task: For each fillable field on this page, produce a structured action plan
that tells the executor how to locate the element and interact with it.

{profile_context}

STORED Q&A BANK:
{qa_section}

PAGE URL: {page_url}

PAGE DOM (cleaned HTML of the form area):
{dom_snapshot}

{f'VISUAL DESCRIPTION: {screenshot_description}' if screenshot_description else ''}

RULES:
1. For each field, choose the MOST RELIABLE locate strategy:
   - Prefer 'label' or 'id' over 'css' when available (more stable across page updates)
   - Use 'css' with attribute selectors like [name=...] or [aria-label=...] when no label/id exists
   - Use 'xpath' only as a last resort when CSS can't express the query
   - Always provide 1-3 fallback selectors

2. For value_source:
   - Use 'profile.xxx' when the field maps to a profile attribute
   - Use 'qa:question text' when it matches a Q&A bank entry
   - Use 'static:value' for fields with obvious fixed answers (like "Yes" for "Are you willing to relocate?" when the profile shows willingness_to_relocate is true)
   - For SENSITIVE fields (race, gender, disability, veteran, criminal history, salary negotiation), set is_sensitive=true

3. For verification:
   - 'fill' actions → verify with value_matches + compare='input_value'
   - 'select' actions → verify with option_selected
   - 'click' actions → verify with page_changed or element_visible
   - 'check'/'uncheck' actions → verify with is_checked
   - 'upload' actions → verify with element_visible (look for filename appearing)

4. Mark has_submit_button=true only if the button would SUBMIT the application.
   'Next', 'Continue', 'Save & Continue' are NOT submit buttons.
"""
    return prompt


# ═══════════════════════════════════════════════════════════════════
# LLM call and response processing
# ═══════════════════════════════════════════════════════════════════

def generate_field_actions(
    client: OllamaClient,
    dom_snapshot: str,
    page_url: str,
    profile: ApplicantProfile,
    method: str = 'instructor',
) -> Optional[PageFieldActions]:
    """
    Call the LLM to produce structured field actions for the current page.
    
    Returns a PageFieldActions object, or None if the LLM call fails.
    """
    prompt = build_page_analysis_prompt(dom_snapshot, page_url, profile)

    try:
        result, _ = client.call_model_structured(
            prompt=prompt,
            response_model=PageFieldActions,
            method=method,
        )
        return result
    except Exception as e:
        logger.error(f"LLM snippet generation failed: {e}")
        return None


def store_snippets(
    field_action: FieldAction,
    platform: str,
    page_url_pattern: str,
    dom_context: str = '',
) -> tuple[ActionSnippet, ActionSnippet]:
    """
    Store a FieldAction's locate and interact as separate ActionSnippet rows.
    
    Returns (locate_snippet, interact_snippet) Django model instances.
    """
    # Locate snippet
    locate_sig = compute_snippet_signature(platform, field_action.field_label, 'locate')
    locate_snippet, created = ActionSnippet.objects.update_or_create(
        signature=locate_sig,
        defaults={
            'snippet_type': 'locate',
            'platform': platform,
            'field_label': field_action.field_label,
            'page_url_pattern': page_url_pattern,
            'action': field_action.locate.model_dump(),
            'verify': field_action.locate.verify.model_dump(),
            'llm_reasoning': field_action.locate.reasoning,
            'dom_context': dom_context,
        }
    )
    if created:
        logger.info(f"New locate snippet: {platform}:{field_action.field_label}")

    # Interact snippet
    interact_sig = compute_snippet_signature(platform, field_action.field_label, 'interact')
    interact_snippet, created = ActionSnippet.objects.update_or_create(
        signature=interact_sig,
        defaults={
            'snippet_type': 'interact',
            'platform': platform,
            'field_label': field_action.field_label,
            'page_url_pattern': page_url_pattern,
            'action': field_action.interact.model_dump(),
            'verify': field_action.interact.verify.model_dump(),
            'dom_context': dom_context,
        }
    )
    if created:
        logger.info(f"New interact snippet: {platform}:{field_action.field_label}")

    return locate_snippet, interact_snippet


def resolve_value(field_action: FieldAction, profile: ApplicantProfile) -> Optional[str]:
    """
    Resolve the actual value to use for a field action.
    
    Interprets value_source patterns:
        'profile.xxx' → looks up profile attribute
        'qa:question'  → looks up Q&A bank
        'static:value' → returns the literal value
        None           → returns None (for clicks, checks, etc.)
    """
    source = field_action.interact.value_source
    if not source:
        return None

    if source.startswith('profile.'):
        attr = source.split('.', 1)[1]
        value = resolve_profile_value(profile, attr)
        return str(value) if value is not None else None

    elif source.startswith('qa:'):
        question_text = source[3:]
        answer, category, _ = lookup_qa_bank(profile, question_text)
        return answer

    elif source.startswith('static:'):
        return source[7:]

    return None
