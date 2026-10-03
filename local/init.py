"""Creates the local DynamoDB table and SQS queue, retrying until both emulators are up."""
import os
import time

import boto3
from botocore.exceptions import ClientError

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
ddb = boto3.client("dynamodb", region_name=REGION)
sqs = boto3.client("sqs", region_name=REGION)

for attempt in range(30):
    try:
        ddb.list_tables()
        sqs.list_queues()
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit("local AWS emulators never became ready")

try:
    ddb.create_table(
        TableName=os.environ["TABLE_NAME"],
        KeySchema=[{"AttributeName": "request_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "request_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
except ClientError as err:
    if err.response["Error"]["Code"] != "ResourceInUseException":
        raise

sqs.create_queue(QueueName="lab-jobs")
print("local init complete")
