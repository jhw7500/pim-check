FROM python:3.11-slim

LABEL maintainer="pim-check" \
      description="iMX8MP QA Automation Tool" \
      version="2.0.0"

WORKDIR /app

# 의존성 레이어 (캐시 최적화)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt paramiko

# 소스 복사
COPY . .

# 리포트 디렉토리 생성
RUN mkdir -p /app/reports/logs

# 헬스체크 (대시보드 모드)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status')" || exit 1

EXPOSE 8080

# 기본: 웹 대시보드 서버
CMD ["python", "web.py", "--host", "0.0.0.0", "--port", "8080"]
