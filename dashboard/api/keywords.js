import { checkAuth, writeKeywords } from "../lib/github.js";

export default async function handler(req, res) {
  if (!checkAuth(req)) {
    res.status(401).json({ error: "Yetkisiz" });
    return;
  }

  // Şifre doğrulama ping'i — gerçek değişiklik yapmadan auth'u test eder
  const body = req.body || {};
  if (body.keyword === "__ping__") {
    res.status(200).json({ ok: true });
    return;
  }

  const raw = typeof body.keyword === "string" ? body.keyword : "";
  const keyword = raw.trim().toLowerCase();
  if (!keyword) {
    res.status(400).json({ error: "Geçersiz keyword" });
    return;
  }
  if (keyword.length > 100) {
    res.status(400).json({ error: "Keyword çok uzun" });
    return;
  }

  try {
    if (req.method === "POST") {
      const result = await writeKeywords(
        (list) => {
          if (list.includes(keyword)) return list;
          return [...list, keyword];
        },
        `Dashboard: '${keyword}' kelimesi eklendi`
      );
      res.status(200).json({ ok: true, keywords: result.keywords, changed: result.changed });
    } else if (req.method === "DELETE") {
      const result = await writeKeywords(
        (list) => list.filter(k => k !== keyword),
        `Dashboard: '${keyword}' kelimesi silindi`
      );
      res.status(200).json({ ok: true, keywords: result.keywords, changed: result.changed });
    } else {
      res.status(405).json({ error: "Method not allowed" });
    }
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
