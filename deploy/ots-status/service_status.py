"""Per-service status for the OpenTAKServer dashboard.

Installed into OpenTAKServer's package by deploy/ots-status/apply.py and
merged into /api/status as a "services" list. Stock OTS 1.7.x has no way to
show whether its own processes (eud_handler, cot_parser, ...) or our feeders
are running, and its CPU gauge only measures the web process.

Status reads systemd and cgroup v2 files, so it needs no root and never opens
a connection to a TAK port (connecting to 8088 would register as a client with
eud_handler). control() backs the dashboard's Start/Stop/Restart buttons and
needs the polkit rule in cbu-service-control.rules.
"""

import os
import subprocess
import time

import psutil

# (systemd unit, label shown on the dashboard)
SERVICES = [
    ("opentakserver.service", "OpenTAKServer web/API"),
    ("eud_handler.service", "TAK client listener (TCP)"),
    ("eud_handler_ssl.service", "TAK client listener (SSL)"),
    ("cot_parser.service", "CoT parser"),
    ("rabbitmq-server.service", "RabbitMQ message bus"),
    ("postgresql@16-main.service", "PostgreSQL database"),
    ("nginx.service", "nginx web proxy"),
    ("cbu-adsb-tak.service", "Feeder: CBU aircraft (ADS-B)"),
    ("cbu-transit-tak.service", "Feeder: RTA Route 1 buses"),
    ("cbu-weather-tak.service", "Feeder: KRAL weather"),
    ("cbu-webmap.service", "WebTAK map"),
]

# Units the dashboard can restart but not stop: stopping any of them takes the
# dashboard down too (opentakserver.service Requires= the eud handlers and
# cot_parser, and every request goes through nginx and postgres), leaving no
# button to start them again.
RESTART_ONLY = {
    "opentakserver.service",
    "eud_handler.service",
    "eud_handler_ssl.service",
    "cot_parser.service",
    "postgresql@16-main.service",
    "nginx.service",
}

# Shown in the confirm dialog before a restart.
_DROPOUT = "The dashboard will drop out for a few seconds while it comes back."
NOTES = {
    "opentakserver.service": _DROPOUT,
    "eud_handler.service": "OpenTAKServer depends on this, so it restarts too. " + _DROPOUT,
    "eud_handler_ssl.service": "OpenTAKServer depends on this, so it restarts too. " + _DROPOUT,
    "cot_parser.service": "OpenTAKServer depends on this, so it restarts too. " + _DROPOUT,
    "postgresql@16-main.service": "OpenTAKServer loses its database until it's back. " + _DROPOUT,
    "nginx.service": _DROPOUT,
    "rabbitmq-server.service": "TAK clients stop receiving CoT until it's back.",
}

ACTIONS = ("start", "stop", "restart")

CGROUP_ROOT = "/sys/fs/cgroup"
CPU_COUNT = os.cpu_count() or 1

# unit -> (monotonic time, cpu usage_usec) from the previous call, so CPU % is
# the average since the dashboard last polled.
_last_cpu = {}


def _systemctl_show():
    units = [u for u, _ in SERVICES]
    out = subprocess.run(
        ["systemctl", "show", "--timestamp=unix",
         "-p", "Id,ActiveState,SubState,ActiveEnterTimestamp,NRestarts,ControlGroup,MainPID",
         *units],
        capture_output=True, text=True, timeout=5,
    ).stdout
    info = {}
    for block in out.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if "Id" in props:
            info[props["Id"]] = props
    return info


def _read_cgroup(cgroup, name):
    try:
        with open(os.path.join(CGROUP_ROOT + cgroup, name)) as f:
            return f.read()
    except (OSError, TypeError):
        return None


def _cpu_usec(cgroup):
    stat = _read_cgroup(cgroup, "cpu.stat")
    if not stat:
        return None
    for line in stat.splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            return int(value)
    return None


def _memory_bytes(cgroup):
    # "anon" is the process memory itself, without the page cache that
    # memory.current also counts.
    stat = _read_cgroup(cgroup, "memory.stat")
    if not stat:
        return None
    for line in stat.splitlines():
        key, _, value = line.partition(" ")
        if key == "anon":
            return int(value)
    return None


def _pids(cgroup):
    procs = _read_cgroup(cgroup, "cgroup.procs")
    return {int(p) for p in procs.split()} if procs else set()


def _listening_ports():
    # pid is only visible for our own processes without root, so map by pid
    # where we can and fall back to the known port list below otherwise.
    ports = {}
    try:
        for c in psutil.net_connections(kind="tcp"):
            if c.status == psutil.CONN_LISTEN and c.laddr:
                ports.setdefault(c.laddr.port, set())
                if c.pid:
                    ports[c.laddr.port].add(c.pid)
    except psutil.Error:
        pass
    return ports


# Ports each service is expected to listen on, checked against what's open.
EXPECTED_PORTS = {
    "opentakserver.service": [8081],
    "eud_handler.service": [8088],
    "eud_handler_ssl.service": [8089],
    "rabbitmq-server.service": [5672],
    "postgresql@16-main.service": [5432],
    "nginx.service": [8080, 8443, 8444, 8446],
    "cbu-webmap.service": [8090],
}


def collect():
    now = time.monotonic()
    wall = time.time()
    info = _systemctl_show()
    listening = _listening_ports()

    services = []
    for unit, label in SERVICES:
        props = info.get(unit, {})
        cgroup = props.get("ControlGroup") or None
        active = props.get("ActiveState", "unknown")

        uptime = None
        entered = props.get("ActiveEnterTimestamp", "").lstrip("@")
        if active == "active" and entered.isdigit():
            uptime = max(0, int(wall - int(entered)))

        cpu_percent = None
        usec = _cpu_usec(cgroup) if cgroup else None
        if usec is not None:
            prev = _last_cpu.get(unit)
            if prev and now > prev[0]:
                cpu_percent = round(
                    (usec - prev[1]) / 1e6 / (now - prev[0]) / CPU_COUNT * 100, 1
                )
            _last_cpu[unit] = (now, usec)

        pids = _pids(cgroup) if cgroup else set()
        ports = []
        for port in EXPECTED_PORTS.get(unit, []):
            owners = listening.get(port)
            ports.append({"port": port, "listening": owners is not None})
        # Also report any other ports we can see this service holding.
        for port, owners in listening.items():
            if owners & pids and port not in EXPECTED_PORTS.get(unit, []):
                ports.append({"port": port, "listening": True})

        services.append({
            "unit": unit,
            "name": label,
            "active_state": active,
            "sub_state": props.get("SubState", ""),
            "running": active == "active",
            "uptime": uptime,
            "restarts": int(props["NRestarts"]) if props.get("NRestarts", "").isdigit() else None,
            "processes": len(pids),
            "cpu_percent": cpu_percent,
            "memory": _memory_bytes(cgroup) if cgroup else None,
            "ports": sorted(ports, key=lambda p: p["port"]),
            "actions": [a for a in ACTIONS if a != "stop" or unit not in RESTART_ONLY],
            "note": NOTES.get(unit, ""),
        })
    return services


def control(unit, action):
    """Start, stop or restart one of SERVICES.

    Needs the polkit rule in cbu-service-control.rules, since OpenTAKServer
    runs unprivileged. --no-block queues the job and returns, so restarting
    opentakserver itself (or a unit it Requires=) can still answer the request
    that asked for it.
    """
    if unit not in dict(SERVICES):
        raise ValueError("unknown service: {}".format(unit))
    if action not in ACTIONS:
        raise ValueError("unknown action: {}".format(action))
    if action == "stop" and unit in RESTART_ONLY:
        raise ValueError("{} can only be restarted from the dashboard".format(unit))
    result = subprocess.run(
        ["systemctl", "--no-block", "--no-ask-password", action, unit],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "systemctl exited {}".format(result.returncode))


def system_cpu_percent():
    # Whole-machine CPU since the previous call (psutil keeps the baseline).
    return psutil.cpu_percent(interval=None)


def safe_collect():
    try:
        return collect()
    except Exception as e:  # never break the stock dashboard
        return [{"unit": "error", "name": "Service status unavailable: {}".format(e),
                 "active_state": "unknown", "sub_state": "", "running": False,
                 "uptime": None, "restarts": None, "processes": 0,
                 "cpu_percent": None, "memory": None, "ports": [],
                 "actions": [], "note": ""}]
