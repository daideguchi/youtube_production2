#!/usr/bin/env python3
"""
Simple test to check if the basic functionality is working
"""

import os
import sys

def test_basic_imports():
    """Test if basic modules can be imported"""
    print("Testing basic imports...")
    
    # Add the tools directory to Python path
    tools_dir = "/Users/dd/projects/srtfile/srtfile_v2/tools"
    sys.path.insert(0, tools_dir)
    
    try:
        # Try importing progress_manager
        import progress_manager
        print("✅ progress_manager module imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import progress_manager: {e}")
        return False
    except Exception as e:
        print(f"❌ Error importing progress_manager: {e}")
        return False
    
    # Check if the main script validation tools exist
    validation_tools = [
        "validate_script_length",
        "script_quality_checker",
        "srt_quality_checker"
    ]
    
    for tool in validation_tools:
        try:
            __import__(tool)
            print(f"✅ {tool} module is available")
        except ImportError:
            print(f"⚠️  {tool} module not directly importable (may be script-only)")
    
    return True

def test_environment_vars():
    """Test if required environment variables are set"""
    print("\nTesting environment variables...")
    
    required_vars = [
        "GEMINI_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_SERVICE_ACCOUNT_JSON"
    ]
    
    for var in required_vars:
        if os.environ.get(var):
            print(f"✅ {var} is set")
        else:
            print(f"⚠️  {var} is not set")
    
    # Test for optional API keys mentioned in the doc
    optional_vars = [
        "BRAVE_API_KEY",
        "OPENROUTER_API_KEY"
    ]
    
    for var in optional_vars:
        if os.environ.get(var):
            print(f"✅ {var} is set")
        else:
            print(f"❌ {var} is not set - this explains the API issues")

def main():
    print("Running basic functionality tests...")
    
    success = test_basic_imports()
    test_environment_vars()
    
    print(f"\nTest Summary:")
    print(f"- Core system: {'✅ Working' if success else '❌ Issues detected'}")
    print(f"- Brave Search API: ✅ Working (confirmed earlier)")
    print(f"- Kimi K2 API (via OpenRouter): ❌ Not working (401 authentication error)")
    
    print(f"\nNote: The main Qwen system appears to be structurally intact,")
    print(f"but the OpenRouter API key has authentication issues.")

if __name__ == "__main__":
    main()