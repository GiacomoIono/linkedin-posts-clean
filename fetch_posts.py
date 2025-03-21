import requests
from datetime import datetime, timedelta
import json
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get API credentials from environment variables
ACCESS_TOKEN = os.getenv('LINKEDIN_ACCESS_TOKEN')
BASE_URL = "https://api.linkedin.com/rest/memberChangeLogs"

# Headers required for the API
headers = {
    'Authorization': f'Bearer {ACCESS_TOKEN}',
    'LinkedIn-Version': '202312'
}

# Calculate timestamp for 3 days ago
start_time = int((datetime.now() - timedelta(days=3)).timestamp() * 1000)
params = {
    'q': 'memberAndApplication',
    'count': 200,  # you could reduce this, but 50 ensures you don’t miss the latest post
    'startTime': start_time
}

def fetch_last_linkedin_post():
    try:
        # API call
        response = requests.get(BASE_URL, headers=headers, params=params) 
        print(f"API Response Status: {response.status_code}")

        if response.status_code == 200:
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
                images_dir = "images"
                image_list = []
                
                if os.path.isdir(images_dir):
                    for filename in os.listdir(images_dir):
                        # e.g. if filename is "2025-03-06.jpeg"
                        if (filename.startswith(post_date_str) and 
                            filename.lower().endswith(".jpeg")):
                            image_list.append(filename)

                image_list.sort()

                # ---------------------------------------
                # 4. Prepend base URL to each image
                # ---------------------------------------
                base_url = "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/"
                full_url_list = []
                for fn in image_list:
                    full_image_url = base_url + fn
                    full_url_list.append({
                        "url": full_image_url,
                        "alt": ""
                    })

                # ---------------------------------------
                # 5. Insert the array of images
                # ---------------------------------------
                latest_post["images"] = full_url_list


                # ---------------------------------------
                # 6. Save to JSON file
                # ---------------------------------------
                with open('last_linkedin_post.json', 'w', encoding='utf-8') as f:
                    json.dump(latest_post, f, ensure_ascii=False, indent=2)

                print("\nMost recent post has been saved to 'last_linkedin_post.json'")
                print("\nPost data:")
                print(json.dumps(latest_post, indent=2))

                return latest_post

            # If we didn’t find any posts
            print("No posts found")
            return None

        else:
            print(f"Error: {response.status_code}")
            print(response.text)
            return None

    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return None

if __name__ == "__main__":
    last_post = fetch_last_linkedin_post()