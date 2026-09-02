from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, UnidentifiedImageError

from .config import REPO_ROOT

STYLE_REFERENCE_DIR = REPO_ROOT / "assets" / "blog-main-image-style"
HUMAN_CONSEQUENCE_REFERENCE_IDS = frozenset({"03", "05", "08", "09", "11"})


@dataclass(frozen=True)
class StyleReference:
    reference_id: str
    filename: str
    description: str

    @property
    def path(self) -> Path:
        return STYLE_REFERENCE_DIR / self.filename


STYLE_REFERENCES: tuple[StyleReference, ...] = (
    StyleReference("01", "01-market-bubble-industrial-system.png", "industrial scale, smoky charcoal atmosphere, transparent fragility, and fine amber accents"),
    StyleReference("02", "02-strategy-fork-city-data-path.png", "rear-view human choice geometry with slate-blue and amber accents"),
    StyleReference("03", "03-human-craft-paper-automation.png", "close human craft, believable hands, tactile paper, and automation held in the background"),
    StyleReference("04", "04-machine-chess-value-tradeoff.png", "centered strategic confrontation with antique brass and burgundy"),
    StyleReference("05", "05-human-research-content-path.png", "quiet human concentration, warm task light, forest tones, and an information path"),
    StyleReference("06", "06-torn-transition-travel-community.png", "torn-paper transition, layered temporal depth, and muted warmth"),
    StyleReference("07", "07-primates-discovery-abundance.png", "organic discovery, social gathering, forest texture, and circular narrative transition"),
    StyleReference("08", "08-overload-gift-rice-system.png", "distressed foreground person, material clutter, spotlight, and a simplified symbolic process"),
    StyleReference("09", "09-wall-break-work-to-community.png", "human movement through a breaking boundary and a coherent environmental transformation"),
    StyleReference("10", "10-guided-path-versus-maze.png", "luminous route versus smoky maze with restrained blue accents"),
    StyleReference("11", "11-corporate-interview-ball-overload.png", "expressive human reaction inside an absurd workplace overload with minimal red accents"),
)
REFERENCE_BY_ID = {reference.reference_id: reference for reference in STYLE_REFERENCES}


def reference_catalog_prompt() -> str:
    return "\n".join(
        f"- {reference.reference_id}: {reference.description}"
        for reference in STYLE_REFERENCES
    )


def validated_style_references(reference_ids: Iterable[str]) -> tuple[StyleReference, ...]:
    chosen_ids = tuple(str(reference_id).zfill(2) for reference_id in reference_ids)
    if len(chosen_ids) != 3 or len(set(chosen_ids)) != 3:
        raise RuntimeError("The image concept must choose exactly three distinct bundled style references.")
    if not HUMAN_CONSEQUENCE_REFERENCE_IDS.intersection(chosen_ids):
        raise RuntimeError(
            "The image concept must include at least one human-consequence reference "
            "(03, 05, 08, 09, or 11)."
        )

    references: list[StyleReference] = []
    for reference_id in chosen_ids:
        reference = REFERENCE_BY_ID.get(reference_id)
        if reference is None:
            raise RuntimeError(f"Unknown bundled style reference: {reference_id}.")
        try:
            with Image.open(reference.path) as image:
                image.verify()
            signature = reference.path.read_bytes()[:8]
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise RuntimeError(f"Bundled style reference is missing or unreadable: {reference.path}.") from exc
        if signature != b"\x89PNG\r\n\x1a\n":
            raise RuntimeError(f"Bundled style reference is not a readable PNG: {reference.path}.")
        references.append(reference)
    return tuple(references)


def reference_manifest(references: Iterable[StyleReference]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for reference in references:
        image_bytes = reference.path.read_bytes()
        manifest.append(
            {
                "id": reference.reference_id,
                "filename": reference.filename,
                "sha256": hashlib.sha256(image_bytes).hexdigest(),
            }
        )
    return manifest
