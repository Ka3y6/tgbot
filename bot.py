import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
import requests
from io import BytesIO

# Настройки
TELEGRAM_TOKEN = "7590795767:AAE-_qp2Ajkd_MVIVc9Q963FSCEp-YT6aSo"
OPENROUTER_API_KEY = "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"
STABILITY_API_KEY = "sk-6gniSvAdfLZRmhpfC3Pjzzl7KkXkvBSOyATCfb5RwCcxnsov"

# Модели
MODELS = {
    "DeepSeek Prover": "deepseek/deepseek-prover-v2:free",
    "Llama 4 Scout": "meta-llama/llama-4-scout:free",
    "GPT-4 Turbo": "openai/gpt-4-turbo-preview"
}

# Настройка логов
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["🧹 Очистить чат", "🔄 Сменить модель"],
            ["🎨 Генерация изображения", "ℹ️ Помощь"]
        ],
        resize_keyboard=True
    )

def get_model_keyboard():
    return ReplyKeyboardMarkup(
        [[model] for model in MODELS.keys()] + [["⬅️ Назад"]],
        resize_keyboard=True
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault("chat_history", [])
    context.user_data.setdefault("settings", {"temperature": 0.7})
    await update.message.reply_text(
        "🤖 Привет! Я AI-бот с поддержкой генерации изображений.\nВыберите модель:",
        reply_markup=get_model_keyboard()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text.strip()

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
            "/start - Перезапуск\n"
            "/img - Генерация изображения\n"
            "🧹 - Очистить историю",
            reply_markup=get_main_keyboard()
        )
        return
    elif user_message == "🎨 Генерация изображения":
        await update.message.reply_text("Напишите /img описание картинки")
        return

    if "selected_model" not in context.user_data:
        await update.message.reply_text("Сначала выберите модель!", reply_markup=get_model_keyboard())
        return

    # Обработка текстового запроса к AI
    context.user_data["chat_history"].append({"role": "user", "content": user_message})

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODELS[context.user_data["selected_model"]],
                "messages": context.user_data["chat_history"],
                "temperature": context.user_data["settings"]["temperature"]
            }
        )
        response.raise_for_status()
        ai_response = response.json()["choices"][0]["message"]["content"]
        await update.message.reply_text(ai_response)

    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        await update.message.reply_text("⚠️ Ошибка обработки запроса")

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        prompt = " ".join(context.args)
        if not prompt:
            await update.message.reply_text("Укажите описание: /img закат на море")
            return

        await update.message.reply_text("🖌️ Генерирую изображение...")

        response = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/sd3",
            headers={
                "Authorization": f"Bearer {STABILITY_API_KEY}",
                "Accept": "image/*"
            },
            files={"none": ''},
            data={
                "prompt": prompt,
                "output_format": "jpeg"
            },
            timeout=60
        )

        response.raise_for_status()
        await update.message.reply_photo(
            photo=BytesIO(response.content),
            caption=f"🖼️ {prompt}"
        )

    except Exception as e:
        logger.error(f"Ошибка генерации: {str(e)}")
        await update.message.reply_text("⚠️ Не удалось создать изображение")

async def handle_model_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected_model = update.message.text.strip()

    if selected_model == "⬅️ Назад":
        await update.message.reply_text("Главное меню:", reply_markup=get_main_keyboard())
        return

    if selected_model not in MODELS:
        await update.message.reply_text("Выберите модель из списка:", reply_markup=get_model_keyboard())
        return

    context.user_data["selected_model"] = selected_model
    await update.message.reply_text(
        f"✅ Выбрана модель: {selected_model}",
        reply_markup=get_main_keyboard()
    )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("img", generate_image))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(f"^({'|'.join(MODELS.keys())}|⬅️ Назад)$"),
        handle_model_selection
    ))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    ))

    app.run_polling()

if __name__ == "__main__":
    main()