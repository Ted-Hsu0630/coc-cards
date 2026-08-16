"""多人換卡計劃。

雙人配對（`matching.py`）回答的是「我跟這個人能換什麼」。這裡回答的是
「這一群人**照什麼順序**換，整體收穫最大」—— 兩者的規則完全相同
（SPEC §2.2，見 matching.py 開頭那段），差別只在要不要考慮交換之間的先後。

**為什麼需要「步驟」。** 換卡會改變狀態，而且存在真正的鏈：
阿明手上有 1 張女巫，送不出去（門檻是 2 張）。他先從小華那裡收到一張
（當接收方沒有限制），變成 2 張，這時才送得給老王。所以第二步確實要等
第一步完成。

**同一步的界線**：一步之內只能動用「這一步開始時就在手上」的卡，這一步
收到的卡要到下一步才算數。這樣同一步裡的每筆交換彼此不影響順序，可以
同時進行 —— 這正是把它們寫在同一步的意義。

**這不是數學上的最優解。** 這是組合最佳化問題。這裡用逐輪貪婪，但兩個地方
是實測調出來的（30 個亂數種子 x 5 種人數）：

1. **同分時先消耗「很多人都送得出」的卡。** 稀缺的多餘卡只有一次機會放對
   位置，先花掉充裕的，稀缺的才有機會等到真正需要它的人。17 人時 30 個
   種子全勝，平均多換 2%。
   反過來的直覺（先滿足最稀缺的需求）實測會**變差** —— 試過，不要再改回去。
2. **打亂同分候選重跑幾次取最好的。** 再多約 1%，17 人約 100ms。

合起來比單次貪婪多 1~2.6%，100 個實測案例沒有一次變差。

═══ 已經量過、不必再重做的三件事 ═══

**一、多算幾步沒有用。** 第 2 步就飽和：

    人數    1步    2步    3步    4步    6步   10步
      12    166    171    171    171    171    171
      17    237    238    238    238    238    238
      20    302    304    304    304    304    304

鏈很稀有 —— 要形成鏈，某人得手上正好 1 張、在前一步當接收方收到第二張、
而且還得有人缺那張又拿得出東西換。條件一多就幾乎不會有第三層。
所以 MAX_STEPS_DEFAULT 維持 3（2 就夠，留一點餘裕）。

順帶回答一個一定會有人問的問題：**畫面上只有第一步不是壞掉。** 拿正式機的
資料重跑 500 次，12 人那組有 440 次跑出兩步、32 次三步，但**分數全部都是
107**，一張都沒多換。多步方案找得到，只是不值得推薦 —— 所以同分時挑步數
少的（見 plan() 裡那個 key）。5 人那組更乾脆，500 次裡 492 次就是一步。
真正的瓶頸不是步數：第一步跑完之後，剩下的空格是**整團沒有人有 2 張**的卡。

**二、整數規劃（ILP）跑得動，但不值得。** 用 pulp + CBC 把單步建成
0/1 規劃（送出張數上限、補到空缺才計分），實測**每一步都能證明最優**：

    人數    變數     本模組     ILP     差距      本模組    ILP
      12   1,959     165.6   166.0   +0.24%     47 ms    70 ms
      17   4,017     252.4   255.2   +1.11%    102 ms   154 ms
      30  12,665     471.8   475.4   +0.76%    331 ms   525 ms

只慢 1.5 倍，這點跟直覺相反（我原本以為會慢到不能放進請求裡）。
但收穫是 **17 人多換不到 3 張**，而代價是：pulp 裝起來 36 MB（內含各平台的
CBC 執行檔，跟 opencv 同量級）、多三種失敗模式（求解器逾時、slim 映像檔裡
跑不起來、CBC 換版本後同分解可能改變 —— 最後這項會直接弄壞
`test_同樣的資料每次算出同樣的計劃`）。

而且它也只是「每一步之內最優」，跨步驟的全域最優要時間索引模型，
變數量乘上步數，那才是真的貴。

**什麼時候該回頭看 ILP**：部落長到 50 人以上，或是要加「讓每個人至少湊滿
一個系列」這類限制 —— 那種條件寫成不等式很自然，寫成貪婪規則會變成一堆特例。

**三、寬鬆上界沒有參考價值。** 算過一個忽略配對限制的上界，本模組只達到
它的 84~89%，看起來還有很多空間 —— 但 ILP 證明真正的最優就在旁邊（差 1%）。
那個差距全是上界自己太鬆，不要拿它當改進的目標。
"""

import random
from dataclasses import dataclass

from core import cards

# 「優先某人」時，讓他補到空缺的交換一律排在其他交換前面。
# 一筆交換最多讓兩個人各補一格，所以總分上限是 2 —— 權重取 10 就足以壓過。
FAVOR_WEIGHT = 10

# 實測第 2 步就飽和（2 步到 10 步一張都沒多），3 步只是留一點餘裕。
MAX_STEPS_DEFAULT = 3

# 打亂同分候選重跑幾次。20 次已經吃掉幾乎全部的收益（試到 50 次只再多 0.1%），
# 20 人約 150ms —— 這支端點是使用者按按鈕才算的，這個延遲划算。
RESTARTS = 20


@dataclass
class Trade:
    """一筆交換。方向是實際的遊戲動作，不可以省略。"""

    initiator: str      # 誰要在部落聊天室開口
    receiver: str
    gives: str          # 發起方送出的卡
    gets: str           # 發起方收到的卡（一定是他完全沒有的）
    series: str
    initiator_new: bool  # 恆為 True：發起方只能指定自己沒有的卡
    receiver_new: bool   # 接收方可能只是收下一張重複卡

    def as_dict(self) -> dict:
        return {
            "initiator": self.initiator,
            "receiver": self.receiver,
            "gives": self.gives,
            "gets": self.gets,
            "series": self.series,
            "initiator_new": self.initiator_new,
            "receiver_new": self.receiver_new,
        }


def _ids_by_series() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in cards.all_cards():
        out.setdefault(c.series, []).append(c.id)
    return out


def plan(
    collections: dict[str, dict[str, int]],
    tags: list[str],
    *,
    max_steps: int = MAX_STEPS_DEFAULT,
    favor: str | None = None,
    restarts: int = RESTARTS,
) -> list[list[dict]]:
    """回傳分好步驟的交換計劃。空清單代表這群人之間換不出東西。

    `collections` 只需含 `tags` 裡這些人的資料；缺席的人視為空收藏。
    `favor` 指定要優先照顧的人，None 代表以全體新增張數最大為目標。

    亂數種子固定成 0..restarts-1，所以**同樣的輸入永遠得到同樣的計劃** ——
    每次重新整理都換一份建議的話，使用者會以為資料變了。
    """
    ids_by_series = _ids_by_series()
    series_of = {cid: s for s, ids in ids_by_series.items() for cid in ids}
    valid = set(series_of)
    base = {
        t: {c: n for c, n in (collections.get(t) or {}).items() if c in valid and n > 0}
        for t in tags
    }

    best: list[list[dict]] | None = None
    best_key = None
    for seed in range(max(1, restarts)):
        steps = _plan_once(base, tags, ids_by_series, series_of, favor, seed, max_steps)
        # 補到的張數最多為主；同樣多的話**先挑步數少的**，再挑筆數少的。
        # 步數擺在筆數前面是有代價考量的：多一步等於全團要等第一步全部做完
        # 才能開始第二步，而多一筆只是多發一則訊息。實測過一組正式資料，
        # 62 筆兩步 vs 63 筆一步、換到的卡一模一樣 —— 為了省一則訊息
        # 讓十二個人多等一輪，划不來。
        gained = sum(
            1 + (1 if tr["receiver_new"] else 0) for batch in steps for tr in batch
        )
        key = (gained, -len(steps), -sum(len(b) for b in steps))
        if best_key is None or key > best_key:
            best, best_key = steps, key
    return best or []


def _plan_once(base, tags, ids_by_series, series_of, favor, seed, max_steps):
    # 複製一份 —— 這裡會就地修改，不可以動到呼叫端的資料，而且每次重跑都要
    # 從原始狀態開始
    have = {t: dict(counts) for t, counts in base.items()}
    steps: list[list[dict]] = []
    for _ in range(max_steps):
        batch, avail, incoming = _one_step(have, tags, ids_by_series, series_of, favor, seed)
        if not batch:
            break
        # **送出去的要扣掉**。只併入收到的、不扣掉送出的話，下一步會讓同一個人
        # 再送出一張他已經送掉的卡 —— 而且畫面上完全看不出來。
        have = avail
        # 這一步收到的卡到現在才併進手牌 —— 併早了就等於允許「用這一步剛拿到的
        # 卡在同一步再送出去」，那兩筆就有先後關係，不能算同一步。
        for t, gained in incoming.items():
            for cid, n in gained.items():
                have[t][cid] = have[t].get(cid, 0) + n
        steps.append([tr.as_dict() for tr in batch])
    return steps


def _supply(have):
    """每張卡有幾個人送得出來。稀缺的多餘卡只有一次機會放對位置。"""
    out: dict[str, int] = {}
    for counts in have.values():
        for cid, n in counts.items():
            if n >= 2:
                out[cid] = out.get(cid, 0) + 1
    return out


def _one_step(have, tags, ids_by_series, series_of, favor, seed=0):
    """算出一整步。回傳 (這步的交換, 扣掉送出後的手牌, 每個人這步收到的卡)。"""
    # avail 是「這一步還能送出的張數」，隨著挑選遞減，所以同一份卡不會被排兩次
    avail = {t: dict(counts) for t, counts in have.items()}
    incoming: dict[str, dict[str, int]] = {t: {} for t in tags}

    candidates = _candidates(have, tags, ids_by_series, favor)
    sup = _supply(have)
    # 先打亂再穩定排序 —— 隨機性只作用在「所有排序鍵都相同」的候選之間。
    random.Random(seed).shuffle(candidates)
    candidates.sort(
        key=lambda c: (
            -c[0],
            # 先消耗很多人都送得出的卡。反過來（先滿足最稀缺的需求）實測更差。
            -sup.get(c[1].gives, 0),
            -sup.get(c[1].gets, 0),
        )
    )

    batch: list[Trade] = []
    for _score, tr in candidates:
        if not _still_valid(tr, avail, incoming, have, series_of):
            continue
        avail[tr.initiator][tr.gives] -= 1
        avail[tr.receiver][tr.gets] -= 1
        incoming[tr.receiver][tr.gives] = incoming[tr.receiver].get(tr.gives, 0) + 1
        incoming[tr.initiator][tr.gets] = incoming[tr.initiator].get(tr.gets, 0) + 1
        batch.append(tr)
    return batch, avail, incoming


def _candidates(have, tags, ids_by_series, favor):
    """列出這一步開始時所有合法的交換，附上分數。

    合法性看的是**這一步開始時**的狀態，不是挑選過程中的狀態 —— 挑選時的
    遞減由 `_still_valid` 再確認一次。分開的理由是：候選只算一次就好，
    每挑一筆就全部重算的話，17 個人要跑好幾秒。
    """
    out: list[tuple[int, Trade]] = []
    for i, a in enumerate(tags):
        for b in tags[i + 1 :]:
            ha, hb = have.get(a, {}), have.get(b, {})
            for series, ids in ids_by_series.items():
                spare_a = [c for c in ids if ha.get(c, 0) >= 2]
                spare_b = [c for c in ids if hb.get(c, 0) >= 2]
                if not spare_a or not spare_b:
                    continue
                for x in spare_a:          # a 送出的
                    for y in spare_b:      # b 送出的
                        if x == y:
                            continue
                        a_new = ha.get(y, 0) == 0
                        b_new = hb.get(x, 0) == 0
                        # 發起方必須完全沒有他要收的那張（紅線 2）。
                        # 所以「誰能發起」是被規則決定的，不是我們挑的。
                        if a_new:
                            out.append(_score_trade(a, b, x, y, series, b_new, favor))
                        if b_new:
                            out.append(_score_trade(b, a, y, x, series, a_new, favor))
    return out


def _score_trade(initiator, receiver, gives, gets, series, receiver_new, favor):
    tr = Trade(
        initiator=initiator, receiver=receiver, gives=gives, gets=gets,
        series=series, initiator_new=True, receiver_new=receiver_new,
    )
    score = 1 + (1 if receiver_new else 0)
    # 只有「補到空缺」才算照顧到他 —— 收下一張重複卡不算。
    # 發起方一定是補到空缺的那一方（他只能指定自己沒有的卡）。
    if favor is not None and (initiator == favor or (receiver == favor and receiver_new)):
        score += FAVOR_WEIGHT
    return score, tr


def _still_valid(tr, avail, incoming, have, series_of):
    """挑選過程中再確認一次。候選是照這一步開始的狀態算的，中途會失效。"""
    # 送出的那一份還在嗎（送完至少要留一張，所以門檻仍是 2）
    if avail[tr.initiator].get(tr.gives, 0) < 2 or avail[tr.receiver].get(tr.gets, 0) < 2:
        return False
    # 同一步裡同一個人不要重複收到同一張卡 —— 第二張是浪費，
    # 而且對方本來可以拿去補別人的空缺
    if tr.gets in incoming[tr.initiator] or tr.gives in incoming[tr.receiver]:
        return False
    # 發起方在這一步已經被安排收下這張卡的話，「他完全沒有」就不再成立
    if have[tr.initiator].get(tr.gets, 0) != 0:
        return False
    return series_of[tr.gives] == series_of[tr.gets]


def summarize(steps: list[list[dict]]) -> dict:
    """給畫面用的統計。誰補了幾張、總共幾筆。"""
    gained: dict[str, int] = {}
    trades = 0
    for batch in steps:
        for tr in batch:
            trades += 1
            gained[tr["initiator"]] = gained.get(tr["initiator"], 0) + 1
            if tr["receiver_new"]:
                gained[tr["receiver"]] = gained.get(tr["receiver"], 0) + 1
    return {"steps": len(steps), "trades": trades, "gained": gained, "total_new": sum(gained.values())}
