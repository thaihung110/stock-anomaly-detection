# Build context: services/  (so shared/ is reachable)
#   docker build -t alert-service:tag -f services/alert-service/Dockerfile services/

FROM python:3.12-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir hatchling

# 1) Install shared telegram-client first so it satisfies the dep declared below.
COPY shared/telegram_client/ shared/telegram_client/
RUN pip install --no-cache-dir ./shared/telegram_client

# 2) Install service runtime deps (parsed from pyproject; telegram-client is already
#    satisfied by step 1 and pip will skip it).
COPY alert-service/pyproject.toml alert-service/pyproject.toml
RUN python3 -c "import tomllib; d=tomllib.load(open('alert-service/pyproject.toml','rb')); open('/deps.txt','w').write('\n'.join(d['project']['dependencies']))" && \
    pip install --no-cache-dir -r /deps.txt

# 3) Install the service itself (no-deps because step 2 handled them).
COPY alert-service/src/ alert-service/src/
RUN pip install --no-cache-dir --no-deps ./alert-service

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY alert-service/src/ src/

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "alert_service.main:app", "--host", "0.0.0.0", "--port", "8080"]
