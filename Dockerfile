FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends chrony ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY revolut_x_client.py precision_engine.py grid_executor.py bot_main.py ./
ENV LIVE_TRADING=false PYTHONUNBUFFERED=1
CMD ["python", "bot_main.py"]

