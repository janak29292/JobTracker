"""
Profile field mapping and Q&A bank integration.

Maps form field labels to ApplicantProfile fields (static mapping),
and falls back to the Q&A bank for questions the static map doesn't cover.
"""
from user.models import ApplicantProfile, Question, Answer


# ── Static profile field map ──
# Normalized label → profile attribute path
# The LLM's job is to map the ACTUAL field label (which could be
# "Mobile No.", "Contact Number", "Phone #") to one of these keys.
PROFILE_FIELD_MAP = {
    'first name': 'first_name',
    'last name': 'last_name',
    'full name': 'full_name',  # property
    'email': 'email',
    'phone': 'phone_number',
    'linkedin': 'linkedin_url',
    'github': 'github_url',
    'portfolio': 'portfolio_url',
    'current location': 'current_location',
    'city': 'current_location',
    'notice period': 'notice_period_days',
    'current ctc': '_current_ctc',  # computed: fixed + variable
    'current salary': '_current_ctc',
    'expected ctc': 'expected_salary',
    'expected salary': 'expected_salary',
    'years of experience': 'years_of_experience',
    'total experience': 'years_of_experience',
    'work authorization': 'work_authorization',
    'visa status': 'work_authorization',
    'resume': '_master_resume',  # triggers file upload
    'cv': '_master_resume',
}


def resolve_profile_value(profile: ApplicantProfile, field_key: str):
    """
    Given a normalized field key from PROFILE_FIELD_MAP,
    return the value from the profile.
    
    Returns None if the key is not recognized or the value is empty.
    """
    attr = PROFILE_FIELD_MAP.get(field_key.lower().strip())
    if not attr:
        return None

    # Special computed fields
    if attr == '_current_ctc':
        return profile.current_salary_fixed + profile.current_salary_variable
    if attr == '_master_resume':
        return profile.master_resume if profile.master_resume else None
    if attr == 'full_name':
        return profile.full_name

    return getattr(profile, attr, None)


def lookup_qa_bank(profile: ApplicantProfile, question_text: str):
    """
    Look up a question in the Q&A bank.
    
    Returns:
        (answer_text, category, is_exact_match) or (None, None, False) if no match.
    """
    # 1. Exact match (case-insensitive)
    exact = Question.objects.filter(pattern__iexact=question_text.strip()).first()
    if exact:
        answer = Answer.objects.filter(
            profile=profile, question=exact
        ).first()
        if answer:
            return answer.text, exact.category, True

    # 2. Semantic match will be handled by the LLM node — 
    #    this function only does deterministic lookups.
    #    The LLM node calls this first, and if it returns None,
    #    it runs semantic matching via the LLM.
    return None, None, False


def is_sensitive_category(category: str) -> bool:
    """Check if a question category should trigger escalation."""
    return category in ('DEMOGRAPHIC', 'SENSITIVE')
