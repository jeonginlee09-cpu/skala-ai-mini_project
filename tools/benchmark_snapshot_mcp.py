"""Benchmark-only adapter: the real MCP server with fixed sample values/time."""
import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path

source = Path(os.environ['BENCHMARK_SOURCE']) / 'mcp_server.py'
spec = importlib.util.spec_from_file_location('benchmark_ops_source', source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FixedClock:
    @classmethod
    def now(cls, tz=None):
        return datetime.fromisoformat('2026-10-21T09:00:00+09:00')


# The assignment source/data are unchanged. Only this isolated child removes jitter.
module.random.uniform = lambda low, high: 0.0
module.datetime = FixedClock
if __name__ == '__main__':
    module.mcp.run(show_banner=False)
