#!/usr/bin/env python3
"""
Offline Mask Coordinate Generator for Mushaf Pages
===================================================

This script processes all Mushaf page images using OpenCV to:
1. Detect ayah finishing signs (ornate circled numbers) via contour analysis.
2. Compute precise bounding boxes for each text word on each line.
3. Save all coordinates to a JSON file for use by the app at runtime.

Usage (run once from the project root):
    conda activate offline-tarteel
    python tarteel/scripts/generate_masks.py

Output:
    tarteel/data/Quran_Dataset/Quran_pages_mask_coords.json
"""

import cv2
import numpy as np
import json
import os
import re
import sys
from collections import Counter

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
DATASET_DIR = os.path.join(PROJECT_ROOT, "tarteel", "data", "Quran_Dataset")
IMAGES_DIR = os.path.join(DATASET_DIR, "Quran_pages_white_background")
LINES_TXT = os.path.join(DATASET_DIR, "Quran_pages_lines_ayah_marker.txt")
OUTPUT_JSON = os.path.join(DATASET_DIR, "Quran_pages_mask_coords.json")


def parse_all_page_lines(txt_path: str) -> dict:
    """Parse the lines text file into a dict: {page_num: [line_text, ...]}."""
    pages = {}
    current_page = None
    with open(txt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("📄 Page"):
                current_page = int(line.split()[2])
                pages[current_page] = []
            elif current_page is not None and line.startswith("Line"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    pages[current_page].append(parts[1].strip())
    return pages


def detect_ayah_markers(binary_img: np.ndarray) -> list:
    """
    Detect ayah finishing signs (ornate circled numbers) using contour analysis.
    
    These markers are the most frequently occurring large, squarish contours
    on a page. They have a consistent area and aspect ratio ~1.0-1.1.
    
    Returns list of (x, y, w, h) tuples for each detected marker.
    """
    contours, _ = cv2.findContours(
        binary_img.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    # Collect info about all large-ish squarish contours
    large_squarish_areas = []
    contour_data = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        asp = bw / bh if bh > 0 else 0
        contour_data.append((x, y, bw, bh, area, asp))
        if area > 5000 and 0.7 < asp < 1.4:
            large_squarish_areas.append(int(area))

    if not large_squarish_areas:
        return []

    # The most common large squarish area is the ayah marker area
    counter = Counter(large_squarish_areas)
    marker_area, marker_count = counter.most_common(1)[0]

    # Need at least 2 markers to be confident (single large contour might be a header)
    if marker_count < 2:
        return []

    tolerance = marker_area * 0.05  # 5% tolerance for slight rendering differences

    markers = []
    for x, y, bw, bh, area, asp in contour_data:
        if abs(area - marker_area) < tolerance and 0.7 < asp < 1.4:
            markers.append((x, y, bw, bh))

    return markers


def process_page(page_num: int, page_lines_raw: list) -> dict:
    """
    Process a single Mushaf page and return its mask coordinates.
    
    Returns a dict with:
    - "markers": list of {x, y, w, h} for ayah finishing signs (NOT masked)
    - "word_boxes": list of {flat_idx, x, y, w, h} for text words (TO BE masked)
    - "header_lines": list of {line_idx, type} for surah/bismillah headers (NOT masked)
    """
    img_path = os.path.join(IMAGES_DIR, f"{page_num:03d}.png")
    if not os.path.exists(img_path):
        return None

    img = cv2.imread(img_path)
    if img is None:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    pg_h, pg_w = gray.shape
    _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)

    # === 1. Detect ayah markers ===
    all_markers = detect_ayah_markers(binary)

    # === 2. Find text bounding region ===
    rows = np.any(binary > 0, axis=1)
    cols = np.any(binary > 0, axis=0)
    if not np.any(rows) or not np.any(cols):
        return None

    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]

    text_top = int(rmin)
    text_bottom = int(rmax)
    text_left = int(cmin)
    text_right = int(cmax)
    text_width = text_right - text_left
    text_height = text_bottom - text_top

    num_lines = len(page_lines_raw)
    if num_lines == 0:
        return None

    line_height = text_height / num_lines

    # === 3. Build result ===
    result = {
        "page": page_num,
        "page_width": pg_w,
        "page_height": pg_h,
        "text_bounds": {
            "top": text_top,
            "bottom": text_bottom,
            "left": text_left,
            "right": text_right,
        },
        "markers": [
            {"x": int(mx), "y": int(my), "w": int(mw), "h": int(mh)}
            for mx, my, mw, mh in all_markers
        ],
        "word_boxes": [],
        "header_lines": [],
    }

    flat_idx = 0
    for line_idx, raw_text in enumerate(page_lines_raw):
        line_text = raw_text.replace(" | ", " ").replace("|", " ")
        ly = text_top + line_idx * line_height
        lh = line_height

        # Detect headers (surah titles and bismillah) — these are NOT masked
        if line_text.startswith("سورة") and len(line_text.split()) <= 3:
            result["header_lines"].append(
                {"line_idx": line_idx, "type": "surah"}
            )
            continue

        if line_text.strip() == "﷽":
            result["header_lines"].append(
                {"line_idx": line_idx, "type": "bismillah"}
            )
            continue

        # Handle embedded bismillah on same line as text
        if "﷽" in line_text:
            line_text = line_text.replace("﷽", "").strip()

        # Split into all tokens
        all_tokens = line_text.split()
        if not all_tokens:
            continue

        # Separate text words from ayah marker tokens
        text_tokens = []
        marker_token_positions = []  # index in all_tokens where a marker appears
        for tok_idx, t in enumerate(all_tokens):
            if re.match(r"^﴿.*?﴾$", t):
                marker_token_positions.append(tok_idx)
            else:
                text_tokens.append((tok_idx, t))

        if not text_tokens:
            continue

        # Find which CV-detected markers overlap this line
        line_markers_cv = []
        for mx, my, mw, mh in all_markers:
            my_center = my + mh / 2
            if ly <= my_center <= ly + lh:
                line_markers_cv.append((mx, my, mw, mh))

        # Sort markers by X position right-to-left (Arabic reading order)
        line_markers_cv.sort(key=lambda m: m[0], reverse=True)

        # Calculate available text width (subtract marker widths + small gaps)
        marker_gap = 5  # pixels of gap around each marker
        total_marker_width = sum(
            mw + marker_gap * 2 for mx, my, mw, mh in line_markers_cv
        )
        available_width = text_width - total_marker_width

        # Proportional word widths based on character count
        weights = [len(t) + 1.5 for _, t in text_tokens]
        total_weight = sum(weights)
        word_widths = [(wt / total_weight) * available_width for wt in weights]

        # Layout from right to left, interleaving markers at their positions
        current_x = float(text_right)
        text_word_idx = 0

        for tok_idx_in_line, token in enumerate(all_tokens):
            if re.match(r"^﴿.*?﴾$", token):
                # Ayah marker: use CV-detected position if available
                if line_markers_cv:
                    mx, my, mw, mh = line_markers_cv.pop(0)
                    current_x = float(mx - marker_gap)
            else:
                # Text word: place with proportional width
                if text_word_idx < len(word_widths):
                    ww = word_widths[text_word_idx]
                    x = current_x - ww

                    result["word_boxes"].append({
                        "flat_idx": flat_idx,
                        "x": round(x, 1),
                        "y": round(ly, 1),
                        "w": round(ww, 1),
                        "h": round(lh, 1),
                    })

                    current_x -= ww
                    text_word_idx += 1
                    flat_idx += 1

    return result


def main():
    print("=" * 60)
    print("  Mushaf Mask Coordinate Generator")
    print("=" * 60)

    # Validate paths
    if not os.path.exists(IMAGES_DIR):
        print(f"ERROR: Image directory not found: {IMAGES_DIR}")
        sys.exit(1)
    if not os.path.exists(LINES_TXT):
        print(f"ERROR: Lines text file not found: {LINES_TXT}")
        sys.exit(1)

    # Parse text file
    print(f"\nParsing {LINES_TXT}...")
    all_pages_lines = parse_all_page_lines(LINES_TXT)
    print(f"  Found {len(all_pages_lines)} pages in text file.")

    # Determine page range
    image_files = sorted(
        f for f in os.listdir(IMAGES_DIR) if f.endswith(".png")
    )
    page_nums = []
    for f in image_files:
        try:
            pn = int(f.replace(".png", ""))
            if pn > 0:  # skip page 0 if it exists (cover)
                page_nums.append(pn)
        except ValueError:
            pass

    print(f"  Found {len(page_nums)} page images (range: {min(page_nums)}-{max(page_nums)}).")

    # Process all pages
    all_results = {}
    total_markers = 0
    total_words = 0
    errors = []

    for i, pn in enumerate(page_nums):
        page_lines = all_pages_lines.get(pn, [])
        if not page_lines:
            errors.append(f"Page {pn}: no text data")
            continue

        result = process_page(pn, page_lines)
        if result is None:
            errors.append(f"Page {pn}: processing failed")
            continue

        all_results[str(pn)] = result
        total_markers += len(result["markers"])
        total_words += len(result["word_boxes"])

        # Progress
        if (i + 1) % 50 == 0 or i == len(page_nums) - 1:
            pct = (i + 1) / len(page_nums) * 100
            print(f"  [{pct:5.1f}%] Processed {i + 1}/{len(page_nums)} pages...")

    # Save
    print(f"\nSaving to {OUTPUT_JSON}...")
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False)

    file_size = os.path.getsize(OUTPUT_JSON) / (1024 * 1024)

    print(f"\n{'=' * 60}")
    print(f"  DONE!")
    print(f"  Pages processed: {len(all_results)}")
    print(f"  Total ayah markers detected: {total_markers}")
    print(f"  Total word boxes generated: {total_words}")
    print(f"  Output file: {OUTPUT_JSON}")
    print(f"  Output size: {file_size:.2f} MB")
    if errors:
        print(f"\n  WARNINGS ({len(errors)}):")
        for e in errors:
            print(f"    - {e}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
