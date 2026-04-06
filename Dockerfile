FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
# requirements.txt의 paramiko는 Windows 조건부이므로 Linux 컨테이너에서 명시 설치
RUN pip install --no-cache-dir -r requirements.txt paramiko

COPY . .

EXPOSE 8080

# 기본: 웹 대시보드 서버
CMD ["python", "web.py", "--host", "0.0.0.0", "--port", "8080"]
