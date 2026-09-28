import json
import re
import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from job.models import Session
from services.parser.parser_constants import (
    FIBONACCI_WAITS,
    LINKEDIN_LOGIN_URL,
    LINKEDIN_USERNAME,
    LINKEDIN_PASSWORD,
    NAUKRI_LOGIN_URL,
    NAUKRI_USERNAME,
    NAUKRI_PASSWORD,
)
from services.parser.linkedin_parser import LinkedInMixin
from services.parser.naukri_parser import NaukriMixin


class Parser(LinkedInMixin, NaukriMixin):

    def __init__(self):
        self.cookies = list(Session.objects.values().filter(id=1))
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

    def _fibonacci_reload(self, check_fn, label):
        """Call check_fn(). If it returns falsy, wait (fibonacci), reload, retry.
        Returns the first truthy result. Raises after all retries exhausted."""
        for attempt in range(len(FIBONACCI_WAITS) + 1):
            result = check_fn()
            if result:
                return result
            if attempt < len(FIBONACCI_WAITS):
                wait = FIBONACCI_WAITS[attempt]
                print(
                    f"⚠ {label}: failed, waiting {wait}s then reloading "
                    f"(attempt {attempt + 1}/{len(FIBONACCI_WAITS)})..."
                )
                time.sleep(wait)
                self.page.reload(wait_until='domcontentloaded', timeout=60000)
            else:
                raise Exception(f"{label}: failed after all retries")

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

    # @shared_task
    def login(self, url, username, password):
        if not username or not password:
            raise ValueError(f"Credentials missing! Please set LINKEDIN_USERNAME and LINKEDIN_PASSWORD environment variables.")
        self.page.goto(url)
        print(url)
        if 'https://www.linkedin.com' in self.page.url:
            try:
                self.page.locator('#username, #session_key, input[type="email"], [autocomplete="username"], [autocomplete="username webauthn"]').locator("visible=true").first.fill(username, timeout=2000)
            except Exception as e:
                print("Username field not found (likely a Welcome Back screen), proceeding to password...")
            self.page.locator('#password, #session_password, input[type="password"], [autocomplete="current-password"]').locator("visible=true").first.fill(password)
            try:
                self.page.locator('label:has-text("Keep me signed in"), label:has-text("Keep me logged in")').locator("visible=true").first.click(timeout=1000)
            except Exception as e:
                pass
            submit_btn = self.page.get_by_role("button", name="Sign in", exact=True).locator("visible=true")
            if submit_btn.count() == 0:
                submit_btn = self.page.locator('button[type="submit"]').locator("visible=true")
            submit_btn.first.click()
            self.page.wait_for_timeout(10000)  # Wait for login to complete and cookies to be set
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
            is_logged_out = 'login' in self.page.url or 'authwall' in self.page.url or 'signup' in self.page.url
            if not is_logged_out:
                try:
                    title = self.page.title()
                    if 'Sign Up' in title or 'Log In' in title:
                        is_logged_out = True
                except:
                    pass
            if not is_logged_out:
                try:
                    is_logged_out = self.page.locator('a[href*="https://www.linkedin.com/login"], a[href*="/login"], a.nav__button-secondary:has-text("Sign in"), form.login__form, .authwall-join-form, .sign-in-modal, a[data-tracking-control-name="public_jobs_nav-header-signin"], form[data-id="sign-in-form"], .contextual-sign-in-modal__sign-in-with-email-cta').count() > 0
                except:
                    pass
            
            if is_logged_out:
                print("LOGIN FAILED, RETRYING")
                cookies = self.login(LINKEDIN_LOGIN_URL, LINKEDIN_USERNAME, LINKEDIN_PASSWORD)
                self.playwright.stop()
                Session.objects.update_or_create(
                    id=1,
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
                    id=1,
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

    def read_job(self, url):
        parsed_url = urlparse(url)
        # self.page.context.add_cookies(json.loads(cookies))
        if parsed_url.netloc == 'www.linkedin.com':
            if parsed_url.path == '/jobs/collections/recommended/' or parsed_url.path == '/jobs/search/':
                self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
                self.page.wait_for_timeout(2000)
                self.check_logged_in(url)
                return self.read_linkedin_jobs()
            elif match := re.search(r'/jobs/view/(\d+)/', parsed_url.path):
                page_url = f"https://www.linkedin.com/jobs/view/?currentJobId={match.group(1)}"
                self.page.goto(page_url, wait_until='domcontentloaded')
                self.page.wait_for_timeout(2000)
                self.check_logged_in(url)
                return self.read_linkedin_jobs()
        if parsed_url.netloc == 'www.naukri.com':
            self.page.goto(url, wait_until='domcontentloaded')
            # We don't check login on every single job read for speed,
            # but if we get blocked we might need to. Letting scan_urls handle initial login for now.
            return self.read_naukri_jobs()

        raise NotImplemented


    def is_expired(self, url):
        parsed_url = urlparse(url)
        if parsed_url.netloc == 'www.linkedin.com':
            # Use clean standalone URL specifically for expiration check to get explicit error pages
            clean_url = url
            if 'currentJobId=' in url:
                match = re.search(r'currentJobId=(\d+)', url)
                if match:
                    clean_url = f"https://www.linkedin.com/jobs/view/{match.group(1)}/"
            self.page.goto(clean_url, wait_until='domcontentloaded')
            self.page.wait_for_timeout(2000)  # Let Playwright event loop process JS redirect to /authwall
            self.check_logged_in(clean_url)
            # breakpoint()
            return self.is_expired_linkedin()

        elif parsed_url.netloc == 'www.naukri.com':
            self.page.goto(url, wait_until='domcontentloaded')
            # breakpoint()
            # time.sleep(1)  # short wait is enough for banner
            return self.is_expired_naukri()

        return False
