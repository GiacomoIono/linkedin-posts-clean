# check_linkedin_token.py
# A simple script to check the validity of the LinkedIn Access Token.

import os
import requests
import json
from dotenv import load_dotenv

def check_token():
    """Loads the token from .env and makes a test call to the LinkedIn API."""
    print("--- LinkedIn Token Checker ---")
    
    # 1. Load environment variables from .env
    load_dotenv()
    token = os.getenv("LINKEDIN_ACCESS_TOKEN")

    if not token:
        print("❌ ERROR: LINKEDIN_ACCESS_TOKEN not found in your .env file.")
        return

    print("🔑 Token loaded successfully from .env file.")

    # 2. Prepare the API request details
    url = "https://api.linkedin.com/rest/memberChangeLogs?q=memberAndApplication&count=10"
    headers = {
        'Authorization': f'Bearer {token}',
        'LinkedIn-Version': '202312'
    }

    print(f"📞 Calling LinkedIn API...")

    # 3. Make the API call and print the results
    try:
        response = requests.get(url, headers=headers)
        
        print(f"\nHTTP Status Code: {response.status_code}")
        print("--- Full API Response ---")
        print(json.dumps(response.json(), indent=2))
        print("------------------------")
        
        if response.status_code == 200:
            print("\n✅ RESULT: Your token is VALID.")
        elif response.status_code == 401:
            print("\n❌ RESULT: Your token is INVALID or EXPIRED. Please generate a new one from the LinkedIn Developer Portal.")
        else:
            print(f"\n⚠️  An unexpected error occurred.")

    except requests.exceptions.RequestException as e:
        print(f"A network error occurred: {e}")

if __name__ == "__main__":
    check_token()