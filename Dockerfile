# Render 네이티브(비-Docker) 파이썬 환경은 apt 시스템 패키지를 설치할 수 없어서
# LibreOffice(soffice)를 넣을 수 없다 — xlsx→PDF 변환에 LibreOffice가 반드시 필요하므로
# Docker로 전환해서 apt로 직접 설치한다. (report_builder.py의 _find_soffice() 참고)
FROM python:3.11-slim-bookworm

# libreoffice-calc: xlsx→PDF 변환에 필요한 최소 구성(soffice 바이너리 포함, 전체 libreoffice보다 훨씬 가벼움)
# fonts-nanum: 한글 폰트 — 없으면 PDF에서 한글이 네모(□)로 깨져서 나온다
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-calc \
        fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=10000
EXPOSE 10000

CMD gunicorn -w 2 -t 180 -b 0.0.0.0:$PORT app:app
