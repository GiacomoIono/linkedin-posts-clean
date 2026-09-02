from __future__ import annotations

from dataclasses import dataclass
import html
from html.parser import HTMLParser
import ipaddress
import json
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from openai import OpenAI

from .config import PipelineConfig
from .enrichment import fill_placeholders, load_prompts
from .utils import strip_html_to_text


LINK_MAX_ATTEMPTS = 2
LINK_DECISIONS = frozenset({"links", "no_material_claims", "no_suitable_source"})
LINK_SOURCE_TYPES = (
    "official_primary",
    "original_research",
    "peer_reviewed",
    "reputable_news",
    "specialist_authority",
)
LINK_OUTPUT_KEYS = frozenset({"decision", "links"})
LINK_ITEM_KEYS = frozenset(
    {"anchor_text", "claim_text", "source_url", "source_title", "source_type"}
)
VERIFICATION_OUTPUT_KEYS = frozenset({"verdicts"})
COVERAGE_VERIFICATION_OUTPUT_KEYS = frozenset({"complete"})
VERDICT_KEYS = frozenset(
    {"proposal_id", "source_url", "supports_claim", "authoritative"}
)
TRACKING_QUERY_KEYS = frozenset(
    {
        "dclid",
        "_hsenc",
        "_hsmi",
        "fbclid",
        "gad_campaignid",
        "gad_source",
        "gbraid",
        "gclid",
        "igshid",
        "mkt_tok",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "s_cid",
        "trk",
        "trkinfo",
        "ref",
        "ref_src",
        "source",
        "vero_conv",
        "vero_id",
        "wbraid",
    }
)
SEARCH_RESULT_HOSTS = frozenset(
    {
        "bing.com",
        "duckduckgo.com",
        "google.com",
        "search.brave.com",
        "search.yahoo.com",
        "yahoo.com",
    }
)
SEARCH_ENGINE_LABELS = frozenset(
    {
        "aol",
        "ask",
        "baidu",
        "bing",
        "brave",
        "duckduckgo",
        "ecosia",
        "google",
        "qwant",
        "sogou",
        "startpage",
        "yahoo",
        "yandex",
    }
)
SEARCH_ONLY_HOST_PREFIXES = (
    "ask.",
    "baidu.",
    "duckduckgo.",
    "ecosia.",
    "qwant.",
    "scholar.google.",
    "startpage.",
    "yandex.",
)
SEARCH_PATH_PREFIXES = ("/s", "/search", "/web")
SEARCH_PATH_SEGMENTS = frozenset({"search-results", "site-search"})
SEARCH_QUERY_KEYS = frozenset(
    {"keyword", "keywords", "p", "q", "query", "s", "term"}
)
GENERIC_EVIDENCE_PATHS = frozenset(
    {
        "about",
        "articles",
        "blog",
        "home",
        "insights",
        "news",
        "press",
        "press-releases",
        "publications",
        "reports",
        "research",
        "resources",
    }
)
LOCALE_HOMEPAGE_PATHS = frozenset(
    {
        "de",
        "en",
        "en-gb",
        "en-us",
        "es",
        "fr",
        "it",
        "ja",
        "ko",
        "pt",
        "pt-br",
        "zh",
        "zh-cn",
        "zh-tw",
    }
)
UNLINKABLE_TAGS = frozenset({"script", "style", "textarea", "title"})
GENERIC_ANCHOR_TEXT = frozenset({"click here", "here", "read more", "this"})
UNSAFE_URL_CHARACTER_RE = re.compile(r'''[\x00-\x20\x7f"'<>\\]''')

LINK_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": sorted(LINK_DECISIONS)},
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "anchor_text": {"type": "string"},
                    "claim_text": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_title": {"type": "string"},
                    "source_type": {
                        "type": "string",
                        "enum": list(LINK_SOURCE_TYPES),
                    },
                },
                "required": sorted(LINK_ITEM_KEYS),
                "additionalProperties": False,
            },
        },
    },
    "required": sorted(LINK_OUTPUT_KEYS),
    "additionalProperties": False,
}

LINK_VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "source_url": {"type": "string"},
                    "supports_claim": {"type": "boolean"},
                    "authoritative": {"type": "boolean"},
                },
                "required": sorted(VERDICT_KEYS),
                "additionalProperties": False,
            },
        }
    },
    "required": sorted(VERIFICATION_OUTPUT_KEYS),
    "additionalProperties": False,
}

COVERAGE_VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "complete": {"type": "boolean"},
    },
    "required": sorted(COVERAGE_VERIFICATION_OUTPUT_KEYS),
    "additionalProperties": False,
}


class LinkingError(RuntimeError):
    pass


@dataclass(frozen=True)
class LinkProposal:
    anchor_text: str
    claim_text: str
    source_url: str
    source_title: str
    source_type: str


@dataclass(frozen=True)
class AnchorApplication:
    proposal: LinkProposal
    start: int
    end: int

    @property
    def opening_tag(self) -> str:
        escaped_url = html.escape(self.proposal.source_url, quote=True)
        return f'<a href="{escaped_url}">'


def object_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def parse_response_json(response: Any, label: str) -> dict[str, Any]:
    status = object_value(response, "status")
    if status not in (None, "completed"):
        raise LinkingError(f"OpenAI returned an incomplete {label} response (status={status}).")

    raw = str(object_value(response, "output_text", "") or "").strip()
    if not raw:
        raise LinkingError(f"OpenAI returned empty {label} output.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LinkingError(f"OpenAI returned invalid {label} JSON: {exc}.") from exc
    if not isinstance(payload, dict):
        raise LinkingError(f"OpenAI returned {label} JSON that is not an object.")
    return payload


def web_search_trace(response: Any) -> tuple[int, set[str], set[str]]:
    search_count = 0
    searched_urls: set[str] = set()
    opened_urls: set[str] = set()
    for item in object_value(response, "output", []) or []:
        if object_value(item, "type") != "web_search_call":
            continue
        if object_value(item, "status") not in (None, "completed"):
            continue
        action = object_value(item, "action", {})
        action_type = object_value(action, "type")
        if action_type == "search":
            search_count += 1
            for source in object_value(action, "sources", []) or []:
                identity = source_url_identity(str(object_value(source, "url", "") or ""))
                if identity:
                    searched_urls.add(identity)
        elif action_type in {"open_page", "find_in_page"}:
            source_url = str(object_value(action, "url", "") or "")
            identity = source_url_identity(source_url)
            if identity:
                opened_urls.add(identity)
    return search_count, searched_urls, opened_urls


def is_tracking_query_key(key: str) -> bool:
    folded = key.casefold()
    return folded.startswith("utm_") or folded in TRACKING_QUERY_KEYS


def is_search_result_page(host: str, path: str, query: str = "") -> bool:
    bare_host = host.removeprefix("www.")
    if (
        bare_host in SEARCH_RESULT_HOSTS
        or bare_host.startswith("google.")
        or bare_host.startswith(SEARCH_ONLY_HOST_PREFIXES)
    ):
        return True
    labels = bare_host.split(".")
    if labels[0] == "search":
        return True
    if bare_host.startswith("news.google."):
        return True
    folded_path = unquote(path or "/").casefold().rstrip("/") or "/"
    path_segments = [segment for segment in folded_path.split("/") if segment]
    if any(segment in SEARCH_PATH_SEGMENTS for segment in path_segments):
        return True
    if (
        path_segments
        and path_segments[0] == "search"
        and bare_host != "developers.google.com"
    ):
        return True
    query_keys = {
        key.casefold() for key, _ in parse_qsl(query, keep_blank_values=True)
    }
    if "search" in path_segments and (
        path_segments[-1] == "search" or query_keys & SEARCH_QUERY_KEYS
    ):
        return True
    return labels[0] in SEARCH_ENGINE_LABELS and any(
        folded_path == prefix or folded_path.startswith(prefix + "/")
        for prefix in SEARCH_PATH_PREFIXES
    )


def source_url_identity(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
        host = (parsed.hostname or "").casefold()
        if parsed.scheme.casefold() != "https" or not host:
            return ""
        port = parsed.port
    except ValueError:
        return ""

    authority = host if port in (None, 443) else f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    clean_query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not is_tracking_query_key(key)
    )
    return urlunsplit(("https", authority, path, urlencode(clean_query, doseq=True), ""))


def validate_source_url(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LinkingError("Each proposed source URL must be a non-empty trimmed string.")
    if UNSAFE_URL_CHARACTER_RE.search(value) or "&amp;" in value.casefold():
        raise LinkingError(f"Proposed source URL contains unsafe characters: {value!r}.")

    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError as exc:
        raise LinkingError(f"Proposed source URL is invalid: {value!r}.") from exc

    if parsed.scheme.casefold() != "https" or not parsed.netloc or not host:
        raise LinkingError(f"Proposed source URL must be an absolute HTTPS URL: {value!r}.")
    if parsed.username or parsed.password:
        raise LinkingError("Proposed source URLs must not contain credentials.")
    if port not in (None, 443):
        raise LinkingError("Proposed source URLs must use the standard HTTPS port.")
    if parsed.fragment:
        raise LinkingError("Proposed source URLs must not contain fragments.")
    if any(is_tracking_query_key(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise LinkingError("Proposed source URLs must not contain tracking parameters.")

    if is_search_result_page(host, parsed.path, parsed.query):
        raise LinkingError("Search-result pages are not valid evidence sources.")
    path_segments = [
        segment.casefold()
        for segment in unquote(parsed.path).strip("/").split("/")
        if segment
    ]
    if not path_segments:
        raise LinkingError("Generic homepages are not valid evidence sources.")
    if len(path_segments) == 1:
        leaf = path_segments[0]
        generic_filename = bool(re.fullmatch(r"(?:default|home|index)(?:\.[a-z0-9]+)?", leaf))
        locale_homepage = leaf in LOCALE_HOMEPAGE_PATHS
        if leaf in GENERIC_EVIDENCE_PATHS or generic_filename or locale_homepage:
            raise LinkingError("Generic section pages are not valid evidence sources.")
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal")) or "." not in host:
        raise LinkingError("Proposed source URLs must identify a public source host.")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise LinkingError("Proposed source URLs must use an authoritative domain, not an IP address.")

    return value


def opened_evidence_url_identities(response: Any) -> set[str]:
    """Return opened pages that also satisfy the evidence-URL policy."""
    identities: set[str] = set()
    for item in object_value(response, "output", []) or []:
        if object_value(item, "type") != "web_search_call":
            continue
        if object_value(item, "status") not in (None, "completed"):
            continue
        action = object_value(item, "action", {})
        if object_value(action, "type") not in {"open_page", "find_in_page"}:
            continue
        source_url = str(object_value(action, "url", "") or "")
        try:
            valid_url = validate_source_url(source_url)
        except LinkingError:
            continue
        identity = source_url_identity(valid_url)
        if identity:
            identities.add(identity)
    return identities


class RawTextSpanParser(HTMLParser):
    def __init__(self, raw_html: str):
        super().__init__(convert_charrefs=False)
        self.raw_html = raw_html
        self.line_starts = [0] + [match.end() for match in re.finditer("\n", raw_html)]
        self.anchor_depth = 0
        self.unlinkable_depth = 0
        self.invalid = ""
        self.spans: list[tuple[int, int]] = []

    def absolute_index(self) -> int:
        line, column = self.getpos()
        try:
            return self.line_starts[line - 1] + column
        except IndexError as exc:
            raise LinkingError("Could not map an HTML text node to the original body.") from exc

    def append_text_range(self, start: int, end: int) -> None:
        if self.anchor_depth or self.unlinkable_depth or end <= start:
            return
        if self.spans and self.spans[-1][1] == start:
            self.spans[-1] = (self.spans[-1][0], end)
        else:
            self.spans.append((start, end))

    def handle_data(self, data: str) -> None:
        start = self.absolute_index()
        end = start + len(data)
        if self.raw_html[start:end] != data:
            raise LinkingError("HTML parsing would not preserve a text node byte for byte.")
        self.append_text_range(start, end)

    def append_reference(self, reference: str) -> None:
        start = self.absolute_index()
        end = start + len(reference)
        if self.raw_html[start:end] != reference:
            alternate = reference[:-1]
            if not reference.endswith(";") or self.raw_html[start : start + len(alternate)] != alternate:
                raise LinkingError("HTML entity parsing would not preserve the body byte for byte.")
            end = start + len(alternate)
        self.append_text_range(start, end)

    def handle_entityref(self, name: str) -> None:
        self.append_reference(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.append_reference(f"&#{name};")

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        folded = tag.casefold()
        if folded == "a":
            if self.anchor_depth:
                self.invalid = "Existing nested anchors make the body unsafe to modify."
            self.anchor_depth += 1
        elif folded in UNLINKABLE_TAGS:
            self.unlinkable_depth += 1

    def handle_startendtag(self, _tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        folded = tag.casefold()
        if folded == "a":
            if not self.anchor_depth:
                self.invalid = "An unmatched closing anchor makes the body unsafe to modify."
            else:
                self.anchor_depth -= 1
        elif folded in UNLINKABLE_TAGS and self.unlinkable_depth:
            self.unlinkable_depth -= 1

    def validated_spans(self) -> list[tuple[int, int]]:
        self.feed(self.raw_html)
        self.close()
        if self.anchor_depth:
            self.invalid = "An unclosed existing anchor makes the body unsafe to modify."
        if self.unlinkable_depth:
            self.invalid = "An unclosed non-content tag makes the body unsafe to modify."
        if self.invalid:
            raise LinkingError(self.invalid)
        return self.spans


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def exact_text_spans(raw_html: str) -> list[tuple[int, int]]:
    return RawTextSpanParser(raw_html).validated_spans()


def html_text_content(raw_html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(raw_html)
    parser.close()
    return "".join(parser.parts)


def normalised_visible_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def validate_claim_text(original_html: str, proposal: LinkProposal) -> None:
    claim = normalised_visible_text(proposal.claim_text)
    anchor = normalised_visible_text(proposal.anchor_text)
    original = normalised_visible_text(html_text_content(original_html))
    if not claim or claim not in original:
        raise LinkingError("Each claim_text must copy existing visible post text exactly.")
    if not anchor or anchor not in claim:
        raise LinkingError("Each claim_text must contain its proposed anchor_text.")


def locate_proposals(
    original_html: str, proposals: list[LinkProposal]
) -> list[AnchorApplication]:
    spans = exact_text_spans(original_html)
    applications: list[AnchorApplication] = []
    seen_anchor_text: set[str] = set()

    for proposal in proposals:
        anchor_text = proposal.anchor_text
        if not anchor_text or anchor_text != anchor_text.strip():
            raise LinkingError("Each anchor_text must be non-empty and have no outer whitespace.")
        if normalised_visible_text(anchor_text).casefold() in GENERIC_ANCHOR_TEXT:
            raise LinkingError(f"Vague anchor text is not allowed: {anchor_text!r}.")
        if anchor_text in seen_anchor_text:
            raise LinkingError(f"Duplicate anchor_text is ambiguous: {anchor_text!r}.")
        seen_anchor_text.add(anchor_text)
        validate_claim_text(original_html, proposal)

        matches: list[tuple[int, int]] = []
        for span_start, span_end in spans:
            text = original_html[span_start:span_end]
            search_from = 0
            while True:
                relative = text.find(anchor_text, search_from)
                if relative < 0:
                    break
                start = span_start + relative
                matches.append((start, start + len(anchor_text)))
                search_from = relative + max(1, len(anchor_text))

        if len(matches) != 1:
            raise LinkingError(
                f"anchor_text must occur exactly once in one unlinked text node: {anchor_text!r} "
                f"(found {len(matches)})."
            )
        start, end = matches[0]
        applications.append(AnchorApplication(proposal=proposal, start=start, end=end))

    applications.sort(key=lambda item: item.start)
    for previous, current in zip(applications, applications[1:]):
        if current.start < previous.end:
            raise LinkingError("Proposed anchors overlap.")
    return applications


def apply_anchor_applications(
    original_html: str, applications: list[AnchorApplication]
) -> str:
    parts: list[str] = []
    cursor = 0
    for application in applications:
        parts.append(original_html[cursor : application.start])
        parts.append(application.opening_tag)
        parts.append(original_html[application.start : application.end])
        parts.append("</a>")
        cursor = application.end
    parts.append(original_html[cursor:])
    linked_html = "".join(parts)
    validate_anchor_only_change(original_html, linked_html, applications)
    return linked_html


def validate_anchor_only_change(
    original_html: str,
    linked_html: str,
    applications: list[AnchorApplication],
) -> None:
    original_index = 0
    linked_index = 0
    for application in applications:
        prefix_length = application.start - original_index
        expected_prefix = original_html[original_index : application.start]
        if linked_html[linked_index : linked_index + prefix_length] != expected_prefix:
            raise LinkingError("Non-link HTML changed before an inserted anchor.")
        linked_index += prefix_length
        if not linked_html.startswith(application.opening_tag, linked_index):
            raise LinkingError("An inserted opening anchor did not match the verified URL.")
        linked_index += len(application.opening_tag)

        anchor_length = application.end - application.start
        expected_anchor = original_html[application.start : application.end]
        if linked_html[linked_index : linked_index + anchor_length] != expected_anchor:
            raise LinkingError("Visible anchor text changed during insertion.")
        linked_index += anchor_length
        if not linked_html.startswith("</a>", linked_index):
            raise LinkingError("An inserted anchor is missing its exact closing tag.")
        linked_index += len("</a>")
        original_index = application.end

    expected_suffix = original_html[original_index:]
    if linked_html[linked_index:] != expected_suffix:
        raise LinkingError("Non-link HTML changed after the inserted anchors.")
    if html_text_content(linked_html) != html_text_content(original_html):
        raise LinkingError("Visible post text changed while adding evidence links.")


def link_input_content(post: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for image in post.get("images", []) or []:
        if not isinstance(image, dict):
            continue
        image_url = str(image.get("url") or "").strip()
        if image_url.startswith("https://"):
            content.append(
                {
                    "type": "input_image",
                    "image_url": image_url,
                    "detail": "high",
                }
            )
    return content


def link_response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": name,
            "strict": True,
            "schema": schema,
        },
        "verbosity": "low",
    }


def research_response_kwargs(
    config: PipelineConfig,
    prompts: dict[str, str],
    post: dict[str, Any],
    correction: str = "",
) -> dict[str, Any]:
    user_prompt = fill_placeholders(
        prompts["link_user"],
        {
            "POST_HTML": str(post.get("content") or ""),
            "PUBLICATION_DATE": str(post.get("published_at") or "Not supplied."),
            "SOURCE_URL": str(post.get("url") or "Not supplied."),
        },
    )
    if correction:
        user_prompt += (
            "\n\nCorrection required:\n"
            f"The previous response was rejected because {correction}\n"
            "Research again where necessary and return a completely new valid result."
        )
    return {
        "model": config.openai_model,
        "instructions": prompts["link_system"],
        "input": [{"role": "user", "content": link_input_content(post, user_prompt)}],
        "tools": [
            {
                "type": "web_search",
                "external_web_access": True,
                "search_context_size": "high",
            }
        ],
        "tool_choice": "auto",
        "include": ["web_search_call.action.sources"],
        "reasoning": {"effort": "medium"},
        "store": False,
        "text": link_response_format("authoritative_link_proposals", LINK_RESPONSE_SCHEMA),
    }


def parse_link_proposal(item: Any) -> LinkProposal:
    if not isinstance(item, dict) or set(item) != LINK_ITEM_KEYS:
        raise LinkingError("Each link proposal must contain exactly the required fields.")
    if not all(isinstance(item[key], str) for key in LINK_ITEM_KEYS):
        raise LinkingError("Every link proposal field must be a string.")
    source_url = validate_source_url(item["source_url"])
    if not item["source_title"].strip():
        raise LinkingError("Each proposed source must have a non-empty title.")
    if item["source_type"] not in LINK_SOURCE_TYPES:
        raise LinkingError(f"Unsupported source type: {item['source_type']!r}.")
    return LinkProposal(
        anchor_text=item["anchor_text"],
        claim_text=item["claim_text"],
        source_url=source_url,
        source_title=item["source_title"].strip(),
        source_type=item["source_type"],
    )


def validate_research_response(
    response: Any,
    original_html: str,
    *,
    skip_invalid_proposals: bool = False,
) -> tuple[str, list[LinkProposal], list[AnchorApplication], int]:
    payload = parse_response_json(response, "link research")
    if set(payload) != LINK_OUTPUT_KEYS:
        raise LinkingError("Link research JSON must contain exactly decision and links.")
    decision = payload["decision"]
    raw_links = payload["links"]
    if decision not in LINK_DECISIONS or not isinstance(raw_links, list):
        raise LinkingError("Link research returned an invalid decision or links value.")

    search_count, searched_urls, opened_urls = web_search_trace(response)
    opened_evidence_urls = opened_evidence_url_identities(response)
    if decision == "links" and not raw_links:
        raise LinkingError("The links decision requires at least one proposal.")
    if decision != "links" and raw_links:
        raise LinkingError("A zero-link decision must not contain proposals.")
    if decision == "no_suitable_source" and (
        search_count < 1 or not opened_evidence_urls
    ):
        raise LinkingError(
            "The no_suitable_source decision requires completed web research and an opened candidate page."
        )

    proposals: list[LinkProposal] = []
    applications: list[AnchorApplication] = []
    rejected = 0
    traced_urls = searched_urls | opened_urls
    for raw_link in raw_links:
        try:
            proposal = parse_link_proposal(raw_link)
            if source_url_identity(proposal.source_url) not in traced_urls:
                raise LinkingError(
                    "Every proposed source must come from a completed web search or opened page: "
                    + proposal.source_url
                )
            application = locate_proposals(original_html, [proposal])[0]
            if any(
                application.start < existing.end and existing.start < application.end
                for existing in applications
            ):
                raise LinkingError("Proposed anchors overlap.")
        except LinkingError:
            if not skip_invalid_proposals:
                raise
            rejected += 1
            continue
        proposals.append(proposal)
        applications.append(application)

    applications.sort(key=lambda item: item.start)
    if decision == "links" and not proposals:
        decision = "no_suitable_source"
    return decision, proposals, applications, rejected


def research_link_proposals(
    client: OpenAI,
    config: PipelineConfig,
    prompts: dict[str, str],
    post: dict[str, Any],
) -> tuple[str, list[LinkProposal], list[AnchorApplication], int]:
    last_error: LinkingError | None = None
    for _attempt in range(1, LINK_MAX_ATTEMPTS + 1):
        response = client.responses.create(
            **research_response_kwargs(
                config,
                prompts,
                post,
                correction=str(last_error or ""),
            )
        )
        try:
            return validate_research_response(
                response,
                str(post.get("content") or ""),
                skip_invalid_proposals=_attempt == LINK_MAX_ATTEMPTS,
            )
        except LinkingError as exc:
            last_error = exc

    raise LinkingError(
        f"OpenAI returned invalid link research after {LINK_MAX_ATTEMPTS} attempts. "
        f"Last error: {last_error}"
    ) from last_error


def verifier_proposals_json(proposals: list[LinkProposal]) -> str:
    serialised = []
    for index, proposal in enumerate(proposals, start=1):
        serialised.append(
            {
                "proposal_id": f"link_{index}",
                "anchor_text": proposal.anchor_text,
                "source_url": proposal.source_url,
                "source_title": proposal.source_title,
                "claimed_source_type": proposal.source_type,
            }
        )
    return json.dumps(serialised, ensure_ascii=False, indent=2)


def verification_response_kwargs(
    config: PipelineConfig,
    prompts: dict[str, str],
    post: dict[str, Any],
    proposals: list[LinkProposal],
    correction: str = "",
) -> dict[str, Any]:
    user_prompt = fill_placeholders(
        prompts["link_verify_user"],
        {
            "PUBLICATION_DATE": str(post.get("published_at") or "Not supplied."),
            "POST_HTML": str(post.get("content") or ""),
            "PROPOSALS": verifier_proposals_json(proposals),
        },
    )
    if correction:
        user_prompt += (
            "\n\nCorrection required:\n"
            f"The previous verification was rejected because {correction}\n"
            "Open every exact source again and return one valid verdict per proposal."
        )
    return {
        "model": config.openai_model,
        "instructions": prompts["link_verify_system"],
        "input": user_prompt,
        "tools": [
            {
                "type": "web_search",
                "external_web_access": True,
                "search_context_size": "high",
            }
        ],
        "tool_choice": "required",
        "include": ["web_search_call.action.sources"],
        "reasoning": {"effort": "medium"},
        "store": False,
        "text": link_response_format("authoritative_link_verdicts", LINK_VERIFICATION_SCHEMA),
    }


def coverage_verification_response_kwargs(
    config: PipelineConfig,
    prompts: dict[str, str],
    post: dict[str, Any],
    research_required: bool,
    correction: str = "",
) -> dict[str, Any]:
    user_prompt = fill_placeholders(
        prompts["link_coverage_verify_user"],
        {
            "RESEARCH_REQUIRED": "yes" if research_required else "no",
            "PUBLICATION_DATE": str(post.get("published_at") or "Not supplied."),
            "POST_HTML": str(post.get("content") or ""),
        },
    )
    if correction:
        user_prompt += (
            "\n\nCorrection required:\n"
            f"The previous audit was rejected because {correction}\n"
            "Audit the complete final body again and return one valid result."
        )
    return {
        "model": config.openai_model,
        "instructions": prompts["link_coverage_verify_system"],
        "input": user_prompt,
        "tools": [
            {
                "type": "web_search",
                "external_web_access": True,
                "search_context_size": "high",
            }
        ],
        "tool_choice": "required" if research_required else "auto",
        "include": ["web_search_call.action.sources"],
        "reasoning": {"effort": "medium"},
        "store": False,
        "text": link_response_format(
            "authoritative_link_coverage_audit",
            COVERAGE_VERIFICATION_SCHEMA,
        ),
    }


def validate_coverage_verification_response(
    response: Any,
    research_required: bool,
) -> bool:
    payload = parse_response_json(response, "link coverage verification")
    if set(payload) != COVERAGE_VERIFICATION_OUTPUT_KEYS:
        raise LinkingError("Link coverage verification JSON must contain exactly complete.")
    if not isinstance(payload["complete"], bool):
        raise LinkingError("The link coverage verifier complete value must be a boolean.")
    if research_required:
        search_count, _, _ = web_search_trace(response)
        if search_count < 1 or not opened_evidence_url_identities(response):
            raise LinkingError(
                "The final coverage audit requires completed web research and an opened candidate page."
            )
    return payload["complete"]


def verify_link_coverage(
    client: OpenAI,
    config: PipelineConfig,
    prompts: dict[str, str],
    post: dict[str, Any],
    *,
    research_required: bool,
) -> None:
    last_error: LinkingError | None = None
    for _attempt in range(1, LINK_MAX_ATTEMPTS + 1):
        response = client.responses.create(
            **coverage_verification_response_kwargs(
                config,
                prompts,
                post,
                research_required,
                correction=str(last_error or ""),
            )
        )
        try:
            complete = validate_coverage_verification_response(
                response,
                research_required,
            )
        except LinkingError as exc:
            last_error = exc
            continue
        if not complete:
            raise LinkingError(
                "Independent verification found incomplete evidence-link coverage."
            )
        return

    raise LinkingError(
        f"OpenAI returned invalid link coverage verification after {LINK_MAX_ATTEMPTS} attempts. "
        f"Last error: {last_error}"
    ) from last_error


def validate_verification_response(
    response: Any,
    proposals: list[LinkProposal],
) -> list[bool]:
    payload = parse_response_json(response, "link verification")
    if set(payload) != VERIFICATION_OUTPUT_KEYS or not isinstance(payload["verdicts"], list):
        raise LinkingError("Link verification JSON must contain exactly a verdicts array.")

    expected = {
        f"link_{index}": proposal
        for index, proposal in enumerate(proposals, start=1)
    }
    verdicts: dict[str, bool] = {}
    for item in payload["verdicts"]:
        if not isinstance(item, dict) or set(item) != VERDICT_KEYS:
            raise LinkingError("Every verifier verdict must contain exactly the required fields.")
        proposal_id = item["proposal_id"]
        if not isinstance(proposal_id, str) or proposal_id not in expected or proposal_id in verdicts:
            raise LinkingError("Verifier proposal IDs must match the proposed links exactly once.")
        if not isinstance(item["source_url"], str):
            raise LinkingError("Verifier source_url values must be strings.")
        expected_proposal = expected[proposal_id]
        if source_url_identity(item["source_url"]) != source_url_identity(expected_proposal.source_url):
            raise LinkingError("The verifier substituted a different source URL.")
        if not isinstance(item["supports_claim"], bool) or not isinstance(item["authoritative"], bool):
            raise LinkingError("Verifier support and authority verdicts must be booleans.")
        verdicts[proposal_id] = item["supports_claim"] and item["authoritative"]

    if set(verdicts) != set(expected):
        raise LinkingError("The verifier must return one verdict for every proposal.")

    opened_urls = opened_evidence_url_identities(response)
    unopened = [
        proposal.source_url
        for proposal in proposals
        if source_url_identity(proposal.source_url) not in opened_urls
    ]
    if unopened:
        raise LinkingError(
            "The independent verifier must open every exact proposed source: "
            + ", ".join(unopened)
        )
    return [verdicts[f"link_{index}"] for index in range(1, len(proposals) + 1)]


def verify_link_proposals(
    client: OpenAI,
    config: PipelineConfig,
    prompts: dict[str, str],
    post: dict[str, Any],
    proposals: list[LinkProposal],
) -> list[bool]:
    accepted: list[bool] = []
    for proposal in proposals:
        last_error: LinkingError | None = None
        for _attempt in range(1, LINK_MAX_ATTEMPTS + 1):
            response = client.responses.create(
                **verification_response_kwargs(
                    config,
                    prompts,
                    post,
                    [proposal],
                    correction=str(last_error or ""),
                )
            )
            try:
                accepted.extend(validate_verification_response(response, [proposal]))
                break
            except LinkingError as exc:
                last_error = exc
        else:
            raise LinkingError(
                f"OpenAI returned invalid link verification after {LINK_MAX_ATTEMPTS} attempts. "
                f"Last error: {last_error}"
            ) from last_error
    return accepted


def link_post_body(
    post: dict[str, Any],
    config: PipelineConfig,
    *,
    client: OpenAI | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not config.openai_api_key:
        raise LinkingError("OPENAI_API_KEY is missing.")
    original_html = str(post.get("content") or "")
    if not original_html or not strip_html_to_text(original_html):
        raise LinkingError("The post body is empty, so evidence links cannot be evaluated.")

    prompts = load_prompts()
    openai_client = client or OpenAI(api_key=config.openai_api_key)
    (
        research_decision,
        proposals,
        applications,
        locally_rejected,
    ) = research_link_proposals(openai_client, config, prompts, post)

    enriched = dict(post)
    if not proposals:
        verify_link_coverage(
            openai_client,
            config,
            prompts,
            post,
            research_required=(
                research_decision == "no_suitable_source" or locally_rejected > 0
            ),
        )
        audit = {
            "decision": research_decision,
            "links_added": 0,
            "proposals_reviewed": locally_rejected,
            "rejected_candidates": locally_rejected,
            "links": [],
        }
        return enriched, audit

    accepted = verify_link_proposals(
        openai_client,
        config,
        prompts,
        post,
        proposals,
    )
    accepted_by_proposal = dict(zip(proposals, accepted))
    accepted_applications = [
        application
        for application in applications
        if accepted_by_proposal[application.proposal]
    ]
    enriched["content"] = apply_anchor_applications(original_html, accepted_applications)
    rejected_by_verifier = accepted.count(False)
    verify_link_coverage(
        openai_client,
        config,
        prompts,
        enriched,
        research_required=locally_rejected > 0 or rejected_by_verifier > 0,
    )
    final_decision = "links" if accepted_applications else "no_suitable_source"
    audit_links = [
        {
            "anchor_text": application.proposal.anchor_text,
            "source_url": application.proposal.source_url,
            "source_title": application.proposal.source_title,
            "source_type": application.proposal.source_type,
        }
        for application in accepted_applications
    ]
    audit = {
        "decision": final_decision,
        "links_added": len(accepted_applications),
        "proposals_reviewed": locally_rejected + len(proposals),
        "rejected_candidates": (
            locally_rejected + len(proposals) - len(accepted_applications)
        ),
        "links": audit_links,
    }
    print(
        "Evidence-link research completed: "
        f"{audit['links_added']} added, {audit['rejected_candidates']} rejected."
    )
    return enriched, audit
