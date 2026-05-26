from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from .config import (
    ENRICHED_POST_PATH,
    LEGACY_ENRICHED_POST_PATH,
    LEGACY_RAW_POST_PATH,
    LEGACY_TWEET_PATH,
    NO_POSTS_FOUND_EXIT_CODE,
    PIPELINE_STATE_PATH,
    RAW_POST_PATH,
    TWEET_PATH,
    ensure_directories,
    load_config,
)
from .enrichment import enrich_post
from .linkedin import fetch_latest_linkedin_post
from .utils import load_json, mirror_json, post_hash, post_identity, write_json
from .webflow import load_webflow_state, sync_post_to_webflow
from .x_posting import generate_tweet, post_to_x


def load_existing_raw_post() -> dict[str, Any] | None:
    return load_json(RAW_POST_PATH, None) or load_json(LEGACY_RAW_POST_PATH, None)


def load_existing_enriched_post() -> dict[str, Any] | None:
    return load_json(ENRICHED_POST_PATH, None) or load_json(LEGACY_ENRICHED_POST_PATH, None)


def load_existing_tweet() -> dict[str, Any] | None:
    return load_json(TWEET_PATH, None) or load_json(LEGACY_TWEET_PATH, None)


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


def same_source_url(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(post_identity(a) and post_identity(a) == post_identity(b))


def already_synced_to_webflow(post: dict[str, Any]) -> dict[str, Any] | None:
    state = load_webflow_state()
    entry = state.get("items", {}).get(post.get("url", ""))
    if not isinstance(entry, dict):
        return None
    if entry.get("item_id") and entry.get("published"):
        return entry
    return None


def main() -> int:
    ensure_directories()
    config = load_config()
    statuses: dict[str, Any] = {}

    print("Starting Webflow CMS JSON pipeline with optional X posting.")
    latest_post = fetch_latest_linkedin_post(config.linkedin_access_token)
    if not latest_post:
        print("No recent LinkedIn posts found.")
        return NO_POSTS_FOUND_EXIT_CODE

    previous_raw = load_existing_raw_post()
    previous_enriched = load_existing_enriched_post()
    previous_tweet = load_existing_tweet()
    matches_existing = same_source_url(latest_post, previous_raw) or same_source_url(latest_post, previous_enriched)

    mirror_json(RAW_POST_PATH, LEGACY_RAW_POST_PATH, latest_post)
    print(f"Latest LinkedIn post: {latest_post.get('url')}")

    if matches_existing and previous_enriched and not config.force_enrich:
        enriched_post = previous_enriched
        print("Latest post matches existing stored post. Skipping OpenAI enrichment.")
        if not same_source_url(enriched_post, latest_post):
            enriched_post = dict(enriched_post)
            enriched_post["url"] = latest_post.get("url", "")
        statuses["enrichment"] = "skipped_existing_post"
    else:
        enriched_post = enrich_post(latest_post, config)
        statuses["enrichment"] = "generated"

    mirror_json(ENRICHED_POST_PATH, LEGACY_ENRICHED_POST_PATH, enriched_post)

    synced_entry = already_synced_to_webflow(enriched_post)
    if matches_existing and synced_entry and not config.force_webflow_sync:
        print("Webflow already synced for this LinkedIn URL. Skipping Webflow API call.")
        webflow_result = {
            "action": "skipped_already_synced",
            "item_id": synced_entry.get("item_id"),
        }
    else:
        webflow_result = sync_post_to_webflow(enriched_post, config)
    statuses["webflow"] = webflow_result

    if matches_existing and previous_tweet and same_source_url(previous_tweet, latest_post) and not config.force_tweetify:
        tweet = previous_tweet
        print("Latest post matches existing tweet artifact. Skipping OpenAI tweet generation.")
        statuses["tweetify"] = "skipped_existing_post"
    elif matches_existing and not config.force_tweetify:
        tweet = None
        print("Latest post matches existing post and no tweet artifact was found. Skipping OpenAI tweet generation.")
        statuses["tweetify"] = "skipped_existing_post_no_artifact"
    else:
        try:
            tweet = generate_tweet(enriched_post, config)
            mirror_json(TWEET_PATH, LEGACY_TWEET_PATH, tweet)
            statuses["tweetify"] = "generated"
        except Exception as exc:
            tweet = None
            statuses["tweetify"] = f"failed: {exc}"
            print(f"Optional X draft generation failed: {exc}")

    if tweet:
        mirror_json(TWEET_PATH, LEGACY_TWEET_PATH, tweet)
        try:
            statuses["x"] = post_to_x(tweet, config)
        except Exception as exc:
            statuses["x"] = f"failed: {exc}"
            print(f"Optional X posting failed: {exc}")
            if config.require_x_posting:
                raise
    else:
        statuses["x"] = "skipped_no_tweet"

    save_pipeline_state(latest_post, enriched_post, statuses)
    print("Required Webflow CMS pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
