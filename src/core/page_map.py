"""Surah/Ayah → Mushaf page number mapping.

Reads the per-page JSON metadata from the Quran Dataset to build
a fast (surah, ayah) → page_number lookup table.
"""

import json
import os

from src.config import DATASET_DIR


class PageMap:
    """Maps (surah_id, ayah_id) tuples to Mushaf page numbers."""

    def __init__(self):
        self._map: dict[tuple[int, int], int] = {}
        self._build()

    def _build(self):
        """Scan the dataset's per-page JSON files to build the lookup table."""
        data_dir = DATASET_DIR / "Quran_pages_data_json"
        if not data_dir.exists():
            print(f"Warning: Dataset not found at {data_dir}")
            return

        for i in range(1, 605):
            path = data_dir / f"page_{i}.json"
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for ayah in data.get("ayahs", []):
                        self._map[(ayah["sura"], ayah["ayah"])] = i

    def get(self, surah_id: int, ayah_id: int) -> int | None:
        """Return the page number for a given surah and ayah, or None."""
        return self._map.get((surah_id, ayah_id))
