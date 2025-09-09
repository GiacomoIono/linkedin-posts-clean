# tweetify_post.py
# Build tweet.json from last_linkedin_post.json using prompts.json → tweet_generation.
# - Input: last_linkedin_post.json  (HTML in "content", images under images[].url)
# - Prompt: prompts.json → tweet_generation (array). Uses id from TWEET_PROMPT_ID or defaults to first.
# - OpenAI: Responses API with vision. Tries JSON schema; falls back to prompt-only JSON if SDK lacks response_format.
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

def parse_model_json_text(resp) -> Dict[str, Any]:
    """
    Parse JSON whether structured outputs are supported or not.
    Works with old/new openai SDKs that expose different response shapes.
    """
    raw = getattr(resp, "output_text", None)
    if not raw:
        try:
            raw = resp.output[0].content[0].text
        except Exception:
            raw = ""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        # Strip fenced code blocks like ```json ... ```
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
    try:
        return json.loads(raw)
    except Exception as e:
        print("❌ Could not parse JSON from model output.", file=sys.stderr)
        print(f"Raw output was:\n{raw[:1000]}", file=sys.stderr)
        sys.exit(1)

# ---------- main ----------

def main():
    load_dotenv()

    # Require API key + model from .env (no hardcoded model)
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not found in .env", file=sys.stderr)
        sys.exit(1)

    model = os.getenv("OPENAI_MODEL")
    if not model:
        print("❌ OPENAI_MODEL not found in .env", file=sys.stderr)
        sys.exit(1)

    tweet_max = int(os.getenv("TWEET_MAX_CHARS", DEFAULT_MAX_CHARS))
    max_images = int(os.getenv("TWEET_MAX_IMAGES", DEFAULT_MAX_IMAGES))

    # Load files
    src = load_json(INPUT_PATH)
    prompts_doc = load_json(PROMPTS_PATH)
    prompt = pick_prompt(prompts_doc)

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
        "- Return ONLY JSON with keys: tweet (string ≤ 280 chars) and images (array of objects with url and alt).\n"
        "- Include ONLY the images you selected (max 4)."
    )

    # Build OpenAI call
    client = OpenAI()

    # Vision blocks: model sees the actual images
    user_blocks: List[Dict[str, Any]] = [{"type": "input_text", "text": user_msg}]
    if image_urls:
        user_blocks.append({"type": "input_text", "text": "Use the attached images for added context. Generate concise, human-first alt text."})
        for url in image_urls:
            user_blocks.append({"type": "input_image", "image_url": url})

    # Preferred: JSON schema (if your SDK supports `response_format`)
    json_schema = {
        "name": "tweet_output",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tweet": {"type": "string", "description": "One tweet optimized for X, ≤ 280 characters."},
                "images": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["url", "alt"],
                        "properties": {
                            "url": {"type": "string"},
                            "alt": {"type": "string", "description": "1 concise sentence, human-first."}
                        }
                    }
                }
            },
            "required": ["tweet", "images"]
        }
    }

# Reformat content for the modern OpenAI v1.x API
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": prompt["tweet_system"]},
    ]
    
    # The new API expects a different format for text and images
    user_content: List[Dict[str, Any]] = [{"type": "text", "text": user_msg}]
    if image_urls:
        user_content.append({"type": "text", "text": "Use the attached images for added context. Generate concise, human-first alt text."})
        for url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": url}})
            
    messages.append({"role": "user", "content": user_content})

    # Call the modern OpenAI API
    try:
        # Use the standard client.chat.completions.create method
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.6,
            max_tokens=400,
            # Use "json_object" mode, which is standard for GPT-4 vision models
            response_format={"type": "json_object"},
        )
        # The response structure is also different in the new SDK
        parsed = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        print(f"❌ OpenAI API error: {e}", file=sys.stderr)
        sys.exit(1)

    # Guardrails: trim tweet to max just in case; keep hashtags/emojis
    tweet_text = soft_trim(parsed.get("tweet", ""), tweet_max)
    sel_images = parsed.get("images", []) or []

    # Final output shape per spec
    out: Dict[str, Any] = {
        "content": tweet_text,
        "url": src.get("url", ""),
        "published_at": src.get("published_at", ""),
        "images": []
    }

    # Only include url + alt for selected images
    for it in sel_images:
        url = (it or {}).get("url")
        alt = (it or {}).get("alt", "").strip()
        if url:
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
