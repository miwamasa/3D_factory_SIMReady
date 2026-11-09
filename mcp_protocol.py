"""Small tools-only MCP profile; HTTP modern, stdio bridge offers legacy compatibility."""
import base64
import json

from policy import LabError

VERSION = "2026-07-28"
SERVER_INFO = {"name": "motor-factory-lab", "version": "0.1.0"}
META_PREFIX = "io.modelcontextprotocol/"


def obj(props=None, required=None):
    return {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}


TOOLS = [
    {"name": "mes_get_snapshot", "description": "ISA95 L3: 工程、WIP、品質、アラーム、KPIのスナップショット", "inputSchema": obj()},
    {"name": "erp_get_orders", "description": "ISA95 L4: 製造指図と優先度・実績を取得", "inputSchema": obj()},
    {"name": "erp_get_inventory", "description": "ISA95 L4: 部材在庫・入荷予定を取得", "inputSchema": obj()},
    {"name": "factory_get_policy", "description": "操作カタログ、制約、担当承認ロールを取得", "inputSchema": obj()},
    {"name": "factory_get_audit", "description": "直近80件の操作・承認・拒否監査ログを取得", "inputSchema": obj()},
    {"name": "factory_list_proposals", "description": "自分の操作案・承認状態を取得", "inputSchema": obj()},
    {"name": "factory_preflight", "description": "副作用のない操作計画と影響を作成。必要なら人間の別経路での承認を待つ。設備状態は変更しない。",
     "inputSchema": obj({"action": {"type": "string"}, "target": {"type": "string"},
                         "arguments": {"type": "object", "description": "factory_get_policyの操作別引数"}}, ["action", "target", "arguments"])},
    {"name": "factory_execute", "description": "承認済み/自動許可の操作案を一度だけ実行。引数変更は受け付けない。有効期限と状態を再検証。",
     "inputSchema": obj({"proposal_id": {"type": "string"}, "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 128}}, ["proposal_id", "idempotency_key"])},
]
TOOLS.sort(key=lambda t: t["name"])
for tool in TOOLS:
    read = tool["name"] not in ("factory_preflight", "factory_execute")
    tool["annotations"] = {"readOnlyHint": read, "destructiveHint": not read, "openWorldHint": False}


def error(request_id, code, message, data=None):
    result = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    if data is not None:
        result["error"]["data"] = data
    return result


def decode_header(value):
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (ValueError, UnicodeError):
            return None
    return value


def dispatch(factory, principal, payload, headers):
    request_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        return 400, error(request_id, -32600, "Invalid request")
    if "id" not in payload or type(request_id) not in (str, int):
        return 400, error(None, -32600, "This synchronous profile accepts requests with string/integer IDs")
    method, params = payload["method"], payload.get("params", {})
    if not isinstance(params, dict) or not isinstance(params.get("_meta"), dict):
        return 400, error(request_id, -32602, "Per-request _meta is required", {"supported": [VERSION]})
    meta = params["_meta"]
    version = meta.get(META_PREFIX + "protocolVersion")
    if not headers.get("MCP-Protocol-Version") or headers.get("MCP-Protocol-Version") != version or decode_header(headers.get("Mcp-Method")) != method:
        return 400, error(request_id, -32020, "HeaderMismatch")
    if version != VERSION:
        return 400, error(request_id, -32022, "Unsupported protocol version", {"supported": [VERSION], "requested": version})
    if not isinstance(meta.get(META_PREFIX + "clientCapabilities"), dict):
        return 400, error(request_id, -32602, "clientCapabilities object is required")
    if method == "tools/call" and (not isinstance(params.get("name"), str) or decode_header(headers.get("Mcp-Name")) != params["name"]):
        return 400, error(request_id, -32020, "HeaderMismatch")
    if method == "server/discover":
        result = {"supportedVersions": [VERSION], "capabilities": {"tools": {}}, "ttlMs": 60000, "cacheScope": "private",
                  "instructions": "Read policy, preflight, wait for an independent human approval, execute exact proposal. Never self-approve."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        tools = [t for t in TOOLS if principal["role"] != "viewer" or t["annotations"]["readOnlyHint"]]
        result = {"tools": tools, "ttlMs": 60000, "cacheScope": "private"}
    elif method == "tools/call":
        name = params["name"]
        if name not in {t["name"] for t in TOOLS}:
            return 200, error(request_id, -32602, "Unknown tool")
        try:
            value = factory.call(principal, name, params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value, "isError": False}
        except LabError as err:
            factory.log_denial(principal, name, err)
            value = err.as_dict()
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "structuredContent": value, "isError": True}
    else:
        return 404, error(request_id, -32601, "Method not found", {"supported": [VERSION]})
    result.update(resultType="complete", _meta={META_PREFIX + "serverInfo": SERVER_INFO})
    return 200, {"jsonrpc": "2.0", "id": request_id, "result": result}
