"""Would a phoneme-aware edit distance change any verdict we actually make?

Tier E asked whether Tilawa's weighted edit distance (phonemeCost.ts) and
heardRatio could forgive our remaining false alarms. Rather than implement
either and measure afterwards, this computes what they would decide about the
words the app actually paints red — and the answer is that no threshold
separates a false alarm from a deliberate mistake.

Kept because the conclusion is worth being able to re-derive when the model
changes: if a future engine's errors stop overlapping with real ones, these
ideas become implementable and this script is how you would notice.

    python scripts/check_phoneme_costs.py
"""
import sys, re, unicodedata
sys.path.insert(0, "/home/hashus/code/hifz")

GROUPS = ["ذدضتط", "ظزذصسث", "جزش", "ةهت", "قكغ", "فبم"]
PAIRS = [("ه","ح"),("غ","خ"),("ء","ع"),("ن","م"),("ن","ل"),("ظ","ض")]
HAMZA = set("ءأإآاؤئ")
NEI = set()
for g in GROUPS:
    for i in range(len(g)):
        for j in range(i+1, len(g)):
            NEI.add(frozenset((g[i], g[j])))
for a, b in PAIRS:
    NEI.add(frozenset((a, b)))

def cost(a, b):
    if a == b: return 0.0
    if a in HAMZA and b in HAMZA: return 0.1
    if frozenset((a, b)) in NEI: return 0.25
    return 1.0

def strip(w):
    w = "".join(c for c in unicodedata.normalize("NFD", w)
                if not unicodedata.combining(c))
    return w.replace("ٱ","ا").replace("ى","ي")

def dist(h, e):
    h, e = strip(h), strip(e)
    prev = [float(j) for j in range(len(e)+1)]
    for i in range(1, len(h)+1):
        cur = [float(i)] + [0.0]*len(e)
        for j in range(1, len(e)+1):
            cur[j] = min(prev[j]+1, cur[j-1]+1, prev[j-1]+cost(h[i-1], e[j-1]))
        prev = cur
    return prev[-1] / max(len(e), 1)

# (heard, reference) pairs the app currently paints red, from the real data
CASES = [
    ("أَلِيمٌ","عَظِيمٌ","benchmark false alarm"),
    ("فَلَهُمْ","وَلَهُمْ","DELIBERATE MISTAKE - must stay caught"),
    ("أَوْ","وَأُنثَىٰ","DELIBERATE MISTAKE - must stay caught"),
    ("خُوبًا","حُوبًا","live false alarm (4:2:14)"),
    ("فُصِّدَتْ","فُصِّلَتْ","live false alarm (41:3:1)"),
    ("فَقَالُوا","وَقَالُوا","live false alarm (41:5:0)"),
    ("فَلَكُورٌ","فَلِأُمِّهِ","live false alarm (4:11)"),
    ("يَتَامَى","الْيَتَامَىٰ","real: dropped ال"),
]
print(f"{'heard':<14}{'reference':<16}{'dist':>6}   note")
for h, e, note in CASES:
    print(f"  {h:<14}{e:<16}{dist(h,e):>6.2f}   {note}")
