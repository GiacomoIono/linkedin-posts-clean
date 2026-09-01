from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from .config import (
    ENRICHED_POST_PATH,
    NO_POSTS_FOUND_EXIT_CODE,
    PIPELINE_STATE_PATH,
    RAW_POST_PATH,
    ensure_directories,
    load_config,
)
from .enrichment import enrich_post
from .linkedin import fetch_latest_linkedin_post
from .utils import load_json, post_hash, post_identity, write_json
from .webflow import find_live_webflow_item, item_id_from, sync_post_to_webflow


def save_pipeline_state(latest_post: dict[str, Any], enriched_post: dict[str, Any], statuses: dict[str, Any]) -> None:
    state = load_json(PIPELINE_STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.update(
        {
            "last_source_url": latest_post.get("url", ""),
            "last_raw_hash": post_hash(latest_post),
            "last_enriched_hash": post_hash(enriched_post),
            "last_run_at": datetime.now(timezone.utc).isoformat(),
            "statuses": statuses,
        }
    )
    write_json(PIPELINE_STATE_PATH, state)


def main() -> int:
    ensure_directories()
    config = load_config()
    statuses: dict[str, Any] = {}

    print("Starting LinkedIn to Webflow CMS pipeline.")
    latest_post = fetch_latest_linkedin_post(config.linkedin_access_token)
    if not latest_post:
        print("No recent LinkedIn posts found.")
        return NO_POSTS_FOUND_EXIT_CODE

    latest_source_url = post_identity(latest_post)
    print(f"Latest LinkedIn post: {latest_post.get('url')}")

    live_webflow_item = find_live_webflow_item(config, latest_source_url)
    live_webflow_item_id = item_id_from(live_webflow_item)
    if live_webflow_item_id and not config.force_webflow_sync:
        print(
            "Webflow already has a live item for this LinkedIn URL: "
            f"{live_webflow_item_id}. Stopping before enrichment or Webflow writes."
        )
        return 0

    write_json(RAW_POST_PATH, latest_post)

    enriched_post = enrich_post(latest_post, config)
    statuses["enrichment"] = "generated"
    write_json(ENRICHED_POST_PATH, enriched_post)

    statuses["webflow"] = sync_post_to_webflow(enriched_post, config)

    save_pipeline_state(latest_post, enriched_post, statuses)
    print("Required Webflow CMS pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
