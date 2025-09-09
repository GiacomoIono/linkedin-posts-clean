# tweetify_post.py
# Creates tweet.json from your latest LinkedIn post.
# - Prefers last_linkedin_post.enriched.json (to keep image ALTs), falls back to last_linkedin_post.json
# - Uses prompts.json -> tweet_generation (array) with optional TWEET_PROMPT_PROFILE
# - Uses OPENAI_MODEL from .env (default gpt-4o-mini)
# - Considers post IMAGES (vision) when generating the tweet
# - Output tweet.json keeps the SAME fields as enriched, but:
#     * removes "seo"
#     * replaces "content" with the tweet text (≤ TWEET_MAX_CHARS)

import json, os, sys, re, html
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parent
INPUT_ENRICHED = REPO_ROOT / "last_linkedin_post.enriched.json"
INPUT_FALLBACK = REPO_ROOT / "last_linkedin_post.json"
OUTPUT_TWEET = REPO_ROOT / "tweet.json"
PROMPTS_PATH = REPO_ROOT / "prompts.json"

# Defaults; can override in .env
DEFAULT_TWEET_MAX = 280
DEFAULT_MAX_IMAGES = 4  # max images to attach to the model for tweet context

# ---------- Utilities ----------

def strip_html_to_text(s: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", s or "")
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()

def soft_trim(s: str, limit: int) -> str:
    s = " ".join(s.split())  # collapse whitespace + remove newlines
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:!?")


def sanitize_tweet(s: str) -> str:
    if not s:
        return ""
    # Remove formatting & disallowed items per our rules
    s = s.replace("**", "").replace("__", "")
    s = s.replace("—", "-")
    # Remove quotes
    s = s.replace('"', "").replace("'", "").replace("“", "").replace("”", "").replace("’", "")
    # Remove hashtags and @mentions
    s = re.sub(r"(?:^|\s)#\w+", "", s)
    s = re.sub(r"(?:^|\s)@\w+", "", s)
    # Collapse whitespace
    s = " ".join(s.split())
    return s.strip()

def _safe_output_text(resp) -> str:
    txt = getattr(resp, "output_text", None)
    if txt:
        return txt.strip()
    try:
        return resp.output[0].content[0].text.strip()
    except Exception:
        return str(resp)

def fill_placeholders(template: str, mapping: dict) -> str:
    out = template
    for k, v in mapping.items():
        out = out.replace("{" + k + "}", v)
    return out

# ---------- Prompts loader (tweet_generation array) ----------

def load_tweet_prompts() -> dict:
    if not PROMPTS_PATH.exists():
        print(f"❌ Missing {PROMPTS_PATH}", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to parse {PROMPTS_PATH.name}: {e}", file=sys.stderr)
        sys.exit(1)

    sets = data.get("tweet_generation")
    if not isinstance(sets, list) or not sets:
        print(f"❌ {PROMPTS_PATH.name} must contain a non-empty array 'tweet_generation'.", file=sys.stderr)
        sys.exit(1)

    desired_id = os.getenv("TWEET_PROMPT_PROFILE", "").strip()
    chosen = None
    if desired_id:
        for s in sets:
            if isinstance(s, dict) and s.get("id") == desired_id:
                chosen = s
                break
        if not chosen:
            print(f"⚠️ TWEET_PROMPT_PROFILE '{desired_id}' not found; using the first set.")

    if not chosen:
        chosen = sets[0]

    required = ["tweet_system", "tweet_user"]
    for key in required:
        if key not in chosen or not isinstance(chosen[key], str):
            print(f"❌ Tweet prompt set is missing key: {key}", file=sys.stderr)
            sys.exit(1)

    return chosen

# ---------- OpenAI call (with images) ----------

def generate_tweet(client: OpenAI, model: str, source_text: str, image_urls: list[str], prompts: dict, tweet_max: int) -> str:
    system_msg = prompts["tweet_system"]

    # Base user instruction from prompts.json
    user_msg = fill_placeholders(
        prompts["tweet_user"],
        {
            "CONTENT": source_text[:4000],
            "TWEET_MAX": str(tweet_max),
        },
    )

    # Add a tiny helper hint when images exist
    img_hint = "Use the attached images for additional context. If any text is visible in the images, reflect it succinctly."
    user_blocks = [{"type": "input_text", "text": user_msg}]
    if image_urls:
        user_blocks.append({"type": "input_text", "text": img_hint})
        for url in image_urls:
            user_blocks.append({"type": "input_image", "image_url": url})

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_msg}]},
            {"role": "user",   "content": user_blocks},
        ],
        temperature=0.7,
        max_output_tokens=200,
    )

    raw = _safe_output_text(response).strip()

    # Strip code fences, if any
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()

    tweet = sanitize_tweet(raw)
    return soft_trim(tweet, tweet_max)

# ---------- Main ----------

def main():
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not found in .env", file=sys.stderr)
        sys.exit(1)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    tweet_max = int(os.getenv("TWEET_MAX_CHARS", DEFAULT_TWEET_MAX))
    max_images = int(os.getenv("TWEET_MAX_IMAGES", DEFAULT_MAX_IMAGES))

    # Prefer enriched JSON (preserves ALT); otherwise fallback to raw
    input_path = INPUT_ENRICHED if INPUT_ENRICHED.exists() else INPUT_FALLBACK
    if not input_path.exists():
        print(f"❌ Missing {INPUT_ENRICHED.name} and {INPUT_FALLBACK.name}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ JSON parse error: {e}", file=sys.stderr)
        sys.exit(1)

    # Source content (strip HTML)
    content_html = data.get("content", "")
    source_text = strip_html_to_text(content_html)

    # Collect image URLs (limit to max_images)
    raw_images = data.get("images", []) or []
    image_urls = []
    for img in raw_images:
        url = (img or {}).get("url")
        if url:
            image_urls.append(url)
        if len(image_urls) >= max_images:
            break

    prompts = load_tweet_prompts()
    client = OpenAI()

    # Generate tweet with text + images context
    try:
        tweet_text = generate_tweet(client, model, source_text, image_urls, prompts, tweet_max)
        if not tweet_text:
            tweet_text = soft_trim(source_text, tweet_max)  # emergency fallback
        print(f"🐦 Tweet: {tweet_text}")
    except Exception as e:
        print(f"❌ Failed to generate tweet: {e}", file=sys.stderr)
        sys.exit(1)

    # Build output with the SAME fields as enriched, but:
    # - remove "seo"
    # - replace "content" with the tweet text
    out = dict(data)  # shallow copy is fine
    out.pop("seo", None)
    out["content"] = tweet_text

    try:
        OUTPUT_TWEET.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n✅ Wrote {OUTPUT_TWEET.name}")
        print("ℹ️ Fields match enriched (minus 'seo'); content is now the tweet text.")
    except Exception as e:
        print(f"❌ Failed to write tweet JSON: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
