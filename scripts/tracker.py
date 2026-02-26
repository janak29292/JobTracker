import webbrowser

from job.tasks import add_jobs, parse_jobs, save_linkedin_recommended_job_urls, \
    save_linkedin_filtered_job_urls, linkedin_recommended_job_pipeline, linkedin_filtered_job_pipeline


def linkedin_recommended_job_pipeline_caller():
    """

    """
    linkedin_recommended_job_pipeline.delay()

def linkedin_filtered_job_pipeline_caller():
    """

    """
    linkedin_filtered_job_pipeline.delay()

def scan_linkedin_recommended_job_urls():
    """
    Scrapes LinkedIn for recommended jobs and stores URLs in database
    """
    save_linkedin_recommended_job_urls.delay()


def scan_linkedin_filtered_job_urls():
    """
    Scrapes LinkedIn for filtered jobs and stores URLs in database for following params
    location: Gurgaon
    Date Posted: Past 24 hours
    Keywords: senior software engineer
    """
    save_linkedin_filtered_job_urls.delay()
    # save_linkedin_filtered_job_urls()


def add_raw_jobs():
    """
    Fetches LinkedIn Job urls from database and Scrapes Job data and
    stores in database as raw data
    """
    add_jobs.delay()


def parse_raw_jobs():
    """
    Fetches stored raw data and parses it to convert into usable
    data
    """
    parse_jobs.delay()


def open_in_browser(url):
    webbrowser.open(url)


def get_browser():
    var = webbrowser.get()
    breakpoint()
