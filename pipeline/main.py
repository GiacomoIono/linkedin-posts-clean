from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from .config import (
    ENRICHED_POST_PATH,
    NO_POSTS_FOUND_EXIT_CODE,
    PIPELINE_STATE_PATH,
    RAW_POST_PATH,
    TWEET_PATH,
    PipelineConfig,
    ensure_directories,
    load_config,
)
from .enrichment import backfill_missing_alt, enrich_post, has_missing_image_alt
from .linkedin import fetch_latest_linkedin_post
from .utils import load_json, post_hash, post_identity, write_json
from .webflow import WEBFLOW_PAYLOAD_VERSION, load_webflow_state, sync_post_to_webflow
from .x_posting import generate_tweet, post_to_x


def load_existing_raw_post() -> dict[str, Any] | None:
    return load_json(RAW_POST_PATH, None)


def load_existing_enriched_post() -> dict[str, Any] | None:
    return load_json(ENRICHED_POST_PATH, None)


def load_existing_tweet() -> dict[str, Any] | None:
    return load_json(TWEET_PATH, None)


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


def webflow_state_entry(source_url: str) -> dict[str, Any] | None:
    state = load_webflow_state()
    entry = state.get("items", {}).get(source_url)
    if not isinstance(entry, dict):
        return None
    if entry.get("item_id"):
        return entry
    return None


def already_synced_to_webflow(post: dict[str, Any]) -> dict[str, Any] | None:
    entry = webflow_state_entry(post.get("url", ""))
    if (
        entry
        and entry.get("published")
        and entry.get("signature") == post_hash(post)
        and entry.get("payload_version") == WEBFLOW_PAYLOAD_VERSION
    ):
        return entry
    return None


def matches_existing_post(
    latest_post: dict[str, Any],
    previous_raw: dict[str, Any] | None,
    previous_enriched: dict[str, Any] | None,
    existing_webflow_entry: dict[str, Any] | None,
) -> bool:
    return (
        same_source_url(latest_post, previous_raw)
        or same_source_url(latest_post, previous_enriched)
        or bool(existing_webflow_entry)
    )


def prepare_enriched_post(
    latest_post: dict[str, Any],
    previous_enriched: dict[str, Any] | None,
    matches_existing: bool,
    config: PipelineConfig,
) -> tuple[dict[str, Any], str]:
    needs_alt_backfill = same_source_url(previous_enriched, latest_post) and has_missing_image_alt(previous_enriched)

    if matches_existing and needs_alt_backfill and not config.force_enrich:
        print("Existing enriched post is missing image ALT text. Running ALT backfill.")
        return backfill_missing_alt(previous_enriched or latest_post, config), "alt_backfilled"

    if matches_existing and not config.force_enrich:
        print("Latest post matches existing stored post. Skipping OpenAI enrichment.")
        enriched_post = previous_enriched or latest_post
        if not same_source_url(enriched_post, latest_post):
            enriched_post = dict(enriched_post)
            enriched_post["url"] = latest_post.get("url", "")
        return enriched_post, "skipped_existing_post"

    return enrich_post(latest_post, config), "generated"


def sync_webflow_if_needed(enriched_post: dict[str, Any], matches_existing: bool, config: PipelineConfig) -> dict[str, Any]:
    synced_entry = already_synced_to_webflow(enriched_post)
    if matches_existing and synced_entry and not config.force_webflow_sync:
        print("Webflow already synced for this LinkedIn URL. Skipping Webflow API call.")
        return {
            "action": "skipped_already_synced",
            "item_id": synced_entry.get("item_id"),
        }
    return sync_post_to_webflow(enriched_post, config)


def run_x_pipeline_if_enabled(
    enriched_post: dict[str, Any],
    latest_post: dict[str, Any],
    matches_existing: bool,
    config: PipelineConfig,
) -> dict[str, Any]:
    if not config.run_x_pipeline:
        print("RUN_X_PIPELINE is false. Skipping X pipeline.")
        return {
            "tweetify": "skipped_x_pipeline_disabled",
            "x": "skipped_x_pipeline_disabled",
        }

    previous_tweet = load_existing_tweet()
    if matches_existing and previous_tweet and same_source_url(previous_tweet, latest_post) and not config.force_tweetify:
        tweet = previous_tweet
        print("Latest post matches existing tweet artifact. Skipping OpenAI tweet generation.")
        tweetify_status = "skipped_existing_post"
    elif matches_existing and not config.force_tweetify:
        tweet = None
        print("Latest post matches existing post and no tweet artifact was found. Skipping OpenAI tweet generation.")
        tweetify_status = "skipped_existing_post_no_artifact"
    else:
        try:
            tweet = generate_tweet(enriched_post, config)
            write_json(TWEET_PATH, tweet)
            tweetify_status = "generated"
        except Exception as exc:
            tweet = None
            tweetify_status = f"failed: {exc}"
            print(f"Optional X draft generation failed: {exc}")

    if not tweet:
        return {"tweetify": tweetify_status, "x": "skipped_no_tweet"}

    write_json(TWEET_PATH, tweet)
    try:
        x_status = post_to_x(tweet, config)
    except Exception as exc:
        x_status = f"failed: {exc}"
        print(f"Optional X posting failed: {exc}")
        if config.require_x_posting:
            raise
    return {"tweetify": tweetify_status, "x": x_status}


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
    latest_source_url = post_identity(latest_post)
    existing_webflow_entry = webflow_state_entry(latest_source_url)
    matches_existing = matches_existing_post(latest_post, previous_raw, previous_enriched, existing_webflow_entry)

    write_json(RAW_POST_PATH, latest_post)
    print(f"Latest LinkedIn post: {latest_post.get('url')}")

    enriched_post, statuses["enrichment"] = prepare_enriched_post(
        latest_post,
        previous_enriched,
        matches_existing,
        config,
    )
    write_json(ENRICHED_POST_PATH, enriched_post)

    statuses["webflow"] = sync_webflow_if_needed(enriched_post, matches_existing, config)
    statuses.update(run_x_pipeline_if_enabled(enriched_post, latest_post, matches_existing, config))

    save_pipeline_state(latest_post, enriched_post, statuses)
    print("Required Webflow CMS pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
