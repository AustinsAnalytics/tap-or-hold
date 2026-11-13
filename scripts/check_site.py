"""Pre-publish check for the static site (runs in GitHub Actions before every deploy, standard library only).

Fails if a page references a file that does not exist, a data file does not parse, a template token was left
unfilled, or the win-probability table has the wrong size.
"""
import json
import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
errors = []

html = (SITE / "index.html").read_text(encoding="utf-8")
if "{{" in html or "}}" in html:
    errors.append("unfilled template token in index.html")
for ref in re.findall(r'(?:src|href)="([^"#:]+)"', html):
    if ref.startswith(("http", "mailto", "//")):
        continue
    if not (SITE / ref).exists():
        errors.append(f"missing file referenced by index.html: {ref}")
for f in (SITE / "data").glob("*.json"):
    try:
        json.loads(f.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"{f.name} does not parse: {e}")
meta = json.loads((SITE / "data" / "we_meta.json").read_text())
n = 1
for d in meta["shape"]:
    n *= d
size = (SITE / "data" / "we.bin").stat().st_size
if size != 2 * n:
    errors.append(f"we.bin has {size} bytes, expected {2 * n}")
for js in (SITE / "assets").glob("*.js"):
    for ref in re.findall(r'fetch\(base \+ "([^"]+)"\)|fetch\("([^"]+)"\)', js.read_text()):
        name = next(x for x in ref if x)
        path = SITE / ("data/" + name if not name.startswith("data/") else name)
        if not path.exists():
            errors.append(f"{js.name} fetches a missing file: {name}")

if errors:
    print("\n".join("FAIL: " + e for e in errors))
    sys.exit(1)
print(f"site OK: {len(list(SITE.rglob('*')))} files")
