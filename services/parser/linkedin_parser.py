import re
import time
from urllib.parse import urlparse

from services.parser.parser_constants import (
    FIBONACCI_WAITS,
    LINKEDIN_FILTERED,
    LINKEDIN_RECOMMENDED,
    POSSIBLE_JOB_IDENTIFIERS,
)


class LinkedInMixin:
    """LinkedIn scanning and scraping methods for Parser."""

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
        total job count and pagination info.
        Returns {'total': int, 'current_page': int, 'total_pages': int}
        or None if not found."""
        try:
            # Check for "No matching jobs found" banner before waiting
            no_results = self.page.locator('.jobs-search-no-results-banner')
            if no_results.count() > 0:
                print("LinkedIn: 'No matching jobs found' banner detected — 0 results")
                return {'total': 0, 'current_page': 1, 'total_pages': 1}

            self.page.wait_for_selector(
                '.jobs-search-results-list__subtitle span',
                timeout=30000
            )
            subtitle = self.page.locator(
                '.jobs-search-results-list__subtitle span'
            ).first.inner_text().strip()
            # e.g. "14 results" or "1,204 results"
            match = re.search(r'([\d,]+)\s*result', subtitle)
            if not match:
                print(f"LinkedIn header found but could not parse count: '{subtitle}'")
                return None
            total = int(match.group(1).replace(',', ''))

            # Parse pagination "Page X of Y"
            current_page = 1
            total_pages = 1
            try:
                pagination = self.page.locator(
                    '.jobs-search-pagination__page-state'
                ).inner_text().strip()
                pg_match = re.search(r'Page\s+(\d+)\s+of\s+(\d+)', pagination)
                if pg_match:
                    current_page = int(pg_match.group(1))
                    total_pages = int(pg_match.group(2))
            except Exception:
                pass  # No pagination element = single page

            print(
                f"LinkedIn header: {total} total jobs, "
                f"page {current_page} of {total_pages}"
            )
            return {
                'total': total,
                'current_page': current_page,
                'total_pages': total_pages
            }
        except Exception as e:
            print(f"⚠ Could not find LinkedIn results header: {e}")
            return None

    def _ensure_linkedin_page(self, page_num):
        """Ensure LinkedIn page header is loaded. Returns header_info dict.
        Raises on failure."""
        header_info = self._fibonacci_reload(
            self._wait_for_job_list_header_linkedin,
            f"LinkedIn page {page_num} header"
        )
        return header_info

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

    def scan_urls_linkedin(self, filtered=False):
        if filtered:
            urls = [
                LINKEDIN_FILTERED + "&geoId=90009626",
                LINKEDIN_FILTERED + "&geoId=104869687&distance=5"
            ]
        else:
            urls = [LINKEDIN_RECOMMENDED]

        job_id_list = []
        for url in urls:
            self._safe_goto(url, wait_until='domcontentloaded')
            time.sleep(2)
            self.check_logged_in(url)

            active_identifier = None
            page_num = 0
            reading = True
            while reading:
                page_num += 1

                header_info = self._ensure_linkedin_page(page_num)

                # No results for this URL — skip to next
                if header_info['total'] == 0:
                    print(f"No results for URL, skipping: {url}")
                    break

                # Page 1: select and lock identifier for all pages
                if page_num == 1:
                    if header_info['total_pages'] == 1:
                        # Single page: use total to pick correct identifier
                        active_identifier = self._find_active_identifier(
                            expected_count=header_info['total']
                        )
                    else:
                        # Multi-page: page size unknown, pick highest count
                        active_identifier = self._find_active_identifier()
                    print(f"Locked identifier: {active_identifier}")

                # Scroll to load all cards
                job_cards = self.page.locator(f'[{active_identifier}]')
                self.page.locator(
                    f'.{self._find_scrollable_parent(job_cards.first)}'
                ).evaluate('''el => {
                    el.scrollTo({
                        top: el.scrollHeight,
                        behavior: 'smooth'
                    });
                }''')
                time.sleep(2)

                # Scrape job IDs
                job_cards = self.page.locator(f'[{active_identifier}]')
                print(f"Page {page_num} count: {job_cards.count()}")
                job_id_page_list = list(filter(
                    lambda x: re.match(r'^\d{10}$', x) is not None,
                    [i.get_attribute(active_identifier) for i in job_cards.all()]
                ))
                page_job_count = len(job_id_page_list)
                print(f"Page {page_num}: {page_job_count} jobs found")

                # If 0 jobs but header says there should be some, retry
                if page_job_count == 0 and header_info['total'] > 0:
                    def check_scrape():
                        self._wait_for_job_list_header_linkedin()
                        cards = self.page.locator(f'[{active_identifier}]')
                        ids = list(filter(
                            lambda x: re.match(r'^\d{10}$', x) is not None,
                            [i.get_attribute(active_identifier)
                             for i in cards.all()]
                        ))
                        if len(ids) > 0:
                            return ids
                        return None

                    job_id_page_list = self._fibonacci_reload(
                        check_scrape,
                        f"LinkedIn page {page_num} scrape"
                    )
                    print(f"Page {page_num} retry: {len(job_id_page_list)} jobs found")

                job_id_list.extend(job_id_page_list)

                try:
                    next_button = self.page.locator(
                        'button[aria-label="View next page"]'
                    )
                    next_button.click()
                    time.sleep(3)
                except Exception:
                    reading = False

            print(f"Finished Reading: {url}")
        print(f"LinkedIn: Found {len(job_id_list)} job URLs")
        return job_id_list

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

    def is_expired_linkedin(self):
        try:
            # Wait for either an h1 (valid job title) or h2 (error page) to appear.
            # This works on standalone pages even with obfuscated CSS classes.
            self.page.wait_for_selector('h1, h2', timeout=10000)
        except Exception as e:
            pass
            
        if 'view' not in self.page.url and 'currentJobId' not in self.page.url:
            return True
            
        try:
            if self.page.locator('#error404, .not-found-404').count() > 0:
                return True
                
            # 1. Structural match for the standalone error page.
            # We look for an h2 followed by a p followed by a link to the jobs homepage.
            # This eliminates any risk of matching honeypot text or accidental job description text.
            error_component = self.page.locator('h2:has-text("Unable to load the page") + p + a[href="https://www.linkedin.com/jobs/"]')
            if error_component.count() > 0:
                return True
                
            # 2. For banners, we ensure the text is inside a specific role/element that is NOT the main article.
            # The job description is always inside an <article> tag or a massive div.
            # We check if the text exists in the DOM, but strictly EXCLUDE anything inside an <article>.
            if self.page.locator(':text("No longer accepting applications"):not(article *)').count() > 0:
                return True
                
            if self.page.locator(':text("Not currently accepting applications"):not(article *)').count() > 0:
                return True
                
            if self.page.locator(':text("This job is closed"):not(article *)').count() > 0:
                return True
        except:
            pass
        return False
