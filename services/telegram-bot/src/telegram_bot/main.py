from telegram_bot.application.bot_runner import BotRunner
from telegram_bot.config import Settings


def main() -> None:
    """Entry point: load config and run the bot."""
    cfg = Settings()  # type: ignore[call-arg]  # env vars injected at runtime
    BotRunner(cfg).run()


if __name__ == "__main__":
    main()
