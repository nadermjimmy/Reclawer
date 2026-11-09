# Railway builds this from the `receiver` directory.
# Stage 1: build the new admin app (receiver/frontend -> /web/dist, served by FastAPI at /app).
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: the receiver (webhook + classic pages + /api + /app).
FROM python:3.12-slim
WORKDIR /app
# LightGBM's OpenMP runtime (not in the slim image)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN rm -rf frontend/node_modules
COPY --from=web /web/dist ./frontend/dist
# "" binds all addresses (IPv4 + IPv6) - "::" alone is IPv6-only under uvicorn
CMD ["uvicorn", "app:app", "--host", "", "--port", "8000"]
