FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

# Package index used during the build. Defaults to the official PyPI; set PIP_INDEX_URL in .env to use a mirror
# if PyPI is slow on your network.
ARG PIP_INDEX_URL=https://pypi.org/simple

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries 8 --index-url ${PIP_INDEX_URL} -r requirements.txt

COPY app app
COPY scripts scripts
COPY mock_platforms mock_platforms

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
