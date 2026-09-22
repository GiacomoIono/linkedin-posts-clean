"""Turn local post images into verified Webflow-hosted image references."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .config import REPO_ROOT, PipelineConfig
from .image_generation import attach_generated_main_image
from .webflow_assets import ensure_webflow_asset


def local_image_path(image: dict[str, Any], *, generated: bool = False) -> Path:
    """Only pipeline-owned image files can become public Webflow assets."""
    value = image.get("local_path")
    if not isinstance(value, str) or not value:
        raise RuntimeError("Each post image must identify a local image file before upload.")
    relative = PurePosixPath(value)
    directory = ("images", "generated") if generated else ("images",)
    if relative.is_absolute() or relative.parts[:-1] != directory or ".." in relative.parts:
        raise RuntimeError("Post image paths must stay in their original or generated image directory.")
    filename = relative.name
    if image.get("filename", filename) != filename:
        raise RuntimeError("Post image filename does not match its local path.")
    root = REPO_ROOT.resolve()
    candidate = root.joinpath(*relative.parts)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or resolved.parent != root.joinpath(*directory):
        raise RuntimeError("Post image paths must not escape the repository through symlinks.")
    if not resolved.is_file():
        raise RuntimeError(f"The post image {filename} is missing locally. Stopping before enrichment.")
    return candidate


def prepare_post_images(post: dict[str, Any], config: PipelineConfig) -> dict[str, Any]:
    """Upload before vision enrichment; keep source and generated roles separate."""
    images = post.get("images")
    if images is None:
        images = []
    if not isinstance(images, list) or any(not isinstance(item, dict) for item in images):
        raise RuntimeError("Post images must be a list of local image records.")

    # Resolve all source paths before causing any upload side effects. In
    # particular a missing source must never silently become a generated hero.
    source_paths = [local_image_path(item) for item in images]
    prepared = attach_generated_main_image(post, config)
    generated = prepared.get("generated_main_image")
    generated_path = local_image_path(generated, generated=True) if generated else None

    def hosted(image: dict[str, Any], path: Path) -> dict[str, Any]:
        asset = ensure_webflow_asset(path, config)
        return {**asset, "alt": str(image.get("alt") or "")}

    prepared["images"] = [hosted(item, path) for item, path in zip(images, source_paths)]
    if generated_path is not None:
        prepared["generated_main_image"] = hosted(generated, generated_path)
    return prepared
