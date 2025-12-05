#!/usr/bin/env python3
"""
Minimal ThingsBoard bootstrapper:
- ensures demo devices exist with fixed access tokens
- installs a root rule chain that saves telemetry and forwards it to Kafka
"""
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import quote_plus
from uuid import uuid4
from typing import Dict, Optional


class RequestError(Exception):
    pass


TB_URL = os.getenv("TB_URL", "http://localhost:8080")
TB_USERNAME = os.getenv("TB_USERNAME", "tenant@thingsboard.org")
TB_PASSWORD = os.getenv("TB_PASSWORD", "tenant")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "tb-telemetry")
DEVICES = json.loads(os.getenv("TB_DEVICES", "[]"))


def _http_request(path: str, method: str = "GET", payload: Optional[Dict] = None, token: Optional[str] = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{TB_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("X-Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            if not body:
                return None
            return json.loads(body.decode())
    except urllib.error.HTTPError as http_err:
        detail = http_err.read().decode()
        raise RequestError(f"HTTP {http_err.code} for {path}: {detail}")
    except urllib.error.URLError as err:
        raise RequestError(f"Request to {path} failed: {err}")


def wait_for_tb(timeout: int = 240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{TB_URL}/api/health", timeout=5) as resp:
                if resp.status in (200, 401, 403):
                    return
        except urllib.error.HTTPError as http_err:
            if http_err.code in (401, 403):
                return
        except Exception:
            pass
        time.sleep(5)
    raise RequestError("ThingsBoard API did not become ready in time")


def login() -> str:
    result = _http_request(
        "/api/auth/login",
        method="POST",
        payload={"username": TB_USERNAME, "password": TB_PASSWORD},
    )
    if not result or "token" not in result:
        raise RequestError("Failed to retrieve ThingsBoard token")
    return result["token"]


def find_device(token: str, name: str) -> Optional[Dict]:
    page = _http_request(
        f"/api/tenant/devices?pageSize=50&page=0&textSearch={quote_plus(name)}",
        token=token,
    ) or {}
    for item in page.get("data", []):
        if item.get("name") == name:
            return item
    return None


def ensure_device(token: str, name: str, access_token: str) -> Dict:
    device = find_device(token, name)
    if not device:
        device = _http_request(
            "/api/device",
            method="POST",
            token=token,
            payload={"name": name, "type": "drone"},
        )
        print(f"[+] Created device {name}")
    dev_id = device["id"]["id"]
    credentials = _http_request(f"/api/device/{dev_id}/credentials", token=token)
    cred_payload = {
        "credentialsType": "ACCESS_TOKEN",
        "credentialsId": access_token,
        "deviceId": {"entityType": "DEVICE", "id": dev_id},
    }
    if credentials and credentials.get("id"):
        cred_payload["id"] = credentials["id"]
        cred_payload["deviceId"] = credentials.get("deviceId", cred_payload["deviceId"])
    _http_request(
        "/api/device/credentials",
        method="POST",
        token=token,
        payload=cred_payload,
    )
    return device


def find_rule_chain(token: str, name: str) -> Optional[Dict]:
    page = _http_request(
        f"/api/ruleChains?pageSize=50&page=0&textSearch={quote_plus(name)}",
        token=token,
    ) or {}
    for item in page.get("data", []):
        if item.get("name") == name:
            return item
    return None


def ensure_kafka_sink_in_root_chain(token: str) -> Dict:
    root_chain = find_rule_chain(token, "Root Rule Chain")
    if not root_chain:
        raise RequestError("Root Rule Chain not found")
    chain_id = root_chain["id"]["id"]
    metadata = _http_request(
        f"/api/ruleChain/{chain_id}/metadata",
        token=token,
    ) or {}
    nodes = metadata.get("nodes", [])
    connections = metadata.get("connections", []) or []

    kafka_index = next(
        (idx for idx, node in enumerate(nodes) if node.get("type") == "org.thingsboard.rule.engine.kafka.TbKafkaNode"),
        None,
    )
    msg_switch_index = next(
        (idx for idx, node in enumerate(nodes) if "TbMsgTypeSwitchNode" in node.get("type", "")),
        None,
    )
    if msg_switch_index is None:
        raise RequestError("Message Type Switch node not found in root rule chain")

    if kafka_index is None:
        kafka_node = {
            "id": {"entityType": "RULE_NODE", "id": str(uuid4())},
            "createdTime": int(time.time() * 1000),
            "ruleChainId": {"entityType": "RULE_CHAIN", "id": chain_id},
            "type": "org.thingsboard.rule.engine.kafka.TbKafkaNode",
            "name": "Kafka sink",
            "debugSettings": None,
            "singletonMode": False,
            "queueName": None,
            "configurationVersion": 0,
            "configuration": {
                "topic": KAFKA_TOPIC,
                "bootstrapServers": KAFKA_BOOTSTRAP,
                "sync": False,
                "timeout": 3000,
                "retries": 1,
                "acks": "1",
                "batchSize": 16384,
                "linger": 1,
                "maxRequestSize": 1048576,
                "key": "${deviceName}",
                "addMetadata": False,
            },
            "externalId": None,
            "additionalInfo": {"layoutX": 1000, "layoutY": 320},
        }
        nodes.append(kafka_node)
        kafka_index = len(nodes) - 1
        print("[+] Added Kafka sink node to Root Rule Chain")

    if not any(
        conn.get("fromIndex") == msg_switch_index and conn.get("toIndex") == kafka_index
        for conn in connections
    ):
        connections.append({"fromIndex": msg_switch_index, "toIndex": kafka_index, "type": "Post telemetry"})
        print("[+] Linked Message Type Switch to Kafka sink for Post telemetry")

    metadata["nodes"] = nodes
    metadata["connections"] = connections

    try:
        _http_request(
            f"/api/ruleChain/{chain_id}/metadata",
            method="POST",
            token=token,
            payload=metadata,
        )
    except RequestError:
        _http_request(
            "/api/ruleChain/metadata",
            method="POST",
            token=token,
            payload=metadata,
        )
    print("[+] Root Rule Chain updated with Kafka forwarding")
    return root_chain


def main():
    wait_for_tb()
    token = login()
    print("[+] Authenticated against ThingsBoard")
    for dev in DEVICES:
        ensure_device(token, dev["name"], dev["token"])
        print(f"[+] Ensured device {dev['name']} with token {dev['token']}")
    ensure_kafka_sink_in_root_chain(token)
    print("[+] ThingsBoard bootstrap complete")


if __name__ == "__main__":
    try:
        main()
    except RequestError as exc:
        raise SystemExit(str(exc))
