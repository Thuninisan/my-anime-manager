FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-alpine
LABEL org.opencontainers.image.version="1.0.0"
ENV PYTHONIOENCODING=utf-8 PYTHONUTF8=1 MAM_DATA_DIR=/app/data TZ=Asia/Shanghai
WORKDIR /app
RUN apk add --no-cache tzdata
COPY pyproject.toml ./
COPY backend/ ./backend/
RUN pip install --no-cache-dir .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "8000"]
