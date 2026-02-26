import json
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
def save_linkedin_recommended_job_urls():
    cookies = Session.objects.get(
        platform='LI'
    ).data
    parser = Parser()
    job_id_list = parser.scan_urls(cookies)
    parser.playwright.stop()

    JobUrl.objects.bulk_create(
        [JobUrl(url=f"https://www.linkedin.com/jobs/collections/recommended/?currentJobId={i}") for i in job_id_list]
    )


@shared_task
def save_linkedin_filtered_job_urls():
    cookies = Session.objects.get(
        platform='LI'
    ).data
    parser = Parser()
    job_id_list = parser.scan_urls(cookies, filtered=True)
    parser.playwright.stop()

    JobUrl.objects.bulk_create(
        [JobUrl(url=f"https://www.linkedin.com/jobs/search/?currentJobId={i}") for i in job_id_list]
    )


@shared_task
def add_jobs():
    cookies = Session.objects.get(
        platform='LI'
    ).data
    raw_data_list = []
    job_urls = list(JobUrl.objects.filter(scanned=False))
    parser = Parser()
    for job_url in job_urls:
        url = job_url.url
        job_url_id = job_url.id
        try:
            print(f"Scraping: {job_url_id}: {url}")
            description, company, job_title, job_info = parser.read_job(url, cookies)
            page_url = parser.page.url
            # parser.playwright.stop()
            raw_data_list.append(
                {
                    "description": description,
                    "company": company,
                    "job_title": job_title,
                    "job_info": job_info,
                    "job_id": parse_qs(urlparse(page_url).query)['currentJobId'][0],
                    "job_url": page_url,
                    "job_url_id": job_url_id
                }
            )
        except Exception as e:
            print(e)
            print('-------------------')
            print(traceback.format_exc())
            parser.playwright.stop()
            parser = Parser()
            # raise e
    parser.playwright.stop()
    for raw_data in raw_data_list:
        job_url = JobUrl.objects.get(id=raw_data["job_url_id"])
        url = job_url.url
        job_url_id = job_url.id
        try:
            print(f"Saving: {job_url_id}: {url}")
            JobRaw.objects.create(
                description=raw_data["description"],
                company=raw_data["company"],
                job_title=raw_data["job_title"],
                job_info=raw_data["job_info"],
                job_id=raw_data["job_id"],
                job_url=raw_data["job_url"],
            )
        except IntegrityError:
            print(
                f"Job: {parse_qs(urlparse(raw_data['job_url']).query)['currentJobId'][0]}: "
                f"{raw_data['company']}: {raw_data['job_title']} already Exists"
            )
        job_url.scanned = True
        job_url.save()
    print(f"Job Urls left: {JobUrl.objects.filter(scanned=False).count()}")


@shared_task
def parse_jobs():
    parser = AdvancedJobParser()
    for raw_job in JobRaw.objects.filter(parsed=False):
        try:
            print(f"Parsing: {raw_job.company}: {raw_job.job_title}")
            raw_text, last_posted = search_dates(
                raw_job.job_info,
                settings={
                    'RELATIVE_BASE': timezone.localtime(raw_job.created_at),
                    'TIMEZONE': 'Asia/Kolkata',
                    'RETURN_AS_TIMEZONE_AWARE': True
                }
            )[0]
            job_location = raw_job.job_info.split('·')[0]
            description_data = parser.parse(raw_job.description)
            create_job(
                raw_job=raw_job,
                last_posted=last_posted,
                job_location=job_location,
                description_data=description_data,
                raw_text=raw_text
            )

        except Exception as e:
            # print(traceback.format_exc())
            print(e)
            # breakpoint()


linkedin_recommended_job_pipeline = chain(
    save_linkedin_recommended_job_urls.si(),
    add_jobs.si(),
    parse_jobs.si(),
)

linkedin_filtered_job_pipeline = chain(
    save_linkedin_filtered_job_urls.si(),
    add_jobs.si(),
    parse_jobs.si(),
)

@shared_task
def temp_task():
    cookies = Session.objects.get(
        platform='LI'
    ).data
    queryset = JobUrl.objects.filter(scanned=True)
    querylist = [(i.id, i.url) for i in queryset]
    data_dict = dict()
    failed_list = []
    parser = Parser()
    for i in querylist:
        try:
            print(f'Reading: {i[0]}: {i[1]}')
            description, company, job_title, job_info = parser.read_job(
                i[1],
                cookies
            )
            data_dict[parser.page.url] = (company, job_title)
        except Exception as e:
            print(f'FAILED: {i[0]}: {i[1]}')
            failed_list.append(i[0])
            print(traceback.format_exc())
    parser.playwright.stop()
    Session.objects.update_or_create(
        platform='RAW',
        defaults={
            'data': json.dumps((data_dict, failed_list), )
        }
    )
