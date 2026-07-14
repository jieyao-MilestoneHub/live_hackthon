# build from repo root: docker build -f backend-api/Dockerfile -t backend .
#
# Context MUST be the repo root so we can COPY both backend-api/ and the shared
# contracts/ directory into the image. Deploy target: ECR -> AWS App Runner.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONTRACTS_DIR=/app/contracts

WORKDIR /app

# Install deps first for better layer caching.
COPY backend-api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App code and the shared contracts (schemas + samples).
COPY backend-api/ /app/
COPY contracts/ /app/contracts/

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
