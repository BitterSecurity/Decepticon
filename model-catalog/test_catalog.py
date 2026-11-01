#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic==2.12.3", "PyYAML==6.0.3", "typer==0.20.0", "pytest==8.4.2"]
# ///
# How to run: uv run model-catalog/test_catalog.py
"""Offline CLI scenarios. Synthetic models never contact a provider."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from generate import Catalog
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def test_terra_is_a_responses_model_with_public_prices_and_reasoning() -> None:
    catalog = json.loads((ROOT / "model-catalog/catalog.json").read_text())
    terra = next(m for m in catalog["models"] if m["id"] == "openai/gpt-6.1-terra")
    assert terra["info"]["mode"] == "responses"
    assert terra["info"]["input_cost_per_token"] == 0.000001
    assert terra["info"]["output_cost_per_token"] == 0.000005
    assert terra["info"]["cache_read_input_token_cost"] == 0.00000005
    assert terra["ui"]["reasoningEfforts"] == ["none", "low", "medium", "high", "xhigh", "max"]
    routes = yaml.safe_load((ROOT / "config/litellm.yaml").read_text())["model_list"]
    route = next(r for r in routes if r["model_name"] == terra["id"])
    assert route["model_info"]["mode"] == "responses"
    assert "temperature" in route["litellm_params"]["additional_drop_params"]


def test_embedding_routes_remain_available_but_not_in_agent_picker(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "model-catalog", tmp_path / "model-catalog")
    subprocess.run(
        [sys.executable, str(tmp_path / "model-catalog/generate.py")],
        check=True,
        capture_output=True,
    )
    picker = (tmp_path / "clients/cli/src/commands/currentProdModels.ts").read_text()
    routes = json.loads((tmp_path / "config/litellm_prod_catalog.json").read_text())["routes"]
    routes += yaml.safe_load((tmp_path / "config/litellm.yaml").read_text())["model_list"]
    available = {row["model_name"] for row in routes}
    # Responses API models are agent models too, despite mode != "chat".
    assert "openai/gpt-6.1-sol" in picker
    for identity in (
        "openai/text-embedding-3-small",
        "gemini/gemini-embedding-001",
        "voyage/voyage-3",
    ):
        assert identity not in picker
        assert identity in available


def test_mimo_routes_use_inference_host_and_preserve_model_ids(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "model-catalog", tmp_path / "model-catalog")
    subprocess.run(
        [sys.executable, str(tmp_path / "model-catalog/generate.py")],
        check=True,
        capture_output=True,
    )
    rows = yaml.safe_load((tmp_path / "config/litellm.yaml").read_text())["model_list"]
    for slug in ("mimo-vl", "mimo-rl", "mimo-7b"):
        route = next(row for row in rows if row["model_name"] == f"openai/{slug}")
        assert route["litellm_params"]["api_base"] == "https://api.xiaomimimo.com/v1"
        assert route["litellm_params"]["model"] == f"openai/{slug}"
        assert route["litellm_params"]["api_key"] == "os.environ/XIAOMI_MIMO_API_KEY"


def test_new_model_propagates_without_editing_product_inventory(tmp_path: Path) -> None:
    # Given: the real catalog, generator and unchanged product policy.
    shutil.copytree(ROOT / "model-catalog", tmp_path / "model-catalog")
    catalog_path = tmp_path / "model-catalog/catalog.json"
    catalog = json.loads(catalog_path.read_text())
    original = copy.deepcopy(catalog)
    prototype = next(m for m in catalog["models"] if m["id"] == "openai/gpt-6.1-sol")
    candidate = copy.deepcopy(prototype)
    candidate["id"] = "openai/catalog-fixture-only"
    candidate["params"]["model"] = candidate["id"]
    candidate["ui"]["id"] = candidate["id"]
    candidate["ui"]["label"] = "Catalog fixture only"
    unknown = copy.deepcopy(candidate)
    unknown["id"] = "unadmitted/catalog-fixture-only"
    unknown["params"]["model"] = unknown["id"]
    unknown["ui"]["id"] = unknown["id"]
    unknown["ui"]["provider"] = "unadmitted"
    catalog["models"].extend([candidate, unknown])
    catalog_path.write_text(json.dumps(catalog))
    lock_path = tmp_path / "model-catalog/upstream.lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        lock["sha256"] = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
        lock_path.write_text(json.dumps(lock))
    policy_before = (tmp_path / "model-catalog/overlay.json").read_bytes()
    # When: the real generation CLI consumes one edited public data file.
    result = subprocess.run(
        [sys.executable, str(tmp_path / "model-catalog/generate.py")],
        capture_output=True,
        text=True,
    )
    # Then: admitted provider routes appear; unknown providers remain disabled.
    assert result.returncode == 0, result.stderr
    policy = json.loads(policy_before)
    for path, output in policy["outputs"].items():
        rendered = yaml.safe_load((tmp_path / path).read_text())
        names = [r["model_name"] for r in rendered.get("model_list", rendered.get("routes"))]
        assert candidate["id"] in names
        assert unknown["id"] not in names
        if policy["product"] == "saas":
            rows = rendered["model_list"]
            new_route = next(r for r in rows if r["model_name"] == candidate["id"])
            assert (
                new_route["litellm_params"]["api_key"] == output["providers"]["openai"]["api_key"]
            )
    if "ui" in policy:
        picker = (tmp_path / policy["ui"]["path"]).read_text()
        assert candidate["id"] in picker
        assert unknown["id"] not in picker
        assert '"creditEligible": false' in picker.split("Catalog fixture only", 1)[1]
    assert (tmp_path / "model-catalog/overlay.json").read_bytes() == policy_before
    assert len(catalog["models"]) == len(original["models"]) + 2
    if evidence_dir := os.environ.get("CATALOG_EVIDENCE_DIR"):
        evidence = Path(evidence_dir) / "fixture"
        paths = [*policy["outputs"], "model-catalog/catalog.json", "model-catalog/overlay.json"]
        if "ui" in policy:
            paths.append(policy["ui"]["path"])
        for name in paths:
            destination = evidence / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp_path / name, destination)


def test_check_detects_generated_drift(tmp_path: Path) -> None:
    # Given: freshly generated outputs, then an accidental manual edit.
    shutil.copytree(ROOT / "model-catalog", tmp_path / "model-catalog")
    script = str(tmp_path / "model-catalog/generate.py")
    subprocess.run([sys.executable, script], check=True, capture_output=True)
    policy = json.loads((tmp_path / "model-catalog/overlay.json").read_text())
    output = tmp_path / next(iter(policy["outputs"]))
    output.write_text(output.read_text() + "\n# drift\n")
    # When: CI runs the actual drift command.
    result = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
    # Then: the command fails and identifies generated drift.
    assert result.returncode != 0
    assert "Generated drift" in result.stderr


@pytest.mark.parametrize("identity", ["auth/private", "subscription/private", "runyour/private"])
def test_private_catalog_identity_rejected(identity: str) -> None:
    # Given: private identity inserted into an otherwise valid catalog record.
    data = json.loads((ROOT / "model-catalog/catalog.json").read_text())
    data["models"][0]["id"] = identity
    # When / Then: boundary parsing fails before generation.
    with pytest.raises(ValidationError, match="Private route"):
        Catalog.model_validate(data)


def test_duplicate_identity_rejected() -> None:
    # Given: a duplicate record in public data.
    data = json.loads((ROOT / "model-catalog/catalog.json").read_text())
    data["models"].append(data["models"][0])
    # When / Then: boundary parsing rejects ambiguous routes.
    with pytest.raises(ValidationError, match="Duplicate model"):
        Catalog.model_validate(data)


def test_tampered_pinned_snapshot_fails_before_generation(tmp_path: Path) -> None:
    # Given: an isolated consumer fixture with a valid data hash (no remote claim).
    shutil.copytree(ROOT / "model-catalog", tmp_path / "model-catalog")
    policy_path = tmp_path / "model-catalog/overlay.json"
    policy = json.loads(policy_path.read_text())
    policy["product"] = "saas"
    policy_path.write_text(json.dumps(policy))
    catalog_path = tmp_path / "model-catalog/catalog.json"
    lock = {
        "repository": "https://github.com/BitterSecurity/Decepticon.git",
        "commit": "0" * 40,
        "sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest(),
        "path": "model-catalog/catalog.json",
    }
    (tmp_path / "model-catalog/upstream.lock.json").write_text(json.dumps(lock))
    catalog_path.write_text(catalog_path.read_text() + "\n")
    # When: the actual CLI sees data whose bytes differ from the recorded pin.
    result = subprocess.run(
        [sys.executable, str(tmp_path / "model-catalog/generate.py")],
        capture_output=True,
        text=True,
    )
    # Then: generation fails closed, without writing route outputs.
    assert result.returncode != 0
    assert "Pinned catalog hash mismatch" in result.stderr
    assert all(not (tmp_path / path).exists() for path in policy["outputs"])


if __name__ == "__main__":
    raise SystemExit(pytest.main(["-c", "/dev/null", "-p", "no:cacheprovider", __file__, "-v"]))
