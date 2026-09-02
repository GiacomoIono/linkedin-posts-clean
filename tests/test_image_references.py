from __future__ import annotations

import hashlib
import unittest

from pipeline.image_references import (
    HUMAN_CONSEQUENCE_REFERENCE_IDS,
    STYLE_REFERENCES,
    validated_style_references,
)

EXPECTED_REFERENCE_HASHES = {
    "01-market-bubble-industrial-system.png": "9514e23896f9d1b0d013eed25ad83d5e45b5e30b5c779f667fb03715b9abcd93",
    "02-strategy-fork-city-data-path.png": "eb946782673c740c1bbf231482e644680ff095a83430bcb6d625556a0c4fc313",
    "03-human-craft-paper-automation.png": "4b4e044ba9b211dc5b6c16f998adfe1eea4ea7ef74e2eacbc8d8db59a2842b0a",
    "04-machine-chess-value-tradeoff.png": "147b7ea1733be1e2c2b924ea1a6892a775afc51c64ca1454bf15bbcc2f7a6640",
    "05-human-research-content-path.png": "6d446aeee9881e7312fdb8d880a551852e3c3d6b9224b086e65a6aaaca8a52ac",
    "06-torn-transition-travel-community.png": "7dba60176c9a2500e2c0f02945367dee50bcdf72e87c148f56183dc150cfb6ae",
    "07-primates-discovery-abundance.png": "8bc4754419267fe498b6b90ec14592b61a386b4f67bf914107b51a3649c4d03b",
    "08-overload-gift-rice-system.png": "9ce8f0089aa7fcb18dee7dce6ceb3084daea720ad48ca093fb0b8a7d60c05923",
    "09-wall-break-work-to-community.png": "eaed29ca0558e66c7ee0a5b9ccf61ca6bb688806f7ab55f5e17133e0353a92f5",
    "10-guided-path-versus-maze.png": "d8078936ca8ba2deb8e61b18c50ee04177b7ccbf693ec4a4733fa3dbdfb01f9c",
    "11-corporate-interview-ball-overload.png": "b97758864084fcf327810b922c3658fe47384160606d94404f850bd188072cbe",
}


class ImageReferenceTests(unittest.TestCase):
    def test_repository_bundles_all_eleven_unique_png_references(self) -> None:
        self.assertEqual(len(STYLE_REFERENCES), 11)
        self.assertEqual(len({reference.reference_id for reference in STYLE_REFERENCES}), 11)
        hashes = set()
        for reference in STYLE_REFERENCES:
            image_bytes = reference.path.read_bytes()
            self.assertTrue(image_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
            digest = hashlib.sha256(image_bytes).hexdigest()
            self.assertEqual(digest, EXPECTED_REFERENCE_HASHES[reference.filename])
            hashes.add(digest)
        self.assertEqual(len(hashes), 11)

    def test_reference_selection_requires_exactly_three_distinct_files(self) -> None:
        for invalid in (["03", "05"], ["03", "05", "05"], ["03", "05", "08", "09"]):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(RuntimeError, "exactly three distinct"):
                validated_style_references(invalid)

    def test_reference_selection_requires_human_consequence(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "human-consequence"):
            validated_style_references(["01", "04", "10"])

        selected = validated_style_references(["01", "03", "10"])
        self.assertTrue(
            HUMAN_CONSEQUENCE_REFERENCE_IDS.intersection(
                reference.reference_id for reference in selected
            )
        )


if __name__ == "__main__":
    unittest.main()
