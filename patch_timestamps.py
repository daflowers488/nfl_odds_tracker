from pathlib import Path

p = Path("odds_pull.py")
src = p.read_text(encoding="utf-8")

# Add UTC import if missing
if "from datetime import UTC" not in src:
    src = src.replace("import requests", "import requests\nfrom datetime import UTC")

# Replace utcnow() -> now(UTC) for month key
src = src.replace(
    'return dt.datetime.utcnow().strftime("%Y%m")',
    'return dt.datetime.now(UTC).strftime("%Y%m")'
)

# Replace utcnow() -> now(UTC) for snapshot timestamp (keep trailing Z)
src = src.replace(
    'ts = dt.datetime.utcnow().isoformat(timespec="seconds")+"Z"',
    'ts = dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")'
)

p.write_text(src, encoding="utf-8")
print("Patched odds_pull.py ✅")
