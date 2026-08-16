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
  saved: {}, // 伺服器上那一份，「取消」要回到這裡
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

/* ---------- 卡片格子（收藏與匯入確認共用） ----------

   點卡面 +1，右上角的紅色小圈 − 減一。排 6 欄是照遊戲內相簿的排法。

   兩個畫面唯一的差別是「未填」：匯入確認要能表達「這格我不知道，不要動它」，
   那跟「設成 0 張」是兩回事（CLAUDE.md 紅線 10 —— 認不出的格子不可以猜）。
   收藏頁沒有未填，最小就是 0。差別只有 allowUnset 一個參數，兩邊共用同一份
   互動邏輯，否則兩個畫面的手感遲早會走鐘。
*/

function cardTile(card, { value, allowUnset = false, note = null, noteClass = "", onChange }) {
  const slot = el("div", `slot ${card.series}`);
  slot.dataset.id = card.id;

  // 卡面本身就是 +1 的按鈕。用 <button> 不是 <div>，鍵盤與螢幕閱讀器才進得來；
  // − 是它的**兄弟**不是子元素 —— 按鈕不能巢狀，巢了瀏覽器會自己拆掉。
  const tile = el("button", "tile");
  tile.type = "button";
  const img = el("img");
  img.src = `/static/img/cards/${card.id}.png`;
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  const dupe = el("span", "dupe");
  tile.append(img, dupe);

  const dec = el("button", "dec", "−");
  dec.type = "button";

  const name = el("div", "name" + (card.confirmed ? "" : " unconfirmed"), cardName(card.id));
  if (!card.confirmed) name.title = "名稱尚未與遊戲畫面核對";

  slot.append(tile, dec, name);
  if (note) slot.append(el("div", `tile-note ${noteClass}`, note));

  let v = value;
  const paint = () => {
    const unset = v === null || v === undefined;
    slot.classList.toggle("unset", unset);
    slot.classList.toggle("have", !unset && v > 0);
    dupe.textContent = !unset && v >= 2 ? `x${v}` : "";
    // 收藏頁 0 張時不需要 −；匯入確認的 0 還要能退回未填，所以照樣留著。
    dec.hidden = unset || (!allowUnset && v === 0);
    const label = unset ? "未填" : v === 0 ? "缺" : `${v} 張`;
    tile.setAttribute("aria-label", `${cardName(card.id)}，${label}`);
    dec.setAttribute("aria-label", `${cardName(card.id)} 減一`);
  };

  tile.addEventListener("click", () => {
    // 未填的第一下是 0 不是 1。「認不出」跟「我看過了，這張是 0 張」都是
    // 常見的結果，跳過 0 會逼人多按九下才回得來（min 是 0，減不到 -1）。
    const unset = v === null || v === undefined;
    v = unset ? (allowUnset ? 0 : 1) : Math.min(v + 1, state.maxCount);
    paint();
    onChange(v);
  });
  dec.addEventListener("click", () => {
    v = v > 0 ? v - 1 : allowUnset ? null : 0;
    paint();
    onChange(v);
  });

  paint();
  return slot;
}

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
      grid.append(
        cardTile(c, {
          value: state.counts[c.id] || 0,
          onChange: (n) => {
            state.counts[c.id] = n;
            state.dirty = true;
            updateProgress();
            markDirty();
          },
        }),
      );
    }
    card.append(grid);
    body.append(card);
  }
  updateProgress();
}

/* 進度條。

   **只建一次，之後改寬度不重建。** 每次都重畫的話 CSS transition 沒有舊值
   可以補間，動畫就完全不會發生 —— 收到一張新卡時進度只會瞬間跳過去。
   原本的膠囊排版在手機上會斷成兩行且左右對不齊，改成固定寬度的標籤加一條
   等寬的軌道，幾個系列的條就一定對得齊。 */
const bars = new Map();

function buildProgress() {
  const box = $("#seriesProgress");
  box.textContent = "";
  bars.clear();
  const rows = [...state.series.map((s) => [s.key, s.name_zh]), ["total", "全部"]];
  for (const [key, label] of rows) {
    const row = el("div", `bar-row ${key}`);
    const track = el("div", "bar-track");
    const fill = el("div", "bar-fill");
    const text = el("span", "bar-text");
    track.append(fill, text);
    row.append(el("span", "bar-label", label), track);
    box.append(row);
    bars.set(key, { fill, text });
  }
}

function updateProgress() {
  if (!bars.size) buildProgress();
  const count = (list) => list.filter((c) => (state.counts[c.id] || 0) > 0).length;
  for (const s of state.series) {
    const ids = state.cards.filter((c) => c.series === s.key);
    setBar(s.key, count(ids), ids.length);
  }
  setBar("total", count(state.cards), state.cards.length);
}

function setBar(key, have, total) {
  const b = bars.get(key);
  if (!b) return;
  b.fill.style.width = total ? `${(have / total) * 100}%` : "0%";
  b.text.textContent = `${have}/${total}`;
}

function markDirty() {
  $("#saveState").textContent = state.dirty ? "尚未儲存" : "已儲存";
  $("#saveBtn").disabled = !state.dirty;
  // 沒改過就沒東西好取消，按鈕直接不出現而不是變灰 —— 少一顆按鈕比多一顆
  // 按不動的按鈕乾淨。
  $("#revertBtn").hidden = !state.dirty;
}

$("#saveBtn").addEventListener("click", async () => {
  $("#saveBtn").disabled = true;
  $("#saveState").textContent = "儲存中…";
  try {
    await api("/api/collection", { method: "PUT", body: JSON.stringify({ counts: state.counts }) });
    state.saved = { ...state.counts }; // 存成功之後，「取消」的目標就是這一份
    state.dirty = false;
    markDirty();
    $("#saveState").textContent = "已儲存";
  } catch (e) {
    $("#saveState").textContent = `儲存失敗：${e.message}`;
    $("#saveBtn").disabled = false;
  }
});

// 回到伺服器上那一份。不重新發請求 —— 上次載到的東西就是原始資料，
// 而且斷線時「取消」還是該有用。
$("#revertBtn").addEventListener("click", () => {
  state.counts = { ...state.saved };
  state.dirty = false;
  renderCollection();
  markDirty();
  $("#saveState").textContent = "已還原";
});

window.addEventListener("beforeunload", (e) => {
  if (state.dirty) e.preventDefault();
});

async function loadCollection() {
  const data = await api("/api/collection");
  state.counts = data.counts || {};
  state.saved = { ...state.counts };
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
        ? "還沒有其他玩家資料"
        : "目前沒有可成立的交換")
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
    `最多換 ${m.trades} 次` +
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
    row.append(cardFace(s.i_give[i], "give", "你送出"));
    row.append(el("span", "arrow", "⇄"));
    row.append(cardFace(s.i_get[i], "get", "你收到"));
    box.append(row);
  }

  const restGive = alternatives(s.i_give, s.trades);
  const restGet = alternatives(s.i_get, s.trades);
  if (restGive.length) box.append(altRow("可改送", restGive, "give"));
  if (restGet.length) box.append(altRow("可改收", restGet, "get"));
  return box;
}

/* 配對用剩的才是備選。
   清單是按「張數」展開的（熔岩獵犬有 4 張就出現 3 次），所以光去重不夠 ——
   配對吃掉前面兩份之後，剩下的那份還是熔岩獵犬，會被印成「可改送：熔岩獵犬」，
   但上面兩列已經在送它了，而且可換次數就是 2 次，那第三份根本換不掉。
   扣掉已經配對的卡才是真正還有得選的。 */
function alternatives(list, trades) {
  const used = new Set(list.slice(0, trades));
  return [...new Set(list.slice(trades))].filter((id) => !used.has(id));
}

// 超過這個張數就收起來。「我受益」的對價可以是任何一張多的卡，實際看過列到
// 9 張的 —— 排滿兩排卡面看起來很重要，其實隨便挑一張都行，資訊量很低。
const ALT_SHOWN = 4;

function altRow(label, ids, cls) {
  const wrap = el("div", "alt");
  wrap.append(el("span", "alt-label", label + "："));
  for (const id of ids.slice(0, ALT_SHOWN)) wrap.append(cardFace(id, `${cls} faded`));

  const rest = ids.slice(ALT_SHOWN);
  if (!rest.length) return wrap;
  // 收起來但留得住：直接砍掉會讓人以為只有這幾張能選，而挑哪一張是他的決定
  const more = el("button", "ghost alt-more", `還有 ${rest.length} 張可選`);
  more.type = "button";
  more.addEventListener("click", () => {
    for (const id of rest) wrap.insertBefore(cardFace(id, `${cls} faded`), more);
    more.remove();
  });
  wrap.append(more);
  return wrap;
}

/* 配對畫面的小卡面。

   跟收藏頁用同一組圖（瀏覽器也已經快取過了），但這裡是唯讀的展示不是按鈕 ——
   純文字的卡名要讀完才知道是哪張，看圖是一眼的事，而使用者在遊戲裡本來就是
   照圖認卡。名字仍然留在圖下面：兩個系列的同名卡（飛龍寶寶）光看圖分不出來。

   role 是圖上面那行「送出 / 收到」。方向本來只靠邊框顏色分，而顏色代表什麼
   畫面上哪裡都沒寫 —— 記反了整份清單會整個讀反，實際發生過。 */
function cardFace(id, cls, role = null) {
  const box = el("div", `face ${cls}`);
  if (role) box.append(el("span", "face-role", role));
  const img = el("img");
  img.src = `/static/img/cards/${id}.png`;
  img.alt = "";
  img.loading = "lazy";
  img.decoding = "async";
  box.append(img, el("span", "face-name", cardName(id)));
  box.title = cardName(id);
  return box;
}

/* ---------- 多人配對 ----------

   雙人配對回答「我跟這個人能換什麼」，這裡回答「這一群人照什麼順序換」。
   因為換卡會改變狀態，而且存在真正的鏈（手上只有 1 張送不出去，收到一張
   變 2 張之後才送得掉），所以結果必須分步驟。同一步裡的交換只動用該步開始
   時就在手上的卡，彼此不影響順序，可以同時進行。 */

const plan = { people: [], picked: new Set(), favor: "" };

for (const b of document.querySelectorAll(".modes button")) {
  b.addEventListener("click", () => setMatchMode(b.dataset.mode));
}

function setMatchMode(mode) {
  for (const b of document.querySelectorAll(".modes button")) {
    b.classList.toggle("active", b.dataset.mode === mode);
  }
  $("#pairPane").hidden = mode !== "pair";
  $("#planPane").hidden = mode !== "plan";
  if (mode === "plan" && !plan.people.length) loadPlanPeople();
}

async function loadPlanPeople() {
  const box = $("#planPeople");
  box.textContent = "載入中…";
  try {
    // 借用部落總覽那支：它已經有名字、部落、有沒有建表
    const data = await api("/api/clan/overview?same_clan=0");
    plan.people = data.players;
  } catch (e) {
    box.textContent = "";
    box.append(el("p", "error", `讀取失敗：${e.message}`));
    return;
  }
  renderPlanPeople();
}

// 篩選之後看得到的人。勾選狀態一律只在這個範圍內談 —— 藏起來的人還留在
// 名單裡的話，畫面上的「已選 N 人」跟實際送出去的會對不起來。
function visiblePeople() {
  return $("#planSameClan").checked ? plan.people.filter((p) => p.same_clan) : plan.people;
}

$("#planSameClan").addEventListener("change", () => {
  const seen = new Set(visiblePeople().map((p) => p.tag));
  for (const t of [...plan.picked]) if (!seen.has(t)) plan.picked.delete(t);
  renderPlanPeople();
});
// 勾起來就全選，再點一下（取消勾選）就全部清掉。picked 只裝得下看得見的人，
// 所以清空不會偷偷留下被篩選藏起來的那幾個。
$("#planAll").addEventListener("change", () => {
  plan.picked.clear();
  if ($("#planAll").checked) for (const p of visiblePeople()) plan.picked.add(p.tag);
  renderPlanPeople();
});

function renderPlanPeople() {
  const box = $("#planPeople");
  box.textContent = "";
  const list = visiblePeople();
  for (const p of list) {
    const me = p.tag === state.me.active_tag;
    const row = el("label", "check person" + (p.has_data ? "" : " nodata"));
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = plan.picked.has(p.tag);
    cb.addEventListener("change", () => {
      if (cb.checked) plan.picked.add(p.tag);
      else plan.picked.delete(p.tag);
      renderPlanPeople();
    });
    const label = `${p.name}${me ? "（你）" : ""}　${p.collected}/${p.total}`;
    row.append(cb, el("span", null, label));
    if (!p.has_data) row.append(el("span", "alt-label", "未建表"));
    box.append(row);
  }
  if (!list.length) {
    box.append(el("p", "hint", "同部落沒有其他人建表，取消上面的篩選看看別的部落。"));
  }

  // 全部勾滿了「全選」才算勾起來 —— 手動取消其中一個，它要跟著彈回未勾，
  // 不然再點一下會變成「清空」而不是使用者以為的「補齊」。
  const all = $("#planAll");
  all.checked = list.length > 0 && list.every((p) => plan.picked.has(p.tag));
  all.disabled = !list.length;

  $("#planCount").textContent = `已選 ${plan.picked.size} 人`;
  // 一個人湊不成交換。與其讓伺服器回錯誤，不如讓按鈕自己說明還缺什麼
  $("#planRun").disabled = plan.picked.size < 2;
  renderPlanFavor();
}

function renderPlanFavor() {
  const sel = $("#planFavor");
  const keep = sel.value;
  sel.textContent = "";
  const none = el("option", null, "不指定（全體新增最多）");
  none.value = "";
  sel.append(none);
  for (const p of plan.people) {
    if (!plan.picked.has(p.tag)) continue;
    const o = el("option", null, p.tag === state.me.active_tag ? `${p.name}（你）` : p.name);
    o.value = p.tag;
    sel.append(o);
  }
  sel.value = [...sel.options].some((o) => o.value === keep) ? keep : "";
}

$("#planRun").addEventListener("click", async () => {
  const btn = $("#planRun");
  const err = $("#planError");
  err.hidden = true;
  btn.disabled = true;
  $("#planBody").textContent = "";
  try {
    const data = await api("/api/matches/plan", {
      method: "POST",
      body: JSON.stringify({
        tags: [...plan.picked],
        favor: $("#planFavor").value || null,
      }),
    });
    renderPlanResult(data);
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
  }
});

function renderPlanResult(data) {
  const body = $("#planBody");
  body.textContent = "";
  const nameOf = (t) => (data.players[t] || {}).name || t;

  if (!data.steps.length) {
    body.append(el("div", "empty", "這幾個人之間換不出東西"));
    return;
  }

  const head = el("div", "card");
  const s = data.summary;
  head.append(el("h2", null, `${s.trades} 筆交換，補上 ${s.total_new} 張`));
  const who = Object.entries(s.gained).sort((a, b) => b[1] - a[1]);
  head.append(el("p", "hint", who.map(([t, n]) => `${nameOf(t)} +${n}`).join("　·　")));
  // 兩件事使用者必須知道，不可以讓畫面看起來比實際更有把握
  head.append(el("p", "hint",
    "這是搜尋出來的好方案，不保證是理論上的最優解。" +
    "第二步以後要等前面那步真的換完；中途有人改了庫存就得重算。"));
  body.append(head);

  data.steps.forEach((batch, i) => {
    const card = el("div", "card");
    const h = el("div", "step-head");
    h.append(el("b", null, `第 ${i + 1} 步`));
    h.append(el("span", "alt-label", `${batch.length} 筆，可同時進行`));
    card.append(h);
    for (const g of groupTrades(batch)) card.append(renderTradeGroup(g, nameOf));
    body.append(card);
  });
}

// 按「誰對誰」分組。實際操作就是找一個人談完好幾張，逐筆列的話同一組人名會
// 重複七八次，21 筆看起來像一面牆。
function groupTrades(batch) {
  const groups = new Map();
  for (const tr of batch) {
    const key = `${tr.initiator}→${tr.receiver}`;
    if (!groups.has(key)) {
      groups.set(key, { initiator: tr.initiator, receiver: tr.receiver, trades: [] });
    }
    groups.get(key).trades.push(tr);
  }
  return [...groups.values()];
}

function renderTradeGroup(g, nameOf) {
  const box = el("div", "tgroup");

  const head = el("div", "tgroup-head");
  // 發起方只能指定自己完全沒有的卡，所以「誰開口」是規則決定的，不是隨便挑的。
  // 反過來發起會被遊戲直接擋掉，這行是操作指示不是補充說明。
  head.append(el("b", null, nameOf(g.initiator)));
  head.append(el("span", "tinit", `發起，找 ${nameOf(g.receiver)} 換 ${g.trades.length} 筆`));
  box.append(head);

  for (const tr of g.trades) {
    const row = el("div", "trade");
    // 這裡的方向是**發起方**的（上面那行剛講完誰發起），所以不寫「你」
    row.append(
      cardFace(tr.gives, "give", "送出"),
      el("span", "arrow", "⇄"),
      cardFace(tr.gets, "get", "收到"),
    );
    if (!tr.receiver_new) row.append(el("span", "alt-label", "對方收到的是重複卡"));
    box.append(row);
  }
  return box;
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
  // 欄位的 class 要跟底下的 td 一致，否則對齊與欄寬只作用在資料列，表頭會歪掉。
  for (const [label, cls] of [["玩家", "who"], ["部落", null], ["已收集", "num"], ["庫存更新", "when"]]) {
    hr.append(el("th", cls, label));
  }
  thead.append(hr);
  table.append(thead);

  const tbody = el("tbody");
  for (const p of data.players) {
    const tr = el("tr");
    tr.append(el("td", "who", p.name));
    const clan = el("td", null, p.clan_name || "無部落");
    if (!p.same_clan) clan.style.color = "var(--warn)";
    tr.append(clan);
    tr.append(el("td", "num", p.has_data ? `${p.collected}/${p.total}` : "未建表"));

    const upd = el("td", "when", p.has_data ? lastUpdated(p.collection_updated_at) : "—");
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
    // 一定要寫「握把」：拖曳只綁在 .grip 上（見底下的 pointerdown），
    // 拖卡片本體不會有任何反應。
    tip.append(el("p", "hint", "拖曳握把可調整順序"));
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
      alert(`已連結「${r.player.name}」`);
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
  read: { label: "辨識成功", cls: "ok" },
  unknown: { label: "辨識失敗", cls: "warn" },
  conflict: { label: "截圖資料不一致", cls: "bad" },
  uncovered: { label: "缺少資料", cls: "dim" },
};

// 這格最後會寫進去的值。使用者沒選就沿用資料庫現值 ——
// 回傳 null 代表「不要動它」，跟「設成 0」是兩回事。
function importFinal(row) {
  if (row.id in imp.picks) return imp.picks[row.id];
  if (row.state === "read") return row.value;
  return null;
}

function importTile(row) {
  // 格子底下只寫**這張卡特有**的事。狀態（認不出／沒拍到）由上面的小標題
  // 講一次就好 —— 每格都重複一遍的話，24 張沒拍到的就會刷出 24 行一模一樣
  // 的字，格子本身反而看不到了。原有張數也只在不是 0 的時候才值得提。
  const cur = row.current || 0;
  const note = [row.note, cur > 0 ? `原有 ${cur} 張` : null].filter(Boolean).join("・") || null;

  return cardTile(state.cardById[row.id], {
    value: importFinal(row),
    allowUnset: true,
    note,
    noteClass: IMPORT_STATE[row.state].cls,
    onChange: (n) => {
      if (n === null) delete imp.picks[row.id];
      else imp.picks[row.id] = n;
      updateImportHint();
    },
  });
}

// 只更新那一行字。整個 redrawImport 會把 60 個格子重建一次，點一下就重來一遍
// 太浪費，而且會把畫面捲回去。上方的辨識摘要是伺服器算的，跟使用者改了什麼無關。
function updateImportHint() {
  const willWrite = imp.rows.filter((r) => importFinal(r) !== null).length;
  $("#importApplyHint").textContent = `會寫入 ${willWrite} 張，其餘 ${60 - willWrite} 張維持原值`;
}

function renderImport(data) {
  imp.rows = data.cards;
  imp.picks = {};

  const list = $("#importFileList");
  list.textContent = "";
  for (const f of data.files) {
    const li = el("li", f.ok ? "ok" : "bad");
    if (f.ok) {
      const exact = f.exact ? "" : "（位置信心度較低，建議核對）";
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
  sum.append(el("h2", null, `辨識成功 ${s.read} / 60`));
  if (left > 0) {
    // 不要寫「辨識失敗」—— left 是 unknown + conflict + uncovered 的總和，
    // 而「辨識失敗」同時又是 unknown 這一個狀態的名字。同一個詞當統稱又當
    // 專名，下面那行的分項數字就會跟這裡對不起來。
    sum.append(el("p", "warn", `${left} 張沒有值 將保持原樣`));
  } else {
    sum.append(el("p", "hint", "全部卡片辨識成功"));
  }
  const brk = [];
  if (s.unknown) brk.push(`辨識失敗 ${s.unknown}`);
  if (s.conflict) brk.push(`截圖資料不一致 ${s.conflict}`);
  if (s.uncovered) brk.push(`缺少資料 ${s.uncovered}`);
  if (brk.length) sum.append(el("p", "hint", brk.join("　·　")));

  // 跟遊戲內進度條核對 —— 有格子沒讀到就報不出可比的數字，這時說清楚為什麼
  // 跟畫面上方的進度條對照。那個數字是遊戲自己算的，跟辨識完全無關，
  // 所以對得上才是真的有意義的驗證 —— 不是自己跟自己說沒問題。
  const chk = el("div", "checkrow");
  const groups = state.importSeries || [];
  const compared = groups.filter((g) => g.owned !== null && g.expected !== null);
  const bad = compared.filter((g) => g.owned !== g.expected);

  if (compared.length && !bad.length) {
    chk.append(el("p", "ok-note", `已與進度條核對 ${compared.length} 個系列相符`));
  } else if (bad.length) {
    chk.append(el("p", "error", "與進度條不符，請逐格檢查："));
  } else {
    chk.append(el("p", "hint", "各系列已擁有張數（進度條讀取失敗，無法自動核對）："));
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
      text = `${g.name} ${g.owned}/${g.total}（進度條讀取失敗，無法自動核對）`;
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
    // 依原因分組，原因寫在小標題上。最需要注意的排前面：不一致代表兩張截圖
    // 互相打架，比單純沒拍到值得先看。
    for (const st of ["conflict", "unknown", "uncovered"]) {
      const rows = needs.filter((r) => r.state === st);
      if (!rows.length) continue;
      const meta = IMPORT_STATE[st];
      box.append(el("h3", `need-head ${meta.cls}`, `${meta.label} ${rows.length} 張`));
      const grid = el("div", "grid");
      for (const r of rows) grid.append(importTile(r));
      box.append(grid);
    }
  }

  const readBox = $("#importRead");
  readBox.textContent = "";
  const readGrid = el("div", "grid");
  for (const r of imp.rows.filter((x) => x.state === "read")) readGrid.append(importTile(r));
  readBox.append(readGrid);

  updateImportHint();
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

/* 開頁時什麼都不要先畫。

   以前是「先讓 HTML 把登入表單畫出來，等 /api/me 回來再跳走」，於是每次
   開啟網頁都會閃一下登入框。而且四支 API 是一支一支等的，分頁列與收藏會
   分好幾次才長出來，看起來又閃一次。

   現在改成：畫面停在「載入中」，需要的東西全部並行拿完、整頁組好，
   最後才一次顯示。 */
async function boot() {
  const [meta, me] = await Promise.all([api("/api/cards"), api("/api/me")]);
  state.cards = meta.cards;
  state.series = meta.series;
  state.maxCount = meta.max_count;
  state.cardById = Object.fromEntries(meta.cards.map((c) => [c.id, c]));
  state.me = me;

  if (!me.logged_in) {
    $("#topbar").hidden = true;
    // 用 ?. —— boot() 不是只跑一次。登入成功、加綁村莊、切換村莊都會再呼叫
    // 一次，那時「載入中」早就被移掉了，寫成 .remove() 會丟 TypeError。
    // 而登入的呼叫包在 try/catch 裡，結果就是**登入成功卻顯示錯誤訊息、
    // 停在登入頁**。
    $("#booting")?.remove();
    show("login");
    return;
  }

  // 沒裝 opencv 的伺服器不顯示截圖分頁 —— 顯示了按下去只會拿到 501。
  // 舊版伺服器沒有這支 API，拿不到就當作沒有。
  const [cap, coll] = await Promise.all([
    api("/api/import/available").catch(() => null),
    api("/api/collection"),
  ]);

  const picker = $("#villagePicker");
  picker.textContent = "";
  for (const p of me.players) {
    const o = el("option", null, `${p.name}（${p.clan_name || "無部落"}）`);
    o.value = p.tag;
    if (p.tag === me.active_tag) o.selected = true;
    picker.append(o);
  }

  if (cap) {
    $('.tabs button[data-view="import"]').hidden = !cap.available;
    $("#importMax").textContent = cap.max_images;
  }

  state.counts = coll.counts || {};
  state.saved = { ...state.counts };
  state.dirty = false;
  renderCollection();
  markDirty();

  $("#topbar").hidden = false;
  $("#booting")?.remove();
  show("collection");
}

boot().catch((e) => {
  document.body.innerHTML = `<main><div class="card"><p class="error">啟動失敗：${e.message}</p></div></main>`;
});
