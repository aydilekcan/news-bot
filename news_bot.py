#!/usr/bin/env python3
"""
Haber botu — sabit kaynak listesi + admin'in panelden eklediği custom RSS'ler.

Akis:
  1. SOURCES + custom_feeds.json'daki her kaynak icin RSS okunur.
  2. Daha once gonderilmis basliklar (link hash + normalize edilmis baslik hash + 30 gunluk
     token-overlap benzerligi) elenir.
  3. Kalan adaylar tek bir Claude API cagrisiyla degerlendirilir; her keep=true item icin
     LLM kisa ozet + siyasi yon (left/neutral/right) doner.
  4. Hem kullanicinin DM'ine (CHAT_ID) hem de varsa public kanala (CHANNEL_ID) gonderilir.
  5. news_data.json'a kaydedilir (dashboard buradan okur), state dosyasi (sent_ids.json) yazilir.
"""

import feedparser
import requests
import json
import os
import re
import hashlib
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID            = os.environ.get("CHAT_ID", "")
CHANNEL_ID         = os.environ.get("CHANNEL_ID", "").strip()
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL          = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")

_base             = os.path.dirname(os.path.abspath(__file__))
STATE_FILE        = os.path.join(_base, "sent_ids.json")
NEWS_DATA_FILE    = os.path.join(_base, "news_data.json")
CUSTOM_FEEDS_FILE = os.path.join(_base, "custom_feeds.json")
LOG_FILE          = os.path.join(_base, "news_bot.log")

DEDUP_WINDOW_DAYS    = 30
SIMILARITY_THRESHOLD = 0.55
MAX_LLM_CANDIDATES   = 180
PER_FEED_LIMIT       = 10
MIN_SCORE            = 6
MAX_DELIVER          = 15
SEND_DELAY_S         = 3.5
NEWS_DATA_KEEP_DAYS  = 90
NEWS_DATA_MAX_ITEMS  = 5000

TURKEY_TZ   = timezone(timedelta(hours=3))
QUIET_HOURS = set(range(1, 7))  # 01:00-06:59 TR

VALID_LEANS = {"left", "neutral", "right"}

# Bazi siteler default feedparser UA'sini engelliyor — tarayici UA gonderiyoruz.
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _gn(query: str, lang: str = "tr") -> str:
    """Google News arama RSS'i — resmi RSS'i olmayan kaynaklar icin koprudur."""
    if lang == "en":
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    else:
        params = {"q": query, "hl": "tr", "gl": "TR", "ceid": "TR:tr"}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


# Sabit kaynak listesi. Her item bir RSS (direkt veya Google News kopru) + sabit label + default_lean.
# Bazi kaynaklarin resmi RSS'i bot trafigini blokluyor (T24, Medyascope, soL) -> Google News kopru.
# Reuters/AP -> Google News EN (TR locale'da sonuc gelmiyor).
SOURCES = [
    # Direkt resmi RSS
    {"url": "https://feeds.bbci.co.uk/turkce/rss.xml",                       "label": "BBC Türkçe",       "default_lean": "neutral"},
    {"url": "https://rss.dw.com/rdf/rss-tur-all",                             "label": "DW Türkçe",        "default_lean": "neutral"},
    {"url": "https://www.diken.com.tr/feed/",                                "label": "Diken",            "default_lean": "left"},
    {"url": "https://yetkinreport.com/feed/",                                "label": "YetkinReport",     "default_lean": "neutral"},
    {"url": "https://bianet.org/biamag.rss",                                 "label": "bianet",           "default_lean": "left"},
    {"url": "https://www.cumhuriyet.com.tr/rss/son_dakika.xml",              "label": "Cumhuriyet",       "default_lean": "left"},
    {"url": "https://www.sozcu.com.tr/feeds-rss-category-sozcu",             "label": "Sözcü",            "default_lean": "left"},
    {"url": "https://www.aa.com.tr/tr/rss/default?cat=guncel",               "label": "Anadolu Ajansı",   "default_lean": "right"},
    {"url": "https://www.trthaber.com/sondakika.rss",                        "label": "TRT Haber",        "default_lean": "right"},
    {"url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCdS7OE5qbJQc7AG4SwlTzKg", "label": "Fatih Altaylı", "default_lean": "neutral"},

    # Google News koprusu (resmi RSS bot engeli veya yok)
    {"url": _gn("site:t24.com.tr when:1d"),         "label": "T24",          "default_lean": "left"},
    {"url": _gn("site:medyascope.tv when:1d"),      "label": "Medyascope",   "default_lean": "left"},
    {"url": _gn("site:sol.org.tr when:1d"),         "label": "soL",          "default_lean": "left"},
    {"url": _gn("site:reuters.com when:1d", "en"),  "label": "Reuters",      "default_lean": "neutral"},
    {"url": _gn("site:apnews.com when:1d", "en"),   "label": "AP",           "default_lean": "neutral"},
]


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_custom_feeds():
    """Admin'in dashboard'tan ekledigi RSS'leri oku. Hatali kayitlari ele."""
    if not os.path.exists(CUSTOM_FEEDS_FILE):
        return []
    try:
        with open(CUSTOM_FEEDS_FILE) as f:
            data = json.load(f)
    except Exception as e:
        log(f"custom_feeds.json okunamadi: {e}")
        return []
    out = []
    if not isinstance(data, list):
        return []
    for item in data:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        label = (item.get("label") or "").strip() or url
        lean = (item.get("default_lean") or "neutral").lower()
        if lean not in VALID_LEANS:
            lean = "neutral"
        out.append({"url": url, "label": label[:60], "default_lean": lean})
    return out


def all_feeds():
    return SOURCES + load_custom_feeds()


# --- State -----------------------------------------------------------------
def _empty_state():
    return {"version": 2, "ids": {}, "fingerprints": []}


def load_state():
    if not os.path.exists(STATE_FILE):
        return _empty_state()
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except Exception:
        return _empty_state()
    if isinstance(data, list):
        ancient = "1970-01-01T00:00:00+00:00"
        return {"version": 2, "ids": {h: ancient for h in data}, "fingerprints": []}
    if not isinstance(data, dict) or "ids" not in data:
        return _empty_state()
    data.setdefault("fingerprints", [])
    return data


def save_state(state):
    cutoff_ids = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    cutoff_fp  = (datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)).isoformat()
    state["ids"] = {h: ts for h, ts in state["ids"].items() if ts >= cutoff_ids or ts.startswith("1970")}
    if len(state["ids"]) > 20000:
        items = sorted(state["ids"].items(), key=lambda kv: kv[1])
        state["ids"] = dict(items[-20000:])
    state["fingerprints"] = [fp for fp in state["fingerprints"] if fp.get("ts", "") >= cutoff_fp]

    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


# --- news_data.json (dashboard'in okudugu kalici store) -------------------
def load_news_data():
    if not os.path.exists(NEWS_DATA_FILE):
        return []
    try:
        with open(NEWS_DATA_FILE) as f:
            data = json.load(f)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def save_news_data(items):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=NEWS_DATA_KEEP_DAYS)).isoformat()
    items = [it for it in items if it.get("ts", "") >= cutoff]
    items.sort(key=lambda it: it.get("ts", ""), reverse=True)
    items = items[:NEWS_DATA_MAX_ITEMS]
    tmp = NEWS_DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, NEWS_DATA_FILE)


# --- Normalize / hash / similarity ----------------------------------------
_STOPWORDS = {
    "ve", "ile", "icin", "bir", "bu", "su", "o", "da", "de", "ki", "mi", "mu",
    "the", "a", "an", "of", "in", "on", "to", "for", "and", "or", "but", "is", "are",
    "olarak", "var", "yok", "oldu", "olacak", "diye", "ise", "ama", "fakat",
    "son", "dakika", "sondakika", "haber", "haberler", "ozel", "flas", "aciklama",
}


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\s+[-–|]\s+[^-–|]{2,40}\s*$", "", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def tokenize(title: str) -> set:
    norm = normalize_title(title)
    return {w for w in norm.split() if len(w) >= 3 and w not in _STOPWORDS}


def link_hash(link: str, title: str) -> str:
    return hashlib.md5(f"{link}|{title}".encode()).hexdigest()


def title_hash(title: str) -> str:
    return "t:" + hashlib.md5(normalize_title(title)[:120].encode()).hexdigest()


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_duplicate(title: str, link: str, state) -> bool:
    if link_hash(link, title) in state["ids"]:
        return True
    if title_hash(title) in state["ids"]:
        return True
    tokens = tokenize(title)
    if not tokens:
        return False
    for fp in state["fingerprints"]:
        if jaccard(tokens, set(fp["tokens"])) >= SIMILARITY_THRESHOLD:
            return True
    return False


# --- Toplama ---------------------------------------------------------------
def collect_candidates(state):
    seen_in_batch = set()
    candidates = []
    for feed_meta in all_feeds():
        url = feed_meta["url"]
        label = feed_meta["label"]
        default_lean = feed_meta["default_lean"]
        try:
            feed = feedparser.parse(url, agent=USER_AGENT)
            if not feed.entries:
                log(f"UYARI: 0 entry — {label}")
                continue
            for entry in feed.entries[:PER_FEED_LIMIT]:
                title = (entry.get("title") or "").strip()
                link  = entry.get("link") or ""
                summary_raw = re.sub(r"<[^>]+>", " ", entry.get("summary") or "")[:400]
                if not title or not link:
                    continue
                # Google News bridge: baslik sonundaki "— Kaynak" ekini kirp
                clean_title = re.sub(r"\s[-–|]\s[^-–|]{2,40}\s*$", "", title).strip()

                t_hash = title_hash(clean_title)
                if t_hash in seen_in_batch:
                    continue
                seen_in_batch.add(t_hash)

                if is_duplicate(clean_title, link, state):
                    continue

                candidates.append({
                    "title":         clean_title,
                    "link":          link,
                    "raw_summary":   summary_raw,
                    "source":        label,
                    "default_lean":  default_lean,
                    "tokens":        tokenize(clean_title),
                })
        except Exception as e:
            log(f"HATA ({label}): {e}")
    return candidates


# --- LLM filtre + ozet + lean ---------------------------------------------
LLM_SYSTEM = """Sen Turk bir karar vericinin haber editorusun. COK SECICI ol — kullanici sadece gercekten kritik haberleri istiyor. Tipik bir saatte 5-15 baslik gecmeli; daha fazlasi gurultudur.

ONEMLI (keep=true) — SADECE bu kategorilerden SOMUT gelisme/karar/data:
- Turk siyaseti SOMUT karar: TBMM oylamasi, kabine karari, parti genel baskanindan onemli aciklama/karar, ust duzey atama/istifa, mahkeme karari (Anayasa Mahkemesi/Danistay/Yargitay/YSK), Sayistay raporu
- Turkiye ekonomi VERISI veya KARARI: TCMB faiz karari, enflasyon/issizlik/buyume verisi yayini, Hazine ihale sonucu, butce-vergi degisikligi, ciddi kur hareketi (>%2 gunluk)
- Kuresel siyaset SOMUT: ABD/AB/Rusya/Cin/Israil/Iran liderlerinden karar/aciklama, savas-ateskes gelismesi, yaptirim karari, NATO/BM Guvenlik Konseyi karari, secim sonucu
- Kuresel ekonomi SOMUT: Fed/ECB faiz karari, ABD CPI/jobs data, BoJ karari, kritik emtia hareketi, sistemik finansal olay

ONEMSIZ (keep=false):
- Magazin, spor, kaza/asayis, hava durumu, kultur-sanat
- Genel kose yazisi/fikir/yorum/analiz (ISTISNA asagida)
- "Aciklama yapacak", "ele alacak" gibi gelecek zamanli muphem haberler
- Tekrarlayan/turev basliklar, sirket PR, urun lansmani, gunluk piyasa kapanisi

ISTISNA — bu iki anahtardan biri baslikta/ozette geciyorsa keep=true (kose yazisi olsa bile):
- "DEVA Partisi" VEYA "Ali Babacan"

SCORE skalasi (1-10):
- 9-10: Cumhurbaskani/parti lideri kararlari, TCMB faiz, Fed faiz, savas/kriz gelismesi
- 7-8: Onemli yasa/karar, kabine atamasi, kritik veri (CPI, buyume), AYM kararlari
- 6: Onemli ama ikinci derece
- 1-5: Sinirda, gondermeyecegim (keep=false yap)

OZET (summary_tr): keep=true ise haberi 1-2 Turkce cumlede ozetle (max 240 karakter). Spesifik ol; "aciklama yapildi" gibi mubhem ifadeler kullanma — KIM, NE yapti/karar verdi yaz.

LEAN: keep=true ise haberin/kaynagin siyasi yonelimi: "left" / "neutral" / "right". Kaynak ipucu sana verilecek (default_lean) ama icerik farkli bir yon gosteriyorsa override et. Reuters/AP/BBC/DW gibi uluslararasi servisler neutral'dir; haber Turk hukumetini destekleyici dille anlatiyorsa right, elestiriyorsa left dusunulebilir. Emin degilsen neutral.

Cikti format'i ZORUNLU: yalniz gecerli JSON array. Her item:
{"i": <int>, "keep": <bool>, "score": <1-10 int>, "summary_tr": "...", "lean": "left|neutral|right"}
keep=false ise summary_tr ve lean atlanabilir. Aciklama veya markdown yazma."""


def llm_filter(candidates):
    if not ANTHROPIC_API_KEY:
        log("ANTHROPIC_API_KEY yok — LLM filtresi devre disi.")
        return []
    if not candidates:
        return []

    pool = candidates[:MAX_LLM_CANDIDATES]
    items_text = "\n".join(
        f"[{i}] ({c['source']}, default_lean={c['default_lean']}) {c['title']}"
        + (f" — {c['raw_summary'][:140]}" if c['raw_summary'] else "")
        for i, c in enumerate(pool)
    )

    body = {
        "model": LLM_MODEL,
        "max_tokens": 6000,
        "system": LLM_SYSTEM,
        "messages": [{"role": "user", "content": f"Asagidaki {len(pool)} basligi degerlendir:\n\n{items_text}\n\nSadece JSON array dondur."}],
    }
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=90,
        )
        if not r.ok:
            log(f"LLM HTTP hata: {r.status_code} — {r.text[:200]}")
            return []
        data = r.json()
        text = "".join(blk.get("text", "") for blk in data.get("content", []) if blk.get("type") == "text").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        m = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not m:
            log(f"LLM cikti JSON degil: {text[:200]}")
            return []
        verdicts = json.loads(m.group(0))
    except Exception as e:
        log(f"LLM hata: {e}")
        return []

    keepers = []
    for v in verdicts:
        try:
            i = int(v["i"])
            if not v.get("keep"):
                continue
            if not (0 <= i < len(pool)):
                continue
            score = int(v.get("score", 5))
            if score < MIN_SCORE:
                continue
            item = dict(pool[i])
            item["score"] = score
            item["summary_tr"] = (v.get("summary_tr") or "").strip()[:300]
            lean = (v.get("lean") or item["default_lean"]).lower()
            if lean not in VALID_LEANS:
                lean = item["default_lean"]
            item["lean"] = lean
            keepers.append(item)
        except Exception:
            continue
    keepers.sort(key=lambda x: -x["score"])
    return keepers[:MAX_DELIVER]


# --- Clustering ------------------------------------------------------------
CLUSTER_THRESHOLD = 0.40  # jaccard >= bu -> ayni cluster


def cluster_keepers(keepers):
    """Run-scope clustering. Donus: list[{id, tokens, members[]}]."""
    clusters = []
    for item in keepers:
        tokens = item["tokens"]
        matched = None
        for c in clusters:
            if jaccard(tokens, c["tokens"]) >= CLUSTER_THRESHOLD:
                matched = c
                break
        if matched:
            matched["members"].append(item)
            # Temsilci: en yuksek skorlu uyenin token'lari
            if item["score"] > matched["best_score"]:
                matched["best_score"] = item["score"]
                matched["tokens"] = tokens
        else:
            # Deterministik id: sorted tokens hash'i
            cid = "c" + hashlib.md5("|".join(sorted(tokens)).encode()).hexdigest()[:12]
            clusters.append({"id": cid, "tokens": tokens, "best_score": item["score"], "members": [item]})

    for c in clusters:
        # En yuksek skor en basa
        c["members"].sort(key=lambda m: -m["score"])
        for m in c["members"]:
            m["cluster_id"] = c["id"]
    return clusters


# --- Telegram -------------------------------------------------------------
LEAN_EMOJI = {"left": "🟥", "neutral": "⬜", "right": "🟦"}


def send_telegram(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=15)
    return r.ok, r.text


def topic_emoji(title: str, summary: str) -> str:
    blob = (title + " " + summary).lower()
    if any(k in blob for k in ["fed", "ecb", "imf", "nato", "putin", "trump", "xi ", "netanyahu", "iran", "ukrayna", "gazze", "israil", "white house", "ab ", "sanctions", "yaptirim"]):
        return "🌍"
    if any(k in blob for k in ["faiz", "enflasyon", "tcmb", "dolar", "euro", "borsa", "bist", "tahvil", "butce", "hazine", "maliye", "vergi", "buyume", "issizlik", "petrol", "brent", "altin"]):
        return "💰"
    return "🏛️"


def deliver_cluster(cluster) -> bool:
    """Cluster basina tek Telegram mesaji — en yuksek skorlu uyenin metni + diger kaynaklarin sayisi."""
    top = cluster["members"][0]
    icon = topic_emoji(top["title"], top.get("summary_tr", ""))
    lean_dot = LEAN_EMOJI.get(top["lean"], "⬜")
    summary_line = f"\n<i>{top['summary_tr']}</i>" if top.get("summary_tr") else ""
    coverage = ""
    if len(cluster["members"]) > 1:
        others = [m["source"] for m in cluster["members"][1:6]]
        coverage = f"\n<i>+ {len(cluster['members']) - 1} kaynak daha: {', '.join(others)}</i>"
    msg = (
        f"<b>{icon} {top['source']}</b> {lean_dot}\n"
        f"{top['title']}{summary_line}{coverage}\n"
        f"<a href='{top['link']}'>→ Habere git</a>"
    )
    delivered = False
    for target in [CHAT_ID, CHANNEL_ID]:
        if not target:
            continue
        ok, resp = send_telegram(target, msg)
        if ok:
            delivered = True
        else:
            log(f"Telegram hatasi ({target}): {resp[:160]}")
        time.sleep(SEND_DELAY_S)
    return delivered


# --- Yardimci --------------------------------------------------------------
def get_chat_id():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url, timeout=10)
    data = r.json()
    if not data.get("result"):
        print("Henuz guncelleme yok.")
        return
    for update in data["result"]:
        msg = update.get("message") or update.get("channel_post")
        if msg:
            chat = msg["chat"]
            print(f"Chat ID: {chat['id']}  (Tip: {chat['type']}, Ad: {chat.get('first_name') or chat.get('title', '')})")


# --- Main ------------------------------------------------------------------
def main():
    if "--get-chat-id" in sys.argv:
        get_chat_id()
        return

    if not TELEGRAM_TOKEN or not (CHAT_ID or CHANNEL_ID):
        log("Eksik env: TELEGRAM_TOKEN ve en az bir hedef gerekli.")
        return

    tr_now = datetime.now(TURKEY_TZ)
    quiet = tr_now.hour in QUIET_HOURS
    if quiet:
        log(f"Sessiz saat ({tr_now.strftime('%H:%M')} TR) — Telegram atlandi, web'e kaydedilecek.")

    state = load_state()
    candidates = collect_candidates(state)
    log(f"{len(candidates)} aday baslik toplandi (dedup sonrasi).")

    if not candidates:
        save_state(state)
        return

    keepers = llm_filter(candidates)
    clusters = cluster_keepers(keepers)
    log(f"LLM {len(keepers)} basligi onemli buldu -> {len(clusters)} cluster.")

    news_data = load_news_data()
    now_iso = datetime.now(timezone.utc).isoformat()
    telegram_sent = 0
    web_recorded = 0
    for cluster in clusters:
        if not quiet:
            if deliver_cluster(cluster):
                telegram_sent += 1

        # Web: cluster icindeki tum uyeleri (her kaynak ayri kart)
        for m in cluster["members"]:
            web_recorded += 1
            state["ids"][link_hash(m["link"], m["title"])] = now_iso
            state["ids"][title_hash(m["title"])] = now_iso
            news_data.append({
                "title":      m["title"],
                "link":       m["link"],
                "source":     m["source"],
                "summary_tr": m.get("summary_tr", ""),
                "lean":       m["lean"],
                "score":      m["score"],
                "ts":         now_iso,
                "cluster_id": cluster["id"],
            })

        # Cluster basina tek fingerprint -> cross-run dedup
        state["fingerprints"].append({
            "tokens":     sorted(cluster["tokens"]),
            "ts":         now_iso,
            "title":      cluster["members"][0]["title"][:100],
            "cluster_id": cluster["id"],
        })

    # Gonderilmeyen tum candidate'lari da state'e isaretle (LLM bir daha bakmasin)
    for c in candidates:
        state["ids"].setdefault(link_hash(c["link"], c["title"]), now_iso)
        state["ids"].setdefault(title_hash(c["title"]), now_iso)

    save_state(state)
    save_news_data(news_data)
    log(f"Telegram: {telegram_sent} cluster, web: {web_recorded} item. State: {len(state['ids'])} id / {len(state['fingerprints'])} fp. News store: {len(news_data)}.")


if __name__ == "__main__":
    main()
