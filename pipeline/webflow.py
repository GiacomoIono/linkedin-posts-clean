from __future__ import annotations

from typing import Any

import requests

from .config import PipelineConfig, WEBFLOW_STATE_PATH
from .utils import iso_to_webflow, load_json, post_hash, slugify, strip_html_to_text, write_json


WEBFLOW_BASE_URL = "https://api.webflow.com/v2"

SOURCE_URL_SLUGS = {
    "linkedin-url",
    "linkedin_url",
    "source-url",
    "source_url",
    "original-url",
    "original_url",
    "external-url",
    "post-url",
}
CONTENT_SLUGS = {
    "content",
    "post-content",
    "post-body",
    "body",
    "body-copy",
    "blog-content",
    "article-body",
    "rich-text",
    "main-content",
}
HEADLINE_SLUGS = {"headline", "seo-headline", "title", "post-title"}
DESCRIPTION_SLUGS = {
    "description",
    "seo-description",
    "meta-description",
    "summary",
    "excerpt",
    "short-description",
    "post-summary",
}
DATE_SLUGS = {"date", "published-at", "published_at", "published-date", "publish-date"}
IMAGE_SLUGS = {"image", "main-image", "cover-image", "featured-image", "thumbnail", "post-image"}
GALLERY_SLUGS = {"images", "gallery", "image-gallery", "post-images"}
ALT_SLUGS = {"alt", "alt-text", "image-alt", "image-description"}


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

    def get_collection(self) -> dict[str, Any]:
        return self.request("GET", f"/collections/{self.collection_id}")

    def list_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        while True:
            data = self.request(
                "GET",
                f"/collections/{self.collection_id}/items",
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

    def publish_item(self, item_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/collections/{self.collection_id}/items/publish",
            json={"itemIds": [item_id]},
        )


def field_slug(field: dict[str, Any]) -> str:
    return str(field.get("slug") or field.get("apiName") or field.get("displayName") or "").strip()


def field_type(field: dict[str, Any]) -> str:
    return str(field.get("type") or "").lower()


def is_required(field: dict[str, Any]) -> bool:
    validations = field.get("validations") if isinstance(field.get("validations"), dict) else {}
    return bool(field.get("isRequired") or validations.get("required"))


def first_image(post: dict[str, Any]) -> dict[str, str] | None:
    images = post.get("images", []) or []
    if not images:
        return None
    image = images[0]
    if not isinstance(image, dict) or not image.get("url"):
        return None
    data = {"url": image["url"]}
    if image.get("alt"):
        data["alt"] = image["alt"]
    return data


def image_gallery(post: dict[str, Any]) -> list[dict[str, str]]:
    gallery = []
    for image in post.get("images", []) or []:
        if isinstance(image, dict) and image.get("url"):
            item = {"url": image["url"]}
            if image.get("alt"):
                item["alt"] = image["alt"]
            gallery.append(item)
    return gallery


def base_field_values(post: dict[str, Any]) -> dict[str, Any]:
    headline = post.get("headline") or strip_html_to_text(post.get("content", ""))[:70] or "LinkedIn post"
    date_prefix = (post.get("published_at") or "")[:10]
    slug_source = f"{date_prefix} {headline}".strip()
    return {
        "name": headline,
        "slug": slugify(slug_source),
        "headline": post.get("headline") or headline,
        "description": post.get("description") or "",
        "content": post.get("content") or "",
        "plain_text": strip_html_to_text(post.get("content", "")),
        "source_url": post.get("url") or "",
        "published_at": iso_to_webflow(post.get("published_at", "")),
        "image": first_image(post),
        "images": image_gallery(post),
        "alt": (first_image(post) or {}).get("alt", ""),
    }


def value_for_field(slug: str, field: dict[str, Any], values: dict[str, Any]) -> Any:
    normalized = slug.lower()
    ftype = field_type(field)

    if normalized in {"name", "slug"}:
        return values[normalized]
    if normalized in SOURCE_URL_SLUGS:
        return values["source_url"]
    if normalized in CONTENT_SLUGS:
        return values["content"] if "rich" in ftype else values["plain_text"]
    if normalized in HEADLINE_SLUGS:
        return values["headline"]
    if normalized in DESCRIPTION_SLUGS:
        return values["description"]
    if normalized in DATE_SLUGS:
        return values["published_at"]
    if normalized in IMAGE_SLUGS:
        return values["image"]
    if normalized in GALLERY_SLUGS:
        return values["images"]
    if normalized in ALT_SLUGS:
        return values["alt"]

    if not is_required(field):
        return None

    if "rich" in ftype:
        return values["content"]
    if any(kind in ftype for kind in ("text", "plain", "email", "link")):
        return values["plain_text"][:5000]
    if "date" in ftype:
        return values["published_at"]
    if "image" in ftype:
        return values["image"]
    if "switch" in ftype or "boolean" in ftype:
        return False
    if "number" in ftype:
        return 0
    return None


def build_field_data(post: dict[str, Any], collection: dict[str, Any]) -> dict[str, Any]:
    values = base_field_values(post)
    fields = collection.get("fields", [])
    field_data = {"name": values["name"], "slug": values["slug"]}

    if not isinstance(fields, list) or not fields:
        field_data.update(
            {
                "content": values["content"],
                "description": values["description"],
                "linkedin-url": values["source_url"],
                "published-at": values["published_at"],
            }
        )
        if values["image"]:
            field_data["image"] = values["image"]
        return field_data

    missing_required = []
    for field in fields:
        slug = field_slug(field)
        if not slug:
            continue
        value = value_for_field(slug, field, values)
        if value is None or value == "" or value == []:
            if is_required(field) and slug not in {"name", "slug"}:
                missing_required.append(slug)
            continue
        field_data[slug] = value

    if missing_required:
        print(f"Webflow required fields without automatic values: {', '.join(missing_required)}")

    return field_data


def item_matches(item: dict[str, Any], source_url: str, slug: str) -> bool:
    field_data = item.get("fieldData", {}) if isinstance(item.get("fieldData"), dict) else {}
    if field_data.get("slug") == slug:
        return True
    for value in field_data.values():
        if value == source_url:
            return True
    return False


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


def sync_post_to_webflow(post: dict[str, Any], config: PipelineConfig) -> dict[str, Any]:
    client = WebflowClient(config.webflow_api_token, config.webflow_collection_id)
    collection = client.get_collection()
    field_data = build_field_data(post, collection)
    source_url = post.get("url", "")
    signature = post_hash(post)

    state = load_webflow_state()
    state_entry = state.get("items", {}).get(source_url, {})
    item_id = state_entry.get("item_id")

    existing_item = None
    if item_id:
        existing_item = {"id": item_id}
    else:
        for item in client.list_items():
            if item_matches(item, source_url, field_data["slug"]):
                existing_item = item
                item_id = item.get("id")
                break

    if existing_item and state_entry.get("signature") == signature and state_entry.get("published"):
        print(f"Webflow already synced for this post: {item_id}")
        return {"action": "skipped", "item_id": item_id}

    if item_id:
        response = client.update_item(str(item_id), field_data)
        action = "updated"
    else:
        response = client.create_item(field_data)
        item_id = response_item_id(response)
        action = "created"

    if config.webflow_publish:
        client.publish_item(str(item_id))
        published = True
    else:
        published = False

    state["items"][source_url] = {
        "item_id": str(item_id),
        "slug": field_data.get("slug", ""),
        "signature": signature,
        "published": published,
    }
    save_webflow_state(state)
    print(f"Webflow item {action}: {item_id}. Published={published}.")
    return {"action": action, "item_id": str(item_id), "published": published}
