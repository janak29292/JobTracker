import re
import time
from urllib.parse import urlparse

from services.parser.parser_constants import FIBONACCI_WAITS, NAUKRI_FILTERED_2


class NaukriMixin:
    """Naukri scanning and scraping methods for Parser."""

    def _wait_for_job_list_header_naukri(self):
        """Wait for the Naukri job list header and extract per-page expected
        count and total. Parses '1 - 20 of 57' → {page_expected: 20, total: 57}.
        Returns None if not found."""
        try:
            # Check for "No results found" page before waiting
            no_results = self.page.locator('div[class*="no-result-container"]')
            if no_results.count() > 0:
                print("Naukri: 'No results found' page detected — 0 results")
                return {'page_expected': 0, 'total': 0}

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

    def _ensure_naukri_page(self, page_num):
        """Ensure Naukri page has header loaded and card count matches expected.
        Returns (header_info, job_cards_locator). Raises on failure."""
        # Phase 1: Get header
        header_info = self._fibonacci_reload(
            self._wait_for_job_list_header_naukri,
            f"Naukri page {page_num} header"
        )
        page_expected = header_info['page_expected']

        # Phase 2: Wait for cards + validate count
        def check_cards():
            try:
                self.page.wait_for_selector('[data-job-id]', timeout=10000)
            except Exception:
                pass
            cards = self.page.locator('[data-job-id]')
            count = cards.count()
            print(f"Page {page_num}: {count} cards (expected {page_expected})")
            if count == page_expected:
                return cards
            return None

        job_cards = self._fibonacci_reload(
            check_cards,
            f"Naukri page {page_num} cards"
        )

        print(f"✓ Naukri page {page_num}: {page_expected} cards confirmed")
        return header_info, job_cards

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

            header_info, job_cards = self._ensure_naukri_page(page_num)

            # No results for this URL — stop pagination
            if header_info['total'] == 0:
                print(f"No results found on Naukri, skipping: {url}")
                break

            job_id_page_list = [
                card.locator('a.title').get_attribute('href')
                for card in job_cards.all()
            ]
            job_url_list.extend(job_id_page_list)

            try:
                # Next page href follows pattern: /search-path-N (e.g., /python-django-senior-jobs-2)
                url_path = urlparse(url).path.rstrip('/')
                next_button = self.page.locator(
                    f'a[href^="{url_path}-"]:has(span:text("Next"))'
                )
                next_button.click()
                time.sleep(3)
            except Exception:
                reading = False

        print(f"Naukri: Found {len(job_url_list)} job URLs")
        print(f"Finished Reading: {url}")
        return job_url_list

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

        desc_locator = self.page.locator('section[class*="job-desc-container"]')
        read_more = self.page.locator('a[class*="read-more-link"]')
        
        if read_more.count() > 0:
            try:
                read_more.first.click(timeout=1000)
                read_more.first.wait_for(state="hidden", timeout=2000)
            except:
                pass
                
        description = desc_locator.inner_text()

        # Fix Key Skills concatenation — extract chips individually
        try:
            key_skill_div = self.page.locator('div[class*="key-skill"]')
            if key_skill_div.count() > 0:
                bad_blob = key_skill_div.first.inner_text()
                
                # Evaluate in browser to avoid IPC overhead
                skills_data = key_skill_div.first.evaluate('''el => {
                    const chips = Array.from(el.querySelectorAll('a[class*="chip"]'));
                    const preferred = [];
                    const required = [];
                    for (const chip of chips) {
                        const nameSpan = chip.querySelector('span');
                        const name = nameSpan ? nameSpan.innerText.trim() : '';
                        if (!name) continue;
                        if (chip.querySelector('i[class*="jd-save"]')) {
                            preferred.push(name);
                        } else {
                            required.push(name);
                        }
                    }
                    return { preferred, required };
                }''')
                
                preferred = skills_data.get('preferred', [])
                required = skills_data.get('required', [])
                
                if preferred or required:
                    skills_text = "\nKey Skills"
                    if preferred:
                        skills_text += f"\nPreferred: {', '.join(preferred)}"
                    if required:
                        skills_text += f"\nOther: {', '.join(required)}"
                    
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
        
        try:
            rating_wrapper = self.page.locator('div[class*="rating-wrapper"]').first
            if rating_wrapper.count() > 0:
                rating_span = rating_wrapper.locator('span[class*="rating"]')
                reviews_span = rating_wrapper.locator('span[class*="reviews"]')
                
                rating_val = rating_span.inner_text().strip() if rating_span.count() > 0 else ""
                reviews_val = reviews_span.inner_text().strip() if reviews_span.count() > 0 else ""
                
                if rating_val and reviews_val:
                    rating_info = f"{rating_val} ({reviews_val})"
                else:
                    rating_info = rating_val or reviews_val
            else:
                rating_info = ""
        except:
            rating_info = ""
            
        job_info = f"{location} · {posted} · {experience} · {salary} · {rating_info}"

        return 'NI', description, company, job_title, job_info

    def is_expired_naukri(self):
        # Wait for either job title or expired alert
        try:
            self.page.wait_for_selector(
                'h1[class*="jd-header-title"], div[class*="exp-alert-message"]',
                timeout=10000
            )
        except:
            pass
        try:
            expired = self.page.locator('div[class*="exp-alert-message"]')
            if expired.count() > 0 and 'expired' in expired.inner_text().lower():
                return True
        except:
            pass
        return False
