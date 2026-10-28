import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import trafilatura


USER_AGENT = (
    "Mozilla/5.0 (compatible; AIPublisherResearch/1.0; "
    "+https://example.com/contact)"
)
REQUEST_TIMEOUT = 25
DELAY_SECONDS = 2.0
MAX_CONTENT_CHARS = 80000


def safe_filename(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in "-_." else "-"
        for char in value.lower()
    )
    return cleaned.strip("-")[:80] or "page"


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def load_report(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        report = json.load(file)

    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise ValueError("Invalid Serper report file")

    return report


def fetch_html(url: str) -> tuple[bytes, int]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl,en;q=0.8",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read(), response.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}") from None
    except TimeoutError:
        raise RuntimeError("Request timed out") from None


def decode_html(html: bytes) -> str:
    try:
        return html.decode("utf-8")
    except UnicodeDecodeError:
        pass

    try:
        return html.decode("windows-1252")
    except UnicodeDecodeError:
        return html.decode("utf-8", errors="replace")


def extract_markdown(html: bytes, url: str) -> str | None:
    decoded = decode_html(html)

    extracted = trafilatura.extract(
        decoded,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_links=False,
        include_images=False,
        with_metadata=False,
    )

    if not extracted:
        return None

    markdown = extracted.strip()

    if not markdown:
        return None

    return markdown[:MAX_CONTENT_CHARS]


def write_markdown(path: Path, markdown: str) -> None:
    with open(path, "wb") as markdown_file:
        markdown_file.write(markdown.encode("utf-8"))


def fetch_selected(
    report_path: str,
    output_dir: str,
    limit: int = 10,
    delay: float = DELAY_SECONDS,
) -> dict:
    report = load_report(report_path)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    result = {
        "report_path": report_path,
        "provider": "serper",
        "query": report.get("query"),
        "requested": limit,
        "attempted": 0,
        "saved": 0,
        "failed": 0,
        "items": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    selected = report["results"][:limit]

    for item in selected:
        url = item.get("url", "")
        page_hash = url_hash(url)
        markdown_path = output_path / f"{page_hash}.md"
        status = "ok"
        error = None
        content_chars = 0

        if markdown_path.exists() and markdown_path.stat().st_size > 0:
            status = "already_saved"
        else:
            result["attempted"] += 1

            try:
                html, http_status = fetch_html(url)
                markdown = extract_markdown(html, url)

                if not markdown:
                    status = "no_content"
                    error = "Main content could not be extracted"
                else:
                    write_markdown(markdown_path, markdown)
                    content_chars = len(markdown)
                    result["saved"] += 1

            except RuntimeError as exc:
                status = "error"
                error = str(exc)
                result["failed"] += 1

            if result["attempted"] < len(selected):
                time.sleep(delay)

        result["items"].append({
            "collection_order": item.get("collection_order"),
            "serp_page": item.get("serp_page"),
            "title": item.get("title", ""),
            "url": url,
            "url_hash": page_hash,
            "markdown_path": str(markdown_path),
            "status": status,
            "content_chars": content_chars,
            "error": error,
        })

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS)
    args = parser.parse_args()

    result = fetch_selected(
        report_path=args.report,
        output_dir=args.output_dir,
        limit=args.limit,
        delay=args.delay,
    )

    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
