import os
import requests
from openai import OpenAI

def test_kimi_k2():
    """Test if Kimi K2 API can be accessed via OpenRouter"""
    print("Testing Kimi K2 API access...")
    
    # Get the OpenRouter API key
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        print("❌ OPENROUTER_API_KEY not found in environment variables")
        return False
    
    try:
        client = OpenAI(
            base_url='https://openrouter.ai/api/v1',
            api_key=api_key,
        )
        
        response = client.chat.completions.create(
            model='moonshotai/kimi-k2:free',
            messages=[
                {'role': 'user', 'content': 'こんにちは、正常に応答できますか？'}
            ]
        )
        
        if response.choices[0].message.content:
            print("✅ Kimi K2 API is accessible")
            print(f"Response: {response.choices[0].message.content}")
            return True
        else:
            print("❌ Kimi K2 API returned empty response")
            return False
            
    except Exception as e:
        print(f"❌ Kimi K2 API error: {e}")
        return False

def test_brave_search():
    """Test if Brave Search API can be accessed"""
    print("\nTesting Brave Search API access...")
    
    # Get the Brave API key
    api_key = os.environ.get('BRAVE_API_KEY')
    if not api_key:
        print("❌ BRAVE_API_KEY not found in environment variables")
        return False
    
    try:
        headers = {
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'X-Subscription-Token': api_key
        }
        
        url = f"https://api.search.brave.com/res/v1/web/search?q=test"
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            json_response = response.json()
            if 'web' in json_response and 'results' in json_response['web']:
                print("✅ Brave Search API is accessible")
                # Just show a quick confirmation without too much detail
                print("Brave Search test completed successfully")
                return True
            else:
                print("❌ Brave Search API returned unexpected response format")
                return False
        else:
            print(f"❌ Brave Search API error: Status code {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Brave Search API error: {e}")
        return False

if __name__ == "__main__":
    print("Testing API connectivity...")
    kimi_success = test_kimi_k2()
    brave_success = test_brave_search()
    
    print(f"\nAPI Test Results:")
    print(f"Kimi K2 API: {'✅ Success' if kimi_success else '❌ Failed'}")
    print(f"Brave Search API: {'✅ Success' if brave_success else '❌ Failed'}")
    
    if kimi_success and brave_success:
        print("\n🎉 Both APIs are working correctly!")
    else:
        print("\n⚠️  Some APIs are not working correctly.")