FROM python:3.12-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir hatchling

COPY pyproject.toml .
RUN python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); open('/deps.txt','w').write('\n'.join(d['project']['dependencies']))" && \
    pip install --no-cache-dir -r /deps.txt

COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src/ src/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["python", "-m", "finnhub_trades_producer.main"]
