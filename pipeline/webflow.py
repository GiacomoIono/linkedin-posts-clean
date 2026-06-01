from __future__ import annotations

import re
from typing import Any

import requests

from .config import PipelineConfig, WEBFLOW_STATE_PATH
from .utils import iso_to_webflow, load_json, post_hash, strip_html_to_text, write_json


WEBFLOW_BASE_URL = "https://api.webflow.com/v2"
WEBFLOW_PAYLOAD_VERSION = 7
AUTHOR_COLLECTION_ID = "63250855178122e0e087d804"
AUTHOR_ITEM_ID = "632508551781225a7587d893"
IMAGE_SEQUENCE_RE = re.compile(r"_(\d+)(?=\.[^.]+$)")


class WebflowError(RuntimeError):
    pass


class WebflowClient:
    def __init__(self, token: str, collection_id: str):
        if not token:
            raise WebflowError("WEBFLOW_API_TOKEN or WEBFLOW_READ_AND_WRITE_BLOG_POSTS is missing.")
        if not collection_id:
            raise WebflowError("WEBFLOW_COLLECTION_ID is missing.")
        self.token = token
        self.collection_id = collection_id

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        url = f"{WEBFLOW_BASE_URL}{path}"
        response = requests.request(method, url, headers=self.headers, timeout=30, **kwargs)
        if response.status_code >= 400:
            body = response.text[:1000] if response.text else ""
            raise WebflowError(f"Webflow {method} {path} failed: {response.status_code} {body}")
        if not response.text:
            return {}
        return response.json()

    def list_items_for_path(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        while True:
            data = self.request(
                "GET",
                f"/collections/{self.collection_id}/{path}",
                params={"offset": offset, "limit": limit},
            )
            batch = data.get("items", [])
            if not isinstance(batch, list):
                break
            items.extend(batch)
            pagination = data.get("pagination", {})
            total = int(pagination.get("total") or len(items))
            if len(items) >= total or not batch:
                break
            offset += limit
        return items

    def list_items(self) -> list[dict[str, Any]]:
        return self.list_items_for_path("items")

    def list_live_items(self) -> list[dict[str, Any]]:
        return self.list_items_for_path("items/live")

    def create_item(self, field_data: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/collections/{self.collection_id}/items",
            params={"skipInvalidFiles": "true"},
            json={"isArchived": False, "isDraft": False, "fieldData": field_data},
        )

    def update_item(self, item_id: str, field_data: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/collections/{self.collection_id}/items",
            params={"skipInvalidFiles": "true"},
            json={"items": [{"id": item_id, "fieldData": field_data}]},
        )

    def update_live_item(self, item_id: str, field_data: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "PATCH",
            f"/collections/{self.collection_id}/items/{item_id}/live",
            params={"skipInvalidFiles": "true"},
            json={"isArchived": False, "isDraft": False, "fieldData": field_data},
        )

    def unpublish_live_item(self, item_id: str) -> dict[str, Any]:
        return self.request("DELETE", f"/collections/{self.collection_id}/items/{item_id}/live")

    def publish_item(self, item_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/collections/{self.collection_id}/items/publish",
            json={"itemIds": [item_id]},
        )


def image_filename(image: dict[str, Any]) -> str:
    url = str(image.get("url") or "")
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower()


def image_sequence(image: dict[str, Any]) -> int | None:
    match = IMAGE_SEQUENCE_RE.search(image_filename(image))
    if not match:
        return None
    return int(match.group(1))


def ordered_images(post: dict[str, Any]) -> list[dict[str, str]]:
    images = []
    for index, image in enumerate(post.get("images", []) or []):
        if isinstance(image, dict) and image.get("url"):
            images.append((index, image))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        index, image = item
        sequence = image_sequence(image)
        if sequence is None:
            return (1, index, index)
        return (0, sequence, index)

    return [
        {
            "url": str(image.get("url") or ""),
            "alt": str(image.get("alt") or ""),
        }
        for _, image in sorted(images, key=sort_key)
    ]


def image_gallery(post: dict[str, Any]) -> list[dict[str, str]]:
    return ordered_images(post)


def post_headline(post: dict[str, Any]) -> str:
    return str(post.get("headline") or strip_html_to_text(post.get("content", ""))[:70] or "LinkedIn post")


def include_optional_field(field_data: dict[str, Any], slug: str, value: Any) -> None:
    if value is None or value == "" or value == []:
        return
    field_data[slug] = value


def build_field_data(post: dict[str, Any]) -> dict[str, Any]:
    images = image_gallery(post)
    first = images[0] if images else None

    field_data: dict[str, Any] = {
        "name": post_headline(post),
        "post-summary": str(post.get("description") or ""),
        "post-body": str(post.get("content") or ""),
        "published-date": iso_to_webflow(post.get("published_at", "")),
        "linkedin-post-link": str(post.get("url") or ""),
        "author": AUTHOR_ITEM_ID,
    }

    include_optional_field(field_data, "post-images", images)
    include_optional_field(field_data, "main-image", first)
    include_optional_field(field_data, "thumbnail-image", first)
    include_optional_field(field_data, "category", post.get("category"))
    include_optional_field(field_data, "tags", post.get("tags"))
    include_optional_field(field_data, "month", post.get("month"))
    if "featured" in post:
        field_data["featured"] = bool(post.get("featured"))

    return field_data


def item_matches(item: dict[str, Any], source_url: str) -> bool:
    field_data = item.get("fieldData", {}) if isinstance(item.get("fieldData"), dict) else {}
    return field_data.get("linkedin-post-link") == source_url


def find_item_by_source_url(client: WebflowClient, source_url: str, live: bool = False) -> dict[str, Any] | None:
    items = client.list_live_items() if live else client.list_items()
    for item in items:
        if item_matches(item, source_url):
            return item
    return None


def item_slug(item: dict[str, Any]) -> str:
    field_data = item.get("fieldData", {}) if isinstance(item.get("fieldData"), dict) else {}
    return str(field_data.get("slug") or "")


def item_id_from(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("id") or "")


def response_item_id(response: dict[str, Any]) -> str:
    if response.get("id"):
        return str(response["id"])
    items = response.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict) and items[0].get("id"):
        return str(items[0]["id"])
    raise WebflowError(f"Could not find item id in Webflow response: {response}")


def load_webflow_state() -> dict[str, Any]:
    state = load_json(WEBFLOW_STATE_PATH, {"items": {}})
    if not isinstance(state, dict):
        return {"items": {}}
    state.setdefault("items", {})
    return state


def save_webflow_state(state: dict[str, Any]) -> None:
    write_json(WEBFLOW_STATE_PATH, state)


def state_entry_for(state: dict[str, Any], source_url: str) -> dict[str, Any]:
    entry = state.get("items", {}).get(source_url, {})
    return entry if isinstance(entry, dict) else {}


def payload_is_current(state_entry: dict[str, Any], signature: str) -> bool:
    return (
        state_entry.get("signature") == signature
        and state_entry.get("payload_version") == WEBFLOW_PAYLOAD_VERSION
    )


def find_existing_item(
    client: WebflowClient,
    source_url: str,
    stored_item_id: str | None,
) -> tuple[dict[str, Any] | None, str]:
    if stored_item_id:
        return {"id": stored_item_id}, "staged"

    staged_item = find_item_by_source_url(client, source_url)
    if staged_item:
        return staged_item, "staged"

    live_item = find_item_by_source_url(client, source_url, live=True)
    if live_item:
        return live_item, "live"

    return None, "missing"


def record_item_state(
    state: dict[str, Any],
    source_url: str,
    item_id: str,
    signature: str,
    published: bool,
    slug: str = "",
) -> None:
    state["items"][source_url] = {
        "item_id": item_id,
        "slug": slug,
        "signature": signature,
        "payload_version": WEBFLOW_PAYLOAD_VERSION,
        "published": published,
    }
    save_webflow_state(state)


def is_not_found_error(exc: WebflowError) -> bool:
    message = str(exc)
    return "404" in message or "resource_not_found" in message


def create_webflow_item(client: WebflowClient, field_data: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    response = client.create_item(field_data)
    return response, response_item_id(response), "created"


def update_staged_item(
    client: WebflowClient,
    item_id: str,
    field_data: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    response = client.update_item(item_id, field_data)
    return response, item_id, "updated"


def replace_live_item(
    client: WebflowClient,
    live_item_id: str,
    field_data: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    try:
        response = client.update_live_item(live_item_id, field_data)
        return response, live_item_id, "updated_live"
    except WebflowError as exc:
        if not is_not_found_error(exc):
            raise
        print(f"Live Webflow item could not be updated: {live_item_id}. Unpublishing and recreating it.")
        try:
            client.unpublish_live_item(live_item_id)
        except WebflowError as unpublish_exc:
            raise WebflowError(
                "Webflow still has a live-only item that blocks this post slug, but the API cannot update "
                f"or unpublish it: {live_item_id}. Publish the deletion in Webflow, then rerun the pipeline."
            ) from unpublish_exc
        return create_webflow_item(client, field_data)


def recover_missing_stored_item(
    client: WebflowClient,
    source_url: str,
    stale_item_id: str,
    field_data: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    print(f"Stored Webflow item ID was not found: {stale_item_id}. Looking up by LinkedIn URL.")

    staged_item = find_item_by_source_url(client, source_url)
    staged_item_id = item_id_from(staged_item)
    if staged_item_id:
        return update_staged_item(client, staged_item_id, field_data)

    live_item = find_item_by_source_url(client, source_url, live=True)
    live_item_id = item_id_from(live_item)
    if live_item_id:
        return replace_live_item(client, live_item_id, field_data)

    return create_webflow_item(client, field_data)


def write_item_to_webflow(
    client: WebflowClient,
    source_url: str,
    item_id: str,
    item_location: str,
    field_data: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    if not item_id:
        return create_webflow_item(client, field_data)

    if item_location == "live":
        return replace_live_item(client, item_id, field_data)

    try:
        return update_staged_item(client, item_id, field_data)
    except WebflowError as exc:
        if not is_not_found_error(exc):
            raise
        return recover_missing_stored_item(client, source_url, item_id, field_data)


def publish_if_needed(client: WebflowClient, item_id: str, action: str, should_publish: bool) -> bool:
    if action == "updated_live":
        return True
    if should_publish:
        client.publish_item(item_id)
        return True
    return False


def sync_post_to_webflow(post: dict[str, Any], config: PipelineConfig) -> dict[str, Any]:
    client = WebflowClient(config.webflow_api_token, config.webflow_collection_id)
    source_url = str(post.get("url") or "")
    signature = post_hash(post)

    state = load_webflow_state()
    state_entry = state_entry_for(state, source_url)
    existing_item, item_location = find_existing_item(client, source_url, state_entry.get("item_id"))
    item_id = item_id_from(existing_item)

    if existing_item and not config.force_webflow_sync and payload_is_current(state_entry, signature):
        slug = state_entry.get("slug") or item_slug(existing_item)
        published = state_entry.get("published", True)
        record_item_state(state, source_url, item_id, signature, published, slug)
        print(f"Webflow already has this LinkedIn URL: {item_id}. Skipping Webflow write.")
        return {"action": "skipped_existing_url", "item_id": item_id}

    field_data = build_field_data(post)
    _, item_id, action = write_item_to_webflow(client, source_url, item_id, item_location, field_data)
    published = publish_if_needed(client, item_id, action, config.webflow_publish)

    record_item_state(state, source_url, item_id, signature, published, field_data.get("slug", ""))
    print(f"Webflow item {action}: {item_id}. Published={published}.")
    return {"action": action, "item_id": item_id, "published": published}
