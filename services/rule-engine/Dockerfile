# Build context: services/  (so shared/ is reachable)
#   docker build -t rule-engine:tag -f services/rule-engine/Dockerfile services/
#
# Note: rule-engine no longer depends on shared/telegram_client (ADR-001).
# All Telegram delivery is handled by alert-service.

FROM python:3.12-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir hatchling

# 1) Install service runtime deps (parsed from pyproject.toml).
COPY rule-engine/pyproject.toml rule-engine/pyproject.toml
RUN python3 -c "import tomllib; d=tomllib.load(open('rule-engine/pyproject.toml','rb')); open('/deps.txt','w').write('\n'.join(d['project']['dependencies']))" && \
    pip install --no-cache-dir -r /deps.txt

# 2) Install the service itself (no-deps because step 1 handled them).
COPY rule-engine/src/ rule-engine/src/
RUN pip install --no-cache-dir --no-deps ./rule-engine

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY rule-engine/src/ src/

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "rule_engine.main:app", "--host", "0.0.0.0", "--port", "8080"]
