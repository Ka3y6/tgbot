import os
import requests
import logging
import json

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# HuggingFace API конфигурация
HUGGINGFACE_API_KEY = "<YOUR_HUGGINGFACE_API_KEY>"
HUGGINGFACE_ENDPOINT = "https://api-inference.huggingface.co/models/facebook/opt-350m"

def test_api():
    logger.info(f"Тестируем эндпоинт: {HUGGINGFACE_ENDPOINT}")
    try:
        headers = {
            "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # Простой тестовый промпт
        prompt = "Hello! How are you?"
        
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 50,
                "temperature": 0.7,
                "return_full_text": False
            }
        }
        
        logger.info(f"Отправка запроса с заголовками: {headers} и телом: {json.dumps(payload)}")
        
        response = requests.post(
            HUGGINGFACE_ENDPOINT,
            headers=headers,
            json=payload
        )

        logger.info(f"Статус ответа: {response.status_code}")
        if response.status_code == 200:
            try:
                data = response.json()
                logger.info(f"Ответ от API: {data}")
            except json.JSONDecodeError:
                logger.error(f"Не удалось декодировать JSON: {response.text}")
        else:
            logger.error(f"Ошибка API: {response.text}")
            
    except Exception as e:
        logger.error(f"Непредвиденная ошибка: {str(e)}")
        logger.error(f"Тип ошибки: {type(e)}")

if __name__ == "__main__":
    test_api() 