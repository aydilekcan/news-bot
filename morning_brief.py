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
import html as _html
from datetime import datetime, timezone, timedelta

TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

# Kategori adı -> emoji. LLM emoji vermezse bunu kullanırız.
CATEGORY_EMOJI = {
    "Siyaset": "🏛️",
    "Ekonomi": "💰",
    "Dünya Gündemi": "🌍",
    "Türkiye Gündemi": "🇹🇷",
    "Spor": "⚽",
    "Teknoloji & Bilim": "💻",
}

_base          = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    path = os.path.join(_base, ".env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID        = os.environ.get("CHAT_ID", "")
LOG_FILE       = os.path.join(_base, "morning_brief.log")
STATE_FILE     = os.path.join(_base, "morning_brief_state.json")


def today_tr():
    """Türkiye saatine göre bugünün tarihi (YYYY-MM-DD)."""
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%d")


def date_label_tr():
    """Okunabilir Türkçe tarih: '16 Haziran 2026'."""
    d = datetime.now(timezone.utc) + timedelta(hours=3)
    return f"{d.day} {TR_MONTHS[d.month - 1]} {d.year}"


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

                headlines.append({"source": feed_cfg["name"], "title": title, "link": link})
        except Exception as e:
            log(f"HATA ({feed_cfg['name']}): {e}")

    return headlines


CATEGORY_NAMES = ["Siyaset", "Ekonomi", "Dünya Gündemi",
                  "Türkiye Gündemi", "Spor", "Teknoloji & Bilim"]

BRIEF_TOOL = {
    "name": "brifing_yayinla",
    "description": "Hazırlanan sabah brifingini kategorilere ayrılmış olarak döndür.",
    "input_schema": {
        "type": "object",
        "properties": {
            "categories": {
                "type": "array",
                "description": "Sadece haber içeren kategoriler, önem sırasına göre.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": CATEGORY_NAMES},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string", "description": "Kısa, net başlık. Kaynak/tarih ekleme."},
                                    "summary": {"type": "string", "description": "2-3 cümle, bağlam ve önem."},
                                    "ref": {"type": "integer", "description": "Bu haberin dayandığı manşetin numarası (listedeki [n])."},
                                },
                                "required": ["title", "summary", "ref"],
                            },
                        },
                    },
                    "required": ["name", "items"],
                },
            }
        },
        "required": ["categories"],
    },
}


def summarize(headlines):
    """LLM'den brifingi tool-use ile alır; ref numaralarından URL/kaynağı eşleştirir."""
    client = anthropic.Anthropic()
    pool = headlines[:250]
    numbered = "\n".join(f"[{i}] [{h['source']}] {h['title']}" for i, h in enumerate(pool))

    prompt = f"""Aşağıda bugün çeşitli Türk ve uluslararası haber kaynaklarından toplanan manşetler var. Her satır: [numara] [Kaynak] Başlık

Bunları analiz ederek kapsamlı bir Türkçe sabah brifingi hazırla ve `brifing_yayinla` aracıyla döndür. Amaç dünü tam yansıtan, hiçbir önemli gelişmeyi kaçırmayan bir özet.

Kategoriler ve kategori başına en önemli 4-6 gelişmeyi seç:
- Siyaset (Türkiye iç siyaseti)
- Ekonomi (Türkiye ve küresel ekonomi)
- Dünya Gündemi (uluslararası gelişmeler)
- Türkiye Gündemi (iç politika dışı önemli gelişmeler: hukuk, toplum, afet, güvenlik vb.)
- Spor
- Teknoloji & Bilim

ÖNEMLİ — Şu siyasi liderlerin açıklama/haberlerini mutlaka Siyaset'e dahil et:
Recep Tayyip Erdoğan, Devlet Bahçeli, Özgür Özel, Ekrem İmamoğlu, Mansur Yavaş,
Ali Babacan, Ahmet Davutoğlu, Müsavat Dervişoğlu, Ümit Özdağ, Fatih Erbakan,
Mahmut Arıkan, Tülay Hatimoğulları, Tuncer Bakırhan.

Kurallar:
- Tekrar eden haberleri tek maddede birleştir; en güvenilir/kapsamlı manşeti seç.
- ref: o maddenin dayandığı manşetin başındaki numara (örn. [42] için 42).
- Önemsiz, magazin, trafik haberlerini atla.
- Sadece gerçekten haber içeren kategorileri döndür; boş kategori ekleme.

Manşetler:
{numbered}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        tools=[BRIEF_TOOL],
        tool_choice={"type": "tool", "name": "brifing_yayinla"},
        messages=[{"role": "user", "content": prompt}],
    )

    tool_input = None
    for block in message.content:
        if block.type == "tool_use" and block.name == "brifing_yayinla":
            tool_input = block.input
            break
    if tool_input is None:
        raise ValueError("Model tool_use döndürmedi")

    # ref numaralarını gerçek URL/kaynakla eşleştir
    for cat in tool_input.get("categories", []):
        for it in cat.get("items", []):
            ref = it.get("ref")
            if isinstance(ref, int) and 0 <= ref < len(pool):
                it["url"] = pool[ref]["link"]
                it["source"] = pool[ref]["source"]
    return tool_input


def _esc(s):
    return _html.escape((s or "").strip(), quote=False)


def render_blocks(data, date_label):
    """Yapılandırılmış brifingi Telegram-HTML bloklarına çevirir (her blok bölünmez bir birim)."""
    blocks = [f"🌅 <b>SABAH BRİFİNG — {_esc(date_label)}</b>"]
    for cat in data.get("categories", []):
        name = (cat.get("name") or "").strip()
        items = cat.get("items") or []
        if not name or not items:
            continue
        emoji = CATEGORY_EMOJI.get(name, "📌")
        blocks.append(f"<b>{emoji} {_esc(name)}</b>")
        for it in items:
            title = _esc(it.get("title"))
            if not title:
                continue
            parts = [f"<b>{title}</b>"]
            summary = _esc(it.get("summary"))
            if summary:
                parts.append(summary)
            url = (it.get("url") or "").strip()
            source = _esc(it.get("source"))
            footer = ""
            if url:
                footer = f"<a href=\"{_html.escape(url, quote=True)}\">→ Habere git</a>"
            if source:
                footer = (footer + " · " if footer else "") + f"<i>{source}</i>"
            if footer:
                parts.append(footer)
            blocks.append("\n".join(parts))
    return blocks


def pack_messages(blocks, limit=3800):
    """Blokları, hiçbir bloğu bölmeden, limit altındaki mesajlara paketle."""
    messages, cur = [], ""
    for b in blocks:
        add = ("\n\n" + b) if cur else b
        if cur and len(cur) + len(add) > limit:
            messages.append(cur)
            cur = b
        else:
            cur += add
    if cur:
        messages.append(cur)
    return messages


def send_telegram(text):
    """Tek bir Telegram mesajı gönderir. Bölme işi pack_messages tarafından yapılır."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)
    if not r.ok:
        log(f"Telegram hatası: {r.text[:200]}")
    return r.ok


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

    try:
        data = summarize(headlines)
        blocks = render_blocks(data, date_label_tr())
    except Exception as e:
        log(f"Özet/JSON işleme hatası: {e}")
        return

    if len(blocks) <= 1:  # sadece başlık var, haber yok
        log("Brifingde gösterilecek haber yok, çıkılıyor.")
        return

    messages = pack_messages(blocks)
    all_ok = True
    for msg in messages:
        if not send_telegram(msg):
            all_ok = False
        time.sleep(0.5)

    if all_ok:
        mark_sent_today()
        log(f"Brifing gönderildi ({len(messages)} mesaj).")
    else:
        log("Brifing kısmen başarısız, state işaretlenmedi.")


if __name__ == "__main__":
    main()
