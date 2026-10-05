"""lab-worker: consumes lab jobs from SQS, provisions them (simulated), records status in DynamoDB."""
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

import boto3

REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
WORK_SECONDS = float(os.getenv("WORK_SECONDS", "2"))
NOTIFY_ON_READY = os.getenv("NOTIFY_ON_READY", "false").lower() == "true"

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")
log = logging.getLogger("lab-worker")
for _noisy in ("botocore", "boto3", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

_running = True


def log_event(**fields):
    log.info(json.dumps({"service": "lab-worker", **fields}))


def _request_stop(signum, _frame):
    global _running
    _running = False
    log_event(event="shutdown_requested", signal=signum)


def set_status(table, request_id, status):
    table.update_item(
        Key={"request_id": request_id},
        UpdateExpression="SET #s = :s, updated_at = :t",
        ConditionExpression="attribute_exists(request_id)",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":t": datetime.now(timezone.utc).isoformat()},
    )


def process(body, table, sns=None, topic_arn=None):
    job = json.loads(body)
    request_id = job["request_id"]
    if job.get("lab_type") == "poison-test":
        raise RuntimeError("simulated provisioning failure")
    set_status(table, request_id, "PROVISIONING")
    time.sleep(WORK_SECONDS)
    set_status(table, request_id, "READY")
    if sns and topic_arn:
        sns.publish(TopicArn=topic_arn, Subject="Lab ready", Message=f"Lab {request_id} is ready.")
    return request_id


def main():
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    queue_url = os.environ["QUEUE_URL"]
    table = boto3.resource("dynamodb", region_name=REGION).Table(os.environ["TABLE_NAME"])
    sqs = boto3.client("sqs", region_name=REGION)
    topic_arn = os.getenv("TOPIC_ARN") if NOTIFY_ON_READY else None
    sns = boto3.client("sns", region_name=REGION) if topic_arn else None
    log_event(event="worker_started", notify=bool(topic_arn))

    while _running:
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=5,
            WaitTimeSeconds=20,
            AttributeNames=["ApproximateReceiveCount"],
        )
        for msg in resp.get("Messages", []):
            if not _running:
                break
            receives = msg.get("Attributes", {}).get("ApproximateReceiveCount")
            try:
                request_id = process(msg["Body"], table, sns, topic_arn)
            except Exception as exc:
                log_event(event="job_failed", error=str(exc), receive_count=receives,
                          message_id=msg["MessageId"])
                continue
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            log_event(event="job_done", request_id=request_id, receive_count=receives)

    log_event(event="worker_stopped")


if __name__ == "__main__":
    main()
