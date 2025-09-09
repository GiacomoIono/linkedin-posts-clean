# enrich_post.py
# Writes to last_linkedin_post.enriched.json (original JSON untouched)
# - Loads prompts from prompts.json -> linkedin_post_enrichment (array)
# - Uses OPENAI_MODEL (default gpt-4o-mini) and LINKEDIN_PROMPT_PROFILE (optional) from .env
# - Produces seo.headline + seo.description (NO top-level "title")
# - Generates ALT text for images with missing/empty "alt"

import json, os, sys, re, html
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parent
INPUT_PATH = REPO_ROOT / "last_linkedin_post.json"
OUTPUT_PATH = REPO_ROOT / "last_linkedin_post.enriched.json"
PROMPTS_PATH = REPO_ROOT / "prompts.json"

HEADLINE_MAX = 70
DESC_MAX = 160

# ---------- Utilities ----------

def strip_html_to_text(s: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", s or "")
    unescaped = html.unescape(no_tags)
    return re.sub(r"\s+", " ", unescaped).strip()

def sanitize(s: str) -> str:
    if not s:
        return ""
    # remove bold markers, em dashes; trim quotes/whitespace
    s = s.replace("**", "").replace("__", "").replace("—", "-").strip()
    return s.strip('"').strip()

def soft_trim(s: str, limit: int) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:!?")


def _safe_output_text(resp) -> str:
    # Works across minor SDK variations
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

# ---------- Prompts loader (array-based) ----------

def load_prompts() -> dict:
    if not PROMPTS_PATH.exists():
        print(f"❌ Missing {PROMPTS_PATH}", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to parse {PROMPTS_PATH.name}: {e}", file=sys.stderr)
        sys.exit(1)

    sets = data.get("linkedin_post_enrichment")
    if not isinstance(sets, list) or not sets:
        print(f"❌ {PROMPTS_PATH.name} must contain a non-empty array 'linkedin_post_enrichment'.", file=sys.stderr)
        sys.exit(1)

    desired_id = os.getenv("LINKEDIN_PROMPT_PROFILE", "").strip()
    chosen = None
    if desired_id:
        for s in sets:
            if isinstance(s, dict) and s.get("id") == desired_id:
                chosen = s
                break
        if not chosen:
            print(f"⚠️ LINKEDIN_PROMPT_PROFILE '{desired_id}' not found; using the first set.")

    if not chosen:
        chosen = sets[0]

    required = ["seo_system", "seo_user", "alt_system", "alt_user"]
    for key in required:
        if key not in chosen or not isinstance(chosen[key], str):
            print(f"❌ Prompt set is missing key: {key}", file=sys.stderr)
            sys.exit(1)

    return chosen

# ---------- OpenAI calls ----------

def generate_seo_fields(client: OpenAI, model: str, plain_text: str, prompts: dict) -> dict:
    system_msg = prompts["seo_system"]
    # Accept both {HEADLINE_MAX} and {TITLE_MAX} placeholders for flexibility
    user_msg = fill_placeholders(
        prompts["seo_user"],
        {
            "CONTENT": plain_text[:4000],
            "HEADLINE_MAX": str(HEADLINE_MAX),
            "TITLE_MAX": str(HEADLINE_MAX),  # in case your prompt still says {TITLE_MAX}
            "DESC_MAX": str(DESC_MAX),
        },
    )

    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_msg}]},
            {"role": "user",   "content": [{"type": "input_text", "text": user_msg}]},
        ],
        temperature=0.7,
        max_output_tokens=200,
    )

    raw = _safe_output_text(response).strip()

    # Strip code fences if present
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()

    # Strict parse or fallback extraction
    data = None
    try:
        data = json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(raw[start:end+1])
            except Exception:
                pass
    if not isinstance(data, dict):
        data = {"headline": "", "description": ""}

    headline = sanitize(data.get("headline", ""))
    description = sanitize(data.get("description", ""))

    headline = soft_trim(headline, HEADLINE_MAX)
    description = soft_trim(description, DESC_MAX)

    headline = " ".join(headline.split())
    description = " ".join(description.split())

    return {"headline": headline, "description": description}

def generate_alt_for_image(client: OpenAI, model: str, image_url: str, context_text: str, prompts: dict) -> str:
    sys_prompt = prompts["alt_system"]
    user_intro = fill_placeholders(
        prompts["alt_user"],
        {"CONTEXT": context_text[:500]},
    )
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": sys_prompt}]},
            {"role": "user", "content": [
                {"type": "input_text", "text": user_intro},
                {"type": "input_image", "image_url": image_url},
            ]},
        ],
        temperature=0.7,
        max_output_tokens=60,
    )
    return sanitize(_safe_output_text(resp))

# ---------- Main ----------

def main():
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ OPENAI_API_KEY not found in .env", file=sys.stderr)
        sys.exit(1)

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # choose gpt-4o for more polish if you want

    if not INPUT_PATH.exists():
        print(f"❌ Missing {INPUT_PATH}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ JSON parse error: {e}", file=sys.stderr)
        sys.exit(1)

    prompts = load_prompts()

    content_html = data.get("content", "")
    images = data.get("images", []) or []
    plain_text = strip_html_to_text(content_html)

    client = OpenAI()

    # 1) SEO (headline + description) under data["seo"]; ensure no top-level "title"
    try:
        seo = generate_seo_fields(client, model, plain_text, prompts)
        data["seo"] = seo
        if "title" in data:
            data.pop("title", None)
        print(f"🔎 SEO: {seo}")
    except Exception as e:
        print(f"❌ Failed to generate SEO fields: {e}", file=sys.stderr)
        sys.exit(1)

    # 2) ALT text for images (only if missing/blank)
    updated = 0
    for img in images:
        url = img.get("url")
        current_alt = (img.get("alt") or "").strip()
        if not url:
            continue
        if current_alt:
            print(f"• Skipping ALT (already present) for {url}")
            continue
        try:
            alt = generate_alt_for_image(client, model, url, plain_text, prompts)
            img["alt"] = alt
            updated += 1
            print(f"🖼️ ALT set for {url} -> {alt}")
        except Exception as e:
            print(f"❌ Failed ALT for {url}: {e}", file=sys.stderr)

    # 3) Write to a NEW file
    try:
        OUTPUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n✅ Wrote {OUTPUT_PATH.name} (seo.headline + seo.description + {updated} ALT)")
        print("ℹ️ Original last_linkedin_post.json unchanged.")
    except Exception as e:
        print(f"❌ Failed to write output JSON: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
