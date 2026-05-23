import { ghGetFile } from "../lib/github.js";

const BUILTIN_SOURCES = [
  { label: "BBC Türkçe",     default_lean: "neutral", builtin: true },
  { label: "DW Türkçe",      default_lean: "neutral", builtin: true },
  { label: "T24",            default_lean: "left",    builtin: true },
  { label: "Diken",          default_lean: "left",    builtin: true },
  { label: "Medyascope",     default_lean: "left",    builtin: true },
  { label: "YetkinReport",   default_lean: "neutral", builtin: true },
  { label: "bianet",         default_lean: "left",    builtin: true },
  { label: "Cumhuriyet",     default_lean: "left",    builtin: true },
  { label: "Sözcü",          default_lean: "left",    builtin: true },
  { label: "soL",            default_lean: "left",    builtin: true },
  { label: "Anadolu Ajansı", default_lean: "right",   builtin: true },
  { label: "TRT Haber",      default_lean: "right",   builtin: true },
  { label: "Reuters",        default_lean: "neutral", builtin: true },
  { label: "AP",             default_lean: "neutral", builtin: true },
  { label: "Fatih Altaylı",  default_lean: "neutral", builtin: true },
];

export default async function handler(req, res) {
  if (req.method !== "GET") {
    res.status(405).json({ error: "Method not allowed" });
    return;
  }

  try {
    const [newsFile, feedsFile] = await Promise.all([
      ghGetFile("news_data.json"),
      ghGetFile("custom_feeds.json"),
    ]);

    let news = [];
    if (newsFile) {
      try { news = JSON.parse(newsFile.content); } catch {}
    }
    if (!Array.isArray(news)) news = [];

    let customFeeds = [];
    if (feedsFile) {
      try {
        const parsed = JSON.parse(feedsFile.content);
        if (Array.isArray(parsed)) customFeeds = parsed.map(f => ({ ...f, builtin: false }));
      } catch {}
    }

    const updated_at = news.length ? news.map(n => n.ts).filter(Boolean).sort().reverse()[0] : null;

    res.setHeader("Cache-Control", "no-store");
    res.status(200).json({
      news,
      sources: [...BUILTIN_SOURCES, ...customFeeds],
      updated_at,
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
