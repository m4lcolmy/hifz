"""Surah/Ayah → Mushaf page number mapping.

Reads the QCF4 per-page JSON metadata to build a fast
(surah, ayah) → page_number lookup table.
"""

import json

from src.config import QCF_PAGES_DIR, DATASET_DIR


class PageMap:
    """Maps (surah_id, ayah_id) tuples to Mushaf page numbers."""

    def __init__(self):
        self._map: dict[tuple[int, int], int] = {}
        self._build()

    def _build(self):
        """Build lookup table from QCF page JSONs (primary) or dataset fallback."""
        # Try QCF pages first
        if QCF_PAGES_DIR.exists():
            self._build_from_qcf()
        else:
            self._build_from_dataset()

    def _build_from_qcf(self):
        """Scan QCF page JSONs to build the lookup table."""
        for i in range(1, 605):
            path = QCF_PAGES_DIR / f"{i:03d}.json"
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for surah in data.get("surahs", []):
                    s_id = surah["id"]
                    for ayah in range(surah["verse_start"], surah["verse_end"] + 1):
                        self._map[(s_id, ayah)] = i
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Error reading QCF page {i}: {e}")

    def _build_from_dataset(self):
        """Fallback: scan dataset's per-page JSON files."""
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
