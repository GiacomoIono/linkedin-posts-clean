from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .config import IMAGE_DIR

LINKEDIN_CHANGE_LOG_URL = "https://api.linkedin.com/rest/memberChangeLogs"
LINKEDIN_VERSION = "202312"
LINKEDIN_PAGE_SIZE = 50
LINKEDIN_MAX_ATTEMPTS = 3
LINKEDIN_RETRY_BACKOFF_SECONDS = 1
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
RAW_IMAGE_BASE_URL = "https://raw.githubusercontent.com/GiacomoIono/linkedin-posts-clean/refs/heads/main/images/"
IMAGE_SEQUENCE_RE = re.compile(r"_(\d+)(?=\.[^.]+$)")
IMAGE_MEDIA_CATEGORIES = {"IMAGE", "CAROUSEL", "MULTI_IMAGE"}
NON_IMAGE_MEDIA_CATEGORIES = {"NONE", "VIDEO"}


def image_filename_sort_key(filename: str) -> tuple[int, int, str]:
    match = IMAGE_SEQUENCE_RE.search(filename.lower())
    if not match:
        return (1, 0, filename)
    return (0, int(match.group(1)), filename)


def find_images_for_date(post_date: str) -> list[dict[str, str]]:
    if not IMAGE_DIR.is_dir():
        return []

    filenames = []
    # Direct children only: the nested images/generated/ directory is never source media.
    for item in IMAGE_DIR.iterdir():
        if not (
            item.is_file()
            and item.name.startswith(post_date)
            and item.name.lower().endswith(IMAGE_EXTENSIONS)
        ):
            continue
        filenames.append(item.name)
    filenames.sort(key=image_filename_sort_key)

    return [{"url": RAW_IMAGE_BASE_URL + filename, "alt": ""} for filename in filenames]


def paragraph_html(raw_text: str) -> str:
    paragraphs = []
    for paragraph in (raw_text or "").strip().split("\n\n"):
        cleaned = paragraph.strip()
        if not cleaned:
            continue
        paragraphs.append(f"<p>{cleaned.replace(chr(10), '<br>')}</p>")
        paragraphs.append("<p>&nbsp;</p>")
    return "".join(paragraphs)


def linkedin_share_has_image(content: dict[str, Any]) -> bool:
    category = str(content.get("shareMediaCategory") or "").strip().upper()
    if category in IMAGE_MEDIA_CATEGORIES:
        return True
    if category in NON_IMAGE_MEDIA_CATEGORIES:
        return False

    media = content.get("media")
    if not isinstance(media, list):
        return False
    return any(
        isinstance(item, dict) and bool(item.get("thumbnails") or item.get("thumbnail"))
        for item in media
    )


def extract_post(element: dict[str, Any]) -> dict[str, Any] | None:
    if element.get("resourceName") != "ugcPosts" or element.get("method") != "CREATE":
        return None

    activity = element.get("activity", {})
    content = activity.get("specificContent", {}).get(
        "com.linkedin.ugc.ShareContent", {}
    )
    raw_text = content.get("shareCommentary", {}).get("text", "")
    timestamp = int(element.get("capturedAt") or 0)
    resource_id = element.get("resourceId", "")

    if not raw_text.strip() or not timestamp or not resource_id:
        return None

    published_at = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
    post_date = published_at.strftime("%Y-%m-%d")
    post_url = f"https://www.linkedin.com/feed/update/{resource_id}"

    return {
        "content": paragraph_html(raw_text),
        "url": post_url,
        "published_at": published_at.isoformat().replace("+00:00", ""),
        "images": find_images_for_date(post_date),
        "linkedin_has_image": linkedin_share_has_image(content),
    }


def request_linkedin_page(headers: dict[str, str], params: dict[str, Any]):
    for attempt in range(1, LINKEDIN_MAX_ATTEMPTS + 1):
        response = requests.get(
            LINKEDIN_CHANGE_LOG_URL, headers=headers, params=params, timeout=30
        )
        print(
            "LinkedIn API response status: "
            f"{response.status_code} (start={params['start']}, attempt={attempt}/{LINKEDIN_MAX_ATTEMPTS})"
        )
        if response.status_code == 200:
            return response

        if response.status_code != 500 or attempt == LINKEDIN_MAX_ATTEMPTS:
            raise RuntimeError(
                f"LinkedIn API failed: {response.status_code} {response.text}"
            )

        delay = LINKEDIN_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
        print(f"LinkedIn API returned 500. Retrying in {delay} second(s).")
        time.sleep(delay)

    raise RuntimeError("LinkedIn API failed without returning a response.")


def fetch_latest_linkedin_post(
    access_token: str, lookback_hours: int = 48
) -> dict[str, Any] | None:
    if not access_token:
        raise RuntimeError("LINKEDIN_ACCESS_TOKEN is missing.")

    start_time = int(
        (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp()
        * 1000
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": LINKEDIN_VERSION,
    }

    latest_post = None
    latest_timestamp = -1
    page_start = 0

    while True:
        params = {
            "q": "memberAndApplication",
            "count": LINKEDIN_PAGE_SIZE,
            "start": page_start,
            "startTime": start_time,
        }
        response = request_linkedin_page(headers, params)
        elements = response.json().get("elements", [])
        if not isinstance(elements, list):
            raise RuntimeError("LinkedIn API failed: response elements must be a list.")

        page_processed_timestamps = []
        for element in elements:
            if not isinstance(element, dict):
                continue

            processed_timestamp = int(element.get("processedAt") or 0)
            if processed_timestamp:
                page_processed_timestamps.append(processed_timestamp)

            timestamp = int(element.get("capturedAt") or 0)
            post = extract_post(element)
            if post and timestamp > latest_timestamp:
                latest_post = post
                latest_timestamp = timestamp

        page_is_older_than_lookback = (
            bool(page_processed_timestamps)
            and max(page_processed_timestamps) < start_time
        )
        if page_is_older_than_lookback:
            print(
                f"Reached LinkedIn records older than the {lookback_hours}-hour lookback window. "
                "Stopping pagination."
            )
            break

        if len(elements) < LINKEDIN_PAGE_SIZE:
            break

        page_start += LINKEDIN_PAGE_SIZE

    return latest_post
