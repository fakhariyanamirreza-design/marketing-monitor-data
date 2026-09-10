#!/usr/bin/env python3
"""Marketing monitor agent: scans free sources for keywords and reports results."""

import json
import os
import sys
import hashlib
import time
import datetime
import argparse
import re
import html
import subprocess
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote_plus
from email.utils import parsedate_to_datetime

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
STATE = HERE / "state.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def load_config():
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if STATE.exists():
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen": []}


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch(url, timeout=30, headers=None):
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def item_id(source, url, title, published):
    key = f"{source}|{url}|{title[:80]}|{published}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# --------------------------- sources ---------------------------

def scan_google_news(cfg, query):
    """Google News RSS search. Free, no API key."""
    base = cfg["sources"]["google_news"]["base"]
    url = f"{base}?q={quote_plus(query)}&hl=en"
    try:
        raw = fetch(url)
    except Exception as e:
        print(f"[google_news] fetch error for '{query}': {e}")
        return []

    items = []
    for m in re.finditer(r"<item>(.*?)</item>", raw, re.S):
        block = m.group(1)
        title = re.search(r"<title>(.*?)</title>", block, re.S)
        link = re.search(r"<link>(.*?)</link>", block, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
        desc = re.search(r"<description>(.*?)</description>", block, re.S)
        if not title or not link:
            continue
        t = html.unescape(title.group(1)).strip()
        l = html.unescape(link.group(1)).strip()
        p = pub.group(1).strip() if pub else ""
        d = html.unescape(re.sub(r"<[^>]+>", "", desc.group(1))).strip() if desc else ""
        items.append({
            "id": item_id("news", l, t, p),
            "platform": "news",
            "source": "google_news",
            "title": t,
            "url": l,
            "snippet": d[:300],
            "published": p,
            "query": query,
        })
    return items


def scan_telegram_channel(username, query):
    """Read a public Telegram channel via t.me/s/UNAME. Free, no login."""
    url = f"https://t.me/s/{username}"
    try:
        raw = fetch(url)
    except Exception as e:
        print(f"[telegram] fetch error for {username}: {e}")
        return []

    items = []
    for m in re.finditer(
        r'<div class="tgme_widget_message[^"]*"[^>]*data-post="([^"]+)"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_date_time">',
        raw, re.S,
    ):
        post_id, body = m.group(1), m.group(2)
        texts = re.findall(r"<div class=\"tgme_widget_message_text[^\"]*\">(.*?)</div>", body, re.S)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", "", tp)).strip() for tp in texts)
        if query.lower() in text.lower():
            link = f"https://t.me/{post_id}"
            items.append({
                "id": item_id("telegram", link, text[:80], post_id),
                "platform": "telegram",
                "source": username,
                "title": text[:140],
                "url": link,
                "snippet": text[:400],
                "published": post_id,
                "query": query,
            })
    return items


def scan_web(cfg, query):
    """Best-effort web search for X/IG/LinkedIn via a free no-key engine."""
    sites = cfg["sources"]["web_search"]["sites"]
    items = []
    for site in sites:
        q = f'site:{site} "{query}"'.replace('"', "%22")
        url = f"https://www.google.com/search?q={quote_plus(q)}&num=20"
        try:
            raw = fetch(url)
        except Exception as e:
            print(f"[web] fetch error {site}: {e}")
            continue
        for m in re.finditer(
            r'<a href="/url\?q=([^&]+)&amp;sa=U[^"]*"[^>]*>.*?<h3[^>]*>(.*?)</h3>',
            raw, re.S,
        ):
            link = html.unescape(m.group(1))
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            items.append({
                "id": item_id("web", link, title, ""),
                "platform": site,
                "source": "web_search",
                "title": title[:200],
                "url": link,
                "snippet": "",
                "published": "",
                "query": query,
            })
    return items


# --------------------------- engine ---------------------------

def run_cycle(cfg, state, now, send_report=None):
    seen = set(state["seen"])
    results = []

    for kw in cfg["keywords"]:
        label = kw["label"]
        for query in kw["queries"]:
            print(f"[scan] {label}: '{query}'")
            if cfg["sources"]["google_news"]["enabled"]:
                for it in scan_google_news(cfg, query):
                    if it["id"] not in seen:
                        results.append(it)
            if cfg["sources"]["web_search"]["enabled"]:
                for it in scan_web(cfg, query):
                    if it["id"] not in seen:
                        results.append(it)
        if cfg["sources"]["telegram_channels"]["enabled"]:
            for ch in cfg["sources"]["telegram_channels"]["channels"]:
                for it in scan_telegram_channel(ch, kw["queries"][0]):
                    if it["id"] not in seen:
                        results.append(it)

    state["seen"].extend(it["id"] for it in results)
    state["seen"] = state["seen"][-2000:]
    save_state(state)

    report = build_report(cfg, results, now)
    if results:
        write_data_files(cfg, report, now)
    if send_report is None:
        send_report = cfg["telegram"]["send_report"] and bool(
            os.environ.get("TELEGRAM_CHAT_ID", cfg["telegram"]["chat_id"])
        )
    if send_report:
        send_telegram(cfg, report)
    print(f"[done] {len(results)} new items")
    return report


def build_report(cfg, results, now):
    lines = []
    lines.append(f"# Marketing Monitor — {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    if not results:
        lines.append("No new mentions found.")
    for kw in cfg["keywords"]:
        label = kw["label"]
        kw_items = [it for it in results if it["query"] in kw["queries"]]
        if not kw_items:
            continue
        lines.append(f"## {label}")
        for it in kw_items[:15]:
            platform = it.get("platform", "")
            lines.append(f"- [{it['title']}]({it['url']}) _{platform}_ — {it['snippet'][:120]}")
        lines.append("")
    return "\n".join(lines)


# --------------------------- delivery ---------------------------

def send_telegram(cfg, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", cfg["telegram"]["bot_token"])
    chat = os.environ.get("TELEGRAM_CHAT_ID", cfg["telegram"]["chat_id"])
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({
        "chat_id": chat,
        "text": text[:4000],
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    try:
        with urlopen(req, timeout=60) as resp:
            resp.read()
        print("[telegram] report sent")
    except Exception as e:
        print(f"[telegram] send error: {e}")


def write_data_files(cfg, report, now):
    d = HERE / cfg["git"]["data_dir"]
    d.mkdir(exist_ok=True)
    day = now.strftime("%Y-%m")
    path = d / f"{day}.md"
    with open(path, "a", encoding="utf-8") as f:
        f.write(report + "\n\n")


def git_push(cfg):
    try:
        subprocess.run(["git", "-C", str(HERE), "config", "user.email", os.environ.get("GIT_EMAIL", "agent@users.noreply.github.com")], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(HERE), "config", "user.name", os.environ.get("GIT_USER", "marketing-monitor")], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(HERE), "add", "-A"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(HERE), "commit", "-m", f"{datetime.datetime.utcnow().isoformat()} report"],
            check=True, capture_output=True,
        )
        remote = cfg["git"]["repo_url"]
        if os.environ.get("CI", ""):
            r = subprocess.run(["git", "-C", str(HERE), "remote", "set-url", "origin", remote], capture_output=True)
        subprocess.run(["git", "-C", str(HERE), "push", "origin", "HEAD"], check=True, capture_output=True)
        print("[git] pushed")
    except subprocess.CalledProcessError as e:
        print(f"[git] error: {e.stderr.decode(errors='replace')[:500]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run a single cycle and exit")
    ap.add_argument("--send-report", action="store_true", help="always send to Telegram")
    ap.add_argument("--no-report-flag", action="store_true", dest="no_report_flag", help="send if env present")
    args = ap.parse_args()

    cfg = load_config()
    state = load_state()

    if args.once:
        run_cycle(cfg, state, datetime.datetime.now(), send_report=args.send_report)
        return

    while True:
        run_cycle(cfg, state, datetime.datetime.now())
        time.sleep(cfg["interval_hours"] * 3600)


if __name__ == "__main__":
    main()