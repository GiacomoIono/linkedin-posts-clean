# LinkedIn Posts Clean

Automation for turning the latest LinkedIn post into a Webflow CMS blog item, with OpenAI enrichment and optional X posting.

The pipeline:

1. Fetches recent LinkedIn post activity.
2. Converts the newest post into HTML.
3. Attaches matching images from `images/`.
4. Generates SEO headline, description, and missing image alt text.
5. Creates or updates the matching item in Webflow CMS.
6. Optionally generates and publishes an X post.
7. Stores run state and generated artifacts under `data/`.

## Project Structure

```text
pipeline/                 Python pipeline code
config/prompts.json        OpenAI prompt profiles for SEO, alt text, and X posts
data/                      Latest raw/enriched posts, X ledger, and Webflow sync state
images/                    Post images named by LinkedIn post date
tests/                     Unit tests for Webflow payload behavior
.github/workflows/         Scheduled GitHub Actions pipeline
requirements.txt           Python dependencies
```

## Setup

Use Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a local `.env` file for secrets and runtime settings. The file is ignored by git.

```bash
LINKEDIN_ACCESS_TOKEN=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-nano

WEBFLOW_API_TOKEN=
WEBFLOW_COLLECTION_ID=63250855178122098387d7ef
WEBFLOW_PUBLISH=true

X_ACCESS_TOKEN=
REQUIRE_X_POSTING=false
```

`WEBFLOW_READ_AND_WRITE_BLOG_POSTS` can be used instead of `WEBFLOW_API_TOKEN`.

## Environment Variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `LINKEDIN_ACCESS_TOKEN` | Yes | None | Reads recent LinkedIn member change logs. |
| `OPENAI_API_KEY` | Yes for new/enriched posts | None | Generates SEO metadata, alt text, and X drafts. |
| `OPENAI_MODEL` | No | `gpt-5-nano` | Model used for OpenAI chat completions. |
| `WEBFLOW_API_TOKEN` | Yes | None | Creates, updates, and publishes Webflow CMS items. |
| `WEBFLOW_READ_AND_WRITE_BLOG_POSTS` | No | None | Fallback Webflow token variable. |
| `WEBFLOW_COLLECTION_ID` | No | `63250855178122098387d7ef` | Target Webflow collection. |
| `WEBFLOW_PUBLISH` | No | `true` | Publishes Webflow items after create/update. |
| `X_ACCESS_TOKEN` | No | None | Publishes generated X posts. Missing token is non-fatal by default. |
| `REQUIRE_X_POSTING` | No | `false` | Fails the run if X posting fails. |
| `FORCE_WEBFLOW_SYNC` | No | `false` | Writes to Webflow even when state says the item is current. |
| `FORCE_ENRICH` | No | `false` | Regenerates OpenAI enrichment for an existing post. |
| `FORCE_TWEETIFY` | No | `false` | Regenerates the X draft for an existing post. |
| `FORCE_X_POST` | No | `false` | Ignores the posted-tweets ledger and posts to X again. |
| `LINKEDIN_PROMPT_PROFILE` | No | First profile | Selects a `linkedin_post_enrichment` prompt profile by `id`. |
| `TWEET_PROMPT_ID` | No | First profile | Selects a `tweet_generation` prompt profile by `id`. |

## Alt Text

Every image with a URL should leave enrichment with a non-empty `alt` value. The pipeline tries these paths in order:

- OpenAI vision using the image URL and post context.
- Explicit source text such as `In the picture:`.
- OpenAI text-only fallback using the post context.
- Local generic post-context fallback.

If an existing enriched post is missing image alt text, the next pipeline run re-enters enrichment instead of skipping it.

## Running Locally

Run the full pipeline:

```bash
python -m pipeline.main
```

If no recent LinkedIn post is found, the command exits with code `2`. The GitHub workflow treats that as a clean no-op.

Run tests:

```bash
python -m unittest discover -s tests
```

## Image Matching

Images are matched to LinkedIn posts by publish date. For a post published on `2026-05-31`, files such as these are attached automatically:

```text
images/2026-05-31.jpeg
images/2026-05-31_1.jpg
images/2026-05-31_2.png
```

Supported image extensions are `.jpg`, `.jpeg`, `.png`, and `.webp`. Public image URLs are built from the `main` branch on GitHub.

## Generated Data

The pipeline writes these JSON artifacts:

| File | Purpose |
| --- | --- |
| `data/last_linkedin_post.json` | Latest raw post fetched from LinkedIn. |
| `data/last_linkedin_post.enriched.json` | Post with generated headline, description, and image alt text. |
| `data/webflow_items.json` | Webflow item IDs, payload signatures, publish state, and payload version. |
| `data/tweet.json` | Generated X post draft and selected images. |
| `data/posted_tweets.json` | Ledger used to avoid duplicate X posts. |
| `data/pipeline_state.json` | Last run timestamp, hashes, source URL, and step statuses. |

## Webflow Behavior

The Webflow sync checks existing state before writing. If the LinkedIn URL, payload signature, payload version, and publish state are already current, the Webflow API write is skipped.

When a write is needed, the pipeline:

- reads the collection schema,
- maps known fields such as title, content, description, source URL, date, image, gallery, and alt text,
- sets the Author field to `Giacomo Iotti`,
- leaves the slug unset so Webflow can populate it automatically,
- creates a new item or updates the known item,
- publishes it when `WEBFLOW_PUBLISH=true`.

## X Posting Behavior

X posting is optional. The pipeline can generate a tweet from the enriched LinkedIn post, select up to four images, upload media, add alt text metadata, publish the post, and record the result in `data/posted_tweets.json`.

If `X_ACCESS_TOKEN` is missing or posting fails, the pipeline logs the failure and continues unless `REQUIRE_X_POSTING=true`.

## GitHub Actions

`.github/workflows/webflow_cms_pipeline.yml` runs the pipeline daily at 20:00 Europe/Zurich. Because GitHub schedules use UTC, the workflow has two cron slots and then checks the actual Zurich hour before running.

The workflow can also be started manually with `workflow_dispatch`. After a successful run, it commits changes under `data/` and `images/` back to `main` when there are changes.

## Tests

The current tests focus on Webflow payload mapping and sync-state freshness:

```bash
python -m unittest tests/test_webflow_payload.py
```
