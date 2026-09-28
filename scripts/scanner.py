from job.models import JobUrl
from services.parser import Parser


def run(source=None, scan_type=None):
    """Scan job listing pages and save discovered job URLs to the database."""
    parser = Parser()
    job_id_list = parser.scan_urls(source=source, scan_type=scan_type)
    parser.playwright.stop()

    if source == 'linkedin':
        if scan_type == 'filtered':
            urls = [f"https://www.linkedin.com/jobs/search/?currentJobId={i}" for i in job_id_list]
        elif scan_type == 'recommended':
            urls = [f"https://www.linkedin.com/jobs/collections/recommended/?currentJobId={i}" for i in job_id_list]
        else:
            urls = []
    elif source == 'naukri':
        urls = list(job_id_list)
    else:
        urls = []

    # Filter out URLs that already exist (prevents duplicates on pipeline restart)
    existing = set(JobUrl.objects.filter(url__in=urls, scanned=False).values_list('url', flat=True))
    new_urls = [url for url in urls if url not in existing]

    if new_urls:
        JobUrl.objects.bulk_create([JobUrl(url=url) for url in new_urls])

    print(f"Created {len(new_urls)} new JobUrls ({len(existing)} already existed)")
