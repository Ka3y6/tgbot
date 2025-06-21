import logging
from io import BytesIO
import asyncio
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import TELEGRAM_TOKEN
from wallet.eth import create_wallet, get_wallet, send_eth
import qrcode
from apscheduler.schedulers.background import BackgroundScheduler
from finance_ai.data_fetch import update_prices, update_news, backfill_prices
from db.models import SessionLocal, Price, News, Forecast
from finance_ai.analysis import analyze_unlabeled_news, build_forecast

# Проверяем наличие обязательного токена
if not TELEGRAM_TOKEN:
    raise RuntimeError("Переменная TELEGRAM_TOKEN не указана в .env")

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.DEBUG
)
logging.getLogger("apscheduler").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура главного меню."""
    return ReplyKeyboardMarkup(
        [
            ["👛 Wallet", "📈 Rates"],
            ["📰 News", "🔮 Forecast"],
            ["ℹ️ Help"],
        ],
        resize_keyboard=True,
    )


def get_model_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура выбора модели."""
    return ReplyKeyboardMarkup(
        [[model] for model in MODELS.keys()] + [["⬅️ Назад"]], resize_keyboard=True
    )


# ---------- Новый блок: клавиатура кошелька ---------- #


def get_wallet_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура управления кошельком."""
    return ReplyKeyboardMarkup(
        [
            ["💰 Balance", "➕ Deposit"],
            ["💸 Withdraw", "📜 History"],
            ["⬅️ Назад"],
        ],
        resize_keyboard=True,
    )


# ---------- Helper: parse & translate news ---------- #

async def _fetch_and_translate(url: str, summary_en: str | None = None, max_chars: int = 400) -> tuple[str, str]:
    """Скачивает статью, извлекает текст и переводит на русский.

    Возвращает (title_ru, snippet_ru). При ошибке – пустые строки."""

    def _worker() -> tuple[str, str]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            }
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            title_en = soup.title.string.strip() if soup.title and soup.title.string else ""

            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            body_en = " ".join(paragraphs) or (summary_en or "")
            body_en = body_en[:max_chars]

            translator = GoogleTranslator(source="auto", target="ru")

            title_ru = translator.translate(title_en) if title_en else ""
            snippet_ru = translator.translate(body_en) if body_en else ""

            # Если не удалось получить текст статьи, используем summary
            if not snippet_ru and summary_en:
                try:
                    translator = GoogleTranslator(source="auto", target="ru")
                    snippet_ru = translator.translate(summary_en[:max_chars])
                except Exception:
                    pass

            return title_ru, snippet_ru
        except Exception as exc:
            logger.exception("Ошибка парсинга/перевода новости %s: %s", url, exc)
            # Пытаемся перевести summary_en даже при ошибке загрузки страницы
            if summary_en:
                try:
                    translator = GoogleTranslator(source="auto", target="ru")
                    snippet_ru = translator.translate(summary_en[:max_chars])
                    return "", snippet_ru
                except Exception:
                    pass
            return "", ""

    return await asyncio.to_thread(_worker)


# ---------- Handlers ---------- #


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start – приветствие и выбор модели."""

    await update.message.reply_text(
        "🤖 Привет! Я финансовый бот. Доступные функции:",
        reply_markup=get_main_keyboard(),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка обычных текстовых сообщений."""

    user_message = update.message.text.strip()

    # --- Команды клавиатуры --- #
    if user_message == "ℹ️ Help":
        await update.message.reply_text(
            "Доступные команды:\n"
            "/createwallet <pwd> – создать кошелёк\n"
            "/wallet – баланс\n"
            "/deposit – депозитный QR\n"
            "/withdraw <amt> <to> <pwd> – вывод ETH\n"
            "/history – последние транзакции\n"
            "/rates – цены BTC/ETH\n"
            "/news – свежие новости\n"
            "/forecast – прогноз цен",
            reply_markup=get_main_keyboard(),
        )
        return
    elif user_message == "👛 Wallet":
        await wallet_cmd(update, context)
        return
    elif user_message == "📈 Rates":
        await rates_cmd(update, context)
        return
    elif user_message == "📰 News":
        await news_cmd(update, context)
        return
    elif user_message == "🔮 Forecast":
        await forecast_cmd(update, context)
        return
    # --- Клавиатура кошелька --- #
    elif user_message == "💰 Balance":
        await wallet_cmd(update, context)
        return
    elif user_message == "➕ Deposit":
        await deposit_cmd(update, context)
        return
    elif user_message == "💸 Withdraw":
        # Для вывода требуется сумма, адрес и пароль – подскажем пользователю формат
        await update.message.reply_text(
            "Введите команду:\n/withdraw <amount_eth> <to_address> <password>",
            reply_markup=get_wallet_keyboard(),
        )
        return
    elif user_message == "📜 History":
        await history_cmd(update, context)
        return
    elif user_message == "⬅️ Назад":
        await update.message.reply_text(
            "Главное меню:", reply_markup=get_main_keyboard()
        )
        return

    # Неизвестное сообщение – игнор
    return


async def create_wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/createwallet <пароль> – генерирует новый кошелёк."""
    if get_wallet(update.effective_user.id):
        await update.message.reply_text("Кошелёк уже существует. Используйте /wallet чтобы посмотреть баланс.")
        return

    if not context.args:
        await update.message.reply_text("Укажите пароль: /createwallet <пароль>")
        return

    password = context.args[0]
    info = create_wallet(update.effective_user.id, password)
    await update.message.reply_text(
        f"✅ Кошелёк создан!\nАдрес: {info.address}\n" "Не забудьте сохранить пароль — он нужен для вывода средств."
    )


async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/wallet – показать адрес и баланс."""
    info = get_wallet(update.effective_user.id)
    if not info:
        await update.message.reply_text(
            "Кошелёк не найден. Создайте его командой /createwallet <пароль>.",
            reply_markup=get_main_keyboard(),
        )
        return

    await update.message.reply_text(
        f"Ваш адрес: {info.address}\nБаланс: {info.balance_eth:.6f} ETH",
        reply_markup=get_wallet_keyboard(),
    )


async def deposit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/deposit – отправляет QR-код адреса."""
    info = get_wallet(update.effective_user.id)
    if not info:
        await update.message.reply_text("Сначала создайте кошелёк: /createwallet <пароль>.")
        return

    qr = qrcode.make(info.address)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    await update.message.reply_photo(photo=bio, caption=f"Адрес для пополнения: {info.address}")


async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/withdraw <amount_eth> <to_address> <password> – вывод средств."""
    if len(context.args) < 3:
        await update.message.reply_text("Формат: /withdraw <amount> <address> <password>")
        return

    amount_str, to_address, password = context.args[:3]
    try:
        amount = float(amount_str)
    except ValueError:
        await update.message.reply_text("Сумма должна быть числом.")
        return

    try:
        tx_hash = send_eth(update.effective_user.id, to_address, amount, password)
        await update.message.reply_text(f"✅ Транзакция отправлена. Hash: {tx_hash}")
    except Exception as exc:
        logger.exception("Ошибка вывода средств: %s", exc)
        await update.message.reply_text(f"⚠️ {exc}")


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/history – показать 5 последних транзакций."""
    from db.models import SessionLocal, Transaction

    with SessionLocal() as session:
        txs = (
            session.query(Transaction)
            .filter(Transaction.user_id == update.effective_user.id)
            .order_by(Transaction.timestamp.desc())
            .limit(5)
            .all()
        )

    if not txs:
        await update.message.reply_text("История пуста.")
        return

    lines = [f"{tx.direction} {tx.amount_eth} ETH – {tx.tx_hash[:10]}…" for tx in txs]
    await update.message.reply_text("\n".join(lines))


async def rates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/rates – показывает текущие цены BTC и ETH."""
    with SessionLocal() as session:
        lines = []
        for coin in ["bitcoin", "ethereum"]:
            latest = (
                session.query(Price)
                .filter(Price.coin == coin)
                .order_by(Price.timestamp.desc())
                .first()
            )
            if latest:
                price_val = float(latest.price_usd)
                lines.append(f"{coin.capitalize()}: ${price_val:.2f}")
        if lines:
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text("Цены ещё не загружены. Подождите пару минут…")


async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/news – показывает 3 свежие новости (кратко, на русском, без ссылок)."""

    with SessionLocal() as session:
        items = (
            session.query(News).order_by(News.published_at.desc()).limit(3).all()
        )

    if not items:
        await update.message.reply_text("Новости ещё не загружены. Попробуйте позже.")
        return

    messages: list[str] = []
    # Обрабатываем статьи последовательно (можно добавить gather для параллели)
    for n in items:
        title_ru, snippet_ru = await _fetch_and_translate(n.url, n.summary)
        if not title_ru:
            title_ru = n.title  # fallback на оригинал
        msg_parts = [title_ru]
        if snippet_ru:
            msg_parts.append(snippet_ru)
        messages.append("\n\n".join(msg_parts))

    await update.message.reply_text("\n\n― ― ―\n\n".join(messages))


async def forecast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/forecast – показывает прогноз на 7 дней для BTC и ETH."""
    with SessionLocal() as session:
        coins = ["bitcoin", "ethereum"]
        lines = []
        for coin in coins:
            forecasts = (
                session.query(Forecast)
                .filter(Forecast.coin == coin)
                .order_by(Forecast.target_date)
                .all()
            )
            if not forecasts:
                continue
            lines.append(f"Прогноз {coin.capitalize()}:")
            for fc in forecasts:
                lines.append(f"{fc.target_date}: ${float(fc.price_usd):.2f}")
            lines.append("")
        if lines:
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text("Прогнозы ещё не готовы. Подождите…")


# ---------- Application bootstrap ---------- #

def run_bot() -> None:
    """Создание и запуск Telegram-приложения."""
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("createwallet", create_wallet_cmd))
    app.add_handler(CommandHandler("wallet", wallet_cmd))
    app.add_handler(CommandHandler("deposit", deposit_cmd))
    app.add_handler(CommandHandler("withdraw", withdraw_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("rates", rates_cmd))
    app.add_handler(CommandHandler("news", news_cmd))
    app.add_handler(CommandHandler("forecast", forecast_cmd))

    # Reply-keyboard buttons handler
    # Any other text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduler for data fetch
    scheduler = BackgroundScheduler()

    def prices_job():
        logger.debug("Запуск задачи prices_job")
        with SessionLocal() as session:
            update_prices(session)
        logger.debug("prices_job завершена")

    def news_job():
        logger.debug("Запуск задачи news_job")
        with SessionLocal() as session:
            update_news(session)
        logger.debug("news_job завершена")

    def sentiment_job():
        logger.debug("Запуск задачи sentiment_job")
        with SessionLocal() as session:
            analyze_unlabeled_news(session)
        logger.debug("sentiment_job завершена")

    def forecast_job():
        logger.debug("Запуск задачи forecast_job")
        with SessionLocal() as session:
            for coin in ["bitcoin", "ethereum"]:
                build_forecast(session, coin)
        logger.debug("forecast_job завершена")

    # --- подгружаем 90-дневную историю цен и немедленно выполняем задачи ---
    with SessionLocal() as s:
        for coin in ["bitcoin", "ethereum"]:
            backfill_prices(s, coin)

    # --- немедленный прогон при старте ---
    prices_job()
    news_job()
    sentiment_job()
    forecast_job()

    # Укороченные интервалы для оперативного наполнения данных
    scheduler.add_job(prices_job, "interval", minutes=2)
    scheduler.add_job(news_job, "interval", minutes=10)
    scheduler.add_job(sentiment_job, "interval", minutes=10)
    scheduler.add_job(forecast_job, "cron", minute=0)  # каждый час в 00 минут

    scheduler.start()

    logger.info("Бот запущен и ожидает события…")
    app.run_polling()


if __name__ == "__main__":
    run_bot() 