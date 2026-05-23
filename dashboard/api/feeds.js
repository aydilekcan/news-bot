import { checkAuth, writeCustomFeeds } from "../lib/github.js";

const VALID_LEANS = new Set(["left", "neutral", "right"]);

function normalizeUrl(u) {
  if (typeof u !== "string") return "";
  const t = u.trim();
  if (!t) return "";
  if (!/^https?:\/\//i.test(t)) return "";
  return t;
}

export default async function handler(req, res) {
  if (!checkAuth(req)) {
    res.status(401).json({ error: "Yetkisiz" });
    return;
  }

  const body = req.body || {};

  // Sifre dogrulama ping'i
  if (body.url === "__ping__") {
    res.status(200).json({ ok: true });
    return;
  }

  const url = normalizeUrl(body.url);
  if (!url) {
    res.status(400).json({ error: "Gecerli bir RSS URL'i gir (http/https)" });
    return;
  }
  if (url.length > 500) {
    res.status(400).json({ error: "URL cok uzun" });
    return;
  }

  const label = (typeof body.label === "string" ? body.label : "").trim().slice(0, 60) || url;
  const lean = (typeof body.default_lean === "string" ? body.default_lean : "neutral").toLowerCase();
  const defaultLean = VALID_LEANS.has(lean) ? lean : "neutral";

  try {
    if (req.method === "POST") {
      const result = await writeCustomFeeds(
        (list) => {
          if (list.some(f => f.url === url)) return list;
          return [...list, { url, label, default_lean: defaultLean }];
        },
        `Dashboard: '${label}' RSS eklendi`
      );
      res.status(200).json({ ok: true, feeds: result.feeds, changed: result.changed });
    } else if (req.method === "DELETE") {
      const result = await writeCustomFeeds(
        (list) => list.filter(f => f.url !== url),
        `Dashboard: '${url}' RSS silindi`
      );
      res.status(200).json({ ok: true, feeds: result.feeds, changed: result.changed });
    } else {
      res.status(405).json({ error: "Method not allowed" });
    }
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
