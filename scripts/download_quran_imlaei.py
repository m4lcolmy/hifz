#!/usr/bin/env python3
"""Download Quran text in Imla'i script (with tashkeel) from quran.com API.

Fetches all 114 surahs and builds a quran.json that matches the existing
format but uses standard Arabic (Imla'i) text with full diacritics.

This text closely matches Whisper's Arabic output, making comparison accurate.
"""

import json
import time
import urllib.request
import sys
from pathlib import Path

API_BASE = "https://api.quran.com/api/v4"
OUTPUT = Path(__file__).parent.parent / "data" / "quran.json"

# We need surah metadata (names, etc.) from the existing file
EXISTING = OUTPUT


def fetch_json(url: str) -> dict:
    """Fetch JSON from a URL with retry."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Hifz-Quran-App/1.0",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt < 2:
                print(f"  Retry {attempt + 1} for {url}: {e}")
                time.sleep(2)
            else:
                raise


def main():
    # Load existing data for surah metadata
    with open(EXISTING, encoding="utf-8") as f:
        existing = json.load(f)

    surah_meta = {}
    for s in existing:
        surah_meta[s["id"]] = {
            "name": s["name"],
            "transliteration": s.get("transliteration", ""),
            "type": s.get("type", ""),
            "total_verses": s.get("total_verses", 0),
        }

    result = []

    for chapter_num in range(1, 115):
        meta = surah_meta.get(chapter_num, {})
        print(f"Fetching surah {chapter_num}/114: {meta.get('name', '?')}...")

        url = f"{API_BASE}/quran/verses/imlaei?chapter_number={chapter_num}"
        data = fetch_json(url)

        verses = []
        for v in data["verses"]:
            verse_key = v["verse_key"]  # e.g. "1:3"
            ayah_num = int(verse_key.split(":")[1])
            verses.append({
                "id": ayah_num,
                "text": v["text_imlaei"],
            })

        result.append({
            "id": chapter_num,
            "name": meta.get("name", ""),
            "transliteration": meta.get("transliteration", ""),
            "type": meta.get("type", ""),
            "total_verses": meta.get("total_verses", len(verses)),
            "verses": verses,
        })

        # Be polite to the API
        time.sleep(0.3)

    # Write output
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=None)

    print(f"\nDone! Wrote {len(result)} surahs to {OUTPUT}")
    print(f"File size: {OUTPUT.stat().st_size / 1024:.1f} KB")

    # Quick verification
    sample = result[0]["verses"][0]["text"]
    print(f"Sample (Al-Fatiha 1:1): {sample}")


if __name__ == "__main__":
    main()
