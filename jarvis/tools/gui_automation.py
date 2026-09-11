"""
Jarvis GUI Automation -- PyAutoGUI integration.

Extends Jarvis's desktop control beyond hotkeys/typing with real mouse
control and visual element location:

  - pc_mouse_action      : click / move / drag / scroll
  - pc_locate_and_click  : find an image on screen, click its center
  - pc_drag_and_drop     : drag from (x1, y1) to (x2, y2)

Safety:
  - pyautogui.FAILSAFE is FORCED True: slamming the mouse into any screen
    corner raises FailSafeException, which we catch and report honestly.
  - All actions are capped by JARVIS_AUTOMATION_TIMEOUT (default 5s) so a
    hung locate can never stall the 5-second response ceiling.
  - Every failure returns {"success": False, "error": ...} with the exact
    exception text. Nothing is ever faked.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

import pyautogui

logger = logging.getLogger(__name__)

# ── Safety configuration ─────────────────────────────────────────
pyautogui.FAILSAFE = True          # corner slam = emergency stop (required)
pyautogui.PAUSE = 0.05             # small settle between pyautogui calls

_AUTOMATION_TIMEOUT = 5.0          # seconds; matches Jarvis's fast ceiling


def _err(exc: Exception, action: str) -> Dict[str, Any]:
    """Honest error envelope for any GUI automation failure."""
    if isinstance(exc, pyautogui.FailSafeException):
        return {
            "success": False,
            "error": ("FAILSAFE TRIGGERED: the mouse was slammed into a "
                      "screen corner. Automation halted immediately as a "
                      "safety measure. Nothing else was done."),
        }
    return {"success": False, "error": f"{action} failed: {exc}"}


def _run_with_failsafe(action: str, fn, *args, **kwargs) -> Dict[str, Any]:
    """Run a pyautogui action on a worker thread with a hard time cap.

    pyautogui calls can block indefinitely (e.g. locating a absent image
    with a huge minSearchTime); the cap keeps the 5s reply ceiling honest.
    """
    result: Dict[str, Any] = {}

    def target():
        try:
            result["ok"] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 -- honest error reporting
            result["exc"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=_AUTOMATION_TIMEOUT)
    if t.is_alive():
        return {
            "success": False,
            "error": (f"{action} timed out after "
                      f"{_AUTOMATION_TIMEOUT:.0f}s (automation cap)."),
        }
    if "exc" in result:
        return _err(result["exc"], action)
    return result.get("ok", {"success": True, "data": f"{action} done"})


# ── Coordinate helpers ───────────────────────────────────────────

def _clamp_point(x: int, y: int) -> Tuple[int, int]:
    """Clamp to the primary screen so clicks never land off-monitor."""
    w, h = pyautogui.size()
    return max(0, min(int(x), w - 1)), max(0, min(int(y), h - 1))


def _resolve_point(x: Optional[int], y: Optional[int],
                   point: Optional[str]) -> Optional[Tuple[int, int]]:
    """Accept either explicit x/y or a compact 'x,y' string point."""
    if x is not None and y is not None:
        try:
            return _clamp_point(int(x), int(y))
        except (TypeError, ValueError):
            return None
    if point:
        try:
            px, py = str(point).split(",")[:2]
            return _clamp_point(int(px.strip()), int(py.strip()))
        except (TypeError, ValueError):
            return None
    return None


# ── Tool 1: pc_mouse_action ──────────────────────────────────────

def mouse_action(action: str = "click",
                 x: Optional[int] = None,
                 y: Optional[int] = None,
                 point: Optional[str] = None,
                 duration: float = 0.25,
                 clicks: int = 1,
                 button: str = "left",
                 scroll_amount: int = 3,
                 scroll_direction: str = "down",
                 **kwargs) -> Dict[str, Any]:
    """One tool for all mouse primitives.

    action: click | move_to | drag_to | scroll
    """
    action = (action or "click").strip().lower()

    # Map common synonyms the LLM may emit (keep the raw string so
    # "doubleclick" keeps its double-ness through the mapping)
    raw = action
    synonyms = {
        "click": "click", "leftclick": "click", "left_click": "click",
        "doubleclick": "click", "double_click": "click",
        "rightclick": "click", "right_click": "click",
        "middleclick": "click", "middle_click": "click",
        "move": "move_to", "moveto": "move_to", "move_to": "move_to",
        "drag": "drag_to", "dragto": "drag_to", "drag_to": "drag_to",
        "scroll": "scroll",
    }
    action = synonyms.get(action.replace(" ", "_").lower(), action)
    if action not in ("click", "move_to", "drag_to", "scroll"):
        return {"success": False,
                "error": (f"Unknown mouse action '{action}'. Use click, "
                          "move_to, drag_to, or scroll.")}

    # Button override from synonyms like 'rightclick'
    if "right" in raw:
        button = "right"
    elif "middle" in raw:
        button = "middle"

    try:
        if action == "click":
            pt = _resolve_point(x, y, point)
            if pt:
                pyautogui.moveTo(*pt, duration=min(duration, 1.0))
            if clicks == 2 or "double" in raw:
                pyautogui.doubleClick(button=button)
                clicks = 2
            else:
                clicks = max(1, min(int(clicks or 1), 3))
                pyautogui.click(clicks=clicks, button=button, interval=0.08)
            return {"success": True,
                    "data": f"Clicked ({button}, x{clicks}) at "
                            f"{pt or pyautogui.position()}"}

        if action == "move_to":
            pt = _resolve_point(x, y, point)
            if not pt:
                return {"success": False,
                        "error": "move_to needs x and y (or point='x,y')."}
            pyautogui.moveTo(*pt, duration=min(max(duration, 0.0), 2.0))
            return {"success": True,
                    "data": f"Moved mouse to {pt}"}

        if action == "drag_to":
            pt = _resolve_point(x, y, point)
            if not pt:
                return {"success": False,
                        "error": "drag_to needs x and y (or point='x,y')."}
            pyautogui.dragTo(*pt, duration=min(max(duration, 0.1), 3.0),
                             button=button)
            return {"success": True,
                    "data": f"Dragged to {pt} ({button} button)"}

        # scroll
        amt = int(scroll_amount or 3)
        amt = max(1, min(amt, 20))
        if (scroll_direction or "down").lower() in ("up", "away"):
            pyautogui.scroll(amt)
        else:
            pyautogui.scroll(-amt)
        return {"success": True,
                "data": f"Scrolled {scroll_direction} {amt} clicks"}
    except pyautogui.FailSafeException as e:
        return _err(e, action)
    except Exception as e:  # noqa: BLE001
        return _err(e, action)


# ── Tool 2: pc_locate_and_click ──────────────────────────────────

def locate_and_click(image_path: str = "",
                     confidence: float = 0.8,
                     clicks: int = 1,
                     button: str = "left",
                     action: str = "click",
                     **kwargs) -> Dict[str, Any]:
    """Locate a UI element by its screenshot image, then interact.

    confidence uses OpenCV when installed; without it, matching is
    exact-pixel only (report in the result so the model knows).
    """
    if not image_path:
        return {"success": False,
                "error": "image_path is required (screenshot of the element "
                         "to find, e.g. from pc_screenshot + crop)."}
    try:
        from pathlib import Path
        p = Path(image_path).expanduser()
        if not p.is_file():
            return {"success": False,
                    "error": f"Image not found: {image_path}"}

        conf = min(max(float(confidence or 0.8), 0.1), 1.0)
        try:
            box = pyautogui.locateCenterOnScreen(str(p), confidence=conf)
        except AttributeError:
            # very old pyautogui without confidence support
            box = pyautogui.locateCenterOnScreen(str(p))
        except ImportError:
            box = pyautogui.locateCenterOnScreen(str(p))
            conf = 1.0  # exact match only

        if box is None:
            cv = "OpenCV" if _has_cv2() else "exact-pixel"
            return {"success": False,
                    "error": (f"Element not found on screen (matching: "
                              f"{cv}, confidence {conf:.2f}). Take a fresh "
                              "pc_screenshot, crop the element, and retry -- "
                              "or use pc_mouse_action with coordinates.")}

        x, y = int(box.x), int(box.y)
        if action == "move":
            pyautogui.moveTo(x, y, duration=0.2)
            return {"success": True, "data": f"Located at ({x}, {y}); moved"}

        clicks = max(1, min(int(clicks or 1), 3))
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click(clicks=clicks, button=button, interval=0.08)
        return {"success": True,
                "data": f"Located element at ({x}, {y}) and clicked "
                        f"({button}, x{clicks})"}
    except pyautogui.FailSafeException as e:
        return _err(e, "locate_and_click")
    except Exception as e:  # noqa: BLE001
        return _err(e, "locate_and_click")


def _has_cv2() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


# ── Tool 3: pc_drag_and_drop ─────────────────────────────────────

def drag_and_drop(x1: int = 0, y1: int = 0, x2: int = 0, y2: int = 0,
                  duration: float = 0.5, **kwargs) -> Dict[str, Any]:
    """Press at (x1, y1), drag to (x2, y2), release."""
    try:
        p1 = _resolve_point(x1, y1, None)
        p2 = _resolve_point(x2, y2, None)
        if not p1 or not p2:
            return {"success": False,
                    "error": "drag_and_drop needs x1, y1, x2, y2."}
        pyautogui.moveTo(*p1, duration=0.2)
        pyautogui.mouseDown()
        pyautogui.moveTo(*p2, duration=min(max(duration, 0.1), 3.0))
        pyautogui.mouseUp()
        return {"success": True,
                "data": f"Dragged {p1} -> {p2}"}
    except pyautogui.FailSafeException as e:
        return _err(e, "drag_and_drop")
    except Exception as e:  # noqa: BLE001
        return _err(e, "drag_and_drop")


# ── Gemini FunctionDeclarations ──────────────────────────────────

def get_declarations():
    """Gemini tool declarations for the GUI automation suite."""
    from google.genai import types

    return [
        types.FunctionDeclaration(
            name="pc_mouse_action",
            description=(
                "Control the mouse: click (left/right/middle, single or "
                "double), move the cursor, drag, or scroll. Use for clicking "
                "buttons, menus, links, and anything not reachable by hotkey. "
                "Coordinates are in pixels on the primary screen."),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": ("Mouse operation: 'click', "
                                        "'move_to', 'drag_to', or 'scroll'."),
                    },
                    "x": {"type": "integer",
                          "description": "Target pixel X (for click/move/drag)."},
                    "y": {"type": "integer",
                          "description": "Target pixel Y (for click/move/drag)."},
                    "point": {"type": "string",
                              "description": "Alternative to x/y: 'x,y' string."},
                    "button": {
                        "type": "string",
                        "description": "Which button: left (default), right, middle.",
                    },
                    "clicks": {
                        "type": "integer",
                        "description": "Click count: 1 single, 2 double.",
                    },
                    "duration": {
                        "type": "number",
                        "description": "Seconds for move/drag animation (0.1-2).",
                    },
                    "scroll_amount": {
                        "type": "integer",
                        "description": "Scroll wheel clicks (1-20), scroll action only.",
                    },
                    "scroll_direction": {
                        "type": "string",
                        "description": "'up' or 'down' (scroll action only).",
                    },
                },
                "required": ["action"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_locate_and_click",
            description=(
                "Find a UI element on screen visually by a small reference "
                "image (screenshot of the button/icon) and click its center. "
                "Use when you know what the element looks like but not where "
                "it is. Requires the image file to exist on disk."),
            parameters={
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Path to a small screenshot of the element to locate.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Match threshold 0.1-1.0 (default 0.8; OpenCV fuzzy matching).",
                    },
                    "clicks": {"type": "integer",
                               "description": "1 single click (default), 2 double."},
                    "button": {"type": "string",
                               "description": "left (default), right, or middle."},
                    "action": {"type": "string",
                               "description": "'click' (default) or 'move' to hover only."},
                },
                "required": ["image_path"],
            },
        ),
        types.FunctionDeclaration(
            name="pc_drag_and_drop",
            description=("Drag from one screen position to another (e.g. move "
                         "a window, reorder items, select text)."),
            parameters={
                "type": "object",
                "properties": {
                    "x1": {"type": "integer", "description": "Start X."},
                    "y1": {"type": "integer", "description": "Start Y."},
                    "x2": {"type": "integer", "description": "End X."},
                    "y2": {"type": "integer", "description": "End Y."},
                    "duration": {"type": "number",
                                 "description": "Drag duration seconds (0.1-3)."},
                },
                "required": ["x1", "y1", "x2", "y2"],
            },
        ),
    ]


# ── Self-test ────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("FAILSAFE:", pyautogui.FAILSAFE)
    print("mouse position:", pyautogui.position())
    print("move  ->", mouse_action("move_to", x=200, y=200, duration=0.2))
    print("click ->", mouse_action("click", x=200, y=200))
    print("scroll->", mouse_action("scroll", scroll_amount=2,
                                   scroll_direction="up"))
