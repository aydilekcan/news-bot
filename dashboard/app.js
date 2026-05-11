// X Bot Dashboard - vanilla JS, no framework

const state = {
  keywords: [],
  tweets: {},          // { keyword: [tweet, ...] }
  activeKeyword: null, // null = tüm tweetler
  password: localStorage.getItem("dashboard_password") || "",
};

// --- API çağrıları ---

async function apiGet(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`GET ${path} failed: ${r.status}`);
  return r.json();
}

async function apiAuth(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${state.password}`,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) {
    state.password = "";
    localStorage.removeItem("dashboard_password");
    showPasswordModal();
    throw new Error("unauthorized");
  }
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`${method} ${path} failed: ${r.status} ${txt}`);
  }
  return r.json();
}

// --- Veri yükle ---

async function loadData() {
  try {
    const data = await apiGet("/api/data");
    state.keywords = data.keywords || [];
    state.tweets = data.tweets || {};
    document.getElementById("last-updated").textContent =
      `Son güncelleme: ${data.updated_at ? formatTime(data.updated_at) : "henüz veri yok"}`;
    render();
  } catch (e) {
    toast("Veri yüklenemedi: " + e.message, "error");
  }
}

// --- Render ---

function render() {
  renderKeywordList();
  renderFilter();
  renderTweets();
  document.getElementById("keyword-count").textContent =
    `${state.keywords.length} kelime`;
}

function renderKeywordList() {
  const ul = document.getElementById("keyword-list");
  ul.innerHTML = "";

  // "Tümü" item
  const allLi = document.createElement("li");
  allLi.className = "keyword-item" + (state.activeKeyword === null ? " active" : "");
  const totalTweets = Object.values(state.tweets).reduce((s, arr) => s + arr.length, 0);
  allLi.innerHTML = `
    <span class="name">📊 Tümü</span>
    <span class="count">${totalTweets}</span>
  `;
  allLi.onclick = () => { state.activeKeyword = null; render(); };
  ul.appendChild(allLi);

  for (const kw of state.keywords) {
    const li = document.createElement("li");
    li.className = "keyword-item" + (state.activeKeyword === kw ? " active" : "");
    const count = (state.tweets[kw] || []).length;
    li.innerHTML = `
      <span class="name">${escape(kw)}</span>
      <span class="count">${count}</span>
      <button class="delete" title="Sil">✕</button>
    `;
    li.querySelector(".name").onclick = () => { state.activeKeyword = kw; render(); };
    li.querySelector(".count").onclick = () => { state.activeKeyword = kw; render(); };
    li.querySelector(".delete").onclick = (e) => {
      e.stopPropagation();
      deleteKeyword(kw);
    };
    ul.appendChild(li);
  }
}

function renderFilter() {
  const sel = document.getElementById("keyword-filter");
  const current = state.activeKeyword;
  sel.innerHTML = `<option value="">Tüm kelimeler</option>` +
    state.keywords.map(k => `<option value="${escape(k)}">${escape(k)}</option>`).join("");
  sel.value = current || "";
  sel.onchange = () => {
    state.activeKeyword = sel.value || null;
    render();
  };
}

function renderTweets() {
  const container = document.getElementById("tweets-container");
  const title = document.getElementById("tweets-title");

  let tweets;
  if (state.activeKeyword) {
    title.textContent = `Tweetler — ${state.activeKeyword}`;
    tweets = state.tweets[state.activeKeyword] || [];
  } else {
    title.textContent = "Tüm Tweetler";
    tweets = Object.values(state.tweets).flat();
  }

  tweets = [...tweets].sort((a, b) =>
    (b.captured_at || "").localeCompare(a.captured_at || "")
  );

  if (tweets.length === 0) {
    container.innerHTML = `<div class="empty">Bu kelime için henüz tweet kaydedilmemiş.</div>`;
    return;
  }

  container.innerHTML = tweets.map(t => `
    <article class="tweet">
      <div class="tweet-head">
        <div class="tweet-author">
          <span class="name">${escape(t.author?.name || t.author?.username || "?")} ${t.author?.verified ? "✓" : ""}</span>
          <span class="handle">@${escape(t.author?.username || "?")} · ${formatTime(t.captured_at)}</span>
        </div>
        <span class="tweet-keyword">${escape(t.keyword)}</span>
      </div>
      <div class="tweet-text">${escape(t.text || "")}</div>
      <div class="tweet-meta">
        <span>❤️ ${t.likes ?? 0}</span>
        <span>🔁 ${t.retweets ?? 0}</span>
        <span>👁 ${t.views ?? 0}</span>
        ${t.url ? `<a href="${escape(t.url)}" target="_blank" rel="noopener">Tweete git →</a>` : ""}
      </div>
    </article>
  `).join("");
}

// --- Mutations ---

async function addKeyword(keyword) {
  if (!state.password) {
    pendingAction = () => addKeyword(keyword);
    showPasswordModal();
    return;
  }
  try {
    await apiAuth("POST", "/api/keywords", { keyword });
    toast(`"${keyword}" eklendi`, "success");
    await loadData();
  } catch (e) {
    if (e.message !== "unauthorized") toast("Eklenemedi: " + e.message, "error");
  }
}

async function deleteKeyword(keyword) {
  if (!confirm(`"${keyword}" silinsin mi?`)) return;
  if (!state.password) {
    pendingAction = () => deleteKeyword(keyword);
    showPasswordModal();
    return;
  }
  try {
    await apiAuth("DELETE", "/api/keywords", { keyword });
    toast(`"${keyword}" silindi`, "success");
    if (state.activeKeyword === keyword) state.activeKeyword = null;
    await loadData();
  } catch (e) {
    if (e.message !== "unauthorized") toast("Silinemedi: " + e.message, "error");
  }
}

// --- Şifre modal ---

let pendingAction = null;

function showPasswordModal() {
  document.getElementById("password-modal").classList.remove("hidden");
  document.getElementById("password-input").focus();
}

function hidePasswordModal() {
  document.getElementById("password-modal").classList.add("hidden");
  document.getElementById("password-input").value = "";
  document.getElementById("password-error").classList.add("hidden");
}

async function tryPassword(pw) {
  // Auth'u doğrulamak için "ping" rolünde küçük bir istek
  state.password = pw;
  try {
    const r = await fetch("/api/keywords", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${pw}`,
      },
      body: JSON.stringify({ keyword: "__ping__" }),
    });
    if (r.status === 401) return false;
    // 400 (validation) veya 200/409 = şifre doğru
    return true;
  } catch {
    return false;
  }
}

// --- Helpers ---

function escape(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatTime(iso) {
  if (!iso) return "?";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "az önce";
  if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} sa önce`;
  return d.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

function toast(msg, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  setTimeout(() => el.classList.add("hidden"), 3000);
}

// --- Event listeners ---

document.getElementById("add-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("new-keyword");
  const kw = input.value.trim().toLowerCase();
  if (!kw) return;
  if (state.keywords.includes(kw)) {
    toast("Bu kelime zaten listede.", "error");
    return;
  }
  input.value = "";
  addKeyword(kw);
});

document.getElementById("refresh").addEventListener("click", loadData);

document.getElementById("lock-btn").addEventListener("click", () => {
  state.password = "";
  localStorage.removeItem("dashboard_password");
  toast("Çıkış yapıldı.", "success");
});

document.getElementById("cancel-password").addEventListener("click", () => {
  hidePasswordModal();
  pendingAction = null;
});

document.getElementById("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = document.getElementById("password-input").value;
  const ok = await tryPassword(pw);
  if (ok) {
    state.password = pw;
    localStorage.setItem("dashboard_password", pw);
    hidePasswordModal();
    const action = pendingAction;
    pendingAction = null;
    if (action) action();
  } else {
    document.getElementById("password-error").classList.remove("hidden");
  }
});

// --- Init ---
loadData();
setInterval(loadData, 5 * 60 * 1000); // 5 dakikada bir otomatik yenile
