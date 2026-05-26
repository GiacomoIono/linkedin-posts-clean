# tweetify_post.py
# Build tweet.json from last_linkedin_post.json using prompts.json -> tweet_generation.
# - Input: last_linkedin_post.json  (HTML in "content", images under images[].url)
# - Prompt: prompts.json -> tweet_generation (array). Uses id from TWEET_PROMPT_ID or defaults to first.
# - OpenAI: Chat Completions API with vision and JSON mode.
# - Output: tweet.json with fields: content, url, published_at, images:[{url, alt}]

import os, sys, json, re, html
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parent
INPUT_PATH = REPO_ROOT.parent / "last_linkedin_post.json"
PROMPTS_PATH = REPO_ROOT.parent / "prompts.json"
OUTPUT_PATH = REPO_ROOT / "tweet.json"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "none"
DEFAULT_OPENAI_MAX_COMPLETION_TOKENS = 2000

DEFAULT_MAX_CHARS = 280
DEFAULT_MAX_IMAGES = 4

# ---------- small helpers ----------

def strip_html_to_text(s: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", s or "")
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()

def soft_trim(s: str, limit: int) -> str:
    s = " ".join((s or "").split())
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:!?")

def load_json(p: Path) -> Any:
    if not p.exists():
        print(f"❌ Missing {p.name}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to parse {p.name}: {e}", file=sys.stderr)
        sys.exit(1)

def pick_prompt(prompts_doc: Dict[str, Any]) -> Dict[str, str]:
    arr = prompts_doc.get("tweet_generation")
    if not isinstance(arr, list) or not arr:
        print("❌ prompts.json must contain a non-empty 'tweet_generation' array.", file=sys.stderr)
        sys.exit(1)

    desired = os.getenv("TWEET_PROMPT_ID", "").strip()
    chosen = None
    if desired:
        for item in arr:
            if isinstance(item, dict) and item.get("id") == desired:
                chosen = item
                break
        if not chosen:
            print(f"⚠️ TWEET_PROMPT_ID '{desired}' not found. Falling back to the first entry.")
    if not chosen:
        chosen = arr[0]

    if not isinstance(chosen.get("tweet_system"), str) or not isinstance(chosen.get("tweet_user"), str):
        print("❌ Selected tweet prompt must contain 'tweet_system' and 'tweet_user' strings.", file=sys.stderr)
        sys.exit(1)
    return {"tweet_system": chosen["tweet_system"], "tweet_user": chosen["tweet_user"]}

def apply_curly_placeholders(template: str, mapping: Dict[str, str]) -> str:
    out = template
    for k, v in mapping.items():
        out = out.replace(f"{{{{{k}}}}}", v)
    return out

def as_newline_list(items: List[str]) -> str:
    return "\n".join(items)

def ensure_list_str(x: Any) -> List[str]:
    out = []
    if isinstance(x, list):
        for it in x:
            url = (it or {}).get("url") if isinstance(it, dict) else str(it)
            if url:
                out.append(str(url))
    return out

def supports_reasoning_effort(model: str) -> bool:
    return (model or "").startswith("gpt-5")

def chat_completion_kwargs(model: str, messages: list, max_completion_tokens: int, response_format: dict | None = None) -> dict:
    kwargs = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_completion_tokens,
    }

    reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip()
    if reasoning_effort and supports_reasoning_effort(model):
        kwargs["reasoning_effort"] = reasoning_effort

    if response_format:
        kwargs["response_format"] = response_format

    return kwargs

def response_text_or_raise(response, label: str) -> str:
    choice = response.choices[0] if response.choices else None
    text = ((choice.message.content if choice and choice.message else None) or "").strip()
    if text:
        return text

    finish_reason = getattr(choice, "finish_reason", "unknown") if choice else "missing_choice"
    usage = getattr(response, "usage", None)
    raise ValueError(
        f"OpenAI returned empty {label} output (finish_reason={finish_reason}, usage={usage}). "
        "Increase TWEET_OPENAI_MAX_COMPLETION_TOKENS or lower OPENAI_REASONING_EFFORT."
    )

def parse_model_json_response(response) -> Dict[str, Any]:
    raw = response_text_or_raise(response, "tweet JSON")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Could not parse JSON from model output: {e}. Raw output was: {raw[:1000]}") from e

    if not isinstance(parsed, dict):
        raise ValueError(f"Model output must be a JSON object. Got: {type(parsed).__name__}")

    return parsed

# ---------- main ----------

def main():
    load_dotenv()

    # Require API key + model from .env (no hardcoded model)
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not found in .env", file=sys.stderr)
        sys.exit(1)

    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    tweet_max = int(os.getenv("TWEET_MAX_CHARS", DEFAULT_MAX_CHARS))
    max_images = int(os.getenv("TWEET_MAX_IMAGES", DEFAULT_MAX_IMAGES))
    openai_max_completion_tokens = int(os.getenv("TWEET_OPENAI_MAX_COMPLETION_TOKENS", DEFAULT_OPENAI_MAX_COMPLETION_TOKENS))

    # Load files
    src = load_json(INPUT_PATH)
    prompts_doc = load_json(PROMPTS_PATH)
    prompt = pick_prompt(prompts_doc)

    # ---- Early exit: nothing new to tweetify ----
    # Skip only when an existing tweet for this URL has non-empty content.
    # If a prior run wrote a blank tweet.json, rebuild it instead of preserving the bad output.
    try:
        existing_out = json.loads(OUTPUT_PATH.read_text(encoding="utf-8")) if OUTPUT_PATH.exists() else None
    except Exception:
        existing_out = None  # if tweet.json is corrupted, proceed to rebuild

    current_url = (src or {}).get("url", "")
    previous_url = (existing_out or {}).get("url", "")
    previous_content = ((existing_out or {}).get("content", "") or "").strip()

    if not os.getenv("FORCE_TWEETIFY") and current_url and previous_url and current_url == previous_url and previous_content:
        print("⏭️  No new LinkedIn post detected (same URL as tweet.json). Skipping tweetify.")
        sys.exit(0)
    if current_url and previous_url and current_url == previous_url and not previous_content:
        print("♻️  Existing tweet.json for this URL is empty. Rebuilding it.")

    # Prepare input text + images
    src_text = strip_html_to_text(src.get("content", ""))
    raw_images = ensure_list_str(src.get("images", []))
    image_urls = raw_images[:max_images]

    # Compose user message from prompts.json ({{linkedin_post_text}}, {{image_urls}})
    user_msg = apply_curly_placeholders(
        prompt["tweet_user"],
        {
            "linkedin_post_text": src_text[:8000],  # plenty for a single post
            "image_urls": as_newline_list(image_urls) if image_urls else "None",
        },
    )
    # Small reminder about the format (helpful in both paths)
    user_msg += (
        "\n\nResponse format:\n"
        "- Return ONLY JSON with keys: tweet (non-empty string ≤ 280 chars) and images (array of objects with url and alt).\n"
        "- Include ONLY the images you selected (max 4)."
    )

    # Build OpenAI call
    client = OpenAI()

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": prompt["tweet_system"]},
    ]

    user_content: List[Dict[str, Any]] = [{"type": "text", "text": user_msg}]
    if image_urls:
        user_content.append({"type": "text", "text": "Use the attached images for added context. Generate concise, human-first alt text."})
        for url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": url}})

    messages.append({"role": "user", "content": user_content})

    # Call the modern OpenAI API
    try:
        resp = client.chat.completions.create(**chat_completion_kwargs(
            model=model,
            messages=messages,
            max_completion_tokens=openai_max_completion_tokens,
            # Use JSON mode so the saved tweet.json keeps a stable shape.
            response_format={"type": "json_object"},
        ))
        parsed = parse_model_json_response(resp)
    except Exception as e:
        print(f"❌ OpenAI API error: {e}", file=sys.stderr)
        sys.exit(1)

    # Guardrails: trim tweet to max just in case; keep hashtags/emojis
    tweet_text = soft_trim(parsed.get("tweet", ""), tweet_max)
    if not tweet_text:
        print(f"❌ OpenAI returned JSON without a non-empty tweet: {parsed}", file=sys.stderr)
        sys.exit(1)

    sel_images = parsed.get("images", []) or []
    if not isinstance(sel_images, list):
        sel_images = []

    # Final output shape per spec
    out: Dict[str, Any] = {
        "content": tweet_text,
        "url": src.get("url", ""),
        "published_at": src.get("published_at", ""),
        "images": []
    }

    # Only include url + alt for selected source images
    allowed_urls = set(raw_images)
    for it in sel_images:
        url = (it or {}).get("url") if isinstance(it, dict) else None
        alt = ((it or {}).get("alt", "") if isinstance(it, dict) else "").strip()
        if url and url in allowed_urls:
            out["images"].append({"url": url, "alt": alt})

    # Write
    try:
        OUTPUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"🐦 Tweet (preview): {tweet_text}")
        print(f"🖼️  Images selected: {len(out['images'])}")
        print(f"✅ Wrote {OUTPUT_PATH.name}")
    except Exception as e:
        print(f"❌ Failed to write {OUTPUT_PATH.name}: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
