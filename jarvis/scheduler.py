"""
Jarvis Scheduler
Runs background tasks for proactive alerts:
- Calendar meeting reminders (15 min before)
- New unread email notifications
- Periodic data refresh
"""

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from jarvis.config import (
    CALENDAR_CHECK_INTERVAL,
    EMAIL_CHECK_INTERVAL,
    MEETING_REMINDER_MINUTES,
)

logger = logging.getLogger("jarvis.scheduler")


class Alert:
    """A single alert to display to the user."""

    def __init__(self, title: str, body: str, level: str = "info", source: str = ""):
        self.title = title
        self.body = body
        self.level = level  # "info", "warning", "urgent"
        self.source = source
        self.timestamp = datetime.now()

    def __str__(self):
        prefix = {"info": "[i]", "warning": "[!]", "urgent": "[!!]"}.get(
            self.level, "[?]"
        )
        return f"{prefix} {self.title}: {self.body}"


class JarvisScheduler:
    """
    Background scheduler that periodically checks Google services
    and fires alerts when something needs attention.

    Runs in its own daemon thread with an asyncio event loop.
    """

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._alert_callback: Callable[[Alert], None] | None = None
        self._brain = None

        # Tracking state to avoid duplicate alerts
        self._last_seen_event_ids: set[str] = set()
        self._last_seen_email_ids: set[str] = set()
        self._alerted_meetings: set[str] = set()

        # Configurable intervals (seconds)
        self.calendar_interval = CALENDAR_CHECK_INTERVAL
        self.email_interval = EMAIL_CHECK_INTERVAL
        self.reminder_minutes = MEETING_REMINDER_MINUTES

        # Enable/disable individual checks
        self.calendar_enabled = True
        self.email_enabled = True

    def set_brain(self, brain):
        """Set the Jarvis brain for API access."""
        self._brain = brain

    def set_alert_callback(self, callback: Callable[[Alert], None]):
        """Set callback to receive alerts (called from the scheduler thread)."""
        self._alert_callback = callback

    def _fire_alert(self, alert: Alert):
        """Send an alert to the callback."""
        logger.info(f"Alert: {alert}")
        if self._alert_callback:
            try:
                self._alert_callback(alert)
            except Exception as e:
                logger.warning(f"Alert callback failed: {e}")

    # ── Lifecycle ──────────────────────────────────────────

    def start(self):
        """Start the background scheduler."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self):
        """Stop the background scheduler."""
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Scheduler stopped")

    def _run_loop(self):
        """Run the scheduler's event loop in a background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._main_loop())

    async def _main_loop(self):
        """Main scheduler loop -- runs checks at configured intervals."""
        last_calendar_check = 0.0
        last_email_check = 0.0

        # Do an initial check shortly after startup
        await asyncio.sleep(10)

        while self._running:
            now = time.time()

            # Calendar check
            if self.calendar_enabled and (now - last_calendar_check) >= self.calendar_interval:
                last_calendar_check = now
                try:
                    await self._check_calendar()
                except Exception as e:
                    logger.warning(f"Calendar check failed: {e}")

            # Email check
            if self.email_enabled and (now - last_email_check) >= self.email_interval:
                last_email_check = now
                try:
                    await self._check_emails()
                except Exception as e:
                    logger.warning(f"Email check failed: {e}")

            # Sleep between checks
            await asyncio.sleep(5)

    # ── Calendar Checks ────────────────────────────────────

    async def _check_calendar(self):
        """Check for upcoming meetings and fire reminders."""
        if not self._brain or not self._brain.composio_session:
            return

        try:
            from datetime import date
            today = date.today().isoformat()

            result = await self._brain.commands._handle_calendar(
                self._brain.composio_session, "get_today", {}
            )

            events = result.get("data", [])
            if not events or isinstance(events, dict) and events.get("status") == "tool_not_found":
                return

            # Parse events and check for upcoming meetings
            if isinstance(events, list):
                for event in events:
                    event_id = event.get("id", event.get("eventId", str(hash(str(event)))))
                    start_time = event.get("start", {})
                    if isinstance(start_time, dict):
                        start_str = start_time.get("dateTime", start_time.get("date", ""))
                    else:
                        start_str = str(start_time)

                    # Check if this meeting is within reminder window
                    if start_str and event_id not in self._alerted_meetings:
                        try:
                            event_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                            now = datetime.now(event_dt.tzinfo) if event_dt.tzinfo else datetime.now()
                            minutes_until = (event_dt - now).total_seconds() / 60

                            if 0 < minutes_until <= self.reminder_minutes:
                                title = event.get("summary", "Untitled Event")
                                self._fire_alert(Alert(
                                    title=f"Meeting in {int(minutes_until)} min",
                                    body=title,
                                    level="warning",
                                    source="calendar",
                                ))
                                self._alerted_meetings.add(event_id)

                            # Reset alert flag if meeting is more than 1 hour away
                            elif minutes_until > 60:
                                self._alerted_meetings.discard(event_id)

                        except (ValueError, TypeError):
                            pass

        except Exception as e:
            logger.debug(f"Calendar check error: {e}")

    # ── Email Checks ───────────────────────────────────────

    async def _check_emails(self):
        """Check for new unread emails."""
        if not self._brain or not self._brain.composio_session:
            return

        try:
            result = await self._brain.commands._handle_email(
                self._brain.composio_session, "get_unread", {"count": 5}
            )

            emails = result.get("data", [])
            if not emails or isinstance(emails, dict) and emails.get("status") == "tool_not_found":
                return

            if isinstance(emails, list):
                new_count = 0
                for email in emails:
                    email_id = email.get("id", email.get("messageId", str(hash(str(email)))))
                    if email_id not in self._last_seen_email_ids:
                        new_count += 1
                        self._last_seen_email_ids.add(email_id)

                if new_count > 0:
                    # Summarize the new emails
                    senders = []
                    for email in emails[:3]:
                        sender = email.get("from", email.get("sender", "Unknown"))
                        if isinstance(sender, dict):
                            sender = sender.get("email", sender.get("name", "Unknown"))
                        senders.append(str(sender))

                    body_text = f"{new_count} new unread email(s)"
                    if senders:
                        body_text += f" -- from: {', '.join(senders[:3])}"
                    if new_count > 3:
                        body_text += f" and {new_count - 3} more"

                    self._fire_alert(Alert(
                        title="New Emails",
                        body=body_text,
                        level="info",
                        source="gmail",
                    ))

                # Cap the seen IDs set to prevent memory growth
                if len(self._last_seen_email_ids) > 500:
                    self._last_seen_email_ids = set(
                        list(self._last_seen_email_ids)[-200:]
                    )

        except Exception as e:
            logger.debug(f"Email check error: {e}")

    # ── Manual Triggers ────────────────────────────────────

    async def force_calendar_check(self):
        """Manually trigger a calendar check."""
        await self._check_calendar()

    async def force_email_check(self):
        """Manually trigger an email check."""
        await self._check_emails()

    def reset_state(self):
        """Reset tracking state (e.g., after reconnection)."""
        self._last_seen_event_ids.clear()
        self._last_seen_email_ids.clear()
        self._alerted_meetings.clear()
        logger.info("Scheduler state reset")
