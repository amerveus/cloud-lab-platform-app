import json

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app import main


@pytest.fixture
def queue_url(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("dynamodb", region_name="us-east-1").create_table(
            TableName="labs",
            KeySchema=[{"AttributeName": "request_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "request_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        url = boto3.client("sqs", region_name="us-east-1").create_queue(QueueName="jobs")["QueueUrl"]
        monkeypatch.setenv("TABLE_NAME", "labs")
        monkeypatch.setenv("QUEUE_URL", url)
        main.table.cache_clear()
        main.sqs.cache_clear()
        yield url


@pytest.fixture
def client(queue_url):
    return TestClient(main.app)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_stores_and_enqueues(client, queue_url):
    resp = client.post("/labs", json={"student_id": "alex", "lab_type": "eks-intro"})
    assert resp.status_code == 202
    request_id = resp.json()["request_id"]

    got = client.get(f"/labs/{request_id}")
    assert got.status_code == 200
    assert got.json()["status"] == "PENDING"

    messages = boto3.client("sqs", region_name="us-east-1").receive_message(QueueUrl=queue_url)
    assert json.loads(messages["Messages"][0]["Body"])["request_id"] == request_id


def test_unknown_lab_returns_404(client):
    assert client.get("/labs/does-not-exist").status_code == 404


def test_invalid_lab_type_is_rejected(client):
    resp = client.post("/labs", json={"student_id": "alex", "lab_type": "mine-bitcoin"})
    assert resp.status_code == 422


def test_metrics_use_route_templates(client):
    client.get("/labs/some-random-id")
    body = client.get("/metrics").text
    assert 'route="/labs/{request_id}"' in body
    assert "some-random-id" not in body


def test_chaos_endpoint_disabled_by_default(client):
    assert client.get("/chaos/error").status_code == 404


def test_chaos_requires_matching_token(client, monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    monkeypatch.setenv("CHAOS_TOKEN", "s3cret")
    assert client.get("/chaos/error").status_code == 404
    assert client.get("/chaos/error", headers={"X-Chaos-Token": "wrong"}).status_code == 404
    assert client.get("/chaos/error", headers={"X-Chaos-Token": "s3cret"}).status_code == 500


def test_chaos_fails_closed_without_configured_token(client, monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    monkeypatch.delenv("CHAOS_TOKEN", raising=False)
    assert client.get("/chaos/error", headers={"X-Chaos-Token": ""}).status_code == 404
