import os
import requests
import json
from pathlib import Path

def load_env():
    env_path = Path("/Users/dd/10_YouTube_Automation/factory_commentary/.env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

def test_azure():
    load_env()
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION") # 2025-04-01-preview from env

    print(f"Endpoint: {endpoint}")
    print(f"Deployment: {deployment}")
    print(f"Version: {api_version}")
    
    if not api_key or not endpoint:
        print("Missing credentials")
        return

    base_url = endpoint.rstrip("/")
    # Standard Azure OpenAI URL construction
    url = f"{base_url}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
    
    print(f"Testing URL: {url}")
    
    headers = {
        "Content-Type": "application/json",
        "api-key": api_key
    }
    payload = {
        "messages": [{"role": "user", "content": "Hi"}],
        "max_completion_tokens": 100
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_azure()
