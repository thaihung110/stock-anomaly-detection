from typing import Any

from telegram.ext import Application

# python-telegram-bot Application has 6 generic params; alias to Any for pragmatic typing
BotApp = Application[Any, Any, Any, Any, Any, Any]
