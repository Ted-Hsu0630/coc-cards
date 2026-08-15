"use strict";

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

const state = {
  cards: [],
  cardById: {},
  series: [],
  maxCount: 10,
  counts: {},
  dirty: false,
  me: null,
};

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

// 別人的庫存有多舊。配對是拿對方存下來的資料在算，三週沒更新的表算出來的
// 結果不能當真 —— 這個標示就是讓人自己判斷要不要相信。
function minutesSince(iso) {
  if (!iso) return null;
  const t = new Date(iso);
  if (isNaN(t)) return null;
  return Math.floor((Date.now() - t) / 60000);
}

// 挑撐得住的最大單位：剛存完看到「180 分鐘前」很難換算，隔了三週看到
// 「30240 分鐘前」更沒意義。時鐘有偏差時 mins 會是負的，一律當「剛剛」。
function lastUpdated(iso) {
  const mins = minutesSince(iso);
  if (mins === null) return "未知";
  if (mins < 1) return "剛剛";
  if (mins < 60) return `${mins} 分鐘前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小時前`;
  return `${Math.floor(hours / 24)} 天前`;
}

// 多久算舊。沒有精算過，是「一週沒動就該打折看待」。
const STALE_MINUTES = 7 * 24 * 60;

// 未知一律視為舊 —— 不知道有多舊，就不該讓它看起來是新的。
function isStale(iso) {
  const mins = minutesSince(iso);
  return mins === null || mins >= STALE_MINUTES;
}

const cardName = (id) => {
  const c = state.cardById[id];
  return c ? c.name_zh || c.name_en || id : id;
};

/* ---------- 取得權杖的圖解 ---------- */

// 登入頁與「加綁小號」都要這份說明，內容一模一樣，所以放在 <template> 裡複製，
// 不在 HTML 重複寫兩份（圖片 URL 相同，瀏覽器只會下載一次）。
function mountGuides() {
  const tpl = $("#tokenGuide");
  if (!tpl) return;
  for (const slot of document.querySelectorAll("[data-guide]")) {
    if (slot.dataset.mounted) continue;
    slot.append(tpl.content.cloneNode(true));
    slot.dataset.mounted = "1";
  }
}
mountGuides();

/* ---------- 檢視切換 ---------- */

function show(view) {
  for (const s of document.querySelectorAll(".view")) s.hidden = s.id !== `view-${view}`;
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.view === view);
  if (view === "matches") loadMatches();
  if (view === "clan") loadClan();
  if (view === "villages") renderVillages();
}

for (const b of document.querySelectorAll(".tabs button")) {
  b.addEventListener("click", () => show(b.dataset.view));
}

/* ---------- 登入 ---------- */

$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#loginError");
  err.hidden = true;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    await api("/api/players/verify", {
      method: "POST",
      body: JSON.stringify({ tag: $("#tagInput").value, token: $("#tokenInput").value }),
    });
    $("#tokenInput").value = "";
    await boot();
  } catch (e2) {
    err.textContent = e2.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
});

$("#logoutBtn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  location.reload();
});

$("#villagePicker").addEventListener("change", async (e) => {
  await api("/api/me/active", { method: "POST", body: JSON.stringify({ tag: e.target.value }) });
  await loadCollection();
  show("collection");
});

/* ---------- 收藏表 ---------- */

function renderCollection() {
  const body = $("#collectionBody");
  body.textContent = "";

  for (const s of state.series) {
    const card = el("div", "card");
    const title = el("h2", "series-title");
    const sw = el("span", `swatch`);
    sw.style.background = `var(--${s.key})`;
    title.append(sw, el("span", null, `${s.name_zh}卡牌`));
    card.append(title);

    const grid = el("div", "grid");
    for (const c of state.cards.filter((c) => c.series === s.key)) {
      grid.append(renderSlot(c));
    }
    card.append(grid);
    body.append(card);
  }
  updateProgress();
}

function renderSlot(c) {
  const n = state.counts[c.id] || 0;
  const slot = el("div", `slot ${c.series}`);
  slot.dataset.id = c.id;

  const name = el("div", "name" + (c.confirmed ? "" : " unconfirmed"), cardName(c.id));
  if (!c.confirmed) name.title = "名稱尚未與遊戲畫面核對";

  const sel = el("select");
  for (let i = 0; i <= state.maxCount; i++) {
    const o = el("option", null, i === 0 ? "缺" : String(i));
    o.value = String(i);
    if (i === n) o.selected = true;
    sel.append(o);
  }
  sel.addEventListener("change", () => {
    state.counts[c.id] = Number(sel.value);
    state.dirty = true;
    paintSlot(slot, Number(sel.value));
    updateProgress();
    markDirty();
  });

  slot.append(name, sel);
  paintSlot(slot, n);
  return slot;
}

function paintSlot(slot, n) {
  slot.classList.toggle("missing", n === 0);
  slot.classList.toggle("spare", n >= 2);
}

function updateProgress() {
  const row = $("#seriesProgress");
  row.textContent = "";
  for (const s of state.series) {
    const ids = state.cards.filter((c) => c.series === s.key);
    const have = ids.filter((c) => (state.counts[c.id] || 0) > 0).length;
    row.append(el("div", `pill ${s.key}`, `${s.name_zh} ${have}/${ids.length}`));
  }
  const total = state.cards.length;
  const have = state.cards.filter((c) => (state.counts[c.id] || 0) > 0).length;
  row.append(el("div", "pill total", `共 ${have}/${total}`));
}

function markDirty() {
  $("#saveState").textContent = state.dirty ? "尚未儲存" : "已儲存";
  $("#saveBtn").disabled = !state.dirty;
}

$("#saveBtn").addEventListener("click", async () => {
  $("#saveBtn").disabled = true;
  $("#saveState").textContent = "儲存中…";
  try {
    await api("/api/collection", { method: "PUT", body: JSON.stringify({ counts: state.counts }) });
    state.dirty = false;
    markDirty();
    $("#saveState").textContent = "已儲存";
  } catch (e) {
    $("#saveState").textContent = `儲存失敗：${e.message}`;
    $("#saveBtn").disabled = false;
  }
});

window.addEventListener("beforeunload", (e) => {
  if (state.dirty) e.preventDefault();
});

async function loadCollection() {
  const data = await api("/api/collection");
  state.counts = data.counts || {};
  state.dirty = false;
  renderCollection();
  markDirty();
}

/* ---------- 配對 ---------- */

$("#sameClanOnly").addEventListener("change", loadMatches);

async function loadMatches() {
  const body = $("#matchBody");
  body.textContent = "";
  body.append(el("div", "empty", "計算中…"));

  const same = $("#sameClanOnly").checked ? "1" : "0";
  let data;
  try {
    data = await api(`/api/matches?same_clan=${same}`);
  } catch (e) {
    body.textContent = "";
    body.append(el("div", "empty", `讀取失敗：${e.message}`));
    return;
  }

  $("#syncWarn").hidden = data.clan_sync_ok !== false;
  $("#matchSummary").textContent =
    `已收集 ${data.collected}/${state.cards.length}　缺 ${data.missing.length} 張　` +
    `可送出 ${data.spares.length} 種　已建表 ${data.total_players} 人`;

  body.textContent = "";
  if (!data.matches.length) {
    body.append(
      el("div", "empty", data.total_players <= 1
        ? "還沒有其他人建表。把網址分享給部落成員。"
        : "目前沒有可成立的交換。")
    );
    return;
  }
  for (const m of data.matches) body.append(renderMatch(m));
}

const KIND_LABEL = { mutual: "互惠", incoming: "我受益", outgoing: "我幫忙" };
const INITIATOR_LABEL = {
  either: "雙方皆可發起",
  me: "由你發起",
  them: "由對方發起",
};

function renderMatch(m) {
  const card = el("div", `card match ${m.kind}`);

  const head = el("div", "match-head");
  head.append(el("span", "who", m.name));
  head.append(el("span", `tag ${m.kind}`, KIND_LABEL[m.kind]));
  if (!m.same_clan) head.append(el("span", "tag offclan", "不同部落"));
  card.append(head);

  const sub = el("p", "hint");
  sub.textContent =
    `${m.clan_name || "無部落"}　${INITIATOR_LABEL[m.initiator]}　` +
    `這一組最多換 ${m.trades} 次` +
    (m.gain ? `（補你 ${m.gain} 張` : "（") +
    (m.help ? `${m.gain ? "、" : ""}補對方 ${m.help} 張` : "") +
    "）";
  card.append(sub);

  card.append(el("p", isStale(m.collection_updated_at) ? "hint stale" : "hint",
    `庫存更新：${lastUpdated(m.collection_updated_at)}`));

  for (const s of m.series) card.append(renderSwap(s));
  return card;
}

// 交換是一對一：從「送出」挑一張，換「收到」的一張。所以直接把可換的次數
// 配成明確的成對顯示，其餘的列成備選 —— 排成兩排讓人自己配的話，
// 很容易被讀成「這三張一起送出換那一張」而白白浪費卡。
function renderSwap(s) {
  const box = el("div", "swap");
  const meta = state.series.find((x) => x.key === s.series);
  const head = el("div", "lbl");
  head.append(el("b", null, meta ? meta.name_zh : s.series));
  head.append(document.createTextNode(`　${KIND_LABEL[s.kind]}　可換 ${s.trades} 次`));
  box.append(head);

  for (let i = 0; i < s.trades; i++) {
    const row = el("div", "pairing");
    row.append(el("span", "chip give", cardName(s.i_give[i])));
    row.append(el("span", "arrow", "⇄"));
    row.append(el("span", "chip get", cardName(s.i_get[i])));
    box.append(row);
  }

  // 單向交換時清單是按張數展開的（同一張多份會重複），備選要去重才不會洗版
  const restGive = [...new Set(s.i_give.slice(s.trades))];
  const restGet = [...new Set(s.i_get.slice(s.trades))];
  if (restGive.length) box.append(altRow("送出可改用", restGive, "give"));
  if (restGet.length) box.append(altRow("收到可改挑", restGet, "get"));
  return box;
}

function altRow(label, ids, cls) {
  const wrap = el("div", "alt");
  wrap.append(el("span", "alt-label", label + "："));
  for (const id of ids) wrap.append(el("span", `chip ${cls} faded`, cardName(id)));
  return wrap;
}

/* ---------- 部落總覽 ---------- */

$("#clanSameOnly").addEventListener("change", loadClan);

async function loadClan() {
  const body = $("#clanBody");
  body.textContent = "";
  body.append(el("div", "empty", "讀取中…"));
  let data;
  try {
    data = await api(`/api/clan/overview?same_clan=${$("#clanSameOnly").checked ? "1" : "0"}`);
  } catch (e) {
    body.textContent = "";
    body.append(el("div", "empty", `讀取失敗：${e.message}`));
    return;
  }

  body.textContent = "";
  const card = el("div", "card");
  const table = el("table");
  const thead = el("thead");
  const hr = el("tr");
  for (const h of ["玩家", "部落", "已收集", "庫存更新"]) hr.append(el("th", null, h));
  thead.append(hr);
  table.append(thead);

  const tbody = el("tbody");
  for (const p of data.players) {
    const tr = el("tr");
    tr.append(el("td", null, p.name));
    const clan = el("td", null, p.clan_name || "無部落");
    if (!p.same_clan) clan.style.color = "var(--warn)";
    tr.append(clan);
    tr.append(el("td", "num", p.has_data ? `${p.collected}/${p.total}` : "未建表"));

    const upd = el("td", "num", p.has_data ? lastUpdated(p.collection_updated_at) : "—");
    if (p.has_data && isStale(p.collection_updated_at)) upd.classList.add("stale");
    tr.append(upd);
    tbody.append(tr);
  }
  table.append(tbody);
  card.append(table);
  body.append(card);
}

/* ---------- 村莊管理 ---------- */

const GRIP_SVG =
  '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">' +
  '<circle cx="6" cy="3" r="1.4"/><circle cx="10" cy="3" r="1.4"/>' +
  '<circle cx="6" cy="8" r="1.4"/><circle cx="10" cy="8" r="1.4"/>' +
  '<circle cx="6" cy="13" r="1.4"/><circle cx="10" cy="13" r="1.4"/></svg>';

function renderVillages() {
  const list = $("#villageList");
  list.textContent = "";
  const many = state.me.players.length > 1;

  if (many) {
    const tip = el("div", "card");
    tip.append(el("p", "hint", "拖曳左側握把可調整順序。"));
    list.append(tip);
  }

  for (const p of state.me.players) {
    const card = el("div", "card village");
    card.dataset.tag = p.tag;
    const row = el("div", "village-row");

    if (many) {
      const grip = el("div", "grip");
      grip.innerHTML = GRIP_SVG;          // 固定字串，非使用者輸入
      grip.title = "拖曳調整順序";
      row.append(grip);
    }

    const who = el("div", "who");
    who.append(el("div", null, `${p.name}　${p.tag}`));
    who.append(el("div", "hint", p.clan_name || "無部落"));
    row.append(who);

    if (many) {
      const btn = el("button", "ghost", "解除綁定");
      btn.addEventListener("click", async () => {
        if (!confirm(`解除綁定 ${p.name}？收藏紀錄會一併刪除。`)) return;
        await api(`/api/players/${encodeURIComponent(p.tag)}`, { method: "DELETE" });
        await boot();
        show("villages");
      });
      row.append(btn);
    }
    card.append(row);
    list.append(card);
  }
}

// 用 Pointer Events 自己實作，不用 HTML5 的 draggable —— 後者在行動瀏覽器上
// 完全不會觸發，而這個站主要就是手機在用。
// 只有握把能起拖，頁面本身才捲得動，「解除綁定」也還按得到。
(function enableVillageDrag() {
  const list = $("#villageList");
  let card = null;
  let startY = 0;
  let originalOrder = [];

  const cards = () => [...list.querySelectorAll(".village")];

  list.addEventListener("pointerdown", (e) => {
    const grip = e.target.closest(".grip");
    if (!grip) return;
    card = grip.closest(".village");
    if (!card) return;

    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    startY = e.clientY;
    originalOrder = cards().map((c) => c.dataset.tag);
    card.classList.add("dragging");
  });

  list.addEventListener("pointermove", (e) => {
    if (!card) return;
    card.style.transform = `translateY(${e.clientY - startY}px)`;

    const mid = (n) => {
      const r = n.getBoundingClientRect();
      return r.top + r.height / 2;
    };
    const myMid = mid(card);
    const prev = card.previousElementSibling?.classList.contains("village")
      ? card.previousElementSibling : null;
    const next = card.nextElementSibling?.classList.contains("village")
      ? card.nextElementSibling : null;

    let target = null;
    if (prev && myMid < mid(prev)) target = prev;
    else if (next && myMid > mid(next)) target = next;
    if (!target) return;

    // 搬動 DOM 之後版面位置變了，要把基準點補回來，否則卡片會憑空跳一格
    const before = card.getBoundingClientRect().top;
    if (target === prev) list.insertBefore(card, prev);
    else list.insertBefore(next, card);
    startY += card.getBoundingClientRect().top - before;
  });

  const finish = async () => {
    if (!card) return;
    card.style.transform = "";
    card.classList.remove("dragging");
    card = null;

    const order = cards().map((c) => c.dataset.tag);
    if (order.join() === originalOrder.join()) return;

    try {
      await api("/api/me/order", { method: "POST", body: JSON.stringify({ tags: order }) });
      await boot();
      show("villages");
    } catch (e) {
      alert(`調整順序失敗：${e.message}`);
      await boot();
      show("villages");
    }
  };

  list.addEventListener("pointerup", finish);
  list.addEventListener("pointercancel", finish);
})();

$("#addForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("#addError");
  err.hidden = true;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const r = await api("/api/players/verify", {
      method: "POST",
      body: JSON.stringify({ tag: $("#addTag").value, token: $("#addToken").value }),
    });
    $("#addTag").value = "";
    $("#addToken").value = "";
    if (r.migrated) {
      // 這個村莊本來是獨立帳號，剛剛被併進來。收藏是照 tag 存的，所以原封不動。
      alert(`已將「${r.player.name}」併入此帳號，收藏保留。`);
    }
    await boot();
    show("collection");
  } catch (e2) {
    err.textContent = e2.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
});

/* ---------- 截圖匯入 ---------- */

// 辨識出來的結果放這裡，**在按下「套用」之前完全不碰 state.counts**。
// 使用者可能看一看就切走，那不該留下任何痕跡。
const imp = { rows: [], picks: {} };

const IMPORT_STATE = {
  read: { label: "讀出來了", cls: "ok" },
  unknown: { label: "認不出", cls: "warn" },
  conflict: { label: "兩張截圖不一致", cls: "bad" },
  uncovered: { label: "沒拍到", cls: "dim" },
};

// 這格最後會寫進去的值。使用者沒選就沿用資料庫現值 ——
// 回傳 null 代表「不要動它」，跟「設成 0」是兩回事。
function importFinal(row) {
  if (row.id in imp.picks) return imp.picks[row.id];
  if (row.state === "read") return row.value;
  return null;
}

function importCountSelect(row, onPick) {
  const sel = el("select");
  const blank = el("option", null, row.state === "read" ? "" : "— 未填 —");
  blank.value = "";
  sel.append(blank);
  for (let i = 0; i <= state.maxCount; i++) {
    const o = el("option", null, String(i));
    o.value = String(i);
    sel.append(o);
  }
  const cur = importFinal(row);
  sel.value = cur === null || cur === undefined ? "" : String(cur);
  sel.addEventListener("change", () => {
    if (sel.value === "") delete imp.picks[row.id];
    else imp.picks[row.id] = Number(sel.value);
    onPick();
  });
  return sel;
}

function importRow(row, onPick) {
  const wrap = el("div", "imp-row");
  const meta = IMPORT_STATE[row.state];

  const left = el("div", "imp-name");
  left.append(el("span", "imp-card", row.name));
  const tag = el("span", `tag ${meta.cls}`, meta.label);
  left.append(tag);
  if (row.note) left.append(el("p", "hint", row.note));
  if (row.state !== "read") {
    const cur = row.current === null || row.current === undefined ? 0 : row.current;
    left.append(el("p", "hint", `不填維持 ${cur} 張`));
  }

  wrap.append(left, importCountSelect(row, onPick));
  return wrap;
}

function renderImport(data) {
  imp.rows = data.cards;
  imp.picks = {};

  const list = $("#importFileList");
  list.textContent = "";
  for (const f of data.files) {
    const li = el("li", f.ok ? "ok" : "bad");
    if (f.ok) {
      const exact = f.exact ? "" : "（位置較不確定，建議核對）";
      li.textContent = `${f.name} —— 相簿第 ${f.range[0]}~${f.range[1]} 張${exact}`;
    } else {
      li.textContent = `${f.name} —— 不採用：${f.reason}`;
    }
    list.append(li);
  }

  redrawImport();
  $("#importResult").hidden = false;
}

function redrawImport() {
  const s = { read: 0, unknown: 0, conflict: 0, uncovered: 0 };
  for (const r of imp.rows) s[r.state]++;
  const filled = imp.rows.filter((r) => r.state !== "read" && importFinal(r) !== null).length;
  const left = imp.rows.length - s.read - filled;

  const sum = $("#importSummary");
  sum.textContent = "";
  sum.append(el("h2", null, `60 張裡讀出 ${s.read} 張`));
  if (left > 0) {
    sum.append(el("p", "warn", `還有 ${left} 張沒有值，將保持原樣。`));
  } else {
    sum.append(el("p", "hint", "全部都有值了。"));
  }
  const brk = [];
  if (s.unknown) brk.push(`認不出 ${s.unknown}`);
  if (s.conflict) brk.push(`兩張截圖不一致 ${s.conflict}`);
  if (s.uncovered) brk.push(`沒拍到 ${s.uncovered}`);
  if (brk.length) sum.append(el("p", "hint", brk.join("　·　")));

  // 跟遊戲內進度條核對 —— 有格子沒讀到就報不出可比的數字，這時說清楚為什麼
  // 跟畫面上方的進度條對照。那個數字是遊戲自己算的，跟辨識完全無關，
  // 所以對得上才是真的有意義的驗證 —— 不是自己跟自己說沒問題。
  const chk = el("div", "checkrow");
  const groups = state.importSeries || [];
  const compared = groups.filter((g) => g.owned !== null && g.expected !== null);
  const bad = compared.filter((g) => g.owned !== g.expected);

  if (compared.length && !bad.length) {
    chk.append(el("p", "ok-note", `已與進度條核對 ${compared.length} 個系列，數字相符。`));
  } else if (bad.length) {
    chk.append(el("p", "error", "與進度條不符，請逐格檢查："));
  } else {
    chk.append(el("p", "hint", "各系列已擁有張數（進度條讀不到，無法自動核對）："));
  }

  for (const g of groups) {
    let text, cls;
    if (g.owned === null && g.expected === null) {
      text = `${g.name} —`;
      cls = "faded";
    } else if (g.owned === null) {
      text = `${g.name} 畫面上是 ${g.expected}/${g.total}（還有 ${g.missing} 格沒讀到）`;
      cls = "faded";
    } else if (g.expected === null) {
      text = `${g.name} ${g.owned}/${g.total}（進度條讀不到，沒得核對）`;
      cls = "faded";
    } else if (g.owned === g.expected) {
      text = `${g.name} ${g.owned}/${g.total} ✓`;
      cls = "good";
    } else {
      text = `${g.name} 讀到 ${g.owned}、畫面上是 ${g.expected} ✗`;
      cls = "wrong";
    }
    const chip = el("span", `chip ${cls}`, text);
    if (g.bar_note) chip.title = g.bar_note;
    chk.append(chip);
    if (g.bar_note) chk.append(el("p", "warn", g.bar_note));
  }
  sum.append(chk);

  const needs = imp.rows.filter((r) => r.state !== "read");
  const needsCard = $("#importNeedsCard");
  needsCard.hidden = needs.length === 0;
  if (needs.length) {
    $("#importNeedsTitle").textContent = `需要你確認的 ${needs.length} 張`;
    const box = $("#importNeeds");
    box.textContent = "";
    for (const r of needs) box.append(importRow(r, redrawImport));
  }

  const readBox = $("#importRead");
  readBox.textContent = "";
  for (const r of imp.rows.filter((x) => x.state === "read")) {
    readBox.append(importRow(r, redrawImport));
  }

  const willWrite = imp.rows.filter((r) => importFinal(r) !== null).length;
  $("#importApplyHint").textContent = `會寫入 ${willWrite} 張，其餘 ${60 - willWrite} 張維持原值`;
}

$("#importFiles").addEventListener("change", (e) => {
  $("#importBtn").disabled = e.target.files.length === 0;
});

$("#importForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const files = $("#importFiles").files;
  if (!files.length) return;

  const btn = $("#importBtn");
  const err = $("#importError");
  err.hidden = true;
  btn.disabled = true;
  btn.textContent = "辨識中…";
  try {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    // 不能用 api()：那個會塞 Content-Type: application/json，
    // multipart 的 boundary 必須讓瀏覽器自己帶
    const res = await fetch("/api/import/screenshots", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    state.importSeries = data.summary.series_owned;
    renderImport(data);
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "開始辨識";
  }
});

$("#importCancel").addEventListener("click", () => {
  $("#importResult").hidden = true;
  $("#importFiles").value = "";
  $("#importBtn").disabled = true;
  imp.rows = [];
  imp.picks = {};
});

$("#importApply").addEventListener("click", async () => {
  const next = { ...state.counts };
  for (const r of imp.rows) {
    const v = importFinal(r);
    if (v !== null) next[r.id] = v;      // null = 沒有值，維持原本的
  }
  const btn = $("#importApply");
  btn.disabled = true;
  try {
    await api("/api/collection", { method: "PUT", body: JSON.stringify({ counts: next }) });
    state.counts = next;
    state.dirty = false;
    $("#importCancel").click();
    await loadCollection();
    show("collection");
  } catch (ex) {
    $("#importError").textContent = ex.message;
    $("#importError").hidden = false;
  } finally {
    btn.disabled = false;
  }
});

/* ---------- 啟動 ---------- */

async function boot() {
  const meta = await api("/api/cards");
  state.cards = meta.cards;
  state.series = meta.series;
  state.maxCount = meta.max_count;
  state.cardById = Object.fromEntries(meta.cards.map((c) => [c.id, c]));

  const me = await api("/api/me");
  state.me = me;

  if (!me.logged_in) {
    $("#topbar").hidden = true;
    show("login");
    return;
  }

  $("#topbar").hidden = false;
  const picker = $("#villagePicker");
  picker.textContent = "";
  for (const p of me.players) {
    const o = el("option", null, `${p.name}（${p.clan_name || "無部落"}）`);
    o.value = p.tag;
    if (p.tag === me.active_tag) o.selected = true;
    picker.append(o);
  }

  // 沒裝 opencv 的伺服器不顯示這個分頁 —— 顯示了按下去只會拿到 501
  try {
    const cap = await api("/api/import/available");
    $('.tabs button[data-view="import"]').hidden = !cap.available;
    // 分頁在這支 API 回來之前是隱藏的，所以使用者不會看到空白的那一格
    $("#importMax").textContent = cap.max_images;
  } catch {
    /* 舊版伺服器沒有這支 API，維持隱藏就好 */
  }

  await loadCollection();
  show("collection");
}

boot().catch((e) => {
  document.body.innerHTML = `<main><div class="card"><p class="error">啟動失敗：${e.message}</p></div></main>`;
});
