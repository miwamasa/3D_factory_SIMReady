"""Replaceable application authorization policy. No transport/session state."""
import math

POLICY_VERSION = "motor-lab/1"


class LabError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

    def as_dict(self):
        return {"ok": False, "code": self.code, "message": self.message}


def require(condition, code, message, status=409):
    if not condition:
        raise LabError(code, message, status)


ACTIONS = {
    "mes.pause_line": {"level": 3, "label": "ラインを一時停止", "approval": None,
                       "properties": {}, "target": "LINE-01"},
    "mes.resume_line": {"level": 3, "label": "ラインを再開", "approval": "supervisor",
                        "properties": {}, "target": "LINE-01"},
    "mes.set_torque": {"level": 3, "label": "締付レシピを変更", "approval": "supervisor",
                       "properties": {"torque_nm": {"type": "number", "minimum": 3.8, "maximum": 5.5}},
                       "target": "LINE-01"},
    "mes.maintenance": {"level": 3, "label": "保全作業を実施", "approval": "supervisor",
                        "properties": {"station_id": {"type": "string", "enum": ["S03", "S05"]}},
                        "target": "LINE-01"},
    "erp.set_priority": {"level": 4, "label": "製造指図の優先度を変更", "approval": "planner",
                         "properties": {"priority": {"type": "integer", "minimum": 1, "maximum": 5}},
                         "target": "order"},
    "erp.replenish": {"level": 4, "label": "部材補充を手配（60秒後入荷）", "approval": "planner",
                      "properties": {"quantity": {"type": "integer", "minimum": 10, "maximum": 300}},
                      "target": "MOTOR-KIT"},
    "erp.release_order": {"level": 4, "label": "製造指図をリリース", "approval": "planner",
                          "properties": {}, "target": "order"},
}


def validate_args(arguments, properties):
    require(isinstance(arguments, dict), "INVALID_ARGUMENTS", "引数はオブジェクトにしてください", 400)
    require(set(arguments) == set(properties), "INVALID_ARGUMENTS", "必要な引数と許可された引数が一致しません", 400)
    for key, rule in properties.items():
        value = arguments[key]
        typ = rule["type"]
        valid = (typ == "string" and isinstance(value, str) or
                 typ == "integer" and type(value) is int or
                 typ == "number" and type(value) in (int, float) and math.isfinite(value))
        require(valid, "INVALID_ARGUMENTS", key + " の型が不正です", 400)
        if "enum" in rule:
            require(value in rule["enum"], "INVALID_ARGUMENTS", key + " の値が不正です", 400)
        if "minimum" in rule:
            require(rule["minimum"] <= value <= rule["maximum"], "SAFETY_LIMIT", key + " が許可範囲外です", 400)


def authorize(principal, action, target, arguments, state):
    require(principal["role"] in ("agent", "supervisor", "planner"), "FORBIDDEN", "参照専用の主体です", 403)
    require(action in ACTIONS, "ACTION_DENIED", "公開されていない操作です。PLC直接操作・品質判定の上書きは許可されません", 403)
    spec = ACTIONS[action]
    if principal["role"] != "agent":
        expected = "planner" if spec["level"] == 4 else "supervisor"
        require(principal["role"] == expected, "FORBIDDEN", "操作の担当ロールが異なります", 403)
    validate_args(arguments, spec["properties"])
    if spec["target"] == "order":
        require(target in state["orders"], "NOT_FOUND", "製造指図が存在しません", 404)
    else:
        require(target == spec["target"], "INVALID_TARGET", "操作対象が不正です", 400)
    if action == "mes.resume_line":
        require(not state["stations"][2]["fault"], "INTERLOCK", "回転子工程の異常を保全してから再開してください")
    if action in ("mes.set_torque", "mes.maintenance"):
        require(not state["running"], "INTERLOCK", "レシピ変更・保全にはラインの一時停止が必要です")
    if action == "erp.release_order":
        require(state["orders"][target]["status"] == "planned", "ORDER_STATE", "未リリースの指図だけが対象です")
    return spec
