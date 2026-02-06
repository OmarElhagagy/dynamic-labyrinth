#!/usr/bin/env python3
"""
Dynamic Labyrinth - Pool Status CLI Tool
=========================================

Command-line tool for monitoring and managing honeytrap container pools.

Usage:
    python pool_status.py status [--json]
    python pool_status.py health [--verbose]
    python pool_status.py assign <level>
    python pool_status.py release <container_id>
    python pool_status.py scale <level> <count>
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import hmac
import hashlib
import time

# Configuration
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
HMAC_SECRET = os.getenv("HMAC_SECRET", "")

# ANSI Colors
class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    NC = "\033[0m"  # No Color


def generate_hmac_signature(body: str = "") -> dict:
    """Generate HMAC authentication headers."""
    if not HMAC_SECRET:
        return {}
    
    timestamp = str(int(time.time()))
    message = f"{timestamp}:{body}"
    signature = hmac.new(
        HMAC_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return {
        "X-Timestamp": timestamp,
        "X-Signature": signature,
    }


def make_request(endpoint: str, method: str = "GET", data: Optional[dict] = None) -> dict:
    """Make an HTTP request to the orchestrator API."""
    url = f"{ORCHESTRATOR_URL}{endpoint}"
    body = json.dumps(data) if data else ""
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers.update(generate_hmac_signature(body))
    
    request = Request(url, method=method, headers=headers)
    
    if data:
        request.data = body.encode()
    
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        raise Exception(f"HTTP {e.code}: {error_body}")
    except URLError as e:
        raise Exception(f"Connection error: {e.reason}")


def cmd_status(args):
    """Show pool status."""
    try:
        pools = make_request("/pools")
        
        if args.json:
            print(json.dumps(pools, indent=2))
            return 0
        
        print("\n" + "=" * 60)
        print("Dynamic Labyrinth - Pool Status")
        print("=" * 60)
        print(f"Timestamp: {datetime.now().isoformat()}")
        print()
        
        total_available = 0
        total_in_use = 0
        total_unhealthy = 0
        
        for pool_name, pool_data in pools.get("pools", {}).items():
            status = pool_data.get("status", "unknown")
            available = pool_data.get("available", 0)
            in_use = pool_data.get("in_use", 0)
            unhealthy = pool_data.get("unhealthy", 0)
            
            total_available += available
            total_in_use += in_use
            total_unhealthy += unhealthy
            
            # Status color
            if status == "healthy":
                status_color = Colors.GREEN
            elif status == "degraded":
                status_color = Colors.YELLOW
            else:
                status_color = Colors.RED
            
            print(f"{pool_name}:")
            print(f"  Status:    {status_color}{status}{Colors.NC}")
            print(f"  Available: {available}")
            print(f"  In Use:    {in_use}")
            print(f"  Unhealthy: {unhealthy}")
            print()
        
        print("-" * 60)
        print(f"Total: {total_available} available, {total_in_use} in use, {total_unhealthy} unhealthy")
        print()
        
        return 0
        
    except Exception as e:
        print(f"{Colors.RED}Error:{Colors.NC} {e}", file=sys.stderr)
        return 1


def cmd_health(args):
    """Check system health."""
    try:
        health = make_request("/healthz")
        
        if args.json:
            print(json.dumps(health, indent=2))
            return 0 if health.get("status") == "ok" else 1
        
        status = health.get("status", "unknown")
        
        if status == "ok":
            print(f"{Colors.GREEN}✓ System is healthy{Colors.NC}")
            if args.verbose:
                print(f"  Uptime: {health.get('uptime', 'N/A')}")
                print(f"  Database: {health.get('database', 'N/A')}")
        else:
            print(f"{Colors.RED}✗ System is unhealthy{Colors.NC}")
            if args.verbose:
                print(f"  Details: {health.get('details', 'N/A')}")
            return 1
        
        return 0
        
    except Exception as e:
        print(f"{Colors.RED}✗ Health check failed: {e}{Colors.NC}", file=sys.stderr)
        return 1


def cmd_assign(args):
    """Assign a container from a pool."""
    try:
        result = make_request("/pools/assign", method="POST", data={
            "level": args.level,
            "session_id": args.session,
        })
        
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        
        container_id = result.get("container_id")
        if container_id:
            print(f"{Colors.GREEN}✓ Assigned container: {container_id}{Colors.NC}")
            print(f"  Level: {args.level}")
            print(f"  Session: {args.session or 'auto-generated'}")
        else:
            print(f"{Colors.RED}✗ Failed to assign container{Colors.NC}")
            return 1
        
        return 0
        
    except Exception as e:
        print(f"{Colors.RED}Error:{Colors.NC} {e}", file=sys.stderr)
        return 1


def cmd_release(args):
    """Release a container back to the pool."""
    try:
        result = make_request(f"/pools/release/{args.container_id}", method="POST")
        
        if args.json:
            print(json.dumps(result, indent=2))
            return 0
        
        if result.get("success"):
            print(f"{Colors.GREEN}✓ Released container: {args.container_id}{Colors.NC}")
        else:
            print(f"{Colors.RED}✗ Failed to release container{Colors.NC}")
            return 1
        
        return 0
        
    except Exception as e:
        print(f"{Colors.RED}Error:{Colors.NC} {e}", file=sys.stderr)
        return 1


def cmd_sessions(args):
    """List active sessions."""
    try:
        sessions = make_request("/sessions")
        
        if args.json:
            print(json.dumps(sessions, indent=2))
            return 0
        
        print("\n" + "=" * 60)
        print("Active Sessions")
        print("=" * 60)
        print()
        
        session_list = sessions.get("sessions", [])
        
        if not session_list:
            print("No active sessions")
            return 0
        
        for session in session_list:
            session_id = session.get("session_id", "unknown")
            container = session.get("container_id", "N/A")
            level = session.get("level", "N/A")
            created = session.get("created_at", "N/A")
            
            print(f"Session: {session_id}")
            print(f"  Container: {container}")
            print(f"  Level: {level}")
            print(f"  Created: {created}")
            print()
        
        print(f"Total: {len(session_list)} active sessions")
        return 0
        
    except Exception as e:
        print(f"{Colors.RED}Error:{Colors.NC} {e}", file=sys.stderr)
        return 1


def cmd_metrics(args):
    """Show Prometheus metrics."""
    try:
        metrics = make_request("/metrics")
        
        if args.json:
            print(json.dumps(metrics, indent=2))
            return 0
        
        print("\n" + "=" * 60)
        print("Metrics")
        print("=" * 60)
        print()
        
        for name, value in metrics.items():
            print(f"{name}: {value}")
        
        return 0
        
    except Exception as e:
        print(f"{Colors.RED}Error:{Colors.NC} {e}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Dynamic Labyrinth - Pool Status CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Status command
    status_parser = subparsers.add_parser("status", help="Show pool status")
    
    # Health command
    health_parser = subparsers.add_parser("health", help="Check system health")
    health_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    
    # Assign command
    assign_parser = subparsers.add_parser("assign", help="Assign container from pool")
    assign_parser.add_argument("level", type=int, choices=[1, 2, 3], help="Interaction level")
    assign_parser.add_argument("--session", help="Session ID (auto-generated if not provided)")
    
    # Release command
    release_parser = subparsers.add_parser("release", help="Release container to pool")
    release_parser.add_argument("container_id", help="Container ID to release")
    
    # Sessions command
    sessions_parser = subparsers.add_parser("sessions", help="List active sessions")
    
    # Metrics command
    metrics_parser = subparsers.add_parser("metrics", help="Show metrics")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    # Route to command handler
    commands = {
        "status": cmd_status,
        "health": cmd_health,
        "assign": cmd_assign,
        "release": cmd_release,
        "sessions": cmd_sessions,
        "metrics": cmd_metrics,
    }
    
    handler = commands.get(args.command)
    if handler:
        return handler(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
