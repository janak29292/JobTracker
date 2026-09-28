"""
Auto-submit trust and review logic.

Determines whether a completed application run can be submitted
automatically or needs to be parked for human review.
"""
from django.conf import settings
from job.models import ApplicationRun


# Configurable thresholds — can be moved to settings.py or a DB config model later
BURN_IN_THRESHOLD = getattr(settings, 'AUTO_APPLY_BURN_IN_THRESHOLD', 10)
NATIVE_PLATFORM_ALWAYS_REVIEW = getattr(settings, 'AUTO_APPLY_NATIVE_ALWAYS_REVIEW', True)


def should_auto_submit(run: ApplicationRun) -> bool:
    """
    Determine if this application run can be submitted without human review.
    
    Rules:
    1. During burn-in (first N successful runs globally): everything goes to review.
       This proves the verification-after-replay mechanism itself is catching what it should.
    2. After burn-in: native platform applies (LinkedIn/Naukri) still require review
       because account safety matters more than speed.
    3. If any step in this run was a fresh LLM decision (cache miss), it needs review —
       a new decision hasn't been proven yet.
    4. All-cache-hit runs with all verifications passed → eligible for auto-submit.
    """
    # During burn-in: everything goes to review
    total_successful = ApplicationRun.objects.filter(status='SUBMITTED').count()
    if total_successful < BURN_IN_THRESHOLD:
        return False

    # Native platforms keep a higher bar — account safety
    if NATIVE_PLATFORM_ALWAYS_REVIEW and run.apply_type in ('native_linkedin', 'native_naukri'):
        return False

    # If any step was a fresh LLM decision, needs review
    if run.had_fresh_decisions:
        return False

    # All cache hits + all verifications passed → eligible
    return True
