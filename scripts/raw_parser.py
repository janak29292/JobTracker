import re

from dateparser.search import search_dates
from django.utils import timezone

from job.models import JobRaw
from services.db_helpers import create_job
from services.description_parser import AdvancedJobParser


def run():
    """Parse all unparsed raw jobs and create structured Job records."""
    desc_parser = AdvancedJobParser()
    for raw_job in JobRaw.objects.filter(parsed=False):
        try:
            print(f"Parsing: {raw_job.company}: {raw_job.job_title}")
            try:
                raw_text, last_posted = parse_date_from_string(
                    raw_job.job_info.split('·')[1].strip(),
                    raw_job.created_at
                )
            except Exception as e:
                print(f"Job Info Parse Error: {e}")
                raw_text, last_posted = parse_date_from_string(
                    raw_job.job_info, raw_job.created_at
                )
            job_location = raw_job.job_info.split('·')[0].strip()
            description_data = desc_parser.parse(raw_job.description)

            if raw_job.platform == 'NI':
                parts = raw_job.job_info.split('·')
                description_data['salary'] = parts[3].strip()
                ratings = parts[4].strip()
            else:
                ratings = None

            create_job(
                raw_job=raw_job,
                last_posted=last_posted,
                job_location=job_location,
                description_data=description_data,
                raw_text=raw_text,
                platform=raw_job.platform,
                ratings=ratings,
            )

        except Exception as e:
            # print(traceback.format_exc())
            print(e)
            # breakpoint()


def parse_date_from_string(date_string, created_at):
    """Parse a relative date string into an absolute datetime."""
    return search_dates(
        re.sub(r'\bfew\b', '3', date_string, flags=re.IGNORECASE),
        settings={
            'RELATIVE_BASE': timezone.localtime(created_at),
            'TIMEZONE': 'Asia/Kolkata',
            'RETURN_AS_TIMEZONE_AWARE': True
        }
    )[0]
