import html
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

import psycopg


DATABASE_URL = os.getenv("DATABASE_URL")

FETCH_TIMEOUT = int(os.getenv("PAGE_FETCH_TIMEOUT", "20"))
FETCH_DELAY_SECONDS = float(os.getenv("PAGE_FETCH_DELAY_SECONDS", "1"))
MAX_PAGE_CHARS = int(os.getenv("PAGE_MAX_CHARS", "20000"))
USER_AGENT = "AIPublisherResearchBot/1.0 (+research content preview)"

BLOCK_TAGS = {
    "script", "style", "noscript", "svg", "iframe", "form",
    "nav", "header", "footer", "aside",
}


def _strip_html(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_html, flags=re.I | re.S)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _remove_blocks(raw_html: str) -> str:
    cleaned = raw_html

    for tag in BLOCK_TAGS:
        pattern = re.compile(
            rf"<{tag}\b.*?</{tag}>",
            flags=re.I | re.S,
        )
        cleaned = pattern.sub(" ", cleaned)

    return cleaned


def fetch_page_text(url: str) -> str:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are supported")

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            content_type = response.headers.get("Content-Type", "")

            if (
                "text/html" not in content_type
                and "application/xhtml" not in content_type
            ):
                raise ValueError(f"Unsupported content type: {content_type}")

            raw = response.read(MAX_PAGE_CHARS * 4).decode(
                response.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection error: {exc.reason}") from exc

    cleaned = _remove_blocks(raw)
    text = _strip_html(cleaned)

    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS].rsplit(" ", 1)[0]

    if len(text) < 200:
        raise RuntimeError("Page text is too short")

    return text


def fetch_research_pages(research_id: int, limit: int = 10):
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT report
                FROM research_runs
                WHERE id = %s AND status = 'completed'
            """, (research_id,))
            row = cur.fetchone()

            if not row:
                raise RuntimeError(
                    f"Research #{research_id} was not found or is not completed"
                )

            report = row[0] or {}
            results = report.get("results", [])

            if not results:
                raise RuntimeError("Research report has no SERP results")

            selected = results[:limit]

            cur.execute("""
                CREATE TABLE IF NOT EXISTS research_pages (
                    id SERIAL PRIMARY KEY,
                    research_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content_text TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (research_id, url)
                );
                CREATE INDEX IF NOT EXISTS research_pages_research_idx
                    ON research_pages(research_id);
            """)

        conn.commit()

    fetched = 0
    failed = 0

    for item in selected:
        url = item.get("url")
        title = item.get("title") or url

        if not url:
            failed += 1
            continue

        try:
            content = fetch_page_text(url)
        except Exception:
            failed += 1
            time.sleep(FETCH_DELAY_SECONDS)
            continue

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO research_pages
                        (research_id, url, title, content_text, char_count)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (research_id, url) DO UPDATE
                    SET title = EXCLUDED.title,
                        content_text = EXCLUDED.content_text,
                        char_count = EXCLUDED.char_count,
                        created_at = NOW()
                """, (research_id, url, title, content, len(content)))
            conn.commit()

        fetched += 1
        time.sleep(FETCH_DELAY_SECONDS)

    return {
        "research_id": research_id,
        "requested": len(selected),
        "fetched": fetched,
        "failed": failed,
    }