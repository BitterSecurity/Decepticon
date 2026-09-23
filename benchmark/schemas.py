from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

CancelOutcome = Literal[
    "clean",  # run reached terminal status without harness-side cancel
    "soft_cancelled",  # cancel API call returned within deadline, run reached terminal
    "rollback",  # action="rollback" succeeded and run reached terminal
    "container_restart",  # had to restart langgraph container to dislodge wedged run
    "failed",  # cancel call failed and verify-terminal also failed
]

# ExploitBench capability tiers (T1 = highest, T5 = lowest). Mirrors
# the grader taxonomy at https://exploitbench.ai/#methodology, where
# the 16 capability flags collapse into 5 ranked tiers via
# ``best_tier_for_caps`` in :mod:`benchmark.providers.exploitbench`.
# ``None`` means the run achieved no capability at all.
ExploitTier = Literal["T1", "T2", "T3", "T4", "T5"]


class Challenge(BaseModel):
    """A single benchmark challenge definition."""

    id: str = Field(description="Challenge identifier, e.g. XBEN-001-24")
    name: str
    description: str
    level: int = Field(description="Difficulty: 1=easy, 2=medium, 3=hard")
    tags: list[str]
    win_condition: str = Field(default="flag")
    compose_dir: Path | None = Field(
        default=None,
        description="Directory containing docker-compose.yml (XBOW provider)",
    )
    # Flag format hint injected into the engagement context so the agent
    # knows what to look for. XBOW's synthetic ``FLAG{<sha256>}`` is the
    # default; Cybench carries the competition-native format per task
    # (``HTB{...}``, ``flag{...}``, …).
    flag_format: str = "FLAG{<64-char-hex>}"
    # Literal expected flag when the provider knows it at load time
    # (Cybench reads it from the task's final subtask answer). XBOW leaves
    # this None because it derives ``FLAG{sha256(id)}`` at evaluate time,
    # and CyberGym leaves it None because success is server-verified rather
    # than a flag string.
    expected_flag: str | None = None
    # ExploitBench-style env metadata. Populated by ``ExploitBenchProvider``;
    # XBOW provider leaves these unset. ``docker_image`` is the GHCR ref
    # (e.g. ``ghcr.io/exploitbench/v8-r1:cve-2024-1939``); ``mcp_interface``
    # is the interface contract advertised by the container's MCP server
    # (e.g. ``rl.mcp.v8_exploit.v1``); ``seed`` lets one Challenge expand
    # into N independent seeded runs without breaking the 1-Challenge =
    # 1-result invariant in the existing harness.
    docker_image: str | None = None
    mcp_interface: str | None = None
    seed: int | None = None
    # MHBench provider only: name of the topology spec class
    # (``Chain2Hosts``, ``EquifaxSmall``, …) or a generated topology JSON
    # file name. Passed verbatim to upstream ``main.py --type``.
    mhbench_env_type: str | None = None
    # CyberGym provider only: upstream task id (``arvo:1234`` /
    # ``oss-fuzz:5678``) passed verbatim to ``cybergym.task.gen_task``.
    # Win condition is a server-verified PoC crash, not a flag.
    cybergym_task_id: str | None = None

    @property
    def flag_pattern(self) -> re.Pattern[str]:
        return re.compile(r"FLAG\{[a-f0-9]+\}")


class SetupResult(BaseModel):
    """Result from setting up a challenge environment."""

    target_url: str
    container_ids: list[str] = Field(default_factory=list)
    success: bool
    error: str | None = None
    extra_ports: dict[int, int] = Field(
        default_factory=dict,
        description="Additional published ports (target_port -> host_port)",
    )


class ChallengeResult(BaseModel):
    """Result from running a single challenge."""

    challenge_id: str
    challenge_name: str
    level: int
    tags: list[str]
    passed: bool
    flag_captured: str | None = None
    duration_seconds: float = 0.0
    error: str | None = None
    # Solve evidence metadata (for public reporting)
    # trace_id is the LangSmith trace identifier (= the LangGraph run_id from
    # client.runs.create), giving observer a direct handle to fetch the full
    # trace tree without needing thread_id-based metadata filtering.
    trace_id: str | None = None
    token_count: int | None = None
    agent_summary: str | None = None
    # Cancel/teardown introspection: ground truth for whether the LangGraph run
    # was actually halted before teardown fired. Populated by the harness
    # cancel-and-verify-terminal path; observers/critics read these to detect
    # cancel/teardown order races without scraping LangSmith.
    cancel_outcome: CancelOutcome | None = None
    terminal_status_at_teardown: str | None = None
    # Setup overhead: time from run_challenge entry to LangGraph submit.
    # Excludes agent execution — captures docker start + provider.setup() cost
    # so duration_seconds reflects only agent wall-clock budget.
    setup_seconds: float | None = None
    # Per-challenge USD cost from LiteLLM /spend/logs (sum of spend
    # field for rows whose startTime falls inside this run's agent
    # window). ``None`` when LiteLLM was unreachable or returned no
    # rows. For subscription routes (auth/*, gemini-sub/*, copilot/*,
    # grok-sub/*, pplx-sub/*) this is shadow cost: what the same
    # request would have cost on the equivalent paid API. Reliable
    # only for sequential runs (parallel=1) — concurrent challenges
    # share the time window and cost cannot be cleanly attributed.
    cost_usd: float | None = None
    # ExploitBench tiered grading. ``capabilities`` is the merged
    # ``best_caps`` bitmap returned by the in-container grader across
    # every ``grade()`` call the agent issued during the episode —
    # capabilities accumulate, they never regress. ``tier_reached`` is
    # the highest tier any single capability satisfied; ``score`` is
    # the bench-v8 weighted sum (one point per capability, double for
    # ace) so leaderboard ordering matches ``exploitbench.ai`` numbers.
    # XBOW runs leave all three None and continue to be scored solely
    # on ``passed`` / ``flag_captured``.
    capabilities: dict[str, bool] = Field(default_factory=dict)
    tier_reached: ExploitTier | None = None
    capability_score: float | None = None
    # Optional environment / bug identifier surfaced for downstream
    # tooling. For ExploitBench this is the bug ID (e.g.
    # ``v8-cve-2024-1939``); for XBOW it stays None because
    # ``challenge_id`` already carries the unique key.
    bug_id: str | None = None


class RunProvenance(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    captured_at: datetime
    context_mode: Literal["hinted", "blind"]
    source_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    source_dirty: bool
    benchmark_revisions: dict[str, str]
    model_profile: str = Field(min_length=1)
    model_assignments: dict[str, list[str]] = Field(min_length=1)
    artifact_hashes: dict[str, str] = Field(min_length=1)
    container_images: dict[str, str] = Field(min_length=1)
    config: dict[str, Any] = Field(min_length=1)
    python_version: str = Field(min_length=1)
    platform: str = Field(min_length=1)


class BenchmarkReport(BaseModel):
    """Aggregated report for a full benchmark run."""

    provider_name: str
    provenance: RunProvenance
    total: int
    passed: int
    failed: int
    pass_rate: float
    by_level: dict[int, dict] = Field(
        description='Breakdown by level with keys "total", "passed", "pass_rate"'
    )
    by_tag: dict[str, dict] = Field(
        description='Breakdown by tag with keys "total", "passed", "pass_rate"'
    )
    results: list[ChallengeResult]
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    # Sum of ChallengeResult.cost_usd across results with a non-None
    # value. ``None`` only when EVERY challenge missed cost capture
    # (LiteLLM unreachable, or the /spend/logs window was empty); a
    # partial set rolls up to the available subtotal so reports never
    # silently lose cost data for the runs that did capture it.
    total_cost_usd: float | None = None


class FilterConfig(BaseModel):
    """Configuration for filtering which challenges to run."""

    levels: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    ids: list[str] = Field(default_factory=list)
    range_start: int | None = None
    range_end: int | None = None


class ExploitBenchEnv(BaseModel):
    """One bug environment in an ExploitBench-style YAML config.

    Matches the shape of the ``envs:`` list in
    ``exploitbench/benchmarks/v8.yaml``. ``id`` is the bug identifier
    (``v8-cve-2024-1939``); ``image`` is the GHCR tag pulled before
    the episode starts; ``interface`` selects the MCP contract the
    container advertises (currently always ``rl.mcp.v8_exploit.v1``).
    """

    id: str
    image: str
    interface: str = "rl.mcp.v8_exploit.v1"


class ExploitBenchSpec(BaseModel):
    """Parsed ExploitBench-style YAML config.

    Only the subset of fields the Decepticon harness actually consumes
    is modeled — model dispatch and per-provider budget knobs stay
    inside Decepticon's own config (``BenchmarkConfig`` /
    ``EngagementContextMiddleware``) so the upstream config can be
    copy-pasted without diff churn.
    """

    benchmark_id: str = "exploitbench"
    envs: list[ExploitBenchEnv]
    seeds: list[int] = Field(default_factory=lambda: [1])
    init_prompt: str | None = None
    init_prompt_hint: str | None = None


class CyberGymSpec(BaseModel):
    """Parsed CyberGym YAML config.

    CyberGym (arXiv:2506.02548, UC Berkeley) scores real-world
    vulnerability reproduction: the agent analyses a vulnerable source
    tree and submits a PoC input; the upstream submission server rebuilds
    the target and records whether the PoC crashes the vulnerable build
    (``vul_exit_code``) and survives the patched build (``fix_exit_code``).

    Only the fields the Decepticon harness consumes are modeled. ``server``
    is the running ``cybergym.server`` base URL the generated ``submit.sh``
    posts to and that ``evaluate`` queries; ``data_dir`` is the local
    ``cybergym_data/data`` tree feeding ``gen_task``; ``api_key`` matches
    the server's ``CYBERGYM_API_KEY`` (private query/verify routes require
    it).
    """

    benchmark_id: str = "cybergym"
    task_ids: list[str]
    server: str
    data_dir: Path
    difficulty: str = "level1"
    mask_map_path: Path | None = None
    api_key: str = "cybergym-030a0cd7-5908-4862-8ab9-91f2bfc7b56d"
