import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Factory, canonical
from mcp_protocol import VERSION, META_PREFIX
from policy import LabError
from server import make_handler, ThreadingHTTPServer

AGENT = {"id": "agent-01", "role": "agent"}
OTHER = {"id": "agent-02", "role": "agent"}
SUP = {"id": "supervisor-01", "role": "supervisor"}
PLAN = {"id": "planner-01", "role": "planner"}
VIEWER = {"id": "viewer-01", "role": "viewer"}


class GateTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.f = Factory(clock=lambda: self.now[0])

    def tearDown(self):
        self.f.db.close()

    def proposal(self, action="mes.resume_line", target="LINE-01", args=None, principal=AGENT):
        return self.f.preflight(principal, action, target, args or {})["proposal"]

    def assertCode(self, code, fn):
        with self.assertRaises(LabError) as cm:
            fn()
        self.assertEqual(cm.exception.code, code)

    def pause(self):
        p = self.proposal("mes.pause_line")
        self.f.execute(AGENT, p["id"], "pause-" + p["id"])

    def test_approval_required_and_agent_cannot_approve(self):
        self.pause()
        p = self.proposal()
        self.assertCode("APPROVAL_REQUIRED", lambda: self.f.execute(AGENT, p["id"], "execute-001"))
        self.assertCode("FORBIDDEN", lambda: self.f.decide(AGENT, p["id"], "approve", "同意しました"))
        self.assertCode("WRONG_APPROVER", lambda: self.f.decide(PLAN, p["id"], "approve", "同意しました"))
        self.f.decide(SUP, p["id"], "approve", "現場の安全確認済み")
        self.f.execute(AGENT, p["id"], "execute-001")
        self.assertTrue(self.f.snapshot()["running"])

    def test_separation_of_duties_even_for_human(self):
        p = self.proposal(principal=SUP)
        self.assertCode("SELF_APPROVAL", lambda: self.f.decide(SUP, p["id"], "approve", "自己承認を試す"))

    def test_idempotency_and_one_time_approval(self):
        p = self.proposal("erp.replenish", "MOTOR-KIT", {"quantity": 20})
        self.f.decide(PLAN, p["id"], "approve", "計画を確認しました")
        result = self.f.execute(AGENT, p["id"], "supply-0001")
        replay = self.f.execute(AGENT, p["id"], "supply-0001")
        self.assertFalse(result["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.f.snapshot()["deliveries"]), 1)
        self.assertCode("ALREADY_EXECUTED", lambda: self.f.execute(AGENT, p["id"], "different-key"))
        p2 = self.proposal("erp.replenish", "MOTOR-KIT", {"quantity": 30})
        self.assertCode("IDEMPOTENCY_CONFLICT", lambda: self.f.execute(AGENT, p2["id"], "supply-0001"))

    def test_concurrent_execute_commits_once(self):
        p = self.proposal("erp.replenish", "MOTOR-KIT", {"quantity": 20})
        self.f.decide(PLAN, p["id"], "approve", "計画を確認しました")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.f.execute(AGENT, p["id"], "same-key-001"), range(8)))
        self.assertEqual(sum(not r["replayed"] for r in results), 1)
        self.assertEqual(len(self.f.snapshot()["deliveries"]), 1)

    def test_expiration_uses_wall_clock_not_simulation(self):
        p = self.proposal()
        self.f.decide(SUP, p["id"], "approve", "現場を確認しました")
        self.f.advance(600)
        self.assertNotEqual(self.f.proposals(AGENT)[0]["status"], "expired")
        self.now[0] += 300
        self.assertCode("EXPIRED", lambda: self.f.execute(AGENT, p["id"], "expired-key"))

    def test_state_change_invalidates_approval(self):
        self.pause()
        p = self.proposal()
        self.f.decide(SUP, p["id"], "approve", "現場を確認しました")
        self.f.control(SUP, "scenario", "torque_drift")
        self.assertCode("STALE", lambda: self.f.execute(AGENT, p["id"], "stale-key01"))
        self.assertFalse(self.f.snapshot()["running"])

    def test_telemetry_does_not_stale_configuration(self):
        p = self.proposal("erp.set_priority", "WO-2602", {"priority": 5})
        self.f.decide(PLAN, p["id"], "approve", "計画を確認しました")
        self.f.advance(100)
        self.f.execute(AGENT, p["id"], "priority-01")
        self.assertEqual(self.f.snapshot()["orders"]["WO-2602"]["priority"], 5)

    def test_principal_cannot_take_over_proposal(self):
        p = self.proposal("mes.pause_line")
        self.assertCode("PRINCIPAL_MISMATCH", lambda: self.f.execute(OTHER, p["id"], "takeover-01"))

    def test_rejection_and_reset_invalidate(self):
        p = self.proposal()
        self.f.decide(SUP, p["id"], "reject", "まだ安全確認が不十分")
        self.assertCode("PROPOSAL_CLOSED", lambda: self.f.execute(AGENT, p["id"], "rejected-01"))
        p2 = self.proposal("mes.pause_line")
        self.f.control(SUP, "reset", 42)
        self.assertCode("PROPOSAL_CLOSED", lambda: self.f.execute(AGENT, p2["id"], "reset-key01"))

    def test_no_direct_plc_or_quality_override(self):
        for action in ("plc.write", "mes.release_ng", "factory.approve", "unknown"):
            self.assertCode("ACTION_DENIED", lambda: self.proposal(action))
        self.assertCode("FORBIDDEN", lambda: self.proposal("mes.pause_line", principal=VIEWER))

    def test_validation_and_interlocks(self):
        self.assertCode("INTERLOCK", lambda: self.proposal("mes.set_torque", args={"torque_nm": 4.2}))
        self.pause()
        for value in (9.9, float('nan'), True, "4.2"):
            with self.assertRaises(LabError):
                self.proposal("mes.set_torque", args={"torque_nm": value})
        self.assertCode("INVALID_ARGUMENTS", lambda: self.proposal("mes.set_torque", args={"torque_nm": 4.2, "approved": True}))
        self.f.control(SUP, "scenario", "bearing_heat")
        self.assertCode("INTERLOCK", lambda: self.proposal())
        self.assertCode("FORBIDDEN", lambda: self.f.control(AGENT, "reset", 42))

    def test_fixed_quality_criteria_and_maintenance(self):
        self.f.control(SUP, "scenario", "torque_drift")
        self.f.advance(500)
        self.assertGreater(self.f.snapshot()["reject"], 0)
        self.pause()
        p = self.proposal("mes.maintenance", args={"station_id": "S05"})
        self.f.decide(SUP, p["id"], "approve", "工具校正を確認しました")
        self.f.execute(AGENT, p["id"], "maintain-01")
        self.assertEqual(self.f.snapshot()["scenario"], "normal")
        self.assertFalse(self.f.snapshot()["running"])

    def test_determinism_and_material_balance(self):
        other = Factory()
        try:
            a, b = self.f.advance(1000), other.advance(1000)
            for s in (a, b):
                s.pop("server_time")
            self.assertEqual(a, b)
            self.assertEqual(a["good"] + a["reject"] + a["kpi"]["wip"], a["next_serial"] - 1)
            self.assertEqual(a["inventory"] + a["next_serial"] - 1, 160)
            self.assertTrue(all(len(st["queue"]) <= 3 for st in a["stations"]))
        finally:
            other.db.close()

    def test_restart_keeps_approval_and_replay_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "factory.sqlite")
            f = Factory(path)
            p = f.preflight(AGENT, "mes.resume_line", "LINE-01", {})["proposal"]
            f.decide(SUP, p["id"], "approve", "現場を確認しました")
            f.db.close()
            f = Factory(path)
            f.execute(AGENT, p["id"], "restart-001")
            f.db.close()
            f = Factory(path)
            self.assertTrue(f.execute(AGENT, p["id"], "restart-001")["replayed"])
            f.db.close()


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Factory()
        cls.credentials = {p["role"]: dict(p, token=p["role"] + "-test-token-" + "x"*40) for p in (AGENT, SUP, PLAN, VIEWER)}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args: None)
        cls.port = cls.server.server_address[1]
        cls.server.RequestHandlerClass = make_handler(cls.f, cls.credentials, True, cls.port)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()
        cls.f.db.close()

    def request(self, path, data=None, role="agent", extra=None):
        headers = {"Content-Type": "application/json"}
        if role:
            headers["Authorization"] = "Bearer " + self.credentials[role]["token"]
        headers.update(extra or {})
        req = urllib.request.Request(self.base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as err:
            return err.code, json.load(err)

    def mcp(self, method="tools/list", params=None, extra=None, role="agent"):
        params = dict(params or {})
        params["_meta"] = {META_PREFIX+"protocolVersion": VERSION, META_PREFIX+"clientCapabilities": {}}
        headers = {"MCP-Protocol-Version": VERSION, "Mcp-Method": method, "Accept": "application/json, text/event-stream"}
        if "name" in params:
            headers["Mcp-Name"] = params["name"]
        headers.update(extra or {})
        return self.request("/mcp", {"jsonrpc":"2.0", "id":1, "method":method, "params":params}, role, headers)

    def test_auth_is_per_request(self):
        self.assertEqual(self.request("/api/snapshot", role=None)[0], 401)
        self.assertEqual(self.request("/api/snapshot")[0], 200)
        self.assertEqual(self.request("/api/snapshot", role=None)[0], 401)

    def test_origin_and_host_rebinding(self):
        self.assertEqual(self.request("/api/snapshot", extra={"Origin":"https://evil.invalid"})[0], 403)
        self.assertEqual(self.request("/api/snapshot", extra={"Host":"evil.invalid"})[0], 403)

    def test_agent_cannot_use_human_or_fixture_endpoints(self):
        self.assertEqual(self.request("/api/approval", {"proposal_id":"unknown","decision":"approve","reason":"自己申告します"})[0], 403)
        self.assertEqual(self.request("/api/control", {"operation":"scenario","value":"normal"})[0], 403)

    def test_tampered_execute_arguments_rejected(self):
        status, value = self.request("/api/tool", {"name":"factory_execute", "arguments":{"proposal_id":"x", "idempotency_key":"test-key-1", "approved":True}})
        self.assertEqual((status, value["code"]), (400,"INVALID_ARGUMENTS"))

    def test_mcp_discovery_and_tool_call(self):
        status, value = self.mcp("server/discover")
        self.assertEqual(status, 200)
        self.assertEqual(value["result"]["supportedVersions"], [VERSION])
        status, value = self.mcp()
        names = [t["name"] for t in value["result"]["tools"]]
        self.assertEqual(names, sorted(names))
        self.assertNotIn("factory_approve", names)
        status, value = self.mcp("tools/call", {"name":"mes_get_snapshot", "arguments":{}})
        self.assertFalse(value["result"]["isError"])
        self.assertEqual(value["result"]["resultType"], "complete")

    def test_mcp_body_header_mismatch_rejected(self):
        status, value = self.mcp("tools/call", {"name":"factory_execute","arguments":{}}, {"Mcp-Name":"mes_get_snapshot"})
        self.assertEqual((status, value["error"]["code"]), (400,-32020))

    def test_unknown_mcp_method_and_business_errors(self):
        status, value = self.mcp("factory/approve")
        self.assertEqual((status,value["error"]["code"]), (404,-32601))
        status, value = self.mcp("tools/call", {"name":"factory_execute","arguments":{"proposal_id":"x","idempotency_key":"missing-id-1"}})
        self.assertEqual(status, 200)
        self.assertTrue(value["result"]["isError"])

    def test_stdio_legacy_bridge(self):
        source = '\n'.join(json.dumps(p) for p in [
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1"}}},
            {"jsonrpc":"2.0","method":"notifications/initialized"},
            {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"mes_get_snapshot","arguments":{}}}])+'\n'
        env = dict(os.environ, FACTORY_AGENT_TOKEN=self.credentials["agent"]["token"], FACTORY_URL=self.base)
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/"stdio_bridge.py")],
                                input=source, text=True, capture_output=True, env=env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("tick", replies[1]["result"]["structuredContent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
