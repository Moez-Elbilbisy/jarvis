"""
Jarvis Browser Automation -- Selenium dynamic web interaction.

For pages that need JavaScript rendering or multi-step navigation that
static scraping can't handle:

  web_browser_interact:  navigate | click_element | input_text |
                         wait_for_selector | get_rendered_html |
                         take_element_screenshot

Lifecycle management (the part everyone gets wrong):
  - BrowserSessionManager: ONE lazy Chrome session, created on first use
  - atexit + signal hooks: chromedriver and chrome are killed even if
    Jarvis crashes or is force-closed -- no orphan processes
  - explicit WebDriverWait everywhere (default 3s, capped 15s) to honor
    the fast-response ethos
  - headless by default (visible=False), anti-automation flags on

All results are honest: a failed wait/missing element/timeout returns
{"success": False, "error": ...} with the exact reason.
"""

import atexit
import logging
import os
import signal
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    """Raised when the browser session can't be provided in time."""

# ── Configuration ────────────────────────────────────────────────

_DEFAULT_WAIT = 3.0            # explicit-wait default (fast ethos)
_MAX_WAIT = 15.0               # never block longer than this
_MAX_HTML_CHARS = 200_000      # rendered-HTML size cap (LLM-friendly)
_PAGELOAD_TIMEOUT = 12.0       # driver.set_page_load_timeout
# First driver creation can stall for MINUTES when Selenium Manager has
# to download chromedriver (or the network blocks it entirely). Bound it
# so a tool call never hangs; a slow download finishes in the background
# and is either kept for the next call or quit -- never orphaned.
_CREATE_BOUND = float(os.getenv("JARVIS_BROWSER_START_TIMEOUT", "20"))


# ── Session manager ──────────────────────────────────────────────

class BrowserSessionManager:
    """Owns one lazy Selenium Chrome session and always tears it down.

    - get_driver() creates Chrome on first use (headless by default)
    - shutdown() quits the driver; safe to call many times
    - registered with atexit so nothing survives Jarvis exiting
    """

    _instance: Optional["BrowserSessionManager"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "BrowserSessionManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._driver = None
        self._lock = threading.Lock()
        # Set while a creation attempt is in flight so concurrent tool
        # calls don't spawn a second Chrome.
        self._creating = threading.Event()
        # When True, a late-finishing creator quits its driver instead of
        # storing it (prevents orphans after shutdown/exit).
        self._shutdown_done = False
        # Creation threads that outlived their bound: when they eventually
        # finish, their driver must be discarded (quit), not leaked.
        self._timed_out_creators = set()
        atexit.register(self.shutdown)
        # In frozen windowed builds Python may not see SIGINT/SIGTERM
        # handlers, so wrap in try/except and keep atexit as the net.
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                prev = signal.getsignal(sig)

                def _handler(signum, frame, _prev=prev):
                    self.shutdown()
                    if callable(_prev):
                        _prev(signum, frame)

                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass  # not the main thread / unsupported -- atexit covers us

    # ── driver lifecycle ─────────────────────────────────────────

    def get_driver(self, visible: bool = False):
        """Return the live driver, creating it lazily if needed.

        Creation is bounded: if the session isn't up within
        _CREATE_BOUND seconds (usually Selenium Manager downloading
        chromedriver on first ever use), raise BrowserUnavailable with an
        honest message. The attempt keeps running in the background; if
        it completes it is either stored for the next call or quit.
        """
        with self._lock:
            if self._driver is not None:
                try:
                    _ = self._driver.current_url  # liveness probe
                    return self._driver
                except Exception:
                    logger.warning("Selenium session died -- recreating.")
                    self._quit_locked()
            if self._creating.is_set():
                # Another call is already creating; wait alongside it.
                join_wait = _CREATE_BOUND
                create_visible = None
            else:
                join_wait = 0.0
                create_visible = visible
                self._creating.set()

        try:
            if join_wait:
                # Piggyback on the in-flight creation.
                if not self._creating.wait(join_wait):
                    raise BrowserUnavailable(
                        "Chrome session is still starting in another call "
                        f"(waited {join_wait:.0f}s). Try again shortly.")
                with self._lock:
                    if self._driver is not None:
                        try:
                            _ = self._driver.current_url
                            return self._driver
                        except Exception:
                            self._quit_locked()
                    raise BrowserUnavailable(
                        "Chrome session failed to start. See the log for "
                        "the exact error.")

            result: dict = {}
            done = threading.Event()

            def _create_target():
                try:
                    result["driver"] = self._create_locked(
                        visible=bool(create_visible))
                except Exception as e:  # noqa: BLE001
                    result["exc"] = e
                finally:
                    done.set()

            t = threading.Thread(target=_create_target, daemon=True)
            t.start()
            if not done.wait(_CREATE_BOUND):
                self._timed_out_creators.add(t)
                # Race: finished between the wait timeout and the flag?
                if done.is_set() and result.get("driver") is not None:
                    self._timed_out_creators.discard(t)
                    with self._lock:
                        self._driver = result["driver"]
                        return self._driver
                raise BrowserUnavailable(
                    f"Chrome session is still starting (bounded at "
                    f"{_CREATE_BOUND:.0f}s). First-ever use downloads "
                    "chromedriver, which can be slow or blocked on "
                    "restricted networks. Try again in ~30 seconds -- if "
                    "it keeps failing, install chromedriver on PATH.")

            self._timed_out_creators.discard(t)
            if "exc" in result:
                raise result["exc"]
            return result["driver"]
        finally:
            if create_visible is not None:
                self._creating.clear()

    def _create_locked(self, visible: bool = False):
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        if not visible:
            opts.add_argument("--headless=new")
        # Anti-bot / stability defaults
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1366,900")
        opts.add_argument("--lang=en-US")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        prefs = {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
        }
        opts.add_experimental_option("prefs", prefs)
        try:
            opts.page_load_strategy = "eager"  # DOM-ready beats full-load
        except Exception:
            pass

        driver = None
        try:
            # Selenium 4.6+ has Selenium Manager: resolves chromedriver
            # automatically (no webdriver-manager needed).
            service = Service()
            driver = webdriver.Chrome(service=service, options=opts)
        except Exception as e:
            raise RuntimeError(
                f"Could not start Chrome session: {e}. Is Chrome installed? "
                "(Selenium Manager fetches chromedriver automatically.)"
            ) from e

        try:
            driver.set_page_load_timeout(_PAGELOAD_TIMEOUT)
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined})")
        except Exception:
            pass

        self._driver = driver

        # Leak guard: if this creation outlived its caller's patience AND
        # the manager already shut down (crash/exit path), quit the fresh
        # driver instead of orphaning chromedriver+chrome. Re-checked under
        # the lock so an adopted (in-use) session is never killed.
        def _abandon_check():
            time.sleep(3.0)
            with self._lock:
                if self._shutdown_done and self._driver is driver:
                    logger.warning(
                        "Quitting browser session that finished after "
                        "shutdown (leak guard).")
                    self._quit_locked()

        threading.Thread(target=_abandon_check, daemon=True).start()

        logger.info("Selenium Chrome session started (headless=%s)",
                    not visible)
        return driver

    def _quit_locked(self) -> None:
        if self._driver is None:
            return
        try:
            self._driver.quit()
        except Exception:
            pass
        self._driver = None

    def shutdown(self) -> None:
        """Quit the driver. Idempotent; safe from any thread/exit path."""
        with self._lock:
            self._shutdown_done = True
            if self._driver is not None:
                logger.info("Shutting down Selenium session.")
                self._quit_locked()

    def reset(self) -> None:
        """Force-quit now; next get_driver() starts fresh."""
        with self._lock:
            self._quit_locked()


# ── Wait helper ──────────────────────────────────────────────────

def _make_wait(driver, timeout: Optional[float]):
    from selenium.webdriver.support.ui import WebDriverWait
    t = max(0.5, min(float(timeout if timeout is not None else _DEFAULT_WAIT),
                     _MAX_WAIT))
    return WebDriverWait(driver, t), t


def _find_visible(driver, selector: str, timeout: Optional[float]):
    """Explicit wait for a visible element by CSS selector."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    wait, t = _make_wait(driver, timeout)
    return wait.until(EC.visibility_of_element_located(
        (By.CSS_SELECTOR, selector))), t


# ── Main tool entry ──────────────────────────────────────────────

def browser_interact(action: str = "navigate",
                     url: Optional[str] = None,
                     selector: Optional[str] = None,
                     text: Optional[str] = None,
                     submit: bool = False,
                     timeout: Optional[float] = None,
                     visible: bool = False,
                     **kwargs) -> Dict[str, Any]:
    """One entry point for all dynamic browser actions.

    action: navigate | click_element | input_text | wait_for_selector |
            get_rendered_html | take_element_screenshot
    """
    mgr = BrowserSessionManager.instance()
    action = (action or "").strip().lower().replace("-", "_")

    try:
        driver = mgr.get_driver(visible=visible)
    except BrowserUnavailable as e:
        return {"success": False, "error": str(e), "action": action}
    except Exception as e:  # noqa: BLE001 -- Chrome missing etc.
        return {"success": False, "error": str(e), "action": action}

    try:
        if action == "navigate":
            if not url:
                return {"success": False,
                        "error": "navigate requires 'url'.", "action": action}
            if not url.strip().lower().startswith(("http://", "https://")):
                url = "https://" + url.strip()
            driver.get(url)
            # Post-navigate sanity wait: <body> present means DOM is usable
            try:
                _find_visible(driver, "body", timeout=timeout)
            except Exception:
                pass  # pages without <body> timing still navigated
            title = ""
            try:
                title = driver.title or ""
            except Exception:
                pass
            return {"success": True, "action": action,
                    "data": f"Navigated to {driver.current_url}"
                            + (f" (title: {title})" if title else "")}

        if action == "click_element":
            if not selector:
                return {"success": False,
                        "error": "click_element requires 'selector'.",
                        "action": action}
            el, t = _find_visible(driver, selector, timeout)
            el.click()
            return {"success": True, "action": action,
                    "data": f"Clicked '{selector}' (waited {t:.1f}s)"}

        if action == "input_text":
            if not selector or text is None:
                return {"success": False,
                        "error": ("input_text requires 'selector' and "
                                  "'text'."), "action": action}
            el, t = _find_visible(driver, selector, timeout)
            el.clear()
            el.send_keys(str(text))
            if submit:
                from selenium.webdriver.common.keys import Keys
                el.send_keys(Keys.ENTER)
            return {"success": True, "action": action,
                    "data": f"Typed {len(str(text))} chars into "
                            f"'{selector}'"
                            + (" and submitted" if submit else "")
                            + f" (waited {t:.1f}s)"}

        if action == "wait_for_selector":
            if not selector:
                return {"success": False,
                        "error": "wait_for_selector requires 'selector'.",
                        "action": action}
            _el, t = _find_visible(driver, selector, timeout)
            return {"success": True, "action": action,
                    "data": f"'{selector}' appeared after {t:.1f}s"}

        if action == "get_rendered_html":
            html = driver.execute_script("return document.documentElement.outerHTML;")
            if not html:
                return {"success": False,
                        "error": "Browser returned empty HTML.",
                        "action": action}
            return {"success": True, "action": action,
                    "data": html[:_MAX_HTML_CHARS],
                    "truncated": len(html) > _MAX_HTML_CHARS}

        if action == "take_element_screenshot":
            if not selector:
                return {"success": False,
                        "error": ("take_element_screenshot requires "
                                  "'selector'."), "action": action}
            el, t = _find_visible(driver, selector, timeout)
            import base64
            png = el.screenshot_as_base64
            out_path = os.path.join(
                os.environ.get("TEMP", os.environ.get("TMP", "/tmp")),
                "jarvis_element_screenshot.png")
            with open(out_path, "wb") as f:
                f.write(base64.b64decode(png))
            return {"success": True, "action": action,
                    "data": f"Element '{selector}' screenshot saved to "
                            f"{out_path} (waited {t:.1f}s)",
                    "path": out_path}

        return {"success": False,
                "error": (f"Unknown action '{action}'. Use navigate, "
                          "click_element, input_text, wait_for_selector, "
                          "get_rendered_html, or take_element_screenshot."),
                "action": action}

    except Exception as e:  # noqa: BLE001 -- TimeoutException, NoSuchElement...
        # A dead session gets one clean retry on the next call.
        if "invalid session id" in str(e).lower():
            mgr.reset()
        return {"success": False,
                "error": f"{type(e).__name__}: {e}",
                "action": action,
                "hint": ("If the content needs JS, make sure navigation "
                         "happened first, or raise 'timeout' (max 15s).")}


# ── Gemini FunctionDeclaration ───────────────────────────────────

def get_declarations():
    from google.genai import types

    return [
        types.FunctionDeclaration(
            name="web_browser_interact",
            description=(
                "Drive a real (headless) Chrome browser with Selenium for "
                "pages that need JavaScript: navigate, click elements by CSS "
                "selector, type into inputs, wait for content to appear, "
                "grab the RENDERED html, or screenshot an element. Session "
                "is reused between calls and cleaned up automatically."),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": ("One of: navigate, click_element, "
                                        "input_text, wait_for_selector, "
                                        "get_rendered_html, "
                                        "take_element_screenshot."),
                    },
                    "url": {"type": "string",
                            "description": "URL (navigate action)."},
                    "selector": {"type": "string",
                                 "description": ("CSS selector (click/input/"
                                                 "wait/screenshot actions).")},
                    "text": {"type": "string",
                             "description": "Text to type (input_text action)."},
                    "submit": {"type": "boolean",
                               "description": ("input_text only: press Enter "
                                               "after typing.")},
                    "timeout": {"type": "number",
                                "description": ("Explicit wait seconds "
                                                "(default 3, max 15).")},
                    "visible": {"type": "boolean",
                                "description": ("Start Chrome visible "
                                                "instead of headless.")},
                },
                "required": ["action"],
            },
        ),
    ]


# ── Self-test ────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(browser_interact("navigate",
                                      url="https://example.com"), indent=2))
    print(json.dumps(browser_interact("get_rendered_html"), indent=2)[:400])
    BrowserSessionManager.instance().shutdown()
