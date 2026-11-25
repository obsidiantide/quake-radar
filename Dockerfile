FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY quake ./quake
COPY web ./web
COPY tests ./tests
ENV QUAKE_DATA_DIR=/data PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "quake"]
