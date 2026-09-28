"""
Signature computation for the step cache.

A signature identifies the *structural identity* of a form step — not its
specific content. Two Greenhouse forms with the same field labels on different
job URLs produce the same signature.
"""
import hashlib
import re
from urllib.parse import urlparse


def strip_url_ids(url: str) -> str:
    """
    Strip numeric IDs and UUIDs from a URL path to produce a platform-level pattern.
    
    greenhouse.io/job/12345/apply → greenhouse.io/job/{id}/apply
    """
    parsed = urlparse(url)
    path_pattern = re.sub(r'/[0-9a-f-]{8,}', '/{id}', parsed.path)
    path_pattern = re.sub(r'/\d+', '/{id}', path_pattern)
    return f"{parsed.netloc}{path_pattern}"


def compute_page_signature(page_url: str, field_labels: list[str]) -> str:
    """
    Compute a structural signature for a form PAGE (all fields combined).
    
    Used by the agent to check "have I seen this exact form layout before?"
    
    Args:
        page_url: The current page URL
        field_labels: List of ALL form field labels visible on the page
    
    Returns:
        A 64-char hex string (SHA-256 truncated)
    """
    url_pattern = strip_url_ids(page_url)
    sorted_labels = sorted(label.lower().strip() for label in field_labels if label.strip())
    raw = f"{url_pattern}|{','.join(sorted_labels)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def compute_snippet_signature(platform: str, field_label: str, snippet_type: str) -> str:
    """
    Compute a signature for a single ActionSnippet (one atomic interaction).
    
    This is per-field, not per-page. Two Workday forms both asking for
    "First Name" share the same locate snippet signature.
    
    Args:
        platform: Platform identifier ('workday', 'greenhouse', etc.)
        field_label: The human-readable field label ('First Name', 'Email', etc.)
        snippet_type: 'locate' or 'interact'
    
    Returns:
        A 64-char hex string (SHA-256 truncated)
    """
    raw = f"{platform.lower()}|{field_label.lower().strip()}|{snippet_type}"
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
