import os
import replicate
import logging

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Установка токена
os.environ["REPLICATE_API_TOKEN"] = "<YOUR_REPLICATE_API_TOKEN>"

def test_api():
    try:
        # Проверяем доступные модели
        logger.info("Проверяем доступные модели...")
        
        # Тестируем простой запрос
        output = replicate.run(
            "meta/llama-2-70b-chat:02e509c789964a7ea8736978a43525956ef40397be9033abf9fd2badfe68c9e3",
            input={
                "prompt": "Привет, как дела?",
                "system_prompt": "Ты - полезный ассистент, который отвечает на русском языке.",
                "temperature": 0.7,
                "max_tokens": 100
            }
        )
        
        logger.info("Ответ от API:")
        for text in output:
            logger.info(text)
            
    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        logger.error(f"Тип ошибки: {type(e)}")

if __name__ == "__main__":
    test_api() 