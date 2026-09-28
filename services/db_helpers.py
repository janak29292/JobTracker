import json

from dateparser.date import DateDataParser
from dateutil.relativedelta import relativedelta
from django.utils import timezone

from django.db import transaction
from job.models import Job, Posting, TechStack, StatusEvent, JobMatch, JobMatchCluster


def create_job(*args, **kwargs):
    raw_job = kwargs.get('raw_job')
    job_location = kwargs.get('job_location')
    description_data = kwargs.get('description_data')
    last_posted = kwargs.get('last_posted')
    raw_text = kwargs.get("raw_text")
    platform = kwargs.get("platform")
    ratings = kwargs.get("ratings")

    tech_stack_primary = json.dumps(description_data['tech_stack']['primary'])
    tech_stack_all = json.dumps(description_data['tech_stack']['all'])
    salary = description_data['salary']

    for tech_stack in description_data['tech_stack']['all']:
        TechStack.objects.get_or_create(
            name=tech_stack
        )

    job, created = Job.objects.update_or_create(
        company=raw_job.company,
        position=raw_job.job_title,
        job_location=job_location,
        defaults={
            'platform': platform,
            'job_description': raw_job.description,
            'apply_url': raw_job.job_url,
            'tech_stack_primary': tech_stack_primary,
            'tech_stack_all': tech_stack_all,
            'salary': salary,
            'ratings': ratings,
            'last_posted': last_posted,
        }
    )

    # IG (Ignored) - Makes sense, if you ignored it once, you probably don't want it popping back up.
    # NA (Not Applied) - It's already in the "open" state, so it doesn't need to be resurrected.
    # MA (Matched) - Same as NA, you're currently evaluating it.
    # IS (Interview Scheduled) - If you have an active interview scheduled, the scraper finding it again shouldn't reset it!
    # OR (Offer Received) - You won! The scraper definitely shouldn't reset this to "Not Applied".

    resurrect = False
    if job.status in ['AC', 'RE']:
        resurrect = True
    elif job.status in ['AF', 'AS', 'CR', 'RP', 'NE']:
        last_event = job.status_events.filter(status=job.status).order_by('-date').first()
        if last_event:
            days_passed = (timezone.now() - last_event.date).days
        else:
            # Fallback for legacy jobs that don't have status events
            # Prioritize applied_on since scraping can falsely refresh last_interaction
            ref_date = job.applied_on or job.last_interaction or job.last_posted
            days_passed = (timezone.now().date() - ref_date).days if ref_date else 100
            
        if job.status in ['AF', 'AS', 'CR'] and days_passed >= 20:
            resurrect = True
        elif job.status == 'RP' and days_passed >= 30:
            resurrect = True
        elif job.status == 'NE' and days_passed >= 45:
            resurrect = True

    if resurrect:
        job.status = 'NA'
        job.save()

    ddp = DateDataParser()
    period = ddp.get_date_data(raw_text).period

    min_date = last_posted - relativedelta(**{
        'days' if period == 'week' else f'{period}s': 7 if period == 'week' else 1
    })

    if not Posting.objects.filter(
        job=job,
        platform_job_id=raw_job.job_id,
        date__gte=min_date
    ).exists():
        Posting.objects.get_or_create(
            job=job,
            date=last_posted,
            platform_job_id=raw_job.job_id,
            defaults={
                "raw_text": raw_text,
            }
        )
    raw_job.parsed = True
    raw_job.save()

def batch_update_job_status(jobs_to_update, status=None):
    """
    Perform a safe bulk update of job statuses, ensuring last_interaction is updated
    and StatusEvents are correctly generated.
    """
    if not jobs_to_update:
        return

    today = timezone.localtime(timezone.now()).date()
    
    with transaction.atomic():
        if status:
            # jobs_to_update can be a list of IDs or a queryset
            Job.objects.filter(id__in=jobs_to_update).update(
                status=status,
                last_interaction=today
            )
            jobs = Job.objects.filter(id__in=jobs_to_update)
            StatusEvent.objects.bulk_create([
                StatusEvent(job=job, status=status) for job in jobs
            ])


@transaction.atomic
def save_job_match(job, score, decision, llm_result, contexts):
    core = llm_result.core_foundation
    core_cluster = JobMatchCluster.objects.create(
        area=core.area,
        job_requires=core.job_requires,
        candidate_has=core.candidate_has,
        importance=core.importance,
        coverage=core.coverage
    )

    job_match = JobMatch.objects.create(
        job=job,
        core_foundation=core_cluster,
        match_score=score,
        decision=decision,
        reasoning=llm_result.reasoning,
        role_relevance=llm_result.role_relevance,
        candidate_level=llm_result.candidate_level,
        job_level=llm_result.job_level,
        salary_alignment=llm_result.salary_alignment,
        applicant_context=contexts.get('applicant_context', ''),
        job_context=contexts.get('job_context', ''),
        prompt=contexts.get('prompt', '')
    )

    secondary_clusters_to_create = [
        JobMatchCluster(
            match=job_match,
            area=c.area,
            job_requires=c.job_requires,
            candidate_has=c.candidate_has,
            importance=c.importance,
            coverage=c.coverage
        ) for c in llm_result.secondary_clusters
    ]
    JobMatchCluster.objects.bulk_create(secondary_clusters_to_create)

    return job_match
