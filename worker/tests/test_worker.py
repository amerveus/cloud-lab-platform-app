import json

import boto3
import pytest
from moto import mock_aws

import worker


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(worker, "WORK_SECONDS", 0)
    with mock_aws():
        t = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="labs",
            KeySchema=[{"AttributeName": "request_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "request_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        t.put_item(Item={"request_id": "r1", "status": "PENDING"})
        yield t


def status_of(table, request_id):
    return table.get_item(Key={"request_id": request_id})["Item"]["status"]


def test_job_marks_lab_ready(table):
    worker.process(json.dumps({"request_id": "r1", "lab_type": "eks-intro"}), table)
    assert status_of(table, "r1") == "READY"


def test_poison_message_raises_and_is_left_for_retry(table):
    with pytest.raises(RuntimeError):
        worker.process(json.dumps({"request_id": "r1", "lab_type": "poison-test"}), table)
    assert status_of(table, "r1") == "PENDING"


def test_unknown_request_is_not_created(table):
    with pytest.raises(Exception):
        worker.process(json.dumps({"request_id": "ghost", "lab_type": "eks-intro"}), table)
    assert "Item" not in table.get_item(Key={"request_id": "ghost"})
