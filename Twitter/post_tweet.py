# post_tweet.py
# Reads tweet.json and posts it to X using the v2 API.
# - Handles image uploads with alt text.
# - Posts the tweet with text and attached media.
# - X posting is optional by default so Webflow CMS JSON publishing is never blocked.

import os
import sys
import json
import requests
import io
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import tweepy

# --- Configuration ---
REPO_ROOT = Path(__file__).resolve().parent
TWEET_JSON_PATH = REPO_ROOT / "tweet.json"
POSTED_TWEETS_PATH = REPO_ROOT / "posted_tweets.json"


def require_x_posting() -> bool:
    return os.getenv("REQUIRE_X_POSTING", "").strip().lower() in {"1", "true", "yes"}


def stop_for_x_issue(message: str, exit_code: int = 1) -> None:
    """Log an X/Twitter issue without failing the CMS pipeline unless strict mode is enabled."""
    print(message, file=sys.stderr)
    if require_x_posting():
        sys.exit(exit_code)

    print("⚠️ X posting failed or was skipped, but this is optional. Continuing so the Webflow CMS JSON can publish.")
    sys.exit(0)


# --- Main Functions ---

def load_posted_tweets() -> dict:
    """Loads the local posting ledger used to avoid reposting a LinkedIn URL."""
    if not POSTED_TWEETS_PATH.exists():
        return {"posted": []}

    try:
        data = json.loads(POSTED_TWEETS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        stop_for_x_issue(f"❌ Failed to parse {POSTED_TWEETS_PATH.name}: {e}")

    if isinstance(data, list):
        data = {"posted": data}

    if not isinstance(data, dict) or not isinstance(data.get("posted"), list):
        stop_for_x_issue(f"❌ {POSTED_TWEETS_PATH.name} must contain a JSON object with a 'posted' array.")

    return data


def find_posted_tweet(posted_doc: dict, linkedin_url: str) -> dict | None:
    for item in posted_doc.get("posted", []):
        if isinstance(item, dict) and item.get("linkedin_url") == linkedin_url:
            return item
    return None


def save_posted_tweets(posted_doc: dict) -> None:
    POSTED_TWEETS_PATH.write_text(
        json.dumps(posted_doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )


def record_posted_tweet(posted_doc: dict, linkedin_url: str, tweet_id: str, tweet_url: str) -> None:
    if not linkedin_url:
        return

    posted_doc.setdefault("posted", []).append({
        "linkedin_url": linkedin_url,
        "tweet_id": tweet_id,
        "tweet_url": tweet_url,
        "posted_at": datetime.now(timezone.utc).isoformat()
    })
    save_posted_tweets(posted_doc)


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
        # Use the v1.1 API client for media uploads.
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
        stop_for_x_issue("❌ Missing X API credentials in .env file.")

    # 2. Load the tweet data from tweet.json
    if not TWEET_JSON_PATH.exists():
        stop_for_x_issue(f"❌ Input file not found: {TWEET_JSON_PATH.name}")

    try:
        tweet_data = json.loads(TWEET_JSON_PATH.read_text(encoding="utf-8"))
        tweet_text = tweet_data.get("content", "").strip()
        linkedin_url = tweet_data.get("url", "").strip()
        images_to_upload = tweet_data.get("images", [])
    except Exception as e:
        stop_for_x_issue(f"❌ Failed to parse {TWEET_JSON_PATH.name}: {e}")

    if not tweet_text:
        stop_for_x_issue("❌ Tweet content is empty. Nothing to post.")

    # ---- Early exit: this LinkedIn URL has already been posted to X ----
    posted_doc = load_posted_tweets()
    if not os.getenv("FORCE_POST") and linkedin_url:
        existing_post = find_posted_tweet(posted_doc, linkedin_url)
        if existing_post:
            print("⏭️  LinkedIn URL already posted to X. Skipping X post.")
            if existing_post.get("tweet_url"):
                print(existing_post["tweet_url"])
            sys.exit(0)

    # 3. Authenticate with BOTH X API versions
    try:
        # Create a v1.1 API object for media uploads
        auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
        api_v1 = tweepy.API(auth)

        # Create a v2 Client object for posting the tweet
        client_v2 = tweepy.Client(
            consumer_key=api_key,
            consumer_secret=api_secret,
            access_token=access_token,
            access_token_secret=access_token_secret
        )
        print("🔐 Authenticated with X API successfully.")
    except Exception as e:
        stop_for_x_issue(f"❌ Failed to authenticate with X API: {e}")

    # 4. Upload images using the v1.1 client
    media_ids = []
    if images_to_upload:
        print(f"Found {len(images_to_upload)} images to upload.")
        for image_info in images_to_upload:
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
        tweet_url = f"https://x.com/i/web/status/{tweet_id}"

        record_posted_tweet(posted_doc, linkedin_url, tweet_id, tweet_url)

        print("\n✨ Success! ✨")
        print("Tweet posted successfully. View it here:")
        print(tweet_url)

    except tweepy.errors.Forbidden as e:
        # Handle the specific duplicate content error
        if "duplicate content" in str(e).lower():
            stop_for_x_issue("❌ Failed to post tweet: You are not allowed to create a Tweet with duplicate content.")
        else:
            stop_for_x_issue(f"❌ Failed to post tweet (403 Forbidden): {e}")
    except Exception as e:
        stop_for_x_issue(f"❌ Failed to post tweet: {e}")


if __name__ == "__main__":
    main()
