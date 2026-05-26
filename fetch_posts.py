import requests
from datetime import datetime, timedelta
import json
import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get API credentials from environment variables
ACCESS_TOKEN = os.getenv('LINKEDIN_ACCESS_TOKEN')
BASE_URL = "https://api.linkedin.com/rest/memberChangeLogs"
NO_POSTS_FOUND_EXIT_CODE = 2
IMAGE_EXTENSIONS = (".jpg", ".jpeg")

# Headers required for the API
headers = {
    'Authorization': f'Bearer {ACCESS_TOKEN}',
    'LinkedIn-Version': '202312'
}

# Calculate timestamp for 5 days ago
start_time = int((datetime.now() - timedelta(days=5)).timestamp() * 1000)
params = {
    'q': 'memberAndApplication',
    'count': 500,  # high enough to avoid missing the latest post
    'startTime': start_time
}


def find_images_for_date(post_date_str):
    images_dir = "images"
    image_list = []

    if os.path.isdir(images_dir):
        for filename in os.listdir(images_dir):
            if (
                filename.startswith(post_date_str)
                and filename.lower().endswith(IMAGE_EXTENSIONS)
            ):
                image_list.append(filename)

    image_list.sort()

    base_url = "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/"
    return [
        {
            "url": base_url + filename,
            "alt": ""
        }
        for filename in image_list
    ]


def fetch_last_linkedin_post():
    try:
        # API call
        response = requests.get(BASE_URL, headers=headers, params=params)
        print(f"API Response Status: {response.status_code}")

        if response.status_code != 200:
            print(f"Error: {response.status_code}", file=sys.stderr)
            print(response.text, file=sys.stderr)
            return False

        data = response.json()

        latest_post = None  # will hold the most recent post
        latest_timestamp = 0  # track the highest timestamp

        # --------------------------
        # 1. Loop through elements
        # --------------------------
        for element in data.get('elements', []):
            if (element.get('resourceName') == 'ugcPosts' and
                element.get('method') == 'CREATE'):

                # Extract the post body
                activity = element.get('activity', {})
                content = (activity.get('specificContent', {})
                                   .get('com.linkedin.ugc.ShareContent', {}))
                raw_text = content.get('shareCommentary', {}).get('text', '')

                # Transform plain text into HTML paragraphs
                paragraphs = raw_text.strip().split("\n\n")
                html_content_parts = []
                for paragraph in paragraphs:
                    # Replace single newlines with <br>
                    paragraph = paragraph.replace("\n", "<br>")
                    # Wrap each paragraph in <p>
                    html_content_parts.append(f"<p>{paragraph}</p>")
                    # Add extra empty paragraph for spacing
                    html_content_parts.append("<p>&nbsp;</p>")

                # Combine everything into one string
                html_content = "".join(html_content_parts)

                # Extract timestamp
                timestamp = element.get('capturedAt', 0)

                # If this post is newer than what we have so far, store it
                if timestamp > latest_timestamp:
                    latest_timestamp = timestamp
                    latest_post = {
                        'content': html_content,
                        'url': f"https://www.linkedin.com/feed/update/{element.get('resourceId', '')}",
                        'published_at': datetime.fromtimestamp(timestamp / 1000).isoformat()
                    }

        # ---------------------------------------
        # 2. If we found a post, process it
        # ---------------------------------------
        if latest_post:
            # Convert the 'published_at' to datetime
            published_at_str = latest_post["published_at"]
            post_datetime = datetime.fromisoformat(published_at_str)
            # Format to YYYY-MM-DD
            post_date_str = post_datetime.strftime("%Y-%m-%d")

            # ---------------------------------------
            # 3. Find matching images in /images folder
            # ---------------------------------------
            latest_post["images"] = find_images_for_date(post_date_str)

            # ---------------------------------------
            # 4. Save to JSON file
            # ---------------------------------------
            with open('last_linkedin_post.json', 'w', encoding='utf-8') as f:
                json.dump(latest_post, f, ensure_ascii=False, indent=2)

            print("\nMost recent post has been saved to 'last_linkedin_post.json'")
            print("\nPost data:")
            print(json.dumps(latest_post, indent=2))

            return latest_post

        # If we did not find any posts, stop before stale data can be posted.
        print("No LinkedIn posts found in the configured lookback window.")
        return None

    except Exception as e:
        print(f"An error occurred: {str(e)}", file=sys.stderr)
        return False


if __name__ == "__main__":
    last_post = fetch_last_linkedin_post()
    if last_post is False:
        sys.exit(1)
    if last_post is None:
        sys.exit(NO_POSTS_FOUND_EXIT_CODE)
