FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt paramiko

COPY . .

EXPOSE 8080

# 기본: 웹 대시보드 서버
CMD ["python", "web.py", "--host", "0.0.0.0", "--port", "8080"]
