#!/usr/bin/env python3
"""
Simple drone telemetry simulator.
Posts JSON telemetry to ThingsBoard HTTP API using device access tokens.
"""
import argparse
import json
import random
import time
from typing import List, Optional
import requests

try:
    from kafka import KafkaProducer  # type: ignore
except ImportError:
    KafkaProducer = None  # type: ignore


def build_payload(drone_id: str):
    base_lat, base_lon = 40.4168, -3.7038  # Madrid as a reference point
    return {
        "ts": int(time.time() * 1000),
        "drone_id": drone_id,
        "latitude": base_lat + random.uniform(-0.05, 0.05),
        "longitude": base_lon + random.uniform(-0.05, 0.05),
        "battery": max(10, random.uniform(40, 100)),
        "altitude": random.uniform(50, 200),
        "speed": random.uniform(5, 25),
        "raw_payload": "",
    }


def send_telemetry(base_url: str, token: str, payload: dict, verbose: bool):
    url = f"{base_url}/api/v1/{token}/telemetry"
    response = requests.post(url, json=payload, timeout=10)
    if verbose:
        print(f"[{token}] {response.status_code} -> {payload}")
    response.raise_for_status()


def send_to_kafka(producer: KafkaProducer, topic: str, payload: dict, verbose: bool):
    producer.send(topic, value=payload)
    producer.flush()
    if verbose:
        print(f"[kafka:{topic}] {payload}")


def run_simulation(
    base_url: str,
    tokens: List[str],
    interval: int,
    once: bool,
    verbose: bool,
    kafka_bootstrap: Optional[str],
    kafka_topic: str,
):
    producer = None
    if kafka_bootstrap:
        if KafkaProducer is None:
            raise RuntimeError("kafka-python is required for Kafka publishing")
        producer = KafkaProducer(
            bootstrap_servers=kafka_bootstrap,
            value_serializer=lambda v: json.dumps(v).encode(),
            linger_ms=50,
        )
    while True:
        for token in tokens:
            drone_id = f"drone-{tokens.index(token)+1}"
            payload = build_payload(drone_id)
            send_telemetry(base_url, token, payload, verbose)
            if producer:
                send_to_kafka(producer, kafka_topic, payload, verbose)
        if once:
            break
        time.sleep(interval)


def parse_args():
    parser = argparse.ArgumentParser(description="Drone telemetry simulator for ThingsBoard.")
    parser.add_argument("--url", default="http://localhost:8080", help="ThingsBoard base URL.")
    parser.add_argument(
        "--tokens",
        nargs="+",
        default=["drone-1-token", "drone-2-token", "drone-3-token"],
        help="Device access tokens to target.",
    )
    parser.add_argument("--interval", type=int, default=15, help="Interval between bursts (seconds).")
    parser.add_argument("--once", action="store_true", help="Send a single burst and exit.")
    parser.add_argument("--verbose", action="store_true", help="Print each POST call.")
    parser.add_argument("--kafka-bootstrap", default=None, help="Kafka bootstrap servers (host:port) to also publish telemetry.")
    parser.add_argument("--kafka-topic", default="tb-telemetry", help="Kafka topic for telemetry forwarding.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_simulation(args.url, args.tokens, args.interval, args.once, args.verbose, args.kafka_bootstrap, args.kafka_topic)
