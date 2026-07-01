# Stage 1: Build React frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend + serve frontend
FROM python:3.12-alpine
ENV PYTHONIOENCODING=utf-8
WORKDIR /app

# Copy Python source and install
COPY pyproject.toml ./
COPY my_anime_manager/ ./my_anime_manager/
RUN pip install --no-cache-dir .

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist/

ENTRYPOINT ["python", "-m", "my_anime_manager"]
CMD ["--serve", "0.0.0.0:8000"]
