FROM python:3.12-slim

WORKDIR /app

# 階段一還用不到 OpenCV，先不裝 —— 映像檔小 40 MB，階段二再加回來
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 資料庫掛 volume，不要留在映像檔裡
RUN mkdir -p /app/data

EXPOSE 3848
CMD ["python", "main.py"]
