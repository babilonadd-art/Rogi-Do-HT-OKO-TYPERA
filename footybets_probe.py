#!/usr/bin/env python3
"""Read-only FootyBets access probe. Does not send Telegram messages or place bets."""
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://footybets.io"
TARGETS = [
    f"{BASE}/predictions/today/corners/",
    f"{BASE}/predictions/today/",
    BASE + "/",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OkoTyperaResearch/1.0; +https://github.com/babilonadd-art/Rogi-Do-HT-OKO-TYPERA)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MARKET_RE = re.compile(r"\b(over|under)\s*\d+(?:[.,]\d+)?\s*corners?\b", re.I)
CONF_RE = re.compile(r"\bconfidence\s*[: ]\s*\d{1,3}%?", re.I)
ODDS_RE = re.compile(r"\bodds\s*[: ]\s*\d+(?:[.,]\d+)?", re.I)


def text_of(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def extract_html(url, report):
    try:
        response = requests.get(url, headers=HEADERS, timeout=25)
        item = {
            "url": url,
            "status": response.status_code,
            "final_url": response.url,
            "content_type": response.headers.get("content-type", ""),
            "bytes": len(response.content),
        }
        report["http_pages"].append(item)
        if response.status_code != 200 or "html" not in item["content_type"].lower():
            return
        soup = BeautifulSoup(response.text, "html.parser")
        item["title"] = soup.title.get_text(" ", strip=True) if soup.title else ""
        item["page_text_chars"] = len(text_of(soup))
        item["corner_market_mentions"] = len(MARKET_RE.findall(text_of(soup)))
        item["confidence_mentions"] = len(CONF_RE.findall(text_of(soup)))
        item["odds_mentions"] = len(ODDS_RE.findall(text_of(soup)))
        item["scripts"] = [
            s.get("src") for s in soup.find_all("script") if s.get("src")
        ][:80]
        item["api_hints"] = sorted(set(
            re.findall(r"""(?:https?://[^"'\s<>]+|/[^"'\s<>]*(?:api|graphql|wp-json)[^"'\s<>]*)""",
                       response.text, re.I)
        ))[:80]
        item["market_contexts"] = []
        for node in soup.find_all(["article", "li", "section", "div"]):
            value = text_of(node)
            if not value or len(value) > 1800 or not MARKET_RE.search(value):
                continue
            if not (CONF_RE.search(value) or ODDS_RE.search(value)):
                continue
            # Prefer compact card containers; deduplicate nested copies.
            if len(value) >= 100:
                item["market_contexts"].append(value[:900])
        item["market_contexts"] = list(dict.fromkeys(item["market_contexts"]))[:20]
        item["candidate_dom_nodes"] = []
        seen_nodes = set()
        for node in soup.find_all(["article", "li", "section", "div"]):
            value = text_of(node)
            if not value or len(value) > 900:
                continue
            if not MARKET_RE.search(value) or not re.search(r"\\bodds\\s*[: ]\\s*\\d", value, re.I):
                continue
            key = (node.name, str(node.get("class")), value)
            if key in seen_nodes:
                continue
            seen_nodes.add(key)
            parent = node.parent
            item["candidate_dom_nodes"].append({
                "tag": node.name,
                "class": node.get("class"),
                "id": node.get("id"),
                "parent_tag": parent.name if parent else None,
                "parent_class": parent.get("class") if parent else None,
                "text": value[:900],
                "html": str(node)[:2500],
            })
        item["candidate_dom_nodes"].sort(key=lambda x: len(x["text"]))
        item["candidate_dom_nodes"] = item["candidate_dom_nodes"][:12]
    except Exception as exc:
        report["http_errors"].append({"url": url, "error": f"{type(exc).__name__}: {exc}"})


async def browser_probe(report):
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        report["browser"] = {"available": False, "reason": str(exc)}
        return

    browser_result = {
        "available": True,
        "pages": [],
        "api_responses": [],
        "errors": [],
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(user_agent=HEADERS["User-Agent"])
            page.set_default_timeout(15000)
            for url in TARGETS[:2]:
                page_info = {"url": url}
                page.on("response", lambda response: None)
                captured = []

                def on_response(response):
                    try:
                        parsed = urlparse(response.url)
                        if parsed.netloc.endswith("footybets.io") and (
                            response.request.resource_type in ("xhr", "fetch")
                            or "json" in response.headers.get("content-type", "").lower()
                        ):
                            captured.append({
                                "url": response.url[:500],
                                "status": response.status,
                                "resource_type": response.request.resource_type,
                                "content_type": response.headers.get("content-type", ""),
                            })
                    except Exception:
                        pass

                page.on("response", on_response)
                try:
                    nav = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(9000)
                    rendered = await page.locator("body").inner_text(timeout=10000)
                    markets = MARKET_RE.findall(rendered)
                    page_info.update({
                        "status": nav.status if nav else None,
                        "final_url": page.url,
                        "rendered_text_chars": len(rendered),
                        "corner_market_mentions": len(markets),
                        "confidence_mentions": len(CONF_RE.findall(rendered)),
                        "odds_mentions": len(ODDS_RE.findall(rendered)),
                        "market_contexts": [],
                    })
                    lines = [re.sub(r"\s+", " ", line).strip() for line in rendered.splitlines()]
                    for i, line in enumerate(lines):
                        if MARKET_RE.search(line):
                            context = " | ".join(x for x in lines[max(0, i-5):min(len(lines), i+8)] if x)
                            page_info["market_contexts"].append(context[:1000])
                    page_info["market_contexts"] = list(dict.fromkeys(page_info["market_contexts"]))[:20]
                except Exception as exc:
                    page_info["error"] = f"{type(exc).__name__}: {exc}"
                browser_result["pages"].append(page_info)
                browser_result["api_responses"].extend(captured)
                page.remove_listener("response", on_response)
        finally:
            await browser.close()
    # Keep only unique, non-sensitive request metadata (no headers/cookies/body).
    unique = {}
    for item in browser_result["api_responses"]:
        unique[(item["url"], item["status"])] = item
    browser_result["api_responses"] = list(unique.values())[:100]
    report["browser"] = browser_result


def main():
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": BASE,
        "purpose": "Read-only compatibility test; no Telegram, no account, no betting actions.",
        "http_pages": [],
        "http_errors": [],
    }
    for url in TARGETS:
        extract_html(url, report)
    asyncio.run(browser_probe(report))

    static_hits = sum(x.get("corner_market_mentions", 0) for x in report["http_pages"])
    rendered_hits = sum(x.get("corner_market_mentions", 0) for x in report.get("browser", {}).get("pages", []))
    api_hits = len(report.get("browser", {}).get("api_responses", []))
    report["summary"] = {
        "static_html_contains_corner_markets": static_hits > 0,
        "javascript_rendered_contains_corner_markets": rendered_hits > 0,
        "same_site_xhr_or_fetch_observed": api_hits > 0,
        "integration_assessment": (
            "POTENTIAL_DATA_PATH_FOUND" if (static_hits or rendered_hits or api_hits)
            else "NO_PUBLIC_DATA_PATH_CONFIRMED"
        ),
        "important": "A successful page load is not proof of stable or permitted automated reuse. Review site terms and repeat the test on multiple days before production use.",
    }

    Path("footybets_probe_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("\nHTTP pages:")
    for page in report["http_pages"]:
        print(f"- {page['status']} {page['url']} | markets={page.get('corner_market_mentions', 0)} | {page.get('title', '')}")
    for page in report.get("browser", {}).get("pages", []):
        print(f"- BROWSER {page.get('status')} {page['url']} | markets={page.get('corner_market_mentions', 0)} | xhr/fetch={len(report['browser'].get('api_responses', []))}")
    print("\nDetailed report saved to footybets_probe_report.json")
    # Fail only on total access failure; no market data is a valid diagnostic result.
    if not any(p.get("status") == 200 for p in report["http_pages"]) and not any(
        p.get("status") == 200 for p in report.get("browser", {}).get("pages", [])
    ):
        sys.exit(2)


if __name__ == "__main__":
    main()
