"""Session-wide test defaults.

``alert_service.bootstrap`` constructs ``Settings()`` from the environment
at *import time* (no kwargs), since it is the process-wide singleton config
used by ``main.py`` and ``consumers/*.py``. Provide fallback values here so
any test importing those modules doesn't need its own env setup.

``setdefault`` only fills gaps — it never overrides an explicitly-set env var,
and it has no effect on the many existing tests that construct
``Settings(telegram_bot_token=..., telegram_chat_id=...)`` directly via kwargs.
"""
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-bot-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
