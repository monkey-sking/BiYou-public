"""Bilibili private message (DM) module.

Provides authenticated API client and polling monitor for B站 private messages
with auto-reply for resource links.
"""

from core.bili_dm.client import BiliDMClient
from core.bili_dm.monitor import ResourceDMMonitor

__all__ = ["BiliDMClient", "ResourceDMMonitor"]
