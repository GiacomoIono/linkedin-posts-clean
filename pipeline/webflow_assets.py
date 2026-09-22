"""Upload local images to Webflow and checkpoint verified, public assets.

The manifest deliberately contains no upload policy, signature or credentials.
A pending entry lets a later run recover a file uploaded before interruption.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests
from PIL import Image

from .config import DATA_DIR, PipelineConfig

ASSET_MANIFEST_PATH = DATA_DIR / "webflow_assets.json"
MAX_ASSET_BYTES = 4_000_000
MAX_ATTEMPTS = 3
API_ROOT = "https://api.webflow.com/v2"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
FORMATS = {
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
    # Multi-picture JPEGs are normal JPEG files with additional image frames.
    "MPO": ("image/jpeg", {".jpg", ".jpeg"}),
    "PNG": ("image/png", {".png"}),
    "WEBP": ("image/webp", {".webp"}),
}
S3_BUCKETS = {"webflow-prod-assets", "webflow-dev-assets"}


class _RequestError(RuntimeError):
    def __init__(self, operation: str, status: int | None = None):
        self.status = status
        message = f"Webflow asset {operation} failed"
        if status is not None:
            message += f" (HTTP {status})"
        if operation in {"metadata", "create"} and status in {401, 403}:
            message += "; check WEBFLOW_API_TOKEN and Assets read/write access"
        if operation == "create" and (status is None or status == 408 or status >= 500):
            message += "; creation may have reached Webflow, so inspect Assets before rerunning"
        super().__init__(message + ".")


class _NotReady(RuntimeError):
    pass


def _no_implicit_auth(request: Any) -> Any:
    # Prevent requests from adding a local .netrc credential to public/S3 calls.
    return request


def _retry_delay(response: Any, attempt: int) -> float:
    raw = response.headers.get("Retry-After", "") if response is not None else ""
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(raw)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = 2 ** attempt
    return max(0.0, min(delay, 60.0))


def _request(method: str, url: str, operation: str, *, attempts: int = MAX_ATTEMPTS, **kwargs: Any) -> requests.Response:
    for attempt in range(attempts):
        response = None
        try:
            response = requests.request(
                method, url, timeout=(10, 45), allow_redirects=False,
                auth=_no_implicit_auth, **kwargs,
            )
        except requests.RequestException:
            # Asset creation has no idempotency key: a lost response may hide
            # a successful metadata write. Only an explicit 429 is safe to retry.
            if operation == "create" or attempt + 1 == attempts:
                raise _RequestError(operation) from None
        else:
            if 200 <= response.status_code < 300:
                return response
            status = response.status_code
            delay = _retry_delay(response, attempt)
            response.close()
            can_retry = status == 429 if operation == "create" else status in RETRY_STATUSES
            if not can_retry or attempt + 1 == attempts:
                raise _RequestError(operation, status) from None
        if response is None:
            delay = _retry_delay(None, attempt)
        time.sleep(delay)
    raise _RequestError(operation)


def _api(method: str, endpoint: str, config: PipelineConfig, operation: str, **kwargs: Any) -> dict[str, Any]:
    response = _request(method, API_ROOT + endpoint, operation,
                        headers={"Authorization": f"Bearer {config.webflow_api_token}"}, **kwargs)
    try:
        data = response.json()
    except (ValueError, requests.RequestException):
        raise RuntimeError(f"Webflow asset {operation} returned invalid JSON.") from None
    finally:
        response.close()
    if not isinstance(data, dict):
        raise RuntimeError(f"Webflow asset {operation} returned invalid metadata.")
    return data


def _allowed_url(value: Any, *, upload: bool = False) -> str:
    if not isinstance(value, str):
        raise RuntimeError("Webflow returned a missing asset URL.")
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        valid_base = parsed.scheme == "https" and parsed.port in {None, 443} and not parsed.username and not parsed.password and not parsed.fragment
    except ValueError:
        valid_base = False
        host = ""
    allowed = False
    if valid_base:
        path_bucket = unquote(parsed.path).lstrip("/").split("/", 1)[0]
        if re.fullmatch(r"s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com", host):
            allowed = path_bucket in S3_BUCKETS
        else:
            for bucket in S3_BUCKETS:
                if re.fullmatch(re.escape(bucket) + r"\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com", host):
                    allowed = True
        if not upload and host.endswith(".website-files.com"):
            allowed = True
        if not upload and host in {"uploads-ssl.webflow.com", "uploads.webflow.com"}:
            allowed = True
        # A CMS/OpenAI URL must stay public after upload credentials expire.
        if not upload and parsed.query:
            allowed = False
    if not allowed:
        raise RuntimeError("Webflow returned an unsupported or non-public asset destination.")
    return value


def _inspect_image(data: bytes) -> tuple[str, tuple[int, int]]:
    if not data or len(data) > MAX_ASSET_BYTES:
        raise ValueError("Webflow image must contain data and be no larger than 4 MB.")
    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            dimensions = image.size
            image.verify()
        with Image.open(BytesIO(data)) as image:
            for frame in range(getattr(image, "n_frames", 1)):
                image.seek(frame)
                image.load()
        if image_format not in FORMATS or min(dimensions) <= 0:
            raise ValueError
    except Exception:
        raise ValueError("Webflow image must be a valid JPEG, PNG or WebP file.") from None
    return image_format, dimensions


def _load_manifest() -> dict[str, Any]:
    if not ASSET_MANIFEST_PATH.exists():
        return {"version": 1, "assets": {}}
    try:
        manifest = json.loads(ASSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise RuntimeError("Webflow asset manifest is unreadable; restore it before uploading to preserve existing upload records.") from None
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("assets"), dict):
        raise RuntimeError("Webflow asset manifest has an unsupported structure; restore it before uploading.")
    return manifest


def _checkpoint(key: str, entry: dict[str, Any]) -> None:
    # Reload to retain checkpoints made since this call began. Pipeline uploads
    # are sequential; the workflow also serialises runs with a concurrency group.
    manifest = _load_manifest()
    manifest["assets"][key] = entry
    ASSET_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ASSET_MANIFEST_PATH.parent, prefix=".webflow-assets-", suffix=".tmp", delete=False) as handle:
            temporary_path = Path(handle.name)
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, ASSET_MANIFEST_PATH)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _hosted_bytes(url: str) -> bytes:
    response = _request("GET", _allowed_url(url), "public download", stream=True)
    try:
        data = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_ASSET_BYTES:
                raise _NotReady("Webflow public image exceeds the upload limit.")
        return bytes(data)
    except requests.RequestException:
        raise _RequestError("public download") from None
    finally:
        response.close()


def _verify_hosted(url: str, sha256: str, dimensions: tuple[int, int]) -> None:
    for attempt in range(MAX_ATTEMPTS):
        try:
            data = _hosted_bytes(url)
            _, actual_dimensions = _inspect_image(data)
            if hashlib.sha256(data).hexdigest() == sha256 and actual_dimensions == dimensions:
                return
        except _RequestError as exc:
            # A just-uploaded public file can briefly return 403/404. Other
            # HTTP/network failures must not trigger duplicate asset creation.
            if exc.status not in {403, 404}:
                raise
        except (ValueError, _NotReady):
            pass
        if attempt + 1 < MAX_ATTEMPTS:
            time.sleep(2 ** attempt)
    raise _NotReady("Webflow public image is missing or does not match the local file after readiness checks.")


def _asset_metadata(asset_id: str, config: PipelineConfig) -> dict[str, Any]:
    if not re.fullmatch(r"[a-fA-F0-9]{24}", asset_id):
        raise RuntimeError("Webflow asset record has an invalid asset ID.")
    metadata = _api("GET", f"/assets/{asset_id}", config, "metadata")
    if metadata.get("id") != asset_id or metadata.get("siteId", config.webflow_site_id) != config.webflow_site_id:
        raise RuntimeError("Webflow asset metadata does not match the requested asset/site.")
    return metadata


def _reconcile_upload_intent(
    intent: dict[str, Any], config: PipelineConfig,
) -> dict[str, Any]:
    """Find a metadata write whose response was lost, without creating again."""
    filename = intent.get("upload_filename")
    if not isinstance(filename, str) or not filename:
        raise RuntimeError("Webflow upload intent lacks its remote filename; restore the manifest before retrying.")
    matches: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    offset = 0
    expected_total: int | None = None
    while True:
        page = _api("GET", f"/sites/{config.webflow_site_id}/assets", config, "metadata",
                    params={"limit": 100, "offset": offset})
        batch = page.get("assets")
        pagination = page.get("pagination")
        if not isinstance(batch, list) or not isinstance(pagination, dict):
            raise RuntimeError("Webflow asset reconciliation returned invalid pagination; keeping the upload intent.")
        total = pagination.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise RuntimeError("Webflow asset reconciliation omitted its total; keeping the upload intent.")
        if expected_total is None:
            expected_total = total
        if total != expected_total or pagination.get("offset", offset) != offset:
            raise RuntimeError("Webflow assets changed during reconciliation; retry without clearing the upload intent.")
        if not batch and offset < total:
            raise RuntimeError("Webflow asset reconciliation returned an incomplete page; keeping the upload intent.")
        if offset + len(batch) > total:
            raise RuntimeError("Webflow asset reconciliation returned an inconsistent count; keeping the upload intent.")
        for asset in batch:
            if not isinstance(asset, dict):
                raise RuntimeError("Webflow asset reconciliation returned invalid metadata; keeping the upload intent.")
            asset_id = str(asset.get("id", ""))
            if not re.fullmatch(r"[a-fA-F0-9]{24}", asset_id):
                raise RuntimeError("Webflow asset reconciliation returned an invalid asset ID.")
            if asset_id in seen_ids:
                raise RuntimeError("Webflow asset reconciliation returned overlapping pages; retry without clearing the upload intent.")
            seen_ids.add(asset_id)
            if asset.get("originalFileName") == filename:
                matches[asset_id] = asset
        offset += len(batch)
        if offset >= total:
            break
    if not matches:
        raise RuntimeError(
            "An earlier Webflow asset creation has an uncertain outcome and no matching asset is listed yet. "
            "Inspect Assets before clearing this upload intent; no duplicate upload was attempted."
        )
    if len(matches) != 1:
        raise RuntimeError(
            "Multiple Webflow assets match the earlier upload intent. "
            "Resolve the duplicate asset records before retrying; no new upload was attempted."
        )
    return {**intent, "asset_id": next(iter(matches)), "status": "pending"}


def ensure_webflow_asset(path: Path, config: PipelineConfig) -> dict[str, Any]:
    """Return a verified public asset for these exact bytes and this Webflow site."""
    if not config.webflow_api_token:
        raise ValueError("WEBFLOW_API_TOKEN is required for Webflow asset uploads.")
    site_id = config.webflow_site_id
    if not re.fullmatch(r"[a-fA-F0-9]{24}", site_id or ""):
        raise ValueError("WEBFLOW_SITE_ID must be a valid Webflow site ID.")
    path = Path(path)
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_ASSET_BYTES + 1)
    except OSError:
        raise ValueError("Local image is missing or unreadable for Webflow upload.") from None
    image_format, dimensions = _inspect_image(data)
    mime, extensions = FORMATS[image_format]
    # Existing source files sometimes have a misleading extension (e.g. PNG
    # bytes named .jpg). Keep their original names for ordering/provenance;
    # correct only the remote filename and MIME, without changing image bytes.
    extension = path.suffix.lower()
    if extension not in extensions:
        extension = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image_format]
    sha256 = hashlib.sha256(data).hexdigest()
    key = f"{site_id}:{sha256}"
    cached = _load_manifest()["assets"].get(key)
    if cached is not None:
        if not isinstance(cached, dict) or cached.get("sha256") != sha256 or cached.get("site_id") != site_id:
            raise RuntimeError("Webflow asset cache entry is corrupt; restore the manifest before uploading.")
        if cached.get("status") == "creating":
            cached = _reconcile_upload_intent(cached, config)
            _checkpoint(key, cached)
        if cached.get("status") not in {"ready", "pending", "create_rejected"}:
            raise RuntimeError("Webflow asset cache entry has an unknown upload status; restore it before uploading.")
    if cached is not None and cached.get("status") != "create_rejected":
        try:
            metadata = _asset_metadata(str(cached.get("asset_id", "")), config)
            url = _allowed_url(metadata.get("hostedUrl") or metadata.get("assetUrl"))
            _verify_hosted(url, sha256, dimensions)
        except _RequestError as exc:
            if exc.status != 404:
                raise
            if cached.get("status") != "ready":
                raise RuntimeError(
                    "An earlier Webflow upload has pending metadata that is not readable yet. "
                    "Inspect the recorded asset before retrying; no duplicate was created."
                ) from None
        except _NotReady:
            raise RuntimeError(
                "The recorded Webflow asset exists but its public file is missing or differs from the local image. "
                "Restore that asset or remove its confirmed incomplete metadata and matching cache entry before retrying; "
                "no duplicate upload was attempted."
            ) from None
        else:
            result = {**cached, "url": url, "status": "ready"}
            _checkpoint(key, result)
            return {"url": url, "asset_id": result["asset_id"], "sha256": sha256, "filename": path.name}

    # Sanitising keeps control characters and long Unicode names out of the
    # multipart headers. The content suffix makes truncation deterministic.
    stem = re.sub(r"[^a-zA-Z0-9_-]", "-", path.stem).strip("-") or "image"
    filename = f"{stem[:70]}-{sha256[:16]}{extension}"
    intent = {"site_id": site_id, "sha256": sha256, "filename": path.name,
              "upload_filename": filename, "status": "creating"}
    # A crash, lost create response or invalid response shape must leave enough
    # durable information to reconcile the server before another metadata write.
    _checkpoint(key, intent)
    try:
        created = _api("POST", f"/sites/{site_id}/assets", config, "create",
                       json={"fileName": filename, "fileHash": hashlib.md5(data).hexdigest()})
    except _RequestError as exc:
        if exc.status is not None and 400 <= exc.status < 500 and exc.status != 408:
            _checkpoint(key, {**intent, "status": "create_rejected"})
        raise
    asset_id = str(created.get("id", ""))
    if not re.fullmatch(r"[a-fA-F0-9]{24}", asset_id):
        raise RuntimeError("Webflow asset creation returned an invalid asset ID.")
    pending = {**intent, "asset_id": asset_id, "status": "pending"}
    _checkpoint(key, pending)
    url = _allowed_url(created.get("hostedUrl") or created.get("assetUrl"))
    pending["url"] = url
    _checkpoint(key, pending)
    details = created.get("uploadDetails")
    if details is not None or created.get("uploadUrl") is not None:
        upload_url = _allowed_url(created.get("uploadUrl"), upload=True)
        if not isinstance(details, dict) or not details or "file" in details or not all(isinstance(k, str) and isinstance(v, str) for k, v in details.items()):
            raise RuntimeError("Webflow returned invalid multipart upload details.")
        # requests prepares data fields first, then files: S3 requires file last.
        response = _request("POST", upload_url, "file upload", data=list(details.items()),
                            files=[("file", (filename, data, mime))])
        response.close()
    # Even when Webflow returns a deduplicated asset without upload details,
    # metadata existence is insufficient: its unauthenticated bytes must work.
    metadata = _asset_metadata(asset_id, config)
    url = _allowed_url(metadata.get("hostedUrl") or metadata.get("assetUrl") or url)
    _verify_hosted(url, sha256, dimensions)
    ready = {**pending, "url": url, "status": "ready"}
    _checkpoint(key, ready)
    return {"url": url, "asset_id": asset_id, "sha256": sha256, "filename": path.name}
