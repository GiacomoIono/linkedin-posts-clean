from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError
import requests

from .config import WEBFLOW_STATE_PATH, PipelineConfig
from .utils import iso_to_webflow, load_json, post_hash, strip_html_to_text, write_json

WEBFLOW_BASE_URL = "https://api.webflow.com/v2"
WEBFLOW_PAYLOAD_VERSION = 9
WEBFLOW_LIVE_READBACK_ATTEMPTS = 3
WEBFLOW_LIVE_READBACK_DELAY_SECONDS = 1
AUTHOR_COLLECTION_ID = "63250855178122e0e087d804"
AUTHOR_ITEM_ID = "632508551781225a7587d893"
IMAGE_SEQUENCE_RE = re.compile(r"_(\d+)(?=\.[^.]+$)")
WEBFLOW_IMAGE_FIELDS = ("post-images", "main-image", "thumbnail-image")
MAX_READBACK_IMAGE_BYTES = 30_000_000
MAX_READBACK_IMAGE_PIXELS = 40_000_000
PRIVATE_IMAGE_REPOSITORY = ("giacomoiono", "linkedin-posts-clean")


class WebflowError(RuntimeError):
    pass


class WebflowVerificationMismatch(WebflowError):
    """The fetched CMS item differs from the intended write, rather than a network error."""

    pass


def skip_invalid_files_value(field_data: dict[str, Any]) -> str:
    return "false" if any(field_data.get(key) for key in WEBFLOW_IMAGE_FIELDS) else "true"


def validate_image_url(url: Any) -> str:
    if not isinstance(url, str) or not url or url != url.strip():
        raise WebflowError("Every Webflow image must have a public HTTPS URL.")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
    except ValueError as exc:
        raise WebflowError("A Webflow image URL is invalid.") from exc
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or host in {"localhost", "127.0.0.1", "::1"}
        or host.endswith(".localhost")
    ):
        raise WebflowError("Every Webflow image must have a public HTTPS URL.")
    path_parts = tuple(unquote(parsed.path).strip("/").lower().split("/")[:2])
    if (
        host in {"raw.githubusercontent.com", "github.com", "www.github.com"}
        and path_parts == PRIVATE_IMAGE_REPOSITORY
    ):
        raise WebflowError(
            "A Webflow image still depends on this repository being public. "
            "Upload it with the Webflow Assets API first."
        )
    return url


def validate_payload_images(field_data: dict[str, Any]) -> None:
    for key in WEBFLOW_IMAGE_FIELDS:
        value = field_data.get(key)
        if value is None or value == []:
            continue
        images = value if key == "post-images" else [value]
        if not isinstance(images, list):
            raise WebflowError(f"Webflow {key} must be an image list.")
        for image in images:
            if not isinstance(image, dict):
                raise WebflowError(f"Webflow {key} contains an invalid image.")
            validate_image_url(image.get("url"))


class WebflowClient:
    def __init__(self, token: str, collection_id: str):
        if not token:
            raise WebflowError(
                "WEBFLOW_API_TOKEN or WEBFLOW_READ_AND_WRITE_BLOG_POSTS is missing."
            )
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
        response = requests.request(
            method, url, headers=self.headers, timeout=30, **kwargs
        )
        if response.status_code >= 400:
            body = response.text[:1000] if response.text else ""
            raise WebflowError(
                f"Webflow {method} {path} failed: {response.status_code} {body}"
            )
        if not response.text:
            return {}
        return response.json()

    def list_items_for_path(self, path: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        offset = 0
        limit = 100
        expected_total: int | None = None
        while True:
            data = self.request(
                "GET",
                f"/collections/{self.collection_id}/{path}",
                params={"offset": offset, "limit": limit},
            )
            if not isinstance(data, dict):
                raise WebflowError("Webflow item lookup returned an invalid response.")
            batch = data.get("items")
            if not isinstance(batch, list):
                raise WebflowError("Webflow item lookup returned an invalid items list.")
            pagination = data.get("pagination")
            if not isinstance(pagination, dict) or any(type(pagination.get(key)) is not int for key in ("total", "offset", "limit")):
                raise WebflowError("Webflow item lookup is missing complete pagination metadata.")
            total = pagination["total"]
            if pagination["offset"] != offset or pagination["limit"] != limit or total < 0:
                raise WebflowError("Webflow item lookup returned inconsistent pagination metadata.")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise WebflowError("Webflow item total changed during lookup; retry the pipeline.")
            if len(batch) > limit or len(items) + len(batch) > total:
                raise WebflowError("Webflow item lookup exceeded its declared item count.")
            for item in batch:
                item_id = item_id_from(item)
                if not item_id or not isinstance(item.get("id"), str) or not item_id.strip() or item_id in seen:
                    raise WebflowError("Webflow item lookup returned a missing or duplicate item ID.")
                seen.add(item_id)
                items.append(item)
            if len(items) == total:
                return items
            if not batch:
                raise WebflowError("Webflow item lookup ended before all items were returned.")
            offset += len(batch)

    def list_items(self) -> list[dict[str, Any]]:
        return self.list_items_for_path("items")

    def list_live_items(self) -> list[dict[str, Any]]:
        return self.list_items_for_path("items/live")

    def get_item(self, item_id: str) -> dict[str, Any]:
        return self.request(
            "GET", f"/collections/{self.collection_id}/items/{item_id}"
        )

    def get_live_item(self, item_id: str) -> dict[str, Any]:
        return self.request(
            "GET", f"/collections/{self.collection_id}/items/{item_id}/live"
        )

    def create_item(self, field_data: dict[str, Any]) -> dict[str, Any]:
        validate_payload_images(field_data)
        return self.request(
            "POST",
            f"/collections/{self.collection_id}/items",
            params={"skipInvalidFiles": skip_invalid_files_value(field_data)},
            json={"isArchived": False, "isDraft": False, "fieldData": field_data},
        )

    def update_item(self, item_id: str, field_data: dict[str, Any]) -> dict[str, Any]:
        validate_payload_images(field_data)
        return self.request(
            "PATCH",
            f"/collections/{self.collection_id}/items",
            params={"skipInvalidFiles": skip_invalid_files_value(field_data)},
            json={"items": [{"id": item_id, "fieldData": field_data}]},
        )

    def update_live_item(
        self, item_id: str, field_data: dict[str, Any]
    ) -> dict[str, Any]:
        validate_payload_images(field_data)
        return self.request(
            "PATCH",
            f"/collections/{self.collection_id}/items/{item_id}/live",
            params={"skipInvalidFiles": skip_invalid_files_value(field_data)},
            json={"isArchived": False, "isDraft": False, "fieldData": field_data},
        )

    def unpublish_live_item(self, item_id: str) -> dict[str, Any]:
        return self.request(
            "DELETE", f"/collections/{self.collection_id}/items/{item_id}/live"
        )

    def publish_item(self, item_id: str) -> dict[str, Any]:
        return self.request(
            "POST",
            f"/collections/{self.collection_id}/items/publish",
            json={"itemIds": [item_id]},
        )


def image_filename(image: dict[str, Any]) -> str:
    if image.get("filename"):
        return str(image["filename"]).lower()
    url = str(image.get("url") or "")
    return unquote(url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]).lower()


def image_sequence(image: dict[str, Any]) -> int | None:
    match = IMAGE_SEQUENCE_RE.search(image_filename(image))
    if not match:
        return None
    return int(match.group(1))


def ordered_images(post: dict[str, Any]) -> list[dict[str, str]]:
    images = []
    source_images = post.get("images", [])
    if not isinstance(source_images, list):
        raise WebflowError("Post source images must be a list.")
    for index, image in enumerate(source_images):
        if not isinstance(image, dict):
            raise WebflowError(f"Source image {index + 1} is invalid.")
        validate_image_url(image.get("url"))
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


def generated_main_image(post: dict[str, Any]) -> dict[str, str] | None:
    image = post.get("generated_main_image")
    if image is None:
        return None
    if not isinstance(image, dict):
        raise WebflowError("The generated main image is invalid.")
    validate_image_url(image.get("url"))
    return {
        "url": str(image.get("url") or ""),
        "alt": str(image.get("alt") or ""),
    }


def post_headline(post: dict[str, Any]) -> str:
    return str(
        post.get("headline")
        or strip_html_to_text(post.get("content", ""))[:60]
        or "LinkedIn post"
    )


def include_optional_field(field_data: dict[str, Any], slug: str, value: Any) -> None:
    if value is None or value == "" or value == []:
        return
    field_data[slug] = value


def build_field_data(post: dict[str, Any]) -> dict[str, Any]:
    images = image_gallery(post)
    first = images[0] if images else None
    fallback_main_image = generated_main_image(post) if not images else None

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
    include_optional_field(field_data, "main-image", fallback_main_image)
    include_optional_field(field_data, "category", post.get("category"))
    include_optional_field(field_data, "tags", post.get("tags"))
    include_optional_field(field_data, "month", post.get("month"))
    if "featured" in post:
        field_data["featured"] = bool(post.get("featured"))

    return field_data


def item_matches(item: dict[str, Any], source_url: str) -> bool:
    field_data = (
        item.get("fieldData", {}) if isinstance(item.get("fieldData"), dict) else {}
    )
    return field_data.get("linkedin-post-link") == source_url


def find_item_by_source_url(
    client: WebflowClient, source_url: str, live: bool = False
) -> dict[str, Any] | None:
    items = client.list_live_items() if live else client.list_items()
    for item in items:
        if item_matches(item, source_url):
            return item
    return None


def find_live_webflow_item(
    config: PipelineConfig, source_url: str
) -> dict[str, Any] | None:
    if not source_url:
        return None
    client = WebflowClient(config.webflow_api_token, config.webflow_collection_id)
    return find_item_by_source_url(client, source_url, live=True)


def item_slug(item: dict[str, Any]) -> str:
    field_data = (
        item.get("fieldData", {}) if isinstance(item.get("fieldData"), dict) else {}
    )
    return str(field_data.get("slug") or "")


def item_id_from(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("id") or "")


def response_item_id(response: dict[str, Any]) -> str:
    if response.get("id"):
        return str(response["id"])
    items = response.get("items")
    if (
        isinstance(items, list)
        and items
        and isinstance(items[0], dict)
        and items[0].get("id")
    ):
        return str(items[0]["id"])
    raise WebflowError(f"Could not find item id in Webflow response: {response}")


def load_webflow_state() -> dict[str, Any]:
    state = load_json(WEBFLOW_STATE_PATH, {"items": {}})
    if not isinstance(state, dict):
        return {"items": {}}
    state.setdefault("items", {})
    return state


def save_webflow_state(state: dict[str, Any]) -> None:
    WEBFLOW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=WEBFLOW_STATE_PATH.parent, prefix=".webflow-items-", suffix=".tmp", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        write_json(temporary_path, state)
        os.replace(temporary_path, WEBFLOW_STATE_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)


def state_entry_for(state: dict[str, Any], source_url: str) -> dict[str, Any]:
    entry = state.get("items", {}).get(source_url, {})
    return entry if isinstance(entry, dict) else {}


def pending_verification_for(
    state: dict[str, Any], source_url: str, collection_id: str
) -> dict[str, Any] | None:
    pending = state_entry_for(state, source_url).get("verification_pending")
    if pending is None:
        return None
    if (
        not isinstance(pending, dict)
        or not isinstance(pending.get("item_id"), str)
        or not pending["item_id"]
        or not isinstance(pending.get("signature"), str)
        or not pending["signature"]
        or not isinstance(pending.get("expected_fields"), dict)
        or not isinstance(pending["expected_fields"].get("post-body"), str)
        or pending["expected_fields"].get("linkedin-post-link") != source_url
        or not isinstance(pending.get("image_digests"), dict)
        or pending.get("location") not in {"staged", "live"}
        or type(pending.get("should_publish")) is not bool
        or pending.get("collection_id") != collection_id
    ):
        raise WebflowError("Saved Webflow verification intent is invalid or belongs to another collection.")
    validate_payload_images(pending["expected_fields"])
    for url, digest in pending["image_digests"].items():
        validate_image_url(url)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise WebflowError("Saved Webflow verification intent has an invalid image fingerprint.")
    return pending


def has_pending_verification(config: PipelineConfig, source_url: str) -> bool:
    return pending_verification_for(
        load_webflow_state(), source_url, config.webflow_collection_id
    ) is not None


def pending_verification_urls(config: PipelineConfig) -> list[str]:
    state = load_webflow_state()
    entries = state.get("items")
    if not isinstance(entries, dict):
        raise WebflowError("Saved Webflow item state is invalid; pending verification cannot be inspected.")
    urls = []
    for source_url, entry in entries.items():
        if not isinstance(source_url, str) or not source_url or not isinstance(entry, dict):
            raise WebflowError("Saved Webflow item state contains an invalid source entry.")
        if pending_verification_for(state, source_url, config.webflow_collection_id) is not None:
            urls.append(source_url)
    return urls


def checkpoint_verification(
    state: dict[str, Any],
    source_url: str,
    *,
    item_id: str,
    collection_id: str,
    signature: str,
    expected_fields: dict[str, Any],
    image_digests: dict[str, str],
    location: str,
    should_publish: bool,
) -> dict[str, Any]:
    pending = {
        "item_id": item_id,
        "collection_id": collection_id,
        "signature": signature,
        "expected_fields": deepcopy(expected_fields),
        "image_digests": dict(image_digests),
        "location": location,
        "should_publish": should_publish,
    }
    state["items"][source_url] = {
        **state_entry_for(state, source_url), "verification_pending": pending
    }
    save_webflow_state(state)
    return pending


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


def create_webflow_item(
    client: WebflowClient, field_data: dict[str, Any]
) -> tuple[dict[str, Any], str, str]:
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
        print(
            f"Live Webflow item could not be updated: {live_item_id}. Unpublishing and recreating it."
        )
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
    print(
        f"Stored Webflow item ID was not found: {stale_item_id}. Looking up by LinkedIn URL."
    )

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


def publish_if_needed(
    client: WebflowClient, item_id: str, action: str, should_publish: bool
) -> bool:
    if action == "updated_live":
        return True
    if should_publish:
        client.publish_item(item_id)
        return True
    return False


def expected_image_digests(post: dict[str, Any]) -> dict[str, str]:
    images = post.get("images", [])
    if not images and post.get("generated_main_image"):
        images = [post["generated_main_image"]]
    digests: dict[str, str] = {}
    for image in images:
        digest = image.get("sha256")
        if digest is None:
            continue
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise WebflowError("An uploaded image has an invalid SHA-256 fingerprint.")
        url = validate_image_url(image.get("url"))
        digest = digest.lower()
        if url in digests and digests[url] != digest:
            raise WebflowError("One image URL has conflicting source fingerprints.")
        digests[url] = digest
    return digests


def download_image_fingerprint(url: str) -> tuple[str, str | None]:
    """Check public image bytes, without API credentials or ambient .netrc auth."""
    validate_image_url(url)
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, timeout=30, stream=True) as response:
                response.raise_for_status()
                validate_image_url(response.url)
                chunks = []
                total = 0
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > MAX_READBACK_IMAGE_BYTES:
                        raise WebflowError("A Webflow read-back image exceeds the download size limit.")
                    chunks.append(chunk)
                image_bytes = b"".join(chunks)
    except requests.RequestException as exc:
        raise WebflowError("A Webflow read-back image could not be downloaded publicly.") from exc
    if not image_bytes:
        raise WebflowError("A Webflow read-back image is empty.")
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.width * image.height > MAX_READBACK_IMAGE_PIXELS:
                raise WebflowError("A Webflow read-back image exceeds the pixel limit.")
            image.verify()
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            pixel_digest = None
            # Animated images require byte identity; checking the first frame is insufficient.
            if getattr(image, "n_frames", 1) == 1:
                normalized = ImageOps.exif_transpose(image).convert("RGBA")
                fingerprint = hashlib.sha256()
                fingerprint.update(f"{normalized.width}x{normalized.height}:RGBA:".encode())
                fingerprint.update(normalized.tobytes())
                pixel_digest = fingerprint.hexdigest()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise WebflowError("A Webflow read-back image is not a valid, decodable image.") from exc
    return hashlib.sha256(image_bytes).hexdigest(), pixel_digest


def verify_saved_images(
    saved_fields: dict[str, Any],
    expected_fields: dict[str, Any],
    *,
    image_digests: dict[str, str] | None = None,
    image_cache: dict[str, tuple[str, str | None]] | None = None,
) -> None:
    digests = image_digests if image_digests is not None else {}
    cache = image_cache if image_cache is not None else {}

    def fingerprint(url: str) -> tuple[str, str | None]:
        if url not in cache:
            cache[url] = download_image_fingerprint(url)
        return cache[url]

    for key in WEBFLOW_IMAGE_FIELDS:
        expected = expected_fields.get(key)
        saved = saved_fields.get(key)
        if not expected:
            if saved:
                raise WebflowVerificationMismatch(f"Webflow read-back contains an unexpected {key} image field.")
            continue
        if key == "post-images":
            if not isinstance(saved, list) or len(saved) != len(expected):
                raise WebflowVerificationMismatch("Webflow read-back did not preserve the source image count.")
            pairs = zip(expected, saved)
        else:
            pairs = [(expected, saved)]
        for index, (expected_image, saved_image) in enumerate(pairs, start=1):
            label = f"{key} image {index}"
            if not isinstance(saved_image, dict) or not saved_image.get("url"):
                raise WebflowVerificationMismatch(f"Webflow read-back omitted {label}.")
            if str(saved_image.get("alt") or "") != str(expected_image.get("alt") or ""):
                raise WebflowVerificationMismatch(f"Webflow read-back did not preserve the alt text for {label}.")
            expected_url = validate_image_url(expected_image.get("url"))
            actual_url = validate_image_url(saved_image.get("url"))
            actual_fingerprint = fingerprint(actual_url)
            expected_digest = digests.get(expected_url)
            if expected_digest and actual_fingerprint[0] == expected_digest:
                continue
            source_fingerprint = fingerprint(expected_url)
            if expected_digest and source_fingerprint[0] != expected_digest:
                raise WebflowVerificationMismatch(f"The uploaded source fingerprint changed for {label}.")
            if actual_fingerprint[0] == source_fingerprint[0]:
                continue
            if source_fingerprint[1] and actual_fingerprint[1] == source_fingerprint[1]:
                continue
            raise WebflowVerificationMismatch(f"Webflow read-back did not preserve the image identity or order for {label}.")


def verify_saved_item(
    client: WebflowClient,
    item_id: str,
    expected_fields: dict[str, Any],
    *,
    live: bool,
    verify_images: bool = True,
    image_digests: dict[str, str] | None = None,
    image_cache: dict[str, tuple[str, str | None]] | None = None,
    verify_all_fields: bool = False,
) -> None:
    attempts = WEBFLOW_LIVE_READBACK_ATTEMPTS if live else 1
    last_error: WebflowError | None = None
    for attempt in range(1, attempts + 1):
        try:
            saved = client.get_live_item(item_id) if live else client.get_item(item_id)
            if item_id_from(saved) != item_id:
                raise WebflowVerificationMismatch(
                    "Webflow read-back returned a different or missing item ID."
                )
            field_data = saved.get("fieldData")
            if (
                not isinstance(field_data, dict)
                or field_data.get("post-body") != expected_fields.get("post-body")
            ):
                location = "live" if live else "staged"
                raise WebflowVerificationMismatch(
                    f"Webflow {location} read-back did not preserve the verified post body exactly."
                )
            if verify_images:
                verify_saved_images(
                    field_data,
                    expected_fields,
                    image_digests=image_digests,
                    image_cache=image_cache,
                )
            if verify_all_fields:
                for key, expected_value in expected_fields.items():
                    if key in WEBFLOW_IMAGE_FIELDS or key == "post-body":
                        continue
                    actual_value = field_data.get(key)
                    if key == "published-date":
                        try:
                            expected_value = datetime.fromisoformat(str(expected_value).replace("Z", "+00:00"))
                            actual_value = datetime.fromisoformat(str(actual_value).replace("Z", "+00:00"))
                        except ValueError:
                            pass
                    if actual_value != expected_value:
                        raise WebflowVerificationMismatch(f"Webflow read-back did not preserve the pending {key} value.")
            return
        except WebflowError as exc:
            last_error = exc
            if image_cache is not None:
                image_cache.clear()
            if attempt < attempts:
                time.sleep(WEBFLOW_LIVE_READBACK_DELAY_SECONDS)

    if last_error is None:
        raise WebflowVerificationMismatch("Webflow read-back ended without a result.")
    if attempts == 1:
        raise last_error
    error_type = WebflowVerificationMismatch if isinstance(last_error, WebflowVerificationMismatch) else WebflowError
    raise error_type(
        f"Webflow live read-back failed after {attempts} attempts. Last error: {last_error}"
    ) from last_error


def verify_saved_post_body(
    client: WebflowClient,
    item_id: str,
    expected_body: str,
    *,
    live: bool,
) -> None:
    """Retain the body-only verifier for callers that are editing HTML alone."""
    verify_saved_item(
        client, item_id, {"post-body": expected_body}, live=live, verify_images=False
    )


def expected_fields_for_write(
    client: WebflowClient,
    source_url: str,
    item_id: str,
    item_location: str,
    field_data: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    expected = deepcopy(field_data)
    if not item_id or all(key in field_data for key in WEBFLOW_IMAGE_FIELDS):
        return expected, item_id, item_location
    try:
        existing = client.get_live_item(item_id) if item_location == "live" else client.get_item(item_id)
    except WebflowError as exc:
        if not is_not_found_error(exc):
            raise
        existing, item_location = find_existing_item(client, source_url, None)
        item_id = item_id_from(existing)
        if not item_id:
            return expected, "", "missing"
        existing = client.get_live_item(item_id) if item_location == "live" else client.get_item(item_id)
    if item_id_from(existing) != item_id or not isinstance(existing.get("fieldData"), dict):
        raise WebflowError("Could not inspect omitted image fields before updating the existing CMS item.")
    for key in WEBFLOW_IMAGE_FIELDS:
        value = existing["fieldData"].get(key)
        if key in field_data or not value:
            continue
        images = value if key == "post-images" else [value]
        if not isinstance(images, list) or any(not isinstance(image, dict) for image in images):
            raise WebflowError("Existing omitted image fields are malformed; stopping before the CMS update.")
        preserved = [{"url": validate_image_url(image.get("url")), "alt": str(image.get("alt") or "")} for image in images]
        expected[key] = preserved if key == "post-images" else preserved[0]
    return expected, item_id, item_location


def finish_pending_write(
    client: WebflowClient,
    state: dict[str, Any],
    source_url: str,
    pending: dict[str, Any],
    *,
    action: str,
    already_verified: bool = False,
    verify_all_fields: bool = False,
) -> dict[str, Any]:
    item_id = pending["item_id"]
    expected = pending["expected_fields"]
    cache: dict[str, tuple[str, str | None]] = {}
    location = pending["location"]
    if not already_verified:
        verify_saved_item(
            client, item_id, expected, live=location == "live",
            image_digests=pending["image_digests"], image_cache=cache,
            verify_all_fields=verify_all_fields,
        )
    published = location == "live"
    if location == "staged" and pending["should_publish"]:
        pending = checkpoint_verification(
            state, source_url, **{**pending, "location": "live"}
        )
        client.publish_item(item_id)
        verify_saved_item(
            client, item_id, expected, live=True,
            image_digests=pending["image_digests"], image_cache=cache,
            verify_all_fields=verify_all_fields,
        )
        published = True
    record_item_state(
        state, source_url, item_id, pending["signature"], published,
        expected.get("slug", ""),
    )
    print(f"Webflow item {action}: {item_id}. Published={published}.")
    return {"action": action, "item_id": item_id, "published": published, "read_back_verified": True}


def recover_pending_verification(
    client: WebflowClient,
    state: dict[str, Any],
    source_url: str,
    pending: dict[str, Any],
) -> dict[str, Any]:
    item_id = pending["item_id"]
    try:
        verify_saved_item(
            client, item_id, pending["expected_fields"],
            live=pending["location"] == "live",
            image_digests=pending["image_digests"],
            verify_all_fields=True,
        )
    except WebflowError as exc:
        if not isinstance(exc, WebflowVerificationMismatch) and not is_not_found_error(exc):
            raise
        # Repair only the saved item. Network/auth failures must not cause a rewrite.
        try:
            existing = client.get_item(item_id)
            location = "staged"
        except WebflowError as staged_exc:
            if not is_not_found_error(staged_exc):
                raise
            try:
                existing = client.get_live_item(item_id)
                location = "live"
            except WebflowError as live_exc:
                if not is_not_found_error(live_exc):
                    raise
                staged_matches = [item for item in client.list_items() if item_matches(item, source_url)]
                live_matches = [item for item in client.list_live_items() if item_matches(item, source_url)]
                matches = staged_matches + live_matches
                match_ids = {item_id_from(item) for item in matches}
                if not matches:
                    raise WebflowError("The pending CMS item is missing and no replacement has the same LinkedIn URL; no duplicate was created.") from live_exc
                if len(staged_matches) > 1 or len(live_matches) > 1 or len(match_ids) != 1 or "" in match_ids:
                    raise WebflowError("The pending CMS item has ambiguous replacements for its LinkedIn URL; no item was changed.") from live_exc
                replacement_id = item_id_from(matches[0])
                replacement = client.get_item(replacement_id) if staged_matches else client.get_live_item(replacement_id)
                if item_id_from(replacement) != replacement_id or not item_matches(replacement, source_url):
                    raise WebflowError("The pending CMS replacement does not match the saved LinkedIn URL.")
                pending = checkpoint_verification(
                    state, source_url, **{**pending, "item_id": replacement_id}
                )
                return recover_pending_verification(client, state, source_url, pending)
        if item_id_from(existing) != item_id or not item_matches(existing, source_url):
            raise WebflowError("The pending CMS repair returned a different item ID or LinkedIn URL.")
        if location == "staged" and pending["location"] == "live":
            try:
                verify_saved_item(
                    client, item_id, pending["expected_fields"], live=False,
                    image_digests=pending["image_digests"], verify_all_fields=True,
                )
            except WebflowVerificationMismatch:
                pass
            else:
                pending = checkpoint_verification(
                    state, source_url, **{**pending, "location": "staged"}
                )
                return finish_pending_write(
                    client, state, source_url, pending, action="verified_pending",
                    already_verified=True, verify_all_fields=True,
                )
        pending = checkpoint_verification(
            state, source_url, **{**pending, "location": location}
        )
        if location == "staged":
            client.update_item(item_id, pending["expected_fields"])
        else:
            client.update_live_item(item_id, pending["expected_fields"])
        return finish_pending_write(
            client, state, source_url, pending, action="repaired_pending",
            verify_all_fields=True,
        )
    return finish_pending_write(
        client, state, source_url, pending, action="verified_pending",
        already_verified=True, verify_all_fields=True,
    )


def sync_post_to_webflow(
    post: dict[str, Any], config: PipelineConfig
) -> dict[str, Any]:
    client = WebflowClient(config.webflow_api_token, config.webflow_collection_id)
    source_url = str(post.get("url") or "")
    signature = post_hash(post)

    state = load_webflow_state()
    pending = pending_verification_for(state, source_url, config.webflow_collection_id)
    if pending is not None:
        return recover_pending_verification(client, state, source_url, pending)

    live_item = find_item_by_source_url(client, source_url, live=True)
    live_item_id = item_id_from(live_item)
    if live_item_id and not config.force_webflow_sync:
        print(
            f"Webflow live item already exists for this LinkedIn URL: {live_item_id}. Skipping Webflow write."
        )
        return {
            "action": "skipped_existing_live_url",
            "item_id": live_item_id,
            "published": True,
        }

    state_entry = state_entry_for(state, source_url)
    if live_item_id:
        existing_item, item_location = live_item, "live"
    else:
        existing_item, item_location = find_existing_item(
            client, source_url, state_entry.get("item_id")
        )
    item_id = item_id_from(existing_item)

    if (
        existing_item
        and not config.force_webflow_sync
        and payload_is_current(state_entry, signature)
    ):
        slug = state_entry.get("slug") or item_slug(existing_item)
        published = state_entry.get("published", True)
        record_item_state(state, source_url, item_id, signature, published, slug)
        print(
            f"Webflow already has this LinkedIn URL: {item_id}. Skipping Webflow write."
        )
        return {"action": "skipped_existing_url", "item_id": item_id}

    field_data = build_field_data(post)
    image_digests = expected_image_digests(post)
    expected, item_id, item_location = expected_fields_for_write(
        client, source_url, item_id, item_location, field_data
    )
    if item_id:
        checkpoint_verification(
            state, source_url, item_id=item_id,
            collection_id=config.webflow_collection_id, signature=signature,
            expected_fields=expected, image_digests=image_digests,
            location=item_location,
            should_publish=config.webflow_publish or item_location == "live",
        )
    _, item_id, action = write_item_to_webflow(
        client, source_url, item_id, item_location, field_data
    )
    if action == "created":
        expected = deepcopy(field_data)
    pending = checkpoint_verification(
        state, source_url, item_id=item_id,
        collection_id=config.webflow_collection_id, signature=signature,
        expected_fields=expected, image_digests=image_digests,
        location="live" if action == "updated_live" else "staged",
        should_publish=config.webflow_publish or action == "updated_live",
    )
    return finish_pending_write(client, state, source_url, pending, action=action)
