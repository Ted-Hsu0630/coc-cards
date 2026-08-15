"""**這個檔案必須留著，而且這幾行不可以搬到別的模組。**

`OPENCV_IO_MAX_IMAGE_PIXELS` 只在 `import cv2` **之前**設定才有效 —— OpenCV
在載入時就把它讀進靜態設定，之後改環境變數不會有任何作用（也不會有錯誤，
只是安靜地維持預設值）。

會 import cv2 的有 `recognize.py` 與 `progress.py` 兩個模組，而且都是在模組
層級。`importer.analyze()` 裡是 **progress 先於 recognize**，所以寫在
`recognize.py` 開頭已經來不及；寫在 `config.py` 也不行 —— `tools/` 底下的
指令稿與 `tests/` 都直接 import `services.recognize`，根本不會經過 config。

Python 保證父套件的 `__init__.py` 先於任何子模組初始化，所以這裡是唯一在
**所有**進入路徑（web app、tools、pytest）都保證跑在 cv2 之前的位置。

## 為什麼需要這道上限

`/api/import/screenshots` 收使用者上傳的圖片。PNG 的壓縮比對單色區域極高
（實測 5000x5000 純黑 = 79KB，約 1:925），所以「檔案大小」完全擋不住
「解碼後有多大」—— 12MB 的上傳額度換得到約 11GB 的點陣圖。

OpenCV 自己的預設是 2³⁰ 像素，等於單張仍可合法吃掉 3.2GB。這台機器只有
7GB 而且要跟 camera-viewer 的 24 小時錄影共用，那個預設等於沒有防護。

24 Mpx 的由來（最大的真實截圖是 6K 螢幕約 20 Mpx，實測的 iPad 截圖是
3.2 Mpx，所以這個值對真實使用者有充裕餘裕）：

    24 Mpx x 3 bytes = 72 MB / 張
    72 MB x MAX_IMAGES(8)   = 576 MB   ← analyze() 會同時持有整批

**超過上限時 OpenCV 是丟 `cv2.error`，不是回 `None`。** 呼叫端只寫
`if img is None` 是接不到的，會變成 500。見 `importer.analyze()`。
"""

import os

# 24 Mpx。改這個數字前先重算上面那道乘法 —— 它會乘以 MAX_IMAGES。
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(24_000_000))
