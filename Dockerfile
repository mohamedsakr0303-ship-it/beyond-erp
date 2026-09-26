# Self-hosting image. Put it behind an HTTPS reverse proxy (see docker-compose.yml).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 BEYOND_ERP_DATA=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 10001 erp && mkdir /data && chown erp /data
COPY run.py .
COPY erp ./erp
USER erp
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/api/bootstrap')" || exit 1
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
