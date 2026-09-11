"""
Jarvis Command Registry
Maps user intents to Composio tool actions.
"""

import datetime
from typing import Any, Callable


class CommandRegistry:
    """
    Registry of command handlers.
    Each handler takes (composio_session, parameters) and returns a result dict.
    """

    def __init__(self):
        self._handlers: dict[str, Callable] = {}
        self._register_defaults()

    def register(self, intent: str, handler: Callable):
        """Register a command handler for an intent."""
        self._handlers[intent] = handler

    def get(self, intent: str) -> Callable | None:
        return self._handlers.get(intent)

    @property
    def available_intents(self) -> list[str]:
        return list(self._handlers.keys())

    def _register_defaults(self):
        """Register built-in command handlers."""
        self.register("calendar", self._handle_calendar)
        self.register("email", self._handle_email)
        self.register("drive", self._handle_drive)
        self.register("photos", self._handle_photos)
        self.register("reminder", self._handle_reminder)
        self.register("search", self._handle_search)
        self.register("system", self._handle_system)

    #  Calendar Commands 

    async def _handle_calendar(self, session, action: str, params: dict) -> dict:
        """Handle calendar-related commands."""
        tools = session.tools()
        today = datetime.date.today().isoformat()

        if action == "get_today":
            # Use Composio's Google Calendar tools
            result = await self._call_composio_tool(
                tools, "GOOGLECALENDAR_GET_CALENDAR_EVENTS",
                {"start_date": today, "end_date": today}
            )
            return {"type": "calendar_events", "data": result}

        elif action == "create_event":
            result = await self._call_composio_tool(
                tools, "GOOGLECALENDAR_CREATE_CALENDAR_EVENT",
                {
                    "summary": params.get("title", ""),
                    "start_datetime": params.get("start_time", ""),
                    "end_datetime": params.get("end_time", ""),
                    "description": params.get("description", ""),
                }
            )
            return {"type": "event_created", "data": result}

        elif action == "get_free_slots":
            result = await self._call_composio_tool(
                tools, "GOOGLECALENDAR_GET_CALENDAR_EVENTS",
                {"start_date": today, "end_date": today}
            )
            return {"type": "free_slots", "data": result}

        return {"error": f"Unknown calendar action: {action}"}

    #  Email Commands 

    async def _handle_email(self, session, action: str, params: dict) -> dict:
        """Handle email-related commands."""
        tools = session.tools()

        if action == "get_recent":
            count = params.get("count", 10)
            result = await self._call_composio_tool(
                tools, "GMAIL_GET_EMAILS",
                {"max_results": count}
            )
            return {"type": "emails", "data": result}

        elif action == "get_unread":
            result = await self._call_composio_tool(
                tools, "GMAIL_GET_EMAILS",
                {"query": "is:unread", "max_results": params.get("count", 20)}
            )
            return {"type": "unread_emails", "data": result}

        elif action == "send":
            result = await self._call_composio_tool(
                tools, "GMAIL_SEND_EMAIL",
                {
                    "to": params.get("to", ""),
                    "subject": params.get("subject", ""),
                    "body": params.get("body", ""),
                }
            )
            return {"type": "email_sent", "data": result}

        elif action == "search":
            result = await self._call_composio_tool(
                tools, "GMAIL_GET_EMAILS",
                {"query": params.get("query", ""), "max_results": params.get("count", 10)}
            )
            return {"type": "email_search", "data": result}

        return {"error": f"Unknown email action: {action}"}

    #  Drive Commands 

    async def _handle_drive(self, session, action: str, params: dict) -> dict:
        """Handle Google Drive commands."""
        tools = session.tools()

        if action == "search":
            result = await self._call_composio_tool(
                tools, "GOOGLEDRIVE_SEARCH_FILES",
                {"query": params.get("query", "")}
            )
            return {"type": "drive_search", "data": result}

        elif action == "list_recent":
            result = await self._call_composio_tool(
                tools, "GOOGLEDRIVE_LIST_FILES",
                {"max_results": params.get("count", 10)}
            )
            return {"type": "drive_files", "data": result}

        return {"error": f"Unknown drive action: {action}"}

    #  Photos Commands 

    async def _handle_photos(self, session, action: str, params: dict) -> dict:
        """Handle Google Photos commands."""
        tools = session.tools()

        if action == "get_recent":
            result = await self._call_composio_tool(
                tools, "GOOGLEPHOTOS_LIST_MEDIA_ITEMS",
                {"page_size": params.get("count", 10)}
            )
            return {"type": "photos", "data": result}

        elif action == "list_albums":
            result = await self._call_composio_tool(
                tools, "GOOGLEPHOTOS_LIST_ALBUMS",
                {"page_size": params.get("count", 20)}
            )
            return {"type": "albums", "data": result}

        return {"error": f"Unknown photos action: {action}"}

    #  Reminder Commands 

    async def _handle_reminder(self, session, action: str, params: dict) -> dict:
        """Handle reminders via Google Calendar all-day events."""
        if action == "set":
            # Create a calendar event as a reminder
            title = params.get("title", "Reminder")
            when = params.get("when", "")

            # Create a short event as reminder
            result = await self._handle_calendar(session, "create_event", {
                "title": f" {title}",
                "start_time": when,
                "end_time": when,
                "description": f"Reminder: {params.get('description', '')}",
            })
            return {"type": "reminder_set", "data": result}

        return {"error": f"Unknown reminder action: {action}"}

    #  Search Commands 

    async def _handle_search(self, session, action: str, params: dict) -> dict:
        """Handle general search (web, files, etc.)."""
        query = params.get("query", "")

        # Search across Drive and provide results
        drive_result = await self._handle_drive(session, "search", {"query": query})
        return {"type": "search_results", "query": query, "drive": drive_result}

    #  System Commands 

    async def _handle_system(self, session, action: str, params: dict) -> dict:
        """Handle system commands (status, help, settings)."""
        if action == "status":
            return {
                "type": "status",
                "data": {
                    "composio": "connected" if session else "disconnected",
                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            }
        elif action == "help":
            return {
                "type": "help",
                "data": {
                    "intents": self.available_intents,
                    "capabilities": [
                        "Calendar: view, create, find free slots",
                        "Email: read, search, send",
                        "Drive: search, list files",
                        "Photos: browse, list albums",
                        "Reminders: set via calendar",
                        "Search: across all services",
                    ],
                }
            }
        return {"error": f"Unknown system action: {action}"}

    #  Helpers 

    async def _call_composio_tool(self, tools, tool_name: str, params: dict) -> Any:
        """Execute a Composio tool with given parameters."""
        try:
            # Find the matching tool
            for tool in tools:
                if hasattr(tool, 'name') and tool.name == tool_name:
                    result = tool.execute(params)
                    return result

            # If tool not found, return a helpful message
            return {
                "status": "tool_not_found",
                "message": f"Tool '{tool_name}' not available. "
                           f"Make sure your Google account is connected via Composio.",
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
