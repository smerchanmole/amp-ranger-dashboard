FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
COPY topo_ranger.PNG /app/topo_ranger.PNG
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8000
WORKDIR /app
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl krb5-user \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY --from=frontend-build /app/frontend/dist frontend/dist/
RUN mkdir -p data
EXPOSE 8000
CMD ["sh", "-c", "test -f certs/localhost.crt || python -m scripts.generate_self_signed_cert; python start.py"]
