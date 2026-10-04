import os

os.environ.setdefault("DECEPTICON_SKIP_BOOT", "1")

from benchmark.terminal_bench.agent import DecepticonTerminalBenchAgent

__all__ = ["DecepticonTerminalBenchAgent"]
