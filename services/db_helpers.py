import json

from dateparser.date import DateDataParser
from dateutil.relativedelta import relativedelta

from job.models import Job, Posting, TechStack


def create_job(*args, **kwargs):
    raw_job = kwargs.get('raw_job')
    job_location = kwargs.get('job_location')
    description_data = kwargs.get('description_data')
    last_posted = kwargs.get('last_posted')
    raw_text = kwargs.get("raw_text")

    experience_min = description_data['experience']['min_years']
    experience_max = description_data['experience']['max_years']
    tech_stack_primary = json.dumps(description_data['tech_stack']['primary'])
    tech_stack_all = json.dumps(description_data['tech_stack']['all'])
    salary = description_data['salary']

    for tech_stack in description_data['tech_stack']['all']:
        TechStack.objects.get_or_create(
            name=tech_stack
        )
    job, created = Job.objects.get_or_create(
        company=raw_job.company,
        position=raw_job.job_title,
        job_location=job_location,
        defaults={
            'job_description': raw_job.description,
            'apply_url': raw_job.job_url,
            'experience_min': experience_min,
            'experience_max': experience_max,
            'tech_stack_primary': tech_stack_primary,
            'tech_stack_all': tech_stack_all,
            'salary': salary,
            'last_posted': last_posted,
        }
    )
    if job.status == 'AC':
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
            defaults={
                "raw_text": raw_text,
            }
        )
    raw_job.parsed = True
    raw_job.save()
