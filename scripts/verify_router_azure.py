import sys
import os
import logging
from pathlib import Path

# Add project root
sys.path.append(os.getcwd())

from factory_common.llm_router import get_router

# Setup logging to console
logging.basicConfig(level=logging.INFO)

def test_router_azure():
    print("Testing LLMRouter with Azure model directly...")
    
    # Manually load env to ensure it's available (Router relies on os.getenv)
    env_path = Path(".env")
    if env_path.exists():
        print(f"Loading .env from {env_path}")
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()
    
    router = get_router()
    
    # Force specific model by inspecting internals or temporary config hack?
    # Better: LLMRouter doesn't expose "call specific model" publicly, only "call task".
    # But we can instantiate the adapter directly.
    
    from factory_common.llm_router import AzureAdapter
    
    # Load config manually to pass to adapter
    import yaml
    with open("configs/llm_router.yaml", 'r') as f:
        config = yaml.safe_load(f)
        
    provider_cfg = config["providers"]["azure"]
    model_cfg = config["models"]["azure_gpt5_mini"]
    
    print(f"Provider Config: {provider_cfg}")
    print(f"Model Config: {model_cfg}")
    
    try:
        adapter = AzureAdapter(provider_cfg, model_cfg)
        print("Adapter initialized.")
        
        response = adapter.call([{"role": "user", "content": "Hello, Azure!"}])
        print(f"Response: {response}")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_router_azure()
