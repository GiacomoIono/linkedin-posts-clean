from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
IMAGE_DIR = REPO_ROOT / "images"
CONFIG_DIR = REPO_ROOT / "config"

RAW_POST_PATH = DATA_DIR / "last_linkedin_post.json"
ENRICHED_POST_PATH = DATA_DIR / "last_linkedin_post.enriched.json"
TWEET_PATH = DATA_DIR / "tweet.json"
POSTED_TWEETS_PATH = DATA_DIR / "posted_tweets.json"
PIPELINE_STATE_PATH = DATA_DIR / "pipeline_state.json"
WEBFLOW_STATE_PATH = DATA_DIR / "webflow_items.json"

LEGACY_RAW_POST_PATH = REPO_ROOT / "last_linkedin_post.json"
LEGACY_ENRICHED_POST_PATH = REPO_ROOT / "last_linkedin_post.enriched.json"
LEGACY_TWEET_PATH = REPO_ROOT / "Twitter" / "tweet.json"
LEGACY_POSTED_TWEETS_PATH = REPO_ROOT / "Twitter" / "posted_tweets.json"

PROMPTS_PATH = CONFIG_DIR / "prompts.json"
LEGACY_PROMPTS_PATH = REPO_ROOT / "prompts.json"

DEFAULT_OPENAI_MODEL = "gpt-5-nano"
DEFAULT_WEBFLOW_COLLECTION_ID = "63250855178122098387d7ef"
NO_POSTS_FOUND_EXIT_CODE = 2


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


@dataclass(frozen=True)
class PipelineConfig:
    linkedin_access_token: str
    openai_api_key: str
    openai_model: str
    webflow_api_token: str
    webflow_collection_id: str
    webflow_publish: bool
    x_access_token: str
    x_client_id: str
    x_client_secret: str
    require_x_posting: bool
    force_webflow_sync: bool
    force_enrich: bool
    force_tweetify: bool
    force_x_post: bool


def load_config() -> PipelineConfig:
    load_dotenv()
    return PipelineConfig(
        linkedin_access_token=first_env("LINKEDIN_ACCESS_TOKEN"),
        openai_api_key=first_env("OPENAI_API_KEY"),
        openai_model=first_env("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
        webflow_api_token=first_env("WEBFLOW_API_TOKEN", "WEBFLOW_READ_AND_WRITE_BLOG_POSTS"),
        webflow_collection_id=first_env("WEBFLOW_COLLECTION_ID") or DEFAULT_WEBFLOW_COLLECTION_ID,
        webflow_publish=env_bool("WEBFLOW_PUBLISH", True),
        x_access_token=first_env("X_ACCESS_TOKEN"),
        x_client_id=first_env("X_CLIENT_ID"),
        x_client_secret=first_env("X_CLIENT_SECRET"),
        require_x_posting=env_bool("REQUIRE_X_POSTING", False),
        force_webflow_sync=env_bool("FORCE_WEBFLOW_SYNC", False),
        force_enrich=env_bool("FORCE_ENRICH", False),
        force_tweetify=env_bool("FORCE_TWEETIFY", False),
        force_x_post=env_bool("FORCE_X_POST", False),
    )


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "Twitter").mkdir(parents=True, exist_ok=True)
