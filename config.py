import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    TWELVE_DATA_API_KEY: str = os.getenv("TWELVE_DATA_API_KEY", "")
    TWELVE_DATA_API_KEY_2: str = os.getenv("TWELVE_DATA_API_KEY_2", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    DB_URL: str = os.getenv("DB_URL", "sqlite:///supplydemand_bot.db")
    PAIRS: list = os.getenv(
        "PAIRS",
        "EUR/USD,GBP/USD,USD/JPY,USD/CHF,USD/CAD,AUD/USD,NZD/USD,XAU/USD,"
        "EUR/JPY,GBP/JPY,EUR/CAD,GBP/CAD,EUR/AUD,GBP/CHF,AUD/JPY,EUR/GBP",
    ).split(",")
    RULEBOOK_PATH: str = os.getenv("RULEBOOK_PATH", "rulebook/rules.yaml")
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8003"))
    SCAN_WORKERS: int = int(os.getenv("SCAN_WORKERS", "4"))


settings = Settings()
