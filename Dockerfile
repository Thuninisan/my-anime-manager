FROM python:3.12-alpine

ENV PYTHONUTF8=1

WORKDIR /app

# Copy dependency file first for Docker layer caching
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy source code
COPY my_anime_manager/ ./my_anime_manager/

ENTRYPOINT ["python", "-m", "my_anime_manager"]
CMD []
