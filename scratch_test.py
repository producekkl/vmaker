import requests
import json

api_key = "AIzaSyAjhzBIWqCqO0lN-OZdWJeuMf7iX6ZAdw0"
model = "imagen-3.0-generate-002"
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateImages?key={api_key}"

payload = {
    "prompt": "A cute cartoon banana wearing sunglasses on a sunny beach.",
    "numberOfImages": 1,
    "outputMimeType": "image/jpeg",
    "aspectRatio": "1:1"
}

headers = {"Content-Type": "application/json"}

try:
    response = requests.post(url, json=payload, headers=headers)
    print("Status Code:", response.status_code)
    res_json = response.json()
    if "generatedImages" in res_json:
        print("Success! Generated image base64 length:", len(res_json["generatedImages"][0]["image"]["imageBytes"]))
    else:
        print("Response:", json.dumps(res_json, indent=2))
except Exception as e:
    print("Failed:", e)
