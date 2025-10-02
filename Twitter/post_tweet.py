# post_tweet.py
# Reads tweet.json and posts it to X using the v2 API.
# - Handles image uploads with alt text.
# - Posts the tweet with text and attached media.

import os
import sys
import json
import requests
import io
from pathlib import Path
from dotenv import load_dotenv
import tweepy

# --- Configuration ---
REPO_ROOT = Path(__file__).resolve().parent
TWEET_JSON_PATH = REPO_ROOT / "tweet.json"
LAST_LINKEDIN_PATH = REPO_ROOT.parent / "last_linkedin_post.json"

# --- Main Functions ---

def upload_image(api: tweepy.API, image_data: dict) -> str | None:
    """Downloads an image from a URL and uploads it to X using the v1.1 API, returning a media_id."""
    url = image_data.get("url")
    alt_text = image_data.get("alt", "")
    if not url:
        return None

    try:
        print(f"🖼️  Downloading image: {url}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        print("   Uploading to X...")
        # MODIFIED: Use the v1.1 API client and the correct method 'media_upload'
        # We pass the filename and the file-like object.
        media = api.media_upload(
            filename="image.jpg",
            file=io.BytesIO(response.content)
        )
        media_id = media.media_id_string
        
        # Attach alt text if available using the v1.1 endpoint
        if alt_text:
            api.create_media_metadata(media_id, alt_text)
            print(f"   Added ALT text: {alt_text[:50]}...")
            
        print(f"   ✅ Upload successful. Media ID: {media_id}")
        return media_id

    except requests.exceptions.RequestException as e:
        print(f"   ❌ Failed to download image {url}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"   ❌ Failed to upload image {url}: {e}", file=sys.stderr)
    
    return None


def main():
    """Main function to load, process, and post the tweet."""
    print("--- Starting Tweet Poster ---")
    load_dotenv()

    # 1. Check for X API credentials
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_token_secret = os.getenv("X_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_token_secret]):
        print("❌ Missing X API credentials in .env file.", file=sys.stderr)
        sys.exit(1)

    # 2. Load the tweet data from tweet.json
    if not TWEET_JSON_PATH.exists():
        print(f"❌ Input file not found: {TWEET_JSON_PATH.name}", file=sys.stderr)
        sys.exit(1)
        
    try:
        tweet_data = json.loads(TWEET_JSON_PATH.read_text(encoding="utf-8"))
        tweet_text = tweet_data.get("content", "").strip()
        images_to_upload = tweet_data.get("images", [])
    except Exception as e:
        print(f"❌ Failed to parse {TWEET_JSON_PATH.name}: {e}", file=sys.stderr)
        sys.exit(1)

    # ---- Early exit: nothing new to post ----
    # If tweet.json exists and its URL equals the current LinkedIn URL, skip posting.
    try:
        last_src = json.loads(LAST_LINKEDIN_PATH.read_text(encoding="utf-8")) if LAST_LINKEDIN_PATH.exists() else None
    except Exception:
        last_src = None  # if last_linkedin_post.json is corrupted, proceed (we'll rely on tweet.json only)

    tweet_url_field = (tweet_data or {}).get("url", "")
    last_url_field = (last_src or {}).get("url", "")

    if not os.getenv("FORCE_POST") and tweet_url_field and last_url_field and tweet_url_field == last_url_field:
        print("⏭️  No new LinkedIn post detected (same URL as tweet.json). Skipping X post.")
        sys.exit(0)


    if not tweet_text:
        print("❌ Tweet content is empty. Nothing to post.", file=sys.stderr)
        sys.exit(1)
        
    # 3. Authenticate with BOTH X API versions
    try:
        # MODIFIED: Create a v1.1 API object for media uploads
        auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
        api_v1 = tweepy.API(auth)
        
        # MODIFIED: Create a v2 Client object for posting the tweet
        client_v2 = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret
        )
        print("🔐 Authenticated with X API successfully.")
    except Exception as e:
        print(f"❌ Failed to authenticate with X API: {e}", file=sys.stderr)
        sys.exit(1)

    # 4. Upload images using the v1.1 client
    media_ids = []
    if images_to_upload:
        print(f"Found {len(images_to_upload)} images to upload.")
        for image_info in images_to_upload:
            # MODIFIED: Pass the v1.1 api object to the upload function
            media_id = upload_image(api_v1, image_info)
            if media_id:
                media_ids.append(media_id)
    
    if images_to_upload and not media_ids:
        print("⚠️ Images were found but none could be uploaded. Posting tweet without images.", file=sys.stderr)

    # 5. Create the tweet using the v2 client
    try:
        print("\n🐦 Posting tweet...")
        response = client_v2.create_tweet(
            text=tweet_text,
            media_ids=media_ids if media_ids else None
        )
        tweet_id = response.data['id']
        tweet_url = f"https://x.com/user/status/{tweet_id}"
        
        print("\n✨ Success! ✨")
        print(f"Tweet posted successfully. View it here:")
        print(tweet_url)

    except tweepy.errors.Forbidden as e:
        # Handle the specific duplicate content error
        if "duplicate content" in str(e).lower():
            print("❌ Failed to post tweet: You are not allowed to create a Tweet with duplicate content.", file=sys.stderr)
        else:
            print(f"❌ Failed to post tweet (403 Forbidden): {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to post tweet: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()