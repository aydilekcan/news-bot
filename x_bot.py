#!/usr/bin/env python3
"""
X (Twitter) keyword bot - belirli keyword'lerin tweetlerini Telegram'a gönderir.
Her 2 saatte bir çalışır (GitHub Actions).
"""

import requests
import json
import os
import time
from datetime import datetime, timezone

XQUIK_API_KEY  = os.environ.get("XQUIK_API_KEY", "xq_838324a6c51b759f1052cee45f4e13efef91683c998626bfb708acb0cad28fa1")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8735024977:AAGKdvu65vz8IZ4Cz-_Oqp0ALh9hry5px4w")
CHAT_ID        = os.environ.get("CHAT_ID", "1173482573")

_base          = os.path.dirname(os.path.abspath(__file__))
STATE_FILE     = os.path.join(_base, "x_sent_ids.json")
LOG_FILE       = os.path.join(_base, "x_bot.log")
KEYWORDS_FILE  = os.path.join(_base, "keywords.json")
TWEETS_FILE    = os.path.join(_base, "tweets_data.json")

XQUIK_HEADERS = {"x-api-key": XQUIK_API_KEY}
MIN_FOLLOWERS = 100  # Çok küçük hesapları filtrele
MAX_TWEETS_PER_KEYWORD = 50  # Dashboard için keyword başına saklanacak son tweet sayısı


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_keywords():
    if os.path.exists(KEYWORDS_FILE):
        with open(KEYWORDS_FILE) as f:
            return json.load(f)
    return []


def load_sent():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_sent(sent):
    with open(STATE_FILE, "w") as f:
        json.dump(list(sent)[-8000:], f)


def load_tweets_data():
    if os.path.exists(TWEETS_FILE):
        with open(TWEETS_FILE) as f:
            return json.load(f)
    return {}


def save_tweets_data(data):
    with open(TWEETS_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def tweet_record(tweet, keyword):
    """Dashboard için saklanan tweet kaydı."""
    author = tweet.get("author", {})
    return {
        "id": tweet.get("id"),
        "keyword": keyword,
        "text": tweet.get("text", ""),
        "url": tweet.get("url", ""),
        "author": {
            "username": author.get("username", ""),
            "name": author.get("name", ""),
            "verified": bool(author.get("verified")),
            "followers": author.get("followers", 0),
        },
        "likes": tweet.get("likeCount", 0),
        "retweets": tweet.get("retweetCount", 0),
        "views": tweet.get("viewCount", 0),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main():
    keywords = load_keywords()
    if not keywords:
        log("Keyword listesi boş. keywords.json kontrol et.")
        return

    sent = load_sent()
    tweets_data = load_tweets_data()
    new_count = 0

    for keyword in keywords:
        tweets = search_keyword(keyword)
        keyword_records = tweets_data.get(keyword, [])
        existing_ids = {r.get("id") for r in keyword_records}

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
                if tweet_id not in existing_ids:
                    keyword_records.append(tweet_record(tweet, keyword))
                    existing_ids.add(tweet_id)
                time.sleep(0.3)

        keyword_records.sort(key=lambda r: r.get("captured_at", ""), reverse=True)
        tweets_data[keyword] = keyword_records[:MAX_TWEETS_PER_KEYWORD]

        time.sleep(1)  # Rate limiting

    # Artık takip edilmeyen keyword'leri tweets_data'dan temizle
    for stale in [k for k in tweets_data if k not in keywords]:
        del tweets_data[stale]

    save_sent(sent)
    save_tweets_data(tweets_data)
    log(f"{new_count} yeni tweet gönderildi.")


if __name__ == "__main__":
    main()
