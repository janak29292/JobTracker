"""
Gesture Engine for Autonomous Job Application Interactions.
Simulates human-like motor kinematics:
1. Curved Bezier mouse trajectories with acceleration and micro-jitter.
2. Click physics: approach -> hover pause -> mousedown -> hold -> mouseup -> settling pause.
3. Natural keystroke typing cadence with variable delays and word pauses.
4. Slider drag trajectories with overshoot and pull-back settling for bot-verification sliders.
5. Caches and learns optimal motion profiles in ActionSnippet.
"""
import time
import math
import random
import hashlib
import logging
from typing import Optional, Tuple, Dict, Any
from playwright.sync_api import Page, Locator

from job.models import ActionSnippet

logger = logging.getLogger(__name__)

# State tracking for estimated mouse position per page
_mouse_positions: Dict[int, Tuple[float, float]] = {}


def _get_current_pos(page: Page) -> Tuple[float, float]:
    """Retrieve or initialize estimated mouse position for the given page."""
    page_id = id(page)
    if page_id in _mouse_positions:
        return _mouse_positions[page_id]
    vp = page.viewport_size or {'width': 1280, 'height': 800}
    init_pos = (float(random.randint(150, 350)), float(random.randint(150, 350)))
    _mouse_positions[page_id] = init_pos
    return init_pos


def _set_current_pos(page: Page, pos: Tuple[float, float]):
    _mouse_positions[id(page)] = pos


def _calculate_bezier_points(
    start: Tuple[float, float],
    end: Tuple[float, float],
    steps: int = 25,
    jitter_range: float = 2.0
) -> list[Tuple[float, float]]:
    """
    Generate cubic Bezier curve points connecting start and end.
    Uses randomized perpendicular control points and sinusoidal easing.
    """
    x0, y0 = start
    x3, y3 = end
    dx = x3 - x0
    dy = y3 - y0
    distance = math.hypot(dx, dy)

    if distance < 5:
        return [end]

    # Calculate perpendicular normal vector
    nx = -dy / distance
    ny = dx / distance

    # Control point 1: 25-40% along the path, offset perpendicularly
    deviation_1 = random.uniform(-0.25, 0.25) * distance
    ctrl1_x = x0 + dx * random.uniform(0.2, 0.4) + nx * deviation_1
    ctrl1_y = y0 + dy * random.uniform(0.2, 0.4) + ny * deviation_1

    # Control point 2: 60-80% along the path, offset perpendicularly
    deviation_2 = random.uniform(-0.2, 0.2) * distance
    ctrl2_x = x0 + dx * random.uniform(0.6, 0.8) + nx * deviation_2
    ctrl2_y = y0 + dy * random.uniform(0.6, 0.8) + ny * deviation_2

    points = []
    for i in range(1, steps + 1):
        # Ease-in Ease-out parameter: t goes from 0 to 1 with cosine smoothing
        u = i / float(steps)
        t = 0.5 * (1.0 - math.cos(math.pi * u))

        # Cubic Bezier equation
        cx = (1 - t)**3 * x0 + 3 * (1 - t)**2 * t * ctrl1_x + 3 * (1 - t) * t**2 * ctrl2_x + t**3 * x3
        cy = (1 - t)**3 * y0 + 3 * (1 - t)**2 * t * ctrl1_y + 3 * (1 - t) * t**2 * ctrl2_y + t**3 * y3

        # Add micro-jitter except near the end
        if i < steps - 2:
            cx += random.uniform(-jitter_range, jitter_range)
            cy += random.uniform(-jitter_range, jitter_range)

        points.append((cx, cy))

    return points


def get_or_create_gesture_snippet(platform: str, gesture_type: str, default_action: dict) -> ActionSnippet:
    """Retrieve or create an ActionSnippet representing a behavioral gesture profile."""
    platform_key = (platform or 'default')[:32]
    sig = hashlib.sha256(f"{platform_key}:{gesture_type}".encode('utf-8')).hexdigest()[:32]
    snippet, _ = ActionSnippet.objects.get_or_create(
        signature=sig,
        defaults={
            'snippet_type': gesture_type,
            'platform': platform_key,
            'action': default_action,
            'verify': {'check': 'action_completed'},
            'total_uses': 0,
            'success_count': 0,
            'fail_count': 0,
            'consecutive_fails': 0,
        }
    )
    return snippet


def record_gesture_outcome(snippet: ActionSnippet, success: bool):
    """Update trust metrics on the gesture profile."""
    try:
        snippet.total_uses += 1
        if success:
            snippet.success_count += 1
            snippet.consecutive_fails = 0
        else:
            snippet.fail_count += 1
            snippet.consecutive_fails += 1
        snippet.save(update_fields=['total_uses', 'success_count', 'fail_count', 'consecutive_fails'])
    except Exception as e:
        logger.debug(f"Failed to record gesture outcome: {e}")


def human_mouse_glide(page: Page, target_x: float, target_y: float, steps: int = 22, jitter_range: float = 2.0):
    """Glide mouse cursor smoothly along a curved Bezier trajectory to (target_x, target_y)."""
    start_pos = _get_current_pos(page)
    points = _calculate_bezier_points(start_pos, (target_x, target_y), steps=steps, jitter_range=jitter_range)

    for pt in points:
        page.mouse.move(pt[0], pt[1])
        time.sleep(random.uniform(0.004, 0.012))

    _set_current_pos(page, (target_x, target_y))


def human_click(page: Page, locator: Locator, platform: str = "default") -> bool:
    """
    Perform human-like click:
    1. Scrolls element into view if needed.
    2. Glides mouse in a Bezier trajectory to a random point inside the bounding box.
    3. Hovers momentarily (80-160ms).
    4. Mousedown -> hold (50-100ms) -> mouseup.
    5. Post-click pause (100-200ms).
    """
    snippet = get_or_create_gesture_snippet(
        platform=platform,
        gesture_type='gesture_click',
        default_action={
            'curve': 'bezier',
            'steps': 20,
            'jitter_range': 1.5,
            'hover_pause_ms': 100,
            'hold_duration_ms': 75,
            'post_click_pause_ms': 150
        }
    )
    cfg = snippet.action or {}
    steps = int(cfg.get('steps', 20))
    jitter = float(cfg.get('jitter_range', 1.5))
    hover_pause = float(cfg.get('hover_pause_ms', 100)) / 1000.0
    hold_duration = float(cfg.get('hold_duration_ms', 75)) / 1000.0
    post_pause = float(cfg.get('post_click_pause_ms', 150)) / 1000.0

    try:
        try:
            locator.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass

        box = locator.bounding_box()
        if not box:
            locator.click(timeout=5000)
            record_gesture_outcome(snippet, True)
            return True

        target_x = box['x'] + box['width'] * random.uniform(0.35, 0.65)
        target_y = box['y'] + box['height'] * random.uniform(0.35, 0.65)

        human_mouse_glide(page, target_x, target_y, steps=steps, jitter_range=jitter)
        time.sleep(hover_pause * random.uniform(0.8, 1.2))

        page.mouse.down()
        time.sleep(hold_duration * random.uniform(0.8, 1.2))
        page.mouse.up()

        time.sleep(post_pause * random.uniform(0.8, 1.2))
        record_gesture_outcome(snippet, True)
        return True

    except Exception as e:
        logger.warning(f"human_click failed: {e}")
        record_gesture_outcome(snippet, False)
        try:
            locator.click(timeout=3000)
            return True
        except Exception:
            return False


def human_type(page: Page, locator: Locator, text: str, platform: str = "default") -> bool:
    """
    Perform human-like typing:
    1. Clicks into the element using human click.
    2. Clears previous content if any.
    3. Types character-by-character with variable delay and pauses at spaces/punctuations.
    """
    snippet = get_or_create_gesture_snippet(
        platform=platform,
        gesture_type='human_type',
        default_action={
            'min_delay_ms': 35,
            'max_delay_ms': 85,
            'space_pause_ms': 130
        }
    )
    cfg = snippet.action or {}
    min_delay = float(cfg.get('min_delay_ms', 35)) / 1000.0
    max_delay = float(cfg.get('max_delay_ms', 85)) / 1000.0
    space_pause = float(cfg.get('space_pause_ms', 130)) / 1000.0

    try:
        human_click(page, locator, platform=platform)
        locator.fill('')  # Clear
        time.sleep(0.1)

        for char in text:
            page.keyboard.type(char)
            if char in (' ', ',', '.', '-', '@'):
                time.sleep(random.uniform(space_pause * 0.8, space_pause * 1.3))
            else:
                time.sleep(random.uniform(min_delay, max_delay))

        record_gesture_outcome(snippet, True)
        return True

    except Exception as e:
        logger.warning(f"human_type failed: {e}")
        record_gesture_outcome(snippet, False)
        try:
            locator.fill(text)
            return True
        except Exception:
            return False


def human_slider_drag(page: Page, slider_locator: Locator, drag_distance_px: int, platform: str = "default") -> bool:
    """
    Perform human slider drag:
    1. Approach slider handle with Bezier glide.
    2. Mousedown on handle.
    3. Drag along horizontal axis with acceleration, slight overshoot, and settle back.
    4. Mouseup.
    """
    snippet = get_or_create_gesture_snippet(
        platform=platform,
        gesture_type='slider_drag',
        default_action={
            'curve': 'bezier',
            'steps': 28,
            'overshoot_px': 6,
            'vertical_jitter': 1.0,
            'hold_duration_ms': 100
        }
    )
    cfg = snippet.action or {}
    steps = int(cfg.get('steps', 28))
    overshoot = int(cfg.get('overshoot_px', 6))
    jitter = float(cfg.get('vertical_jitter', 1.0))
    hold_duration = float(cfg.get('hold_duration_ms', 100)) / 1000.0

    try:
        box = slider_locator.bounding_box()
        if not box:
            return False

        start_x = box['x'] + box['width'] / 2.0
        start_y = box['y'] + box['height'] / 2.0

        human_mouse_glide(page, start_x, start_y, steps=15)
        time.sleep(0.08)

        page.mouse.down()
        time.sleep(hold_duration)

        target_x = start_x + drag_distance_px
        overshot_x = target_x + overshoot

        for i in range(1, steps + 1):
            u = i / float(steps)
            t = 1.0 - math.pow(1.0 - u, 3)
            cur_x = start_x + (overshot_x - start_x) * t
            cur_y = start_y + random.uniform(-jitter, jitter)
            page.mouse.move(cur_x, cur_y)
            time.sleep(random.uniform(0.005, 0.012))

        settle_steps = 6
        for j in range(1, settle_steps + 1):
            s = j / float(settle_steps)
            cur_x = overshot_x - (overshoot * s)
            page.mouse.move(cur_x, start_y)
            time.sleep(0.01)

        time.sleep(0.08)
        page.mouse.up()
        _set_current_pos(page, (target_x, start_y))

        record_gesture_outcome(snippet, True)
        return True

    except Exception as e:
        logger.warning(f"human_slider_drag failed: {e}")
        record_gesture_outcome(snippet, False)
        return False

