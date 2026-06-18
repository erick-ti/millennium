#!/usr/bin/env python3
"""Read the host box's load and emit one JSON metric sample on stdout.

Runs on the HOST (NOT the backend container, which is isolated and can't see host
/proc or the host disk). The millennium-host-metrics timer pipes this into a one-off
``docker compose run --rm --no-deps -T backend python manage.py record_host_metrics``
container, which validates + persists it. Pure stdlib — nothing to install on the box.
Reads only world-readable /proc + statvfs, so it needs no privileges.
"""

from __future__ import annotations

import json
import os
import time


# The parse_* helpers take text (not a file) so they're pure + unit-testable off-box —
# the /proc column layout is the most regression-prone part of this collector, and it
# lives under infra/ where ruff/mypy/pytest don't normally reach (see
# backend/tests/test_collect_host_metrics.py).


def parse_cpu_times(stat_text: str) -> tuple[int, int]:
    """(idle, busy_total) jiffies from the aggregate ``cpu`` line of /proc/stat.

    idle folds in iowait (parts[4]). The busy total sums only parts[:8] — guest
    (parts[8]) and guest_nice (parts[9]) are ALREADY accounted inside user/nice, so
    summing them would double-count the denominator (cosmetic on a non-virtualising
    box, but wrong)."""
    for line in stat_text.splitlines():
        if line.startswith("cpu "):
            parts = [int(x) for x in line.split()[1:]]
            idle = parts[3] + (parts[4] if len(parts) > 4 else 0)  # idle + iowait
            return idle, sum(parts[:8])
    raise RuntimeError("no aggregate cpu line in /proc/stat")


def _read_cpu_times() -> tuple[int, int]:
    with open("/proc/stat") as f:
        return parse_cpu_times(f.read())


def cpu_percent(interval: float = 0.3) -> float:
    """Whole-box CPU utilisation over a short sampling interval (a single /proc/stat
    read only gives cumulative-since-boot, so we delta two reads). Clamped to [0, 100]:
    the kernel's iowait counter can move backwards on SMP, so over a ~0.3s window the
    idle delta can momentarily exceed the busy delta and yield a small negative — which
    the ingest command would otherwise reject, discarding the whole (valid) sample."""
    idle1, total1 = _read_cpu_times()
    time.sleep(interval)
    idle2, total2 = _read_cpu_times()
    total_delta = total2 - total1
    idle_delta = idle2 - idle1
    if total_delta <= 0:
        return 0.0
    pct = 100.0 * (1.0 - idle_delta / total_delta)
    return round(min(100.0, max(0.0, pct)), 1)


def parse_mem_used_total_mb(meminfo_text: str) -> tuple[int, int]:
    """(used, total) MB, using MemTotal - MemAvailable for 'used' (the figure `free`
    reports as real pressure), falling back to MemFree on a kernel without
    MemAvailable."""
    info: dict[str, int] = {}
    for line in meminfo_text.splitlines():
        key, _, rest = line.partition(":")
        if rest.strip():
            info[key] = int(rest.split()[0])  # values are in kB
    total_kb = info["MemTotal"]
    avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
    return (total_kb - avail_kb) // 1024, total_kb // 1024


def mem_used_total_mb() -> tuple[int, int]:
    with open("/proc/meminfo") as f:
        return parse_mem_used_total_mb(f.read())


def disk_used_total_gb() -> tuple[float, float]:
    """(used, total) GB for the root filesystem. used = blocks not free (matches `df`'s
    Used column); the percent the UI derives is used/total — i.e. of the FULL disk,
    including the ~5% root-reserved blocks, so it reads a touch below `df`'s Use%
    (which divides by used+available)."""
    st = os.statvfs("/")
    total = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    gb = 1024**3
    return round(used / gb, 2), round(total / gb, 2)


def parse_net_rx_tx_bytes(netdev_text: str) -> tuple[int, int]:
    """(rx, tx) cumulative bytes since boot over real interfaces. Skips loopback and
    docker/bridge/veth virtuals so container-internal traffic isn't double-counted.
    Column 0 of the post-colon fields is received bytes, column 8 transmitted."""
    rx = tx = 0
    for line in netdev_text.splitlines()[2:]:  # the first two lines are headers
        name, sep, data = line.partition(":")
        if not sep:
            continue
        iface = name.strip()
        if iface == "lo" or iface.startswith(("veth", "docker", "br-")):
            continue
        cols = data.split()
        rx += int(cols[0])  # column 0 = received bytes
        tx += int(cols[8])  # column 8 = transmitted bytes
    return rx, tx


def net_rx_tx_bytes() -> tuple[int, int]:
    with open("/proc/net/dev") as f:
        return parse_net_rx_tx_bytes(f.read())


def main() -> None:
    rx, tx = net_rx_tx_bytes()
    used_mb, total_mb = mem_used_total_mb()
    used_gb, total_gb = disk_used_total_gb()
    with open("/proc/loadavg") as f:
        load_1m = float(f.read().split()[0])
    print(
        json.dumps(
            {
                "cpu_percent": cpu_percent(),
                "load_1m": load_1m,
                "mem_used_mb": used_mb,
                "mem_total_mb": total_mb,
                "disk_used_gb": used_gb,
                "disk_total_gb": total_gb,
                "net_rx_bytes": rx,
                "net_tx_bytes": tx,
            }
        )
    )


if __name__ == "__main__":
    main()
