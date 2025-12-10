import os
import yaml
import time
import json
import logging
from typing import Dict, Any, List, Optional, Union
from pathlib import Path
from dotenv import load_dotenv

# Try importing OpenAI
try:
    from openai import OpenAI, AzureOpenAI
except ImportError:
    OpenAI = None
    AzureOpenAI = None

# Try importing Gemini
try:
    import google.generativeai as genai
except ImportError:
    genai = None

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMRouter")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs" / "llm_router.yaml"
ENV_PATH = PROJECT_ROOT / ".env"

def _load_env_forced():
    """Load .env file and OVERWRITE existing env vars to ensure SSOT."""
    if ENV_PATH.exists():
        # Using python-dotenv with override=True
        load_dotenv(dotenv_path=ENV_PATH, override=True)
    else:
        logger.warning(f".env not found at {ENV_PATH}")

class LLMRouter:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LLMRouter, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        _load_env_forced()
        self.config = self._load_config()
        self._setup_clients()
        self._initialized = True

    def _load_config(self) -> Dict[str, Any]:
        if not CONFIG_PATH.exists():
            raise FileNotFoundError(f"Router config not found at {CONFIG_PATH}")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _setup_clients(self):
        self.clients = {}
        providers = self.config.get("providers", {})

        # Azure
        if "azure" in providers:
            p = providers["azure"]
            ep = os.getenv(p.get("env_endpoint"))
            key = os.getenv(p.get("env_api_key"))
            ver = p.get("default_api_version")
            if ep and key and AzureOpenAI:
                # Handle missing protocol in endpoint if common
                if not ep.startswith("http"):
                    ep = "https://" + ep
                
                # Fix: Strip trailing paths from endpoint for SDK
                # The AzureOpenAI client expects the base endpoint (e.g. https://foo.openai.azure.com/)
                # It appends /openai/deployments/... itself.
                # If users put full path in .env, we should clean it.
                if "/openai/" in ep:
                    ep = ep.split("/openai/")[0]
                
                self.clients["azure"] = AzureOpenAI(
                    api_key=key,
                    api_version=ver,
                    azure_endpoint=ep
                )

        # OpenRouter
        if "openrouter" in providers:
            p = providers["openrouter"]
            key = os.getenv(p.get("env_api_key"))
            base = p.get("base_url")
            if key and OpenAI:
                self.clients["openrouter"] = OpenAI(
                    api_key=key,
                    base_url=base
                )

        # Gemini
        if "gemini" in providers:
            p = providers["gemini"]
            key = os.getenv(p.get("env_api_key"))
            if key and genai:
                genai.configure(api_key=key)
                self.clients["gemini"] = "configured" # Client is static

    def get_models_for_task(self, task: str) -> List[str]:
        task_conf = self.config.get("tasks", {}).get(task)
        if not task_conf:
            logger.warning(f"Task '{task}' not defined in config. Using fallback standard tier.")
            tier = "standard"
        else:
            tier = task_conf.get("tier")
        
        return self.config.get("tiers", {}).get(tier, [])

    def call(self, 
             task: str, 
             messages: List[Dict[str, str]], 
             system_prompt_override: Optional[str] = None,
             temperature: Optional[float] = None,
             max_tokens: Optional[int] = None,
             response_format: Optional[str] = None,
             **kwargs) -> Any:
        
        models = self.get_models_for_task(task)
        if not models:
            raise ValueError(f"No models available for task: {task}")

        last_error = None

        # System Prompt Injection/Override logic
        # If task has system_prompt_override in config, prepend it?
        # Usually messages already contain system prompt.
        # But if 'system_prompt_override' arg is passed, we might want to replace the first system message.
        if system_prompt_override:
            # Check if messages[0] is system
            if messages and messages[0]['role'] == 'system':
                messages[0]['content'] = system_prompt_override
            else:
                messages.insert(0, {"role": "system", "content": system_prompt_override})

        for model_key in models:
            model_conf = self.config.get("models", {}).get(model_key)
            if not model_conf:
                continue

            provider_name = model_conf.get("provider")
            client = self.clients.get(provider_name)
            
            if not client:
                logger.debug(f"Client for {provider_name} not ready. Skipping {model_key}")
                continue

            try:
                logger.info(f"Router: Invoking {model_key} for {task}...")
                return self._invoke_provider(
                    provider_name, 
                    client, 
                    model_conf, 
                    messages, 
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    **kwargs
                )
            except Exception as e:
                logger.warning(f"Failed to call {model_key}: {e}")
                last_error = e
                # Fallback to next model
                continue
        
        raise RuntimeError(f"All models failed for task '{task}'. Last error: {last_error}")

    def _invoke_provider(self, provider, client, model_conf, messages, **kwargs):
        cap = model_conf.get("capabilities", {})
        mode = cap.get("mode", "chat")
        
        # Merge defaults
        defaults = model_conf.get("defaults", {})
        params = {**defaults, **kwargs}
        
        # Clean params based on args
        # e.g. if temperature passed explicitly, use it.
        # kwargs already has precedence in python dict merge if we did defaults | kwargs
        # But here we do explicit args.
        
        # Prepare API call args
        api_args = {}
        
        # Parameters Filter for Reasoning Models
        # Reasoning models (o1, gpt-5-mini) have strict parameter constraints.
        # They often reject: temperature, top_p, frequency_penalty, presence_penalty, logprobs
        is_reasoning_model = cap.get("reasoning", False)
        
        # List of params to exclude for reasoning models
        # (We only include them if NOT reasoning model)
        standard_params = ["temperature", "top_p", "frequency_penalty", "presence_penalty"]
        
        for param in standard_params:
            if not is_reasoning_model:
                # Check params dict first, then kwargs
                if param in params and params[param] is not None:
                     api_args[param] = params[param]
                elif param in kwargs and kwargs[param] is not None:
                     api_args[param] = kwargs[param]
            else:
                # For reasoning models, strictly ignore these params to avoid 400 errors.
                pass

        # Max Tokens (Reasoning models use max_completion_tokens)
        if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
            # Check model limit?
             api_args["max_completion_tokens" if provider == "azure" else "max_tokens"] = kwargs["max_tokens"]

        # Response Format (JSON)
        if kwargs.get("response_format") == "json_object":
            if cap.get("json_mode"):
                api_args["response_format"] = {"type": "json_object"}
            else:
                # Model doesn't support native JSON mode
                # Just append instruction to prompt?
                # Or trust the prompt has it.
                # For models without native JSON support, we'll rely on prompt engineering
                pass

        # IMAGE GENERATION
        if mode == "image_generation":
            return self._invoke_image_gen(provider, client, model_conf, messages, **kwargs)

        # TEXT/CHAT
        model_name = model_conf.get("deployment") if provider == "azure" else model_conf.get("model_name")
        
        if provider == "azure":
             # Azure specific
             pass
        
        # Common OpenAI/Azure Interface
        if provider in ["azure", "openrouter"]:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                **api_args
            )
            return response.choices[0].message.content

        # Gemini Chat (Not implemented fully in config yet for Text, but ready)
        if provider == "gemini":
            # Convert messages to Gemini format
            # This is complex, skipping for now as we don't use Gemini for text in this config
            raise NotImplementedError("Gemini Text not supported yet")

    def _invoke_image_gen(self, provider, client, model_conf, messages, **kwargs):
        if provider != "gemini":
            raise NotImplementedError("Only Gemini supported for image gen currently")
        
        # Extract prompt from messages
        # Usually the last user message
        prompt = ""
        for m in reversed(messages):
            if m["role"] == "user":
                prompt = m["content"]
                break
        
        if not prompt:
            raise ValueError("No prompt found for image generation")

        model_name = model_conf.get("model_name")
        
        # GenAI call
        model = genai.GenerativeModel(model_name)
        
        # Config
        aspect_ratio = kwargs.get("aspect_ratio", "16:9")
        # Gemini currently doesn't support aspect ratio via API in standard way?
        # Actually it does in newer versions via generation_config
        # But let's keep it simple for now or use the prompt injection
        
        full_prompt = f"{prompt} --aspect_ratio {aspect_ratio}" # Prompt injection for safety if model follows it
        
        # Actual API might differ based on library version. 
        # Assuming generate_content returning image data or url?
        # Gemini 2.5 Flash Image might return PIL image.
        
        response = model.generate_content(prompt)
        # Verify response
        # This part depends heavily on the specific Gemini model output format
        # For 'flash-image', it might return an image object part.
        
        # For now, let's assume we return the result object and let caller handle
        # OR better: return a standardized dict
        
        return response

def get_router():
    return LLMRouter()
