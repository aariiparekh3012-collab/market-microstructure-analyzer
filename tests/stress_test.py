#!/usr/bin/env python3
"""WebSocket stress test for the Market Microstructure Analyzer server.

Standalone script — run directly:
    cd /root/mma && python tests/stress_test.py --clients 50 --duration 15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StressTestConfig:
    num_clients: int = 100
    duration_seconds: int = 30
    endpoint: str = "/ws/orderbook/RELIANCE"
    ramp_up_seconds: int = 5


@dataclass
class ClientStats:
    messages_received: int = 0
    connection_time_ms: float = 0.0
    first_message_latency_ms: float = 0.0
    dropped_messages: int = 0
    errors: list = field(default_factory=list)
    disconnect_count: int = 0


@dataclass
class StressTestResult:
    total_clients_connected: int = 0
    total_messages_received: int = 0
    messages_per_second: float = 0.0
    per_client_msg_rate: float = 0.0
    connection_success_rate: float = 0.0
    mean_first_msg_latency_ms: float = 0.0
    p99_first_msg_latency_ms: float = 0.0
    total_errors: int = 0
    total_disconnects: int = 0

    def to_dict(self) -> dict:
        return {
            "total_clients_connected": self.total_clients_connected,
            "total_messages_received": self.total_messages_received,
            "messages_per_second": round(self.messages_per_second, 2),
            "per_client_msg_rate": round(self.per_client_msg_rate, 2),
            "connection_success_rate": round(self.connection_success_rate, 4),
            "mean_first_msg_latency_ms": round(self.mean_first_msg_latency_ms, 2),
            "p99_first_msg_latency_ms": round(self.p99_first_msg_latency_ms, 2),
            "total_errors": self.total_errors,
            "total_disconnects": self.total_disconnects,
        }


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------

def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def start_server(port: int = 8765) -> subprocess.Popen:
    env = os.environ.copy()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "backend.api.main:app",
            "--host", "0.0.0.0",
            "--port", str(port),
            "--log-level", "warning",
        ],
        cwd="/root/mma",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for readiness
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            stdout = proc.stdout.read().decode() if proc.stdout else ""
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            raise RuntimeError(
                f"Server exited early (rc={proc.returncode}).\n"
                f"stdout: {stdout}\nstderr: {stderr}"
            )
        if _port_open(port):
            # Give the lifespan a moment to finish starting the streamer
            time.sleep(1)
            return proc
        time.sleep(0.3)
    proc.kill()
    raise RuntimeError("Server did not become ready within 20 seconds")


def kill_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


# ---------------------------------------------------------------------------
# WebSocket client worker
# ---------------------------------------------------------------------------

async def client_worker(
    client_id: int,
    ws_url: str,
    duration: float,
    stats: ClientStats,
) -> None:
    """Connect, receive messages for *duration* seconds, record stats."""
    import websockets
    import websockets.exceptions

    last_seq: Optional[int] = None
    t_connect_start = time.monotonic()

    try:
        async with websockets.connect(
            ws_url,
            open_timeout=10,
            close_timeout=5,
            max_size=2**20,
        ) as ws:
            stats.connection_time_ms = (time.monotonic() - t_connect_start) * 1000
            deadline = time.monotonic() + duration

            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    stats.disconnect_count += 1
                    stats.errors.append("connection_closed_during_recv")
                    return

                stats.messages_received += 1

                if stats.messages_received == 1:
                    stats.first_message_latency_ms = (
                        time.monotonic() - t_connect_start
                    ) * 1000 - stats.connection_time_ms

                # Detect dropped messages via sequence gaps
                try:
                    data = json.loads(raw)
                    seq = data.get("seq")
                    if seq is not None and last_seq is not None:
                        gap = seq - last_seq
                        if gap > 1:
                            stats.dropped_messages += gap - 1
                    if seq is not None:
                        last_seq = seq
                except (json.JSONDecodeError, TypeError):
                    pass

    except Exception as exc:
        stats.connection_time_ms = (time.monotonic() - t_connect_start) * 1000
        stats.errors.append(f"{type(exc).__name__}: {exc}")
        stats.disconnect_count += 1


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run_stress_test(config: StressTestConfig, port: int = 8765) -> StressTestResult:
    ws_url = f"ws://127.0.0.1:{port}{config.endpoint}"
    all_stats: List[ClientStats] = [ClientStats() for _ in range(config.num_clients)]

    ramp_delay = (
        config.ramp_up_seconds / config.num_clients
        if config.num_clients > 1
        else 0
    )

    tasks: List[asyncio.Task] = []
    print(f"  Ramping up {config.num_clients} clients over {config.ramp_up_seconds}s ...")

    for i in range(config.num_clients):
        t = asyncio.create_task(
            client_worker(i, ws_url, config.duration_seconds, all_stats[i])
        )
        tasks.append(t)
        if ramp_delay > 0 and i < config.num_clients - 1:
            await asyncio.sleep(ramp_delay)

    print(f"  All clients launched. Waiting {config.duration_seconds}s for data collection ...")
    await asyncio.gather(*tasks, return_exceptions=True)

    # --- aggregate ---
    connected = sum(1 for s in all_stats if s.connection_time_ms > 0 and not (
        s.messages_received == 0 and s.errors
    ))
    total_msgs = sum(s.messages_received for s in all_stats)
    total_errors = sum(len(s.errors) for s in all_stats)
    total_disconnects = sum(s.disconnect_count for s in all_stats)

    effective_duration = config.duration_seconds
    mps = total_msgs / effective_duration if effective_duration > 0 else 0
    per_client = mps / connected if connected > 0 else 0

    latencies = [
        s.first_message_latency_ms
        for s in all_stats
        if s.messages_received > 0
    ]
    latencies.sort()
    mean_lat = sum(latencies) / len(latencies) if latencies else 0.0
    p99_lat = latencies[int(len(latencies) * 0.99)] if latencies else 0.0

    return StressTestResult(
        total_clients_connected=connected,
        total_messages_received=total_msgs,
        messages_per_second=mps,
        per_client_msg_rate=per_client,
        connection_success_rate=connected / config.num_clients if config.num_clients else 0,
        mean_first_msg_latency_ms=mean_lat,
        p99_first_msg_latency_ms=p99_lat,
        total_errors=total_errors,
        total_disconnects=total_disconnects,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(result: StressTestResult, config: StressTestConfig) -> None:
    print("\n" + "=" * 60)
    print("  WEBSOCKET STRESS TEST REPORT")
    print("=" * 60)
    print(f"  Endpoint:                {config.endpoint}")
    print(f"  Clients requested:       {config.num_clients}")
    print(f"  Duration:                {config.duration_seconds}s")
    print(f"  Ramp-up:                 {config.ramp_up_seconds}s")
    print("-" * 60)
    print(f"  Clients connected:       {result.total_clients_connected}")
    print(f"  Connection success rate: {result.connection_success_rate:.1%}")
    print(f"  Total messages received: {result.total_messages_received:,}")
    print(f"  Aggregate msg/s:         {result.messages_per_second:,.1f}")
    print(f"  Per-client msg/s (mean): {result.per_client_msg_rate:,.1f}")
    print(f"  Mean first-msg latency:  {result.mean_first_msg_latency_ms:,.1f} ms")
    print(f"  P99 first-msg latency:   {result.p99_first_msg_latency_ms:,.1f} ms")
    print(f"  Total errors:            {result.total_errors}")
    print(f"  Total disconnects:       {result.total_disconnects}")
    print("=" * 60)


def save_results(result: StressTestResult, config: StressTestConfig, path: str) -> None:
    output = {
        "config": {
            "num_clients": config.num_clients,
            "duration_seconds": config.duration_seconds,
            "endpoint": config.endpoint,
            "ramp_up_seconds": config.ramp_up_seconds,
        },
        "results": result.to_dict(),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Results saved to {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="WebSocket stress test")
    parser.add_argument("--clients", type=int, default=100, help="Number of concurrent WS clients")
    parser.add_argument("--duration", type=int, default=30, help="Test duration in seconds")
    parser.add_argument("--endpoint", type=str, default="/ws/orderbook/RELIANCE",
                        help="WebSocket endpoint path")
    parser.add_argument("--ramp-up", type=int, default=5, help="Ramp-up period in seconds")
    parser.add_argument("--port", type=int, default=8765, help="Server port")
    parser.add_argument("--output", type=str, default="/root/mma/data/stress_test_results.json",
                        help="Output JSON path")
    args = parser.parse_args()

    config = StressTestConfig(
        num_clients=args.clients,
        duration_seconds=args.duration,
        endpoint=args.endpoint,
        ramp_up_seconds=args.ramp_up,
    )

    print(f"[*] Starting FastAPI server on port {args.port} ...")
    server_proc = start_server(port=args.port)
    print(f"[*] Server ready (pid={server_proc.pid})")

    try:
        result = asyncio.run(run_stress_test(config, port=args.port))
        print_report(result, config)
        save_results(result, config, args.output)
    finally:
        print("\n[*] Shutting down server ...")
        kill_server(server_proc)
        print("[*] Done.")


if __name__ == "__main__":
    main()
