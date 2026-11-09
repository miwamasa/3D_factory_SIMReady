"""Deterministic discrete-time line and transactional approval gate (stdlib only)."""
import copy
import hashlib
import json
import random
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from policy import ACTIONS, POLICY_VERSION, LabError, authorize, require


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def uid():
    return uuid.uuid4().hex


def seed_state(seed=42):
    stations = []
    names = ["部材供給", "ステータ巻線", "回転子・軸受", "ハウジング組付", "ボルト締付", "EOL検査"]
    cycles = [12, 24, 18, 20, 16, 26]
    for i, (name, cycle) in enumerate(zip(names, cycles)):
        stations.append({"id": "S%02d" % (i + 1), "name": name, "cycle": cycle,
                         "queue": [], "fault": None, "temperature": 32.0,
                         "busy_seconds": 0, "completed": 0, "power_kw": [0.3, 2.4, 1.8, 0.8, 0.6, 1.2][i]})
    return {"seed": seed, "tick": 0, "running": True, "speed": 5, "clock_running": True,
            "line_revision": 1, "inventory_revision": 1, "safety_epoch": 1,
            "torque_nm": 4.2, "stations": stations, "next_serial": 1,
            "orders": {"WO-2601": {"id": "WO-2601", "model": "M-100", "quantity": 80,
                       "started": 0, "good": 0, "reject": 0, "priority": 3, "status": "released", "revision": 1},
                       "WO-2602": {"id": "WO-2602", "model": "M-100", "quantity": 60,
                       "started": 0, "good": 0, "reject": 0, "priority": 2, "status": "planned", "revision": 1}},
            "inventory": 160, "deliveries": [], "good": 0, "reject": 0, "energy_kwh": 0,
            "running_seconds": 0, "alarms": [], "history": [], "quality": [], "scenario": "normal"}


class Factory:
    def __init__(self, db_path=":memory:", clock=time.time):
        self.lock = threading.RLock()
        self.clock = clock
        self.db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, principal TEXT NOT NULL,
          data TEXT NOT NULL, status TEXT NOT NULL, expires REAL NOT NULL, result TEXT);
        CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, proposal TEXT UNIQUE NOT NULL,
          approver TEXT NOT NULL, role TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS executions(principal TEXT NOT NULL, key TEXT NOT NULL,
          proposal TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(principal,key));
        CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
          tick INTEGER NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, data TEXT NOT NULL);
        """)
        if not self.db.execute("SELECT 1 FROM state").fetchone():
            self.db.execute("INSERT INTO state VALUES(1,?)", (canonical(seed_state()),))

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def state(self):
        return json.loads(self.db.execute("SELECT data FROM state WHERE id=1").fetchone()[0])

    def save(self, state):
        self.db.execute("UPDATE state SET data=? WHERE id=1", (canonical(state),))

    def event(self, state, actor, event, data):
        self.db.execute("INSERT INTO audit(at,tick,actor,event,data) VALUES(?,?,?,?,?)",
                        (self.clock(), state["tick"], actor, event, canonical(data)))

    def log_denial(self, principal, operation, err):
        with self.transaction():
            self.event(self.state(), principal["id"], "denied", {"operation": operation, "code": err.code})

    def snapshot(self):
        with self.lock:
            s = self.state()
            total = s["good"] + s["reject"]
            availability = s["running_seconds"] / max(1, s["tick"])
            # Educational estimate: ideal line takt=26s, ignore line fill for performance.
            performance = min(1.0, total * 26 / max(1, s["running_seconds"]))
            quality = s["good"] / total if total else 1.0
            baseline = None
            if s["tick"] > 300:
                cutoff = s["tick"] - 300
                baseline = next((point for point in s["history"] if point["tick"] >= cutoff), None)
            output_window = s["tick"] - (baseline["tick"] if baseline else 0)
            window_output = total - (baseline["good"] + baseline["reject"] if baseline else 0)
            lead_times = [unit["lead_time_s"] for unit in s["quality"][-20:] if unit.get("lead_time_s") is not None]
            s["kpi"] = {"availability": round(availability, 4), "performance": round(performance, 4),
                        "quality": round(quality, 4), "oee_estimate": round(availability * performance * quality, 4),
                        "wip": sum(len(x["queue"]) for x in s["stations"]),
                        "output_rate_per_hour": round(window_output * 3600 / max(1, output_window), 1),
                        "output_window_s": output_window,
                        "lead_time_avg_s": round(sum(lead_times) / len(lead_times), 1) if lead_times else None,
                        "lead_time_sample_count": len(lead_times),
                        "throughput_per_hour": round(s["good"] * 3600 / max(1, s["tick"]), 1)}
            s["policy_version"] = POLICY_VERSION
            s["server_time"] = self.clock()
            return s

    def _alarm(self, s, code, station, message):
        if not any(a["code"] == code and a["active"] for a in s["alarms"]):
            s["alarms"].append({"id": uid(), "code": code, "station": station,
                                  "message": message, "active": True, "tick": s["tick"]})
            s["safety_epoch"] += 1
            self.event(s, "simulator", "alarm.raised", {"code": code, "station": station})

    def advance(self, seconds):
        require(type(seconds) is int and 1 <= seconds <= 3600, "INVALID_ARGUMENTS", "stepは1〜3600秒です", 400)
        with self.transaction():
            s = self.state()
            for _ in range(seconds):
                self._tick(s)
            self.save(s)
        return self.snapshot()

    def _tick(self, s):
        s["tick"] += 1
        for d in s["deliveries"][:]:
            if d["due_tick"] <= s["tick"]:
                s["inventory"] += d["quantity"]
                s["inventory_revision"] += 1
                s["deliveries"].remove(d)
                self.event(s, "simulator", "material.received", d)
                for a in s["alarms"]:
                    if a["code"] == "MATERIAL_LOW":
                        a["active"] = False
        if s["running"]:
            s["running_seconds"] += 1
            for i in range(5, -1, -1):
                station = s["stations"][i]
                station["temperature"] = round(min(95, station["temperature"] + (0.07 if station["fault"] else -0.015)), 2)
                station["temperature"] = max(32, station["temperature"])
                if not station["queue"] or station["fault"]:
                    continue
                unit = station["queue"][0]
                unit["elapsed"] = min(station["cycle"], unit["elapsed"] + 1)
                station["busy_seconds"] += 1
                s["energy_kwh"] += station["power_kw"] / 3600
                if unit["elapsed"] < station["cycle"]:
                    continue
                if i < 5 and len(s["stations"][i + 1]["queue"]) >= 3:
                    continue
                station["queue"].pop(0)
                station["completed"] += 1
                if i == 4:
                    noise = random.Random(s["seed"] + unit["serial"] * 13).uniform(-0.12, 0.12)
                    unit["torque"] = round(s["torque_nm"] + noise + (0.9 if s["scenario"] == "torque_drift" else 0), 3)
                if i < 5:
                    unit["elapsed"] = 0
                    s["stations"][i + 1]["queue"].append(unit)
                else:
                    # Physical product nominal stays 4.2; changing recipe does not change acceptance criteria.
                    bad_torque = abs(unit.get("torque", 4.2) - 4.2) > 0.35
                    base_defect = random.Random(s["seed"] + unit["serial"] * 37).random() < 0.025
                    reject = bad_torque or base_defect
                    key = "reject" if reject else "good"
                    s[key] += 1
                    order = s["orders"][unit["order"]]
                    order[key] += 1
                    if order["good"] + order["reject"] >= order["quantity"]:
                        order["status"] = "completed"
                        order["revision"] += 1
                    entered_tick = unit.get("entered_tick")
                    lead_time = s["tick"] - entered_tick if entered_tick is not None else None
                    s["quality"].append({"serial": "M%05d" % unit["serial"], "order": unit["order"],
                                         "torque_nm": unit.get("torque"), "result": "NG" if reject else "OK",
                                         "tick": s["tick"], "lead_time_s": lead_time})
                    s["quality"] = s["quality"][-100:]
                    if bad_torque:
                        self._alarm(s, "TORQUE_NG", "S05", "締付トルクが製品検査範囲を逸脱")
            orders = sorted((o for o in s["orders"].values() if o["status"] == "released" and o["started"] < o["quantity"]),
                            key=lambda o: (-o["priority"], o["id"]))
            if orders and len(s["stations"][0]["queue"]) < 3:
                if s["inventory"] > 0:
                    order = orders[0]
                    s["stations"][0]["queue"].append({"serial": s["next_serial"], "order": order["id"],
                                                       "elapsed": 0, "entered_tick": s["tick"]})
                    s["next_serial"] += 1
                    s["inventory"] -= 1
                    order["started"] += 1
                else:
                    self._alarm(s, "MATERIAL_LOW", "S01", "モーターキットが欠品。ERP補充を手配してください")
        if s["tick"] % 10 == 0:
            s["history"].append({"tick": s["tick"], "good": s["good"], "reject": s["reject"],
                                  "wip": sum(len(x["queue"]) for x in s["stations"])})
            s["history"] = s["history"][-120:]

    def context(self, s, action, target):
        # Telemetry ticks do not invalidate a proposal. Configuration, alarms and order transitions do.
        if action.startswith("mes."):
            return {"line_revision": s["line_revision"], "safety_epoch": s["safety_epoch"]}
        if action == "erp.replenish":
            return {"inventory_revision": s["inventory_revision"]}
        return {"order_revision": s["orders"][target]["revision"]}

    def preflight(self, principal, action, target, arguments):
        with self.transaction():
            s = self.state()
            spec = authorize(principal, action, target, arguments, s)
            p = {"id": uid(), "principal": principal["id"], "action": action, "target": target,
                 "arguments": copy.deepcopy(arguments), "context": self.context(s, action, target),
                 "policy_version": POLICY_VERSION, "required_role": spec["approval"],
                 "created_at": self.clock(), "expires_at": self.clock() + 300,
                 "impact": self.impact(s, action, target, arguments)}
            p["digest"] = hashlib.sha256(canonical(p).encode()).hexdigest()
            self.db.execute("INSERT INTO proposals(id,principal,data,status,expires) VALUES(?,?,?,?,?)",
                            (p["id"], principal["id"], canonical(p), "pending" if spec["approval"] else "ready", p["expires_at"]))
            self.event(s, principal["id"], "preflight", {"proposal_id": p["id"], "action": action, "digest": p["digest"]})
            return {"ok": True, "proposal": p, "status": "approval_required" if spec["approval"] else "ready"}

    def impact(self, s, action, target, args):
        if action == "mes.set_torque":
            return {"before": s["torque_nm"], "after": args["torque_nm"], "unit": "N·m",
                    "note": "以後の締付に適用。製品検査基準4.2±0.35 N·mは固定。既存NG品は解除しません。"}
        if action == "mes.maintenance":
            return {"station": args["station_id"], "note": "仮想保全として故障・ドリフトを即時除去。停止を維持。"}
        if action == "erp.replenish":
            return {"quantity": args["quantity"], "lead_time_sim_seconds": 60, "cost_jpy": args["quantity"] * 3200}
        if action == "erp.set_priority":
            return {"before": s["orders"][target]["priority"], "after": args["priority"], "note": "次回投入時から指図を選択。工程内品は移動しません。"}
        return {"note": ACTIONS[action]["label"], "target": target}

    def proposals(self, principal):
        with self.lock:
            rows = self.db.execute("SELECT * FROM proposals ORDER BY rowid DESC LIMIT 100").fetchall()
            result = []
            for row in rows:
                p = json.loads(row["data"])
                if principal["role"] == "agent" and row["principal"] != principal["id"]:
                    continue
                status = row["status"]
                if status in ("pending", "approved", "ready") and self.clock() >= row["expires"]:
                    status = "expired"
                elif status in ("pending", "approved", "ready") and p["context"] != self.context(self.state(), p["action"], p["target"]):
                    status = "stale"
                decision = self.db.execute("SELECT * FROM decisions WHERE proposal=?", (p["id"],)).fetchone()
                p.update(status=status, decision=dict(decision) if decision else None)
                result.append(p)
            return result

    def decide(self, principal, proposal_id, decision, reason):
        require(principal["role"] in ("supervisor", "planner"), "FORBIDDEN", "人間の承認者資格が必要です", 403)
        require(decision in ("approve", "reject"), "INVALID_ARGUMENTS", "承認・却下を指定してください", 400)
        require(isinstance(reason, str) and 3 <= len(reason.strip()) <= 500, "INVALID_ARGUMENTS", "理由は3〜500文字です", 400)
        with self.transaction():
            row = self.db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            require(row is not None, "NOT_FOUND", "操作案が見つかりません", 404)
            p, s = json.loads(row["data"]), self.state()
            require(row["status"] == "pending", "ALREADY_DECIDED", "この操作案は判断済みです")
            require(p["principal"] != principal["id"], "SELF_APPROVAL", "提案者と承認者を分離してください", 403)
            require(p["required_role"] == principal["role"], "WRONG_APPROVER", "この操作は別の担当者による承認が必要です", 403)
            require(self.clock() < p["expires_at"], "EXPIRED", "操作案の有効期限が切れました")
            require(p["context"] == self.context(s, p["action"], p["target"]), "STALE", "状態が変わりました。再度preflightしてください")
            self.db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?)",
                            (uid(), proposal_id, principal["id"], principal["role"], decision, reason.strip(), self.clock()))
            status = "approved" if decision == "approve" else "rejected"
            self.db.execute("UPDATE proposals SET status=? WHERE id=?", (status, proposal_id))
            self.event(s, principal["id"], "approval." + status, {"proposal_id": proposal_id, "digest": p["digest"], "reason": reason.strip()})
            return {"ok": True, "status": status, "proposal_id": proposal_id}

    def execute(self, principal, proposal_id, idempotency_key):
        require(isinstance(idempotency_key, str) and 8 <= len(idempotency_key) <= 128,
                "INVALID_ARGUMENTS", "idempotency_keyは8〜128文字です", 400)
        with self.transaction():
            old = self.db.execute("SELECT * FROM executions WHERE principal=? AND key=?", (principal["id"], idempotency_key)).fetchone()
            if old:
                require(old["proposal"] == proposal_id, "IDEMPOTENCY_CONFLICT", "同じキーが別の操作に使われています")
                return dict(json.loads(old["result"]), replayed=True)
            row = self.db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            require(row is not None, "NOT_FOUND", "操作案が見つかりません", 404)
            p, s = json.loads(row["data"]), self.state()
            require(row["principal"] == principal["id"], "PRINCIPAL_MISMATCH", "提案した主体だけが実行できます", 403)
            require(row["status"] != "executed", "ALREADY_EXECUTED", "操作案は既に実行済みです")
            require(row["status"] in ("ready", "pending", "approved"), "PROPOSAL_CLOSED", "却下・無効化された操作案です")
            require(self.clock() < row["expires"], "EXPIRED", "操作案の有効期限が切れました")
            require(p["policy_version"] == POLICY_VERSION, "POLICY_CHANGED", "ポリシーが変わりました")
            authorize(principal, p["action"], p["target"], p["arguments"], s)
            require(p["context"] == self.context(s, p["action"], p["target"]), "STALE", "承認後に状態が変わりました。再度preflightしてください")
            if p["required_role"]:
                require(row["status"] == "approved", "APPROVAL_REQUIRED", "担当者の承認が必要です", 403)
                d = self.db.execute("SELECT * FROM decisions WHERE proposal=?", (proposal_id,)).fetchone()
                require(d is not None and d["decision"] == "approve" and d["role"] == p["required_role"]
                        and d["approver"] != principal["id"], "APPROVAL_REQUIRED", "有効な承認がありません", 403)
            self._apply(s, p)
            self.save(s)
            result = {"ok": True, "code": "EXECUTED", "proposal_id": proposal_id, "action": p["action"],
                      "target": p["target"], "digest": p["digest"], "tick": s["tick"], "replayed": False}
            self.db.execute("UPDATE proposals SET status='executed',result=? WHERE id=?", (canonical(result), proposal_id))
            self.db.execute("INSERT INTO executions VALUES(?,?,?,?)", (principal["id"], idempotency_key, proposal_id, canonical(result)))
            self.event(s, principal["id"], "executed", result)
            return result

    def _apply(self, s, p):
        action, args, target = p["action"], p["arguments"], p["target"]
        if action == "mes.pause_line":
            s["running"] = False
        elif action == "mes.resume_line":
            s["running"] = True
        elif action == "mes.set_torque":
            s["torque_nm"] = args["torque_nm"]
        elif action == "mes.maintenance":
            idx = 2 if args["station_id"] == "S03" else 4
            s["stations"][idx]["fault"] = None
            s["stations"][idx]["temperature"] = 32
            if idx == 4 and s["scenario"] == "torque_drift" or idx == 2 and s["scenario"] == "bearing_heat":
                s["scenario"] = "normal"
            for a in s["alarms"]:
                if a["station"] == args["station_id"]:
                    a["active"] = False
            s["safety_epoch"] += 1
        elif action == "erp.set_priority":
            s["orders"][target]["priority"] = args["priority"]
            s["orders"][target]["revision"] += 1
        elif action == "erp.release_order":
            s["orders"][target]["status"] = "released"
            s["orders"][target]["revision"] += 1
        elif action == "erp.replenish":
            s["deliveries"].append({"id": uid(), "quantity": args["quantity"], "due_tick": s["tick"] + 60})
            s["inventory_revision"] += 1
        if action.startswith("mes."):
            s["line_revision"] += 1

    def control(self, principal, operation, value=None):
        require(principal["role"] == "supervisor", "FORBIDDEN", "シナリオ設定は現場責任者専用です", 403)
        with self.transaction():
            s = self.state()
            if operation == "speed":
                require(type(value) is int and value in (1, 5, 20, 60), "INVALID_ARGUMENTS", "速度は1/5/20/60倍です", 400)
                s["speed"] = value
            elif operation == "clock":
                require(type(value) is bool, "INVALID_ARGUMENTS", "clockは真偽値です", 400)
                s["clock_running"] = value
            elif operation == "scenario":
                require(value in ("normal", "torque_drift", "bearing_heat", "material_shortage"), "INVALID_ARGUMENTS", "不明なシナリオです", 400)
                # Changing a scenario is a test fixture operation, separate from agent production tools.
                s["scenario"] = value
                s["safety_epoch"] += 1
                s["line_revision"] += 1
                s["stations"][2]["fault"] = None
                for a in s["alarms"]:
                    a["active"] = False
                if value == "torque_drift":
                    self._alarm(s, "TORQUE_DRIFT", "S05", "工具ドリフト +0.9 N·m。停止後にS05保全を推奨")
                elif value == "bearing_heat":
                    s["stations"][2]["fault"] = "BEARING_HEAT"
                    s["stations"][2]["temperature"] = 82
                    s["running"] = False
                    self._alarm(s, "BEARING_HEAT", "S03", "軸受温度異常：設備インターロックで停止")
                elif value == "material_shortage":
                    s["inventory"] = 0
                    s["inventory_revision"] += 1
                    self._alarm(s, "MATERIAL_LOW", "S01", "部材欠品。ERP補充を手配してください")
            elif operation == "reset":
                require(type(value) is int and 0 <= value <= 999999, "INVALID_ARGUMENTS", "seedは0〜999999の整数です", 400)
                s = seed_state(value)
                self.db.execute("UPDATE proposals SET status='invalidated' WHERE status IN ('pending','ready','approved')")
                # Executed handles stay consumed; history and approvals are retained for traceability.
            else:
                raise LabError("NOT_FOUND", "不明なシミュレータ操作です", 404)
            self.save(s)
            self.event(s, principal["id"], "simulator." + operation, {"value": value})
        return self.snapshot()

    def audit(self, limit=80):
        with self.lock:
            return [dict(r, data=json.loads(r["data"])) for r in self.db.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))]

    def call(self, principal, name, args):
        require(isinstance(args, dict), "INVALID_ARGUMENTS", "argumentsはオブジェクトです", 400)
        allowed = {"mes_get_snapshot": set(), "erp_get_orders": set(), "erp_get_inventory": set(),
                   "factory_get_policy": set(), "factory_get_audit": set(), "factory_list_proposals": set(),
                   "factory_preflight": {"action", "target", "arguments"},
                   "factory_execute": {"proposal_id", "idempotency_key"}}
        require(name in allowed, "UNKNOWN_TOOL", "不明なツールです", 404)
        require(set(args) == allowed[name], "INVALID_ARGUMENTS", "ツールの引数が一致しません", 400)
        if name == "mes_get_snapshot":
            return self.snapshot()
        if name == "erp_get_orders":
            return {"orders": list(self.snapshot()["orders"].values())}
        if name == "erp_get_inventory":
            s = self.snapshot()
            return {"material": "MOTOR-KIT", "available": s["inventory"], "deliveries": s["deliveries"]}
        if name == "factory_get_policy":
            return {"version": POLICY_VERSION, "actions": ACTIONS, "proposal_ttl_seconds": 300,
                    "forbidden": ["PLC direct write", "quality override", "agent self approval"]}
        if name == "factory_get_audit":
            return {"events": self.audit()}
        if name == "factory_list_proposals":
            return {"proposals": self.proposals(principal)}
        require(principal["role"] != "viewer", "FORBIDDEN", "参照専用の主体です", 403)
        if name == "factory_preflight":
            require(isinstance(args["action"], str) and isinstance(args["target"], str), "INVALID_ARGUMENTS", "action/targetは文字列です", 400)
            return self.preflight(principal, args["action"], args["target"], args["arguments"])
        require(isinstance(args["proposal_id"], str), "INVALID_ARGUMENTS", "proposal_idは文字列です", 400)
        return self.execute(principal, args["proposal_id"], args["idempotency_key"])
