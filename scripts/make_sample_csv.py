"""Make a repeatable test CSV for the importer: lots of good rows plus every kind of trouble.

    python -m scripts.make_sample_csv [base_url] [rows] [output]
    defaults: http://mock-platforms:9100  1000  samples/catalogue/sample-1000.csv

base_url is where the fake community websites live (the mock platform server's /sites/ pages). The default is the address
the api container uses inside docker compose. The same seed always gives the same file, so results can be compared.
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://mock-platforms:9100"
TOTAL = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(__file__).resolve().parent.parent / "samples" / "catalogue" / "sample-1000.csv"
TOPICS = ["ai", "video", "marketing", "indie", "devtools", "design", "startups", "creators", "podcasts", "open-source"]
LANG_NAMES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "ja": "Japanese"}


def _shout_host(url: str) -> str:
    """HTTP://HOST:9100/sites/x : upper-case scheme and host (the same site), path left alone (paths are case-sensitive)."""
    scheme, rest = url.split("://", 1)
    host, _, path = rest.partition("/")
    return f"{scheme.upper()}://{host.upper()}/{path}"


def main() -> None:
    rnd = random.Random(2026)
    rows: list[list[str]] = []
    site = lambda slug: f"{BASE}/sites/{slug}"
    n_good, n_dup, n_name_dup, n_dead, n_slow, n_bad, n_noname = (int(TOTAL * f) for f in (0.62, 0.08, 0.03, 0.07, 0.05, 0.04, 0.02))
    good: list[tuple[str, str]] = []
    for i in range(n_good):
        kind = rnd.choice(["discourse", "ghost", "lemmy", "plain", "plain", "lang"])
        lang = rnd.choice(["de", "fr", "es", "ja"]) if kind == "lang" else "en"
        slug = f"lang-{lang}-{i}" if kind == "lang" else f"{kind}-{i}"
        declared = LANG_NAMES[lang] if rnd.random() < 0.9 else LANG_NAMES[rnd.choice(list(LANG_NAMES))]  # about 1 in 10 rows declares the wrong language
        name = f"Community {i}"
        good.append((name, slug))
        rows.append([name, site(slug), declared if rnd.random() < 0.8 else "", ";".join(rnd.sample(TOPICS, 2))])
    for _ in range(n_dup):  # the same site written differently
        name, slug = rnd.choice(good)
        variant = rnd.choice([_shout_host(site(slug)), site(slug) + "/", site(slug) + "?utm_source=newsletter", site(slug).replace("http://", "http://www.")])
        rows.append([f"{name} (copy {rnd.randint(1, 99)})", variant, "English", "ai"])
    for k in range(n_name_dup):  # same name as an earlier row, different site
        name, _ = rnd.choice(good)
        rows.append([name.lower(), site(f"plain-extra-{k}"), "English", ""])
    for k in range(n_dead):
        rows.append([f"Gone Community {k}", site(rnd.choice(["dead", "gone", "boom"]) + f"-{k}"), "English", ""])
    for k in range(n_slow):
        rows.append([f"Slow Community {k}", site(f"slow-{k}"), "English", ""])
    bad = ["", "not a url", "ftp://files.example.com", "http://", "just words here", "javascript:alert(1)", "https://exa mple.com"]
    for k in range(n_bad):
        rows.append([f"Broken Link {k}", bad[k % len(bad)], "English", ""])
    for k in range(n_noname):
        rows.append(["", site(f"plain-noname-{k}"), "English", ""])
    while len(rows) < TOTAL:
        k = len(rows)
        rows.append([f"Filler Community {k}", site(f"plain-filler-{k}"), "", ""])
    rnd.shuffle(rows)
    rows = rows[:TOTAL]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["name", "url", "language", "topics"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
