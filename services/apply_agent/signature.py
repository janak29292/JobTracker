"""
Signature computation for the step cache and page identity.

A signature identifies the *structural identity* of a page step — not its
specific content. Two Greenhouse forms with the same field labels on different
job URLs produce the same signature.

v2: dom_signature is now structural-only (no input values) to avoid
false-positive page-change detection when filling form fields.
"""
import hashlib
import re
from urllib.parse import urlparse

from playwright.sync_api import Page


def strip_url_ids(url: str) -> str:
    """
    Strip numeric IDs and UUIDs from a URL path to produce a platform-level pattern.

    greenhouse.io/job/12345/apply → greenhouse.io/job/{id}/apply
    """
    parsed = urlparse(url)
    path_pattern = re.sub(r'/[0-9a-f-]{8,}', '/{id}', parsed.path)
    path_pattern = re.sub(r'/\d+', '/{id}', path_pattern)
    return f"{parsed.netloc}{path_pattern}"


def compute_structural_signature(url: str, dom_snapshot: str) -> str:
    """
    Compute a STRUCTURAL signature for the current page.

    Hashes URL pattern + DOM skeleton (tag names, classes, IDs, form structure).
    Does NOT include input values or text content of paragraphs — those change
    when we fill fields and would cause false page-change detection.

    Args:
        url: Current page URL
        dom_snapshot: Cleaned visible HTML of the page

    Returns:
        A 64-char hex string (SHA-256 truncated)
    """
    url_pattern = strip_url_ids(url)

    # Strip input values and dynamic text to get structural skeleton
    structural = re.sub(r'value="[^"]*"', 'value=""', dom_snapshot)
    structural = re.sub(r'value=\'[^\']*\'', "value=''", structural)
    # Strip text content within elements (keeps tags/attrs only)
    structural = re.sub(r'>([^<]{100,})<', '><', structural)

    raw = f"{url_pattern}|{structural}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def compute_snippet_signature(
    platform: str, field_label: str, snippet_type: str, page_type: str = ''
) -> str:
    """
    Compute a signature for a single ActionSnippet (one atomic interaction).

    This is per-field, not per-page. Two Workday forms both asking for
    "First Name" share the same locate snippet signature.

    Args:
        platform: Platform identifier ('workday', 'greenhouse', etc.)
        field_label: The human-readable field label ('First Name', 'Email', etc.)
        snippet_type: 'locate' or 'interact'
        page_type: Optional page type context ('application_form', 'job_listing')
    """
    raw = f"{platform.lower()}|{field_label.lower().strip()}|{snippet_type}"
    if page_type:
        raw += f"|{page_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def extract_url_platform(url: str) -> str:
    """
    Classify a URL into a platform identifier.

    Returns one of: 'linkedin', 'naukri', 'greenhouse', 'lever', 'workday',
    or the netloc as fallback for unknown platforms.
    """
    netloc = urlparse(url).netloc.lower()

    PLATFORM_PATTERNS = {
        'linkedin': ['linkedin.com'],
        'naukri': ['naukri.com'],
        'greenhouse': ['greenhouse.io', 'boards.greenhouse.io'],
        'lever': ['lever.co', 'jobs.lever.co'],
        'workday': ['myworkdayjobs.com', 'workday.com'],
        'icims': ['icims.com'],
        'smartrecruiters': ['smartrecruiters.com'],
        'ashbyhq': ['ashbyhq.com'],
        'bamboohr': ['bamboohr.com'],
        'jobvite': ['jobvite.com'],
    }

    for platform, domains in PLATFORM_PATTERNS.items():
        if any(domain in netloc for domain in domains):
            return platform

    return netloc  # unknown — use the domain itself
