# LinkedIn Posts Clean

This project takes your latest LinkedIn post and turns it into a Webflow blog post.

In plain English, the pipeline does this:

1. Looks for your newest LinkedIn post from the last 48 hours.
2. Turns the post text into simple blog-post HTML.
3. Finds matching images in the `images/` folder.
4. Uses OpenAI to create the headline, summary, and missing image ALT text.
5. Sends the post to the Webflow Blog Posts collection.
6. Skips X/Twitter unless you explicitly turn that part on.
7. Saves a record of what happened in the `data/` folder.

## Quick Start

Create the Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a local `.env` file:

```bash
LINKEDIN_ACCESS_TOKEN=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-nano

WEBFLOW_API_TOKEN=
WEBFLOW_COLLECTION_ID=63250855178122098387d7ef
WEBFLOW_PUBLISH=true

RUN_X_PIPELINE=false
X_ACCESS_TOKEN=
REQUIRE_X_POSTING=false
```

Then run:

```bash
python -m pipeline.main
```

To run the tests:

```bash
python -m unittest discover -s tests
```

## The Important Settings

Most days, these are the only settings you need to care about:

| Setting | What it does |
| --- | --- |
| `LINKEDIN_ACCESS_TOKEN` | Lets the script read your recent LinkedIn activity. |
| `OPENAI_API_KEY` | Lets the script write the headline, summary, and image ALT text. |
| `WEBFLOW_API_TOKEN` | Lets the script create, update, and publish Webflow posts. |
| `WEBFLOW_PUBLISH` | When `true`, Webflow items are published after they are written. |
| `RUN_X_PIPELINE` | When `false`, the X pipeline is skipped completely. This is the default. |
| `X_ACCESS_TOKEN` | Needed only if `RUN_X_PIPELINE=true`. |

`WEBFLOW_READ_AND_WRITE_BLOG_POSTS` can also be used instead of `WEBFLOW_API_TOKEN`.

## LinkedIn Window

The LinkedIn scraper looks back exactly 48 hours from the time the script runs.

That means it is a rolling time window, not "today and yesterday" as calendar days. For example, if the script runs at 04:00 on June 3, it searches back to 04:00 on June 1.

If no LinkedIn post is found in that window, the script exits cleanly with code `2`. The GitHub Action treats that as "nothing to do", not as a failure.

## Images

Put images in the `images/` folder and name them by the LinkedIn post date.

For one image:

```text
images/2026-06-01.jpg
```

For multiple images:

```text
images/2026-06-01_1.jpg
images/2026-06-01_2.jpg
images/2026-06-01_3.jpg
```

Supported formats are `.jpg`, `.jpeg`, `.png`, and `.webp`.

When there are multiple images, the number decides the order. `_1` is first, `_2` is second, and so on. When there is only one image, the filename can just be the date.

The pipeline sends:

- all images to Webflow's `post-images` field, in the right order;
- the first image to `main-image`;
- the first image to `thumbnail-image`;
- an `alt` value for every image.

Important: image URLs are built from the GitHub `main` branch. So if you run the pipeline locally with brand-new local images, Webflow can only fetch them after those images exist on GitHub.

## ALT Text

Every image should leave the enrichment step with ALT text.

The pipeline tries, in order:

1. OpenAI vision, using the actual image URL and the LinkedIn post context.
2. Any explicit image description already written in the post.
3. A text-only OpenAI fallback.
4. A simple local fallback.

The ALT prompt includes both:

- the image source URL;
- the post context.

This helps OpenAI describe the specific image instead of writing generic ALT text about the whole post.

## Webflow

The Webflow script is now tuned to the exact Blog Posts collection schema.

It fills these fields:

| Webflow field | Value sent by the pipeline |
| --- | --- |
| `name` | Generated headline. |
| `post-summary` | Generated description. |
| `post-body` | LinkedIn post content as rich text HTML. |
| `post-images` | One or more ordered image objects, each with `url` and `alt`. |
| `published-date` | LinkedIn publish date. |
| `linkedin-post-link` | Original LinkedIn post URL. |
| `author` | The configured Webflow author item. |
| `main-image` | First image. |
| `thumbnail-image` | First image. |
| `category` | Optional, if present in the post data. |
| `tags` | Optional, if present in the post data. |
| `month` | Optional, if present in the post data. |
| `featured` | Optional, if present in the post data. |

The pipeline does not send `slug` at all. Webflow is left to handle that field.

To avoid duplicates, Webflow matching is based on the LinkedIn URL and the saved Webflow item state in `data/webflow_items.json`.

If the saved Webflow item ID no longer exists, the script looks for the item by LinkedIn URL. It also handles the awkward case where Webflow still has a live-only version of an item after a manual deletion.

## X Posting

The X pipeline is disabled by default.

```bash
RUN_X_PIPELINE=false
```

When it is `false`, the script does not generate a tweet, does not upload images to X, and does not call the X API.

If you set:

```bash
RUN_X_PIPELINE=true
```

then the script tries to:

1. Generate a tweet from the enriched LinkedIn post.
2. Select up to four images.
3. Upload those images to X.
4. Add ALT text metadata.
5. Publish the post.
6. Save the result in `data/posted_tweets.json`.

If X fails, the Webflow pipeline still succeeds unless `REQUIRE_X_POSTING=true`.

## GitHub Action Schedule

The workflow runs at 04:00 Europe/Zurich time.

The file is:

```text
.github/workflows/webflow_cms_pipeline.yml
```

GitHub schedules use UTC, so the workflow has two possible UTC slots and then checks the real Zurich time before doing any work. This keeps the schedule correct across daylight saving time.

You can also start the workflow manually from GitHub Actions.

After a successful run, the workflow commits updates under:

```text
data/
images/
```

## Project Files

| Path | Purpose |
| --- | --- |
| `pipeline/main.py` | The main pipeline flow. |
| `pipeline/linkedin.py` | Fetches the latest LinkedIn post from the last 48 hours. |
| `pipeline/enrichment.py` | Creates headline, summary, and ALT text. |
| `pipeline/webflow.py` | Builds the exact Webflow payload and syncs the CMS item. |
| `pipeline/x_posting.py` | Optional X/Twitter generation and posting. |
| `pipeline/config.py` | Environment variables and defaults. |
| `config/prompts.json` | OpenAI prompts. |
| `images/` | Images matched to posts by date. |
| `data/` | Saved pipeline state and latest generated JSON files. |
| `tests/` | Tests for the pipeline behavior. |
| `webflow_schema.json` | Reference copy of the Webflow collection schema. |
| `webflow_schema_item_example.json` | Reference copy of a real Webflow item. |

## Saved Data

The script writes these files:

| File | What it contains |
| --- | --- |
| `data/last_linkedin_post.json` | The latest raw LinkedIn post found. |
| `data/last_linkedin_post.enriched.json` | The post after headline, summary, and ALT text are added. |
| `data/webflow_items.json` | Webflow item IDs and sync state. |
| `data/tweet.json` | The generated X draft, only when X is enabled. |
| `data/posted_tweets.json` | A ledger used to avoid posting the same thing to X twice. |
| `data/pipeline_state.json` | The latest run status. |

## Useful Force Flags

Use these only when you want to override the normal "skip if already done" behavior.

| Flag | What it does |
| --- | --- |
| `FORCE_WEBFLOW_SYNC=true` | Writes to Webflow even if the saved state says it is already current. |
| `FORCE_ENRICH=true` | Regenerates headline, summary, and ALT text. |
| `FORCE_TWEETIFY=true` | Regenerates the X draft. |
| `FORCE_X_POST=true` | Ignores the X posting ledger and posts again. |

## Prompt Limits

The headline and description limits are stored in:

```text
pipeline/enrichment.py
```

Current values:

```text
HEADLINE_MAX = 70
DESCRIPTION_MAX = 160
ALT_MAX = 180
```
