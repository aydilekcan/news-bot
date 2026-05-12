#!/usr/bin/env python3
"""
Sabah brifing botu - her sabah 7:30'da dünün en önemli haberlerini özetler.
Kurulum: crontab -e  →  30 7 * * * python3 ~/news_bot/morning_brief.py
"""

import feedparser
import anthropic
import requests
import os
import json
import time
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8735024977:AAGKdvu65vz8IZ4Cz-_Oqp0ALh9hry5px4w")
CHAT_ID        = os.environ.get("CHAT_ID", "1173482573")
_base          = os.path.dirname(os.path.abspath(__file__))
LOG_FILE       = os.path.join(_base, "morning_brief.log")
STATE_FILE     = os.path.join(_base, "morning_brief_state.json")


def today_tr():
    """Türkiye saatine göre bugünün tarihi (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")


def already_sent_today():
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("last_sent_date") == today_tr()
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def mark_sent_today():
    with open(STATE_FILE, "w") as f:
        json.dump({"last_sent_date": today_tr()}, f)

FEEDS = [
    {"name": "Bloomberg HT",        "url": "https://www.bloomberght.com/rss"},
    {"name": "NTV Ekonomi",         "url": "https://www.ntv.com.tr/ekonomi.rss"},
    {"name": "AA Ekonomi",          "url": "https://www.aa.com.tr/tr/rss/default?cat=ekonomi"},
    {"name": "Sözcü Ekonomi",       "url": "https://www.sozcu.com.tr/rss/ekonomi.xml"},
    {"name": "Halk TV Ekonomi",     "url": "https://www.halktv.com.tr/rss/ekonomi"},
    {"name": "Cumhuriyet Ekonomi",  "url": "https://www.cumhuriyet.com.tr/rss/ekonomi.xml"},
    {"name": "Sözcü Siyaset",       "url": "https://www.sozcu.com.tr/rss/siyaset.xml"},
    {"name": "Halk TV Siyaset",     "url": "https://www.halktv.com.tr/rss/siyaset"},
    {"name": "T24",                 "url": "https://news.google.com/rss/search?q=site:t24.com.tr&hl=tr&gl=TR&ceid=TR:tr"},
    {"name": "Medyascope",          "url": "https://medyascope.tv/feed/"},
    {"name": "Serbestiyet",         "url": "https://serbestiyet.com/feed/"},
    {"name": "Karar",               "url": "https://www.karar.com/rss"},
    {"name": "Anka Haber",          "url": "https://news.google.com/rss/search?q=site:ankahaber.net&hl=tr&gl=TR&ceid=TR:tr"},
    {"name": "Cumhur Haber",        "url": "https://news.google.com/rss/search?q=site:cumhurhaber.com&hl=tr&gl=TR&ceid=TR:tr"},
    {"name": "Hibya Haber",         "url": "https://www.hibya.com/rss.xml"},
    {"name": "Cumhuriyet",          "url": "https://www.cumhuriyet.com.tr/rss/son_dakika.xml"},
    {"name": "AA Güncel",           "url": "https://www.aa.com.tr/tr/rss/default?cat=guncel"},
    {"name": "NTV Gündem",          "url": "https://www.ntv.com.tr/gundem.rss"},
    {"name": "CNN Türk",            "url": "https://www.cnnturk.com/feed/rss/all/news"},
    {"name": "AHaber",              "url": "https://www.ahaber.com.tr/rss/anasayfa.xml"},
    {"name": "BBC Türkçe",          "url": "https://feeds.bbci.co.uk/turkish/rss.xml"},
    {"name": "Al Jazeera EN",       "url": "https://www.aljazeera.com/xml/rss/all.xml"},
]


def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def collect_headlines():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    headlines = []

    for feed_cfg in FEEDS:
        try:
            feed = feedparser.parse(feed_cfg["url"])
            for entry in feed.entries[:20]:
                title = (entry.get("title") or "").strip()
                link  = entry.get("link") or ""
                if not title or not link:
                    continue

                # Zaman filtresi — timestamp yoksa dahil et
                pub = entry.get("published_parsed")
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue

                headlines.append(f"[{feed_cfg['name']}] {title} | {link}")
        except Exception as e:
            log(f"HATA ({feed_cfg['name']}): {e}")

    return headlines


def summarize(headlines):
    client = anthropic.Anthropic()
    headlines_text = "\n".join(headlines[:250])

    prompt = f"""Aşağıda bugün çeşitli Türk ve uluslararası haber kaynaklarından toplanan manşetler var.

Bu manşetleri analiz ederek kapsamlı bir Türkçe sabah brifing özeti hazırla. Amaç dünü tam olarak yansıtan, hiçbir önemli gelişmeyi kaçırmayan bir özet sunmak.

Şu kategorilerde en önemli 4-6 gelişmeyi seç:
- 🏛️ Siyaset (Türkiye iç siyaseti)
- 💰 Ekonomi (Türkiye ve küresel ekonomi)
- 🌍 Dünya Gündemi (uluslararası gelişmeler)
- 🇹🇷 Türkiye Gündemi (iç politika dışı önemli gelişmeler: hukuk, toplum, afet, güvenlik vb.)
- ⚽ Spor
- 💻 Teknoloji & Bilim

ÖNEMLİ — Aşağıdaki siyasi liderlerin açıklamaları veya haberlerini mutlaka Siyaset kategorisine dahil et:
Recep Tayyip Erdoğan, Devlet Bahçeli, Özgür Özel, Ekrem İmamoğlu, Mansur Yavaş,
Ali Babacan, Ahmet Davutoğlu, Müsavat Dervişoğlu, Ümit Özdağ, Fatih Erbakan,
Mahmut Arıkan, Tülay Hatimoğulları, Tuncer Bakırhan.
Bu isimlerden biri geçen haber varsa mutlaka özetle.

Her madde için format:
• Başlık (Kaynak) — link
  2-3 cümle açıklayıcı özet. Bağlam ve önem belirt.

Kurallar:
- Tekrar eden haberleri tek maddede birleştir, kaynağı en güvenilir olanı seç
- Önemsiz, magazin veya trafik haberi gibi içerikleri atla
- Her kategori gerçekten dolu ve bilgilendirici olsun
- Eğer bir kategoride haber yoksa o kategoriyi yazma
- Mesajın en sonuna bugünün tarihini ekle

Haberler:
{headlines_text}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Telegram 4096 karakter sınırı — gerekirse böl
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        if not r.ok:
            log(f"Telegram hatası: {r.text[:120]}")
        time.sleep(0.5)


def main():
    if already_sent_today():
        log("Bugün zaten gönderildi, atlanıyor.")
        return

    log("Sabah brifing başlıyor...")
    headlines = collect_headlines()
    log(f"{len(headlines)} manşet toplandı.")

    if not headlines:
        log("Haber bulunamadı, çıkılıyor.")
        return

    brief = summarize(headlines)
    header = f"🌅 <b>SABAH BRİFİNG</b> — {datetime.now().strftime('%d %B %Y')}\n\n"
    send_telegram(header + brief)
    mark_sent_today()
    log("Brifing gönderildi.")


if __name__ == "__main__":
    main()
