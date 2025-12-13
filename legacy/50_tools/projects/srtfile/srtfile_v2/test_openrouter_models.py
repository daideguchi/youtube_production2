import os
import requests
from openai import OpenAI

def test_openrouter_models():
    """Test if other models on OpenRouter work with the API key"""
    print("Testing OpenRouter API access with various models...")
    
    # Get the OpenRouter API key
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        print("❌ OPENROUTER_API_KEY not found in environment variables")
        return
    
    client = OpenAI(
        base_url='https://openrouter.ai/api/v1',
        api_key=api_key,
    )
    
    # List of models to test
    models_to_test = [
        'openai/gpt-3.5-turbo',
        'google/gemini-pro',
        'anthropic/claude-3-haiku',  # This one should be available to most accounts
    ]
    
    for model in models_to_test:
        try:
            print(f"\nTesting model: {model}")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {'role': 'user', 'content': 'Hello, are you working? Please respond with just "OK".'}
                ]
            )
            
            if response.choices[0].message.content:
                print(f"✅ {model} is accessible")
                print(f"Response: {response.choices[0].message.content}")
            else:
                print(f"❌ {model} returned empty response")
                
        except Exception as e:
            print(f"❌ {model} error: {e}")

def test_kimi_specific():
    """Test Kimi K2 specifically"""
    print("\n" + "="*50)
    print("Testing Kimi K2 model specifically...")
    
    # Get the OpenRouter API key
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        print("❌ OPENROUTER_API_KEY not found in environment variables")
        return
    
    client = OpenAI(
        base_url='https://openrouter.ai/api/v1',
        api_key=api_key,
    )
    
    try:
        # Try the Kimi K2 model again
        response = client.chat.completions.create(
            model='moonshotai/kimi-k2:free',
            messages=[
                {'role': 'user', 'content': 'こんにちは、正常に応答できますか？'}
            ]
        )
        
        if response.choices[0].message.content:
            print("✅ Kimi K2 is accessible")
            print(f"Response: {response.choices[0].message.content}")
        else:
            print("❌ Kimi K2 returned empty response")
            
    except Exception as e:
        print(f"❌ Kimi K2 error: {e}")
        # Print the full error for debugging
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("Testing OpenRouter API connectivity...")
    test_openrouter_models()
    test_kimi_specific()