#!/usr/bin/env python3
"""MCP stdio → authenticated HTTP. Supports modern and legacy 2025-11-25 clients.

Credentials come ONLY from FACTORY_AGENT_TOKEN (never from tool arguments).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from mcp_protocol import META_PREFIX, VERSION, SERVER_INFO, error


def main():
    token = os.environ.get("FACTORY_AGENT_TOKEN")
    if not token:
        print("FACTORY_AGENT_TOKEN is required", file=sys.stderr)
        return 1
    url = os.environ.get("FACTORY_URL", "http://127.0.0.1:8765") + "/mcp"
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username or parsed.password:
        print("Only loopback HTTP factory URLs are supported", file=sys.stderr)
        return 1
    legacy = False
    for line in sys.stdin:
        request_id = None
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("Expected object")
            request_id = payload.get("id")
            if payload.get("method") == "initialize":
                legacy = True
                result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO,
                          "instructions": "Local tools-only profile; human approvals happen in the separate dashboard."}
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            elif "id" not in payload:
                continue  # initialized/cancelled notifications; calls are synchronous here.
            else:
                params = payload.setdefault("params", {})
                meta = params.setdefault("_meta", {})
                meta.update({META_PREFIX + "protocolVersion": VERSION,
                             META_PREFIX + "clientCapabilities": meta.get(META_PREFIX + "clientCapabilities", {}),
                             META_PREFIX + "clientInfo": meta.get(META_PREFIX + "clientInfo", {"name": "stdio-bridge", "version": "0.1"})})
                headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                           "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": VERSION,
                           "Mcp-Method": payload.get("method", "")}
                if payload.get("method") == "tools/call":
                    headers["Mcp-Name"] = params.get("name", "")
                req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        response = json.load(r)
                except urllib.error.HTTPError as e:
                    response = json.load(e)
                    if "error" not in response:
                        response = error(request_id, -32603, "HTTP request rejected", {"status": e.code})
                if legacy and "result" in response:
                    response["result"].pop("resultType", None)
            print(json.dumps(response, ensure_ascii=False), flush=True)
        except (ValueError, TypeError, KeyError):
            print(json.dumps(error(request_id, -32700, "Invalid JSON-RPC input")), flush=True)
        except (OSError, urllib.error.URLError):
            print(json.dumps(error(request_id, -32603, "Factory server unavailable")), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
