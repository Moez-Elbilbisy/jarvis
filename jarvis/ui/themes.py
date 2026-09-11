"""
Jarvis Themes
Stark Industries-inspired color system.
The signature: arc reactor cyan (#00d4ff) on void black.
"""


class Theme:
    """A single UI theme with all color definitions."""

    def __init__(self, name: str, colors: dict):
        self.name = name
        self.colors = colors

    def __getitem__(self, key):
        return self.colors.get(key, "#000000")

    def get(self, key, default="#000000"):
        return self.colors.get(key, default)


# ── STARK — The primary theme ────────────────────────────
# Arc reactor cyan on near-black. Clinical, precise, alive.

STARK = Theme("Stark", {
    # Window & panels — blue-tinted void, not purple
    "window_bg": "#06080d",
    "left_panel_bg": "#080b12",
    "right_panel_bg": "#0a0d14",
    "header_bg": "#080b12",
    "header_border": "#141a24",

    # Text — cool clinical white
    "text_primary": "#e8ecf0",
    "text_secondary": "#a0a8b4",
    "text_muted": "#505862",
    "text_dim": "#343c46",

    # Labels
    "jarvis_label": "#00d4ff",
    "state_label": "#505862",
    "title_color": "#a0a8b4",
    "time_color": "#343c46",

    # Buttons — glass-like, subtle
    "btn_bg": "#0e1218",
    "btn_border": "#1a2030",
    "btn_text": "#808894",
    "btn_hover_bg": "#141c28",
    "btn_hover_border": "#00d4ff",
    "btn_hover_text": "#e8ecf0",
    "btn_pressed_bg": "#0a0e14",

    # Send button — the arc reactor
    "send_btn_bg": "#00d4ff",
    "send_btn_text": "#06080d",
    "send_btn_hover": "#33dfff",

    # Input field — focus glow
    "input_bg": "#0e1218",
    "input_border": "#1a2030",
    "input_text": "#e8ecf0",
    "input_focus_border": "#00d4ff",
    "input_placeholder": "#343c46",

    # Chat display
    "chat_bg": "#0a0d14",
    "chat_text": "#c0c8d0",
    "chat_user_time": "#505862",
    "chat_user_text": "#e8ecf0",
    "chat_assistant_time": "#00d4ff",
    "chat_assistant_text": "#c0c8d0",
    "chat_system_bg": "#0e1218",
    "chat_system_text": "#343c46",

    # Notification bar
    "notif_bg": "#0e1218",
    "notif_border": "#1a2030",
    "notif_text": "#e8ecf0",
    "notif_close": "#505862",
    "notif_close_hover": "#e8ecf0",

    # Status
    "status_connected": "#00d4ff",
    "status_disconnected": "#343c46",

    # Code blocks
    "code_bg": "#0e1218",

    # Accent — the arc reactor
    "accent": "#00d4ff",
    "accent_dim": "#004455",
    "accent_glow": "rgba(0, 212, 255, 0.08)",

    # HUD elements
    "hud_corner": "#1a2030",
    "hud_corner_active": "#00d4ff",
})


# ── LIGHT — Clean, precise, not warm ─────────────────────

LIGHT = Theme("Light", {
    "window_bg": "#f0f2f5",
    "left_panel_bg": "#ffffff",
    "right_panel_bg": "#f7f8fa",
    "header_bg": "#ffffff",
    "header_border": "#e0e3e8",

    "text_primary": "#1a1e24",
    "text_secondary": "#4a5060",
    "text_muted": "#8890a0",
    "text_dim": "#b0b8c4",

    "jarvis_label": "#0066aa",
    "state_label": "#8890a0",
    "title_color": "#4a5060",
    "time_color": "#b0b8c4",

    "btn_bg": "#f0f2f5",
    "btn_border": "#d8dce4",
    "btn_text": "#4a5060",
    "btn_hover_bg": "#e4e8ee",
    "btn_hover_border": "#0066aa",
    "btn_hover_text": "#1a1e24",
    "btn_pressed_bg": "#dce0e8",

    "send_btn_bg": "#0066aa",
    "send_btn_text": "#ffffff",
    "send_btn_hover": "#005590",

    "input_bg": "#ffffff",
    "input_border": "#d8dce4",
    "input_text": "#1a1e24",
    "input_focus_border": "#0066aa",
    "input_placeholder": "#b0b8c4",

    "chat_bg": "#f7f8fa",
    "chat_text": "#3a4050",
    "chat_user_time": "#8890a0",
    "chat_user_text": "#1a1e24",
    "chat_assistant_time": "#0066aa",
    "chat_assistant_text": "#3a4050",
    "chat_system_bg": "#f0f2f5",
    "chat_system_text": "#8890a0",

    "notif_bg": "#f0f2f5",
    "notif_border": "#d8dce4",
    "notif_text": "#1a1e24",
    "notif_close": "#8890a0",
    "notif_close_hover": "#1a1e24",

    "status_connected": "#00884a",
    "status_disconnected": "#b0b8c4",

    "code_bg": "#f0f2f5",

    "accent": "#0066aa",
    "accent_dim": "#d0e4f2",
    "accent_glow": "rgba(0, 102, 170, 0.06)",

    "hud_corner": "#d8dce4",
    "hud_corner_active": "#0066aa",
})


# ── FURY — SHIELD tactical ───────────────────────────────
# Matte military green. Duty-focused, no-nonsense.

FURY = Theme("Fury", {
    "window_bg": "#0a0f0a",
    "left_panel_bg": "#0d130d",
    "right_panel_bg": "#0f150f",
    "header_bg": "#0d130d",
    "header_border": "#1a2a1a",

    "text_primary": "#d8e0d0",
    "text_secondary": "#90a080",
    "text_muted": "#506040",
    "text_dim": "#344030",

    "jarvis_label": "#70b840",
    "state_label": "#506040",
    "title_color": "#90a080",
    "time_color": "#344030",

    "btn_bg": "#121a12",
    "btn_border": "#1e2e1e",
    "btn_text": "#708060",
    "btn_hover_bg": "#1a281a",
    "btn_hover_border": "#70b840",
    "btn_hover_text": "#d8e0d0",
    "btn_pressed_bg": "#0e140e",

    "send_btn_bg": "#70b840",
    "send_btn_text": "#0a0f0a",
    "send_btn_hover": "#80cc50",

    "input_bg": "#121a12",
    "input_border": "#1e2e1e",
    "input_text": "#d8e0d0",
    "input_focus_border": "#70b840",
    "input_placeholder": "#344030",

    "chat_bg": "#0f150f",
    "chat_text": "#b0c0a0",
    "chat_user_time": "#506040",
    "chat_user_text": "#d8e0d0",
    "chat_assistant_time": "#70b840",
    "chat_assistant_text": "#b0c0a0",
    "chat_system_bg": "#121a12",
    "chat_system_text": "#344030",

    "notif_bg": "#121a12",
    "notif_border": "#1e2e1e",
    "notif_text": "#d8e0d0",
    "notif_close": "#506040",
    "notif_close_hover": "#d8e0d0",

    "status_connected": "#70b840",
    "status_disconnected": "#344030",

    "code_bg": "#121a12",

    "accent": "#70b840",
    "accent_dim": "#1a3a10",
    "accent_glow": "rgba(112, 184, 64, 0.08)",

    "hud_corner": "#1e2e1e",
    "hud_corner_active": "#70b840",
})


# ── OBADIAH — Warm authority ─────────────────────────────
# Amber on dark walnut. Executive, grounded, unhurried.

OBADIAH = Theme("Obadiah", {
    "window_bg": "#0f0b08",
    "left_panel_bg": "#130e09",
    "right_panel_bg": "#15100b",
    "header_bg": "#130e09",
    "header_border": "#2a1e14",

    "text_primary": "#f0e4d4",
    "text_secondary": "#b8a488",
    "text_muted": "#6a5a44",
    "text_dim": "#4a3e30",

    "jarvis_label": "#d4a030",
    "state_label": "#6a5a44",
    "title_color": "#b8a488",
    "time_color": "#4a3e30",

    "btn_bg": "#1a1410",
    "btn_border": "#2e2218",
    "btn_text": "#8a7a64",
    "btn_hover_bg": "#221a14",
    "btn_hover_border": "#d4a030",
    "btn_hover_text": "#f0e4d4",
    "btn_pressed_bg": "#140e0a",

    "send_btn_bg": "#d4a030",
    "send_btn_text": "#0f0b08",
    "send_btn_hover": "#e0b040",

    "input_bg": "#1a1410",
    "input_border": "#2e2218",
    "input_text": "#f0e4d4",
    "input_focus_border": "#d4a030",
    "input_placeholder": "#4a3e30",

    "chat_bg": "#15100b",
    "chat_text": "#c8b8a0",
    "chat_user_time": "#6a5a44",
    "chat_user_text": "#f0e4d4",
    "chat_assistant_time": "#d4a030",
    "chat_assistant_text": "#c8b8a0",
    "chat_system_bg": "#1a1410",
    "chat_system_text": "#4a3e30",

    "notif_bg": "#1a1410",
    "notif_border": "#2e2218",
    "notif_text": "#f0e4d4",
    "notif_close": "#6a5a44",
    "notif_close_hover": "#f0e4d4",

    "status_connected": "#d4a030",
    "status_disconnected": "#4a3e30",

    "code_bg": "#1a1410",

    "accent": "#d4a030",
    "accent_dim": "#3a2a10",
    "accent_glow": "rgba(212, 160, 48, 0.08)",

    "hud_corner": "#2e2218",
    "hud_corner_active": "#d4a030",
})


# ── Theme Registry ────────────────────────────────────────

ALL_THEMES: dict[str, Theme] = {
    "stark": STARK,
    "light": LIGHT,
    "fury": FURY,
    "obadiah": OBADIAH,
}

DEFAULT_THEME = "stark"


def get_theme(name: str) -> Theme:
    """Get a theme by name. Falls back to stark if not found."""
    return ALL_THEMES.get(name.lower(), STARK)


def list_themes() -> list[str]:
    """Return all available theme names."""
    return list(ALL_THEMES.keys())
