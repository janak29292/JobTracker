from typing import Optional, Dict, Any, List
import logging
from django.db.models import F
from user.models import ApplicantProfile, Question, Answer, EncounteredQuestion, WorkExperience, Education, Project

logger = logging.getLogger(__name__)


def get_applicant_data_bundle(profile: ApplicantProfile) -> Dict[str, Any]:
    """
    Assemble the complete structured data bundle for the applicant.
    Provides flat profile info plus relational WorkExperience, Education, and Projects.
    This rich JSON is supplied directly to the LLM for macro and micro reasoning.
    """
    if not profile:
        return {}

    # Experiences
    experiences_qs = WorkExperience.objects.filter(profile=profile).order_by('-is_current', '-start_date')
    experiences_list = []
    for exp in experiences_qs:
        experiences_list.append({
            'company_name': exp.company_name,
            'job_title': exp.job_title,
            'start_date': str(exp.start_date) if exp.start_date else '',
            'end_date': str(exp.end_date) if exp.end_date else ('Present' if exp.is_current else ''),
            'is_current': exp.is_current,
            'tech_stacks': exp.tech_stacks or [],
        })

    # Education
    education_qs = Education.objects.filter(profile=profile).order_by('-end_date')
    education_list = []
    for edu in education_qs:
        education_list.append({
            'institution': edu.institution,
            'degree': edu.degree,
            'start_date': str(edu.start_date) if edu.start_date else '',
            'end_date': str(edu.end_date) if edu.end_date else '',
            'graduation_year': str(edu.end_date.year) if edu.end_date else '',
        })

    # Projects
    projects_qs = Project.objects.filter(profile=profile)
    projects_list = []
    for p in projects_qs:
        projects_list.append({
            'name': p.name,
            'description': p.description,
            'link': p.link or '',
            'tech_stacks': p.tech_stacks or [],
        })

    latest_exp = experiences_list[0] if experiences_list else {}
    latest_edu = education_list[0] if education_list else {}

    # Fetch custom Q&A bank answers if available
    qa_answers = {}
    try:
        for a in Answer.objects.filter(profile=profile).select_related('question'):
            if a.question and a.question.pattern:
                qa_answers[a.question.pattern] = a.text
    except Exception as e:
        logger.warning(f"Error fetching answers: {e}")

    return {
        'profile': {
            'first_name': profile.first_name,
            'last_name': profile.last_name,
            'full_name': profile.full_name,
            'email': profile.email,
            'phone_number': profile.phone_number,
            'current_location': profile.current_location,
            'preferred_locations': profile.preferred_locations or [],
            'years_of_experience': profile.years_of_experience,
            'notice_period_days': profile.notice_period_days,
            'work_authorization': profile.work_authorization,
            'current_salary': profile.current_salary_fixed + profile.current_salary_variable,
            'expected_salary': profile.expected_salary,
            'currency': profile.currency,
            'linkedin_url': profile.linkedin_url or '',
            'github_url': profile.github_url or '',
            'portfolio_url': profile.portfolio_url or '',
            'resume_file': profile.master_resume.path if profile.master_resume else None,
            'core_skills': profile.core_skills or [],
        },
        'qa_bank': qa_answers,
        'current_employment': {
            'company': latest_exp.get('company_name', ''),
            'title': latest_exp.get('job_title', profile.designation or ''),
            'start_date': latest_exp.get('start_date', ''),
            'is_current': latest_exp.get('is_current', True),
        },
        'latest_education': {
            'institution': latest_edu.get('institution', ''),
            'degree': latest_edu.get('degree', ''),
            'graduation_year': latest_edu.get('graduation_year', ''),
        },
        'experiences': experiences_list,
        'education': education_list,
        'projects': projects_list,
    }


def resolve_profile_value(profile: ApplicantProfile, field_key: str):
    """
    Direct model attribute fallback if accessed directly.
    In the v2 Hierarchical architecture, the LLM directly resolves and provides values.
    """
    if not profile or not field_key:
        return None
    cleaned = field_key.lower().strip().replace(' ', '_')
    if hasattr(profile, cleaned):
        return getattr(profile, cleaned)
    return None


def resolve_by_question_id(profile: ApplicantProfile, question_id: int) -> Optional[str]:
    """
    Given a Question ID from the preloaded catalog, query the user's Answer on demand.
    Returns answer text or None.
    """
    if not profile or not question_id:
        return None
    try:
        answer = Answer.objects.filter(profile=profile, question_id=question_id).first()
        return answer.text if answer else None
    except Exception as e:
        logger.warning(f"Error fetching answer for question_id={question_id}: {e}")
        return None


def track_encountered_question(
    profile: ApplicantProfile,
    question_text: str,
    is_optional: bool = True,
    job_id: Optional[int] = None
):
    """
    Track unmapped optional questions across runs and increment encounter counts.
    """
    if not profile or not question_text:
        return

    cleaned_text = question_text.strip()[:500]
    try:
        record, created = EncounteredQuestion.objects.get_or_create(
            user=profile,
            question_text=cleaned_text,
            defaults={
                'is_optional': is_optional,
                'encounter_count': 1,
            }
        )
        if not created:
            record.encounter_count = F('encounter_count') + 1
            record.save(update_fields=['encounter_count', 'updated_at'])
            logger.info(f"Incremented encounter count for optional question: '{cleaned_text}'")
        else:
            logger.info(f"Recorded new encountered optional question: '{cleaned_text}'")
    except Exception as e:
        logger.warning(f"Could not track encountered question: {e}")


def lookup_qa_bank(profile: ApplicantProfile, question_text: str):
    """
    Deterministic exact lookup in Q&A bank.
    Returns: (answer_text, category, is_exact_match) or (None, None, False).
    """
    exact = Question.objects.filter(pattern__iexact=question_text.strip()).first()
    if exact:
        answer = Answer.objects.filter(
            profile=profile, question=exact
        ).first()
        if answer:
            return answer.text, exact.category, True

    return None, None, False


def is_sensitive_category(category: str) -> bool:
    """Check if a question category should trigger escalation."""
    return category in ('DEMOGRAPHIC', 'SENSITIVE')
