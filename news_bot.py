#!/usr/bin/env python3
"""
Haber botu — sabit kaynak listesi + admin'in panelden eklediği custom RSS'ler.

Akis:
  1. SOURCES + custom_feeds.json'daki her kaynak icin RSS okunur.
  2. Daha once gonderilmis basliklar (link hash + normalize edilmis baslik hash + 30 gunluk
     token-overlap benzerligi) elenir.
  3. Kalan adaylar tek bir Claude API cagrisiyla degerlendirilir; her keep=true item icin
     LLM kisa ozet + siyasi yon (left/neutral/right) doner. LLM'e son 48 saatin olaylari da
     verilir: her haber mevcut bir olaya baglanir (yeni gelisme mi, tekrar mi) ya da yeni olay acar.
  4. Katmanli bildirim (DM + kanal ayni kural): 9-10 puan aninda "son dakika", 7-8 puan
     3 saatlik ozet mesajina (olay basina tek satir), 6 puan sadece web. Ayni olayin
     tekrari bildirim uretmez.
  5. news_data.json'a kaydedilir (dashboard buradan okur), state dosyasi (sent_ids.json) yazilir.
"""

import feedparser
import requests
import json
import os
import re
import hashlib
import html
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

_base             = os.path.dirname(os.path.abspath(__file__))


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

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID            = os.environ.get("CHAT_ID", "")
CHANNEL_ID         = os.environ.get("CHANNEL_ID", "").strip()
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
# Haber filtresi (puan + olay eslestirme) muhakeme ister -> Sonnet 5. Resmi veri cikarimi mekanik -> Haiku.
LLM_MODEL          = os.environ.get("LLM_MODEL", "claude-sonnet-5")
# Sonnet 5 / Opus icin dusunme derinligi (low/medium/high). Haiku 4.5 effort'u desteklemez -> bos birak.
LLM_EFFORT         = os.environ.get("LLM_EFFORT", "low").strip()
DATA_LLM_MODEL     = os.environ.get("DATA_LLM_MODEL", "claude-haiku-4-5")

STATE_FILE        = os.path.join(_base, "sent_ids.json")
NEWS_DATA_FILE    = os.path.join(_base, "news_data.json")
CUSTOM_FEEDS_FILE = os.path.join(_base, "custom_feeds.json")
DATA_STATE_FILE   = os.path.join(_base, "data_state.json")
LOG_FILE          = os.path.join(_base, "news_bot.log")

DEDUP_WINDOW_DAYS    = 30
SIMILARITY_THRESHOLD = 0.55
MAX_LLM_CANDIDATES   = 180
PER_FEED_LIMIT       = 10
MIN_SCORE            = 6    # web/dashboard'a kayit esigi (6 = sadece arsiv, bildirim yok)
MAX_KEEP             = 25   # run basina web'e kaydedilecek max haber

# Katmanli bildirim: 9-10 aninda, 7-8 3 saatlik ozette, 6 sadece web.
URGENT_SCORE         = 9
DIGEST_SCORE         = 7
MAX_URGENT_PER_DAY   = 6    # asilirsa urgent'lar da ozete duser
DIGEST_HOURS         = (0, 9, 12, 15, 18, 21)  # TR saati, ozet slotlari
DIGEST_MAX_ITEMS     = 8
DIGEST_SILENT        = True # ozet sessiz bildirimle gelsin (son dakika sesli)
PENDING_MAX_AGE_H    = 9    # ozete girmeyi bekleyen haber en fazla bu kadar bekler
TG_MAX_CHARS         = 3900 # Telegram limiti 4096 (gorunen metin)

# Olay hafizasi: son 48 saatte islenmis olaylar LLM'e verilir; ayni olayin
# tekrari bildirim uretmez, sadece somut yeni gelisme uretir.
STORY_MEMORY_HOURS   = 48
STORY_PROMPT_LIMIT   = 80
URGENT_RESEND_GAP_H  = 1    # ayni olay icin iki son dakika arasi min sure
HOT_STORY_COUNT      = 10   # 48 saatte bu kadar haber -> ozette "🔥 Gundem" etiketi
MAX_DATA_CANDIDATES  = 60
DATA_PER_FEED_LIMIT  = 8
MAX_DATA_DELIVER     = 8
DATA_LINK_KEEP_DAYS  = 14   # ayni resmi makaleyi tekrar LLM'e gondermemek icin
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


# Resmi/guvenilir veri kaynaklari (ayri kanal). Konu-odakli haber suzgecinden bagimsiz
# ikinci bir LLM pass'inde yapilandirilmis veri (gosterge -> deger -> donem) cikarilir.
# NOT: TUIK/TCMB siteleri JS-render + RSS rakam icermiyor; bu yuzden resmi rakami KOTALAYAN
# haber kapsamini izliyoruz (veri resmi kurumun verisi, link ise haber). source_tr daima
# resmi kurum olur (TUIK/TCMB/OECD/Eurostat/bakanlik), haberi yapan gazete degil.
# OECD/Eurostat: yalniz TURKIYE'yi iceren/Turkiye ile ilgili veri (kullanicinin istegi).
DATA_SOURCES = [
    # TÜİK — en onemli veriler (enflasyon, isgucu, buyume, dis ticaret). Haber bulteni rakamlari.
    {"url": _gn('TÜİK (enflasyon OR TÜFE OR ÜFE OR "kira artış oranı") when:10d'),                          "label": "TÜİK"},
    {"url": _gn('TÜİK (işsizlik OR işgücü OR büyüme OR GSYH OR "milli gelir") when:16d'),                   "label": "TÜİK"},
    {"url": _gn('TÜİK ("dış ticaret" OR ihracat OR ithalat OR "cari açık" OR "sanayi üretim") when:16d'),   "label": "TÜİK"},
    # TCMB — faiz / para politikasi (rakam haberde net)
    {"url": _gn('TCMB ("politika faizi" OR "faiz kararı" OR "para politikası" OR rezerv OR "enflasyon raporu") when:12d'), "label": "TCMB"},
    # Bakanliklar — butce / dis ticaret
    {"url": _gn('(Hazine OR "Ticaret Bakanlığı" OR "Mehmet Şimşek") (bütçe OR "bütçe açığı" OR "dış ticaret" OR ihracat OR ithalat) when:14d'), "label": "Hazine/Ticaret Bakanlığı"},
    # OECD / Eurostat — YALNIZ Turkiye'yi iceren veriler
    {"url": _gn('OECD (Turkey OR Türkiye) (GDP OR inflation OR unemployment OR growth OR forecast OR outlook) when:21d', "en"), "label": "OECD"},
    {"url": _gn('Eurostat (Turkey OR Türkiye) (inflation OR GDP OR unemployment OR prices OR trade) when:21d', "en"),          "label": "Eurostat"},
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
    return {"version": 2, "ids": {}, "fingerprints": [], "stories": [], "pending": [],
            "story_seq": 0, "urgent_log": [], "last_digest_ts": ""}


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
    for k, v in _empty_state().items():
        data.setdefault(k, v)
    return data


def save_state(state):
    cutoff_ids = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    cutoff_fp  = (datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)).isoformat()
    state["ids"] = {h: ts for h, ts in state["ids"].items() if ts >= cutoff_ids or ts.startswith("1970")}
    if len(state["ids"]) > 20000:
        items = sorted(state["ids"].items(), key=lambda kv: kv[1])
        state["ids"] = dict(items[-20000:])
    state["fingerprints"] = [fp for fp in state["fingerprints"] if fp.get("ts", "") >= cutoff_fp]
    now = datetime.now(timezone.utc)
    cutoff_story = (now - timedelta(hours=STORY_MEMORY_HOURS)).isoformat()
    cutoff_pend  = (now - timedelta(hours=PENDING_MAX_AGE_H)).isoformat()
    cutoff_urg   = (now - timedelta(hours=24)).isoformat()
    state["stories"]    = [s for s in state["stories"] if s.get("last_ts", "") >= cutoff_story]
    state["pending"]    = [p for p in state["pending"] if p.get("ts", "") >= cutoff_pend]
    state["urgent_log"] = [t for t in state["urgent_log"] if t >= cutoff_urg]

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


# --- data_state.json (resmi veri kanali: gosterge basina son gorulen deger) ---
def _empty_data_state():
    return {"version": 1, "indicators": {}, "seen_links": {}}


def load_data_state():
    if not os.path.exists(DATA_STATE_FILE):
        return _empty_data_state()
    try:
        with open(DATA_STATE_FILE) as f:
            data = json.load(f)
    except Exception:
        return _empty_data_state()
    if not isinstance(data, dict):
        return _empty_data_state()
    data.setdefault("version", 1)
    data.setdefault("indicators", {})
    data.setdefault("seen_links", {})
    return data


def save_data_state(ds):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DATA_LINK_KEEP_DAYS)).isoformat()
    ds["seen_links"] = {h: ts for h, ts in ds.get("seen_links", {}).items() if ts >= cutoff}
    tmp = DATA_STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(ds, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_STATE_FILE)


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
def fix_mojibake(text: str) -> str:
    """UTF-8'i latin-1 sanilarak bozulmus basliklari onar (or. TRT: 'BakanÄ±' -> 'Bakanı')."""
    if "Ã" not in text and "Ä" not in text and "Å" not in text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


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
                title = fix_mojibake(title)
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
LLM_SYSTEM = """Sen Turk bir karar vericinin haber editorusun. COK SECICI ol — kullanici sadece gercekten kritik haberleri istiyor ve bildirim yorgunlugu yasiyor.

PUAN = BILDIRIM KATMANI: 9-10 kullaniciya ANINDA (sesli) gider — gunde sadece birkac tane olmali. 7-8 uc saatlik ozet mesajina girer. 6 sadece arsive yazilir, bildirim olmaz. Puanlari SISIRME; emin degilsen bir alt puani ver.

ONCELIK: Kullanici EKONOMI haberlerine daha cok agirlik istiyor. Bir ekonomi haberi sinirdaysa keep=true lehine karar ver ve digerlerine gore daha comert puanla. Siyaset/diger alanlarda seciciligi koru.

OZEL ONCELIK — GECIM MALIYETI & SAYISAL KIYAS: Asagidakileri ONE CIKAR (keep=true lehine, +1 puan):
- Icinde "fiyat", "zam", "ucret", "asgari ucret", "oran", "enflasyon", "iflas", "kapanma/kapandi", "yuzde"/"%" gecen haberler
- Vatandasin gunluk gecim maliyetini dogrudan etkileyen haberler (akaryakit/dogalgaz/elektrik/su zam, gida fiyati, kira, faiz/kredi, maas/emekli ayligi, vergi/harc)
- Bir oncekiyle KIYASLANABILIR somut bir sayi iceren haberler (eski deger -> yeni deger)
RUTIN istisnasi yine gecerli: yatay/kucuk gunluk piyasa hareketi gurultudur; ama yukaridaki kategorilerde somut bir DEGISIM varsa gecir.

ONEMLI (keep=true) — SADECE bu kategorilerden SOMUT gelisme/karar/data:
- Turk siyaseti SOMUT karar: TBMM oylamasi, kabine karari, parti genel baskanindan onemli aciklama/karar, ust duzey atama/istifa, mahkeme karari (Anayasa Mahkemesi/Danistay/Yargitay/YSK), Sayistay raporu
- Turkiye ekonomi (GENIS tut — ekonomi ONCELIKLI alandir): TCMB faiz/politika karari, enflasyon/issizlik/buyume/cari acik verisi, Hazine ihale sonucu, butce-vergi-tesvik degisikligi, onemli ekonomi politikasi veya regulasyon, ust duzey ekonomi atamasi (Bakan/TCMB/BDDK/SPK), makro etkili sektor/sirket gelismesi (buyuk yatirim, iflas, satin alma, ihracat-enerji-banka kararlari), kayda deger kur/borsa/altin/faiz hareketi (>%2 gunluk veya rejim degisikligi)
- Kuresel siyaset SOMUT: ABD/AB/Rusya/Cin/Israil/Iran liderlerinden karar/aciklama, savas-ateskes gelismesi, yaptirim karari, NATO/BM Guvenlik Konseyi karari, secim sonucu
- Kuresel ekonomi (GENIS tut): Fed/ECB/BoJ faiz veya politika karari/sinyali, ABD CPI/jobs/buyume datasi, onemli ticaret-tarife karari, kritik emtia/petrol hareketi, sistemik finansal olay, kuresel piyasalari etkileyen buyuk sirket/sektor gelismesi

ONEMSIZ (keep=false):
- Magazin, spor, kaza/asayis, hava durumu, kultur-sanat
- Genel kose yazisi/fikir/yorum/analiz (ISTISNA asagida)
- "Aciklama yapacak", "ele alacak" gibi gelecek zamanli muphem haberler
- Tekrarlayan/turev basliklar, sirket PR, urun lansmani, RUTIN gunluk piyasa ozeti/kapanisi (kucuk/yatay hareket) — ANCAK sert hareket RUTIN DEGILDIR: borsa endeksi/kur/altin gunde >%2 hareket ettiyse veya sert dusus/yukselis varsa keep=true yap, "kapanis" basligi olsa bile

ISTISNA — bu iki anahtardan biri baslikta/ozette geciyorsa keep=true (kose yazisi olsa bile):
- "DEVA Partisi" VEYA "Ali Babacan"

SCORE skalasi (1-10):
- 9-10 (SON DAKIKA): TCMB/Fed/ECB faiz karari, savas/ateskes/buyuk saldiri, Cumhurbaskani veya ana muhalefet liderinin kritik KARARI, bakan/eski bakan/TCMB baskani duzeyinde atama-istifa-gorevden alma-tutuklama, erken secim/kapatma davasi gibi rejim etkili gelisme, piyasada sert kriz (kur/borsa gunluk >%3), buyuk afet, milyonlarca vatandasi/birikimi etkileyen SISTEMIK finansal kriz veya buyuk yolsuzluk skandalinin patlak vermesi.
- BUYUK OLAYLARDA KIRILMA ANI: Olay listesinde cok sayida haber/kaynak ile gorunen buyuk bir olayin (or. "48 saatte 40 haber, 9 kaynak") GIDISATINI DEGISTIREN adimi 9-10'dur: hukumet/Cumhurbaskani karari, magdurlara odeme/tazmin plani, ust duzey isimlerin (bakan, eski bakan, TCMB/kurum yoneticisi — ESKI yoneticiler DAHIL —, holding patronu) tutuklanmasi veya istifasi, yasal duzenleme. Ayni olayin RUTIN ayrintilari (yeni gozalti listesi, ifade detayi, "kimdir" yazisi, yorum/analiz, tepki aciklamasi) 9-10 DEGILDIR; bunlar 6-7'dir.
- NOT: Buyuk bir sorusturma/kriz kapsamindaki gozalti-tutuklama haberleri asayis haberi DEGILDIR; olayin parcasi olarak degerlendir.
- 7-8: Onemli yasa/karar, ust duzey atama, kritik veri (enflasyon, CPI, buyume), AYM/Danistay kararlari, onemli ekonomi gelismesi, gecim maliyetine dogrudan somut zam/degisim
- 6: Onemli ama ikinci derece (arsive gider)
- 1-5: Sinirda, gondermeyecegim (keep=false yap)

OLAY HAFIZASI (tekrari onlemek icin kritik): Sana son 48 saatte islenmis OLAYLARIN listesi verilecek ([s12] baslik — ozet). Olay = belirli somut bir vaka/karar zinciri (or. "fon sorusturmasi", "TCMB doviz donusum destegi degisikligi", "X sirketinin konkordatosu"). Her keep=true aday icin:
- story: Aday listedeki bir olayla AYNI vakayi anlatiyorsa o olayin id'si ("s12"). Degilse yeni olay etiketi "n1", "n2", ... — bu listede AYNI yeni vakayi anlatan TUM adaylara (farkli kaynak, farkli kelime olsa bile) AYNI n-etiketini ver.
- new_dev: story mevcut bir s-id ise: aday o olaya gore SOMUT YENI bir adim iceriyorsa true (gozalti -> tutuklama, aciklama -> karar, yeni rakam, yeni taraf/aktor). Ayni seyin baska kaynaktan/kelimelerle tekrari, yorum, arka plan, "kimdir/ne oldu" yazilari false. Yeni olaylar (n-etiketi) icin true.
- TEKRARI ELEME: Mevcut bir olayin baska kaynaktaki tekrari, olay onemliyse yine keep=true olsun (new_dev=false). Bildirim uretmez ama arsivde olayin kaynak cesitliligi olarak gorunur. Puanini olayin onemine gore ver.

OZET (summary_tr): keep=true ise haberi 1-2 Turkce cumlede ozetle (max 240 karakter). Spesifik ol; "aciklama yapildi" gibi mubhem ifadeler kullanma — KIM, NE yapti/karar verdi yaz.

METRIK (metric): Haberde bir oncekiyle KIYASLANABILIR sayisal degisim VARSA su formatta cikar: "[ne] [eski deger] -> [yeni deger] ([% veya puan degisim])". Ornek: "Asgari ucret 17.002 TL -> 22.104 TL (+%30)" veya "TUFE (yillik) %38,1 -> %41,6 (+3,5 puan)". Eski deger veya degisim haberde YOKSA bos birak (""). Tahmin etme, uydurma; sadece haberde acikca gecen rakamlari kullan.

LEAN: keep=true ise haberin/kaynagin siyasi yonelimi: "left" / "neutral" / "right". Kaynak ipucu sana verilecek (default_lean) ama icerik farkli bir yon gosteriyorsa override et. Reuters/AP/BBC/DW gibi uluslararasi servisler neutral'dir; haber Turk hukumetini destekleyici dille anlatiyorsa right, elestiriyorsa left dusunulebilir. Emin degilsen neutral.

Cikti format'i ZORUNLU: yalniz gecerli JSON array. Her item:
{"i": <int>, "keep": <bool>, "score": <1-10 int>, "story": "s12|n1", "new_dev": <bool>, "summary_tr": "...", "metric": "...", "lean": "left|neutral|right"}
keep=false ise story, new_dev, summary_tr, metric ve lean atlanabilir. metric yoksa "" birak. Aciklama veya markdown yazma."""


def _stories_prompt_block(stories) -> str:
    """Son olaylari LLM'e verilecek kisa listeye cevir (en yeni once)."""
    recent = sorted(stories, key=lambda s: s.get("last_ts", ""), reverse=True)[:STORY_PROMPT_LIMIT]
    if not recent:
        return "(henuz yok)"
    def size(s):
        n, k = s.get("count", 1), len(s.get("sources", []))
        return f" ({STORY_MEMORY_HOURS} saatte {n} haber, {k} kaynak)" if n > 1 else ""
    return "\n".join(
        f"[{s['id']}]{size(s)} {s['title'][:110]}" + (f" — {s['summary'][:140]}" if s.get("summary") else "")
        for s in recent
    )


def llm_filter(candidates, stories):
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
    user_content = (
        f"SON {STORY_MEMORY_HOURS} SAATTEKI OLAYLAR:\n{_stories_prompt_block(stories)}\n\n"
        f"ADAYLAR — asagidaki {len(pool)} basligi degerlendir:\n\n{items_text}\n\nSadece JSON array dondur."
    )
    verdicts = _llm_json_array(LLM_SYSTEM, user_content, 16000, LLM_MODEL, LLM_EFFORT)
    if not verdicts:
        return []

    known_ids = {s["id"] for s in stories}
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
            item["metric"] = (v.get("metric") or "").strip()[:160]
            lean = (v.get("lean") or item["default_lean"]).lower()
            if lean not in VALID_LEANS:
                lean = item["default_lean"]
            item["lean"] = lean
            story = str(v.get("story") or "").strip().lower()
            if story in known_ids:
                item["story_key"] = story
                item["new_dev"] = bool(v.get("new_dev"))
            else:
                # Yeni olay: LLM'in n-etiketi (ayni run'da ayni olayi birlestirir) veya tekil
                item["story_key"] = ("new:" + story) if re.fullmatch(r"n\d+", story) else f"new:solo{i}"
                item["new_dev"] = True
            keepers.append(item)
        except Exception:
            continue
    keepers.sort(key=lambda x: -x["score"])
    return keepers[:MAX_KEEP]


# --- Olay gruplama + hafiza -----------------------------------------------
# Temsilci kaynak secimi: puan esitse once ajans/tarafsiz kaynak.
SOURCE_PRIORITY = ["Anadolu Ajansı", "Reuters", "AP", "BBC Türkçe", "DW Türkçe", "TRT Haber"]


def _source_rank(source: str) -> int:
    return SOURCE_PRIORITY.index(source) if source in SOURCE_PRIORITY else len(SOURCE_PRIORITY)


def group_by_story(keepers):
    """LLM'in story etiketine gore grupla. Donus: list[{key, members[], new_dev}]."""
    groups = {}
    for item in keepers:
        g = groups.setdefault(item["story_key"], {"key": item["story_key"], "members": [], "new_dev": False})
        g["members"].append(item)
        g["new_dev"] = g["new_dev"] or item["new_dev"]
    out = list(groups.values())
    for g in out:
        g["members"].sort(key=lambda m: (-m["score"], _source_rank(m["source"])))
    out.sort(key=lambda g: -g["members"][0]["score"])
    return out


def _hours_since(iso: str) -> float:
    if not iso:
        return 1e9
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600


def upsert_story(state, group, now_iso):
    """Grubu olay hafizasina yaz (yeni olay ac veya mevcut olayi guncelle)."""
    top = group["members"][0]
    sources = [m["source"] for m in group["members"]]
    if not group["key"].startswith("new:"):
        story = next((s for s in state["stories"] if s["id"] == group["key"]), None)
        if story:
            story["last_ts"] = now_iso
            story["count"] = story.get("count", 1) + len(group["members"])
            story["score"] = max(story.get("score", 0), top["score"])
            story["sources"] = sorted(set(story.get("sources", [])) | set(sources))
            if group["new_dev"]:
                # Son gelismeyi hafizada tut ki LLM bir sonraki adimi kiyaslayabilsin
                story["title"] = top["title"][:160]
                story["summary"] = top.get("summary_tr", "")[:240]
            return story, True
    state["story_seq"] = state.get("story_seq", 0) + 1
    story = {
        "id":          f"s{state['story_seq']}",
        # Dashboard gruplamasi icin kalici, cakismayan id
        "cluster_id":  "st" + hashlib.md5(f"{top['title']}|{now_iso}".encode()).hexdigest()[:10],
        "title":       top["title"][:160],
        "summary":     top.get("summary_tr", "")[:240],
        "first_ts":    now_iso,
        "last_ts":     now_iso,
        "notified_ts": "",
        "score":       top["score"],
        "count":       len(group["members"]),
        "sources":     sorted(set(sources)),
    }
    state["stories"].append(story)
    return story, False


def pending_entry(group, story, is_update, now_iso):
    top = group["members"][0]
    return {
        "story_id":   story["id"],
        "title":      top["title"],
        "summary_tr": top.get("summary_tr", ""),
        "metric":     top.get("metric", ""),
        "source":     top["source"],
        "link":       top["link"],
        "lean":       top["lean"],
        "score":      top["score"],
        "others":     [m["source"] for m in group["members"][1:]],
        "is_update":  is_update,
        "extra_devs": 0,
        "story_count": story.get("count", 1),
        "ts":         now_iso,
    }


def queue_for_digest(state, entry):
    """Olay basina ozette tek satir: ayni olay zaten bekliyorsa birlestir."""
    for i, p in enumerate(state["pending"]):
        if p["story_id"] != entry["story_id"]:
            continue
        others = sorted((set(p.get("others", [])) | set(entry["others"]) | {p["source"]}) - {entry["source"]})
        if entry["score"] >= p["score"]:
            entry["others"] = others
            entry["extra_devs"] = p.get("extra_devs", 0) + (1 if entry["is_update"] else 0)
            entry["is_update"] = entry["is_update"] or p.get("is_update", False)
            state["pending"][i] = entry
        else:
            p["others"] = sorted((set(p.get("others", [])) | set(entry["others"]) | {entry["source"]}) - {p["source"]})
            p["extra_devs"] = p.get("extra_devs", 0) + (1 if entry["is_update"] else 0)
        return
    state["pending"].append(entry)


# --- Telegram -------------------------------------------------------------
LEAN_EMOJI = {"left": "🟥", "neutral": "⬜", "right": "🟦"}


_canonical_cache = {}

def canonical_chat_id(target: str) -> str:
    """Hedefi (@kanal veya -100... gibi farkli yazimlar) Telegram'in dondurdugu
    sayisal chat id'ye cevir. Boylece ayni kanalin iki farkli yazimi tek hedef sayilir."""
    target = (target or "").strip()
    if not target:
        return ""
    if target in _canonical_cache:
        return _canonical_cache[target]
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getChat"
        r = requests.get(url, params={"chat_id": target}, timeout=10)
        data = r.json()
        if data.get("ok"):
            target_id = str(data["result"]["id"])
            _canonical_cache[target] = target_id
            return target_id
    except Exception as e:
        log(f"getChat hatasi ({target}): {e}")
    # Cozulemezse ham degeri kullan (dedup en azindan birebir esleseni yakalar)
    _canonical_cache[target] = target
    return target


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def send_telegram(chat_id: str, text: str, silent: bool = False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "disable_notification": silent,
    }, timeout=15)
    return r.ok, r.text


def broadcast(msg: str, silent: bool = False) -> bool:
    """Mesaji tum hedeflere (CHAT_ID + CHANNEL_ID) gonder; ayni kanalin farkli yazimini ele."""
    delivered = False
    seen_targets = set()
    for target in [CHAT_ID, CHANNEL_ID]:
        target = (target or "").strip()
        if not target:
            continue
        canon = canonical_chat_id(target)
        if canon in seen_targets:
            continue
        seen_targets.add(canon)
        try:
            ok, resp = send_telegram(target, msg, silent)
        except Exception as e:
            # Gecici ag/timeout hatasi tum run'i dusurmesin (state kaydedilebilsin)
            log(f"Telegram istek hatasi ({target}): {e}")
            ok, resp = False, ""
        if ok:
            delivered = True
        else:
            log(f"Telegram hatasi ({target}): {resp[:160]}")
        time.sleep(SEND_DELAY_S)
    return delivered


def topic_emoji(title: str, summary: str) -> str:
    blob = (title + " " + summary).lower()
    if any(k in blob for k in ["fed", "ecb", "imf", "nato", "putin", "trump", "xi ", "netanyahu", "iran", "ukrayna", "gazze", "israil", "white house", "ab ", "sanctions", "yaptirim"]):
        return "🌍"
    if any(k in blob for k in ["faiz", "enflasyon", "tcmb", "dolar", "euro", "borsa", "bist", "tahvil", "butce", "hazine", "maliye", "vergi", "buyume", "issizlik", "petrol", "brent", "altin"]):
        return "💰"
    return "🏛️"


def deliver_urgent(group, is_update: bool) -> bool:
    """Son dakika: olay basina tek, sesli mesaj — temsilci kaynak + diger kaynaklarin sayisi."""
    top = group["members"][0]
    icon = topic_emoji(top["title"], top.get("summary_tr", ""))
    lean_dot = LEAN_EMOJI.get(top["lean"], "⬜")
    head = "🔄 GELİŞME" if is_update else "🔴 SON DAKİKA"
    summary_line = f"\n<i>{_esc(top['summary_tr'])}</i>" if top.get("summary_tr") else ""
    metric_line = f"\n📊 <b>{_esc(top['metric'])}</b>" if top.get("metric") else ""
    coverage = ""
    if len(group["members"]) > 1:
        others = [m["source"] for m in group["members"][1:6]]
        coverage = f"\n<i>+ {len(group['members']) - 1} kaynak daha: {_esc(', '.join(others))}</i>"
    msg = (
        f"<b>{head} · {icon} {_esc(top['source'])}</b> {lean_dot}\n"
        f"<b>{_esc(top['title'])}</b>{metric_line}{summary_line}{coverage}\n"
        f"<a href='{_esc(top['link'])}'>→ Habere git</a>"
    )
    return broadcast(msg)


def _digest_line(p) -> str:
    icon = topic_emoji(p["title"], p.get("summary_tr", ""))
    upd = "🔄 " if p.get("is_update") else ""
    extra = f" <i>(+{p['extra_devs']} gelişme)</i>" if p.get("extra_devs") else ""
    hot = ""
    if p.get("story_count", 1) >= HOT_STORY_COUNT:
        hot = f"🔥 <b>Gündem</b> <i>({STORY_MEMORY_HOURS} saatte {p['story_count']} haber)</i>\n"
    line = f"{hot}{icon} {upd}<b>{_esc(p['title'])}</b>{extra} {LEAN_EMOJI.get(p['lean'], '⬜')}"
    if p.get("metric"):
        line += f"\n📊 {_esc(p['metric'])}"
    if p.get("summary_tr"):
        line += f"\n<i>{_esc(p['summary_tr'])}</i>"
    src = f"<a href='{_esc(p['link'])}'>{_esc(p['source'])}</a>"
    if p.get("others"):
        src += f" · +{len(p['others'])} kaynak"
    return line + "\n" + src


def _visible_len(html_text: str) -> int:
    return len(re.sub(r"<[^>]+>", "", html_text))


def latest_digest_slot(now_tr: datetime) -> datetime:
    base = now_tr.replace(minute=0, second=0, microsecond=0)
    for back in range(0, 25):
        t = base - timedelta(hours=back)
        if t.hour in DIGEST_HOURS:
            return t
    return base


def maybe_flush_digest(state, quiet: bool) -> int:
    """Son ozet slotundan beri ozet gitmediyse bekleyen haberleri tek mesajda gonder."""
    if quiet:
        return 0
    now_tr = datetime.now(TURKEY_TZ)
    slot = latest_digest_slot(now_tr)
    last = state.get("last_digest_ts") or ""
    if last and datetime.fromisoformat(last) >= slot:
        return 0
    now_iso = datetime.now(timezone.utc).isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=PENDING_MAX_AGE_H)).isoformat()
    pending = [p for p in state["pending"] if p.get("ts", "") >= cutoff]
    if not pending:
        state["pending"] = []
        state["last_digest_ts"] = now_iso
        return 0

    pending.sort(key=lambda p: (-p["score"], -p.get("story_count", 1), p["ts"]))
    header = f"🗞 <b>Haber özeti · {now_tr.strftime('%H:%M')}</b>"
    lines = []
    for p in pending[:DIGEST_MAX_ITEMS]:
        candidate = "\n\n".join([header] + lines + [_digest_line(p)])
        if _visible_len(candidate) > TG_MAX_CHARS:
            break
        lines.append(_digest_line(p))
    skipped = len(pending) - len(lines)
    footer = f"\n\n<i>+{skipped} haber daha dashboard'da</i>" if skipped > 0 else ""
    msg = "\n\n".join([header] + lines) + footer

    if not broadcast(msg, silent=DIGEST_SILENT):
        return 0   # pending korunur, sonraki run tekrar dener
    sent_ids = {p["story_id"] for p in pending[:len(lines)]}
    for s in state["stories"]:
        if s["id"] in sent_ids:
            s["notified_ts"] = now_iso
    state["pending"] = []
    state["last_digest_ts"] = now_iso
    return len(lines)


# --- Resmi veri kanali -----------------------------------------------------
DATA_LLM_SYSTEM = """Sen resmi istatistik kurumlarinin yayinlarini izleyen bir veri analistisin. Sana resmi kaynaklardan (TUIK, TCMB, OECD, Eurostat, bakanliklar) gelen baslik+ozet listesi verilecek. Gorevin YALNIZCA yayinlanmis SOMUT bir SAYISAL gosterge iceren item'lari cikarmak.

CIKAR (is_data=true) — su tur SAYISAL resmi veriler:
- Enflasyon (TUFE/UFE), issizlik, buyume (GSYH), dis ticaret (ihracat/ithalat/denge), cari acik, butce gerceklesme/borc, sanayi/perakende/tarim-gida fiyat endeksleri, TCMB politika faizi/rezerv, OECD/Eurostat makro gostergeleri
- Item'da SOMUT bir SAYI (oran/tutar/endeks puani) olmali. Sayi yoksa is_data=false.

ONCELIK: TURKIYE gostergeleri (TUIK/TCMB/bakanliklar) en onemli — ozellikle vatandasin gecim maliyeti (enflasyon/TUFE, kira artis orani, asgari ucret, issizlik). Bunlari her zaman cikar.

OECD/Eurostat KISITI: Bu kaynaklardan SADECE Turkiye'yi iceren / Turkiye ile ilgili veriyi cikar (or. "OECD Turkiye buyume tahmini", "Eurostat'a gore Turkiye enflasyonu AB'de en yuksek"). Sirf euro bolgesi / baska ulke / Turkiye'siz AB verisini is_data=false yap, ELE.

KAYNAK: Veri cogu zaman bir gazete/haber sitesi tarafindan kotalanir ama ASIL kaynak resmi kurumdur. source_tr'yi DAIMA verinin resmi kaynagi yap (TUIK / TCMB / OECD / Eurostat / Hazine ve Maliye / Ticaret Bakanligi) — haberi yapan gazete (AA, Bloomberg HT, Cumhuriyet vb.) DEGIL.

TEKILLESTIR: Ayni temel yayini/rakami farkli baslik veya farkli kaynak tekrar ediyorsa (or. ayni donem ayni enflasyon rakami onlarca haberde) SADECE BIR KEZ cikar — en net olani sec. Ayni rakami farkli para biriminde (euro/dolar) tekrar verme; kaynaktaki ASIL birimi kullan.

ELE (is_data=false):
- Genel haber/yorum/duyuru/etkinlik, "aciklanacak/ele alinacak" gibi gelecek zamanli, sayisiz metinler

BEKLENTI/TAHMIN: Rakam resmi GERCEKLESMIS bir sonuc degil de beklenti/tahmin/anket/projeksiyon ise is_forecast=true (yine cikar, isaretle). Resmi gerceklesmis veri ise is_forecast=false.

Her item icin alanlar:
- key: gosterge icin KARARLI, kisa snake_case anahtar; ayni gosterge HER ZAMAN ayni key almali. Yillik/aylik varyantlari ayri key yap. Ornek: "tr_tufe_yillik", "tr_tufe_aylik", "tr_ufe_yillik", "tr_issizlik", "tr_gsyh_buyume", "tr_dis_ticaret_dengesi", "tr_cari_acik", "tr_butce_dengesi", "tcmb_politika_faizi", "ea_hicp_yillik", "oecd_tr_buyume_tahmin"
- indicator_tr: Turkce insan-okur ad. Ornek: "TUFE (yillik)", "Issizlik orani", "Politika faizi"
- value: aciklanan deger, kaynaktaki haliyle. Ornek: "%41,6", "3,2 milyar $", "%50"
- period: verinin donemi. Ornek: "Mayis 2025", "2025 1. ceyrek". Bilinmiyorsa "".
- prev_in_text: kaynak metninde ACIKCA gecen bir onceki donem degeri varsa yaz, yoksa "".
- yoy: metinde gecen gecen-yil-ayni-donem kiyasi varsa yaz, yoksa "".
- is_forecast: bool
- source_tr: kurum adi (TUIK/TCMB/OECD/Eurostat/Ticaret Bakanligi/Hazine ve Maliye)

Cikti ZORUNLU: yalniz gecerli JSON array. Her item:
{"i": <int>, "is_data": <bool>, "key": "...", "indicator_tr": "...", "value": "...", "period": "...", "prev_in_text": "...", "yoy": "...", "is_forecast": <bool>, "source_tr": "..."}
is_data=false ise diger alanlar atlanabilir. Tahmin etme, uydurma — sadece metinde acikca gecen rakami kullan. Aciklama veya markdown yazma."""


def collect_data_candidates(data_state):
    seen_links = data_state.get("seen_links", {})
    seen_in_batch = set()
    candidates = []
    for feed_meta in DATA_SOURCES:
        url = feed_meta["url"]
        label = feed_meta["label"]
        try:
            feed = feedparser.parse(url, agent=USER_AGENT)
            if not feed.entries:
                log(f"VERI UYARI: 0 entry — {label}")
                continue
            for entry in feed.entries[:DATA_PER_FEED_LIMIT]:
                title = (entry.get("title") or "").strip()
                link  = entry.get("link") or ""
                summary_raw = re.sub(r"<[^>]+>", " ", entry.get("summary") or "")[:500]
                if not title or not link:
                    continue
                clean_title = re.sub(r"\s[-–|]\s[^-–|]{2,40}\s*$", "", title).strip()
                lh = link_hash(link, clean_title)
                if lh in seen_links or lh in seen_in_batch:
                    continue
                seen_in_batch.add(lh)
                candidates.append({
                    "title":       clean_title,
                    "link":        link,
                    "raw_summary": summary_raw,
                    "source":      label,
                    "link_hash":   lh,
                })
        except Exception as e:
            log(f"VERI HATA ({label}): {e}")
    return candidates


def _llm_json_array(system: str, user_content: str, max_tokens: int, model: str = "", effort: str = ""):
    """Anthropic messages cagrisi -> JSON array (list) veya None."""
    body = {
        "model": model or LLM_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }
    if effort:
        body["output_config"] = {"effort": effort}
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=body,
            timeout=240,
        )
        if not r.ok:
            log(f"LLM HTTP hata: {r.status_code} — {r.text[:200]}")
            return None
        data = r.json()
        text = "".join(blk.get("text", "") for blk in data.get("content", []) if blk.get("type") == "text").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        m = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not m:
            log(f"LLM cikti JSON degil: {text[:200]}")
            return None
        return json.loads(m.group(0))
    except Exception as e:
        log(f"LLM hata: {e}")
        return None


def data_filter(candidates):
    if not ANTHROPIC_API_KEY:
        log("ANTHROPIC_API_KEY yok — veri pass'i devre disi.")
        return []
    if not candidates:
        return []
    pool = candidates[:MAX_DATA_CANDIDATES]
    items_text = "\n".join(
        f"[{i}] ({c['source']}) {c['title']}" + (f" — {c['raw_summary'][:240]}" if c['raw_summary'] else "")
        for i, c in enumerate(pool)
    )
    verdicts = _llm_json_array(
        DATA_LLM_SYSTEM,
        f"Asagidaki {len(pool)} resmi kaynak basligini degerlendir:\n\n{items_text}\n\nSadece JSON array dondur.",
        4000,
        DATA_LLM_MODEL,
    )
    if not verdicts:
        return []
    points = []
    for v in verdicts:
        try:
            i = int(v["i"])
            if not (0 <= i < len(pool)):
                continue
            if not v.get("is_data"):
                continue
            key = (v.get("key") or "").strip().lower()
            value = (v.get("value") or "").strip()
            if not key or not value:
                continue
            src = pool[i]
            points.append({
                "key":          key,
                "indicator_tr": (v.get("indicator_tr") or key).strip()[:80],
                "value":        value[:60],
                "period":       (v.get("period") or "").strip()[:40],
                "prev_in_text": (v.get("prev_in_text") or "").strip()[:60],
                "yoy":          (v.get("yoy") or "").strip()[:120],
                "is_forecast":  bool(v.get("is_forecast")),
                "source_tr":    (v.get("source_tr") or src["source"]).strip()[:40],
                "title":        src["title"],
                "link":         src["link"],
                "link_hash":    src["link_hash"],
            })
        except Exception:
            continue
    return points


_SCALE = {"trilyon": 1e12, "milyar": 1e9, "milyon": 1e6, "bin": 1e3}


def _parse_num(s: str):
    """Turkce sayi metnini (float, is_percent) cifti olarak coz; cozulemezse None."""
    if not s:
        return None
    low = s.lower()
    is_pct = "%" in s or "yuzde" in low or "yüzde" in low or "puan" in low
    m = re.search(r"-?\d[\d.,]*", low)
    if not m:
        return None
    num = m.group(0).rstrip(".,")
    if "," in num:                       # ',' ondalik, '.' binlik
        num = num.replace(".", "").replace(",", ".")
    else:
        parts = num.split(".")           # sadece nokta varsa: binlik mi ondalik mi?
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            num = num.replace(".", "")
    try:
        val = float(num)
    except ValueError:
        return None
    for w, mult in _SCALE.items():
        if w in low:
            val *= mult
            break
    return (val, is_pct)


def _unit_class(s: str) -> str:
    """Degerin birim sinifi — farkli birimleri kiyaslamayi engellemek icin."""
    low = s.lower()
    if "%" in s or "yuzde" in low or "yüzde" in low or "puan" in low:
        return "pct"
    if "$" in s or "dolar" in low or "usd" in low:
        return "usd"
    if "€" in s or "euro" in low or "avro" in low or "eur" in low:
        return "eur"
    if "₺" in s or "lira" in low or re.search(r"\btl\b", low):
        return "try"
    return "num"


def compute_delta(old_s: str, new_s: str) -> str:
    """Iki deger arasindaki degisimi insan-okur ifadeye cevir; cozulemezse ''."""
    # Farkli birim (euro vs dolar, oran vs tutar...) -> kiyas anlamsiz
    if _unit_class(old_s) != _unit_class(new_s):
        return ""
    o = _parse_num(old_s)
    n = _parse_num(new_s)
    if not o or not n:
        return ""
    ov, op = o
    nv, np = n
    if op and np:                        # iki oran -> puan farki
        d = nv - ov
        if abs(d) < 1e-9:
            return ""
        return f"{d:+.1f} puan".replace(".", ",")
    if ov == 0:
        return ""
    pct = (nv - ov) / abs(ov) * 100
    if abs(pct) < 0.05:
        return ""
    return f"%{pct:+.1f}".replace(".", ",")


def process_data_points(points, data_state):
    """Yeni/degisen gostergeleri dondur, data_state'i guncelle. Deger-bazli dedup."""
    indicators = data_state.setdefault("indicators", {})
    now_iso = datetime.now(timezone.utc).isoformat()
    out = []
    batch_sigs = set()   # ayni run icinde farkli kaynak/isimle gelen ayni rakam-donem -> tek mesaj
    for p in points:
        key = p["key"]
        # Run-ici yakin-tekrar: ayni (donem, sayisal deger, birim) imzasi bir kez gecsin
        parsed = _parse_num(p["value"])
        sig = (p["period"], round(parsed[0], 2) if parsed else p["value"], _unit_class(p["value"]))
        if sig in batch_sigs:
            continue
        batch_sigs.add(sig)
        store_key = ("fc:" + key) if p["is_forecast"] else key
        prev_stored = indicators.get(store_key)
        # Ayni deger + ayni donem zaten gonderildiyse atla
        if prev_stored and prev_stored.get("value") == p["value"] and prev_stored.get("period") == p["period"]:
            continue
        baseline = indicators.get(key)   # gercek veri baseline'i (forecast icin de kiyas)
        prev_value = (baseline or {}).get("value", "") or p.get("prev_in_text", "")
        item = dict(p)
        item["prev_value"] = prev_value
        item["delta"]      = compute_delta(prev_value, p["value"]) if prev_value else ""
        item["is_first"]   = (baseline is None) and not p.get("prev_in_text")
        out.append(item)
        indicators[store_key] = {
            "value":        p["value"],
            "period":       p["period"],
            "ts":           now_iso,
            "indicator_tr": p["indicator_tr"],
        }
        if len(out) >= MAX_DATA_DELIVER:
            break
    return out


def deliver_data_item(item) -> bool:
    head = (f"📈 BEKLENTİ — {item['source_tr']}" if item["is_forecast"]
            else f"📊 RESMİ VERİ — {item['source_tr']}")
    if item["prev_value"] and not item["is_first"]:
        delta = f" ({item['delta']})" if item["delta"] else ""
        metric = f"<b>{item['indicator_tr']}:</b> {item['prev_value']} → {item['value']}{delta}"
    elif item["is_first"]:
        metric = f"<b>{item['indicator_tr']}:</b> {item['value']} <i>(ilk kayıt)</i>"
    else:
        metric = f"<b>{item['indicator_tr']}:</b> {item['value']}"
    period_line = f" — {item['period']}" if item["period"] else ""
    yoy_line = f"\n<i>Geçen yıl kıyas: {item['yoy']}</i>" if item["yoy"] else ""
    date_str = datetime.now(TURKEY_TZ).strftime("%d.%m.%Y")
    msg = (
        f"<b>{head}</b>\n"
        f"{metric}{period_line}{yoy_line}\n"
        f"<a href='{item['link']}'>→ Kaynak</a> · {date_str}"
    )
    return broadcast(msg)


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

    keepers = llm_filter(candidates, state["stories"]) if candidates else []
    groups = group_by_story(keepers)
    log(f"LLM {len(keepers)} basligi onemli buldu -> {len(groups)} olay.")

    news_data = load_news_data()
    now_iso = datetime.now(timezone.utc).isoformat()
    urgent_sent = queued = repeats = web_recorded = 0
    for group in groups:
        story, existed = upsert_story(state, group, now_iso)
        top = group["members"][0]
        already_notified = bool(story.get("notified_ts"))
        is_update = existed and already_notified and group["new_dev"]

        if already_notified and not group["new_dev"]:
            repeats += 1           # ayni olayin tekrari: bildirim yok, sadece web
        elif top["score"] >= URGENT_SCORE:
            can_urgent = (not quiet
                          and len(state["urgent_log"]) < MAX_URGENT_PER_DAY
                          and _hours_since(story.get("notified_ts", "")) >= URGENT_RESEND_GAP_H)
            if can_urgent and deliver_urgent(group, is_update):
                urgent_sent += 1
                story["notified_ts"] = now_iso
                state["urgent_log"].append(now_iso)
                # Ayni olay ozette bekliyorsa artik gerek yok
                state["pending"] = [p for p in state["pending"] if p["story_id"] != story["id"]]
            else:
                queue_for_digest(state, pending_entry(group, story, is_update, now_iso))
                queued += 1
        elif top["score"] >= DIGEST_SCORE:
            queue_for_digest(state, pending_entry(group, story, is_update, now_iso))
            queued += 1

        # Web: olaydaki tum uyeler (her kaynak ayri kart), olay id'si ile gruplu
        for m in group["members"]:
            web_recorded += 1
            state["ids"][link_hash(m["link"], m["title"])] = now_iso
            state["ids"][title_hash(m["title"])] = now_iso
            news_data.append({
                "title":      m["title"],
                "link":       m["link"],
                "source":     m["source"],
                "summary_tr": m.get("summary_tr", ""),
                "metric":     m.get("metric", ""),
                "lean":       m["lean"],
                "score":      m["score"],
                "ts":         now_iso,
                "type":       "news",
                "cluster_id": story["cluster_id"],
            })

        # Olay basina tek fingerprint -> ucuz cross-run on-eleme (LLM'e gitmeden)
        state["fingerprints"].append({
            "tokens":     sorted(top["tokens"]),
            "ts":         now_iso,
            "title":      top["title"][:100],
            "cluster_id": story["cluster_id"],
        })

    digest_sent = maybe_flush_digest(state, quiet)
    log(f"Bildirim: {urgent_sent} son dakika, {queued} ozete eklendi, {repeats} tekrar elendi, "
        f"ozet: {digest_sent} haber gonderildi. Bekleyen: {len(state['pending'])}.")

    # Gonderilmeyen tum candidate'lari da state'e isaretle (LLM bir daha bakmasin)
    for c in candidates:
        state["ids"].setdefault(link_hash(c["link"], c["title"]), now_iso)
        state["ids"].setdefault(title_hash(c["title"]), now_iso)

    # --- Resmi veri kanali (konu suzgecinden bagimsiz ikinci pass) ---
    try:
        data_state = load_data_state()
        data_candidates = collect_data_candidates(data_state)
        data_points = data_filter(data_candidates)
        data_items = process_data_points(data_points, data_state)
        log(f"Veri: {len(data_candidates)} aday -> {len(data_points)} gosterge -> {len(data_items)} yeni/degisen.")
        data_sent = 0
        for item in data_items:
            if not quiet and deliver_data_item(item):
                data_sent += 1
            change = (f"{item['prev_value']} → {item['value']}"
                      + (f" ({item['delta']})" if item['delta'] else "")) if item['prev_value'] else item['value']
            news_data.append({
                "title":      f"{item['indicator_tr']}: {item['value']}" + (f" ({item['period']})" if item['period'] else ""),
                "link":       item["link"],
                "source":     item["source_tr"],
                "summary_tr": change,
                "metric":     item.get("delta", ""),
                "lean":       "neutral",
                "score":      0,
                "ts":         now_iso,
                "type":       "forecast" if item["is_forecast"] else "data",
                "cluster_id": "",
            })
        # Tum veri adaylarini gorildi isaretle -> ayni makaleyi tekrar LLM'e gonderme
        seen = data_state.setdefault("seen_links", {})
        for c in data_candidates:
            seen.setdefault(c["link_hash"], now_iso)
        save_data_state(data_state)
        log(f"Veri kanali: {data_sent} mesaj gonderildi.")
    except Exception as e:
        log(f"Veri kanali HATA: {e}")

    save_state(state)
    save_news_data(news_data)
    log(f"Web: {web_recorded} item. State: {len(state['ids'])} id / {len(state['fingerprints'])} fp. News store: {len(news_data)}.")


if __name__ == "__main__":
    main()
