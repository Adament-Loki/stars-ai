"""Download the canonical stock Stars! UNEDITED.MOD used by the design system.

Explicit utility: this script performs network access only when the user runs it.
"""
from pathlib import Path
from urllib.request import urlopen

URL = "https://raw.githubusercontent.com/stars-4x/starsapi/master/src/main/java/org/starsautohost/starsapi/items/UNEDITED.MOD"
DEST = Path(__file__).resolve().parents[1] / "src" / "stars_ai" / "UNEDITED.MOD"

with urlopen(URL, timeout=30) as r:
    data = r.read()
DEST.write_bytes(data)
print(f"Saved {len(data)} bytes to {DEST}")
