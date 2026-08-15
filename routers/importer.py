"""截圖匯入。

**這裡只做辨識，不寫資料庫。** 辨識結果一律回傳給前端讓使用者核對，
真正的寫入還是走既有的 `PUT /api/collection`。

理由：辨識再準也會有讀不出來、沒涵蓋到、多張截圖互相矛盾的格子。
直接寫進去的話，使用者要等到配對結果變怪才會發現，那時已經分不清
是誰的資料錯了。多按一次「確認」的成本遠低於一份錯的收藏。
"""

import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from routers.deps import get_conn, require_active_tag
from services import importer, players

router = APIRouter(prefix="/api", tags=["import"])


@router.get("/import/available")
def is_available():
    """前端用這個決定要不要顯示「截圖匯入」分頁。"""
    return {"available": importer.available()}


@router.post("/import/screenshots")
async def import_screenshots(
    files: list[UploadFile] = File(...),
    conn: sqlite3.Connection = Depends(get_conn),
    tag: str = Depends(require_active_tag),
):
    if not importer.available():
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "這台伺服器沒有安裝辨識所需的套件")
    if len(files) > importer.MAX_IMAGES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"一次最多 {importer.MAX_IMAGES} 張（相簿只有 60 張卡，5 張截圖就拍得完）",
        )

    payload = []
    for f in files:
        # **不要改回無參數的 read()。** 那會把整個檔案讀進記憶體才發現太大，
        # 而這是唯一收使用者上傳的端點。多讀 1 byte 就足以判斷有沒有超過，
        # 真正的拒絕交給 importer.analyze()（它要逐檔回報原因）。
        # 跟 All-in-One Downloader 的 .torrent 端點是同一招。
        payload.append((f.filename or "未命名", await f.read(importer.MAX_IMAGE_BYTES + 1)))
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "沒有收到檔案")

    existing = players.get_collection(conn, tag)
    result = importer.analyze(payload, existing=existing)
    result["tag"] = tag
    return result
