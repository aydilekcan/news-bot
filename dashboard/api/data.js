import { ghGetFile } from "../lib/github.js";

export default async function handler(req, res) {
  if (req.method !== "GET") {
    res.status(405).json({ error: "Method not allowed" });
    return;
  }

  try {
    const [kwFile, tweetsFile] = await Promise.all([
      ghGetFile("keywords.json"),
      ghGetFile("tweets_data.json"),
    ]);

    let keywords = [];
    if (kwFile) {
      try { keywords = JSON.parse(kwFile.content); } catch {}
    }

    let tweets = {};
    let updated_at = null;
    if (tweetsFile) {
      try { tweets = JSON.parse(tweetsFile.content); } catch {}
      // En son captured_at'ı bul
      const allCaptured = Object.values(tweets)
        .flat()
        .map(t => t.captured_at)
        .filter(Boolean);
      if (allCaptured.length) {
        updated_at = allCaptured.sort().reverse()[0];
      }
    }

    res.setHeader("Cache-Control", "no-store");
    res.status(200).json({ keywords, tweets, updated_at });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
}
