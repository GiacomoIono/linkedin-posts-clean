from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse {path}: {exc}") from exc


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def strip_html_to_text(value: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", value or "")
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def sanitize_text(value: str) -> str:
    value = (value or "").replace("**", "").replace("__", "").replace("\u2014", "-")
    return re.sub(r"\s+", " ", value).strip().strip('"').strip()


def soft_trim(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:!?")


def post_identity(post: dict[str, Any] | None) -> str:
    if not isinstance(post, dict):
        return ""
    return str(post.get("url") or "").strip()


def post_hash(post: dict[str, Any]) -> str:
    payload = {
        "url": post.get("url", ""),
        "published_at": post.get("published_at", ""),
        "content": post.get("content", ""),
        "images": post.get("images", []),
        "headline": post.get("headline", ""),
        "description": post.get("description", ""),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def iso_to_webflow(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return raw
