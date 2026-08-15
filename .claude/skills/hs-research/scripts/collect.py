#!/usr/bin/env python3
"""Сбор свежих материалов по Hearthstone из Reddit и YouTube.

    python3 collect.py reddit --sub CompetitiveHS --sort top --time week
    python3 collect.py reddit --sub hearthstone --query "Violet Hold" --time month
    python3 collect.py youtube --query "hearthstone best decks" --recent
    python3 collect.py all --query "meta"

Что важно знать про источники:

* Reddit отдаётся только через RSS на old.reddit.com. JSON-эндпоинт возвращает
  403, прямой фетч из Claude Code заблокирован. RSS быстро упирается в 429,
  поэтому запросы идут по одному с паузой и повтором по возрастающей.
* YouTube не отдаёт RSS для поиска — разбираем ytInitialData из HTML выдачи.
  Для канала RSS есть: /feeds/videos.xml?channel_id=UC...
* X/Twitter напрямую недоступен: API платный, nitter мёртв. Твиты ищутся
  только через веб-поиск (WebSearch с site:x.com) — этот скрипт их не берёт.

Вывод — JSON в stdout, чтобы редактор мог сразу с ним работать.
"""

import argparse
import html
import json
import re
import subprocess
import sys
import time

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SUBS = ["hearthstone", "CompetitiveHS", "wildhearthstone", "BobsTavern"]


def fetch(url, tries=4, pause=4.0):
    """GET с бэкоффом. Reddit отвечает 429 почти сразу — это нормально, ждём."""
    for attempt in range(tries):
        proc = subprocess.run(
            ["curl", "-sS", "-m", "25", "-A", UA, "-w", "\n__HTTP__%{http_code}", url],
            capture_output=True, text=True,
        )
        body = proc.stdout
        code = "000"
        if "__HTTP__" in body:
            body, _, code = body.rpartition("__HTTP__")
        code = code.strip()
        if code == "200" and body.strip():
            return body
        if code == "429":
            time.sleep(pause * (attempt + 1))
            continue
        if attempt == tries - 1:
            print(f"! {url} -> HTTP {code}", file=sys.stderr)
        time.sleep(pause)
    return ""


def parse_reddit_rss(xml, sub):
    items = []
    for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.DOTALL):
        def grab(tag):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", entry, re.DOTALL)
            if not m:
                return ""
            # Reddit экранирует дважды: &amp;#39; -> &#39; -> '. Распаковать оба
            # слоя, и только потом снимать теги, иначе они остаются в тексте.
            raw = html.unescape(html.unescape(m.group(1)))
            return " ".join(re.sub(r"<[^>]+>", " ", raw).split())

        link = re.search(r'<link[^>]*href="([^"]+)"', entry)
        content = grab("content")
        items.append({
            "source": f"reddit/r/{sub}",
            "title": grab("title"),
            "author": grab("name"),
            "date": grab("updated")[:10],
            "url": html.unescape(link.group(1)) if link else "",
            "excerpt": " ".join(content.split())[:600],
        })
    return items


def reddit(sub, sort="top", period="week", query=None, limit=15):
    if query:
        url = (f"https://old.reddit.com/r/{sub}/search.rss?q={query}"
               f"&restrict_sr=1&sort={sort}&t={period}&limit={limit}")
    else:
        url = f"https://old.reddit.com/r/{sub}/{sort}/.rss?t={period}&limit={limit}"
    xml = fetch(url)
    if not xml:
        return []
    return parse_reddit_rss(xml, sub)[:limit]


def youtube(query, recent=False, limit=15):
    # sp=CAI...: сортировка по дате; EgIIAw — фильтр «за неделю»
    sp = "&sp=CAISBAgDEAE%253D" if recent else ""
    url = (f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
           f"{sp}&hl=en&gl=US&persist_hl=1")  # без локали даты приходят на языке узла
    body = fetch(url, tries=2, pause=2.0)
    if not body:
        return []

    m = re.search(r"var ytInitialData = (\{.*?\});</script>", body, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    out = []

    def walk(node):
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            vr = node.get("videoRenderer")
            if vr and vr.get("videoId"):
                title = "".join(r.get("text", "") for r in
                                vr.get("title", {}).get("runs", []))
                owner = "".join(r.get("text", "") for r in
                                vr.get("ownerText", {}).get("runs", []))
                out.append({
                    "source": "youtube",
                    "title": title,
                    "author": owner,
                    "date": vr.get("publishedTimeText", {}).get("simpleText", ""),
                    "views": vr.get("viewCountText", {}).get("simpleText", ""),
                    "url": f"https://www.youtube.com/watch?v={vr['videoId']}",
                    "excerpt": "".join(
                        s.get("text", "") for s in
                        vr.get("detailedMetadataSnippets", [{}])[0]
                        .get("snippetText", {}).get("runs", [])
                    )[:400],
                })
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


def main():
    ap = argparse.ArgumentParser(description="Сбор материалов по Hearthstone")
    ap.add_argument("mode", choices=["reddit", "youtube", "all"])
    ap.add_argument("--sub", default=None, help="сабреддит; по умолчанию — все основные")
    ap.add_argument("--query", default=None)
    ap.add_argument("--sort", default="top", choices=["top", "hot", "new"])
    ap.add_argument("--time", dest="period", default="week",
                    choices=["day", "week", "month", "year"])
    ap.add_argument("--recent", action="store_true", help="youtube: только свежее")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args()

    results = []

    if args.mode in ("reddit", "all"):
        subs = [args.sub] if args.sub else SUBS
        for i, sub in enumerate(subs):
            if i:
                time.sleep(3)  # Reddit жёстко режет частые запросы
            results += reddit(sub, args.sort, args.period, args.query, args.limit)

    if args.mode in ("youtube", "all"):
        q = args.query or "hearthstone"
        if "hearthstone" not in q.lower():
            q = f"hearthstone {q}"
        results += youtube(q, args.recent or args.period in ("day", "week"), args.limit)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not results:
        print("! ничего не собрано — вероятно 429 от Reddit; "
              "повторить через минуту или идти через WebSearch", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
