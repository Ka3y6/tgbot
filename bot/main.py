import logging
from io import BytesIO

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import requests

from config import TELEGRAM_TOKEN, OPENROUTER_API_KEY, STABILITY_API_KEY, DEFAULT_TEMPERATURE
from wallet.eth import create_wallet, get_wallet, send_eth
import qrcode
from apscheduler.schedulers.background import BackgroundScheduler
from finance_ai.data_fetch import update_prices, update_news
from db.models import SessionLocal, Price, News, Forecast
from finance_ai.analysis import analyze_unlabeled_news, build_forecast

# Проверяем наличие обязательных токенов
if not all([TELEGRAM_TOKEN, OPENROUTER_API_KEY, STABILITY_API_KEY]):
    missing = [
        name
        for name, value in (
            ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
            ("OPENROUTER_API_KEY", OPENROUTER_API_KEY),
            ("STABILITY_API_KEY", STABILITY_API_KEY),
        )
        if not value
    ]
    raise RuntimeError(
        f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}. "
        "Заполните их в .env файле."
    )

# Модели LLM, доступные через OpenRouter
MODELS = {
    "DeepSeek Prover": "deepseek/deepseek-prover-v2:free",
    "Llama 4 Scout": "meta-llama/llama-4-scout:free",
    "GPT-4 Turbo": "openai/gpt-4-turbo-preview",
}

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def get_main_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура главного меню."""
    return ReplyKeyboardMarkup(
        [
            ["🧹 Очистить чат", "🔄 Сменить модель"],
            ["🎨 Генерация изображения", "ℹ️ Помощь"],
        ],
        resize_keyboard=True,
    )


def get_model_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура выбора модели."""
    return ReplyKeyboardMarkup(
        [[model] for model in MODELS.keys()] + [["⬅️ Назад"]], resize_keyboard=True
    )


# ---------- Handlers ---------- #


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start – приветствие и выбор модели."""

    context.user_data.setdefault("chat_history", [])
    context.user_data.setdefault("settings", {"temperature": DEFAULT_TEMPERATURE})

    await update.message.reply_text(
        "🤖 Привет! Я AI-бот с поддержкой генерации изображений.\nВыберите модель:",
        reply_markup=get_model_keyboard(),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка обычных текстовых сообщений."""

    user_message = update.message.text.strip()

    # --- Команды клавиатуры --- #
    if user_message == "🧹 Очистить чат":
        context.user_data["chat_history"] = []
        await update.message.reply_text("История очищена.", reply_markup=get_main_keyboard())
        return
    elif user_message == "🔄 Сменить модель":
        await update.message.reply_text("Выберите модель:", reply_markup=get_model_keyboard())
        return
    elif user_message == "ℹ️ Помощь":
        await update.message.reply_text(
            "Доступные команды:\n"
            "/start – перезапуск\n"
            "/img – генерация изображения\n"
            "🧹 – очистить историю",
            reply_markup=get_main_keyboard(),
        )
        return
    elif user_message == "🎨 Генерация изображения":
        await update.message.reply_text("Напишите /img описание картинки")
        return

    # Если модель ещё не выбрана
    if "selected_model" not in context.user_data:
        await update.message.reply_text("Сначала выберите модель!", reply_markup=get_model_keyboard())
        return

    # --- Запрос к LLM --- #
    context.user_data["chat_history"].append({"role": "user", "content": user_message})

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODELS[context.user_data["selected_model"]],
                "messages": context.user_data["chat_history"],
                "temperature": context.user_data["settings"]["temperature"],
            },
            timeout=60,
        )
        response.raise_for_status()
        ai_response = response.json()["choices"][0]["message"]["content"]
        await update.message.reply_text(ai_response)

    except Exception as exc:
        logger.exception("Ошибка при обращении к LLM: %s", exc)
        await update.message.reply_text("⚠️ Ошибка обработки запроса")


async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /img – генерация изображения через Stability AI."""

    prompt = " ".join(context.args)
    if not prompt:
        await update.message.reply_text("Укажите описание: /img закат на море")
        return

    await update.message.reply_text("🖌️ Генерирую изображение…")

    try:
        response = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/sd3",
            headers={
                "Authorization": f"Bearer {STABILITY_API_KEY}",
                "Accept": "image/*",
            },
            files={"none": ""},
            data={"prompt": prompt, "output_format": "jpeg"},
            timeout=60,
        )
        response.raise_for_status()

        await update.message.reply_photo(photo=BytesIO(response.content), caption=f"🖼️ {prompt}")

    except Exception as exc:
        logger.exception("Ошибка генерации изображения: %s", exc)
        await update.message.reply_text("⚠️ Не удалось создать изображение")


async def handle_model_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора модели из клавиатуры."""

    selected_model = update.message.text.strip()

    if selected_model == "⬅️ Назад":
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return

    if selected_model not in MODELS:
        await update.message.reply_text("Выберите модель из списка:", reply_markup=get_model_keyboard())
        return

    context.user_data["selected_model"] = selected_model
    await update.message.reply_text(f"✅ Выбрана модель: {selected_model}", reply_markup=get_main_keyboard())


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
        await update.message.reply_text("Кошелёк не найден. Создайте его командой /createwallet <пароль>.")
        return

    await update.message.reply_text(f"Ваш адрес: {info.address}\nБаланс: {info.balance_eth:.6f} ETH")


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
    """/news – выводит 3 последних новости."""
    with SessionLocal() as session:
        items = (
            session.query(News).order_by(News.published_at.desc()).limit(3).all()
        )
    if not items:
        await update.message.reply_text("Новости ещё не загружены. Попробуйте позже.")
        return

    text = "\n\n".join([f"{n.title}\n{n.url}" for n in items])
    await update.message.reply_text(text)


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
    app.add_handler(CommandHandler("img", generate_image))
    app.add_handler(CommandHandler("createwallet", create_wallet_cmd))
    app.add_handler(CommandHandler("wallet", wallet_cmd))
    app.add_handler(CommandHandler("deposit", deposit_cmd))
    app.add_handler(CommandHandler("withdraw", withdraw_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("rates", rates_cmd))
    app.add_handler(CommandHandler("news", news_cmd))
    app.add_handler(CommandHandler("forecast", forecast_cmd))

    # Model selection keyboard
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(f"^({'|'.join(MODELS.keys())}|⬅️ Назад)$"),
            handle_model_selection,
        )
    )

    # Any other text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Scheduler for data fetch
    scheduler = BackgroundScheduler()

    def prices_job():
        with SessionLocal() as session:
            update_prices(session)

    def news_job():
        with SessionLocal() as session:
            update_news(session)

    def sentiment_job():
        with SessionLocal() as session:
            analyze_unlabeled_news(session)

    def forecast_job():
        with SessionLocal() as session:
            for coin in ["bitcoin", "ethereum"]:
                build_forecast(session, coin)

    scheduler.add_job(prices_job, "interval", minutes=5)
    scheduler.add_job(news_job, "interval", hours=1)
    scheduler.add_job(sentiment_job, "interval", minutes=30)
    scheduler.add_job(forecast_job, "cron", hour=0)
    scheduler.start()

    logger.info("Бот запущен и ожидает события…")
    app.run_polling()


if __name__ == "__main__":
    run_bot() 