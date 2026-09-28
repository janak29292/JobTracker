import os
from celery import shared_task

from scripts import scanner, scraper, raw_parser, tracker, jd_extractor, tech_curator, job_matcher
from scripts.auto_apply import run_batch, resume_run


@shared_task
def initialize_parser(login_url, username, password):
    tracker.run(login_url, username, password)


@shared_task
def save_job_urls(source=None, scan_type=None):
    scanner.run(source=source, scan_type=scan_type)


@shared_task
def add_jobs():
    scraper.run()


@shared_task
def parse_jobs():
    raw_parser.run()


@shared_task
def extract_jd_fields(batch_size=None, platform=None, resume=False, job_ids=None, dry_run=False):
    jd_extractor.run(batch_size=batch_size, platform=platform, resume=resume, job_ids=job_ids, dry_run=dry_run)


@shared_task
def auto_curate_buffer(batch_size=None, add_to_jobs=False, dry_run=False):
    tech_curator.run(batch_size=batch_size, add_to_jobs=add_to_jobs, dry_run=dry_run)


@shared_task
def qualify_jobs(batch_size=None, job_ids=None, resume=True):
    job_matcher.run(batch_size=batch_size, job_ids=job_ids, resume=resume)


@shared_task
def auto_apply_batch(batch_size=None):
    """Process queued applications through the auto-apply agent."""
    run_batch(batch_size=batch_size)


@shared_task
def resume_application(run_id: int):
    """Resume a parked application after human review approval."""
    resume_run(run_id)


@shared_task
def shutdown_pc():
    print("Shutdown flag detected. Shutting down the computer in 60 seconds...")
    os.system("shutdown.exe /s /t 60")

