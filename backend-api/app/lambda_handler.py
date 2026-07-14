"""AWS Lambda entrypoint.

Wraps the FastAPI app with Mangum so it runs behind a Lambda Function URL
(or API Gateway). Used by Dockerfile.lambda (CMD app.lambda_handler.handler).
Local/App-Runner runs still use `uvicorn app.main:app` — this file is Lambda-only.
"""
from mangum import Mangum

from app.main import app

handler = Mangum(app)
