import webbrowser
from celery import chain
from job.tasks import add_jobs, parse_jobs, save_job_urls


def run_job_pipeline(sources=None, scan_type=None, sync=False):
    """
    Runs the full pipeline to scan URLs, scrape details, and parse jobs.
    """
    if sync:
        for source in sources:
            save_job_urls(source=source, scan_type=scan_type)
        add_jobs()
        parse_jobs()
    else:
        scan_chain = [save_job_urls.si(source=source, scan_type=scan_type) for source in sources]
        pipeline = chain(
            *scan_chain,
            add_jobs.si(),
            parse_jobs.si(),
        )
        pipeline.delay()


def scan_job_urls(sources=None, scan_type=None, sync=False):
    for source in sources:
        if sync:
            save_job_urls(source=source, scan_type=scan_type)
        else:
            save_job_urls.delay(source=source, scan_type=scan_type)


def add_raw_jobs(sync=False):
    """
    Fetches LinkedIn Job urls from database and Scrapes Job data and
    stores in database as raw data
    """
    if sync:
        add_jobs()
    else:
        add_jobs.delay()


def parse_raw_jobs(sync=False):
    """
    Fetches stored raw data and parses it to convert into usable
    data
    """
    if sync:
        parse_jobs()
    else:
        parse_jobs.delay()


def open_in_browser(url):
    webbrowser.open(url)


def get_browser():
    var = webbrowser.get()
    breakpoint()
