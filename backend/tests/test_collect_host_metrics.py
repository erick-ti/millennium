"""Unit tests for the host-side /proc parser (infra/hetzner/collect_host_metrics.py).

That collector lives under infra/ — outside the backend package, so ruff/mypy/pytest
don't reach it normally — yet its fixed /proc column indices + interface skip-filter are
the most regression-prone part of Slice 3 (an off-by-one column swap would pass CI and
only surface as wrong production numbers). The parse_* helpers take text, so we load the
module by path and feed it representative /proc fixtures.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_collector() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "infra" / "hetzner" / "collect_host_metrics.py"
    spec = importlib.util.spec_from_file_location("collect_host_metrics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = _load_collector()


def test_parse_cpu_idle_includes_iowait_and_total_excludes_guest() -> None:
    # cols: user nice system idle iowait irq softirq steal guest guest_nice
    idle, total = collector.parse_cpu_times("cpu  100 0 50 800 40 0 10 0 7 7\ncpu0 1 2 3 4")
    assert idle == 840  # idle(800) + iowait(40)
    assert total == 100 + 0 + 50 + 800 + 40 + 0 + 10 + 0  # parts[:8], NOT the guest cols


def test_parse_mem_uses_available_then_free_fallback() -> None:
    used, total = collector.parse_mem_used_total_mb(
        "MemTotal: 8000000 kB\nMemAvailable: 6000000 kB\nMemFree: 100 kB\n"
    )
    assert total == 8000000 // 1024
    assert used == (8000000 - 6000000) // 1024  # MemAvailable wins
    # No MemAvailable → fall back to MemFree.
    used2, _ = collector.parse_mem_used_total_mb("MemTotal: 8000000 kB\nMemFree: 5000000 kB\n")
    assert used2 == (8000000 - 5000000) // 1024


def test_parse_net_reads_rx_tx_columns_and_skips_virtual_interfaces() -> None:
    text = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes packets errs drop fifo frame compressed multicast|bytes packets\n"
        "    lo:  500 1 0 0 0 0 0 0 500 1\n"  # loopback — skipped
        "  eth0: 1000 1 2 3 4 5 6 7 2000 8\n"  # rx=cols[0]=1000, tx=cols[8]=2000
        "docker0: 999 1 2 3 4 5 6 7 999 1\n"  # virtual — skipped
        " br-ab:  9 1 2 3 4 5 6 7 9 1\n"  # bridge — skipped
        " veth0:  9 1 2 3 4 5 6 7 9 1\n"  # veth — skipped
    )
    rx, tx = collector.parse_net_rx_tx_bytes(text)
    assert rx == 1000  # only eth0 counted
    assert tx == 2000


def test_parse_net_counts_multiple_real_interfaces() -> None:
    text = (
        "h1\nh2\n"
        "  eth0: 1000 0 0 0 0 0 0 0 2000 0\n"
        "  ens5: 30 0 0 0 0 0 0 0 40 0\n"
    )
    rx, tx = collector.parse_net_rx_tx_bytes(text)
    assert rx == 1030
    assert tx == 2040
