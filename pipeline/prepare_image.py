from __future__ import annotations

import sys

from .config import (
    NO_POSTS_FOUND_EXIT_CODE,
    PipelineConfig,
    ensure_directories,
    load_config,
)
from .image_generation import (
    generate_missing_main_image,
    source_images,
)
from .linkedin import fetch_latest_linkedin_post
from .utils import post_identity
from .webflow import find_live_webflow_item, has_pending_verification, item_id_from


def prepare_latest_post_image(config: PipelineConfig) -> int:
    latest_post = fetch_latest_linkedin_post(config.linkedin_access_token)
    if not latest_post:
        print("No recent LinkedIn posts found. No fallback image is needed.")
        return NO_POSTS_FOUND_EXIT_CODE

    if source_images(latest_post):
        print(
            "The latest LinkedIn post already has one or more source images. Skipping image generation."
        )
        return 0

    source_url = post_identity(latest_post)
    if has_pending_verification(config, source_url):
        print("Webflow verification is pending. Reusing its saved image and content intent.")
        return 0

    live_webflow_item = find_live_webflow_item(config, source_url)
    live_webflow_item_id = item_id_from(live_webflow_item)
    if live_webflow_item_id and not config.force_webflow_sync:
        print(
            "Webflow already has a live item for this LinkedIn URL: "
            f"{live_webflow_item_id}. Skipping image generation."
        )
        return 0

    result = generate_missing_main_image(latest_post, config)
    print(f"Fallback image preparation: {result['action']}.")
    return 0


def main() -> int:
    ensure_directories()
    config = load_config()
    return prepare_latest_post_image(config)


if __name__ == "__main__":
    sys.exit(main())
