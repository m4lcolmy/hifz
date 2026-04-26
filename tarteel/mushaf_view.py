import os
import json
import csv
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPathItem
from PyQt6.QtGui import QPixmap, QColor, QPainterPath, QBrush, QPen
from PyQt6.QtCore import Qt, QRectF

class MushafView(QGraphicsView):
    """
    A Qt component that displays a Mushaf page entirely masked by a solid color.
    As recitation progresses, it unmasks specific regions (bounding boxes) and
    applies a semi-transparent green/red overlay to indicate correct/incorrect recitation.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setStyleSheet("background: transparent;")
        
        self.page_item = None
        self.mask_item = None
        
        # word_boxes maps (surah_id, ayah_id, word_index) to QRectF
        self.word_boxes = {}
        self.highlight_items = {}
        
        # Colors
        self.mask_color = Qt.GlobalColor.white 
        self.correct_color = QColor(0, 255, 0, 80)    # Green, semi-transparent
        self.incorrect_color = QColor(255, 0, 0, 80)  # Red, semi-transparent

        self.current_page_num = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene.sceneRect().isValid():
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def load_dataset_page(self, dataset_dir: str, page_num: int):
        self.scene.clear()
        self.word_to_box = {}
        self.current_page_num = page_num
        
        # 1. Load Background Image
        img_path = os.path.join(dataset_dir, "Quran_pages_white_background", f"{page_num:03d}.png")
        
        pixmap = QPixmap(img_path)
        if pixmap.isNull():
            print(f"Error: Could not load image at {img_path}")
            return
            
        self.page_item = self.scene.addPixmap(pixmap)
        self.scene.setSceneRect(QRectF(pixmap.rect()))
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        
        page_width = pixmap.width()
        page_height = pixmap.height()
        
        # Dynamically calculate exact text bounding box
        import numpy as np
        from PIL import Image
        
        try:
            img_pil = Image.open(img_path).convert("L")
            arr = np.array(img_pil)
            rows = np.any(arr < 250, axis=1)
            cols = np.any(arr < 250, axis=0)
            
            if np.any(rows) and np.any(cols):
                rmin, rmax = np.where(rows)[0][[0, -1]]
                cmin, cmax = np.where(cols)[0][[0, -1]]
                m_top = rmin
                m_bottom = page_height - rmax
                m_left = cmin
                m_right = page_width - cmax
            else:
                raise ValueError("Blank image")
        except Exception as e:
            print(f"Fallback bbox due to error: {e}")
            m_top = 0.015 * page_height
            m_bottom = 0.028 * page_height
            m_left = 0.053 * page_width
            m_right = 0.043 * page_width

        text_width = page_width - m_left - m_right
        
        # 2. Extract Lines and Tokens
        page_lines = []
        in_page = False
        txt_path = os.path.join(dataset_dir, "Quran_pages_lines_ayah_marker.txt")
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("📄 Page"):
                        if int(line.split()[2]) == page_num:
                            in_page = True
                        elif in_page:
                            break
                    elif in_page and line.startswith("Line"):
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            page_lines.append(parts[1].strip())
        except Exception as e:
            print(f"Error reading lines txt: {e}")
            return
            
        flat_tokens = []
        lines_word_counts = []
        for line_text in page_lines:
            line_text = line_text.replace(" | ", " ").replace("|", " ")
            
            # Skip Surah headers
            if line_text.startswith("سورة") and len(line_text.split()) <= 3:
                lines_word_counts.append(0)
                continue
                
            # Skip standalone Bismillah lines
            if line_text.strip() == "﷽":
                lines_word_counts.append(0)
                continue
                
            # Handle special case (Page 187: Surah At-Tawbah) where Bismillah 
            # is incorrectly placed on the same line as the Ayah text in the dataset
            if "﷽" in line_text:
                line_text = line_text.replace("﷽", "").strip()
                
            tokens = line_text.split()
            lines_word_counts.append(len(tokens))
            for t in tokens:
                flat_tokens.append(t)
                
        # Generate Boxes
        text_height = page_height - m_top - m_bottom
        line_height = text_height / len(lines_word_counts) if lines_word_counts else 0
        
        flat_boxes = {}
        current_idx = 0
        padding = 0  # No padding so boxes perfectly tile to hide the text
        for line_idx, word_count in enumerate(lines_word_counts):
            if word_count == 0: continue
            word_width = text_width / word_count
            for w_idx_in_line in range(word_count):
                x = page_width - m_right - (w_idx_in_line + 1) * word_width
                y = m_top + line_idx * line_height
                rect = QRectF(x + padding, y + padding, word_width - padding*2, line_height - padding*2)
                flat_boxes[current_idx] = rect
                current_idx += 1
                
        # 3. Align with quran.json
        import json, re, difflib
        def normalize(t):
            t = t.replace('ـ', '')
            t = re.sub(r'[أإآٱ]', 'ا', t)
            t = t.replace('ى', 'ي')
            t = t.replace('ؤ', 'و')
            t = t.replace('ئ', 'ي')
            t = t.replace('ة', 'ه')
            t = re.sub(r'[ً-ٰٟۖ-ۜ۟-۪ۨ-ۭ]', '', t)
            t = re.sub(r'[^\w\s]', '', t)
            t = re.sub(r'\d+', '', t)
            t = re.sub(r'[٠-٩]', '', t)
            t = t.replace('ا', '')
            return t.strip()
            
        quran_json_path = os.path.join(os.path.dirname(dataset_dir), "quran.json")
        data_json_path = os.path.join(dataset_dir, "Quran_pages_data_json", f"page_{page_num}.json")
        try:
            with open(quran_json_path, 'r', encoding='utf-8') as f:
                quran_data = json.load(f)
            with open(data_json_path, 'r', encoding='utf-8') as f:
                page_ayahs = json.load(f).get("ayahs", [])
                
            q_words_flat = []
            q_words_meta = []
            for ayah_meta in page_ayahs:
                s = ayah_meta["sura"]
                a = ayah_meta["ayah"]
                for sura_node in quran_data:
                    if sura_node["id"] == s:
                        for verse in sura_node["verses"]:
                            if verse["id"] == a:
                                for idx, qw in enumerate(verse["text"].split()):
                                    q_words_flat.append(qw)
                                    q_words_meta.append((s, a, idx))
                                break
                        break
                        
            q_norm = [normalize(w) for w in q_words_flat]
            f_norm = [normalize(w) for w in flat_tokens]
            
            for meta in q_words_meta:
                self.word_to_box[meta] = []
                
            sm = difflib.SequenceMatcher(None, q_norm, f_norm)
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                if op == 'equal' or op == 'replace':
                    for k in range(max(i2 - i1, j2 - j1)):
                        qi = i1 + k if i1 + k < i2 else i2 - 1
                        fi = j1 + k if j1 + k < j2 else j2 - 1
                        meta = q_words_meta[qi]
                        if fi not in self.word_to_box[meta]:
                            self.word_to_box[meta].append(fi)
                elif op == 'insert':
                    for fi in range(j1, j2):
                        if i1 > 0:
                            meta = q_words_meta[i1 - 1]
                            self.word_to_box[meta].append(fi)
                        elif i1 < len(q_words_meta):
                            meta = q_words_meta[i1]
                            self.word_to_box[meta].append(fi)
        except Exception as e:
            print(f"Error mapping words: {e}")

        # 4. Create QGraphicsRectItems for each box
        self.box_items = {}
        for idx, rect in flat_boxes.items():
            item = self.scene.addRect(rect)
            item.setPen(Qt.GlobalColor.transparent)
            
            f_t = flat_tokens[idx]
            # If it's an ayah marker or a waqf sign, don't hide it with a white mask initially
            if re.match(r'^﴿.*?﴾$', f_t) or f_t in ['ۖ', 'ۗ', 'ۘ', 'ۙ', 'ۚ', 'ۛ', '۩', '۞', 'س', 'ع', 'ج', 'م', 'قلي', 'صلي', 'لا', '∴']:
                item.setBrush(QColor(255, 255, 255, 0)) # Transparent
            else:
                item.setBrush(QColor(255, 255, 255, 255)) # Opaque white
                
            self.box_items[idx] = item

    def update_recitation(self, surah: int, ayah: int, word_index: int, is_correct: bool):
        if not hasattr(self, 'box_items'):
            return
            
        box_indices = self.word_to_box.get((surah, ayah, word_index))
        if box_indices is None:
            return
            
        for b_idx in box_indices:
            item = self.box_items.get(b_idx)
            if item:
                if is_correct:
                    item.setBrush(QColor(34, 197, 94, 80)) # Green
                else:
                    item.setBrush(QColor(239, 68, 68, 80)) # Red
