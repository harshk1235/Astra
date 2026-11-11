FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first: this layer is cached until requirements.txt
# changes, so code-only changes rebuild in seconds.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Don't run as root inside the container.
RUN useradd --create-home --uid 10001 astra
USER astra

CMD ["python", "main.py"]
