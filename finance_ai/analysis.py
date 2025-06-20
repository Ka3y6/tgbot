from __future__ import annotations

import datetime as dt
import logging
from typing import List

import pandas as pd
from prophet import Prophet
from transformers import AutoTokenizer, AutoModelForSequenceClassification, pipeline

from db.models import SessionLocal, News, Price, Forecast

logger = logging.getLogger(__name__)

# -------- finBERT -------- #

try:
    _TOKENIZER = AutoTokenizer.from_pretrained("ProsusAI/finbert")
    _MODEL = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
    _SENTIMENT_PIPE = pipeline("sentiment-analysis", model=_MODEL, tokenizer=_TOKENIZER)
    logger.info("finBERT loaded")
except Exception as exc:
    logger.exception("Cannot load finBERT: %s", exc)
    _SENTIMENT_PIPE = None  # type: ignore


def analyze_unlabeled_news(session: SessionLocal) -> None:
    """Проставляет сентимент тем новостям, у которых он ещё None."""

    if _SENTIMENT_PIPE is None:
        return

    unlabeled = session.query(News).filter(News.sentiment.is_(None)).limit(20).all()
    for news in unlabeled:
        try:
            result = _SENTIMENT_PIPE(news.title[:512])[0]
            news.sentiment = result.get("label", "neutral").lower()
        except Exception as exc:
            logger.warning("Sentiment failed for %s: %s", news.url, exc)
    if unlabeled:
        session.commit()
        logger.info("Sentiment updated for %d news items", len(unlabeled))


# -------- Prophet Forecast -------- #

LOOKBACK_DAYS = 90
FORECAST_DAYS = 7


def build_forecast(session: SessionLocal, coin: str) -> None:
    """Строит прогноз на FORECAST_DAYS для coin и записывает в БД."""

    # собираем исторические данные
    since = dt.datetime.utcnow() - dt.timedelta(days=LOOKBACK_DAYS)
    prices = (
        session.query(Price)
        .filter(Price.coin == coin, Price.timestamp >= since)
        .order_by(Price.timestamp)
        .all()
    )
    if len(prices) < 30:  # мало данных
        logger.info("Недостаточно цен для прогноза %s", coin)
        return

    df = pd.DataFrame({"ds": [p.timestamp for p in prices], "y": [float(p.price_usd) for p in prices]})

    model = Prophet(daily_seasonality=True)
    model.fit(df)

    future = model.make_future_dataframe(periods=FORECAST_DAYS)
    forecast = model.predict(future)
    future_rows = forecast.tail(FORECAST_DAYS)

    # чистим старые прогнозы, добавляем новые
    session.query(Forecast).filter(Forecast.coin == coin).delete()

    for _, row in future_rows.iterrows():
        session.add(
            Forecast(
                coin=coin,
                target_date=row["ds"].date(),
                price_usd=float(row["yhat"]),
            )
        )
    session.commit()
    logger.info("Forecast updated for %s", coin) 