#!/usr/bin/env python3
"""
Haber botu - ekonomi ve siyaset haberlerini Telegram'a gönderir.
Kurulum: crontab -e  →  0 * * * * python3 ~/news_bot/news_bot.py
"""

import feedparser
import requests
import json
import os
import hashlib
import sys
import time
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8735024977:AAGKdvu65vz8IZ4Cz-_Oqp0ALh9hry5px4w")
CHAT_ID        = os.environ.get("CHAT_ID", "1173482573")

_base = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_base, "sent_ids.json")
LOG_FILE   = os.path.join(_base, "news_bot.log")

FEEDS = [
    # --- Ekonomi odaklı ---
    {"name": "Bloomberg HT",       "url": "https://www.bloomberght.com/rss",                                "tag": "💰 Ekonomi"},
    {"name": "NTV Ekonomi",        "url": "https://www.ntv.com.tr/ekonomi.rss",                             "tag": "💰 Ekonomi"},
    {"name": "AA Ekonomi",         "url": "https://www.aa.com.tr/tr/rss/default?cat=ekonomi",               "tag": "💰 Ekonomi"},
    {"name": "Sözcü Ekonomi",      "url": "https://www.sozcu.com.tr/rss/ekonomi.xml",                       "tag": "💰 Ekonomi"},
    {"name": "Halk TV Ekonomi",    "url": "https://www.halktv.com.tr/rss/ekonomi",                          "tag": "💰 Ekonomi"},
    {"name": "Cumhuriyet Ekonomi", "url": "https://www.cumhuriyet.com.tr/rss/ekonomi.xml",                  "tag": "💰 Ekonomi"},

    # --- Siyaset odaklı ---
    {"name": "Sözcü Siyaset",      "url": "https://www.sozcu.com.tr/rss/siyaset.xml",                       "tag": "🏛️ Siyaset"},
    {"name": "Halk TV Siyaset",    "url": "https://www.halktv.com.tr/rss/siyaset",                          "tag": "🏛️ Siyaset"},
    {"name": "T24",                "url": "https://news.google.com/rss/search?q=site:t24.com.tr&hl=tr&gl=TR&ceid=TR:tr", "tag": "🏛️ Siyaset", "unfiltered": True},
    {"name": "Medyascope",         "url": "https://medyascope.tv/feed/",                                    "tag": "🏛️ Siyaset"},
    {"name": "Serbestiyet",        "url": "https://serbestiyet.com/feed/",                                  "tag": "🏛️ Siyaset"},
    {"name": "Karar",              "url": "https://www.karar.com/rss",                                      "tag": "🏛️ Siyaset"},

    # --- Genel gündem (filtreli) ---
    {"name": "Anka Haber",         "url": "https://news.google.com/rss/search?q=site:ankahaber.net&hl=tr&gl=TR&ceid=TR:tr",    "tag": "📰 Gündem"},
    {"name": "Cumhur Haber",       "url": "https://news.google.com/rss/search?q=site:cumhurhaber.com&hl=tr&gl=TR&ceid=TR:tr",  "tag": "📰 Gündem"},
    {"name": "Hibya Haber",        "url": "https://www.hibya.com/rss.xml",                                 "tag": "📰 Gündem"},
    {"name": "Cumhuriyet",         "url": "https://www.cumhuriyet.com.tr/rss/son_dakika.xml",               "tag": "📰 Gündem"},
    {"name": "AA Güncel",          "url": "https://www.aa.com.tr/tr/rss/default?cat=guncel",                "tag": "📰 Gündem"},
    {"name": "NTV Gündem",         "url": "https://www.ntv.com.tr/gundem.rss",                              "tag": "📰 Gündem"},
    {"name": "CNN Türk",           "url": "https://www.cnnturk.com/feed/rss/all/news",                      "tag": "📺 Gündem"},
    {"name": "AHaber",             "url": "https://www.ahaber.com.tr/rss/anasayfa.xml",                     "tag": "📺 Gündem"},
    {"name": "BBC Türkçe",         "url": "https://feeds.bbci.co.uk/turkish/rss.xml",                       "tag": "🌍 Uluslararası"},
    {"name": "Al Jazeera EN",      "url": "https://www.aljazeera.com/xml/rss/all.xml",                      "tag": "🌍 Uluslararası"},
]

# Genel haber feedleri için anahtar kelime filtresi
ECO_KEYWORDS = [
    "ekonomi", "dolar", "euro", "faiz", "enflasyon", "borsa", "merkez bankası",
    "tcmb", "büyüme", "ihracat", "ithalat", "bütçe", "hazine", "maliye",
    "economy", "inflation", "interest rate", "gdp", "finance", "market",
    "trade", "central bank", "fiscal", "monetary", "recession", "fed",
]
POL_KEYWORDS = [
    "siyaset", "hükümet", "meclis", "cumhurbaşkanı", "erdoğan", "iktidar",
    "muhalefet", "chp", "akp", "mhp", "dem", "seçim", "parti", "bakan",
    "politics", "government", "election", "parliament", "president",
    "minister", "senate", "congress", "policy", "nato", "sanctions",
]
LEADER_KEYWORDS = [
    "ali babacan", "ahmet davutoğlu", "mahmut arıkan", "müsavat dervişoğlu",
    "özgür özel", "recep tayyip erdoğan", "ümit özdağ",
    "mansur yavaş", "ekrem imamoğlu", "fatih erbakan",
    "devlet bahçeli", "tülay hatimoğulları", "tuncer bakırhan",
    "babacan", "davutoğlu", "dervişoğlu", "imamoğlu", "özdağ",
    "bahçeli", "hatimoğulları", "bakırhan",
]
FILTER_KEYWORDS = set(ECO_KEYWORDS + POL_KEYWORDS + LEADER_KEYWORDS)

# Bu feedler zaten filtreli — tüm haberleri gönder
UNFILTERED_TAGS = {"💰 Ekonomi", "🏛️ Siyaset"}


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_sent():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_sent(sent):
    with open(STATE_FILE, "w") as f:
        json.dump(list(sent)[-6000:], f)  # son 6000 kayıt yeterli


def make_id(link, title):
    return hashlib.md5(f"{link}{title}".encode()).hexdigest()


def is_relevant(title, summary=""):
    text = (title + " " + summary).lower()
    return any(kw in text for kw in FILTER_KEYWORDS)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=10)
    return r.ok, r.text


def get_chat_id():
    """Bota mesaj attıktan sonra chat_id'yi bulur."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url, timeout=10)
    data = r.json()
    if not data.get("result"):
        print("Henüz güncelleme yok. Lütfen bota bir mesaj atın, sonra tekrar çalıştırın.")
        return
    for update in data["result"]:
        msg = update.get("message") or update.get("channel_post")
        if msg:
            chat = msg["chat"]
            print(f"Chat ID: {chat['id']}  (Tip: {chat['type']}, Ad: {chat.get('first_name') or chat.get('title', '')})")


def main():
    if "--get-chat-id" in sys.argv:
        get_chat_id()
        return

    sent = load_sent()
    new_items = []

    for feed_cfg in FEEDS:
        try:
            feed = feedparser.parse(feed_cfg["url"])
            for entry in feed.entries[:15]:
                title   = (entry.get("title") or "").strip()
                link    = entry.get("link") or ""
                summary = entry.get("summary") or ""

                if not title or not link:
                    continue

                item_id = make_id(link, title)
                if item_id in sent:
                    continue

                if feed_cfg.get("unfiltered") or is_relevant(title, summary):
                    new_items.append({
                        "id":     item_id,
                        "source": feed_cfg["name"],
                        "tag":    feed_cfg["tag"],
                        "title":  title,
                        "link":   link,
                    })
                    sent.add(item_id)
        except Exception as e:
            log(f"HATA ({feed_cfg['name']}): {e}")

    for item in new_items:
        msg = (
            f"<b>{item['tag']} | {item['source']}</b>\n"
            f"{item['title']}\n"
            f"<a href='{item['link']}'>→ Habere git</a>"
        )
        ok, resp = send_telegram(msg)
        if not ok:
            log(f"Telegram hatası: {resp[:120]}")
        time.sleep(0.4)

    save_sent(sent)
    log(f"{len(new_items)} yeni haber gönderildi.")


if __name__ == "__main__":
    main()
