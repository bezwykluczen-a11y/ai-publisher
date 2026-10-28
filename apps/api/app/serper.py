import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


ENDPOINT = "https://google.serper.dev/search"


def canonical_key(url: str) -> str | None:
    try:
        parts = urllib.parse.urlsplit(url.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None

        query = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(
                parts.query, keep_blank_values=True
            )
            if not key.lower().startswith("utm_")
            and key.lower() not in {"gclid", "fbclid", "srsltid"}
        ]

        return urllib.parse.urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            urllib.parse.urlencode(sorted(query)),
            "",
        ))
    except ValueError:
        return None


def search_page(
    query: str,
    country: str,
    language: str,
    page: int,
) -> dict:
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is missing")

    payload = json.dumps({
        "q": query,
        "gl": country,
        "hl": language,
        "num": 10,
        "page": page,
    }).encode("utf-8")

    request = urllib.request.Request(
        ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Serper returned HTTP {exc.code}; collection stopped"
        ) from None
    except urllib.error.URLError:
        raise RuntimeError(
            "Could not connect to Serper; collection stopped"
        ) from None
    except TimeoutError:
        raise RuntimeError("Serper request timed out") from None

    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("Serper returned invalid JSON") from None

    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Serper response")
    if data.get("error") or data.get("message") or "organic" not in data:
        raise RuntimeError(
            "Serper response did not contain expected organic results"
        )
    if not isinstance(data["organic"], list):
        raise RuntimeError("Invalid organic results format")

    return data


def collect_results(
    query: str,
    country: str = "pl",
    language: str = "pl",
    target: int = 50,
    max_pages: int = 10,
) -> dict:
    query = query.strip()
    if not query:
        raise ValueError("Query cannot be empty")
    if not 1 <= target <= 100:
        raise ValueError("Target must be between 1 and 100")
    if not 1 <= max_pages <= 10:
        raise ValueError("Max pages must be between 1 and 10")

    report = {
        "provider": "serper",
        "query": query,
        "country": country,
        "language": language,
        "requested_unique": target,
        "results_per_page_requested": 10,
        "max_pages": max_pages,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "requests_attempted": 0,
        "pages_received": 0,
        "raw_organic_count": 0,
        "duplicates_skipped": 0,
        "results": [],
        "error": None,
    }
    seen = set()
    stop_reason = "page_limit"

    for page in range(1, max_pages + 1):
        report["requests_attempted"] += 1
        try:
            data = search_page(query, country, language, page)
        except RuntimeError as exc:
            report["error"] = str(exc)
            stop_reason = "error"
            break

        report["pages_received"] += 1
        organic = data["organic"]
        report["raw_organic_count"] += len(organic)

        if not organic:
            stop_reason = "empty_page"
            break

        for index, item in enumerate(organic, start=1):
            if not isinstance(item, dict):
                continue

            url = item.get("link")
            if not isinstance(url, str):
                continue

            key = canonical_key(url)
            if key is None:
                continue
            if key in seen:
                report["duplicates_skipped"] += 1
                continue

            seen.add(key)
            report["results"].append({
                "collection_order": len(report["results"]) + 1,
                "serp_page": page,
                "page_item_index": index,
                "provider_position": item.get("position"),
                "title": item.get("title", ""),
                "url": url,
                "deduplication_key": key,
                "snippet": item.get("snippet", ""),
            })

            if len(report["results"]) >= target:
                break

        if len(report["results"]) >= target:
            stop_reason = "target_reached"
            break

    report["unique_count"] = len(report["results"])
    report["target_reached"] = report["unique_count"] >= target
    report["stop_reason"] = stop_reason
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--country", default="pl")
    parser.add_argument("--language", default="pl")
    parser.add_argument("--target", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=10)
    args = parser.parse_args()

    report = collect_results(
        query=args.query,
        country=args.country,
        language=args.language,
        target=args.target,
        max_pages=args.max_pages,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["target_reached"] else 2

if __name__ == "__main__":
    sys.exit(main())