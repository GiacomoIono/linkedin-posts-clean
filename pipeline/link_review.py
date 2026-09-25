"""Best-effort, credential-safe diagnostics for optional evidence linking."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import traceback
from typing import Any
from urllib.parse import urlsplit
import uuid

from .config import DATA_DIR, PipelineConfig


LINK_REVIEW_DIR = DATA_DIR / "link_reviews"
SENSITIVE_NAME = re.compile(r"(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|AUTHORIZATION|CREDENTIAL)", re.I)
TOKEN_PATTERNS = (
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{10,}|gh[pousr]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,})\b"),
    re.compile(r"(?i)\bBearer\s+[^\s\"'<>]+"),
    re.compile(r"(?i)([?&](?:access_token|api_key|token|secret|password)=)[^&#\s\"'<>]+"),
    re.compile(r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|authorization|password|client_secret)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'<>}\[\]]+"),
)


def redact_diagnostics(value: Any, config: PipelineConfig) -> Any:
    """Return JSON-safe diagnostics without configured or recognisable secrets."""
    secrets = {
        getattr(config, "linkedin_access_token", ""),
        getattr(config, "openai_api_key", ""),
        getattr(config, "webflow_api_token", ""),
        *(value for name, value in os.environ.items() if SENSITIVE_NAME.search(name)),
    }
    ordered_secrets = sorted((secret for secret in secrets if secret), key=len, reverse=True)

    def clean_text(text: str) -> str:
        for secret in ordered_secrets:
            text = text.replace(secret, "[REDACTED]")
        for pattern in TOKEN_PATTERNS:
            text = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", text)
        return text

    def clean(item: Any, depth: int = 0) -> Any:
        if depth > 20:
            return "[diagnostic nesting limit]"
        if isinstance(item, str):
            return clean_text(item)
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, dict):
            return {
                clean_text(str(key)): clean(child, depth + 1)
                for key, child in item.items()
            }
        if isinstance(item, (list, tuple, set)):
            return [clean(child, depth + 1) for child in item]
        if isinstance(item, bytes):
            return clean_text(item.decode("utf-8", errors="replace"))
        return clean_text(str(item))

    return clean(value)


def exception_details(exc: Exception, config: PipelineConfig) -> dict[str, Any]:
    details: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }
    for attribute in ("status_code", "code", "request_id", "body"):
        value = getattr(exc, attribute, None)
        if value is not None:
            details[attribute] = value
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            details["response_text"] = response.text
        except Exception:
            details["response_text"] = "[response body unavailable]"
    return redact_diagnostics(details, config)


def _console_text(value: Any, config: PipelineConfig) -> str:
    return str(redact_diagnostics(value, config)).replace("\r", "\\r").replace("\n", "\\n")


def _actions_context() -> dict[str, Any]:
    context = {
        "repository": os.getenv("GITHUB_REPOSITORY", ""),
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
        "sha": os.getenv("GITHUB_SHA", ""),
    }
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    parsed = urlsplit(server)
    if (
        parsed.scheme == "https" and parsed.netloc and not parsed.username
        and not parsed.password and not parsed.query and not parsed.fragment
        and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", context["repository"])
        and context["run_id"].isdigit()
    ):
        context["run_url"] = f"{server}/{context['repository']}/actions/runs/{context['run_id']}"
        if context["run_attempt"].isdigit():
            context["attempt_url"] = f"{context['run_url']}/attempts/{context['run_attempt']}"
    return context


def _persist_report(report: dict[str, Any], config: PipelineConfig) -> None:
    # The runner-temp copy survives a failed commit/rebase and includes this run only.
    directories = [LINK_REVIEW_DIR]
    runner_temp = os.getenv("RUNNER_TEMP")
    if runner_temp:
        directories.append(Path(runner_temp) / "link-reviews")
    content = json.dumps(redact_diagnostics(report, config), ensure_ascii=False, indent=2) + "\n"
    for directory in directories:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{report['report_id']}.json"
            temporary = directory / f".{report['report_id']}.tmp"
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
            print(f"Link review report saved: {_console_text(target, config)}")
        except Exception as exc:
            print(f"Link review report could not be saved: {_console_text(exception_details(exc, config), config)}")


def _notify_review(report: dict[str, Any], config: PipelineConfig) -> None:
    message = (
        f"Evidence links need manual review for {report['source_url']}. "
        f"The original body is retained. Report: {report['report_path']}"
    )
    clean = str(redact_diagnostics(message, config))
    if os.getenv("GITHUB_ACTIONS") == "true":
        escaped = clean.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::warning title=Evidence links need manual review::{escaped}")
    else:
        print(f"WARNING: {_console_text(clean, config)}")
    print("Link review diagnostics: " + _console_text(json.dumps(report["audit"], ensure_ascii=False), config))
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        # Dynamic data stays in escaped HTML, preventing Markdown/HTML injection.
        summary = {
            "source_url": report["source_url"],
            "report_path": report["report_path"],
            "webflow": report["webflow"],
            "audit": report["audit"],
        }
        encoded = html.escape(json.dumps(redact_diagnostics(summary, config), ensure_ascii=False, indent=2))
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write("\n### Evidence links need manual review\n\nThe original body was retained.\n\n<pre>" + encoded + "</pre>\n")


def record_link_review(
    original_post: dict[str, Any],
    final_post: dict[str, Any],
    audit: dict[str, Any],
    config: PipelineConfig,
    *,
    report: dict[str, Any] | None = None,
    webflow_status: dict[str, Any] | None = None,
    webflow_error: Exception | None = None,
) -> dict[str, Any] | None:
    """Create/update a durable per-run report; diagnostics never block publication."""
    try:
        now = datetime.now(timezone.utc)
        initial = report is None
        if report is None:
            source_url = str(original_post.get("url") or "")
            source_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]
            report_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{source_hash}-{uuid.uuid4().hex[:8]}"
            report = {
                "schema_version": 1,
                "report_id": report_id,
                "report_path": f"data/link_reviews/{report_id}.json",
                "source_url": source_url,
                "published_at": original_post.get("published_at"),
                "headline": original_post.get("headline"),
                "created_at": now.isoformat(),
                "model": config.openai_model,
                "actions": _actions_context(),
                "original_html": original_post.get("content", ""),
                "final_html": final_post.get("content", ""),
                "audit": audit,
                "manual_review_required": bool(audit.get("manual_review_required")),
                "webflow": {"collection_id": config.webflow_collection_id, "status": "pending"},
            }
        report["updated_at"] = now.isoformat()
        if webflow_status is not None:
            report["webflow"].update({"status": "completed", **webflow_status})
        elif webflow_error is not None:
            report["webflow"].update({"status": "failed", "error": exception_details(webflow_error, config)})
        # No editor URL is fabricated: collection/item IDs alone do not identify a site.
        report = redact_diagnostics(report, config)
        _persist_report(report, config)
        if report["manual_review_required"] and (initial or webflow_status is not None or webflow_error is not None):
            _notify_review(report, config)
        return report
    except Exception as exc:
        print(f"Link review logging failed: {_console_text(exception_details(exc, config), config)}")
        return report
