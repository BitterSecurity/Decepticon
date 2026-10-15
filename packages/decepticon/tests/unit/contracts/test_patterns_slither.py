"""Tests for the Solidity pattern scanner + Slither ingestion."""

from __future__ import annotations

from decepticon.tools.contracts.foundry import (
    generate_access_control_test,
    generate_flashloan_test,
    generate_reentrancy_test,
)
from decepticon.tools.contracts.patterns import scan_solidity_source
from decepticon_core.types.kg import Severity


class TestPatternScanner:
    def test_reentrancy_pattern_detected(self) -> None:
        src = 'function withdraw() external { (bool ok,) = msg.sender.call{value: 1}(""); }'
        findings = scan_solidity_source(src)
        assert any(f.rule == "reentrancy.external-call-before-write" for f in findings)

    def test_tx_origin_flagged(self) -> None:
        src = "function auth() public { require(tx.origin == owner); }"
        assert any(f.rule == "auth.tx-origin" for f in scan_solidity_source(src))

    def test_delegatecall_critical(self) -> None:
        src = 'function proxy(address impl) public { impl.delegatecall(""); }'
        findings = scan_solidity_source(src)
        assert any(
            f.rule == "delegatecall.unchecked-target" and f.severity == Severity.CRITICAL
            for f in findings
        )

    def test_block_timestamp_randomness(self) -> None:
        src = "function rand() public view returns (uint) { return block.timestamp % 100; }"
        assert any(f.rule == "randomness.block-timestamp" for f in scan_solidity_source(src))

    def test_ecrecover_unchecked(self) -> None:
        src = "address signer = ecrecover(hash, v, r, s);"
        assert any(f.rule == "signature.ecrecover-unchecked" for f in scan_solidity_source(src))

    def test_narrow_cast_flagged(self) -> None:
        src = "uint256 big; uint128 small = uint128(big);"
        assert any(f.rule == "math.unchecked-cast" for f in scan_solidity_source(src))

    def test_oracle_single_source(self) -> None:
        src = "(uint160 sqrtPriceX96, , , , , , ) = pool.slot0();"
        assert any(f.rule == "oracle.single-source-price" for f in scan_solidity_source(src))

    def test_flashloan_callback_no_auth(self) -> None:
        src = "function executeOperation(address[] calldata a, uint[] calldata b, uint[] calldata c, address d, bytes calldata e) external returns (bool) { return true; }"
        findings = scan_solidity_source(src)
        assert any(f.rule == "flashloan.callback-no-auth" for f in findings)

    def test_line_numbers_and_snippet_populated(self) -> None:
        src = "// header\n// line 2\ntx.origin\n"
        findings = scan_solidity_source(src)
        assert any(f.line == 3 for f in findings)
        assert any("tx.origin" in f.snippet for f in findings)


# ``TestSlitherIngest`` previously called ``ingest_slither_json(data, g)``
# with the legacy ``KnowledgeGraph`` positional argument. After
# ``slither.py`` was rewritten to write directly through
# ``KGStore.record_observations`` (keyword-only ``engagement`` kwarg,
# no ``graph`` parameter), those tests no longer match the signature.
# They are reintroduced in a dedicated KGStore-mock-based test PR —
# see the Slither RFC §4.4.


class TestFoundry:
    def test_reentrancy_template_contains_markers(self) -> None:
        h = generate_reentrancy_test("Vault", "withdraw", "src/Vault.sol")
        assert "contract ReentrancyAttacker" in h.source
        assert "Vault target" in h.source
        assert "withdraw" in h.source
        assert h.path.endswith("_Reentrancy.t.sol")

    def test_access_template_expects_revert_before_unauthorised_call(self) -> None:
        h = generate_access_control_test("Token", "mint")
        assert "test_unauthorised_call_reverts" in h.source
        assert h.source.index("vm.startPrank(ATTACKER);") < h.source.index("vm.expectRevert();")
        assert h.source.index("vm.expectRevert();") < h.source.index("target.mint();")
        assert h.source.index("target.mint();") < h.source.index("vm.stopPrank();")

    def test_flashloan_template(self) -> None:
        h = generate_flashloan_test("Pool")
        assert "executeOperation" in h.source
