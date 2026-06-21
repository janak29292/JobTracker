import json
import os
import re
import traceback
from urllib.parse import parse_qs, urlparse

from celery import shared_task, chain
from dateparser.search import search_dates
from django.db import IntegrityError
from django.utils import timezone

from job.models import Session, JobRaw, JobUrl, Job, TechStack, Posting
from services.db_helpers import create_job
from services.description_parser import AdvancedJobParser
from services.parser import Parser


@shared_task
def initialize_parser(login_url, username, password):
    cookies = []
    parser = Parser()
    try:
        cookies = parser.login(login_url, username, password)
        parser.playwright.stop()
    except Exception as e:
        print(e)
        parser.playwright.stop()
    return cookies


@shared_task
def save_job_urls(source=None, scan_type=None):
    parser = Parser()
    job_id_list = parser.scan_urls(source=source, scan_type=scan_type)
    parser.playwright.stop()

    if source == 'linkedin':
        if scan_type == 'filtered':
            JobUrl.objects.bulk_create(
                [JobUrl(url=f"https://www.linkedin.com/jobs/search/?currentJobId={i}") for i in job_id_list]
            )
        if scan_type == 'recommended':
            JobUrl.objects.bulk_create(
                [JobUrl(url=f"https://www.linkedin.com/jobs/collections/recommended/?currentJobId={i}") for i in
                 job_id_list]
            )
    if source == 'naukri':
        JobUrl.objects.bulk_create(
            [JobUrl(url=url) for url in job_id_list]
        )


@shared_task
def add_jobs():
    buffer_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scrape_buffer.jsonl')

    # Recovery: if buffer file exists from a previous interrupted run, save it first
    if os.path.exists(buffer_path):
        print(f"Found previous scrape buffer, saving first...")
        _save_raw_data_from_file(buffer_path)

    job_urls = list(JobUrl.objects.filter(scanned=False))
    if len(job_urls) > 0:
        last = job_urls[-1].id
    else:
        print("No jobs to scrape")
        return

    parser = Parser()
    for job_url in job_urls:
        url = job_url.url
        job_url_id = job_url.id
        try:
            print(f"Scraping: {job_url_id} of {last}: {url}")
            result = parser.read_job(url)
            if result is None:
                print(f"Expired: {job_url_id}: {url}")
                with open(buffer_path, 'a') as f:
                    f.write(json.dumps({"job_url_id": job_url_id}) + '\n')
                continue

            platform, description, company, job_title, job_info = result
            page_url = parser.page.url
            parsed = urlparse(page_url)

            # Extract job_id based on source
            if 'naukri.com' in parsed.netloc:
                # Naukri: job ID is the last numeric segment in the path
                job_id = re.search(r'-(\d{10,})/?$', parsed.path).group(1)
            else:
                # LinkedIn: job ID is in the currentJobId query param
                job_id = parse_qs(parsed.query)['currentJobId'][0]

            raw_data = {
                "platform": platform,
                "description": description,
                "company": company,
                "job_title": job_title,
                "job_info": job_info,
                "job_id": job_id,
                "job_url": page_url,
                "job_url_id": job_url_id
            }

            # Append as a single JSON line
            with open(buffer_path, 'a') as f:
                f.write(json.dumps(raw_data) + '\n')

        except Exception as e:
            print(e)
            # print('-------------------')
            # print(traceback.format_exc())
            parser.playwright.stop()
            parser = Parser()
    parser.playwright.stop()

    _save_raw_data_from_file(buffer_path)


def _save_raw_data_from_file(buffer_path):
    with open(buffer_path, 'r') as f:
        lines = f.readlines()

    print(f"Saving {len(lines)} scraped jobs from buffer...")

    # Bulk-fetch all JobUrls upfront
    parsed_lines = []
    for line in lines:
        parsed_lines.append(json.loads(line))
    url_ids = [entry["job_url_id"] for entry in parsed_lines]
    job_urls_map = {ju.id: ju for ju in JobUrl.objects.filter(id__in=url_ids)}

    job_raw_list = []
    scanned_url_ids = []
    expired_url_ids = []
    remaining_lines = []

    for i, raw_data in enumerate(parsed_lines):
        try:
            job_url = job_urls_map.get(raw_data["job_url_id"])
            if not job_url:
                print(f"JobUrl not found: {raw_data['job_url_id']}")
                continue

            if "description" not in raw_data:
                # Expired/skipped job — collect for bulk update
                print(f"Marking scanned (expired): {job_url.id}")
                expired_url_ids.append(job_url.id)
                continue

            print(f"Saving: {job_url.id}: {job_url.url}")
            job_raw_list.append(JobRaw(
                platform=raw_data["platform"],
                description=raw_data["description"],
                company=raw_data["company"],
                job_title=raw_data["job_title"],
                job_info=raw_data["job_info"],
                job_id=raw_data["job_id"],
                job_url=raw_data["job_url"],
            ))
            scanned_url_ids.append(job_url.id)

        except Exception as e:
            print(f"Error processing job: {e}")
            remaining_lines.append(lines[i])

    # Bulk update expired jobs
    if expired_url_ids:
        JobUrl.objects.filter(id__in=expired_url_ids).update(scanned=True)

    try:
        print(f"Saving {len(job_raw_list)} scraped jobs from buffer...")
        JobRaw.objects.bulk_create(job_raw_list)
        # Only mark as scanned AFTER bulk_create succeeds
        JobUrl.objects.filter(id__in=scanned_url_ids).update(scanned=True)
    except Exception as e:
        print(f"Raw Jobs not Saved: {e}")
        # Keep all lines in buffer for retry since bulk_create failed
        remaining_lines = list(lines)

    with open(buffer_path, 'w') as f:
        f.writelines(remaining_lines)


@shared_task
def parse_jobs():
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
                description_data['experience'] = desc_parser.extract_experience(parts[2].strip())
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
    return search_dates(
        re.sub(r'\bfew\b', '3', date_string, flags=re.IGNORECASE),
        settings={
            'RELATIVE_BASE': timezone.localtime(created_at),
            'TIMEZONE': 'Asia/Kolkata',
            'RETURN_AS_TIMEZONE_AWARE': True
        }
    )[0]
