#!/usr/bin/env python3
"""
Haber botu — siyaset + ekonomi odakli, LLM destekli onem filtresi.

Akis:
  1. Google News'in topic aramalari + birkac genis besleme uzerinden ham basliklar toplanir.
  2. Daha once gonderilmis basliklar (link hash + normalize edilmis baslik hash + 30 gunluk
     token-overlap benzerligi) elenir.
  3. Kalan adaylar tek bir Claude API cagrisiyla degerlendirilir; sadece "onemli" olanlar gecer.
  4. Hem kullanicinin DM'ine (CHAT_ID) hem de varsa public kanala (CHANNEL_ID) gonderilir.
  5. State dosyasi (sent_ids.json) yeni schema'ya yazilir, eski liste format'i otomatik migrate edilir.
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
CHANNEL_ID         = os.environ.get("CHANNEL_ID", "").strip()  # ornek: "@can_haber_botu" veya "-1001234567890"
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL          = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")

_base      = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_base, "sent_ids.json")
LOG_FILE   = os.path.join(_base, "news_bot.log")

DEDUP_WINDOW_DAYS   = 30          # Bu kadar gun once gorulen basliklarla benzerlik karsilastirmasi yapilir
SIMILARITY_THRESHOLD = 0.55       # Jaccard token overlap esigi (>= bu => ayni haber)
MAX_LLM_CANDIDATES   = 120        # Tek calismada LLM'e gidecek maksimum baslik (maliyet sapkasi)
PER_FEED_LIMIT       = 20         # Her beslemeden cekilecek maksimum baslik
MIN_SCORE            = 6          # LLM score >= bu => gonder (1-10 olcek)
MAX_DELIVER          = 15         # Tek run'da maksimum gonderim (top score'a gore)
SEND_DELAY_S         = 3.5        # Mesajlar arasi bekleme (Telegram kanal rate limit'i icin)

# --- Kaynaklar -------------------------------------------------------------
# Google News'in topic/arama RSS'leri "kendi tarayicimiz" rolunu ustlenir;
# yuzlerce yerli/yabanci kaynagi tek bir RSS'ten toplar.
def _gn(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "tr",
        "gl": "TR",
        "ceid": "TR:tr",
    })

FEEDS = [
    # Yurt ici siyaset
    _gn('("TBMM" OR "Meclis" OR "milletvekili" OR "Cumhurbaskani" OR "bakan" OR "kabine") when:1d'),
    _gn('("CHP" OR "AKP" OR "MHP" OR "DEM Parti" OR "IYI Parti" OR "Yeniden Refah" OR "Zafer Partisi" OR "Gelecek Partisi" OR "DEVA Partisi" OR "Saadet Partisi") when:1d'),
    _gn('("Erdogan" OR "Ozgur Ozel" OR "Devlet Bahceli" OR "Babacan" OR "Davutoglu" OR "Imamoglu" OR "Mansur Yavas" OR "Ozdag" OR "Erbakan" OR "Bakirhan" OR "Hatimogullari") when:1d'),
    _gn('("kamu kurumu" OR "Sayistay" OR "Anayasa Mahkemesi" OR "YSK" OR "Danistay" OR "Yargitay" OR "HSK") when:1d'),

    # Yurt ici ekonomi
    _gn('("Merkez Bankasi" OR "TCMB" OR "faiz karari" OR "enflasyon" OR "TUIK" OR "isgucu" OR "issizlik" OR "buyume" OR "cari acik" OR "butce") when:1d'),
    _gn('("dolar" OR "euro" OR "kur" OR "borsa Istanbul" OR "BIST" OR "tahvil" OR "Hazine" OR "Maliye Bakanligi" OR "vergi") when:1d'),

    # Kuresel ekonomi
    _gn('("Federal Reserve" OR "Fed faiz" OR "ECB" OR "IMF" OR "World Bank" OR "OECD" OR "global recession" OR "S&P 500" OR "oil price" OR "Brent") when:1d'),

    # Kuresel siyaset
    _gn('("Beyaz Saray" OR "White House" OR "NATO" OR "AB" OR "European Union" OR "BM Guvenlik Konseyi" OR "yaptirim" OR "sanctions" OR "Putin" OR "Trump" OR "Xi Jinping" OR "Netanyahu") when:1d'),
    _gn('("Ortadogu" OR "Israil" OR "Gazze" OR "Iran" OR "Ukrayna" OR "Rusya savas" OR "Suriye" OR "Cin Tayvan") when:1d'),

    # Ek kapsama icin birkac genis besleme (filtre LLM'de)
    "https://www.aa.com.tr/tr/rss/default?cat=guncel",
    "https://feeds.bbci.co.uk/turkish/rss.xml",
    "https://www.bloomberght.com/rss",
]


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# --- State (id'ler + fingerprint'ler) -------------------------------------
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

    # Eski liste format'i: tum hash'leri "ancient" timestamp ile migrate et
    if isinstance(data, list):
        ancient = "1970-01-01T00:00:00+00:00"
        return {"version": 2, "ids": {h: ancient for h in data}, "fingerprints": []}

    if not isinstance(data, dict) or "ids" not in data:
        return _empty_state()
    data.setdefault("fingerprints", [])
    return data


def save_state(state):
    # Eski kayitlari budama: ids 60 gun, fingerprint DEDUP_WINDOW_DAYS gun
    cutoff_ids = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    cutoff_fp  = (datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)).isoformat()
    state["ids"] = {
        h: ts for h, ts in state["ids"].items()
        if ts >= cutoff_ids or ts.startswith("1970")  # ancient'lari koru
    }
    # ancient olanlardan en eskileri at, toplam 20000'i gecmesin
    if len(state["ids"]) > 20000:
        items = sorted(state["ids"].items(), key=lambda kv: kv[1])
        state["ids"] = dict(items[-20000:])
    state["fingerprints"] = [fp for fp in state["fingerprints"] if fp.get("ts", "") >= cutoff_fp]

    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


# --- Normalize / hash / similarity ----------------------------------------
_STOPWORDS = {
    "ve", "ile", "icin", "bir", "bu", "su", "o", "da", "de", "ki", "mi", "mu",
    "the", "a", "an", "of", "in", "on", "to", "for", "and", "or", "but", "is", "are",
    "olarak", "var", "yok", "oldu", "olacak", "diye", "ise", "ama", "fakat",
    "son", "dakika", "sondakika", "haber", "haberler", "ozel", "flas", "aciklama",
}


def normalize_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"\s+[-–|]\s+[^-–|]{2,40}\s*$", "", t)  # Google News "- Kaynak" eki
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


# --- Toplama --------------------------------------------------------------
def collect_candidates(state):
    seen_in_batch = set()  # ayni run'da farkli feedlerden gelen kopyalari at
    candidates = []
    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                log(f"UYARI: 0 entry — {url[:80]}")
                continue
            for entry in feed.entries[:PER_FEED_LIMIT]:
                title = (entry.get("title") or "").strip()
                link  = entry.get("link") or ""
                summary = re.sub(r"<[^>]+>", " ", entry.get("summary") or "")[:300]
                if not title or not link:
                    continue

                # Google News'in "Baslik - Kaynak" formatindan kaynak adini cek
                src_match = re.search(r"\s[-–|]\s([^-–|]{2,40})\s*$", title)
                source = src_match.group(1).strip() if src_match else "—"
                clean_title = re.sub(r"\s[-–|]\s[^-–|]{2,40}\s*$", "", title).strip()

                # In-run kopya
                t_hash = title_hash(clean_title)
                if t_hash in seen_in_batch:
                    continue
                seen_in_batch.add(t_hash)

                # State'e gore kopya
                if is_duplicate(clean_title, link, state):
                    continue

                candidates.append({
                    "title":   clean_title,
                    "link":    link,
                    "summary": summary,
                    "source":  source,
                    "tokens":  tokenize(clean_title),
                })
        except Exception as e:
            log(f"HATA ({url[:60]}): {e}")
    return candidates


# --- LLM onem filtresi ----------------------------------------------------
LLM_SYSTEM = """Sen Turk bir karar vericinin haber editorusun. COK SECICI ol — kullanici sadece gercekten kritik haberleri istiyor. Tipik bir saatte 5-15 baslik gecmeli; daha fazlasi gurultudur. Comert davranma.

ONEMLI (true) — SADECE bu kategorilerden SOMUT gelisme/karar/data:
- Turk siyaseti SOMUT karar: TBMM oylamasi, kabine karari, parti genel baskanindan onemli aciklama/karar, ust duzey atama/istifa, mahkeme karari (Anayasa Mahkemesi/Danistay/Yargitay/YSK), Sayistay raporu
- Turkiye ekonomi VERISI veya KARARI: TCMB faiz karari, enflasyon/issizlik/buyume verisi yayini, Hazine ihale sonucu, butce-vergi degisikligi, ciddi kur hareketi (>%2 gunluk)
- Kuresel siyaset SOMUT: ABD/AB/Rusya/Cin/Israil/Iran liderlerinden karar/aciklama, savas-ateskes gelismesi, yaptirim karari, NATO/BM Guvenlik Konseyi karari, secim sonucu
- Kuresel ekonomi SOMUT: Fed/ECB faiz karari, ABD CPI/jobs data, BoJ karari, kritik emtia hareketi, sistemik finansal olay

ONEMSIZ (false) — sik yapilan hatalar:
- Magazin, spor, kaza/asayis, hava durumu, kultur-sanat
- Genel kose yazisi/fikir/yorum/analiz
- "Aciklama yapacak", "aciklama bekleniyor", "ele alacak", "gorusecek", "degerlendirecek" gibi GELECEK ZAMANLI muphem haberler — somut karar/sonuc olmadan
- Tekrarlayan/turev basliklar, ayni olayin n'inci versiyonu
- Sirket PR/halkla iliskiler, urun lansmani
- Anket sonucu degil de "anket aciklanacak" gibi metahaberler
- Gunluk normal piyasa kapanisi (sadece anormal hareketler onemli)
- Yerel olay/asayis, kucuk capli haber

ISTISNA — sadece su iki anahtar bir baslik/ozette geciyorsa keep=true (kose yazisi/yorum olsa bile):
- "DEVA Partisi" VEYA "Ali Babacan"

SCORE skalasi (1-10):
- 9-10: Cumhurbaskani/parti lideri kararlari, TCMB faiz, Fed faiz, savas/kriz gelismesi
- 7-8: Onemli yasa/karar, kabine atamasi, kritik veri (CPI, buyume), AYM kararlari
- 6: Onemli ama ikinci derece (yaptirim, lider aciklamasi, anket sonucu)
- 1-5: Sinirda, gondermeyecegim (keep=false yap)

Cikti format'i ZORUNLU: yalniz gecerli JSON array. Her item: {"i": <baslik_index_int>, "keep": <bool>, "score": <1-10 int>}. score yalniz keep=true ise anlamli. Aciklama yazma."""


def llm_filter(candidates):
    if not ANTHROPIC_API_KEY:
        log("ANTHROPIC_API_KEY yok — LLM filtresi devre disi, hicbir haber gonderilmiyor.")
        return []
    if not candidates:
        return []

    # Maliyet sapkasi
    pool = candidates[:MAX_LLM_CANDIDATES]
    items = "\n".join(
        f"[{i}] {c['title']}" + (f" — {c['summary'][:140]}" if c['summary'] else "")
        for i, c in enumerate(pool)
    )

    body = {
        "model": LLM_MODEL,
        "max_tokens": 4000,
        "system": LLM_SYSTEM,
        "messages": [{"role": "user", "content": f"Asagidaki {len(pool)} basligi degerlendir:\n\n{items}\n\nSadece JSON array dondur."}],
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
            timeout=60,
        )
        if not r.ok:
            log(f"LLM HTTP hata: {r.status_code} — {r.text[:200]}")
            return []
        data = r.json()
        text = "".join(blk.get("text", "") for blk in data.get("content", []) if blk.get("type") == "text").strip()
        # Olasi markdown/code fence temizle
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        # Ilk array'i yakala
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
            if 0 <= i < len(pool):
                item = dict(pool[i])
                item["score"] = int(v.get("score", 5))
                if item["score"] < MIN_SCORE:
                    continue
                keepers.append(item)
        except Exception:
            continue
    keepers.sort(key=lambda x: -x["score"])
    return keepers[:MAX_DELIVER]


# --- Telegram -------------------------------------------------------------
def send_telegram(chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, timeout=15)
    return r.ok, r.text


def emoji_for(title: str, summary: str) -> str:
    blob = (title + " " + summary).lower()
    if any(k in blob for k in ["fed", "ecb", "imf", "nato", "putin", "trump", "xi ", "netanyahu", "iran", "ukrayna", "gazze", "israil", "white house", "ab ", "sanctions", "yaptirim"]):
        return "🌍"
    if any(k in blob for k in ["faiz", "enflasyon", "tcmb", "dolar", "euro", "borsa", "bist", "tahvil", "butce", "hazine", "maliye", "vergi", "buyume", "issizlik", "petrol", "brent", "altin"]):
        return "💰"
    return "🏛️"


def deliver(item) -> bool:
    icon = emoji_for(item["title"], item["summary"])
    msg = (
        f"<b>{icon} {item['source']}</b>\n"
        f"{item['title']}\n"
        f"<a href='{item['link']}'>→ Habere git</a>"
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


# --- Yardimci komutlar ---------------------------------------------------
def get_chat_id():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url, timeout=10)
    data = r.json()
    if not data.get("result"):
        print("Henuz guncelleme yok. Bota mesaj atin ve tekrar deneyin.")
        return
    for update in data["result"]:
        msg = update.get("message") or update.get("channel_post")
        if msg:
            chat = msg["chat"]
            print(f"Chat ID: {chat['id']}  (Tip: {chat['type']}, Ad: {chat.get('first_name') or chat.get('title', '')})")


# --- Main ----------------------------------------------------------------
def main():
    if "--get-chat-id" in sys.argv:
        get_chat_id()
        return

    if not TELEGRAM_TOKEN or not (CHAT_ID or CHANNEL_ID):
        log("Eksik env: TELEGRAM_TOKEN ve en az bir hedef (CHAT_ID veya CHANNEL_ID) gerekli.")
        return

    state = load_state()
    candidates = collect_candidates(state)
    log(f"{len(candidates)} aday baslik toplandi (dedup sonrasi).")

    if not candidates:
        save_state(state)
        return

    keepers = llm_filter(candidates)
    log(f"LLM {len(keepers)} basligi onemli buldu.")

    now_iso = datetime.now(timezone.utc).isoformat()
    for item in keepers:
        if is_duplicate(item["title"], item["link"], state):
            continue
        if deliver(item):
            state["ids"][link_hash(item["link"], item["title"])] = now_iso
            state["ids"][title_hash(item["title"])] = now_iso
            state["fingerprints"].append({
                "tokens": sorted(item["tokens"]),
                "ts": now_iso,
                "title": item["title"][:100],
            })

    # Gonderilmeyen adaylarin baslik hashleri de state'e — bir sonraki turda LLM'e tekrar gitmesin
    for c in candidates:
        state["ids"].setdefault(link_hash(c["link"], c["title"]), now_iso)
        state["ids"].setdefault(title_hash(c["title"]), now_iso)

    save_state(state)
    log(f"Gonderildi: {sum(1 for _ in keepers)}. Toplam state: {len(state['ids'])} id / {len(state['fingerprints'])} fingerprint.")


if __name__ == "__main__":
    main()
