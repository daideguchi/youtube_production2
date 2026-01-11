import os
import requests
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)

def test_azure():
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

    print(f"Endpoint: {endpoint}")
    print(f"Deployment: {deployment}")
    print(f"API Version: {api_version}")
    print(f"Key provided: {'Yes' if api_key else 'No'}")

    if not endpoint or not api_key:
        print("Missing credentials.")
        return

    base_url = endpoint.rstrip("/")
    if "openai.azure.com" in base_url:
        clean_base = base_url.split("/openai")[0] 
        url = f"{clean_base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    else:
        url = f"{base_url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    
    print(f"URL: {url}")

    headers = {
        "Content-Type": "application/json",
        "api-key": api_key
    }
    
    payload = {
        "messages": [{"role": "user", "content": "Hello, are you working? Please answer in one sentence."}],
        "max_completion_tokens": 4096
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"Status Code: {resp.status_code}")
        print(f"Response Body: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_azure()
