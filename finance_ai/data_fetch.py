from __future__ import annotations

import datetime as dt
import logging
from typing import List

import requests
import feedparser

from db.models import Price, News, SessionLocal

logger = logging.getLogger(__name__)

# CoinGecko simple price endpoint
COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price"
# Список монет CoinGecko IDs, которые отслеживаем
TRACKED_COINS = ["bitcoin", "ethereum"]

# RSS лента новостей (Cointelegraph)
NEWS_FEED_URL = "https://cointelegraph.com/rss"

# Endpoint для исторических данных (цены за N дней, шаг ~час)
COINGECKO_CHART = "https://api.coingecko.com/api/v3/coins/{coin}/market_chart"


def update_prices(session: SessionLocal, coins: List[str] | None = None) -> None:
    """Обновляем цены указанных монет и сохраняем в БД."""

    symbols = coins or TRACKED_COINS
    try:
        response = requests.get(
            COINGECKO_API,
            params={"ids": ",".join(symbols), "vs_currencies": "usd"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        now = dt.datetime.utcnow()
        for coin in symbols:
            if coin not in data:
                continue
            price = float(data[coin]["usd"])
            session.add(Price(coin=coin, price_usd=price, timestamp=now))
        session.commit()
        logger.info("Цены обновлены: %s", {c: data.get(c, {}) for c in symbols})
    except Exception as exc:
        logger.exception("Не удалось получить цены: %s", exc)


def update_news(session: SessionLocal, feed_url: str = NEWS_FEED_URL) -> None:
    """Парсит RSS-ленту, сохраняет новые статьи."""

    try:
        parsed = feedparser.parse(feed_url)
        new_count = 0
        for entry in parsed.entries:
            url = entry.link
            # Существуют ли уже
            exists = session.query(News).filter(News.url == url).first()
            if exists:
                continue
            published = dt.datetime(*entry.published_parsed[:6]) if entry.get("published_parsed") else dt.datetime.utcnow()
            news_item = News(
                title=entry.title,
                url=url,
                published_at=published,
                summary=entry.get("summary", ""),
            )
            session.add(news_item)
            new_count += 1
        if new_count:
            session.commit()
            logger.info("Добавлено %d новостных записей", new_count)
    except Exception as exc:
        logger.exception("Ошибка обновления новостей: %s", exc)


def backfill_prices(session: SessionLocal, coin: str, days: int = 90) -> None:
    """Скачивает историю цен за *days* и добивает недостающие точки в таблицу."""

    try:
        resp = requests.get(
            COINGECKO_CHART.format(coin=coin),
            params={"vs_currency": "usd", "days": days},
            timeout=30,
        )
        resp.raise_for_status()
        entries = resp.json().get("prices", [])  # [[ts_ms, price], ...]

        added = 0
        for ts_ms, price in entries:
            ts = dt.datetime.utcfromtimestamp(ts_ms / 1000)
            exists = (
                session.query(Price)
                .filter(Price.coin == coin, Price.timestamp == ts)
                .first()
            )
            if exists:
                continue
            session.add(Price(coin=coin, price_usd=float(price), timestamp=ts))
            added += 1

        if added:
            session.commit()
        logger.info("Backfilled %d rows for %s", added, coin)
    except Exception as exc:
        logger.exception("Backfill error for %s: %s", coin, exc) 