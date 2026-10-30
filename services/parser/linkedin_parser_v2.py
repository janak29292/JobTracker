import re
import time
from urllib.parse import urlparse

from services.parser.parser_constants import (
    FIBONACCI_WAITS,
    LINKEDIN_FILTERED_2,
    LINKEDIN_RECOMMENDED,
    POSSIBLE_JOB_IDENTIFIERS,
)
from services.parser.linkedin_parser import LinkedInMixin


class LinkedInMixinV2(LinkedInMixin):
    """v2 LinkedIn scanning and scraping methods for Parser.
    Inherits from LinkedInMixin so non-scanner methods (e.g. read_linkedin_jobs,
    is_expired_linkedin) remain available from v1, while providing an adaptive
    scanner for both the new LinkedIn Flagship layout and the Classic layout."""

    def _extract_job_id(self, raw_value):
        """Extract canonical 10-digit numeric job ID from an attribute value."""
        if not raw_value or not isinstance(raw_value, str):
            return None
        raw_value = raw_value.strip()
        if re.match(r'^\d{10}$', raw_value):
            return raw_value
        if match := re.search(r'job-card-component-ref-(\d{10})', raw_value):
            return match.group(1)
        if match := re.search(r'urn:li:(?:fsd_)?jobPosting:(\d{10})', raw_value):
            return match.group(1)
        if match := re.search(r'currentJobId=(\d{10})', raw_value):
            return match.group(1)
        return None

    def _find_active_identifier(self, expected_count=None):
        """Find which job identifier LinkedIn is currently using.
        Supports both classic attributes ('data-occludable-job-id', etc.)
        and the new Flagship attribute ('componentkey')."""
        identifier_counts = {}
        best_identifier = None
        best_count = 0

        # Check candidate identifiers (new Flagship componentkey first, then classic attributes)
        candidate_identifiers = [
            'componentkey',
            *POSSIBLE_JOB_IDENTIFIERS
        ]

        for identifier in candidate_identifiers:
            if identifier == 'componentkey':
                # Target unique card wrappers to prevent double-counting inner elements
                elements = self.page.locator('div[role="button"][componentkey^="job-card-component-ref-"]')
            else:
                elements = self.page.locator(f'[{identifier}]')

            count = sum(
                1 for el in elements.all()
                if self._extract_job_id(el.get_attribute(identifier)) is not None
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
        """Wait for the LinkedIn search results header and extract expected total
        job count and pagination info. Supports both Flagship and Classic layouts.
        Returns {'total': int, 'current_page': int, 'total_pages': int} or None."""
        try:
            # Check for "No matching jobs found" banner before waiting
            no_results = self.page.locator('.jobs-search-no-results-banner, [componentkey="noResultsBanner"]')
            if no_results.count() > 0:
                print("LinkedIn: 'No matching jobs found' banner detected — 0 results")
                return {'total': 0, 'current_page': 1, 'total_pages': 1}

            # Try locating the header element (Flagship or Classic)
            header_selector = (
                '[componentkey="searchResultsHeaderComponent"] p, '
                '.jobs-search-results-list__subtitle span'
            )
            self.page.wait_for_selector(header_selector, timeout=30000)

            # Extract header text
            subtitle = ""
            for el in self.page.locator(header_selector).all():
                text = el.inner_text().strip()
                if re.search(r'[\d,]+\+?\s*result', text, re.IGNORECASE):
                    subtitle = text
                    break

            if not subtitle:
                # Fallback to first matching element's text
                subtitle = self.page.locator(header_selector).first.inner_text().strip()

            # e.g. "99+ results", "14 results", or "1,204 results"
            match = re.search(r'([\d,]+)\+?\s*result', subtitle, re.IGNORECASE)
            if not match:
                print(f"LinkedIn header found but could not parse count: '{subtitle}'")
                return None
            total = int(match.group(1).replace(',', ''))

            # Parse pagination
            current_page = 1
            total_pages = 1
            try:
                # 1. New Flagship pagination: [data-testid="pagination-controls-list"]
                nav_list = self.page.locator('[data-testid="pagination-controls-list"]')
                if nav_list.count() > 0:
                    active_page_btn = nav_list.locator('button[aria-current="true"]')
                    if active_page_btn.count() > 0:
                        current_page = int(active_page_btn.first.inner_text().strip())

                    # Check max page indicator available
                    page_indicators = nav_list.locator('button[data-testid^="pagination-indicator-"]').all()
                    if page_indicators:
                        nums = [
                            int(ind.inner_text().strip()) for ind in page_indicators
                            if ind.inner_text().strip().isdigit()
                        ]
                        if nums:
                            total_pages = max(nums)

                    # If next button is visible, total_pages is at least current_page + 1
                    next_btn = self.page.locator('button[data-testid="pagination-controls-next-button-visible"]')
                    if next_btn.count() > 0 and total_pages <= current_page:
                        total_pages = current_page + 1
                else:
                    # 2. Classic pagination: "Page X of Y"
                    pagination = self.page.locator('.jobs-search-pagination__page-state')
                    if pagination.count() > 0:
                        pg_text = pagination.first.inner_text().strip()
                        pg_match = re.search(r'Page\s+(\d+)\s+of\s+(\d+)', pg_text)
                        if pg_match:
                            current_page = int(pg_match.group(1))
                            total_pages = int(pg_match.group(2))
            except Exception:
                pass  # Single page or non-fatal pagination read issue

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
            current_url = getattr(self.page, 'url', 'unknown')
            print(f"⚠ Could not find LinkedIn results header on {current_url}: {e}")
            return None

    def _ensure_linkedin_page(self, page_num):
        """Ensure LinkedIn page header is loaded. Returns header_info dict.
        Raises on failure."""
        header_info = self._fibonacci_reload(
            self._wait_for_job_list_header_linkedin,
            f"LinkedIn page {page_num} header"
        )
        return header_info

    def _scroll_job_list(self, active_identifier):
        """Scroll the job list container to trigger lazy loading of job cards."""
        # 1. New Flagship layout: direct scroll on lazy-column container
        lazy_col = self.page.locator('[data-testid="lazy-column"], [componentkey="SearchResultsMainContent"]')
        if lazy_col.count() > 0:
            lazy_col.first.evaluate('''el => {
                el.scrollTo({
                    top: el.scrollHeight,
                    behavior: 'smooth'
                });
            }''')
            time.sleep(2)
            return

        # 2. Classic layout: dynamic scrollable parent
        if active_identifier == 'componentkey':
            job_cards = self.page.locator('div[role="button"][componentkey^="job-card-component-ref-"]')
        else:
            job_cards = self.page.locator(f'[{active_identifier}]')

        if job_cards.count() > 0:
            scrollable_parent = self._find_scrollable_parent(job_cards.first)
            if scrollable_parent:
                self.page.locator(f'.{scrollable_parent}').evaluate('''el => {
                    el.scrollTo({
                        top: el.scrollHeight,
                        behavior: 'smooth'
                    });
                }''')
                time.sleep(2)

    def _get_job_cards_locator(self, active_identifier):
        """Return the locator for job cards based on the active identifier."""
        if active_identifier == 'componentkey':
            return self.page.locator('div[role="button"][componentkey^="job-card-component-ref-"]')
        return self.page.locator(f'[{active_identifier}]')

    def scan_urls_linkedin(self, filtered=False):
        if filtered:
            urls = [
                LINKEDIN_FILTERED_2 + "&geoId=90009626",
                LINKEDIN_FILTERED_2 + "&geoId=104869687&distance=5"
            ]
        else:
            urls = [LINKEDIN_RECOMMENDED]

        job_id_list = []
        for url in urls:
            print(f"Scanning URL: {url}")
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
                self._scroll_job_list(active_identifier)

                # Scrape job IDs
                job_cards = self._get_job_cards_locator(active_identifier)
                card_count = job_cards.count()
                print(f"Page {page_num} count: {card_count}")

                raw_attributes = [i.get_attribute(active_identifier) for i in job_cards.all()]
                extracted_ids = [self._extract_job_id(attr) for attr in raw_attributes]
                # Filter out None and deduplicate while preserving order
                job_id_page_list = list(dict.fromkeys([jid for jid in extracted_ids if jid is not None]))
                page_job_count = len(job_id_page_list)
                print(f"Page {page_num}: {page_job_count} jobs found")

                # If 0 jobs but header says there should be some, retry
                if page_job_count == 0 and header_info['total'] > 0:
                    def check_scrape():
                        self._wait_for_job_list_header_linkedin()
                        cards = self._get_job_cards_locator(active_identifier)
                        attrs = [c.get_attribute(active_identifier) for c in cards.all()]
                        ids = list(dict.fromkeys([
                            self._extract_job_id(a) for a in attrs
                            if self._extract_job_id(a) is not None
                        ]))
                        if len(ids) > 0:
                            return ids
                        return None

                    job_id_page_list = self._fibonacci_reload(
                        check_scrape,
                        f"LinkedIn page {page_num} scrape"
                    )
                    print(f"Page {page_num} retry: {len(job_id_page_list)} jobs found")

                job_id_list.extend(job_id_page_list)

                # Pagination: try Flagship next button, then Classic next button
                try:
                    next_button = self.page.locator(
                        'button[data-testid="pagination-controls-next-button-visible"], '
                        'button[aria-label="View next page"]'
                    )
                    if next_button.count() > 0 and next_button.first.is_visible():
                        next_button.first.click()
                        time.sleep(3)
                    else:
                        reading = False
                except Exception:
                    reading = False

            print(f"Finished Reading: {url}")
        print(f"LinkedIn: Found {len(job_id_list)} job URLs")
        return job_id_list

    def read_linkedin_jobs(self):
        """Extract job details (description, company, title, info) from a LinkedIn job page.
        Supports both the new Flagship standalone layout, the search two-pane view,
        and legacy layouts."""
        time.sleep(2)
        try:
            # Multi-condition wait for dynamic content hydration or early exit indicators
            self.page.wait_for_selector(
                '[data-testid="expandable-text-box"], '
                'div[componentkey^="JobDetails_AboutTheJob_"], '
                'div[id^="JobDetails_AboutTheJob_"], '
                'article.jobs-description__container, '
                '#error404, '
                ':text("No longer accepting applications")',
                timeout=15000
            )
        except Exception:
            pass

        # # 1. Pre-check for closed/expired/404 postings
        # if self.is_expired_linkedin():
        #     return None

        # 2. Expand truncated description if 'more' button is present
        try:
            more_btn = self.page.locator(
                'button[data-testid="expandable-text-button"], '
                '.jobs-description__footer-button, '
                'button:has-text("more")'
            )
            if more_btn.count() > 0:
                more_btn.first.click(timeout=2000, force=True)
                self.page.wait_for_timeout(400)
        except Exception:
            pass

        # 3. Extract description
        description = None
        desc_locators = [
            self.page.locator('[data-testid="expandable-text-box"]'),
            self.page.locator('div[componentkey^="JobDetails_AboutTheJob_"]'),
            self.page.locator('div[id^="JobDetails_AboutTheJob_"]'),
            self.page.locator('article.jobs-description__container'),
            self.page.locator('h2:has-text("About the job") + div'),
        ]
        for loc in desc_locators:
            if loc.count() > 0:
                try:
                    text = loc.first.inner_text()
                    if text and len(text.strip()) > 30:
                        description = text.strip()
                        break
                except Exception:
                    continue

        if not description:
            try:
                description = self.page.locator('h2:has-text("About the job") + div p').first.inner_text(timeout=2000)
            except Exception:
                try:
                    description = self.page.locator('h2:has-text("About the job") + div + div p').first.inner_text(timeout=2000)
                except Exception:
                    pass

        if not description:
            raise ValueError("Could not extract LinkedIn job description")

        # Clean trailing '… more' if left over from expansion button
        description = re.sub(r'(?:\s*…\s*more|\s*\.\.\.\s*more)$', '', description.strip())

        # 4. Extract company name
        company = None
        for loc in [
            self.page.locator('p a[href*="/company/"]').first,
            self.page.locator('a[href*="/company/"]').first,
            self.page.locator('.job-details-jobs-unified-top-card__company-name').first,
        ]:
            if loc.count() > 0:
                try:
                    text = loc.inner_text().strip()
                    if text and not text.lower().startswith('see all') and not text.lower().startswith('follow'):
                        company = self._clean_paragraph(text)
                        break
                except Exception:
                    pass

        if not company:
            # Fallback: attribute extraction (safe on SVGs) or page title
            try:
                aria = self.page.locator('[aria-label*="Company"]').first
                if aria.count() > 0:
                    val = aria.get_attribute('aria-label') or ""
                    m = re.search(r'Company(?:\s+logo\s+for)?,\s*([^.\n]+)', val, re.IGNORECASE)
                    if m:
                        company = self._clean_paragraph(m.group(1))
            except Exception:
                pass

        if not company:
            title_parts = self.page.title().split(' | ')
            company = self._clean_paragraph(title_parts[1].strip()) if len(title_parts) >= 2 else "Unknown"

        # 5. Extract job title
        title_loc = self.page.locator('.job-details-jobs-unified-top-card__job-title')
        if title_loc.count() > 0:
            job_title = self._clean_paragraph(title_loc.first.inner_text())
        else:
            job_title = self._clean_paragraph(self.page.title().split(' | ')[0].strip())

        # 6. Extract job info (location, date, badges)
        job_info = None
        if self.page.locator('.job-details-jobs-unified-top-card__tertiary-description-container').count() > 0:
            try:
                job_info = self._clean_paragraph(
                    self.page.locator('.job-details-jobs-unified-top-card__tertiary-description-container').first.inner_text()
                )
            except Exception:
                pass

        if not job_info:
            meta_loc = self.page.locator('p:has-text("·")')
            if meta_loc.count() > 0:
                try:
                    job_info = self._clean_paragraph(meta_loc.first.inner_text())
                except Exception:
                    pass

        return 'LI', description, company, job_title, job_info

    def is_expired_linkedin(self):
        """Check whether the loaded LinkedIn job posting is expired, closed, or a 404."""
        try:
            self.page.wait_for_selector(
                '#error404, .not-found-404, [data-testid="expandable-text-box"], '
                'article.jobs-description__container, [componentkey^="JobDetails_AboutTheJob_"], '
                'h1, h2',
                timeout=10000
            )
        except Exception:
            pass

        if 'view' not in self.page.url and 'currentJobId' not in self.page.url:
            return True

        try:
            # 404 / Not found elements
            if self.page.locator('#error404, .not-found-404').count() > 0:
                return True

            # Standalone error page structural match: h2 + p + link to jobs homepage
            error_component = self.page.locator('h2:has-text("Unable to load the page") + p + a[href="https://www.linkedin.com/jobs/"]')
            if error_component.count() > 0:
                return True

            # Check closed job notices outside the job description container
            closed_selectors = [
                ':text("No longer accepting applications"):not([data-testid="expandable-text-box"] *):not(article *)',
                ':text("Not currently accepting applications"):not([data-testid="expandable-text-box"] *):not(article *)',
                ':text("This job is closed"):not([data-testid="expandable-text-box"] *):not(article *)',
            ]
            for sel in closed_selectors:
                if self.page.locator(sel).count() > 0:
                    return True

            # Check closed job indicator in manage job banner container
            if self.page.locator('div[id^="JobDetails_ManageJobBanner_"]:has-text("closed"), div[id^="JobDetails_ManageJobBanner_"]:has-text("No longer accepting")').count() > 0:
                return True
        except Exception:
            pass
        return False

