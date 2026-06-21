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
    "&f_T=39%2C25169%2C25194"
    "&f_WT=1%2C3"
    "&f_TPR=r86400"
)

LINKEDIN_DATE_POSTED = {
    "&f_TPR=r86400": "Past 24 hours",
    "&f_TPR=r604800": "Past week",
    "&f_TPR=r2592000": "Past month"
}


LINKEDIN_LOCATIONS = {
    "&geoId=115884833": "Gurugram, Haryana, India",
    "&geoId=106187582": "Delhi, India",
    "&geoId=115918471": "New Delhi, Delhi, India",
    "&geoId=104869687": "Noida, Uttar Pradesh, India",
    "&geoId=90009626": "Greater Delhi Area"
}

LINKEDIN_LOCATIONS_2 = {
    "&f_PP=104793846,", # gURGAON
    "&f_PP=106442238", # GURUGRAM
    "&f_PP=104869687", # nOIDA
}

LINKEDIN_LOCATIONS_2_STR = "&f_PP=104793846,106442238,104869687"


# At the top of your file
POSSIBLE_JOB_IDENTIFIERS = [
    'data-occludable-job-id',
    'data-job-id',
    'data-entity-urn',
]


NAUKRI_LOGIN_URL = "https://www.naukri.com/nlogin/login"

NAUKRI_USERNAME = os.getenv('NAUKRI_USERNAME')
NAUKRI_PASSWORD = os.getenv('NAUKRI_PASSWORD')

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
    "&jobAge=1"
)

NAUKRI_FILTERED_2 = (
    "https://www.naukri.com/python-django-senior-jobs?"
    "experience=5"
    "&cityTypeGid=6"
    "&cityTypeGid=72"
    "&cityTypeGid=73"
    "&cityTypeGid=213"
    "&cityTypeGid=220"
    "&cityTypeGid=350"
    "&cityTypeGid=9508"
    "&jobAge=1"
    # "&functionAreaIdGid=3"
    "&functionAreaIdGid=5"
    # "&glbl_qcrc=1019"
    # "&glbl_qcrc=1020"
    # "&glbl_qcrc=1025"
    # "&glbl_qcrc=1026"
    "&glbl_qcrc=1028"
    "&ctcFilter=10to15"
    "&ctcFilter=25to50"
    "&ctcFilter=15to25"
)


class Parser:

    def __init__(self):
        self.cookies = list(Session.objects.exclude(platform='RAW').values())
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
        for i in self.cookies:
            context.add_cookies(json.loads(i.get('data')))
        self.page = context.new_page()

    def _clean_paragraph(self, text):
        """
        Removes extra blank spaces from string
        """
        return ' '.join(text.split()).strip()

    def _find_active_identifier(self, expected_count=None):
        """Find which job identifier LinkedIn is currently using.
        If expected_count is provided, prefers the identifier whose count
        matches expected_count (to avoid picking suggestion cards over
        filtered results). Falls back to highest count with a warning."""
        identifier_counts = {}
        best_identifier = None
        best_count = 0
        for identifier in POSSIBLE_JOB_IDENTIFIERS:
            elements = self.page.locator(f'[{identifier}]')
            count = sum(
                1 for el in elements.all()
                if el.get_attribute(identifier) and re.match(r'^\d{10}$', el.get_attribute(identifier))
            )
            print(f"{identifier}: {count} valid IDs")
            if count > 0:
                identifier_counts[identifier] = count
            if count > best_count:
                best_count = count
                best_identifier = identifier

        # If expected_count is provided, prefer the identifier that matches it
        if expected_count is not None:
            matching = [
                (ident, cnt) for ident, cnt in identifier_counts.items()
                if cnt == expected_count
            ]
            if matching:
                selected, selected_count = matching[0]
                print(
                    f"✓ Selected: {selected} ({selected_count} IDs) — "
                    f"matches expected header count ({expected_count})"
                )
                return selected
            else:
                # No exact match — fall back to highest count with warning
                if best_identifier:
                    print(
                        f"⚠ WARNING: No identifier matches expected count ({expected_count}). "
                        f"Falling back to highest: {best_identifier} ({best_count} IDs)"
                    )
                    return best_identifier

        if best_identifier:
            print(f"Selected: {best_identifier} ({best_count} IDs)")
            return best_identifier
        raise Exception("No valid job identifier found")

    def _wait_for_job_list_header_linkedin(self):
        """Wait for the LinkedIn search results header and extract the expected
        total job count (e.g. '14 results' -> 14). Returns None if not found."""
        try:
            self.page.wait_for_selector(
                '.jobs-search-results-list__subtitle span',
                timeout=30000
            )
            subtitle = self.page.locator(
                '.jobs-search-results-list__subtitle span'
            ).first.inner_text().strip()
            # e.g. "14 results" or "1,204 results"
            match = re.search(r'([\d,]+)\s*result', subtitle)
            if match:
                count = int(match.group(1).replace(',', ''))
                print(f"LinkedIn header: {subtitle} → expected {count} total jobs")
                return count
            print(f"LinkedIn header found but could not parse count: '{subtitle}'")
            return None
        except Exception as e:
            print(f"⚠ Could not find LinkedIn results header: {e}")
            return None

    def _wait_for_job_list_header_naukri(self):
        """Wait for the Naukri job list header and extract per-page expected
        count and total. Parses '1 - 20 of 57' → {page_expected: 20, total: 57}.
        Returns None if not found."""
        try:
            self.page.wait_for_selector(
                'span[class*="count-string"], #jobs-list-header',
                timeout=30000
            )
            count_el = self.page.locator('span[class*="count-string"]').first
            count_text = count_el.inner_text().strip()
            # e.g. "1 - 20 of 57" or "1 - 15 of 15"
            match = re.search(r'(\d+)\s*-\s*(\d+)\s+of\s+(\d+)', count_text)
            if match:
                page_start = int(match.group(1))
                page_end = int(match.group(2))
                total = int(match.group(3))
                page_expected = page_end - page_start + 1
                print(
                    f"Naukri header: '{count_text}' → "
                    f"expecting {page_expected} jobs on this page, {total} total"
                )
                return {'page_expected': page_expected, 'total': total}
            print(f"Naukri header found but could not parse: '{count_text}'")
            return None
        except Exception as e:
            print(f"⚠ Could not find Naukri results header: {e}")
            return None

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
        print(url)
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
        elif 'naukri.com' in self.page.url:
            try:
                username_loc = self.page.locator('form#loginForm input#usernameField').first
                username_loc.wait_for(state="visible", timeout=10000)
                username_loc.fill(username)
            except Exception as e:
                print(e)
            try:
                self.page.locator('form#loginForm input#passwordField').first.fill(password)
                self.page.locator('form#loginForm button[type="submit"]:has-text("Login")').first.click()
                time.sleep(3)
            except Exception as e:
                print(e)
        else:
            breakpoint()
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
                self.page.goto(redirect_url, wait_until='domcontentloaded')
                time.sleep(2)
        elif 'https://www.naukri.com' in self.page.url:
            try:
                self.page.wait_for_selector(
                    '#login_Layer, .nI-gNb-lg-rg__login, .nI-gNb-icon-img',
                    timeout=10000
                )
            except:
                pass
            
            is_logged_out = self.page.locator('#login_Layer, .nI-gNb-lg-rg__login').count() > 0 or 'nlogin/login' in self.page.url
            if is_logged_out:
                print("NAUKRI LOGIN FAILED, RETRYING")
                cookies = self.login(NAUKRI_LOGIN_URL, NAUKRI_USERNAME, NAUKRI_PASSWORD)
                self.playwright.stop()
                Session.objects.update_or_create(
                    platform='NI',
                    defaults={
                        'data': cookies
                    }
                )
                self.__init__()
                self.page.context.add_cookies(json.loads(cookies))
                self.page.goto(redirect_url)
                time.sleep(2)


    def scan_urls(self, source=None, scan_type=None):
        if source == 'linkedin':
            return self.scan_urls_linkedin(filtered=scan_type=='filtered')
        elif source == 'naukri':
            return self.scan_urls_naukri()
        return []

    def _safe_goto(self, url, wait_until='load', retries=3, timeout=60000):
        """Navigate to a URL with retries and longer timeout."""
        for attempt in range(1, retries + 1):
            try:
                self.page.goto(url, wait_until=wait_until, timeout=timeout)
                return
            except Exception as e:
                print(f"Navigation attempt {attempt}/{retries} failed for {url}: {e}")
                if attempt < retries:
                    time.sleep(2 * attempt)
                else:
                    raise

    def scan_urls_naukri(self):
        url = NAUKRI_FILTERED_2
        self._safe_goto(url)
        time.sleep(2)
        self.check_logged_in(url)

        job_url_list = []
        page_num = 0
        reading = True
        while reading:
            page_num += 1
            try:
                # Wait for the header to confirm page is loaded
                header_info = self._wait_for_job_list_header_naukri()
                page_expected = header_info['page_expected'] if header_info else None

                job_cards = self.page.locator('[data-job-id]')
                page_count = job_cards.count()
                print(f"Page {page_num} card count: {page_count}")

                # If 0 cards but header says there should be some, retry once
                if page_count == 0 and page_expected and page_expected > 0:
                    print(
                        f"⚠ Page {page_num}: 0 cards found but header expects "
                        f"{page_expected}. Waiting 5s and retrying..."
                    )
                    time.sleep(5)
                    job_cards = self.page.locator('[data-job-id]')
                    page_count = job_cards.count()
                    print(f"Page {page_num} retry card count: {page_count}")

                if page_expected is not None:
                    if page_count == page_expected:
                        print(f"✓ Page {page_num}: card count matches expected ({page_expected})")
                    else:
                        print(
                            f"⚠ Page {page_num}: card count ({page_count}) differs from "
                            f"expected ({page_expected})"
                        )

                job_id_page_list = [
                    card.locator('a.title').get_attribute('href') for card in job_cards.all()
                ]
                job_url_list.extend(job_id_page_list)

                try:
                    # Next page href follows pattern: /search-path-N (e.g., /python-django-senior-jobs-2)
                    url_path = urlparse(url).path.rstrip('/')
                    next_button = self.page.locator(f'a[href^="{url_path}-"]:has(span:text("Next"))')
                    next_button.click()
                    time.sleep(3)
                except Exception as e:
                    reading = False

            except Exception as e:
                print(f"Naukri scan error on page {page_num}: {e}")
                reading = False

        print(f"Naukri: Found {len(job_url_list)} job URLs")
        print(f"FInished Reading : {url}")
        return job_url_list

    def scan_urls_linkedin(self, filtered=False):
        # self.page.context.add_cookies(json.loads(cookies))
        if filtered:
            urls = [
                LINKEDIN_FILTERED + "&geoId=90009626",
                LINKEDIN_FILTERED + "&geoId=104869687&distance=5"
            ]
            # urls = [LINKEDIN_FILTERED + LINKEDIN_LOCATIONS_2_STR]
        else:
            urls = [LINKEDIN_RECOMMENDED]

        job_id_list = []
        for url in urls:
            self._safe_goto(url, wait_until='domcontentloaded')
            time.sleep(2)
            self.check_logged_in(url)

            page_num = 0
            reading = True
            while reading:
                page_num += 1
                try:
                    # Wait for header and get expected total job count
                    expected_total = self._wait_for_job_list_header_linkedin()

                    active_identifier = self._find_active_identifier(
                        expected_count=expected_total
                    )
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
                    print(f"Page {page_num} count: {job_cards.count()}")
                    job_id_page_list = list(
                        filter(
                            lambda x: re.match(r'^\d{10}$', x) is not None,
                            [i.get_attribute(active_identifier) for i in job_cards.all()]
                        )
                    )
                    page_job_count = len(job_id_page_list)
                    print(f"Page {page_num}: {page_job_count} jobs found")

                    # If 0 jobs but header says there should be some, retry once
                    if page_job_count == 0 and expected_total and expected_total > 0:
                        print(
                            f"⚠ Page {page_num}: 0 jobs found but header expects "
                            f"{expected_total}. Waiting 5s and retrying..."
                        )
                        time.sleep(5)
                        active_identifier = self._find_active_identifier(
                            expected_count=expected_total
                        )
                        job_cards = self.page.locator(f'[{active_identifier}]')
                        job_id_page_list = list(
                            filter(
                                lambda x: re.match(r'^\d{10}$', x) is not None,
                                [i.get_attribute(active_identifier) for i in job_cards.all()]
                            )
                        )
                        page_job_count = len(job_id_page_list)
                        print(f"Page {page_num} retry: {page_job_count} jobs found")

                    job_id_list.extend(job_id_page_list)

                    try:
                        next_button = self.page.locator('button[aria-label="View next page"]')
                        next_button.click()
                        time.sleep(3)
                    except Exception as e:
                        reading = False

                except Exception as e:
                    print(f"Scan error on page {page_num}: {e}")
                    reading = False

            print(f"FInished Reading : {url}")
        print(f"LinkedIn: Found {len(job_id_list)} job URLs")
        return job_id_list

    def read_job(self, url):
        parsed_url = urlparse(url)
        # self.page.context.add_cookies(json.loads(cookies))
        if parsed_url.netloc == 'www.linkedin.com':
            if parsed_url.path == '/jobs/collections/recommended/' or parsed_url.path == '/jobs/search/':
                self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
                time.sleep(2)
                self.check_logged_in(url)
                return self.read_linkedin_jobs()
            elif match := re.search(r'/jobs/view/(\d+)/', parsed_url.path):
                page_url = f"https://www.linkedin.com/jobs/view/?currentJobId={match.group(1)}"
                self.page.goto(page_url, wait_until='domcontentloaded')
                time.sleep(2)
                self.check_logged_in(url)
                return self.read_linkedin_jobs()
        if parsed_url.netloc == 'www.naukri.com':
            self.page.goto(url, wait_until='domcontentloaded')
            # We don't check login on every single job read for speed,
            # but if we get blocked we might need to. Letting scan_urls handle initial login for now.
            return self.read_naukri_jobs()

        raise NotImplemented

    def read_naukri_jobs(self):
        # Wait for either job title or expired alert
        try:
            self.page.wait_for_selector(
                'h1[class*="jd-header-title"], div[class*="exp-alert-message"]',
                timeout=10000
            )
        except:
            pass

        expired = self.page.locator('div[class*="exp-alert-message"]')
        if expired.count() > 0 and 'expired' in expired.inner_text().lower():
            return None

        job_title = self._clean_paragraph(
            self.page.locator('h1[class*="jd-header-title"]').inner_text()
        )
        company = self._clean_paragraph(
            self.page.locator('div[class*="jd-header-comp-name"] a').first.inner_text()
        )
        description = self.page.locator('section[class*="job-desc-container"]').inner_text()
        try:
            self.page.locator('a[class*="read-more-link"]').click(timeout=2000)
            time.sleep(1)
            description = self.page.locator('section[class*="job-desc-container"]').inner_text()
        except:
            pass

        # Fix Key Skills concatenation — extract chips individually
        try:
            chips = self.page.locator('div[class*="key-skill"] a[class*="chip"]').all()

            key_skill_div = self.page.locator('div[class*="key-skill"]')
            bad_blob = key_skill_div.inner_text() if key_skill_div.count() > 0 else ""

            if chips:
                preferred = []
                required = []
                for chip in chips:
                    name = chip.locator('span').inner_text().strip()
                    if chip.locator('i[class*="jd-save"]').count() > 0:
                        preferred.append(name)
                    else:
                        required.append(name)
                skills_text = "\nKey Skills"
                if preferred:
                    skills_text += f"\nPreferred: {', '.join(preferred)}"
                if required:
                    skills_text += f"\nOther: {', '.join(required)}"
                # if "Key Skills" in description:
                #     description = description[:description.index("Key Skills")] + skills_text
                if bad_blob and bad_blob in description:
                    description = description.replace(bad_blob, skills_text)
        except:
            pass  # If chip extraction fails, keep original description

        # Location
        location = self._clean_paragraph(
            self.page.locator('span[class*="jhc__location"]').first.inner_text()
        )

        # Stats: Posted, Openings, Applicants
        stats = {}
        stat_elements = self.page.locator('span[class*="jhc__stat"]').all()
        for stat in stat_elements:
            label = stat.locator('label').inner_text().strip().rstrip(':')
            value = stat.locator('span').inner_text().strip()
            stats[label] = value

        posted = stats.get('Posted', '')
        experience = self._clean_paragraph(
            self.page.locator('div[class*="jhc__exp"] span').first.inner_text()
        )
        salary = self._clean_paragraph(
            self.page.locator('div[class*="jhc__salary"] span').first.inner_text()
        )
        rating_info = self._clean_paragraph(
            self.page.locator('div[class*="rating-wrapper"]').first.inner_text()
        )
        job_info = f"{location} · {posted} · {experience} · {salary} · {rating_info}"

        return 'NI', description, company, job_title, job_info

    def read_linkedin_jobs(self):
        time.sleep(2)
        try:
            # Wait for actual paragraphs to load so we don't just grab "About the job"
            self.page.wait_for_selector('article.jobs-description__container p', timeout=10000)
            description = self.page.locator('article.jobs-description__container').inner_text()
        except Exception as e:
            try:
                description = self.page.locator('h2:has-text("About the job") + div p').first.inner_text(timeout=10)
            except Exception as e:
                try:
                    description = self.page.locator('h2:has-text("About the job") + div + div p').first.inner_text(timeout=10)
                except Exception as e:
                    raise e

        company = self._clean_paragraph(
            self.page.locator('.job-details-jobs-unified-top-card__company-name').inner_text()
        )
        job_title = self._clean_paragraph(
            self.page.locator('.job-details-jobs-unified-top-card__job-title').inner_text()
        )
        job_info = self._clean_paragraph(
            self.page.locator('.job-details-jobs-unified-top-card__tertiary-description-container').inner_text()
        )
        return 'LI', description, company, job_title, job_info
