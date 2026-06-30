# Build context: services/
#   docker build -t llm-agent:tag -f services/llm-agent/Dockerfile services/
#
# LLM provider extras (OpenAI + Gemini) are installed explicitly here so the
# same image supports switching LLM_MODEL without a rebuild.

FROM python:3.12-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir hatchling

# 1) Install service runtime deps (parsed from pyproject.toml).
COPY llm-agent/pyproject.toml llm-agent/pyproject.toml
RUN python3 -c "import tomllib; d=tomllib.load(open('llm-agent/pyproject.toml','rb')); open('/deps.txt','w').write('\n'.join(d['project']['dependencies']))" && \
    pip install --no-cache-dir -r /deps.txt && \
    pip install --no-cache-dir "langchain-openai>=0.2" "langchain-google-genai>=2.0"

# 2) Install the service itself (no-deps because step 1 handled them).
COPY llm-agent/src/ llm-agent/src/
RUN pip install --no-cache-dir --no-deps ./llm-agent

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY llm-agent/src/ src/

ENV PYTHONUNBUFFERED=1

EXPOSE 8081

CMD ["uvicorn", "llm_agent.main:app", "--host", "0.0.0.0", "--port", "8081"]
