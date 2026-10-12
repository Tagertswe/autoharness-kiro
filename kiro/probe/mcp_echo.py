"""Minimal stdio MCP server for the Kiro probe: one tool, no side effects.

Answers a single question from docs/kiro-port-spec.md (spike S3): can a local stdio MCP server
declared in an agent config be started and called on a locked-down install? Same wire shape as
autoharness.stage_skill.server (newline-delimited JSON-RPC, protocol 2024-11-05), with a tool that
only echoes, so trying it cannot stage or write anything.
"""
import json
import sys

TOOL_NAME = "probe_echo"
_PROTOCOL_VERSION = "2024-11-05"


def handle(request):
    method = request.get("method")
    req_id = request.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": _PROTOCOL_VERSION, "capabilities": {"tools": {}},
            "serverInfo": {"name": "ahprobe", "version": "0.1.0"}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": [{
            "name": TOOL_NAME,
            "description": "autoharness probe: echoes the text back with a marker word",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
        }]}}
    if method == "tools/call":
        params = request.get("params") or {}
        if params.get("name") != TOOL_NAME:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32602, "message": f"unknown tool: {params.get('name')}"}}
        text = (params.get("arguments") or {}).get("text", "")
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": f"AHPROBE-MCP-OK {text}"}]}}
    if req_id is None:
        return None  # notification (e.g. notifications/initialized): no reply
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        response = handle(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
