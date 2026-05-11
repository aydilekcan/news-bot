// Paylaşılan GitHub API yardımcıları

export function getRepo() {
  const repo = process.env.GITHUB_REPO;
  if (!repo) throw new Error("GITHUB_REPO env var ayarlı değil");
  return repo;
}

export function getToken() {
  const t = process.env.GITHUB_TOKEN;
  if (!t) throw new Error("GITHUB_TOKEN env var ayarlı değil");
  return t;
}

export function getBranch() {
  return process.env.GITHUB_BRANCH || "main";
}

export async function ghGetFile(path) {
  const repo = getRepo();
  const branch = getBranch();
  const r = await fetch(
    `https://api.github.com/repos/${repo}/contents/${encodeURIComponent(path)}?ref=${encodeURIComponent(branch)}`,
    {
      headers: {
        "Authorization": `Bearer ${getToken()}`,
        "Accept": "application/vnd.github+json",
        "User-Agent": "news-bot-dashboard",
      },
      cache: "no-store",
    }
  );
  if (r.status === 404) return null;
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`GitHub GET ${path}: ${r.status} ${txt.slice(0, 200)}`);
  }
  const data = await r.json();
  const content = Buffer.from(data.content, "base64").toString("utf-8");
  return { content, sha: data.sha };
}

export async function ghPutFile(path, content, sha, message) {
  const repo = getRepo();
  const branch = getBranch();
  const body = {
    message,
    content: Buffer.from(content, "utf-8").toString("base64"),
    branch,
  };
  if (sha) body.sha = sha;
  const r = await fetch(
    `https://api.github.com/repos/${repo}/contents/${encodeURIComponent(path)}`,
    {
      method: "PUT",
      headers: {
        "Authorization": `Bearer ${getToken()}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "news-bot-dashboard",
      },
      body: JSON.stringify(body),
    }
  );
  if (!r.ok) {
    const txt = await r.text();
    const err = new Error(`GitHub PUT ${path}: ${r.status} ${txt.slice(0, 200)}`);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

export function checkAuth(req) {
  const expected = process.env.DASHBOARD_PASSWORD;
  if (!expected) return false;
  const auth = req.headers["authorization"] || req.headers["Authorization"];
  if (!auth || !auth.startsWith("Bearer ")) return false;
  return auth.substring("Bearer ".length) === expected;
}

// keywords.json'u oku — yoksa boş liste döner
export async function readKeywords() {
  const file = await ghGetFile("keywords.json");
  if (!file) return { keywords: [], sha: null };
  try {
    return { keywords: JSON.parse(file.content), sha: file.sha };
  } catch {
    return { keywords: [], sha: file.sha };
  }
}

// keywords.json'u yaz — çakışma durumunda yeniden dener
export async function writeKeywords(updateFn, commitMessage) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const { keywords, sha } = await readKeywords();
    const updated = updateFn([...keywords]);
    if (JSON.stringify(updated) === JSON.stringify(keywords)) {
      return { keywords, changed: false };
    }
    const content = JSON.stringify(updated, null, 2) + "\n";
    try {
      await ghPutFile("keywords.json", content, sha, commitMessage);
      return { keywords: updated, changed: true };
    } catch (e) {
      if (e.status === 409 && attempt < 2) continue; // sha conflict, retry
      throw e;
    }
  }
  throw new Error("keywords.json güncellenemedi (çakışma)");
}
