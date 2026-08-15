"""把多張截圖的辨識結果合併成一份「待確認的收藏」。

原則：**辨識結果永遠不直接寫進資料庫**，一律先給人看過。
所以這裡的任務不是「盡量填滿」，而是**誠實標出每一格的來源與把握**。

每張卡的狀態：

    read       讀出來了
    unknown    有截圖涵蓋到，但讀不出來（徽章數字認不得、被畫面切掉…）
    conflict   兩張截圖給了不一樣的值
    uncovered  沒有任何截圖涵蓋到

後三種一律要人工填。UI 不可以把它們預設成 0 或 1 —— 那是在猜。

OpenCV 是**選用相依**：沒安裝時 available() 回 False，整個網站其餘功能照常。
這樣正式環境不裝 opencv 也能跑，不必動 requirements.txt。
"""

import logging

from core import cards as cards_mod

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 12 * 1024 * 1024
# 相簿只有 60 張卡、一次拍得到 12 格，5 張截圖就涵蓋得完，8 張是給重拍與
# 部分重疊的餘裕。**這個數字直接決定記憶體天花板**：analyze() 會同時持有
# 整批解碼後的點陣圖，而單張的上限是 services/__init__.py 設的 24 Mpx，
# 所以最壞情況是 8 x 72MB = 576MB。要調大先去看那份說明。
MAX_IMAGES = 8


def available() -> bool:
    try:
        import cv2  # noqa: F401

        from services import recognize  # noqa: F401
    except Exception:
        return False
    return True


class ImageTooLarge(ValueError):
    """解碼後的尺寸超過 `OPENCV_IO_MAX_IMAGE_PIXELS`（見 services/__init__.py）。

    跟「這不是圖片檔」要分開回報：使用者看到「尺寸過大」才知道該去縮圖，
    看到「不是圖片檔」只會一直重傳同一個檔案。
    """


def _load(data: bytes):
    import cv2
    import numpy as np

    buf = np.frombuffer(data, np.uint8)
    try:
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except cv2.error as e:
        # 超過像素上限時 OpenCV 是**丟例外**，不是回 None（實測 OpenCV 5.0：
        # `(-215:Assertion failed) pixels <= CV_IO_MAX_IMAGE_PIXELS`）。
        # 少了這個 except，一張壓縮炸彈就是一個 500 而不是一句友善的拒絕。
        raise ImageTooLarge(str(e)) from e


def analyze(files: list[tuple[str, bytes]], existing: dict[str, int] | None = None) -> dict:
    """辨識多張截圖。files 是 (檔名, 位元組) 的清單。

    existing 是這個村莊目前資料庫裡的收藏，用來在「讀不出來」時顯示現值 ——
    使用者才知道不填的話會維持什麼，而不是被歸零。
    """
    from services import progress as P
    from services import recognize as R

    existing = existing or {}
    digits, art = R.load_digits(), R.load_art()
    bar_tmpl = P.load_templates()

    # report 與 results 不可以用 zip 對齊 —— 太大／讀不出來的檔案會進 report
    # 但不會進 results，後面每一筆就全部錯位、把 A 圖的結果掛到 B 圖上。
    # 用 entry 直接持有自己的 result。
    images, results, report = [], [], []
    for name, data in files:
        entry = {"name": name, "ok": False, "reason": ""}
        report.append(entry)
        if len(data) > MAX_IMAGE_BYTES:
            # 不印實際大小：路由層是用 read(MAX+1) 截斷讀進來的，這裡的
            # len(data) 只會是 MAX+1，印出來會嚴重低報真正上傳的量。
            entry["reason"] = f"檔案太大（超過 {MAX_IMAGE_BYTES // 1024 // 1024} MB）"
            continue
        try:
            img = _load(data)
        except ImageTooLarge:
            entry["reason"] = "圖片尺寸過大，請用原始截圖不要放大"
            continue
        if img is None:
            entry["reason"] = "這不是能讀取的圖片檔"
            continue
        r = R.recognize(img, digits=digits, art=art)
        entry["ok"], entry["reason"] = r.ok, r.reason
        entry["_result"] = r
        images.append(img)
        results.append(r)
        if r.ok:
            # 畫面上方的系列進度條是遊戲自己算的，跟我們的辨識完全無關 ——
            # 拿來當獨立檢查碼
            entry["_bars"] = P.read_progress(img, bar_tmpl)

    R.resolve_batch(results, images=images, art=art)

    ids = R.album_ids()
    # card_id -> [(值或 None, 檔名, 說明)]
    seen: dict[str, list] = {}
    bars: dict[str, list] = {}          # series -> [(值, 檔名)]
    for entry in report:
        r = entry.pop("_result", None)
        for key, pair in entry.pop("_bars", {}).items():
            bars.setdefault(key, []).append((tuple(pair), entry["name"]))
        if r is None or not r.ok:
            continue
        entry["cells"] = len(r.cells)
        entry["exact"] = r.exact
        entry["range"] = [r.start + 1, r.start + len(r.cells)]
        for i, c in enumerate(r.cells):
            if r.start + i >= len(ids):
                continue
            seen.setdefault(ids[r.start + i], []).append((c.count, entry["name"], c.note))

    out = []
    for card in cards_mod.all_cards():
        cid = card.id
        obs = seen.get(cid, [])
        cur = existing.get(cid)
        row = {
            "id": cid,
            "name": card.name_zh,
            "series": card.series,
            "current": cur,
            "value": None,
            "state": "uncovered",
            "note": "",
            "sources": [n for _, n, _ in obs],
        }
        if obs:
            values = {v for v, _, _ in obs if v is not None}
            if len(values) > 1:
                row["state"] = "conflict"
                row["note"] = "不同截圖讀到不一樣的張數：" + "、".join(
                    f"{n}={v}" for v, n, _ in obs if v is not None
                )
            elif values:
                row["state"] = "read"
                row["value"] = values.pop()
            else:
                row["state"] = "unknown"
                row["note"] = next((note for _, _, note in obs if note), "讀不出來")
        out.append(row)

    need = [r for r in out if r["state"] != "read"]
    return {
        "files": report,
        "cards": out,
        "summary": {
            "total": len(out),
            "read": len(out) - len(need),
            "need_input": len(need),
            "by_state": {s: sum(1 for r in out if r["state"] == s)
                         for s in ("read", "unknown", "conflict", "uncovered")},
            "series_owned": _series_owned(out, bars),
        },
    }


def _series_owned(rows, bars: dict[str, list] | None = None) -> list[dict]:
    """每個系列「已擁有幾張」，以及跟畫面上進度條的比對結果。

    只有該系列全部讀出來時才給 owned —— 有格子沒讀到就報不出可比對的總數，
    硬報一個偏低的數字反而會讓人以為辨識錯了。

    expected 來自畫面上方的進度條，那是**遊戲自己算的**，跟我們的辨識完全
    無關，所以兩者一致才是真的有意義的驗證。不同截圖的進度條互相矛盾時
    （通常是把別人的截圖混進來了）一律不採用，寧可沒有檢查碼也不要錯的。
    """
    bars = bars or {}
    out = []
    for key, s in cards_mod.series_meta().items():
        seg = [r for r in rows if r["series"] == key]
        unread = [r for r in seg if r["state"] != "read"]

        expected, bar_note = None, ""
        seen_vals = {v for v, _ in bars.get(key, [])}
        if len(seen_vals) == 1:
            expected = seen_vals.pop()[0]
        elif len(seen_vals) > 1:
            bar_note = "不同截圖的進度條數字不一致，可能混到別人的截圖了"
        out.append({
            "key": key,
            "name": s["name_zh"],
            "total": len(seg),
            "owned": sum(1 for r in seg if (r["value"] or 0) > 0) if not unread else None,
            "expected": expected,
            "bar_note": bar_note,
            "missing": len(unread),
        })
    return out
