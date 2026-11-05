"""Unit tests for intune/intune_policy.py (no real Graph calls)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "intune"))
import intune_policy  # noqa: E402


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


class FakeSession:
    """Records Graph calls and answers with the queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url, json))
        return self.responses.pop(0)


class IntunePolicyTests(unittest.TestCase):

    def test_export_drops_ids_dates_and_nulls(self):
        live = {"@odata.context": "x", "@odata.type": "#microsoft.graph.windows10CompliancePolicy",
                "id": "p1", "version": 3, "createdDateTime": "2026-11-04",
                "displayName": "cp-kh-windows-baseline", "activeFirewallRequired": True,
                "passwordMinimumLength": None}
        cleaned = intune_policy.clean_policy(live)
        self.assertEqual(cleaned, {
            "@odata.type": "#microsoft.graph.windows10CompliancePolicy",
            "displayName": "cp-kh-windows-baseline", "activeFirewallRequired": True})

    def test_apply_updates_and_assigns_group_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_file = Path(tmp) / "policy.json"
            policy_file.write_text(json.dumps({
                "@odata.type": "#microsoft.graph.windows10CompliancePolicy",
                "displayName": "cp-kh-windows-baseline"}))
            settings = {"KH_ASSIGN_GROUP_ID": "group-1", "KH_POLICY_FILE": policy_file}
            session = FakeSession(
                FakeResponse(200, {"value": [{"id": "p1", "displayName": "cp-kh-windows-baseline"}]}),
                FakeResponse(204),   # PATCH policy
                FakeResponse(200),   # POST assign
                FakeResponse(200, {"value": []}))  # GET assignments
            with mock.patch.object(intune_policy, "make_session", return_value=session), \
                    mock.patch("builtins.print"):
                intune_policy.do_apply(settings, dry_run=False)

        self.assertEqual([c[0] for c in session.calls], ["GET", "PATCH", "POST", "GET"])
        targets = session.calls[2][2]["assignments"]
        self.assertEqual(len(targets), 1)  # one include, no exclusions
        self.assertEqual(targets[0]["target"]["groupId"], "group-1")
        self.assertEqual(targets[0]["target"]["@odata.type"],
                         "#microsoft.graph.groupAssignmentTarget")

    def test_429_is_retried(self):
        session = FakeSession(FakeResponse(429, headers={"Retry-After": "1"}),
                              FakeResponse(200, {"value": []}))
        with mock.patch.object(intune_policy.time, "sleep"):
            result = intune_policy.graph_request(session, "GET", "https://graph/x")
        self.assertEqual(result, {"value": []})
        self.assertEqual(len(session.calls), 2)

    def test_403_stops_with_config_error(self):
        session = FakeSession(FakeResponse(403, {"error": {"message": "Forbidden"}}))
        with self.assertRaises(intune_policy.GraphError) as caught:
            intune_policy.graph_request(session, "GET", "https://graph/x")
        self.assertEqual(caught.exception.exit_code, intune_policy.EXIT_CONFIG)
        self.assertIn("permission missing", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
