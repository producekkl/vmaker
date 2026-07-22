import requests

# Test querying status directly from Kling AI API
api_key = "api-key-kling-CKfiXWbldm4nuFIXb27uo0Iq9F17xIW-D2GUyt-5PAc"
api_base = "https://api-singapore.klingai.com"
task_type = "image2video"
task_id = "906592193387823133"

headers = {
    "Authorization": f"Bearer {api_key}"
}
url = f"{api_base}/v1/videos/{task_type}/{task_id}"

try:
    print("Requesting URL:", url)
    res = requests.get(url, headers=headers, timeout=20)
    print("Status Code:", res.status_code)
    print("Response JSON:", res.json())
except Exception as e:
    print("Error:", e)
