import json
import sys


def reply(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        continue
    if "id" not in request:
        continue
    method = request.get("method")
    if method == "initialize":
        reply(request["id"], {"protocolVersion": "2024-11-05", "capabilities": {}})
    elif method == "tools/list":
        reply(request["id"], {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object"}}]})
    elif method == "resources/list":
        reply(request["id"], {"resources": [{"uri": "memo://one", "name": "Memo", "mimeType": "text/plain"}]})
    elif method == "tools/call":
        args = request.get("params", {}).get("arguments", {})
        reply(request["id"], {"content": [{"type": "text", "text": str(args.get("text", ""))}]})
    elif method == "resources/read":
        reply(request["id"], {"contents": [{"mimeType": "text/plain", "text": "live memo"}]})
    else:
        reply(request["id"], error={"code": -32601, "message": "method not found"})
