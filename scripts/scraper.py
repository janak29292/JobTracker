import json
import re
from urllib.parse import parse_qs, urlparse

from django.conf import settings

from job.models import JobUrl, JobRaw
from services.parser import Parser


def run():
    """Scrape job details from unscanned URLs and save as raw data."""
    buffer_dir = settings.BASE_DIR / 'buffer'
    buffer_dir.mkdir(exist_ok=True)
    buffer_path = buffer_dir / 'scrape_buffer.jsonl'

    # Recovery: if buffer file exists from a previous interrupted run, save it first
    if buffer_path.exists():
        print(f"Found previous scrape buffer, saving first...")
        save_from_buffer(buffer_path)

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
            parser.playwright.stop()
            parser = Parser()
    parser.playwright.stop()

    save_from_buffer(buffer_path)


def save_from_buffer(buffer_path):
    """Read scraped data from buffer file and save to database."""
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
