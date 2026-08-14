"""村莊／部落標籤的正規化。

SPEC §8：`#` 沒 URL-encode 打 CoC API 會回 **404 而不是 400**，很容易被誤判成
「查無此玩家」。正規化只准在這裡做一次，其他地方一律用這裡的輸出。
"""

import re
import urllib.parse

# Supercell 的 tag 不使用容易混淆的字元（沒有 O、I、S、B、Z、字母 D 之外的…），
# 但官方沒有公開完整字母表，這裡只做寬鬆的格式檢查，真偽交給 API 判定。
_VALID = re.compile(r"^#[0289PYLQGRJCUV]{3,12}$")
_CLEAN = re.compile(r"[^0-9A-Z]")


def normalize(tag: str) -> str:
    """`9qrul2cvj` / `#9QRUL2CVJ` / ` 9QRUL2cvj ` → `#9QRUL2CVJ`

    使用者常把 O 打成 0、把小寫貼進來，這裡一併吸收掉。
    """
    if not tag:
        raise ValueError("標籤不可為空")
    t = _CLEAN.sub("", tag.strip().upper().replace("O", "0"))
    if not t:
        raise ValueError("標籤格式不正確")
    return "#" + t


def is_plausible(tag: str) -> bool:
    return bool(_VALID.match(tag))


def encode(tag: str) -> str:
    """給 CoC API 用的路徑片段。`#` → `%23`。"""
    return urllib.parse.quote(normalize(tag), safe="")
