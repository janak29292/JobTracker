import json
import os
import re
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from job.models import Session

LINKEDIN_USERNAME = os.getenv("LINKEDIN_USERNAME")
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD")
LINKEDIN_LOGIN_URL = 'https://www.linkedin.com/login'
LINKEDIN_RECOMMENDED = (
    'https://www.linkedin.com/jobs/collections/recommended/?'
    'discover=recommended'
    '&discoveryOrigin=JOBS_HOME_JYMBII'
)
LINKEDIN_FILTERED = (
    "https://www.linkedin.com/jobs/search/?"
    "&geoId=115884833"
    "&f_T=39%2C25169%2C25194"
    "&f_WT=1%2C3"
)

LINKEDIN_JOB_IDENTIFIER = 'data-occludable-job-id'
# At the top of your file
POSSIBLE_JOB_IDENTIFIERS = [
    'data-occludable-job-id',
    'data-job-id',
    'data-entity-urn',
]


sdfdskf = (
    "https://www.linkedin.com/jobs/search/?"
    "currentJobId=4371134213"
    "&f_T=9%2C39%2C25169%2C25194"
    "&f_TPR=r86400"
    "&geoId=115884833"
    "&keywords=senior%20software%20engineer%20python"
    "&origin=JOB_SEARCH_PAGE_JOB_FILTER"
    "&refresh=true"
    "&sortBy=R"
)

NAUKRI_FILTERED = (
    "https://www.naukri.com/python-django-senior-jobs?"
    "experience=4"
    "&cityTypeGid=6"
    "&cityTypeGid=72"
    "&cityTypeGid=73"
    "&cityTypeGid=213"
    "&cityTypeGid=220"
    "&cityTypeGid=350"
    "&cityTypeGid=9508"
)

class Parser:

    def __init__(self):
        self.playwright = sync_playwright().start()

        self.browser = self.playwright.chromium.launch(
            headless=False,  # Headless is MORE detectable
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                # '--start-maximized',
                # '--start-fullscreen',
            ],
            # slow_mo=1000,
        )

        context = self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            # viewport=None,
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            locale='en-US',
            timezone_id='Asia/Kolkata',
            permissions=['geolocation'],
        )
        self.page = context.new_page()

    def _clean_paragraph(self, text):
        """
        Removes extra blank spaces from string
        """
        return ' '.join(text.split()).strip()

    def _find_active_identifier(self):
        """Find which job identifier LinkedIn is currently using"""
        for identifier in POSSIBLE_JOB_IDENTIFIERS:
            test = self.page.locator(f'[{identifier}]').first
            if test.count() > 0:
                return identifier
        raise Exception("No valid job identifier found")

    def _find_scrollable_parent(self, element):
        scrollable_parent = element.evaluate('''
            el => {
                let parent = el.parentElement;
                while (parent) {
                    const overflowY = window.getComputedStyle(parent).overflowY;
                    const overflowX = window.getComputedStyle(parent).overflowX;

                    // Check if element is scrollable
                    if ((overflowY === 'scroll' || overflowY === 'auto') && parent.scrollHeight > parent.clientHeight) {
                        return parent.className; // or return other identifier
                    }
                    if ((overflowX === 'scroll' || overflowX === 'auto') && parent.scrollWidth > parent.clientWidth) {
                        return parent.className;
                    }

                    parent = parent.parentElement;
                }
                return null;
            }
        ''')
        return scrollable_parent
        # print(f"Scrollable parent class: {scrollable_parent}")

    # @shared_task
    def login(self, url, username, password):
        self.page.goto(url)
        if 'https://www.linkedin.com' in self.page.url:
            try:
                self.page.locator('#username').fill(username)
            except Exception as e:
                print(e)
            self.page.locator('#password').fill(password)
            try:
                self.page.get_by_label('Keep me logged in').dispatch_event('click', timeout=1000)
            except Exception as e:
                print(e)
            self.page.click('button[type="submit"]:has-text("Sign in")')
        cookies = self.page.context.cookies()
        return json.dumps(cookies)
        # breakpoint()

    def check_logged_in(self, redirect_url):
        if 'https://www.linkedin.com' in self.page.url:
            if 'login' in self.page.url:
                print("LOGIN FAILED, RETRYING")
                cookies = self.login(LINKEDIN_LOGIN_URL, LINKEDIN_USERNAME, LINKEDIN_PASSWORD)
                self.playwright.stop()
                Session.objects.update_or_create(
                    platform='LI',
                    defaults={
                        'data': cookies
                    }
                )
                self.__init__()
                self.page.context.add_cookies(json.loads(cookies))
                self.page.goto(redirect_url)
                time.sleep(2)

    #
    # def test_restart(self):
    #     time.sleep(5)
    #     self.playwright.stop()
    #     print(Session.objects.first())
    #     self.__init__()
    #     self.page.goto('https://example.com/')
    #     time.sleep(5)

    def scan_urls(self, cookies, filtered=False):
        self.page.context.add_cookies(json.loads(cookies))
        if filtered:
            self.page.goto(LINKEDIN_FILTERED)
            time.sleep(2)
            self.check_logged_in(LINKEDIN_FILTERED)
            self.page.locator('button[id="searchFilter_timePostedRange"]').click()
            # self.page.locator('label[for="timePostedRange-r86400"]').click()

            # Locate the Date Posted filter panel specifically
            date_filter_panel = self.page.locator('fieldset:has(legend:has-text("Date posted"))')

            # Now scope everything to this panel
            date_filter_panel.locator('label[for="timePostedRange-r86400"]').click()
            date_filter_panel.locator('button[aria-label^="Apply current filter"]').click()
            time.sleep(2)
        else:
            self.page.goto(LINKEDIN_RECOMMENDED)
            time.sleep(2)
            self.check_logged_in(LINKEDIN_RECOMMENDED)
        reading = True
        job_id_list = []
        while reading:
            try:
                active_identifier = self._find_active_identifier()
                print(active_identifier)

                job_cards = self.page.locator(f'[{active_identifier}]')
                self.page.locator(f'.{self._find_scrollable_parent(job_cards.first)}').evaluate('''
                    el => {
                        el.scrollTo({
                            top: el.scrollHeight,
                            behavior: 'smooth'
                        });
                    }
                ''')
                time.sleep(2)
                job_cards = self.page.locator(f'[{active_identifier}]')
                print(job_cards.count())
                job_id_page_list = list(
                    filter(
                        lambda x: re.match(r'^\d{10}$', x) is not None,
                        [i.get_attribute(active_identifier) for i in job_cards.all()]
                    )
                )
                job_id_list.extend(job_id_page_list)

                next_button = self.page.locator('button[aria-label="View next page"]')
                next_button.click()
                time.sleep(3)
            except Exception as e:
                reading = False

        print("FInished Reading")
        return job_id_list

    def read_job(self, url, cookies):
        parsed_url = urlparse(url)
        self.page.context.add_cookies(json.loads(cookies))
        if parsed_url.netloc == 'www.linkedin.com':
            if parsed_url.path == '/jobs/collections/recommended/' or parsed_url.path == '/jobs/search/':
                self.page.goto(url)
                time.sleep(2)
                self.check_logged_in(url)
                return self.read_linkedin_recommended()
            elif match := re.search(r'/jobs/view/(\d+)/', parsed_url.path):
                page_url = f"https://www.linkedin.com/jobs/view/?currentJobId={match.group(1)}"
                self.page.goto(page_url)
                time.sleep(2)
                self.check_logged_in(url)
                return self.read_linkedin_recommended()

    def read_linkedin_recommended(self):
        # start_time = time.time()
        # print("Starting")
        time.sleep(2)
        try:
            description = self.page.locator('h2:has-text("About the job") + div p').first.inner_text(timeout=10000)
        except Exception as e:
            # print(f"First Fail: {start_time - time.time()}")
            try:
                description = self.page.locator('h2:has-text("About the job") + div + div p').first.inner_text(timeout=10)
            except Exception as e:
                # print(f"Second Fail: {start_time - time.time()}")
                raise e

        # self.page.locator('#myReferenceElement + div:first-of-type')
        # soup = BeautifulSoup(content, 'html.parser')
        company = self._clean_paragraph(
            self.page.locator('.job-details-jobs-unified-top-card__company-name').inner_text()
        )
        job_title = self._clean_paragraph(
            self.page.locator('.job-details-jobs-unified-top-card__job-title').inner_text()
        )
        job_info = self._clean_paragraph(
            self.page.locator('.job-details-jobs-unified-top-card__tertiary-description-container').inner_text()
        )
        return description, company, job_title, job_info
