"""CSV import for the destination catalogue (Advanced level).

A CSV of candidate destinations goes in; a row-by-row report and new catalogue rows come out. For every row:
  1. validate     required columns, a usable URL, a known language code
  2. normalise    https by default, lower-case host, no "www.", no fragment, no tracking parameters, no trailing slash
  3. de-duplicate against earlier rows of the same file AND against the catalogue (same site or same name)
  4. reachability does the site answer? (concurrent, with a timeout, one retry, politely: one request per host at a time)
  5. enrichment   page title, language (<html lang>), and which platform it runs (Discourse, Ghost, Lemmy ...)
Accepted rows become catalogue rows with CONSERVATIVE rules (no self-promotion, one link, disclosure, one post a week)
until a person confirms better rules. A site that runs a platform we have a connector for gets that connector; any other
site becomes an "assisted" destination (a person posts by hand with our assist pack).

Safety: the service fetches URLs that came from a file, so by default it refuses private, loopback and link-local
addresses (server-side request forgery). Local demos switch that off with IMPORT_ALLOW_PRIVATE_HOSTS=true.
"""
from __future__ import annotations

import csv
import hashlib
import io
import ipaddress
import re
import socket
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.catalogue import Catalogue, slugify
from app.errors import ErrorCode, IncuBrixError
from app.models import Destination
from app.output_manifest import build_manifest

MAX_ROWS = 10_000
MAX_CSV_BYTES = 20_000_000
MAX_PAGE_BYTES = 200_000
REQUIRED = ("name", "url")
REPORT_COLUMNS = ["row", "name", "url", "status", "reasons", "warnings", "normalized_url", "final_url", "http_status", "response_ms",
                  "detected_platform", "detected_language", "language", "title", "connector_key", "destination_id", "duplicate_of"]

CONSERVATIVE_RULES = {  # used until a person confirms better rules (see the rules-capture feature)
    "frequency_cap": {"max_posts": 1, "per_seconds": 604800},
    "self_promo_allowed": False,
    "link_policy": "max:1",
    "disclosure_required": True,
    "automation_allowed": True,
}
PLATFORM_CONNECTORS = {"discourse": "discourse", "ghost": "ghost", "lemmy": "lemmy", "devto": "devto"}
HOST_PLATFORMS = {"dev.to": "devto", "hashnode.com": "hashnode"}
LANGUAGES = {"english": "en", "german": "de", "deutsch": "de", "french": "fr", "francais": "fr", "spanish": "es", "espanol": "es",
             "portuguese": "pt", "italian": "it", "dutch": "nl", "hindi": "hi", "japanese": "ja", "korean": "ko", "chinese": "zh",
             "russian": "ru", "arabic": "ar", "turkish": "tr", "polish": "pl", "swedish": "sv"}
TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$)")


# ---------------------------------------------------------------- pure helpers (unit-tested)
def normalize_url(raw: str, allow_local: bool = False) -> tuple[str | None, str | None]:
    """Returns (normalised_url, None) or (None, reason). allow_local accepts single-word hosts such as docker service names."""
    raw = (raw or "").strip()
    if not raw:
        return None, "url is empty"
    if " " in raw:
        return None, "url contains spaces"
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None, "url cannot be parsed"
    if parts.scheme not in ("http", "https"):
        return None, f"scheme {parts.scheme!r} is not http or https"
    if not host:
        return None, "url has no host"
    if "." not in host and not _is_ip(host) and not (allow_local and host):
        return None, "host is not a domain name"
    if host.startswith("www."):
        host = host[4:]
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)))
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, netloc, path, query, "")), None


def dedupe_key(normalized_url: str) -> str:
    p = urlsplit(normalized_url)
    return f"{p.netloc}{p.path}?{p.query}" if p.query else f"{p.netloc}{p.path}"


def normalize_language(raw: str | None) -> tuple[str | None, str | None]:
    """'English' / 'en-US' / 'EN' -> 'en'. Returns (code, warning)."""
    value = (raw or "").strip().lower().replace("_", "-")
    if not value:
        return None, None
    if value in LANGUAGES:
        return LANGUAGES[value], None
    if re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})?", value):
        return value.split("-")[0], None
    return None, f"language {raw!r} not recognised"


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def is_private_host(host: str) -> bool:
    """True when the host resolves to a private, loopback or link-local address. Unresolvable hosts are 'not private'
    (the fetch will fail on its own)."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    return any((lambda a: a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast)(ipaddress.ip_address(x)) for x in addresses)


_META = re.compile(r"<meta\s+[^>]*?>", re.I)


def parse_page(html: str) -> dict[str, str | None]:
    """Title, language, generator and description, with plain regular expressions (no HTML library needed)."""
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    lang = re.search(r"<html[^>]*\blang\s*=\s*[\"']?([A-Za-z-]+)", html, re.I)
    info: dict[str, str | None] = {"title": re.sub(r"\s+", " ", title.group(1)).strip()[:200] if title else None,
                                   "lang": lang.group(1) if lang else None, "generator": None, "description": None}
    for tag in _META.findall(html):
        name = re.search(r"name\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        content = re.search(r"content\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        if name and content:
            if name.group(1).lower() == "generator":
                info["generator"] = content.group(1)[:100]
            elif name.group(1).lower() == "description":
                info["description"] = content.group(1)[:300]
    return info


def detect_platform(host: str, generator: str | None) -> str | None:
    for domain, platform in HOST_PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    g = (generator or "").lower()
    for platform in ("discourse", "ghost", "lemmy", "wordpress", "mastodon", "flarum", "nodebb"):
        if platform in g:
            return platform
    return None


def parse_csv(text: str) -> list[dict[str, str]]:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "the CSV is empty")
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames]
    missing = [c for c in REQUIRED if c not in reader.fieldnames]
    if missing:
        raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"the CSV needs the columns {list(REQUIRED)}; missing {missing}", {"columns_found": reader.fieldnames})
    rows = []
    for raw in reader:
        rows.append({k: (v or "").strip() for k, v in raw.items() if k})
        if len(rows) > MAX_ROWS:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"too many rows (limit {MAX_ROWS})")
    return rows


# ---------------------------------------------------------------- the importer
class Importer:
    def __init__(self, catalogue: Catalogue, http: httpx.Client, *, workers: int = 32, allow_private_hosts: bool = False,
                 fetch_timeout_s: float = 5.0, clock: Callable[[], float] = time.time) -> None:
        self.catalogue, self.http, self.workers = catalogue, http, workers
        self.allow_private_hosts, self.fetch_timeout_s, self.clock = allow_private_hosts, fetch_timeout_s, clock
        self._host_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def run(self, csv_text: str, *, commit: bool = True, progress: Callable[[int], None] = lambda p: None) -> dict[str, Any]:
        started = self.clock()
        rows = parse_csv(csv_text)
        results = [self._blank(i, r) for i, r in enumerate(rows, start=2)]  # row 1 is the header, as in a spreadsheet
        by_row = {r["row"]: r for r in results}

        self._validate_and_dedupe(rows, results)
        todo = [r for r in results if r["status"] == "pending"]
        done = 0
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="import") as pool:
            for _ in pool.map(self._check_site, todo):
                done += 1
                progress(int(90 * done / max(len(todo), 1)))
        for r in todo:
            if r["status"] == "pending":
                r["status"] = "accepted"

        created = 0
        if commit:
            for r in results:
                if r["status"] == "accepted":
                    created += self._add_to_catalogue(r, by_row)
        progress(99)
        finished = self.clock()
        counts = {s: sum(1 for r in results if r["status"] == s) for s in ("accepted", "duplicate", "invalid", "unreachable")}
        warnings = [f"row {r['row']}: {w}" for r in results for w in r["warnings"]][:200]
        return {
            "rows": len(results), **counts, "catalogue_rows_created": created, "committed": commit,
            "duration_s": round(finished - started, 2), "rows_per_second": round(len(results) / max(finished - started, 0.001), 1),
            "report": public_report(results),
            "manifest": build_manifest(kind="csv_import", started_at=started, finished_at=finished,
                                       settings={"workers": self.workers, "fetch_timeout_s": self.fetch_timeout_s, "allow_private_hosts": self.allow_private_hosts, "commit": commit},
                                       warnings=warnings),
        }

    def fetch_csv(self, url: str) -> str:
        """Download a CSV from an address (refusing private addresses, like every fetch the importer makes)."""
        normalized, reason = normalize_url(url, self.allow_private_hosts)
        if reason:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"cannot use that address: {reason}")
        if not self.allow_private_hosts and is_private_host(urlsplit(normalized).hostname or ""):
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "the service will not fetch private or local addresses")
        data: list[bytes] = []
        try:
            with self.http.stream("GET", normalized, timeout=self.fetch_timeout_s * 2, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"the CSV address answered HTTP {resp.status_code}", {"http_status": resp.status_code})
                size = 0
                for chunk in resp.iter_bytes():
                    data.append(chunk)
                    size += len(chunk)
                    if size > MAX_CSV_BYTES:
                        raise IncuBrixError(ErrorCode.VALIDATION_ERROR, f"the CSV is larger than {MAX_CSV_BYTES // 1_000_000} MB")
        except httpx.HTTPError as exc:
            raise IncuBrixError(ErrorCode.PLATFORM_UNAVAILABLE, f"could not download the CSV ({type(exc).__name__})") from None
        try:
            return b"".join(data).decode("utf-8")
        except UnicodeDecodeError:
            raise IncuBrixError(ErrorCode.VALIDATION_ERROR, "the CSV must be UTF-8 text") from None

    # ---------- step 1-3: validate, normalise, de-duplicate ----------
    @staticmethod
    def _blank(row_number: int, raw: dict[str, str]) -> dict[str, Any]:
        return {"row": row_number, "name": raw.get("name", ""), "url": raw.get("url", ""), "status": "pending", "reasons": [], "warnings": [],
                "normalized_url": None, "final_url": None, "http_status": None, "response_ms": None, "detected_platform": None,
                "detected_language": None, "language": None, "title": None, "connector_key": None, "destination_id": None, "duplicate_of": None,
                "_raw": raw}

    def _validate_and_dedupe(self, rows: list[dict[str, str]], results: list[dict[str, Any]]) -> None:
        existing = self._existing_catalogue()
        seen_url: dict[str, int] = {}
        seen_name: dict[str, int] = {}
        for raw, r in zip(rows, results):
            if not raw.get("name"):
                r["reasons"].append("name is empty")
            normalized, reason = normalize_url(raw.get("url", ""), self.allow_private_hosts)
            if reason:
                r["reasons"].append(reason)
            lang, lang_warning = normalize_language(raw.get("language"))
            if lang_warning:
                r["warnings"].append(lang_warning)
            r["language"] = lang
            if r["reasons"]:
                r["status"] = "invalid"
                continue
            r["normalized_url"] = normalized
            key, name_key = dedupe_key(normalized), raw["name"].casefold()
            if key in seen_url or name_key in seen_name:
                first = seen_url.get(key) or seen_name.get(name_key)
                r["status"], r["duplicate_of"] = "duplicate", f"row {first}"
                r["reasons"].append("same site as an earlier row" if key in seen_url else "same name as an earlier row")
                continue
            if key in existing["urls"] or slugify(raw["name"]) in existing["ids"]:
                dup_id = existing["urls"].get(key) or slugify(raw["name"])
                r["status"], r["duplicate_of"] = "duplicate", f"catalogue: {dup_id}"
                r["reasons"].append("already in the catalogue")
                continue
            seen_url[key] = seen_name[name_key] = r["row"]

    def _existing_catalogue(self) -> dict[str, Any]:
        urls, ids = {}, set()
        for item in self.catalogue.list(limit=100000)["items"]:
            ids.add(item["id"])
            dest = item["destination"]
            for candidate in (dest.get("base_url"), (dest.get("extra") or {}).get("submit_url")):
                norm, _ = normalize_url(candidate or "")
                if norm:
                    urls[dedupe_key(norm)] = item["id"]
        return {"urls": urls, "ids": ids}

    # ---------- step 4-5: reachability and enrichment ----------
    def _check_site(self, r: dict[str, Any]) -> None:
        url = r["normalized_url"]
        host = urlsplit(url).hostname or ""
        if not self.allow_private_hosts and is_private_host(host):
            r["status"] = "invalid"
            r["reasons"].append("host is a private or local address; the service will not fetch it")
            return
        with self._host_lock(host):  # one request per host at a time: polite, and it cannot hammer a small forum
            last_error = ""
            for attempt in (1, 2):
                t0 = self.clock()
                try:
                    status, final_url, body = self._fetch(url)
                except httpx.TimeoutException:
                    last_error = "timed out"
                    continue
                except httpx.HTTPError as exc:
                    last_error = f"connection failed ({type(exc).__name__})"
                    continue
                r["http_status"], r["final_url"], r["response_ms"] = status, final_url, int((self.clock() - t0) * 1000)
                break
            else:
                r["status"] = "unreachable"
                r["reasons"].append(last_error)
                return
        if status in (401, 403, 429):
            r["warnings"].append(f"site answered HTTP {status} (blocks automated visits); kept, details could not be read")
        elif status >= 400:
            r["status"] = "unreachable"
            r["reasons"].append(f"site answered HTTP {status}")
            return
        info = parse_page(body)
        r["title"] = info["title"]
        r["detected_language"], _ = normalize_language(info["lang"])
        r["detected_platform"] = detect_platform(host, info["generator"])
        if r["language"] and r["detected_language"] and r["language"] != r["detected_language"]:
            r["warnings"].append(f"language in the file is {r['language']!r} but the page says {r['detected_language']!r}")
        if not r["language"]:
            r["language"] = r["detected_language"]
        raw_connector = r["_raw"].get("connector_key")
        r["connector_key"] = raw_connector or PLATFORM_CONNECTORS.get(r["detected_platform"] or "", "assisted")

    def _fetch(self, url: str) -> tuple[int, str, str]:
        with self.http.stream("GET", url, timeout=self.fetch_timeout_s, follow_redirects=True) as resp:
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_PAGE_BYTES:
                    break
            return resp.status_code, str(resp.url), b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")

    def _host_lock(self, host: str) -> threading.Lock:
        with self._locks_guard:
            return self._host_locks[host]

    # ---------- step 6: the catalogue ----------
    def _add_to_catalogue(self, r: dict[str, Any], by_row: dict[int, dict[str, Any]]) -> int:
        raw = r["_raw"]
        origin = urlsplit(r["normalized_url"])
        base = f"{origin.scheme}://{origin.netloc}"
        key = r["connector_key"]
        extra: dict[str, Any] = {"source": "csv_import", "rules_status": "default_conservative_unconfirmed", "site_title": r["title"]}
        if key == "assisted":
            extra["submit_url"] = r["normalized_url"]
        destination = Destination(name=raw["name"], connector_key=key, base_url=None if key == "assisted" else base,
                                  community=raw.get("community") or None, rules=dict(CONSERVATIVE_RULES), extra=extra)
        topics = [t.strip() for t in re.split(r"[;|]", raw.get("topics", "")) if t.strip()]
        row, created = self.catalogue.upsert(destination, language=r["language"], topics=topics)
        r["destination_id"] = row["id"]
        return 1 if created else 0


def report_to_csv(report: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=REPORT_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in report:
        writer.writerow({**r, "reasons": "; ".join(r["reasons"]), "warnings": "; ".join(r["warnings"])})
    return out.getvalue()


def public_report(report: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in report]


def import_id_for(csv_text: str) -> str:
    return hashlib.sha256(csv_text.encode("utf-8")).hexdigest()[:12]
