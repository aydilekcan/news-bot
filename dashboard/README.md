# X Bot Panel

X (Twitter) botunun keyword'lerini yönetmek ve eşleşen tweet'leri görmek için web panel.

## Kurulum

### 1) GitHub Personal Access Token (PAT) oluştur

1. https://github.com/settings/tokens?type=beta → **Generate new token (Fine-grained)**
2. **Repository access**: sadece bu repo (`news_bot`)
3. **Permissions** → **Repository permissions**:
   - **Contents**: Read and write
4. Token'ı kopyala (sadece bir kez gösterilir).

### 2) Vercel'e deploy et

1. https://vercel.com → **Add New → Project** → bu GitHub repo'sunu import et
2. **Root Directory**: `dashboard` olarak ayarla
3. **Environment Variables** sekmesinde şunları ekle:

| Key | Value |
| --- | --- |
| `GITHUB_TOKEN` | yukarıda oluşturduğun PAT |
| `GITHUB_REPO` | `kullanici-adi/news_bot` (örnek: `aydilekcan/news_bot`) |
| `GITHUB_BRANCH` | `main` (opsiyonel, default `main`) |
| `DASHBOARD_PASSWORD` | panel için belirleyeceğin şifre |

4. **Deploy**.

### 3) Kullanım

- Vercel'in verdiği URL'i aç (örnek: `https://news-bot-dashboard.vercel.app`).
- Tweet'leri ve keyword'leri görüntülemek için şifre gerekmiyor.
- Keyword eklemek/silmek için bir kerelik şifre sorulur, sonra `localStorage`'da tutulur.

## Nasıl çalışıyor?

- Panel, `keywords.json` ve `tweets_data.json` dosyalarını GitHub API üzerinden okur.
- Keyword eklendiğinde/silindiğinde, panel `keywords.json`'u GitHub API ile günceller.
- Bot bir sonraki çalışmasında (her 2 saatte bir) yeni keyword listesini kullanır.
- Bot her çalıştığında `tweets_data.json` dosyasına son tweet'leri yazar ve commit'ler.

## Lokal geliştirme

`vercel dev` ile hem statik dosyaları hem de API route'larını lokalde çalıştırabilirsin.

```bash
# 1) Vercel CLI'yi kur (bir kerelik)
npm i -g vercel

# 2) dashboard klasörüne gir
cd dashboard

# 3) .env.local dosyasını oluştur (örnek dosyadan kopyala)
cp .env.example .env.local
# .env.local içini gerçek değerlerle doldur

# 4) Lokal sunucuyu başlat
vercel dev
```

Tarayıcıda `http://localhost:3000` aç.

**Notlar:**
- `.env.local` `.gitignore`'da, secret'lar repo'ya gitmez.
- Lokal'de yaptığın "keyword ekle/sil" işlemleri **canlı repo'da** `keywords.json`'u değiştirir (test için ayrı bir branch'e yönlendirmek istersen `GITHUB_BRANCH=test` yapabilirsin — o branch'in mevcut olması gerekir).
- İlk `vercel dev` çağrısı projeyi Vercel hesabına bağlamak ister; "link to existing project" hayır de, sadece local olarak çalıştırıyorsun.

## Sorun giderme

- **"Veri yüklenemedi"**: `GITHUB_TOKEN` ve `GITHUB_REPO` ayarlarını kontrol et.
- **Keyword eklediğimde 401**: `DASHBOARD_PASSWORD` ayarı eksik veya yanlış girdin → kilit ikonuna tıkla, tekrar gir.
- **Yeni tweet'ler gelmiyor**: GitHub Actions sekmesinden X bot workflow'unun çalışıp çalışmadığını kontrol et.
- **Push çakışması**: Bot push'u en fazla 3 kere rebase'le dener. Çok sık keyword değişikliği yapmazsan sorun olmaz.
