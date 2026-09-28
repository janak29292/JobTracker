"""
Pydantic models for LLM-generated ActionSnippets.

These models define the structured vocabulary the LLM uses to produce
browser actions. The LLM fills in these schemas; a fixed executor
interprets them — no exec() involved.

Used with OllamaClient.call_model_structured() via instructor.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════
# Locate snippet — find an element on the page
# ═══════════════════════════════════════════════════════════════════

class LocateVerify(BaseModel):
    """Verification check for a locate action."""
    check: Literal[
        'element_exists', 'element_visible'
    ] = Field(
        description="What to verify after locating the element. "
        "element_exists — check the element is in the DOM. "
        "element_visible — check the element is visible on screen."
    )
    expected_tag: Optional[str] = Field(
        default=None,
        description="Expected HTML tag name (lowercase), e.g. 'input', 'select', 'textarea', 'button'. "
        "Leave null if the tag doesn't matter."
    )
    expected_attrs: Optional[dict] = Field(
        default=None,
        description="Expected HTML attributes as key-value pairs, e.g. {\"type\": \"text\"}, {\"type\": \"email\"}. "
        "Leave null if attributes don't matter."
    )


class LocateSnippet(BaseModel):
    """Structured instruction for finding a single element on a page."""
    strategy: Literal[
        'css', 'role', 'text', 'label', 'xpath', 'id', 'placeholder'
    ] = Field(
        description="How to find the element. "
        "css — CSS selector, e.g. 'input[name=\"firstName\"]'. "
        "role — ARIA role with optional name, e.g. 'textbox:First Name'. "
        "text — visible text content, e.g. 'Submit Application'. "
        "label — associated label text, e.g. 'First Name'. "
        "xpath — XPath expression (use only when CSS can't express the query). "
        "id — element ID attribute, e.g. 'firstName'. "
        "placeholder — placeholder text, e.g. 'Enter your name'."
    )
    selector: str = Field(
        description="The selector value for the chosen strategy. "
        "For 'role' strategy, format as 'role:accessible_name' e.g. 'textbox:First Name'. "
        "For all others, the raw selector string."
    )
    fallbacks: List[str] = Field(
        default_factory=list,
        description="Alternative selectors to try if the primary fails. "
        "Use the SAME strategy as the primary. "
        "Provide 1-3 alternatives covering common variants of the same field. "
        "e.g. if primary is css 'input[name=\"firstName\"]', "
        "fallbacks might be ['#first-name', '[aria-label=\"First Name\"]']."
    )
    verify: LocateVerify = Field(
        description="How to verify the locate succeeded."
    )
    reasoning: str = Field(
        description="1 sentence: why you chose this strategy and selector for this field."
    )


# ═══════════════════════════════════════════════════════════════════
# Interact snippet — perform an action on a found element
# ═══════════════════════════════════════════════════════════════════

class InteractVerify(BaseModel):
    """Verification check for an interact action."""
    check: Literal[
        'value_matches', 'is_checked', 'page_changed',
        'element_visible', 'option_selected'
    ] = Field(
        description="What to verify after the interaction. "
        "value_matches — the element's value matches what was filled (use for fill). "
        "is_checked — the checkbox/radio is in the checked state (use for check/uncheck). "
        "page_changed — the URL changed after clicking (use for navigation clicks). "
        "element_visible — a new element appeared (use for modals/next steps). "
        "option_selected — the dropdown's selected value matches (use for select)."
    )
    compare: Optional[str] = Field(
        default=None,
        description="What to compare against. "
        "input_value — compare element.input_value() to the filled value. "
        "inner_text — compare element.inner_text() to the expected text. "
        "Leave null for checks that don't need comparison (is_checked, page_changed)."
    )


class InteractSnippet(BaseModel):
    """Structured instruction for performing one action on a located element."""
    action: Literal[
        'fill', 'click', 'select', 'upload', 'check', 'uncheck'
    ] = Field(
        description="The browser action to perform. "
        "fill — type text into an input/textarea (clears first). "
        "click — click a button, link, or other clickable element. "
        "select — select an option from a dropdown. "
        "upload — upload a file to a file input. "
        "check — check a checkbox. "
        "uncheck — uncheck a checkbox."
    )
    value_source: Optional[str] = Field(
        default=None,
        description="Where the value comes from. Use one of these patterns: "
        "'profile.first_name', 'profile.last_name', 'profile.full_name', "
        "'profile.email', 'profile.phone_number', 'profile.linkedin_url', "
        "'profile.github_url', 'profile.portfolio_url', 'profile.current_location', "
        "'profile.notice_period_days', 'profile.expected_salary', "
        "'profile.years_of_experience', 'profile.work_authorization', "
        "'profile.master_resume' (for file upload). "
        "For Q&A bank answers: 'qa:<question text>' e.g. 'qa:Are you authorized to work in India?'. "
        "For static values: 'static:<value>' e.g. 'static:Yes'. "
        "Leave null for actions that don't need a value (click, check, uncheck)."
    )
    input_type: Optional[str] = Field(
        default=None,
        description="The HTML input type: text, email, tel, number, file, select, checkbox, radio, textarea. "
        "Helps the executor handle the element correctly. Leave null for click actions."
    )
    verify: InteractVerify = Field(
        description="How to verify the interaction succeeded."
    )


# ═══════════════════════════════════════════════════════════════════
# Combined response — what the LLM returns for a single field
# ═══════════════════════════════════════════════════════════════════

class FieldAction(BaseModel):
    """
    Complete action plan for ONE form field.
    
    Contains both the locate and interact instructions.
    The LLM generates this for each field it encounters.
    """
    field_label: str = Field(
        description="The human-readable label of this field exactly as it appears on the page."
    )
    field_purpose: str = Field(
        description="What this field is asking for in normalized terms, e.g. 'first_name', 'email', "
        "'phone_number', 'work_authorization', 'resume_upload', 'screening_question'. "
        "This helps the system match it to profile data."
    )
    is_sensitive: bool = Field(
        default=False,
        description="True if this is a sensitive/demographic question that should be escalated "
        "rather than auto-filled. Examples: race, gender, veteran status, disability, "
        "criminal history, salary negotiation. "
        "NOT sensitive: work authorization (factual), notice period, years of experience."
    )
    locate: LocateSnippet = Field(
        description="How to find this field's input element on the page."
    )
    interact: InteractSnippet = Field(
        description="What to do with the element once found."
    )


class PageFieldActions(BaseModel):
    """
    The LLM's response for all fields on the current page.
    
    Even though fields are processed one at a time during execution,
    the LLM sees the full page once and produces actions for all fields
    in a single call — this is more efficient than one LLM call per field.
    """
    fields: List[FieldAction] = Field(
        description="One action plan per form field visible on the current page. "
        "Include ALL fillable fields — inputs, textareas, selects, checkboxes, file uploads. "
        "Do NOT include read-only text, labels, or decorative elements. "
        "Order them top-to-bottom as they appear on the page."
    )
    has_submit_button: bool = Field(
        description="True if there is a submit/apply/send button visible on the current page. "
        "False if this is a multi-step form and the button says 'Next' or 'Continue'."
    )
    next_button: Optional[LocateSnippet] = Field(
        default=None,
        description="If this is a multi-step form, provide the locate snippet for the "
        "'Next'/'Continue'/'Save & Continue' button. Null if has_submit_button is True "
        "or if there's no navigation button."
    )
    submit_button: Optional[LocateSnippet] = Field(
        default=None,
        description="If has_submit_button is True, provide the locate snippet for the "
        "submit button. Null otherwise."
    )
    page_assessment: str = Field(
        description="1-2 sentences: what kind of page is this (login, personal info, "
        "screening questions, resume upload, review, confirmation) and any concerns."
    )
