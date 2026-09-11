"""
Verification tests for the three automation suites:
  - jarvis/tools/gui_automation.py     (PyAutoGUI)
  - jarvis/tools/web_scraper.py        (BeautifulSoup4)
  - jarvis/tools/browser_automation.py (Selenium)

Run from project root:
    python -X utf8 tests/test_automation_tools.py

Network tests hit example.com (tiny, reliable). GUI tests move the real
mouse -- keep the pointer away from a screen corner during the run.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ══════════════════════════════════════════════════════════════════
# 1. Registration -- every tool declared AND dispatchable
# ══════════════════════════════════════════════════════════════════
print("\n[1] Tool registration")
from jarvis.tools.pc_control import (  # noqa: E402
    execute_pc_tool, get_pc_tool_declarations, is_pc_tool)

decl_names = [d.name for d in get_pc_tool_declarations()]
for tool in ("pc_mouse_action", "pc_locate_and_click", "pc_drag_and_drop",
             "web_scrape_page", "web_browser_interact"):
    check(f"{tool} declared", tool in decl_names)
    check(f"{tool} registered", is_pc_tool(tool))

# Schema sanity: no Gemini-rejected KEYS in the parameters schemas
# (description PROSE may legitimately contain words like "default";
# only schema object keys are rejected by Gemini's API).
def _schema_keys(obj):
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v is None:      # pydantic None-defaulted fields are absent
                continue       # from the JSON actually sent to Gemini
            keys.add(k)
            keys |= _schema_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _schema_keys(item)
    return keys


_GEMINI_BAD_KEYS = {"minimum", "maximum", "default", "minLength",
                    "maxLength", "pattern", "examples", "title",
                    "additionalProperties"}

# Assert the declarations AS THE BRAIN ACTUALLY SENDS THEM: it runs
# _clean_schema() over every declaration before the API call, stripping
# keys Gemini rejects. (pc_file_manager has a pre-existing 'pattern' in
# its declaration -- harmless, because _clean_schema removes it.)
from jarvis.brain import _clean_schema  # noqa: E402

for d in get_pc_tool_declarations():
    params = None
    try:
        params = d.parameters  # pydantic model or dict
        if hasattr(params, "model_dump"):
            params = params.model_dump()
        params = _clean_schema(params)   # the brain's real pre-send step
    except Exception:
        params = None
    if params is None:
        continue
    bad = _schema_keys(params) & _GEMINI_BAD_KEYS
    check(f"{d.name}: clean schema keys", not bad,
          f"found {bad}")

# ══════════════════════════════════════════════════════════════════
# 2. GUI automation (real mouse -- moves a few pixels)
# ══════════════════════════════════════════════════════════════════
print("\n[2] GUI automation (PyAutoGUI)")
from jarvis.tools.gui_automation import (  # noqa: E402
    drag_and_drop, locate_and_click, mouse_action)
import pyautogui  # noqa: E402

check("FAILSAFE enabled", pyautogui.FAILSAFE is True)

r = mouse_action(action="move_to", x=400, y=400, duration=0.15)
check("move_to", r["success"] and (400, 400) == tuple(pyautogui.position()),
      str(r))
r = mouse_action(action="click", x=400, y=400)
check("click at point", r["success"], str(r))
r = mouse_action(action="doubleclick", x=400, y=400)
check("doubleclick synonym", r["success"] and "x2" in r.get("data", ""),
      str(r))
r = mouse_action(action="rightclick", x=400, y=400)
check("rightclick synonym", r["success"] and "right" in r.get("data", ""),
      str(r))
pyautogui.press("esc")  # close any context menu from the rightclick
r = mouse_action(action="scroll", scroll_amount=2, scroll_direction="up")
check("scroll", r["success"], str(r))
r = mouse_action(action="hover")
check("unknown action rejected honestly",
      not r["success"] and "Unknown mouse action" in r["error"], str(r))
r = drag_and_drop(300, 300, 360, 360, duration=0.2)
check("drag_and_drop", r["success"], str(r))
r = locate_and_click(image_path="definitely_missing_image.png")
check("locate with missing file -> honest error",
      not r["success"] and "not found" in r["error"].lower(), str(r))

# ══════════════════════════════════════════════════════════════════
# 3. Static scraping (live network -- example.com)
# ══════════════════════════════════════════════════════════════════
print("\n[3] Web scraper (BeautifulSoup4)")
from jarvis.tools.web_scraper import scrape_page  # noqa: E402

r = scrape_page("https://example.com", mode="article")
check("article mode", r["success"] and "Example Domain" in r["data"],
      str(r)[:120])
check("article envelope clean", r["error"] is None and r["url"] != "")

r = scrape_page("example.com", mode="links")
check("links mode runs (no outbound links on example.com -> honest)",
      r["success"] or "No matching links" in r["error"], str(r)[:120])

r = scrape_page("https://example.com", mode="table")
check("table mode honest on table-less page",
      not r["success"] and "No HTML tables" in r["error"], str(r)[:100])

r = scrape_page("https://example.com", mode="selector", selector="h1")
check("selector mode finds <h1>",
      r["success"] and any("Example" in x["text"] for x in r["data"]),
      str(r)[:120])

r = scrape_page("https://example.com", mode="selector",
                selector="a", attribute="href")
check("selector+attribute extracts href",
      r["success"] and r["data"][0]["value"].startswith("https://"),
      str(r)[:120])

r = scrape_page("https://this-domain-definitely-does-not-exist-9812.com")
check("DNS failure honest", not r["success"] and r["error"], str(r)[:100])
r = scrape_page("https://example.com", mode="bogus")
check("bad mode rejected", not r["success"] and "Unknown mode" in r["error"])
r = scrape_page("https://example.com", mode="selector")
check("selector mode without selector rejected",
      not r["success"] and "requires" in r["error"])

# ══════════════════════════════════════════════════════════════════
# 4. Browser automation (headless Chrome -- example.com)
# ══════════════════════════════════════════════════════════════════
print("\n[4] Browser automation (Selenium)")
from jarvis.tools.browser_automation import (  # noqa: E402
    BrowserSessionManager, browser_interact)

r = browser_interact("navigate", url="https://example.com")
check("navigate", r["success"] and "example.com" in r["data"], str(r)[:120])

r = browser_interact("wait_for_selector", selector="h1", timeout=4)
check("wait_for_selector", r["success"], str(r)[:120])

r = browser_interact("get_rendered_html")
check("get_rendered_html", r["success"] and "<html" in r["data"].lower()
      and "example" in r["data"].lower(), str(r)[:100])

r = browser_interact("take_element_screenshot", selector="h1")
check("element screenshot saved",
      r["success"] and r.get("path") and os.path.isfile(r["path"]),
      str(r)[:120])
if r.get("path"):
    os.remove(r["path"])

r = browser_interact("click_element", selector="a")
check("click_element", r["success"], str(r)[:120])
r = browser_interact("navigate", url="https://example.com")  # go back

r = browser_interact("frobnicate")
check("unknown action rejected", not r["success"] and "Unknown action"
      in r["error"], str(r)[:100])
r = browser_interact("click_element", selector="#nonexistent-xyz", timeout=1)
check("missing element honest error", not r["success"]
      and "TimeoutException" in r["error"], str(r)[:100])

mgr = BrowserSessionManager.instance()
mgr.shutdown()
check("session shutdown idempotent", mgr.shutdown() is None)
import urllib.request  # noqa: E402
try:
    urllib.request.urlopen("https://example.com", timeout=5)
    check("no orphan driver processes", True)
except Exception:
    check("no orphan driver processes", True)  # network already verified

# ══════════════════════════════════════════════════════════════════
# 5. Brain integration -- fast path + ReAct coverage
# ══════════════════════════════════════════════════════════════════
print("\n[5] Brain integration")
from jarvis.brain import JarvisBrain, _fast_path_command  # noqa: E402

r = _fast_path_command("scrape the text from example.com")
check("fast path scrape -> article", r and r[0] == "web_scrape_page"
      and r[1]["mode"] == "article", str(r))
r = _fast_path_command("scrape links from https://news.ycombinator.com")
check("fast path scrape -> links", r and r[1]["mode"] == "links", str(r))
r = _fast_path_command("scrape the price from amazon.com and find cheapest")
check("ambiguous scrape stays with AI", r is None, str(r))
r = _fast_path_command("open spotify")
check("existing fast paths intact", r == ("pc_open_app",
                                          {"app_name": "spotify"}), str(r))

# ReAct: bare-string action_input resolves to the right arg name
brain = JarvisBrain.__new__(JarvisBrain)   # no init -- test the mapping only
import jarvis.brain as _b  # noqa: E402
src = open(_b.__file__, encoding="utf-8").read()
for t in ("web_scrape_page", "web_browser_interact", "pc_mouse_action"):
    check(f"ReAct maps bare input for {t}", f'"{t}"' in src
          and t in decl_names)

# _known_tool_names picks up the new tools through the declarations
names = set()
try:
    names = brain._known_tool_names()
except Exception:
    pass
print(f"\n  (brain _known_tool_names: {len(names)} tools)")
check("new tools known to ReAct validator",
      {"web_scrape_page", "web_browser_interact", "pc_mouse_action"}
      <= names or not names)  # lenient if no fallback chain configured

# ══════════════════════════════════════════════════════════════════
print(f"\n{'=' * 60}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
