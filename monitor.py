#!/usr/bin/env python3
"""Marketing Intelligence monitor: scores news by relevance to Rasmio.

Reads config.json (sources/telegram/git) and rules.json (categories,
weights, scoring params, priority thresholds). Every fetched item is
classified into categories and given a Relevance Score (0-100) and a
Priority. Output is ranked by priority for Telegram and stored as
Markdown + JSONL in the git data dir.
"""

import json
import os
import time
import datetime
import argparse
import re
import html
import hashlib
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote_plus

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
RULES = HERE / "rules.json"
STATE = HERE / "data" / "seen.json"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def fetch(url, timeout=30, headers=None):
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def item_id(source, url, title, published):
    key = f"{source}|{url}|{title[:120]}|{published}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def priority_for(score, prio):
    for name, p in prio.items():
        if score >= p["min"]:
            return name, p["label_fa"]
    return "low", prio["low"]["label_fa"]


def _escape_html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _clean_title(title):
    """Strip emojis, hashtags, channel handles and URLs from a raw title."""
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF\uFE0F]", "", str(title))
    t = re.sub(r"#[^\s#]+", "", t)
    t = re.sub(r"@[\w_а-яа-я]+", "", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.strip(" |:.•■🔸–—-_*")
    return t[:120] or str(title)[:120]


def _source_label(platform, source):
    """Persian label for where the item came from."""
    if platform == "telegram":
        return "تلگرام"
    if platform == "eitaa":
        return "ایتا"
    if source == "google_news":
        return "گوگل نیوز"
    host = str(source)
    if "irna.ir" in host:
        return "ایرنا"
    if "isna.ir" in host:
        return "ایسنا"
    if "mehrnews" in host:
        return "مهر"
    if "khabaronline" in host:
        return "خبرآنلاین"
    if "entekhab" in host:
        return "انتخاب"
    if "tasnimnews" in host:
        return "تسنیم"
    if "donya-e-eqtesad" in host:
        return "دنیای اقتصاد"
    if "farsnews" in host:
        return "فارس"
    if "ilna" in host:
        return "ایلنا"
    if "pana.ir" in host:
        return "پانا"
    return "خبرگزاری"


def _is_persian(text):
    """True if text contains at least one Persian letter."""
    return bool(re.search(r"[\u0600-\u06FF]", text))


def _dedup_titles(items):
    """Remove duplicate items by near-identical titles."""
    seen_titles = set()
    out = []
    for it in items:
        key = re.sub(r"[\s\u200c\u200d]+", " ", it["title"][:100].strip().lower())
        if key in seen_titles:
            continue
        seen_titles.add(key)
        out.append(it)
    return out


# --------------------------- sources ---------------------------

def scan_googlenews_rss(cfg, query, lang="en", geo="IR"):
    base = cfg["sources"]["google_news"]["base"]
    url = f"{base}?q={quote_plus(query)}&hl={lang}&gl={geo}&ceid={geo.lower()}:{lang}"
    try:
        raw = fetch(url)
    except Exception as e:
        print(f"[news] fetch error: {e}")
        return []
    return parse_rss_items(raw, platform="news", source="google_news")


def _strip_cdata(s):
    """Remove <![CDATA[ ... ]]> wrappers, if present."""
    if s.startswith("<![CDATA["):
        s = s[9:-3]
    return s


def parse_rss_items(raw, platform, source):
    """Generic parser for any RSS/Atom feed."""
    items = []
    for m in re.finditer(r"<item>(.*?)</item>", raw, re.S):
        block = m.group(1)
        title = re.search(r"<title>(.*?)</title>", block, re.S)
        link = re.search(r"<link>(.*?)</link>", block, re.S)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", block, re.S)
        desc = re.search(r"<description>(.*?)</description>", block, re.S)
        if not title or not link:
            continue
        t = _strip_cdata(html.unescape(title.group(1))).strip()
        l = _strip_cdata(html.unescape(link.group(1))).strip()
        p = _strip_cdata(pub.group(1)).strip() if pub else ""
        d = _strip_cdata(html.unescape(re.sub(r"<[^>]+>", "", desc.group(1)))).strip() if desc else ""
        items.append({
            "id": item_id(source, l, t, p),
            "platform": platform,
            "source": source,
            "title": t,
            "url": l,
            "snippet": d[:500],
            "published": p,
        })
    return items


def scan_iranian_rss(feed):
    """Fetch a Persian news agency RSS feed."""
    try:
        raw = fetch(feed)
    except Exception as e:
        print(f"[ir_rss] fetch error {feed}: {e}")
        return []
    return parse_rss_items(raw, platform="news", source=feed)


def scan_telegram_channel(username):
    """Scan a public Telegram channel page (t.me/s/...)."""
    url = f"https://t.me/s/{username}"
    regex = r'data-post="([^"]+)".*?<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>'
    try:
        raw = fetch(url)
    except Exception as e:
        print(f"[telegram] fetch error for {username}: {e}")
        return []
    items = []
    for m in re.finditer(regex, raw, re.S):
        post_id, body = m.group(1), m.group(2)
        text = html.unescape(re.sub(r"<br\s*/?>", " ", re.sub(r"<[^>]+>", "", body))).strip()
        if not text:
            continue
        link = f"https://t.me/{post_id}"
        items.append({
            "id": item_id("telegram", link, text[:120], post_id),
            "platform": "telegram",
            "source": username,
            "title": text[:160],
            "url": link,
            "snippet": text[:600],
            "published": post_id,
        })
    return items


# --------------------------- scoring ---------------------------

def match_keywords(text, category):
    """Return list of (kw_text, weight, in_title) matches for a category."""
    lower = (text or "").lower()
    title_lower = (text or "").lower()
    # search both title and body: caller passes joined text
    hits = []
    for kw in category["keywords"]:
        kwt = kw["text"].lower()
        if kwt in lower:
            hits.append((kw["text"], kw["weight"], False))
    return hits


def score_item(item, rules):
    """Classify an item into categories and compute relevance score."""
    sc = rules["scoring"]
    text = f"{item['title']} {item['snippet']}".lower()
    title = item["title"].lower()

    matched = {}   # category_id -> {strength, hits, labels}
    for cat in rules["categories"]:
        hits = []
        strength = 0.0
        for kw in cat["keywords"]:
            kwt = kw["text"].lower()
            in_title = kwt in title
            in_body = kwt in text
            if not (in_title or in_body):
                continue
            mult = sc.get("title_multiplier", 2.0) if in_title else 1.0
            hits.append((kw["text"], kw["weight"], in_title))
            strength += kw["weight"] * mult
        if hits:
            matched[cat["id"]] = {
                "label": cat["label"],
                "label_fa": cat["label_fa"],
                "type": cat["type"],
                "weight": cat["weight"],
                "cap": cat.get("cap", 100),
                "is_brand": cat.get("is_brand", False),
                "strength": strength,
                "hits": hits,
            }

    if not matched:
        return None

    # primary category = strongest keyword match
    best = max(matched.values(), key=lambda m: (m["strength"], m["weight"]))
    secondary = [m for cid, m in matched.items() if m is not best]

    # strength normalised against the primary category's own keyword hits
    str_norm = min(1.0, best["strength"] / sc.get("relevance_divisor", 2.0))
    # category weight of the primary category drives the score
    cat_norm = min(1.0, best["weight"] / sc.get("coverage_divisor", 0.30))
    # secondary categories add a small bonus only
    secondary_bonus = 0.03 * len(secondary)

    # Persian language boost
    persian_boost = 8.0 if _is_persian(item.get("title", "")) else 0.0

    score = 100 * (0.6 * str_norm + 0.4 * cat_norm + secondary_bonus) + persian_boost
    brand_matched = best["is_brand"]
    if brand_matched:
        score += sc.get("brand_bonus", 20)
        score = max(score, sc.get("brand_floor", 80))
    else:
        # cap by the primary category so trend/competitor news never reads as critical
        score = min(score, best.get("cap", 100))
    score = round(min(100.0, score))

    prio, prio_fa = priority_for(score, rules["priorities"])

    # reasons
    reasons = []
    for cat_id, m in matched.items():
        reason_hits = ", ".join(f"«{h[0]}»" + (" (عنوان)" if h[2] else "") for h in m["hits"][:3])
        reasons.append(f"[{m['label_fa']} {int(m['weight']*100)}%] {reason_hits}")
    if brand_matched:
        reasons.append("ارتباط مستقیم با رسمیو → امتیاز اضافه")

    return {
        "category": best["label"],
        "category_id": next(k for k, v in matched.items() if v is best),
        "categories": sorted(matched.keys()),
        "type": best["type"],
        "type_fa": best["label_fa"],
        "score": score,
        "priority": prio,
        "priority_fa": prio_fa,
        "reasons": reasons,
    }


# --------------------------- engine ---------------------------

def category_query(rules, cat):
    terms = " OR ".join(f'"{t}"' for t in cat["search"])
    return f"({terms})"


def category_queries(cat):
    """Return one query per search term (a quoted phrase, insensitive to OR)."""
    qs = []
    for t in cat["search"]:
        t = t.strip()
        if not t:
            continue
        if " OR " in t:
            qs.append("(" + t + ")")
        else:
            qs.append(f'"{t}"')
    return qs or [f'"{cat["search"][0]}"']


def fetch_all(cfg, rules, seen):
    raw = []
    gn = cfg["sources"]["google_news"]
    lang = gn.get("lang", "en")
    geo = gn.get("geo", "IR")

    for cat in rules["categories"]:
        for q in category_queries(cat):
            print(f"[scan] {cat['label']} :: {q}")
            raw.extend(scan_googlenews_rss(cfg, q, lang=lang, geo=geo))
            time.sleep(0.3)

    if cfg["sources"]["telegram_channels"]["enabled"]:
        for ch in cfg["sources"]["telegram_channels"]["channels"]:
            print(f"[scan] telegram::{ch}")
            raw.extend(scan_telegram_channel(ch))
            time.sleep(0.3)

    if cfg["sources"]["iranian_rss"]["enabled"]:
        for feed in cfg["sources"]["iranian_rss"]["feeds"]:
            print(f"[scan] ir_rss::{feed}")
            raw.extend(scan_iranian_rss(feed))
            time.sleep(0.3)

    raw = _dedup_titles(raw)

    results = []
    min_score = rules["scoring"].get("min_score", 0)
    for it in raw:
        scored = score_item(it, rules)
        if not scored:
            continue
        if it["id"] in seen:
            continue
        if scored["score"] < min_score:
            continue
        it.update(scored)
        results.append(it)

    results.sort(key=lambda r: (r["score"], r["published"]), reverse=True)
    return results


def run_cycle(cfg, rules, state, now, send_report=None):
    seen = set(state["seen"])
    results = fetch_all(cfg, rules, seen)
    state["seen"].extend(r["id"] for r in results)
    state["seen"] = state["seen"][-3000:]
    save_json(STATE, state)

    if results:
        write_data_files(cfg, rules, results, now)

    if send_report is None:
        send_report = cfg["telegram"]["send_report"] and bool(
            os.environ.get("TELEGRAM_CHAT_ID", cfg["telegram"]["chat_id"])
        )
    if send_report:
        for text in build_telegram_messages(rules, results, now):
            send_telegram(cfg, text)

    print(f"[done] {len(results)} new, " + ", ".join(f"{p}:{c}" for p, c in priority_counts(results).items()))
    return results


def priority_counts(results):
    counts = {}
    for r in results:
        counts[r["priority"]] = counts.get(r["priority"], 0) + 1
    return counts


def build_telegram_messages(rules, results, now):
    """Build one Telegram message per category (max ~3900 chars each) so every news item is shown."""
    head = f"<b>Market Intelligence — {now.strftime('%Y-%m-%d %H:%M')}</b>"
    if not results:
        return [head + "\nخبر مرتبط جدیدی یافت نشد."]

    per_cat = {}
    for it in results:
        per_cat.setdefault(it["category_id"], []).append(it)

    msgs = []
    for cid in sorted(per_cat, key=lambda c: -len(per_cat[c])):
        label = _label_fa(rules, cid)
        lines = [f"<b>{label}</b>"]
        budget = 3900 - len(head) - 1 - (len(label) + 7)
        for it in per_cat[cid]:
            block = (
                f"🔸 {_source_label(it.get('platform'), it.get('source', ''))} - "
                f'<a href="{_escape_html(it.get("url", ""))}">{_escape_html(_clean_title(it.get("title", "")))}</a>'
            )
            if budget - (len(block) + 2) < 0:
                msgs.append("\n".join([head] + lines).rstrip("\n"))
                lines = [f"<b>{label} (ادامه)</b>"]
                budget = 3900 - len(head) - 1 - (len(label) + 16)
            budget -= len(block) + 2
            lines.append(block)
            lines.append("")  # blank line between news items
        msgs.append("\n".join([head] + lines).rstrip("\n"))
    return msgs


# --------------------------- period reports ---------------------------

COMPLAINT_KW = [
    "شکایت", "مشکل", "باگ", "خطا", "خرابی", "خراب", "عدم دسترسی",
    "پشتیبانی ضعیف", "اعتراض", "نارضایتی", "قطعی", "آسیب به سرویس",
]


def _label_fa(rules, cid):
    for c in rules["categories"]:
        if c["id"] == cid:
            return c["label_fa"]
    return "سایر"


def _prio_fa(rules, key):
    p = rules["priorities"].get(key, {})
    return p.get("label_fa", key)


def _item_block(it, rules, num=None):
    prefix = f"{num}. " if num else "• "
    return (
        f"{prefix}🔸 {_source_label(it.get('platform'), it.get('source', ''))} - "
        f'<a href="{_escape_html(it.get("url", ""))}">{_escape_html(_clean_title(it.get("title", "")))}</a>'
    )


def load_period_items(cfg, days):
    """Load all records from data/*.jsonl within the last `days` days."""
    d = HERE / cfg["git"]["data_dir"]
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    out = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.jsonl")):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = datetime.datetime.fromisoformat(rec["ts"])
                except Exception:
                    continue
                if ts >= cutoff:
                    out.append(rec)
    return out


def _counts(values):
    d = {}
    for v in values:
        d[v] = d.get(v, 0) + 1
    return d


def _prio_summary(rules, counts):
    order = ["critical", "high", "medium", "low"]
    return "، ".join(f"{_prio_fa(rules, k)}: {counts.get(k, 0)}" for k in order if counts.get(k, 0))


def build_daily_report(cfg, rules, now):
    items = load_period_items(cfg, 1)
    if not items:
        return "<b>گزارش روزانه — رسمیو</b>\nدر ۲۴ ساعت گذشته خبری ثبت نشد."

    lines = [f"<b>گزارش روزانه — رسمیو</b>", f"بازه: ۲۴ ساعت گذشته ({now.strftime('%Y-%m-%d')})"]
    pc = _counts(it.get("priority", "low") for it in items)
    lines.append(f"\n📊 مجموع: {len(items)} خبر | {_prio_summary(rules, pc)}")

    cat_counts = _counts(it.get("category_id") for it in items)
    banded = [f"▫️ {_label_fa(rules, cid)}: {n}" for cid, n in sorted(cat_counts.items(), key=lambda kv: -kv[1])]
    lines.append("\n🗂 به تفکیک دسته:\n" + "\n".join(banded))

    src_counts = _counts(it.get("platform", "news") for it in items)
    src_label = {"news": "خبرگزاری‌ها", "telegram": "تلگرام", "eitaa": "ایتا"}
    src_line = "، ".join(f"{src_label.get(k, k)}: {n}" for k, n in sorted(src_counts.items(), key=lambda kv: -kv[1]))
    lines.append(f"\n📡 به تفکیک منبع: {src_line}")

    brand = [it for it in items if "رسمیو" in f"{it.get('title', '')} {it.get('snippet', '')}"]
    if brand:
        lines.append(f"\n🟠 منشن رسمیو: {len(brand)}")

    top = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:5]
    lines.append("\n🏆 ۵ خبر برتر:")
    lines.extend(_item_block(it, rules, i + 1) for i, it in enumerate(top))
    return "\n".join(lines)


def _has_complaint(it):
    text = f"{it.get('title', '')} {it.get('snippet', '')}"
    # match full words only so «قطعی» doesn't match «مقطعی» / «متوقف»
    guard = r"(?<![A-Za-z\u0600-\u06FF])" 
    for k in COMPLAINT_KW:
        if re.search(guard + re.escape(k) + r"(?![A-Za-z\u0600-\u06FF])", text):
            return True
    return False


def _top_keywords(rules, items, limit):
    freq = {}
    for it in items:
        text = f"{it.get('title', '')} {it.get('snippet', '')}".lower()
        for cat in rules["categories"]:
            for kw in cat["keywords"]:
                kt = kw["text"].lower()
                if len(kt) < 2:
                    continue
                if kt in text and _is_persian(kt):
                    freq[kw["text"]] = freq.get(kw["text"], 0) + 1
    return sorted(freq.items(), key=lambda kv: -kv[1])[:limit]


def build_weekly_report(cfg, rules, now):
    items = load_period_items(cfg, 7)
    if not items:
        return "<b>گزارش هفتگی — رسمیو</b>\nدر ۷ روز گذشته خبری ثبت نشد."

    start = (now - datetime.timedelta(days=6)).strftime("%Y-%m-%d")
    lines = [f"<b>گزارش هفتگی — رسمیو</b>", f"بازه: {start} تا {now.strftime('%Y-%m-%d')}"]

    pc = _counts(it.get("priority", "low") for it in items)
    lines.append(f"\n📊 مجموع: {len(items)} خبر | {_prio_summary(rules, pc)}")

    cat_counts = _counts(it.get("category_id") for it in items)
    lines.append("\n🔊 سهم صدا (Share of Voice):")
    for cid, n in sorted(cat_counts.items(), key=lambda kv: -kv[1]):
        pct = int(round(100.0 * n / len(items)))
        lines.append(f"  {_label_fa(rules, cid)}: {n} ({pct}٪)")

    brand = [it for it in items if "رسمیو" in f"{it.get('title', '')} {it.get('snippet', '')}"]
    comp = [it for it in items if "لینکا" in f"{it.get('title', '')} {it.get('snippet', '')}"]
    lines.append(f"\n🟠 منشن رسمیو: {len(brand)}")
    lines.append(f"👥 منشن رقیب (لینکا): {len(comp)}")

    complaints = [it for it in items if _has_complaint(it)]
    if complaints:
        lines.append(f"\n⚠️ هشدار / شکایت مشتریان: {len(complaints)}")
        for it in complaints[:3]:
            lines.append(_item_block(it, rules))
    else:
        lines.append("\n⚠️ هشدار / شکایت مشتریان: 0")

    topics = _top_keywords(rules, items, 10)
    if topics:
        lines.append("\n🔥 موضوعات داغ هفته:")
        for k, n in topics:
            lines.append(f"  {k}: {n}")
    return "\n".join(lines)


def dispatch_report(cfg, text):
    if os.environ.get("TELEGRAM_BOT_TOKEN", cfg["telegram"]["bot_token"]) and os.environ.get(
        "TELEGRAM_CHAT_ID", cfg["telegram"]["chat_id"]
    ):
        send_telegram(cfg, text)
    else:
        print(text)


# --------------------------- delivery & storage ---------------------------

def send_telegram(cfg, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", cfg["telegram"]["bot_token"])
    chat = os.environ.get("TELEGRAM_CHAT_ID", cfg["telegram"]["chat_id"])
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({
        "chat_id": chat,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    try:
        with urlopen(req, timeout=60) as resp:
            resp.read()
        print("[telegram] report sent")
    except Exception as e:
        print(f"[telegram] send error: {e}")


def write_data_files(cfg, rules, results, now):
    d = HERE / cfg["git"]["data_dir"]
    d.mkdir(exist_ok=True)
    day = now.strftime("%Y-%m")
    md_path = d / f"{day}.md"
    jl_path = d / f"{day}.jsonl"

    md_lines = [f"# Market Intelligence Feed — {now.strftime('%Y-%m-%d %H:%M')}", ""]
    for it in results:
        md_lines.append(f"## [{it['score']}/100] {it['priority_fa']} — {it['type_fa']}")
        md_lines.append(f"- {it['title']}")
        md_lines.append(f"- {it['url']}")
        md_lines.append(f"- Category: {it['category']} | Reasons: {'; '.join(it['reasons'])}")
        md_lines.append("")
    with open(md_path, "a", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    with open(jl_path, "a", encoding="utf-8") as f:
        for it in results:
            rec = {
                "id": it["id"],
                "ts": now.isoformat(timespec="seconds"),
                "title": it["title"],
                "url": it["url"],
                "source": it["source"],
                "platform": it["platform"],
                "published": it["published"],
                "snippet": it.get("snippet", ""),
                "category": it["category"],
                "category_id": it["category_id"],
                "categories": it["categories"],
                "type": it["type"],
                "score": it["score"],
                "priority": it["priority"],
                "reasons": it["reasons"],
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run a single cycle and exit")
    ap.add_argument("--send-report", action="store_true", default=None, help="force send to Telegram (None=auto)")
    ap.add_argument("--daily-report", action="store_true", help="build & send daily summary (last 24h)")
    ap.add_argument("--weekly-report", action="store_true", help="build & send weekly analysis (last 7 days)")
    args = ap.parse_args()

    cfg = load_json(CONFIG)
    rules = load_json(RULES)
    STATE.parent.mkdir(exist_ok=True)
    state = {"seen": []}
    if STATE.exists():
        state = load_json(STATE)

    if args.daily_report:
        dispatch_report(cfg, build_daily_report(cfg, rules, datetime.datetime.now()))
        return
    if args.weekly_report:
        dispatch_report(cfg, build_weekly_report(cfg, rules, datetime.datetime.now()))
        return

    if args.once:
        run_cycle(cfg, rules, state, datetime.datetime.now(), send_report=args.send_report)
        return

    while True:
        run_cycle(cfg, rules, state, datetime.datetime.now())
        time.sleep(cfg["interval_hours"] * 3600)


if __name__ == "__main__":
    main()