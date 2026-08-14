FROM python:3.12-slim

WORKDIR /app

# 分兩層裝：requirements.txt 幾乎不動，opencv 那層才是大的，
# 分開才能吃到 Docker 的層快取
COPY requirements.txt requirements-cv.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir -r requirements-cv.txt

COPY . .

# data/ 是 named volume，只放會變動的狀態（資料庫）。
# 靜態資料放 assets/ —— named volume 只在第一次建立時從映像檔複製內容，
# 之後就完全遮蔽映像檔，放在 data/ 底下的更新永遠不會生效。
RUN mkdir -p /app/data

EXPOSE 3848
CMD ["python", "main.py"]
