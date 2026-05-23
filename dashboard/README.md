# Haber Botu Paneli

`news_bot.py`'nin gönderdiği haberleri kaynak rozeti, siyasi yön etiketi (sol/nötr/sağ) ve AI özetiyle birlikte gösteren web panel. Ayrıca yönetici şifresiyle özel RSS feed eklenebilir.

## Mimari

- Bot (`news_bot.py`) GitHub Actions üzerinden çalışır, haberleri:
  - **Telegram'a** gönderir (mevcut akış)
  - **`news_data.json`'a** yazıp commit'ler (son 30 gün, max 1000 kayıt)
- Panel (Vercel'de host edilen statik site + serverless API), `news_data.json`'u GitHub API'siyle okur.
- "Özel RSS ekle" formuyla yönetici `custom_feeds.json`'u günceller; bot bir sonraki run'da bu kaynaklardan da çeker. LLM her haber için özet ve lean üretir — admin'in tanımladığı default_lean ipucu LLM'e iletilir, gerekirse LLM override eder.

## Kurulum

### 1) GitHub Personal Access Token (PAT) oluştur

1. https://github.com/settings/tokens?type=beta → **Generate new token (Fine-grained)**
2. **Repository access**: sadece bu repo (`news_bot`)
3. **Permissions** → **Repository permissions**:
   - **Contents**: Read and write
4. Token'ı kopyala.

### 2) Vercel'e deploy et

1. https://vercel.com → **Add New → Project** → bu GitHub repo'sunu import et
2. **Root Directory**: `dashboard`
3. **Environment Variables**:

| Key | Value |
| --- | --- |
| `GITHUB_TOKEN` | PAT |
| `GITHUB_REPO` | `kullanici/news_bot` |
| `GITHUB_BRANCH` | `main` (opsiyonel) |
| `DASHBOARD_PASSWORD` | yönetici şifresi |

4. **Deploy**.

### 3) Kullanım

- Panel URL'sini aç. Haberler şifresiz görüntülenir.
- Özel RSS eklemek/silmek için sağ üstteki kilit ikonu üzerinden şifre.
- Sol panelden kaynağa veya siyasi yöne göre filtrele.

## Lokal geliştirme

```bash
cd dashboard
cp .env.example .env.local      # değerleri doldur
npm i -g vercel                  # bir kerelik
vercel dev                       # http://localhost:3000
```

## Veri dosyaları

| Dosya | Kim yazıyor | Ne içeriyor |
| --- | --- | --- |
| `news_data.json` | bot (her run'da) | Son 30 gün gönderilmiş haberler (özet+lean+score dahil) |
| `custom_feeds.json` | panel (admin) | Bot'a eklenecek ekstra RSS'ler (`url`, `label`, `default_lean`) |
| `sent_ids.json` | bot | Dedup için ID + fingerprint state'i |

## Sorun giderme

- **"Veri yüklenemedi"**: `GITHUB_TOKEN` / `GITHUB_REPO` env'lerini kontrol et.
- **401 RSS eklerken**: kilit ikonuna basıp şifreyi yeniden gir.
- **Bot yeni haber çekmiyor**: GitHub Actions sekmesinde Haber Botu workflow'unun çalıştığını doğrula. `news_bot.log` çıktısına bak.
- **Lean yanlış**: `default_lean` sadece ipucu — LLM içerik bağlamına göre override edebiliyor. Sistematik bir hata varsa `LLM_SYSTEM` prompt'unu güncelle.
