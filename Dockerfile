# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + serve frontend
FROM python:3.12-alpine
ENV PYTHONUTF8=1
WORKDIR /app

# Copy dependency file first for Docker layer caching
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy Python source code
COPY my_anime_manager/ ./my_anime_manager/

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist/

ENTRYPOINT ["python", "-m", "my_anime_manager"]
CMD ["--serve", "0.0.0.0:8000"]
