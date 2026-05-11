#!/usr/bin/env python3
"""
X (Twitter) keyword bot - belirli keyword'lerin tweetlerini Telegram'a gönderir.
Her 2 saatte bir çalışır (GitHub Actions).
"""

import requests
import json
import os
import hashlib
import time
from datetime import datetime

XQUIK_API_KEY  = os.environ.get("XQUIK_API_KEY", "xq_838324a6c51b759f1052cee45f4e13efef91683c998626bfb708acb0cad28fa1")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8735024977:AAGKdvu65vz8IZ4Cz-_Oqp0ALh9hry5px4w")
CHAT_ID        = os.environ.get("CHAT_ID", "1173482573")

_base      = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_base, "x_sent_ids.json")
LOG_FILE   = os.path.join(_base, "x_bot.log")

KEYWORDS = [
    "ali babacan",
    "deva partisi",
    "deva partili",
    "deva partisi milletvekili",
    "sadullah kısacık",
    "mehmet emin ekmen",
    "idris şahin",
    "elif esen deva",
    "hasan karal",
    "burak dalgın deva",
]

XQUIK_HEADERS = {"x-api-key": XQUIK_API_KEY}
MIN_FOLLOWERS = 100  # Çok küçük hesapları filtrele


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
        json.dump(list(sent)[-8000:], f)


def search_keyword(query, limit=20):
    params = {"q": query, "limit": limit}
    try:
        r = requests.get(
            "https://xquik.com/api/v1/x/tweets/search",
            headers=XQUIK_HEADERS,
            params=params,
            timeout=15,
        )
        if r.ok:
            return r.json().get("tweets", [])
        else:
            log(f"Arama hatası ({query}): {r.status_code} {r.text[:80]}")
    except Exception as e:
        log(f"İstek hatası ({query}): {e}")
    return []


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=10)
    return r.ok


def format_tweet(tweet, keyword):
    author   = tweet.get("author", {})
    username = author.get("username", "?")
    name     = author.get("name", username)
    verified = "✓" if author.get("verified") else ""
    text     = tweet.get("text", "")
    url      = tweet.get("url", "")
    likes    = tweet.get("likeCount", 0)
    retweets = tweet.get("retweetCount", 0)
    views    = tweet.get("viewCount", 0)

    return (
        f"🐦 <b>X | {keyword}</b>\n"
        f"<b>{name}</b> {verified} @{username}\n"
        f"{text}\n"
        f"❤️ {likes}  🔁 {retweets}  👁 {views}\n"
        f"<a href='{url}'>→ Tweete git</a>"
    )


def main():
    sent = load_sent()
    new_count = 0

    for keyword in KEYWORDS:
        tweets = search_keyword(keyword)
        for tweet in tweets:
            tweet_id = tweet.get("id")
            if not tweet_id or tweet_id in sent:
                continue

            # Çok küçük hesapları atla
            followers = tweet.get("author", {}).get("followers", 0)
            if followers < MIN_FOLLOWERS:
                continue

            msg = format_tweet(tweet, keyword)
            if send_telegram(msg):
                sent.add(tweet_id)
                new_count += 1
                time.sleep(0.3)

        time.sleep(1)  # Rate limiting

    save_sent(sent)
    log(f"{new_count} yeni tweet gönderildi.")


if __name__ == "__main__":
    main()
