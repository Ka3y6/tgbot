import requests
import json

# HuggingFace API конфигурация
HUGGINGFACE_API_KEY = "<YOUR_HUGGINGFACE_API_KEY>"
HUGGINGFACE_ENDPOINT = "https://api-inference.huggingface.co/models/facebook/opt-350m"

def test_api():
    headers = {
        "Authorization": f"Bearer {HUGGINGFACE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "inputs": "Привет",
        "parameters": {
            "max_length": 100,
            "temperature": 0.7,
            "top_p": 0.95
        }
    }
    
    try:
        response = requests.post(HUGGINGFACE_ENDPOINT, headers=headers, json=data)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"Parsed Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    test_api() 