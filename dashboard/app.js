// Haber Botu Dashboard - cluster-tabanli, GroundNews tarzi.

const state = {
  news: [],
  sources: [],
  activeSource: null,
  activeLean: "all",
  timeWindowHours: 24,   // 0 = tumu
  sortBy: "recent",      // recent | coverage | score
  expanded: new Set(),
  password: localStorage.getItem("dashboard_password") || "",
};

const LEAN_LABEL = { left: "SOL", neutral: "NÖTR", right: "SAĞ" };
const LEAN_SHORT = { left: "Sol", neutral: "Nötr", right: "Sağ" };

// ----- API -----
async function apiGet(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`GET ${path}: ${r.status}`);
  return r.json();
}

async function apiAuth(method, path, body) {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json", "Authorization": `Bearer ${state.password}` },
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
    throw new Error(`${method} ${path}: ${r.status} ${txt}`);
  }
  return r.json();
}

async function loadData() {
  try {
    const data = await apiGet("/api/data");
    state.news = data.news || [];
    state.sources = data.sources || [];
    document.getElementById("last-updated").textContent =
      `Son haber: ${data.updated_at ? formatTime(data.updated_at) : "henüz veri yok"}`;
    render();
  } catch (e) {
    toast("Veri yüklenemedi: " + e.message, "error");
  }
}

// ----- Filtreleme & cluster -----
function filteredNews() {
  let items = state.news;
  if (state.timeWindowHours > 0) {
    const cutoff = Date.now() - state.timeWindowHours * 3600 * 1000;
    items = items.filter(n => {
      const t = Date.parse(n.ts);
      return !isNaN(t) && t >= cutoff;
    });
  }
  if (state.activeSource) items = items.filter(n => n.source === state.activeSource);
  return items;
}

function clusterStories(items) {
  const map = new Map();
  for (const n of items) {
    const cid = n.cluster_id || `solo:${n.link}`;
    if (!map.has(cid)) map.set(cid, []);
    map.get(cid).push(n);
  }
  const stories = [];
  for (const [cid, members] of map.entries()) {
    members.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
    const top = members[0];
    const leanCounts = { left: 0, neutral: 0, right: 0 };
    for (const m of members) {
      const l = m.lean && leanCounts.hasOwnProperty(m.lean) ? m.lean : "neutral";
      leanCounts[l]++;
    }
    const total = members.length || 1;
    const latestTs = members.map(m => m.ts).sort().reverse()[0];
    stories.push({
      id: cid,
      top,
      members,
      leanCounts,
      leanPct: {
        left: Math.round((leanCounts.left / total) * 100),
        neutral: Math.round((leanCounts.neutral / total) * 100),
        right: Math.round((leanCounts.right / total) * 100),
      },
      latestTs,
    });
  }
  // Lean filter: dominantLean'i state.activeLean ile karsilastir
  let filtered = stories;
  if (state.activeLean !== "all") {
    filtered = stories.filter(s => {
      const lc = s.leanCounts;
      const dom = Object.entries(lc).reduce((a, b) => (b[1] > a[1] ? b : a));
      return dom[0] === state.activeLean;
    });
  }
  // Sort
  if (state.sortBy === "recent") {
    filtered.sort((a, b) => (b.latestTs || "").localeCompare(a.latestTs || ""));
  } else if (state.sortBy === "coverage") {
    filtered.sort((a, b) => b.members.length - a.members.length || (b.latestTs || "").localeCompare(a.latestTs || ""));
  } else if (state.sortBy === "score") {
    filtered.sort((a, b) => (b.top.score ?? 0) - (a.top.score ?? 0));
  }
  return filtered;
}

// ----- Render -----
function render() {
  renderSources();
  renderStats();
  renderStories();
}

function renderSources() {
  const ul = document.getElementById("source-list");
  ul.innerHTML = "";
  const filtered = filteredNews();
  const countByLabel = new Map();
  for (const n of filtered) {
    countByLabel.set(n.source, (countByLabel.get(n.source) || 0) + 1);
  }

  const allLi = document.createElement("li");
  allLi.className = "source-item" + (state.activeSource === null ? " active" : "");
  allLi.innerHTML = `<span class="name">Tümü</span><span class="count">${filtered.length}</span>`;
  allLi.onclick = () => { state.activeSource = null; render(); };
  ul.appendChild(allLi);

  for (const src of state.sources) {
    const count = countByLabel.get(src.label) || 0;
    const li = document.createElement("li");
    li.className = "source-item" + (state.activeSource === src.label ? " active" : "");
    li.innerHTML = `
      <span class="name">
        <span class="dot lean-${src.default_lean}"></span>
        ${escape(src.label)}
        ${src.builtin ? "" : '<span class="custom-tag">özel</span>'}
      </span>
      <span class="count">${count}</span>
      ${src.builtin ? "" : `<button class="delete" title="Sil" aria-label="Sil">✕</button>`}
    `;
    li.querySelector(".name").onclick = () => { state.activeSource = src.label; render(); };
    li.querySelector(".count").onclick = () => { state.activeSource = src.label; render(); };
    const del = li.querySelector(".delete");
    if (del) {
      del.onclick = (e) => { e.stopPropagation(); deleteFeed(src.url, src.label); };
    }
    ul.appendChild(li);
  }
  document.getElementById("source-count").textContent = `${state.sources.length} aktif`;
}

function renderStats() {
  const panel = document.getElementById("stats-panel");
  const items = filteredNews();
  const stories = clusterStories(items);
  const leanTotal = { left: 0, neutral: 0, right: 0 };
  for (const n of items) {
    if (leanTotal.hasOwnProperty(n.lean)) leanTotal[n.lean]++;
  }
  const total = items.length || 1;
  panel.innerHTML = `
    <div class="panel-head"><h2>Özet</h2></div>
    <div class="stat-row"><span class="label">Hikayeler</span><span class="value">${stories.length}</span></div>
    <div class="stat-row"><span class="label">Toplam haber</span><span class="value">${items.length}</span></div>
    <div class="stat-row"><span class="label"><span class="dot lean-left"></span>Sol</span><span class="value">${Math.round(leanTotal.left/total*100)}%</span></div>
    <div class="stat-row"><span class="label"><span class="dot lean-neutral"></span>Nötr</span><span class="value">${Math.round(leanTotal.neutral/total*100)}%</span></div>
    <div class="stat-row"><span class="label"><span class="dot lean-right"></span>Sağ</span><span class="value">${Math.round(leanTotal.right/total*100)}%</span></div>
  `;
}

function renderStories() {
  const container = document.getElementById("story-container");
  const stories = clusterStories(filteredNews());

  document.getElementById("feed-title").textContent =
    state.activeSource ? `Hikayeler — ${state.activeSource}` : "Hikayeler";
  document.getElementById("feed-count").textContent =
    `${stories.length} hikaye · ${filteredNews().length} haber`;

  if (stories.length === 0) {
    container.innerHTML = `<div class="empty">Bu filtreyle eşleşen haber yok.<br><span class="small">Tarih aralığını genişletmeyi dene.</span></div>`;
    return;
  }

  container.innerHTML = stories.map(s => renderStoryCard(s)).join("");

  // Toggle handlers
  container.querySelectorAll(".story-card").forEach(card => {
    const id = card.dataset.storyId;
    if (state.expanded.has(id)) card.classList.add("expanded");
    const toggle = card.querySelector(".toggle");
    if (toggle) {
      toggle.onclick = () => {
        if (state.expanded.has(id)) state.expanded.delete(id);
        else state.expanded.add(id);
        card.classList.toggle("expanded");
      };
    }
  });
}

function renderStoryCard(s) {
  const top = s.top;
  const topicTag = topicLabel(top.title, top.summary_tr || "");
  const leanBar = renderLeanBar(s.leanPct);
  const coverageRows = s.members.map(m => `
    <div class="coverage-row">
      <span class="coverage-source"><span class="dot lean-${m.lean || 'neutral'}"></span>${escape(m.source || "—")}</span>
      <div class="coverage-headline">
        <a href="${escape(m.link)}" target="_blank" rel="noopener">${escape(m.title)}</a>
        ${m.summary_tr ? `<span class="source-summary">${escape(m.summary_tr)}</span>` : ""}
      </div>
      <span class="lean-tag lean-${m.lean || 'neutral'}">${LEAN_SHORT[m.lean] || "Nötr"}</span>
    </div>
  `).join("");

  return `
    <article class="story-card" data-story-id="${escape(s.id)}">
      <div class="story-meta">
        <span class="topic">${escape(topicTag)}</span>
        <span class="dot-sep">·</span>
        <span>${formatTime(s.latestTs)}</span>
        <span class="dot-sep">·</span>
        <span>${s.members.length} kaynak</span>
      </div>
      <h3 class="story-title">
        <a href="${escape(top.link)}" target="_blank" rel="noopener">${escape(top.title)}</a>
      </h3>
      ${top.summary_tr ? `<p class="story-summary">${escape(top.summary_tr)}</p>` : ""}
      ${leanBar}
      <div class="story-footer">
        <span class="source-count">${s.members.length} ${s.members.length === 1 ? "kaynak" : "kaynak"}</span>
        ${s.members.length > 1 ? `<button class="toggle">Tüm başlıklar <span class="toggle-icon">▾</span></button>` : ""}
      </div>
      ${s.members.length > 1 ? `<div class="coverage-list">${coverageRows}</div>` : ""}
    </article>
  `;
}

function renderLeanBar(pct) {
  const segs = [
    { key: "left",    pct: pct.left,    label: pct.left },
    { key: "neutral", pct: pct.neutral, label: pct.neutral },
    { key: "right",   pct: pct.right,   label: pct.right },
  ];
  return `
    <div class="lean-bar">
      ${segs.map(s => s.pct > 0
        ? `<div class="lean-bar-segment lean-${s.key}" style="flex:${s.pct}">${s.pct}%</div>`
        : `<div class="lean-bar-segment empty"></div>`
      ).join("")}
    </div>
  `;
}

function topicLabel(title, summary) {
  const blob = (title + " " + summary).toLowerCase();
  if (/\b(fed|ecb|imf|nato|putin|trump|xi |netanyahu|iran|ukrayna|gazze|israil|white house|sanctions|yaptirim)\b/.test(blob)) return "Küresel";
  if (/\b(faiz|enflasyon|tcmb|dolar|euro|borsa|bist|tahvil|butce|hazine|maliye|vergi|buyume|issizlik|petrol|brent|altin)\b/.test(blob)) return "Ekonomi";
  if (/\b(tbmm|meclis|chp|akp|mhp|iyi parti|deva|saadet|erdogan|babacan|ozel|imamoglu|atama|istifa|yargi|mahkeme)\b/.test(blob)) return "Siyaset";
  return "Genel";
}

// ----- Custom feeds -----
async function addFeed({ url, label, default_lean }) {
  if (!state.password) {
    pendingAction = () => addFeed({ url, label, default_lean });
    showPasswordModal();
    return;
  }
  try {
    await apiAuth("POST", "/api/feeds", { url, label, default_lean });
    toast(`"${label || url}" eklendi`, "success");
    await loadData();
  } catch (e) {
    if (e.message !== "unauthorized") toast("Eklenemedi: " + e.message, "error");
  }
}

async function deleteFeed(url, label) {
  if (!confirm(`"${label}" kaynak silinsin mi?`)) return;
  if (!state.password) {
    pendingAction = () => deleteFeed(url, label);
    showPasswordModal();
    return;
  }
  try {
    await apiAuth("DELETE", "/api/feeds", { url });
    toast(`"${label}" silindi`, "success");
    if (state.activeSource === label) state.activeSource = null;
    await loadData();
  } catch (e) {
    if (e.message !== "unauthorized") toast("Silinemedi: " + e.message, "error");
  }
}

// ----- Password -----
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
  try {
    const r = await fetch("/api/feeds", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${pw}` },
      body: JSON.stringify({ url: "__ping__" }),
    });
    return r.status !== 401;
  } catch {
    return false;
  }
}

// ----- Helpers -----
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
  const diff = (Date.now() - d) / 1000;
  if (diff < 60) return "az önce";
  if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} sa önce`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} gün önce`;
  return d.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

function toast(msg, kind = "") {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = `toast ${kind}`;
  setTimeout(() => el.classList.add("hidden"), 2800);
}

// ----- Event listeners -----
document.getElementById("time-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  document.querySelectorAll("#time-filters .chip").forEach(c => c.classList.remove("active"));
  btn.classList.add("active");
  state.timeWindowHours = Number(btn.dataset.time);
  render();
});

document.getElementById("lean-filters").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  document.querySelectorAll("#lean-filters .chip").forEach(c => c.classList.remove("active"));
  btn.classList.add("active");
  state.activeLean = btn.dataset.lean;
  render();
});

document.getElementById("sort-by").addEventListener("change", (e) => {
  state.sortBy = e.target.value;
  render();
});

document.getElementById("add-feed-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const url = document.getElementById("feed-url").value.trim();
  const label = document.getElementById("feed-label").value.trim();
  const default_lean = document.getElementById("feed-lean").value;
  if (!url) return;
  document.getElementById("feed-url").value = "";
  document.getElementById("feed-label").value = "";
  addFeed({ url, label, default_lean });
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

// ----- Init -----
loadData();
setInterval(loadData, 5 * 60 * 1000);
