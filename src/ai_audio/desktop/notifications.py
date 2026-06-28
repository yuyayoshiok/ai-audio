"""Cross-platform desktop notifications.

Uses ``plyer`` as the primary backend; falls back silently if unavailable.
"""

from __future__ import annotations


def notify(title: str, message: str) -> None:
    try:
        from plyer import notification

        notification.notify(title=title, message=message, app_name="ai-audio", timeout=5)
    except Exception:
        # Never crash the main flow over a failed notification.
        pass
