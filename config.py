import os
from pathlib import Path

from dotenv import load_dotenv

# Загружаем переменные окружения из .env (если файл присутствует)
load_dotenv(dotenv_path=Path(".env"))

TELEGRAM_TOKEN: str | None = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY: str | None = os.getenv("OPENROUTER_API_KEY")
STABILITY_API_KEY: str | None = os.getenv("STABILITY_API_KEY")

# Значение температуры по умолчанию для LLM-запросов
DEFAULT_TEMPERATURE: float = float(os.getenv("DEFAULT_TEMPERATURE", "0.7")) 