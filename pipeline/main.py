from __future__ import annotations

import sys
from copy import deepcopy
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
from .image_generation import attach_generated_main_image, source_images
from .linkedin import fetch_latest_linkedin_post
from .linking import link_post_body
from .link_review import exception_details, record_link_review, redact_diagnostics
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


def _record_link_review_safely(*args: Any, **kwargs: Any) -> dict[str, Any] | None:
    # Keep even unexpected reporter defects outside the required CMS path.
    try:
        return record_link_review(*args, **kwargs)
    except Exception:
        print("Link review logging failed unexpectedly; continuing the CMS pipeline.")
        return kwargs.get("report")


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
    enriched_post = attach_generated_main_image(enriched_post, config)
    statuses["image"] = "source_images" if source_images(latest_post) else "generated_main_image"
    pre_link_post = deepcopy(enriched_post)
    try:
        linked_post, link_audit = link_post_body(deepcopy(pre_link_post), config)
        if not isinstance(linked_post, dict) or not isinstance(link_audit, dict):
            raise TypeError("Evidence linking must return a post object and an audit object.")
        if not isinstance(linked_post.get("content"), str):
            raise TypeError("Evidence linking returned an invalid post body.")
        if {key: value for key, value in linked_post.items() if key != "content"} != {
            key: value for key, value in pre_link_post.items() if key != "content"
        }:
            raise ValueError("Evidence linking changed fields outside the post body.")
        enriched_post = pre_link_post if link_audit.get("manual_review_required") else linked_post
        link_audit = redact_diagnostics(link_audit, config)
    except Exception as exc:
        enriched_post = pre_link_post
        link_audit = {
            "decision": "manual_review_required",
            "manual_review_required": True,
            "fallback": "original_body",
            "links_added": 0,
            "proposals_reviewed": 0,
            "rejected_candidates": 0,
            "links": [],
            "corrections_attempted": 0,
            "attempts": [],
            "errors": [{"stage": "link_post_body", **exception_details(exc, config)}],
        }
    statuses["links"] = link_audit
    review_report = _record_link_review_safely(pre_link_post, enriched_post, link_audit, config)
    write_json(ENRICHED_POST_PATH, enriched_post)

    try:
        statuses["webflow"] = sync_post_to_webflow(enriched_post, config)
    except Exception as exc:
        _record_link_review_safely(
            pre_link_post, enriched_post, link_audit, config,
            report=review_report, webflow_error=exc,
        )
        raise
    _record_link_review_safely(
        pre_link_post, enriched_post, link_audit, config,
        report=review_report, webflow_status=statuses["webflow"],
    )

    save_pipeline_state(latest_post, enriched_post, statuses)
    print("Required Webflow CMS pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
