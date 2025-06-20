from __future__ import annotations

import datetime as dt
import os
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    LargeBinary,
    DateTime,
    Numeric,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# URI для БД (по умолчанию SQLite файл bot.db)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    telegram_id: int = Column(Integer, primary_key=True, index=True)
    # Адрес кошелька в сети Ethereum
    address: str | None = Column(String, unique=True)
    # Зашифрованный приватный ключ (AES-GCM)
    encrypted_key: bytes | None = Column(LargeBinary)
    # Соль для PBKDF2
    salt: bytes | None = Column(LargeBinary)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.telegram_id} {self.address}>"


class Transaction(Base):
    __tablename__ = "transactions"

    id: int = Column(Integer, primary_key=True)
    user_id: int = Column(Integer, index=True)
    tx_hash: str = Column(String, unique=True)
    direction: str = Column(String)  # 'in' / 'out'
    amount_eth: float = Column(Numeric(precision=18, scale=8))
    timestamp: dt.datetime = Column(DateTime, default=dt.datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tx {self.tx_hash[:10]}… {self.amount_eth} ETH>"


class Price(Base):
    __tablename__ = "prices"

    id: int = Column(Integer, primary_key=True)
    coin: str = Column(String, index=True)
    price_usd: float = Column(Numeric(precision=18, scale=8))
    timestamp: dt.datetime = Column(DateTime, default=dt.datetime.utcnow, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Price {self.coin} {self.price_usd} USD>"


class News(Base):
    __tablename__ = "news"

    id: int = Column(Integer, primary_key=True)
    title: str = Column(String)
    url: str = Column(String, unique=True)
    published_at: dt.datetime = Column(DateTime)
    summary: str | None = Column(String)
    sentiment: str | None = Column(String)

    def __repr__(self):  # pragma: no cover
        return f"<News {self.title[:30]}…>"


class Forecast(Base):
    __tablename__ = "forecasts"

    id: int = Column(Integer, primary_key=True)
    coin: str = Column(String, index=True)
    target_date: dt.date = Column(DateTime)
    price_usd: float = Column(Numeric(precision=18, scale=8))
    created_at: dt.datetime = Column(DateTime, default=dt.datetime.utcnow)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Forecast {self.coin} {self.target_date} {self.price_usd}>"


# Создаём таблицы при первом запуске
Base.metadata.create_all(bind=engine) 