"""Read-only audit of staged/live CMS image dependencies before repo privacy changes."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import requests

from .config import load_config
from .utils import write_json
from .webflow import WebflowClient, WebflowError

REPOSITORY_OWNER = "giacomoiono"
REPOSITORY_NAME = "linkedin-posts-clean"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif", ".bmp", ".ico")
CSS_URL_RE = re.compile(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^)]*))\s*\)", re.IGNORECASE)
LAZY_IMAGE_ATTRIBUTES = {
    "data-src", "data-srcset", "data-lazy-src", "data-lazy-srcset", "lazy-src",
    "lazy-srcset", "data-original", "data-image", "data-background", "data-bg",
    "data-lazyload", "data-lazy",
}


class ImageAuditError(RuntimeError):
    pass


def normalise_url(url: str) -> str:
    url = html.unescape(url).strip()
    return "https:" + url if url.startswith("//") else url


def repository_dependent_url(url: str) -> bool:
    try:
        parsed = urlsplit(normalise_url(url))
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    parts = unquote(parsed.path).strip("/").lower().split("/")
    repository = [REPOSITORY_OWNER, REPOSITORY_NAME]
    if host in {"github.com", "www.github.com", "raw.githubusercontent.com", "raw.github.com", "raw.githack.com", "rawcdn.githack.com", "rawgit.com", "cdn.rawgit.com"}:
        return parts[:2] == repository
    if host == "media.githubusercontent.com":
        return parts[:3] == ["media", *repository]
    if host == "jsdelivr.net" or host.endswith(".jsdelivr.net"):
        return len(parts) >= 3 and parts[:2] == ["gh", REPOSITORY_OWNER] and parts[2].split("@", 1)[0] == REPOSITORY_NAME
    if host == f"{REPOSITORY_OWNER}.github.io":
        return parts[0] == REPOSITORY_NAME
    return False


def report_url(url: str) -> str:
    """Keep useful public references without retaining URL credentials or signatures."""
    try:
        parsed = urlsplit(normalise_url(url))
        host = parsed.hostname or ""
        if parsed.port:
            host += f":{parsed.port}"
        query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            sensitive = re.search(r"token|secret|password|credential|signature|api.?key|authorization", key, re.IGNORECASE)
            query.append((key, "REDACTED" if sensitive else value))
        return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), ""))
    except ValueError:
        return "[invalid URL]"


def image_link_url(url: str) -> bool:
    try:
        return unquote(urlsplit(normalise_url(url)).path).lower().endswith(IMAGE_EXTENSIONS)
    except ValueError:
        return False


def srcset_urls(value: str) -> list[str]:
    """Collect srcset URL tokens without mistaking width/density descriptors for URLs."""
    urls = []
    remaining = value
    while remaining:
        remaining = remaining.lstrip(" \t\n\r\f,")
        if not remaining:
            break
        match = re.match(r"\S+", remaining)
        assert match is not None
        url = match.group(0)
        remaining = remaining[len(url):]
        if url.endswith(","):
            urls.append(url.rstrip(","))
            continue
        urls.append(url)
        comma = remaining.find(",")
        remaining = remaining[comma + 1:] if comma >= 0 else ""
    return urls


class BodyImageReferences(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images: list[tuple[str, str]] = []
        self.links: list[tuple[str, str]] = []
        self.anchors: list[dict[str, Any]] = []
        self.in_style = False
        self.style_parts: list[str] = []
        self.in_lightbox_json = False
        self.json_parts: list[str] = []

    def css_images(self, value: str, field: str) -> None:
        for match in CSS_URL_RE.finditer(value):
            url = next(part for part in match.groups() if part is not None).strip()
            if url:
                self.images.append((field, url))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "a" and values.get("href"):
            self.anchors.append({"url": values["href"], "image": image_link_url(values["href"]) or "w-lightbox" in values.get("class", "").split()})
        if tag in {"img", "picture"}:
            for anchor in self.anchors:
                anchor["image"] = True
        for name, value in values.items():
            if not value:
                continue
            image_attr = name in LAZY_IMAGE_ATTRIBUTES or (
                tag in {"img", "source", "image"} and name in {"src", "srcset", "href", "xlink:href"}
            ) or (tag == "video" and name == "poster") or (tag == "input" and values.get("type", "").lower() == "image" and name == "src")
            if image_attr:
                urls = srcset_urls(value) if "srcset" in name else [value]
                self.images.extend((f"post-body.{tag}[{name}]", url) for url in urls)
            if name == "style":
                self.css_images(value, f"post-body.{tag}[style]")
        if tag == "style":
            self.in_style = True
            self.style_parts = []
        if tag == "script" and "w-json" in values.get("class", "").split():
            self.in_lightbox_json = True
            self.json_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def finish_anchor(self) -> None:
        anchor = self.anchors.pop()
        target = self.images if anchor["image"] else self.links
        target.append(("post-body.a[href]", anchor["url"]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchors:
            self.finish_anchor()
        if tag == "style" and self.in_style:
            self.css_images("".join(self.style_parts), "post-body.style")
            self.in_style = False
        if tag == "script" and self.in_lightbox_json:
            self.in_lightbox_json = False
            try:
                data = json.loads("".join(self.json_parts))
            except (ValueError, TypeError) as exc:
                raise ImageAuditError("Embedded Webflow lightbox JSON could not be inspected.") from exc
            self.lightbox_images(data)

    def lightbox_images(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"url", "src", "thumbnailUrl"} and isinstance(child, str) and (
                    value.get("type") == "image" or key == "thumbnailUrl" or image_link_url(child)
                ):
                    self.images.append((f"post-body.lightbox[{key}]", child))
                self.lightbox_images(child)
        elif isinstance(value, list):
            for child in value:
                self.lightbox_images(child)

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.style_parts.append(data)
        if self.in_lightbox_json:
            self.json_parts.append(data)

    def close(self) -> None:
        super().close()
        while self.anchors:
            self.finish_anchor()
        if self.in_style:
            self.css_images("".join(self.style_parts), "post-body.style")
        if self.in_lightbox_json:
            raise ImageAuditError("Embedded Webflow lightbox JSON is incomplete.")


def fetch_complete_items(client: WebflowClient, *, live: bool) -> list[dict[str, Any]]:
    items = []
    seen = set()
    offset = 0
    limit = 100
    expected_total = None
    endpoint = "items/live" if live else "items"
    while True:
        response = client.request("GET", f"/collections/{client.collection_id}/{endpoint}", params={"offset": offset, "limit": limit})
        if not isinstance(response, dict) or not isinstance(response.get("items"), list):
            raise ImageAuditError("CMS response is missing its items list.")
        pagination = response.get("pagination")
        if not isinstance(pagination, dict):
            raise ImageAuditError("CMS response is missing complete pagination metadata.")
        for key in ("total", "offset", "limit"):
            if type(pagination.get(key)) is not int or pagination[key] < 0:
                raise ImageAuditError(f"CMS pagination {key} is invalid.")
        if pagination["offset"] != offset or pagination["limit"] != limit:
            raise ImageAuditError("CMS pagination does not match the requested page.")
        if expected_total is None:
            expected_total = pagination["total"]
        elif pagination["total"] != expected_total:
            raise ImageAuditError("CMS item total changed during pagination; rerun the audit.")
        batch = response["items"]
        if len(batch) > limit or len(items) + len(batch) > expected_total:
            raise ImageAuditError("CMS returned more items than its pagination permits.")
        for item in batch:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                raise ImageAuditError("CMS response contains an item with a missing or invalid ID.")
            if item["id"] in seen:
                raise ImageAuditError("CMS pagination returned a duplicate item ID.")
            seen.add(item["id"])
            items.append(item)
        if len(items) == expected_total:
            return items
        if not batch:
            raise ImageAuditError("CMS pagination stopped before all items were returned.")
        offset += len(batch)


def audit_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    hosts: Counter[str] = Counter()
    result: dict[str, Any] = {"complete": True, "items_audited": len(items), "native_image_references": 0, "embedded_image_references": 0, "image_host_counts": {}, "image_dependencies": [], "repository_hyperlinks": [], "errors": []}

    for item in items:
        fields = item.get("fieldData")
        identity = {"item_id": item["id"]}
        if not isinstance(fields, dict):
            result["errors"].append({**identity, "error": "CMS item is missing fieldData."})
            continue
        if isinstance(fields.get("slug"), str):
            identity["slug"] = fields["slug"]

        def record_image(field: str, url: Any, *, native: bool) -> None:
            if not isinstance(url, str) or not url.strip():
                result["errors"].append({**identity, "field": field, "error": "Image reference has no valid URL."})
                return
            try:
                parsed = urlsplit(normalise_url(url))
                host = parsed.hostname
            except ValueError:
                parsed = None
                host = None
            if not host and (parsed is None or parsed.scheme != "data"):
                result["errors"].append({**identity, "field": field, "url": report_url(url), "error": "Image URL host could not be resolved from CMS content."})
                return
            hosts[(host or "(embedded data)").lower()] += 1
            result["native_image_references" if native else "embedded_image_references"] += 1
            if repository_dependent_url(url):
                result["image_dependencies"].append({**identity, "field": field, "url": report_url(url)})

        for key in ("main-image", "thumbnail-image", "post-images"):
            value = fields.get(key)
            if value is None or value == "" or value == []:
                continue
            images = value if key == "post-images" else [value]
            if not isinstance(images, list):
                result["errors"].append({**identity, "field": key, "error": "CMS gallery is not an image list."})
                continue
            for index, image in enumerate(images):
                record_image(f"{key}[{index}]" if key == "post-images" else key, image.get("url") if isinstance(image, dict) else None, native=True)
        body = fields.get("post-body") or ""
        if not isinstance(body, str):
            result["errors"].append({**identity, "field": "post-body", "error": "CMS body is not HTML text."})
            continue
        parser = BodyImageReferences()
        try:
            parser.feed(body)
            parser.close()
        except ImageAuditError as exc:
            result["errors"].append({**identity, "field": "post-body", "error": str(exc)})
            continue
        for field, url in parser.images:
            record_image(field, url, native=False)
        for field, url in parser.links:
            if repository_dependent_url(url):
                result["repository_hyperlinks"].append({**identity, "field": field, "url": report_url(url)})
    result["image_host_counts"] = dict(sorted(hosts.items()))
    result["complete"] = not result["errors"]
    return result


def audit_collection(client: WebflowClient) -> dict[str, Any]:
    report: dict[str, Any] = {"audit_version": 1, "audited_at": datetime.now(timezone.utc).isoformat(), "collection_id": client.collection_id, "repository": f"{REPOSITORY_OWNER}/{REPOSITORY_NAME}", "read_only": True, "endpoints": {}}
    for location in ("staged", "live"):
        try:
            items = fetch_complete_items(client, live=location == "live")
            report["endpoints"][location] = audit_items(items)
        except (ImageAuditError, WebflowError, requests.RequestException) as exc:
            if isinstance(exc, ImageAuditError):
                message = str(exc)
            else:
                status = re.search(r"failed: (\d{3})\b", str(exc))
                message = "CMS request failed" + (f" with HTTP {status.group(1)}." if status else ".")
            report["endpoints"][location] = {"complete": False, "items_audited": 0, "errors": [{"error": message}]}
    report["complete"] = all(endpoint["complete"] for endpoint in report["endpoints"].values())
    report["image_dependency_count"] = sum(len(endpoint.get("image_dependencies", [])) for endpoint in report["endpoints"].values())
    report["repository_hyperlink_count"] = sum(len(endpoint.get("repository_hyperlinks", [])) for endpoint in report["endpoints"].values())
    report["ready_for_private_images"] = report["complete"] and report["image_dependency_count"] == 0
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Write a compact JSON audit report to this path.")
    arguments = parser.parse_args(argv)
    config = load_config()
    try:
        client = WebflowClient(config.webflow_api_token, config.webflow_collection_id)
    except WebflowError:
        print("CMS audit could not start: configure the Webflow API token and collection ID.")
        return 2
    report = audit_collection(client)
    write_json(arguments.output, report)
    counts = ", ".join(f"{name}={endpoint['items_audited']}" for name, endpoint in report["endpoints"].items())
    print(f"CMS image audit: {counts}; complete={report['complete']}; image dependencies={report['image_dependency_count']}; repository hyperlinks={report['repository_hyperlink_count']}.")
    print(f"Report: {arguments.output.resolve()}")
    return 0 if report["ready_for_private_images"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
