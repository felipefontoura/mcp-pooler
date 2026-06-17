FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir "mcp>=1.13" "uvicorn>=0.30" "starlette>=0.37"

COPY pooler.py .

EXPOSE 9100

HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9100/health', timeout=3).status == 200 else 1)"

CMD ["python", "pooler.py"]
