from __future__ import annotations

import argparse
import sys

from .config import (
    NO_POSTS_FOUND_EXIT_CODE,
    PipelineConfig,
    ensure_directories,
    load_config,
)
from .image_generation import (
    generate_missing_main_image,
    generated_image_path,
    source_images,
    wait_for_generated_image_public,
)
from .image_processing import is_valid_prepared_png_file
from .linkedin import fetch_latest_linkedin_post
from .utils import post_identity
from .webflow import find_live_webflow_item, item_id_from


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


def verify_latest_post_image(config: PipelineConfig) -> int:
    latest_post = fetch_latest_linkedin_post(config.linkedin_access_token)
    if not latest_post:
        print(
            "No recent LinkedIn posts found. No fallback image URL needs verification."
        )
        return NO_POSTS_FOUND_EXIT_CODE

    if source_images(latest_post):
        print(
            "The latest LinkedIn post uses source images. No generated image URL needs verification."
        )
        return 0

    if not is_valid_prepared_png_file(generated_image_path(latest_post)):
        print(
            "No prepared generated PNG exists locally. No public image URL needs verification."
        )
        return 0

    wait_for_generated_image_public(latest_post, config)
    return 0


def main(verify_public: bool = False) -> int:
    ensure_directories()
    config = load_config()
    if verify_public:
        return verify_latest_post_image(config)
    return prepare_latest_post_image(config)


def cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify-public",
        action="store_true",
        help="Verify the commit-pinned generated PNG URL without calling OpenAI or Webflow.",
    )
    args = parser.parse_args()
    return main(verify_public=args.verify_public)


if __name__ == "__main__":
    sys.exit(cli())
