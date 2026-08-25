from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .config import GENERATED_IMAGE_MANIFEST_PATH
from .utils import load_json, post_identity, write_json

GENERATED_IMAGE_MANIFEST_VERSION = 1


def empty_generated_image_manifest() -> dict[str, Any]:
    return {"version": GENERATED_IMAGE_MANIFEST_VERSION, "files": {}}


def load_generated_image_manifest() -> dict[str, Any]:
    manifest = load_json(
        GENERATED_IMAGE_MANIFEST_PATH,
        empty_generated_image_manifest(),
    )
    if not isinstance(manifest, dict):
        raise RuntimeError("The generated-image manifest must be a JSON object.")
    if manifest.get("version") != GENERATED_IMAGE_MANIFEST_VERSION:
        raise RuntimeError("The generated-image manifest version is unsupported.")
    if not isinstance(manifest.get("files"), dict):
        raise RuntimeError(
            "The generated-image manifest files value must be an object."
        )
    return manifest


def write_generated_image_manifest(manifest: dict[str, Any]) -> None:
    temporary_path = GENERATED_IMAGE_MANIFEST_PATH.with_suffix(
        GENERATED_IMAGE_MANIFEST_PATH.suffix + ".tmp"
    )
    try:
        write_json(temporary_path, manifest)
        temporary_path.replace(GENERATED_IMAGE_MANIFEST_PATH)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def image_sha256(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def generated_image_entry(filename: str) -> dict[str, Any] | None:
    entry = load_generated_image_manifest()["files"].get(filename)
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise RuntimeError(
            f"The generated-image manifest entry for {filename} must be an object."
        )
    return entry


def registered_generated_filenames() -> set[str]:
    return set(load_generated_image_manifest()["files"])


def validate_registered_generated_image(
    path: Path,
    post_url: str | None = None,
) -> dict[str, Any]:
    entry = generated_image_entry(path.name)
    if entry is None:
        raise RuntimeError(
            f"{path.name} exists but is not registered as an OpenAI-generated main image. "
            "It will not be overwritten or treated as a generated fallback."
        )

    registered_url = str(entry.get("post_url") or "")
    if post_url and registered_url != post_url:
        raise RuntimeError(
            f"The date filename {path.name} is already registered to another LinkedIn post. "
            "Date-only filenames cannot safely represent two posts published on the same day."
        )

    expected_hash = str(entry.get("sha256") or "")
    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"The generated-image manifest references {path.name}, but that file is missing."
        ) from exc

    if not expected_hash or image_sha256(image_bytes) != expected_hash:
        raise RuntimeError(
            f"The generated image {path.name} does not match its manifest checksum. "
            "Stopping before it can be published as source media."
        )
    return entry


def ensure_generated_filename_available(
    path: Path,
    post: dict[str, Any],
) -> dict[str, Any] | None:
    post_url = post_identity(post)
    if not post_url:
        raise RuntimeError(
            "Cannot register a generated image without a LinkedIn post URL."
        )

    entry = generated_image_entry(path.name)
    if entry is not None:
        registered_url = str(entry.get("post_url") or "")
        if registered_url != post_url:
            raise RuntimeError(
                f"The date filename {path.name} is already registered to another LinkedIn post. "
                "Date-only filenames cannot safely represent two posts published on the same day."
            )
        if path.exists():
            validate_registered_generated_image(path, post_url)
        return entry

    if path.exists():
        raise RuntimeError(
            f"The normal image path {path.name} already exists and is not registered as generated. "
            "It will not be overwritten."
        )
    return None


def record_generated_image(
    post: dict[str, Any],
    filename: str,
    image_bytes: bytes,
    model: str,
) -> None:
    post_url = post_identity(post)
    if not post_url:
        raise RuntimeError(
            "Cannot register a generated image without a LinkedIn post URL."
        )

    manifest = load_generated_image_manifest()
    existing = manifest["files"].get(filename)
    if isinstance(existing, dict) and str(existing.get("post_url") or "") != post_url:
        raise RuntimeError(
            f"The date filename {filename} is already registered to another LinkedIn post."
        )

    manifest["files"][filename] = {
        "post_url": post_url,
        "published_at": str(post.get("published_at") or ""),
        "sha256": image_sha256(image_bytes),
        "model": model,
    }
    write_generated_image_manifest(manifest)
