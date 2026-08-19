from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from openai import OpenAI

from .config import RAW_POST_PATH, PipelineConfig, load_config
from .enrichment import DESCRIPTION_MAX, HEADLINE_MAX, generate_seo, load_prompts
from .utils import load_json, strip_html_to_text


MAX_PREVIEW_RUNS = 20


def preview_run_count(value: str) -> int:
    try:
        runs = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("runs must be a whole number") from exc
    if not 1 <= runs <= MAX_PREVIEW_RUNS:
        raise argparse.ArgumentTypeError(f"runs must be between 1 and {MAX_PREVIEW_RUNS}")
    return runs


def load_saved_post(path: Path) -> dict[str, Any]:
    post = load_json(path, None)
    if not isinstance(post, dict):
        raise RuntimeError(f"Saved post must be a JSON object: {path}")
    if not strip_html_to_text(str(post.get("content") or "")):
        raise RuntimeError(f"Saved post has no content: {path}")
    return post


def generate_previews(
    post: dict[str, Any],
    config: PipelineConfig,
    runs: int,
) -> list[dict[str, str]]:
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing from the local environment or .env file.")

    plain_text = strip_html_to_text(str(post.get("content") or ""))
    prompts = load_prompts()
    client = OpenAI(api_key=config.openai_api_key)
    return [generate_seo(client, config, plain_text, prompts) for _ in range(runs)]


def print_previews(
    previews: list[dict[str, str]],
    post: dict[str, Any],
    post_path: Path,
    model: str,
) -> None:
    print("SEO-only live preview")
    print(f"Source file: {post_path}")
    print(f"Source post: {post.get('url') or 'URL not saved'}")
    print(f"Model: {model}")
    print("Network calls: OpenAI only.")
    print("LinkedIn, Webflow and X calls: none.")
    print("Files written: none.")

    for index, preview in enumerate(previews, start=1):
        headline = preview["headline"]
        description = preview["description"]
        print()
        print(f"Run {index}")
        print(f"Title ({len(headline)}/{HEADLINE_MAX} characters): {headline}")
        print(f"Description ({len(description)}/{DESCRIPTION_MAX} characters): {description}")
        print(json.dumps(preview, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate SEO metadata from a saved post using OpenAI only. "
            "This command never calls LinkedIn, Webflow or X and never writes files."
        )
    )
    parser.add_argument(
        "--post",
        type=Path,
        default=RAW_POST_PATH,
        help=f"Saved post JSON file (default: {RAW_POST_PATH})",
    )
    parser.add_argument(
        "--runs",
        type=preview_run_count,
        default=1,
        help=f"Number of OpenAI generations, from 1 to {MAX_PREVIEW_RUNS} (default: 1)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        post = load_saved_post(args.post)
        config = load_config()
        previews = generate_previews(post, config, args.runs)
        print_previews(previews, post, args.post, config.openai_model)
    except Exception as exc:
        print(f"SEO-only preview failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
