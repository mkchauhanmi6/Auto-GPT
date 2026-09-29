"""Optional phone alerts via a Telegram bot (so you can copy signals into Sensibull on mobile).

Create a bot with @BotFather, send it a message, then set TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID (your chat id, e.g. from https://api.telegram.org/bot<token>/getUpdates).
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable, Optional


def telegram_notifier() -> Optional[Callable[[str], None]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None

    def send(text: str) -> None:
        body = json.dumps({"chat_id": chat_id, "text": text[:4000]}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15).read()

    return send
