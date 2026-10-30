"""
Credentials manager for corporate ATS portals (SuccessFactors, Workday, Taleo).
Provides dynamic password generation, enterprise complexity validation,
and per-domain credential persistence in PortalCredential.
"""
import re
import secrets
import string
import logging
from typing import Tuple, Optional
from urllib.parse import urlparse

from job.models import PortalCredential

logger = logging.getLogger(__name__)


def extract_domain(url: str) -> str:
    """Extract clean domain/host from a URL for credential scoping."""
    if not url:
        return ''
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        # Clean port if present
        host = host.split(':')[0]
        return host
    except Exception:
        return ''


def generate_portal_password(length: int = 16) -> str:
    """
    Generate an enterprise-compliant, high-entropy password.
    Guarantees:
      - >= 2 uppercase letters
      - >= 2 lowercase letters
      - >= 2 digits
      - >= 2 special symbols (!@#$%^&*-_=+)
    Complies with SAP SuccessFactors, Workday, Taleo, and Oracle password rules.
    """
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    symbols = "!@#$%^&*-_=+"

    # Seed with guaranteed character varieties
    password = [
        secrets.choice(uppercase),
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
        secrets.choice(digits),
        secrets.choice(symbols),
        secrets.choice(symbols),
    ]

    all_chars = uppercase + lowercase + digits + symbols
    for _ in range(length - len(password)):
        password.append(secrets.choice(all_chars))

    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def get_portal_credential(domain: str, email: str) -> Optional[str]:
    """Retrieve existing password for a domain and email if one exists."""
    if not domain or not email:
        return None
    cred = PortalCredential.objects.filter(domain=domain, email=email).first()
    if cred:
        cred.save()  # updates last_used timestamp
        return cred.password
    return None


def get_or_create_portal_credential(domain: str, email: str) -> Tuple[str, bool]:
    """
    Retrieve existing password for this portal domain, or generate a new one
    and persist it to PortalCredential. Returns (password, is_new).
    """
    if not domain or not email:
        return generate_portal_password(), True

    cred = PortalCredential.objects.filter(domain=domain, email=email).first()
    if cred:
        cred.save()  # updates last_used timestamp
        return cred.password, False

    new_password = generate_portal_password(length=16)
    PortalCredential.objects.create(
        domain=domain,
        email=email,
        password=new_password
    )
    logger.info(f"[credentials] Generated and saved new portal credential for domain '{domain}' ({email})")
    return new_password, True

