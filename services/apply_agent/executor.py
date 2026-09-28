"""
Snippet executor — safe, fixed interpreter for LLM-generated ActionSnippets.

The LLM generates structured action descriptors (JSON), not arbitrary code.
This module interprets those descriptors using a fixed vocabulary of
Playwright operations and verification checks.

Vocabulary:
    Locate strategies: css, role, text, label, xpath, id
    Interact actions:  fill, click, select, upload, check, uncheck
    Verify checks:     element_exists, value_matches, is_checked,
                       page_changed, element_visible, option_selected

The executor NEVER calls exec() or eval(). Every operation maps to a
hardcoded Playwright method call.
"""
import logging
from typing import Optional

from playwright.sync_api import Page, Locator

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# LOCATE — find an element on the page
# ═══════════════════════════════════════════════════════════════════

LOCATE_STRATEGIES = {
    'css', 'role', 'text', 'label', 'xpath', 'id', 'placeholder',
}


def execute_locate(page: Page, action: dict) -> Optional[Locator]:
    """
    Find an element on the page using the strategy from a locate snippet.
    
    Tries the primary selector, then falls back to alternatives.
    Returns the Playwright Locator if found, None if all fail.
    
    Args:
        page: Playwright Page object
        action: The snippet's action dict, e.g.:
            {
                "strategy": "css",
                "selector": "input[name='firstName']",
                "fallbacks": ["#first-name", "[aria-label='First Name']"]
            }
    """
    strategy = action.get('strategy', 'css')
    selector = action.get('selector', '')
    fallbacks = action.get('fallbacks', [])

    if strategy not in LOCATE_STRATEGIES:
        logger.warning(f"Unknown locate strategy: {strategy}")
        return None

    # Try primary selector
    locator = _resolve_locator(page, strategy, selector)
    if locator and _is_visible(locator):
        return locator

    # Try fallbacks
    for fallback in fallbacks:
        locator = _resolve_locator(page, strategy, fallback)
        if locator and _is_visible(locator):
            logger.info(f"Primary selector failed, fallback matched: {fallback}")
            return locator

    return None


def _resolve_locator(page: Page, strategy: str, selector: str) -> Optional[Locator]:
    """Map a strategy+selector to a Playwright Locator."""
    try:
        if strategy == 'css':
            return page.locator(selector).first
        elif strategy == 'id':
            return page.locator(f'#{selector}').first
        elif strategy == 'xpath':
            return page.locator(f'xpath={selector}').first
        elif strategy == 'role':
            # selector format: "textbox" or "button:Submit" (role:name)
            parts = selector.split(':', 1)
            role = parts[0]
            name = parts[1] if len(parts) > 1 else None
            if name:
                return page.get_by_role(role, name=name).first
            return page.get_by_role(role).first
        elif strategy == 'text':
            return page.get_by_text(selector).first
        elif strategy == 'label':
            return page.get_by_label(selector).first
        elif strategy == 'placeholder':
            return page.get_by_placeholder(selector).first
    except Exception as e:
        logger.debug(f"Locator failed ({strategy}={selector}): {e}")
    return None


def _is_visible(locator: Locator) -> bool:
    """Check if a locator resolves to a visible element."""
    try:
        return locator.is_visible(timeout=2000)
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
# INTERACT — perform an action on a found element
# ═══════════════════════════════════════════════════════════════════

INTERACT_ACTIONS = {
    'fill', 'click', 'select', 'upload', 'check', 'uncheck',
}


def execute_interact(locator: Locator, action: dict, value: str = '') -> bool:
    """
    Perform a browser interaction on a located element.
    
    Args:
        locator: Playwright Locator for the target element
        action: The snippet's action dict, e.g.:
            {
                "action": "fill",
                "value_source": "profile.first_name",
                "input_type": "text"
            }
        value: The resolved value to use (already looked up from profile/Q&A bank)
    
    Returns:
        True if the action executed without error, False otherwise.
    """
    action_type = action.get('action', '')
    
    if action_type not in INTERACT_ACTIONS:
        logger.warning(f"Unknown interact action: {action_type}")
        return False

    try:
        if action_type == 'fill':
            # Clear existing value first, then fill
            locator.clear()
            locator.fill(str(value))
            return True
            
        elif action_type == 'click':
            locator.click()
            return True
            
        elif action_type == 'select':
            # value should be the option value or label
            locator.select_option(value)
            return True
            
        elif action_type == 'upload':
            # value should be the file path
            locator.set_input_files(str(value))
            return True
            
        elif action_type == 'check':
            locator.check()
            return True
            
        elif action_type == 'uncheck':
            locator.uncheck()
            return True

    except Exception as e:
        logger.error(f"Interact '{action_type}' failed: {e}")
        return False

    return False


# ═══════════════════════════════════════════════════════════════════
# VERIFY — check that the action worked
# ═══════════════════════════════════════════════════════════════════

VERIFY_CHECKS = {
    'element_exists', 'value_matches', 'is_checked',
    'page_changed', 'element_visible', 'option_selected',
}


def execute_verify(
    page: Page, 
    locator: Optional[Locator], 
    verify: dict, 
    expected_value: str = '',
    pre_action_url: str = ''
) -> bool:
    """
    Run the verification check from a snippet.
    
    Args:
        page: Playwright Page object
        locator: The element that was acted on (may be None for page-level checks)
        verify: The snippet's verify dict, e.g.:
            {
                "check": "value_matches",
                "compare": "input_value",
                "expected_tag": "input",
                "expected_attrs": {"type": "text"}
            }
        expected_value: The value that should have been set
        pre_action_url: The URL before the action (for page_changed check)
    
    Returns:
        True if verification passed, False otherwise.
    """
    check = verify.get('check', '')
    
    if check not in VERIFY_CHECKS:
        logger.warning(f"Unknown verify check: {check}")
        return False

    try:
        if check == 'element_exists':
            if locator is None:
                return False
            return locator.count() > 0

        elif check == 'element_visible':
            if locator is None:
                return False
            return locator.is_visible(timeout=2000)

        elif check == 'value_matches':
            if locator is None:
                return False
            compare = verify.get('compare', 'input_value')
            if compare == 'input_value':
                actual = locator.input_value(timeout=2000)
                return actual == str(expected_value)
            elif compare == 'inner_text':
                actual = locator.inner_text(timeout=2000)
                return str(expected_value) in actual
            return False

        elif check == 'is_checked':
            if locator is None:
                return False
            return locator.is_checked(timeout=2000)

        elif check == 'option_selected':
            if locator is None:
                return False
            # Check that the select element's value matches
            actual = locator.input_value(timeout=2000)
            return actual == str(expected_value)

        elif check == 'page_changed':
            # Verify the page URL changed after an action (e.g., clicking "Next")
            current_url = page.url
            return current_url != pre_action_url

        # Also verify expected tag/attrs if specified
        if locator and verify.get('expected_tag'):
            tag = locator.evaluate('el => el.tagName.toLowerCase()')
            if tag != verify['expected_tag']:
                logger.warning(f"Expected tag '{verify['expected_tag']}', got '{tag}'")
                return False

        if locator and verify.get('expected_attrs'):
            for attr, expected in verify['expected_attrs'].items():
                actual = locator.get_attribute(attr)
                if actual != expected:
                    logger.warning(f"Expected {attr}='{expected}', got '{actual}'")
                    return False

    except Exception as e:
        logger.error(f"Verify '{check}' failed: {e}")
        return False

    return True


# ═══════════════════════════════════════════════════════════════════
# ORCHESTRATOR — run a full locate→interact→verify cycle
# ═══════════════════════════════════════════════════════════════════

def run_snippet_pair(
    page: Page,
    locate_snippet: dict,
    interact_snippet: dict,
    value: str = ''
) -> dict:
    """
    Execute a complete locate→interact→verify cycle.
    
    Args:
        page: Playwright Page object
        locate_snippet: ActionSnippet data with snippet_type='locate'
        interact_snippet: ActionSnippet data with snippet_type='interact'
        value: The resolved value to fill/select
    
    Returns:
        {
            "success": bool,
            "locate_ok": bool,
            "interact_ok": bool,
            "verify_ok": bool,
            "error": str or None
        }
    """
    result = {
        'success': False,
        'locate_ok': False,
        'interact_ok': False,
        'verify_ok': False,
        'error': None,
    }
    pre_action_url = page.url

    # 1. Locate
    locator = execute_locate(page, locate_snippet['action'])
    if locator is None:
        result['error'] = 'Element not found'
        return result
    
    # Verify the locate
    locate_verified = execute_verify(
        page, locator, locate_snippet['verify']
    )
    if not locate_verified:
        result['error'] = 'Locate verification failed'
        return result
    result['locate_ok'] = True

    # 2. Interact
    interact_ok = execute_interact(locator, interact_snippet['action'], value)
    if not interact_ok:
        result['error'] = 'Interaction failed'
        return result
    result['interact_ok'] = True

    # 3. Verify the interact
    # Brief pause to let framework-driven forms update their state
    page.wait_for_timeout(500)
    
    verify_ok = execute_verify(
        page, locator, interact_snippet['verify'],
        expected_value=value,
        pre_action_url=pre_action_url
    )
    if not verify_ok:
        result['error'] = 'Interact verification failed'
        return result
    result['verify_ok'] = True

    result['success'] = True
    return result
