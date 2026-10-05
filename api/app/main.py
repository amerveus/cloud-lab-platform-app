"""lab-api: accepts lab environment requests, stores them, and queues them for provisioning."""
import hmac
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

import boto3
from fastapi import FastAPI, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
log = logging.getLogger("lab-api")
for _noisy in ("botocore", "boto3", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


class _SkipHealthChecks(logging.Filter):
    def filter(self, record):
        return "/healthz" not in record.getMessage()


logging.getLogger("uvicorn.access").addFilter(_SkipHealthChecks())

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "route"]
)
LABS_CREATED = Counter("lab_requests_created_total", "Lab requests accepted", ["lab_type"])

app = FastAPI(title="lab-api", version=os.getenv("APP_VERSION", "dev"))

LabType = Literal["ec2-basics", "vpc-networking", "eks-intro", "poison-test"]


class LabRequest(BaseModel):
    student_id: str = Field(min_length=1, max_length=64)
    lab_type: LabType


@lru_cache
def table():
    return boto3.resource("dynamodb", region_name=REGION).Table(os.environ["TABLE_NAME"])


@lru_cache
def sqs():
    return boto3.client("sqs", region_name=REGION)


def log_event(**fields):
    log.info(json.dumps({"service": "lab-api", **fields}))


@app.middleware("http")
async def record_metrics(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
        HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/labs", status_code=202)
def create_lab(req: LabRequest):
    request_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    table().put_item(
        Item={
            "request_id": request_id,
            "student_id": req.student_id,
            "lab_type": req.lab_type,
            "status": "PENDING",
            "created_at": now,
            "updated_at": now,
        }
    )
    sqs().send_message(
        QueueUrl=os.environ["QUEUE_URL"],
        MessageBody=json.dumps({"request_id": request_id, "lab_type": req.lab_type}),
    )
    LABS_CREATED.labels(req.lab_type).inc()
    log_event(event="lab_requested", request_id=request_id, lab_type=req.lab_type)
    return {"request_id": request_id, "status": "PENDING"}


@app.get("/labs/{request_id}")
def get_lab(request_id: str):
    item = table().get_item(Key={"request_id": request_id}).get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="lab request not found")
    return item


@app.get("/chaos/error")
def chaos_error(x_chaos_token: str | None = Header(default=None)):
    """Fault injection for alert demos.

    Returns 404, as if the route did not exist, unless CHAOS_ENABLED=true AND the
    X-Chaos-Token header matches CHAOS_TOKEN. Fails closed when no token is configured.
    """
    expected = os.getenv("CHAOS_TOKEN", "")
    enabled = os.getenv("CHAOS_ENABLED", "false").lower() == "true"
    if not (enabled and expected and x_chaos_token
            and hmac.compare_digest(x_chaos_token, expected)):
        raise HTTPException(status_code=404, detail="not found")
    raise HTTPException(status_code=500, detail="injected failure")
