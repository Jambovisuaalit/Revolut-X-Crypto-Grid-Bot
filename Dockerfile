FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates chrony \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Ed25519 request timestamps require a correctly synchronized host clock.
# chrony is installed for deployments that grant the required clock capability;
# production should also enforce NTP on the container host.
CMD ["python", "bot_main.py"]
