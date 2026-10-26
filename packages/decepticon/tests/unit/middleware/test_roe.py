"""Tests for the RoE guardrail middleware + machine-readable schema."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage

from decepticon.middleware import roe as roe_mod
from decepticon.middleware._audit_sink import RoEAuditSink, verify_ledger
from decepticon.middleware._command_targets import extract_targets
from decepticon.middleware.roe import (
    RoEGuardrailMiddleware,
    _load_rules_for_workspace,
    _redact_secrets,
    _to_text,
    build_default_sink,
)
from decepticon_core.types.roe import (
    MachineEnforcement,
    evaluate_command,
    evaluate_target,
    evaluate_time_window,
)


class TestMachineEnforcementSchema:
    def test_empty_dict_defaults_to_audit(self) -> None:
        rules = MachineEnforcement.from_dict({})
        assert rules.in_scope == ()
        assert rules.out_of_scope == ()

    def test_none_parses_to_empty_scope(self) -> None:
        rules = MachineEnforcement.from_dict(None)
        assert rules.in_scope == ()
        assert rules.out_of_scope == ()

    def test_string_rules_parsed(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"in_scope": ["10.0.0.0/24", "*.acme.com", "single-host.example"]}
        )
        assert len(rules.in_scope) == 3
        kinds = [r.resolved_kind() for r in rules.in_scope]
        assert "cidr" in kinds
        assert "domain-glob" in kinds
        assert "host" in kinds

    def test_dict_rules_parsed_with_type(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"in_scope": [{"target": "10.0.0.0/24", "type": "ip-range"}]}
        )
        assert rules.in_scope[0].pattern == "10.0.0.0/24"

    def test_legacy_mode_is_ignored(self) -> None:
        legacy = MachineEnforcement.from_dict({"in_scope": ["example.com"]})
        current = MachineEnforcement.from_dict({"in_scope": ["example.com"]})
        assert legacy == current

    def test_cloud_metadata_denied_by_default(self) -> None:
        rules = MachineEnforcement.from_dict({"in_scope": ["10.0.0.0/8"]})
        decision = evaluate_target("169.254.169.254", rules)
        assert not decision.allow
        assert decision.reason_code == "FORBIDDEN_DESTINATION"

    def test_cloud_metadata_allowable_when_opted_in(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"in_scope": ["169.254.0.0/16"], "allow_cloud_metadata": True}
        )
        decision = evaluate_target("169.254.169.254", rules)
        assert decision.allow


class TestSensitiveTldGuardrail:
    @pytest.mark.parametrize("host", ["whitehouse.gov", "ARMY.MIL", "mit.edu", "icann.int"])
    def test_sensitive_tlds_denied_by_default(self, host: str) -> None:
        rules = MachineEnforcement.from_dict({"in_scope": [host]})
        decision = evaluate_target(host, rules)
        assert not decision.allow
        assert decision.reason_code == "SENSITIVE_TLD"

    def test_subdomain_of_sensitive_tld_denied(self) -> None:
        rules = MachineEnforcement.from_dict({"in_scope": ["*.mit.edu"]})
        assert not evaluate_target("portal.mit.edu", rules).allow

    def test_sensitive_tld_with_port_denied(self) -> None:
        assert not evaluate_target("foo.gov:8443", MachineEnforcement()).allow

    def test_sensitive_tld_allowed_when_opted_in(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"in_scope": ["mit.edu"], "allow_sensitive_tlds": True}
        )
        decision = evaluate_target("mit.edu", rules)
        assert decision.allow
        assert decision.reason_code == "IN_SCOPE"

    def test_block_precedes_in_scope_match(self) -> None:
        # Even an explicit in-scope entry does not override the default-deny;
        # the operator must opt in via allow_sensitive_tlds.
        rules = MachineEnforcement.from_dict({"in_scope": ["target.gov"]})
        assert evaluate_target("target.gov", rules).reason_code == "SENSITIVE_TLD"

    def test_commercial_tld_unaffected(self) -> None:
        rules = MachineEnforcement.from_dict({"in_scope": ["acme.com"]})
        assert evaluate_target("acme.com", rules).allow

    def test_ip_literal_never_matches_tld(self) -> None:
        # IPs have no TLD; the guardrail must not touch them.
        assert evaluate_target("8.8.8.8", MachineEnforcement()).allow

    def test_substring_not_suffix_is_not_matched(self) -> None:
        # ``.international`` is not ``.int``; only a full trailing label counts.
        rules = MachineEnforcement.from_dict({"in_scope": ["foo.international.com"]})
        assert evaluate_target("foo.international.com", rules).allow


class TestEvaluateTarget:
    def test_no_in_scope_means_allow_with_out_of_scope_only(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"out_of_scope": ["10.99.0.0/16"], "allow_cloud_metadata": True}
        )
        assert evaluate_target("8.8.8.8", rules).allow
        assert not evaluate_target("10.99.1.1", rules).allow

    def test_in_scope_required_when_set(self) -> None:
        rules = MachineEnforcement.from_dict({"in_scope": ["10.0.0.0/24"]})
        assert evaluate_target("10.0.0.5", rules).allow
        assert not evaluate_target("8.8.8.8", rules).allow

    def test_out_of_scope_precedes_in_scope(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"in_scope": ["10.0.0.0/24"], "out_of_scope": ["10.0.0.5"]}
        )
        assert not evaluate_target("10.0.0.5", rules).allow
        assert evaluate_target("10.0.0.6", rules).allow

    def test_domain_glob_match(self) -> None:
        rules = MachineEnforcement.from_dict({"in_scope": ["*.acme.com"]})
        assert evaluate_target("api.acme.com", rules).allow
        assert evaluate_target("www.acme.com", rules).allow
        assert not evaluate_target("partner.acme-evil.com", rules).allow
        assert not evaluate_target("evilcorp.com", rules).allow

    def test_empty_target_allowed(self) -> None:
        assert evaluate_target("", MachineEnforcement()).allow


class TestEvaluateCommand:
    def test_no_patterns_allow(self) -> None:
        assert evaluate_command("nmap 10.0.0.1", MachineEnforcement()).allow

    def test_forbidden_pattern_blocks(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"forbidden_command_patterns": [r"(?i)\brm\s+-rf\s+/(?!tmp)"]}
        )
        d = evaluate_command("rm -rf /etc", rules)
        assert not d.allow
        assert d.reason_code == "FORBIDDEN_COMMAND"

    def test_invalid_regex_skipped(self) -> None:
        rules = MachineEnforcement.from_dict({"forbidden_command_patterns": ["[unclosed"]})
        assert evaluate_command("rm -rf /etc", rules).allow


class TestTimeWindowSchema:
    def test_no_windows_by_default(self) -> None:
        rules = MachineEnforcement.from_dict({})
        assert rules.authorized_windows == ()
        assert rules.blackout_windows == ()

    def test_authorized_windows_parsed_from_pairs(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"authorized_windows": [["2026-06-01T09:00:00+00:00", "2026-06-01T18:00:00+00:00"]]}
        )
        assert len(rules.authorized_windows) == 1
        start, end = rules.authorized_windows[0]
        assert start == datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)

    def test_dict_windows_parsed(self) -> None:
        rules = MachineEnforcement.from_dict(
            {
                "blackout_windows": [
                    {"start": "2026-06-01T22:00:00+00:00", "end": "2026-06-02T06:00:00+00:00"}
                ]
            }
        )
        assert len(rules.blackout_windows) == 1

    def test_bad_values_skipped(self) -> None:
        rules = MachineEnforcement.from_dict(
            {
                "authorized_windows": [
                    ["not-a-date", "2026-06-01T18:00:00+00:00"],
                    ["2026-06-01T18:00:00+00:00", "2026-06-01T09:00:00+00:00"],
                    "Mon-Fri 09:00-18:00 KST",
                    ["2026-06-01T09:00:00+00:00", "2026-06-01T18:00:00+00:00"],
                ]
            }
        )
        assert len(rules.authorized_windows) == 1


class TestEvaluateTimeWindow:
    AUTH = {"authorized_windows": [["2026-06-01T09:00:00+00:00", "2026-06-01T18:00:00+00:00"]]}
    BLACKOUT = {"blackout_windows": [["2026-06-01T12:00:00+00:00", "2026-06-01T13:00:00+00:00"]]}

    def test_no_windows_allow(self) -> None:
        now = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)
        assert evaluate_time_window(now, MachineEnforcement()).allow

    def test_in_window_allow(self) -> None:
        rules = MachineEnforcement.from_dict(self.AUTH)
        now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
        d = evaluate_time_window(now, rules)
        assert d.allow
        assert d.reason_code == "IN_TESTING_WINDOW"

    def test_out_of_window_refuse(self) -> None:
        rules = MachineEnforcement.from_dict(self.AUTH)
        now = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
        d = evaluate_time_window(now, rules)
        assert not d.allow
        assert d.reason_code == "OUTSIDE_TESTING_WINDOW"

    def test_window_end_is_exclusive(self) -> None:
        rules = MachineEnforcement.from_dict(self.AUTH)
        now = datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)
        assert not evaluate_time_window(now, rules).allow

    def test_blackout_refuse(self) -> None:
        rules = MachineEnforcement.from_dict({**self.AUTH, **self.BLACKOUT})
        now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
        d = evaluate_time_window(now, rules)
        assert not d.allow
        assert d.reason_code == "BLACKOUT_WINDOW"

    def test_blackout_takes_precedence_over_authorized(self) -> None:
        rules = MachineEnforcement.from_dict({**self.AUTH, **self.BLACKOUT})
        now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
        assert evaluate_time_window(now, rules).reason_code == "BLACKOUT_WINDOW"

    def test_blackout_only_allows_outside_blackout(self) -> None:
        rules = MachineEnforcement.from_dict(self.BLACKOUT)
        now = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
        assert evaluate_time_window(now, rules).allow


class TestExtractTargets:
    def test_empty_returns_empty(self) -> None:
        assert extract_targets("") == set()
        assert extract_targets("  ") == set()
        assert extract_targets("ls -la") == set()

    def test_extracts_nmap_targets(self) -> None:
        cmd = "nmap -sV -p 22,80 10.0.0.5"
        assert "10.0.0.5" in extract_targets(cmd)

    def test_extracts_nmap_cidr(self) -> None:
        cmd = "nmap -sV 10.0.0.0/24"
        assert "10.0.0.0/24" in extract_targets(cmd)

    def test_extracts_ssh_target(self) -> None:
        targets = extract_targets("ssh root@10.0.0.5")
        assert "10.0.0.5" in targets

    def test_extracts_ssh_with_port(self) -> None:
        assert "10.0.0.5" in extract_targets("ssh -p 2222 user@10.0.0.5")

    def test_extracts_curl_url(self) -> None:
        targets = extract_targets("curl -X GET https://api.acme.com/v1/users")
        assert {"api.acme.com"} <= targets

    def test_extracts_hostname_after_verb(self) -> None:
        assert "target.example" in extract_targets("nmap target.example -p 80")

    def test_extracts_impacket_credentials_target(self) -> None:
        cmd = "impacket-secretsdump 'corp/admin:Password!@10.0.0.10'"
        targets = extract_targets(cmd)
        assert "10.0.0.10" in targets

    def test_ssh_keyfile_not_a_target(self) -> None:
        # Regression: ``-i key.pem`` is a local keyfile, not a network target.
        # Extracting it made RoE ENFORCE mode refuse a legitimate in-scope ssh
        # because the keyfile evaluated NOT_IN_SCOPE.
        targets = extract_targets("ssh -i key.pem user@10.0.0.5")
        assert "10.0.0.5" in targets
        assert "key.pem" not in targets

    def test_scp_local_files_not_targets(self) -> None:
        targets = extract_targets("scp -P 2222 -i id_rsa report.txt user@10.0.0.5:/tmp")
        assert "10.0.0.5" in targets
        assert "report.txt" not in targets

    def test_nmap_output_file_not_a_target(self) -> None:
        targets = extract_targets("nmap -oA scan.txt 10.0.0.5")
        assert "10.0.0.5" in targets
        assert "scan.txt" not in targets

    def test_real_domains_still_extracted(self) -> None:
        # Guard against over-correction: real hostnames whose final label is
        # not a file extension must still extract.
        assert "api.acme.com" in extract_targets("curl https://api.acme.com/x")
        assert "target.example" in extract_targets("nmap target.example")


class TestAuditSink:
    def test_append_creates_file(self, tmp_path: Path) -> None:
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        sink.append({"event": "test1"})
        assert (tmp_path / "audit.jsonl").exists()
        lines = (tmp_path / "audit.jsonl").read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["seq"] == 1
        assert rec["prev_hash"] == "0" * 64
        assert len(rec["hash"]) == 64

    def test_chain_is_consistent(self, tmp_path: Path) -> None:
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        for i in range(5):
            sink.append({"event": f"evt-{i}"})
        result = verify_ledger(tmp_path / "audit.jsonl")
        assert result.ok
        assert result.records_checked == 5

    def test_tamper_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        sink = RoEAuditSink(path=path)
        for i in range(3):
            sink.append({"event": f"evt-{i}"})
        lines = path.read_text().splitlines()
        rec1 = json.loads(lines[1])
        rec1["event"] = "TAMPERED"
        lines[1] = json.dumps(rec1)
        path.write_text("\n".join(lines) + "\n")
        result = verify_ledger(path)
        assert not result.ok
        assert result.first_bad_seq == 2

    def test_hmac_chain_when_key_set(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        sink = RoEAuditSink(path=path, hmac_key=b"operator-secret-key")
        for i in range(3):
            sink.append({"event": f"evt-{i}"})
        result = verify_ledger(path, hmac_key=b"operator-secret-key")
        assert result.ok
        assert result.records_checked == 3

    def test_hmac_mismatch_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        sink = RoEAuditSink(path=path, hmac_key=b"correct-key")
        sink.append({"event": "test"})
        result = verify_ledger(path, hmac_key=b"wrong-key")
        assert not result.ok
        assert "hmac mismatch" in result.reason

    def test_new_sink_hydrates_from_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        s1 = RoEAuditSink(path=path)
        s1.append({"event": "a"})
        s1.append({"event": "b"})
        s2 = RoEAuditSink(path=path)
        s2.append({"event": "c"})
        result = verify_ledger(path)
        assert result.ok
        assert result.records_checked == 3
        recs = [json.loads(line) for line in path.read_text().splitlines() if line]
        assert [r["seq"] for r in recs] == [1, 2, 3]


def _make_request(tool_name: str, command: str = "", state: dict | None = None):
    request = MagicMock()
    request.tool = MagicMock()
    request.tool.name = tool_name
    request.state = state or {}
    request.tool_call = MagicMock()
    request.tool_call.args = {"command": command}
    request.tool_call.id = "tc-test"
    request.tool_call_args = {"command": command}
    request.tool_call_id = "tc-test"
    return request


def _make_network_request(tool_name: str, args: dict, state: dict | None = None):
    request = MagicMock()
    request.tool = MagicMock()
    request.tool.name = tool_name
    request.state = state or {}
    request.tool_call = MagicMock()
    request.tool_call.args = args
    request.tool_call.id = "tc-test"
    request.tool_call_args = args
    request.tool_call_id = "tc-test"
    return request


def _write_roe(workspace: Path, machine_enforcement: dict) -> None:
    (workspace / "plan").mkdir(parents=True, exist_ok=True)
    (workspace / "plan" / "roe.json").write_text(
        json.dumps({"machine_enforcement": machine_enforcement}), encoding="utf-8"
    )


class TestRoEMiddleware:
    def test_roe_logs_and_enforces(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_request("bash", "nmap 8.8.8.8", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert isinstance(result, ToolMessage)
        assert "ROE_REFUSED" in result.content
        assert (tmp_path / "audit.jsonl").exists()

    def test_enforce_mode_refuses_out_of_scope(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request("bash", "nmap 8.8.8.8", state={"workspace_path": str(tmp_path)})
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "[ROE_REFUSED]" in result.content
        assert result.status == "error"

    def test_enforce_mode_allows_in_scope(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request("bash", "nmap 10.0.0.10", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"

    def test_enforce_blocks_imds_by_default(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/8"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request(
            "bash",
            "curl -s http://169.254.169.254/latest/meta-data/",
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "FORBIDDEN_DESTINATION" in result.content

    def test_ungated_tool_passes_through(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request("opplan_add_objective", "", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        mw.wrap_tool_call(req, handler)
        assert handler.called

    def test_audit_records_carry_engagement_and_command(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_request(
            "bash",
            "nmap 10.0.0.10",
            state={"workspace_path": str(tmp_path), "engagement_name": "acme-q2"},
        )
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        mw.wrap_tool_call(req, handler)
        recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert len(recs) == 1
        assert recs[0]["engagement"] == "acme-q2"
        assert "nmap 10.0.0.10" in recs[0]["command_excerpt"]
        assert recs[0]["decision"] == "allow"

    def test_audit_records_refuse(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_request("bash", "nmap 8.8.8.8", state={"workspace_path": str(tmp_path)})
        handler = MagicMock()
        mw.wrap_tool_call(req, handler)
        recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert len(recs) == 1
        assert recs[0]["decision"] == "refuse"
        assert recs[0]["reason_code"] == "NOT_IN_SCOPE"

    def test_enforce_refuses_outside_testing_window(self, tmp_path: Path) -> None:
        _write_roe(
            tmp_path,
            {
                "in_scope": ["10.0.0.0/24"],
                "authorized_windows": [["2026-06-01T09:00:00+00:00", "2026-06-01T18:00:00+00:00"]],
            },
        )
        now = datetime(2026, 6, 1, 22, 0, tzinfo=timezone.utc)
        mw = RoEGuardrailMiddleware(now=lambda: now)
        req = _make_request("bash", "nmap 10.0.0.10", state={"workspace_path": str(tmp_path)})
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "OUTSIDE_TESTING_WINDOW" in result.content

    def test_enforce_allows_inside_testing_window(self, tmp_path: Path) -> None:
        _write_roe(
            tmp_path,
            {
                "in_scope": ["10.0.0.0/24"],
                "authorized_windows": [["2026-06-01T09:00:00+00:00", "2026-06-01T18:00:00+00:00"]],
            },
        )
        now = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
        mw = RoEGuardrailMiddleware(now=lambda: now)
        req = _make_request("bash", "nmap 10.0.0.10", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"

    def test_enforce_refuses_during_blackout(self, tmp_path: Path) -> None:
        _write_roe(
            tmp_path,
            {
                "in_scope": ["10.0.0.0/24"],
                "blackout_windows": [["2026-06-01T12:00:00+00:00", "2026-06-01T13:00:00+00:00"]],
            },
        )
        now = datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc)
        mw = RoEGuardrailMiddleware(now=lambda: now)
        req = _make_request("bash", "nmap 10.0.0.10", state={"workspace_path": str(tmp_path)})
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "BLACKOUT_WINDOW" in result.content

    def test_enforce_allows_when_no_windows_configured(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        now = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)
        mw = RoEGuardrailMiddleware(now=lambda: now)
        req = _make_request("bash", "nmap 10.0.0.10", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"

    def test_abort_marker_halts_gated_call(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        (tmp_path / ".abort").write_text("", encoding="utf-8")
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_request("bash", "nmap 10.0.0.10", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert isinstance(result, ToolMessage)
        assert result.content.startswith("[AGENT_HALTED]")
        assert result.status == "error"
        recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert len(recs) == 1
        assert recs[0]["event"] == "abort"
        assert recs[0]["reason_code"] == "EMERGENCY_ABORT"

    def test_no_marker_allows_gated_call(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request("bash", "nmap 10.0.0.10", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"

    def test_abort_marker_ignored_for_ungated_tool(self, tmp_path: Path) -> None:
        (tmp_path / ".abort").write_text("", encoding="utf-8")
        mw = RoEGuardrailMiddleware()
        req = _make_request("opplan_add_objective", "", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"


class TestRedactSecrets:
    def test_password_flag_redacted(self) -> None:
        assert _redact_secrets("mysql -u root -p s3cr3t -h db") == "mysql -u root -p *** -h db"

    def test_long_password_flag_redacted(self) -> None:
        assert _redact_secrets("tool --password=hunter2 -h db") == "tool --password=*** -h db"
        assert _redact_secrets("tool --pass myval x") == "tool --pass *** x"

    def test_token_flag_redacted(self) -> None:
        assert _redact_secrets("gh --token ghp_aBcD1234 --repo x") == "gh --token *** --repo x"

    def test_sshpass_redacted(self) -> None:
        assert (
            _redact_secrets("sshpass -p MyP@ss ssh u@10.0.0.5") == "sshpass -p *** ssh u@10.0.0.5"
        )

    def test_curl_user_pass_redacted(self) -> None:
        assert (
            _redact_secrets("curl -u admin:p4ssw0rd https://api.acme.com")
            == "curl -u admin:*** https://api.acme.com"
        )

    def test_authorization_header_redacted(self) -> None:
        out = _redact_secrets('curl -H "Authorization: Bearer abc.def" https://api.acme.com')
        assert "abc.def" not in out
        assert "***" in out

    def test_api_key_header_redacted(self) -> None:
        out = _redact_secrets('curl -H "X-API-Key: deadbeef" https://api.acme.com')
        assert "deadbeef" not in out
        assert "***" in out

    def test_non_secret_header_untouched(self) -> None:
        cmd = 'curl -H "Content-Type: application/json" https://api.acme.com'
        assert _redact_secrets(cmd) == cmd

    def test_pgpassword_redacted(self) -> None:
        assert (
            _redact_secrets("PGPASSWORD=topsecret psql -U postgres")
            == "PGPASSWORD=*** psql -U postgres"
        )

    def test_impacket_domain_creds_redacted(self) -> None:
        out = _redact_secrets("impacket-secretsdump corp/admin:Password!@10.0.0.10")
        assert "Password!" not in out
        assert "corp/admin:***@10.0.0.10" in out

    def test_url_userinfo_redacted(self) -> None:
        out = _redact_secrets("curl https://user:pass@host.example/path")
        assert "user:***@host.example" in out
        assert ":pass@" not in out

    def test_plain_command_unchanged(self) -> None:
        assert _redact_secrets("nmap 10.0.0.10") == "nmap 10.0.0.10"

    def test_ssh_user_host_without_password_unchanged(self) -> None:
        assert _redact_secrets("ssh -i key.pem user@10.0.0.5") == "ssh -i key.pem user@10.0.0.5"

    def test_empty_command_unchanged(self) -> None:
        assert _redact_secrets("") == ""

    def test_redaction_is_deterministic(self) -> None:
        cmd = 'mysql -u root -p s3cr3t; curl -H "Authorization: Bearer X" h'
        assert _redact_secrets(cmd) == _redact_secrets(cmd)


class TestAuditRecordRedaction:
    def test_password_redacted_in_audit_record(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"mode": "audit"})
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_request(
            "bash", "mysql -u root -p s3cr3t -h db", state={"workspace_path": str(tmp_path)}
        )
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        mw.wrap_tool_call(req, handler)
        recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert "s3cr3t" not in recs[0]["command_excerpt"]
        assert "-p ***" in recs[0]["command_excerpt"]

    def test_bearer_header_redacted_in_audit_record(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"mode": "audit"})
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_request(
            "bash",
            'curl -H "Authorization: Bearer s3cr3ttoken" https://api.acme.com',
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        mw.wrap_tool_call(req, handler)
        recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert "s3cr3ttoken" not in recs[0]["command_excerpt"]
        assert "***" in recs[0]["command_excerpt"]

    def test_sshpass_redacted_in_audit_record(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"mode": "audit"})
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_request(
            "bash",
            "sshpass -p HunterPass ssh user@10.0.0.5",
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        mw.wrap_tool_call(req, handler)
        recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert "HunterPass" not in recs[0]["command_excerpt"]
        assert "sshpass -p ***" in recs[0]["command_excerpt"]


class TestNetworkToolGating:
    def test_http_request_out_of_scope_refused_in_enforce(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["*.acme.com"]})
        mw = RoEGuardrailMiddleware()
        req = _make_network_request(
            "http_request",
            {"method": "GET", "url": "https://evilcorp.com/x"},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "[ROE_REFUSED]" in result.content
        assert result.status == "error"

    def test_http_request_in_scope_allowed(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["*.acme.com"]})
        mw = RoEGuardrailMiddleware()
        req = _make_network_request(
            "http_request",
            {"method": "GET", "url": "https://api.acme.com/v1"},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"

    def test_proxy_send_request_out_of_scope_refused(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["*.acme.com"]})
        mw = RoEGuardrailMiddleware()
        req = _make_network_request(
            "proxy_send_request",
            {"method": "POST", "url": "https://evilcorp.com/login"},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "[ROE_REFUSED]" in result.content

    def test_browser_action_goto_url_respected(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["*.acme.com"]})
        mw = RoEGuardrailMiddleware()
        req = _make_network_request(
            "browser_action",
            {"action": "goto", "params_json": json.dumps({"url": "https://evilcorp.com/"})},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "[ROE_REFUSED]" in result.content

    def test_web_search_is_gated_but_target_extractor_is_noop(self, tmp_path: Path) -> None:
        # web_search is OSINT (DDG is exempt from in-scope target gating)
        # but must still be audited + throttled by the middleware.
        _write_roe(tmp_path, {"in_scope": ["*.acme.com"]})
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_network_request(
            "web_search",
            {"query": "site:evilcorp.com login"},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock(return_value=ToolMessage(content="{}", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "{}"
        recs = [
            json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines() if line
        ]
        assert any(r.get("tool") == "web_search" and r.get("decision") == "allow" for r in recs)

    def test_browser_action_in_scope_url_allowed(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["*.acme.com"]})
        mw = RoEGuardrailMiddleware()
        req = _make_network_request(
            "browser_action",
            {"action": "goto", "params_json": json.dumps({"url": "https://app.acme.com/"})},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"

    def test_browser_action_malformed_params_degrades_to_allow(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["*.acme.com"]})
        mw = RoEGuardrailMiddleware()
        req = _make_network_request(
            "browser_action",
            {"action": "goto", "params_json": "{not valid json"},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"

    def test_browser_action_without_url_allowed(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["*.acme.com"]})
        mw = RoEGuardrailMiddleware()
        req = _make_network_request(
            "browser_action",
            {"action": "screenshot", "params_json": json.dumps({"full_page": True})},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert handler.called
        assert result.content == "ok"

    def test_http_request_imds_blocked_by_default(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/8"]})
        mw = RoEGuardrailMiddleware()
        req = _make_network_request(
            "http_request",
            {"method": "GET", "url": "http://169.254.169.254/latest/meta-data/"},
            state={"workspace_path": str(tmp_path)},
        )
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "FORBIDDEN_DESTINATION" in result.content


class TestSlotRegistration:
    def test_slot_is_in_enum_and_safety_critical(self) -> None:
        from decepticon_core.contracts.slots import (
            SAFETY_CRITICAL_SLOTS,
            SLOTS_PER_ROLE,
            MiddlewareSlot,
        )

        assert MiddlewareSlot.ROE_GUARDRAIL.value == "roe-guardrail"
        assert MiddlewareSlot.ROE_GUARDRAIL in SAFETY_CRITICAL_SLOTS
        for role, slots in SLOTS_PER_ROLE.items():
            assert MiddlewareSlot.ROE_GUARDRAIL in slots, (
                f"role {role!r} missing ROE_GUARDRAIL slot"
            )

    def test_default_factory_is_registered(self) -> None:
        from decepticon.agents.middleware_slots import DEFAULT_SLOT_FACTORIES
        from decepticon_core.contracts.slots import MiddlewareSlot

        assert MiddlewareSlot.ROE_GUARDRAIL in DEFAULT_SLOT_FACTORIES
        factory = DEFAULT_SLOT_FACTORIES[MiddlewareSlot.ROE_GUARDRAIL]
        mw = factory(role="recon")
        assert isinstance(mw, RoEGuardrailMiddleware)

    def test_legacy_names_alias_to_guardrail(self) -> None:
        """Compat shim (removed at 2.0.0): the pre-rename slot member and
        class name must still resolve to the renamed canonicals so
        downstream plugins / downstream subagents keep importing them."""
        from decepticon.middleware import (
            RoEEnforcementMiddleware,
            RoEGuardrailMiddleware,
        )
        from decepticon.middleware.roe import (
            RoEEnforcementMiddleware as RoEEnforcementFromModule,
        )
        from decepticon_core.contracts.slots import MiddlewareSlot

        # Enum alias: same member, same value, same assembly position.
        assert MiddlewareSlot.ROE_ENFORCEMENT is MiddlewareSlot.ROE_GUARDRAIL
        assert MiddlewareSlot.ROE_ENFORCEMENT.value == "roe-guardrail"
        # The legacy slot name must NOT appear as its own member in
        # iteration (else the slot would be assembled twice).
        assert MiddlewareSlot.ROE_ENFORCEMENT in list(MiddlewareSlot)
        assert sum(1 for s in MiddlewareSlot if s.value == "roe-guardrail") == 1
        # Class alias points at the renamed class.
        assert RoEEnforcementMiddleware is RoEGuardrailMiddleware
        assert RoEEnforcementFromModule is RoEGuardrailMiddleware


class TestFqdnTrailingDotNormalization:
    """Regression: a trailing dot is DNS-equivalent, so the FQDN form of a host
    must not bypass any scope check. Previously ``metadata.google.internal.``
    and the IMDS IP ``169.254.169.254.`` slipped past the forbidden-destination
    and out-of-scope deny rules (the IP form also failed ``ip_address()``
    parsing and fell through to default-allow)."""

    def test_trailing_dot_does_not_bypass_forbidden_destination(self) -> None:
        rules = MachineEnforcement.from_dict({"mode": "enforce"})
        for host in (
            "metadata.google.internal",
            "metadata.google.internal.",
            "169.254.169.254",
            "169.254.169.254.",
        ):
            decision = evaluate_target(host, rules)
            assert decision.allow is False, host
            assert decision.reason_code == "FORBIDDEN_DESTINATION", host

    def test_trailing_dot_does_not_bypass_out_of_scope(self) -> None:
        rules = MachineEnforcement.from_dict(
            {"in_scope": ["*.acme.com"], "out_of_scope": ["billing.acme.com"]}
        )
        for host in ("billing.acme.com", "billing.acme.com."):
            decision = evaluate_target(host, rules)
            assert decision.allow is False, host
            assert decision.reason_code == "OUT_OF_SCOPE", host

    def test_trailing_dot_still_matches_in_scope_glob(self) -> None:
        rules = MachineEnforcement.from_dict({"in_scope": ["*.acme.com"]})
        for host in ("app.acme.com", "app.acme.com."):
            decision = evaluate_target(host, rules)
            assert decision.allow is True, host
            assert decision.reason_code == "IN_SCOPE", host

    def test_trailing_dot_on_exact_in_scope_host(self) -> None:
        rules = MachineEnforcement.from_dict({"in_scope": ["single-host.example"]})
        assert evaluate_target("single-host.example.", rules).allow is True
        # An unrelated FQDN-form host is still refused (not in scope).
        assert evaluate_target("other.example.", rules).allow is False


class TestLoadRulesFallback:
    def test_malformed_roe_returns_empty_rules(self, tmp_path: Path) -> None:
        (tmp_path / "plan").mkdir(parents=True, exist_ok=True)
        (tmp_path / "plan" / "roe.json").write_text("{not valid json", encoding="utf-8")
        rules = _load_rules_for_workspace(str(tmp_path))
        assert rules.configured is False

    def test_malformed_roe_gated_call_refused_and_logged(self, tmp_path: Path) -> None:
        (tmp_path / "plan").mkdir(parents=True, exist_ok=True)
        (tmp_path / "plan" / "roe.json").write_text("}}garbage{{", encoding="utf-8")
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink)
        req = _make_request("bash", "nmap 8.8.8.8", state={"workspace_path": str(tmp_path)})
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert isinstance(result, ToolMessage)
        assert "ROE_CONFIG_MISSING" in result.content
        recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert len(recs) == 1
        assert recs[0]["decision"] == "refuse"
        assert recs[0]["enforced"] is True


class TestToTextFlattening:
    def test_to_text_direct(self) -> None:
        assert _to_text("plain") == "plain"
        assert _to_text(["a", "b"]) == "ab"
        assert _to_text([{"type": "text", "text": "x"}, {"type": "other"}]) == "x"
        assert _to_text(42) == "42"


class TestRoEMiddlewareAsync:
    def test_async_enforce_refuses_out_of_scope(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request("bash", "nmap 8.8.8.8", state={"workspace_path": str(tmp_path)})
        called = False

        async def handler(_req):
            nonlocal called
            called = True
            return ToolMessage(content="ok", tool_call_id="tc-test")

        result = asyncio.run(mw.awrap_tool_call(req, handler))
        assert not called
        assert "[ROE_REFUSED]" in result.content
        assert result.status == "error"
        assert result.tool_call_id == "tc-test"

    def test_async_enforce_allows_in_scope(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request("bash", "nmap 10.0.0.10", state={"workspace_path": str(tmp_path)})

        async def handler(_req):
            return ToolMessage(content="ok", tool_call_id="tc-test")

        result = asyncio.run(mw.awrap_tool_call(req, handler))
        assert result.content == "ok"

    def test_async_roe_refusal_preserves_output_contract(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"out_of_scope": ["10.99.0.0/16"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request("bash", "nmap 10.99.1.1", state={"workspace_path": str(tmp_path)})

        async def handler(_req):
            return ToolMessage(content="scan output", tool_call_id="tc-test")

        result = asyncio.run(mw.awrap_tool_call(req, handler))
        assert "[ROE_REFUSED]" in result.content
        assert "scan output" not in result.content


class TestTcidFallback:
    def test_refused_carries_id_from_tool_call(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"in_scope": ["10.0.0.0/24"]})
        mw = RoEGuardrailMiddleware()
        req = _make_request("bash", "nmap 8.8.8.8", state={"workspace_path": str(tmp_path)})
        del req.tool_call_id
        req.tool_call.id = "fallback-id"
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "[ROE_REFUSED]" in result.content
        assert result.tool_call_id == "fallback-id"


class TestBuildDefaultSink:
    def test_env_path_used(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_target = tmp_path / "env" / "ledger.jsonl"
        monkeypatch.setenv("DECEPTICON_ROE_AUDIT_PATH", str(env_target))
        sink = build_default_sink(str(tmp_path / "workspace"))
        assert sink is not None
        assert sink.path == env_target

    def test_no_env_no_workspace_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DECEPTICON_ROE_AUDIT_PATH", raising=False)
        assert build_default_sink(None) is None

    def test_workspace_path_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DECEPTICON_ROE_AUDIT_PATH", raising=False)
        sink = build_default_sink(str(tmp_path))
        assert sink is not None
        assert sink.path == tmp_path / "audit" / "roe-decisions.jsonl"


class _ConcurrencyProbe:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def exit(self) -> None:
        with self.lock:
            self.active -= 1


class TestConcurrencyGate:
    def test_limit_none_means_unlimited(self) -> None:
        mw = RoEGuardrailMiddleware()
        rules = MachineEnforcement.from_dict({"mode": "audit"})
        assert mw._resolve_limit(rules) is None
        with mw._sync_gate(rules, gated=True):
            pass
        assert mw._sync_sema is None

    def test_limit_zero_means_unlimited(self) -> None:
        mw = RoEGuardrailMiddleware()
        rules = MachineEnforcement.from_dict({"max_concurrent_connections": 0})
        assert mw._resolve_limit(rules) is None
        with mw._sync_gate(rules, gated=True):
            pass
        assert mw._sync_sema is None

    def test_first_seen_limit_wins(self) -> None:
        mw = RoEGuardrailMiddleware()
        first = MachineEnforcement.from_dict({"max_concurrent_connections": 2})
        second = MachineEnforcement.from_dict({"max_concurrent_connections": 9})
        assert mw._resolve_limit(first) == 2
        assert mw._resolve_limit(second) == 2

    def test_ungated_call_is_not_gated(self) -> None:
        mw = RoEGuardrailMiddleware()
        rules = MachineEnforcement.from_dict({"max_concurrent_connections": 1})
        with mw._sync_gate(rules, gated=False):
            pass
        assert mw._sync_sema is None

    def test_sync_gate_admits_at_most_n(self, tmp_path: Path) -> None:
        _write_roe(
            tmp_path,
            {"max_concurrent_connections": 2},
        )
        mw = RoEGuardrailMiddleware()
        probe = _ConcurrencyProbe()
        release = threading.Event()

        def handler(_request):
            probe.enter()
            release.wait(timeout=5)
            probe.exit()
            return ToolMessage(content="ok", tool_call_id="tc-test")

        def run() -> None:
            req = _make_request("bash", "nmap 10.0.0.1", state={"workspace_path": str(tmp_path)})
            mw.wrap_tool_call(req, handler)

        threads = [threading.Thread(target=run) for _ in range(5)]
        for t in threads:
            t.start()
        deadline = time.time() + 5
        while probe.active < 2 and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        peak_while_blocked = probe.peak
        release.set()
        for t in threads:
            t.join(timeout=5)
        assert peak_while_blocked == 2
        assert probe.peak == 2

    def test_sync_gate_unlimited_runs_all_concurrently(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"mode": "audit"})
        mw = RoEGuardrailMiddleware()
        probe = _ConcurrencyProbe()
        release = threading.Event()
        started = threading.Semaphore(0)

        def handler(_request):
            probe.enter()
            started.release()
            release.wait(timeout=5)
            probe.exit()
            return ToolMessage(content="ok", tool_call_id="tc-test")

        def run() -> None:
            req = _make_request("bash", "nmap 10.0.0.1", state={"workspace_path": str(tmp_path)})
            mw.wrap_tool_call(req, handler)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for _ in range(4):
            assert started.acquire(timeout=5)
        assert probe.peak == 4
        release.set()
        for t in threads:
            t.join(timeout=5)

    async def test_async_gate_admits_at_most_n(self, tmp_path: Path) -> None:
        _write_roe(
            tmp_path,
            {"max_concurrent_connections": 2},
        )
        mw = RoEGuardrailMiddleware()
        active = 0
        peak = 0
        gate = asyncio.Event()

        async def handler(_request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await gate.wait()
            active -= 1
            return ToolMessage(content="ok", tool_call_id="tc-test")

        async def run():
            req = _make_request("bash", "nmap 10.0.0.1", state={"workspace_path": str(tmp_path)})
            return await mw.awrap_tool_call(req, handler)

        tasks = [asyncio.create_task(run()) for _ in range(5)]
        deadline = time.time() + 5
        while active < 2 and time.time() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        assert peak == 2
        gate.set()
        await asyncio.gather(*tasks)
        assert peak == 2

    async def test_async_gate_unlimited_runs_all_concurrently(self, tmp_path: Path) -> None:
        _write_roe(tmp_path, {"mode": "audit"})
        mw = RoEGuardrailMiddleware()
        active = 0
        peak = 0
        gate = asyncio.Event()

        async def handler(_request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await gate.wait()
            active -= 1
            return ToolMessage(content="ok", tool_call_id="tc-test")

        async def run():
            req = _make_request("bash", "nmap 10.0.0.1", state={"workspace_path": str(tmp_path)})
            return await mw.awrap_tool_call(req, handler)

        tasks = [asyncio.create_task(run()) for _ in range(4)]
        deadline = time.time() + 5
        while active < 4 and time.time() < deadline:
            await asyncio.sleep(0.01)
        assert peak == 4
        gate.set()
        await asyncio.gather(*tasks)

    def test_refused_call_releases_no_slot(self, tmp_path: Path) -> None:
        _write_roe(
            tmp_path,
            {"in_scope": ["10.0.0.0/24"], "max_concurrent_connections": 1},
        )
        mw = RoEGuardrailMiddleware()
        req = _make_request("bash", "nmap 8.8.8.8", state={"workspace_path": str(tmp_path)})
        handler = MagicMock()
        result = mw.wrap_tool_call(req, handler)
        assert not handler.called
        assert "[ROE_REFUSED]" in result.content
        assert mw._sync_sema is None


class TestRoEThrottle:
    def test_zero_delay_never_waits(self) -> None:
        mw = RoEGuardrailMiddleware(jitter_frac=0.0)
        rules = MachineEnforcement.from_dict({"min_inter_request_delay_ms": 0})
        assert mw._pace_wait_seconds(rules) == 0.0

    def test_first_call_does_not_wait_then_burst_is_spaced(self, monkeypatch) -> None:
        mw = RoEGuardrailMiddleware(jitter_frac=0.0)
        rules = MachineEnforcement.from_dict({"min_inter_request_delay_ms": 200})
        clock = {"t": 1000.0}
        monkeypatch.setattr(roe_mod.time, "monotonic", lambda: clock["t"])
        assert mw._pace_wait_seconds(rules) == 0.0
        assert abs(mw._pace_wait_seconds(rules) - 0.2) < 1e-9
        assert abs(mw._pace_wait_seconds(rules) - 0.4) < 1e-9

    def test_elapsed_gap_resets_wait(self, monkeypatch) -> None:
        mw = RoEGuardrailMiddleware(jitter_frac=0.0)
        rules = MachineEnforcement.from_dict({"min_inter_request_delay_ms": 100})
        clock = {"t": 5000.0}
        monkeypatch.setattr(roe_mod.time, "monotonic", lambda: clock["t"])
        assert mw._pace_wait_seconds(rules) == 0.0
        clock["t"] += 1.0
        assert mw._pace_wait_seconds(rules) == 0.0

    def test_jitter_added_above_floor_under_contention(self, monkeypatch) -> None:
        mw = RoEGuardrailMiddleware(jitter_frac=0.5)
        rules = MachineEnforcement.from_dict({"min_inter_request_delay_ms": 200})
        clock = {"t": 1000.0}
        monkeypatch.setattr(roe_mod.time, "monotonic", lambda: clock["t"])
        monkeypatch.setattr(roe_mod.random, "uniform", lambda _a, b: b)
        assert mw._pace_wait_seconds(rules) == 0.0
        assert abs(mw._pace_wait_seconds(rules) - 0.3) < 1e-9

    def test_dispatch_sleeps_and_records_throttle(self, tmp_path: Path, monkeypatch) -> None:
        _write_roe(tmp_path, {"min_inter_request_delay_ms": 150})
        sink = RoEAuditSink(path=tmp_path / "audit.jsonl")
        mw = RoEGuardrailMiddleware(sink=sink, jitter_frac=0.0)
        slept: list[float] = []
        monkeypatch.setattr(roe_mod.time, "monotonic", lambda: 1000.0)
        monkeypatch.setattr(roe_mod.time, "sleep", lambda s: slept.append(s))
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        req = _make_request("bash", "id", state={"workspace_path": str(tmp_path)})
        mw.wrap_tool_call(req, handler)
        mw.wrap_tool_call(req, handler)
        assert slept and abs(slept[0] - 0.15) < 1e-9
        assert handler.call_count == 2
        recs = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert any(
            r.get("event") == "throttle" and r["reason_code"] == "MIN_INTER_REQUEST_DELAY"
            for r in recs
        )

    def test_refused_call_is_not_paced(self, tmp_path: Path, monkeypatch) -> None:
        _write_roe(
            tmp_path,
            {"in_scope": ["10.0.0.0/24"], "min_inter_request_delay_ms": 500},
        )
        mw = RoEGuardrailMiddleware(jitter_frac=0.0)
        slept: list[float] = []
        monkeypatch.setattr(roe_mod.time, "sleep", lambda s: slept.append(s))
        handler = MagicMock()
        req = _make_request("bash", "nmap 8.8.8.8", state={"workspace_path": str(tmp_path)})
        result = mw.wrap_tool_call(req, handler)
        assert "[ROE_REFUSED]" in result.content
        assert not handler.called
        assert slept == []

    def test_ungated_tool_not_paced(self, tmp_path: Path, monkeypatch) -> None:
        _write_roe(tmp_path, {"min_inter_request_delay_ms": 500})
        mw = RoEGuardrailMiddleware(jitter_frac=0.0)
        slept: list[float] = []
        monkeypatch.setattr(roe_mod.time, "sleep", lambda s: slept.append(s))
        handler = MagicMock(return_value=ToolMessage(content="ok", tool_call_id="tc-test"))
        req = _make_request("opplan_add_objective", "", state={"workspace_path": str(tmp_path)})
        mw.wrap_tool_call(req, handler)
        mw.wrap_tool_call(req, handler)
        assert slept == []
