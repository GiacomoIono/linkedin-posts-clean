from __future__ import annotations

import re
from typing import Any

import requests

from .config import PipelineConfig, WEBFLOW_STATE_PATH
from .utils import iso_to_webflow, load_json, post_hash, slugify, strip_html_to_text, write_json


WEBFLOW_BASE_URL = "https://api.webflow.com/v2"
WEBFLOW_PAYLOAD_VERSION = 2

SOURCE_URL_SLUGS = {
    "linkedin-url",
    "linkedin_url",
    "linkedin-link",
    "linkedin_post_link",
    "linkedin-post",
    "linkedin-post-link",
    "linkedin-post-url",
    "source-url",
    "source_url",
    "source-link",
    "original-url",
    "original_url",
    "original-post",
    "original-post-url",
    "external-url",
    "external-link",
    "canonical-url",
    "permalink",
    "url",
    "post-url",
    "post-link",
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
ALT_SLUGS = {
    "alt",
    "alt-tag",
    "alt-tags",
    "alt-text",
    "image-alt",
    "image-alt-tag",
    "image-alt-tags",
    "image-alt-text",
    "image-description",
    "images-alt",
    "images-alt-tag",
    "images-alt-tags",
    "images-alt-text",
    "main-image-alt",
    "main-image-alt-tag",
    "featured-image-alt",
    "featured-image-alt-tag",
    "cover-image-alt",
    "cover-image-alt-tag",
    "thumbnail-alt",
    "thumbnail-alt-tag",
}


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


def image_alt_text(post: dict[str, Any]) -> str:
    for key in ("alt", "image_alt", "image_alt_tag", "images_alt_tag"):
        alt = str(post.get(key) or "").strip()
        if alt:
            return alt

    for image in post.get("images", []) or []:
        if isinstance(image, dict):
            alt = str(image.get("alt") or "").strip()
            if alt:
                return alt
    return ""


def base_field_values(post: dict[str, Any]) -> dict[str, Any]:
    headline = post.get("headline") or strip_html_to_text(post.get("content", ""))[:70] or "LinkedIn post"
    date_prefix = (post.get("published_at") or "")[:10]
    source_id_match = re.search(r"(\d{8,})", post.get("url", ""))
    source_id = source_id_match.group(1)[-8:] if source_id_match else ""
    slug_source = f"{date_prefix} {headline} {source_id}".strip()
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
        "alt": image_alt_text(post),
    }


def field_keys(slug: str, field: dict[str, Any]) -> set[str]:
    keys = {slug.lower()}
    for key in ("displayName", "name", "apiName"):
        value = field.get(key)
        if value:
            keys.add(slugify(str(value), limit=120))
            keys.add(str(value).strip().lower())
    return keys


def key_matches(keys: set[str], candidates: set[str]) -> bool:
    return bool(keys & candidates)


def is_link_field_type(ftype: str) -> bool:
    return "link" in ftype or "url" in ftype


def looks_like_source_url_field(keys: set[str], ftype: str) -> bool:
    if key_matches(keys, SOURCE_URL_SLUGS):
        return True

    if any("linkedin" in key and ("url" in key or "link" in key) for key in keys):
        return True

    if not is_link_field_type(ftype):
        return False

    return any(
        "linkedin" in key
        or "source" in key
        or "original" in key
        or "external" in key
        or "canonical" in key
        or "permalink" in key
        or key in {"post", "post-link", "post-url"}
        for key in keys
    )


def looks_like_alt_field(keys: set[str]) -> bool:
    return key_matches(keys, ALT_SLUGS) or any("alt" in key for key in keys)


def looks_like_gallery_field(keys: set[str], ftype: str) -> bool:
    if key_matches(keys, GALLERY_SLUGS) or any("gallery" in key for key in keys):
        return True
    if "multi" in ftype and "image" in ftype:
        return True
    return any(key == "images" or key.endswith("-images") for key in keys)


def looks_like_image_field(keys: set[str], ftype: str) -> bool:
    if key_matches(keys, IMAGE_SLUGS):
        return True
    if "image" in ftype:
        return True
    return any("image" in key or "cover" in key or "thumbnail" in key for key in keys)


def value_for_field(slug: str, field: dict[str, Any], values: dict[str, Any]) -> Any:
    keys = field_keys(slug, field)
    ftype = field_type(field)

    if "name" in keys:
        return values["name"]
    if "slug" in keys:
        return values["slug"]
    if looks_like_source_url_field(keys, ftype):
        return values["source_url"]
    if key_matches(keys, CONTENT_SLUGS) or any(("body" in key or "content" in key) for key in keys):
        return values["content"] if "rich" in ftype else values["plain_text"]
    if key_matches(keys, HEADLINE_SLUGS) or any(("headline" in key or "title" in key) for key in keys):
        return values["headline"]
    if key_matches(keys, DESCRIPTION_SLUGS) or any(("description" in key or "summary" in key or "excerpt" in key) for key in keys):
        return values["description"]
    if key_matches(keys, DATE_SLUGS) or any("date" in key or "published" in key for key in keys):
        return values["published_at"]
    if looks_like_alt_field(keys):
        return values["alt"]
    if looks_like_gallery_field(keys, ftype):
        return values["images"]
    if looks_like_image_field(keys, ftype):
        return values["image"]

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
        if values["alt"]:
            field_data["images-alt-tag"] = values["alt"]
        if values["image"]:
            field_data["image"] = values["image"]
        if values["images"]:
            field_data["images"] = values["images"]
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


def item_matches(item: dict[str, Any], source_url: str) -> bool:
    field_data = item.get("fieldData", {}) if isinstance(item.get("fieldData"), dict) else {}
    for value in field_data.values():
        if value == source_url:
            return True
    return False


def item_slug(item: dict[str, Any]) -> str:
    field_data = item.get("fieldData", {}) if isinstance(item.get("fieldData"), dict) else {}
    return str(field_data.get("slug") or "")


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
            if item_matches(item, source_url):
                existing_item = item
                item_id = item.get("id")
                break

    payload_is_current = (
        state_entry.get("signature") == signature
        and state_entry.get("payload_version") == WEBFLOW_PAYLOAD_VERSION
    )
    if existing_item and not config.force_webflow_sync and payload_is_current:
        state["items"][source_url] = {
            "item_id": str(item_id),
            "slug": state_entry.get("slug") or item_slug(existing_item),
            "signature": signature,
            "payload_version": WEBFLOW_PAYLOAD_VERSION,
            "published": state_entry.get("published", True),
        }
        save_webflow_state(state)
        print(f"Webflow already has this LinkedIn URL: {item_id}. Skipping Webflow write.")
        return {"action": "skipped_existing_url", "item_id": str(item_id)}

    collection = client.get_collection()
    field_data = build_field_data(post, collection)

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
        "payload_version": WEBFLOW_PAYLOAD_VERSION,
        "published": published,
    }
    save_webflow_state(state)
    print(f"Webflow item {action}: {item_id}. Published={published}.")
    return {"action": action, "item_id": str(item_id), "published": published}
