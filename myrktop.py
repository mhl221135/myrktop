#!/usr/bin/env python3
"""
myrktop — Orange Pi 5 (RK3588) System Monitor
Enhancements:
  - ASCII progress bars for CPU / GPU / RAM
  - Top-5 CPU process list
  - Tiered refresh intervals (SMART cached 60s, disk 5s, RAM 1s, CPU 0.5s)
  - Background thread for slow SMART queries
  - Per-drive I/O (read/write MB/s + IOPS from /proc/diskstats)
  - Hardware rows (GPU/NPU/RGA) hidden when not present on the board
  - 'r' toggles refresh speed, 'h' shows help overlay, 'q' quits
  - No duplicate class; no subprocess for RAM/disk reads
"""

import urwid
import subprocess
import threading
import re
import os
import time

# ─────────────────────────────────────────────
# Module-level state (replaces naked globals)
# ─────────────────────────────────────────────
prev_cpu: dict = {}
prev_net: dict = {}
prev_disk_io: dict = {}

# Tiered refresh cache
_cache: dict = {}
_cache_lock = threading.Lock()

# Background SMART thread
_smart_lock = threading.Lock()
_smart_cache: dict = {"nvme": [], "ata": [], "ts": 0.0}
_smart_thread_running = False

SMART_TTL     = 60    # seconds between SMART refreshes
DISK_TTL      = 5     # seconds between fstab/disk refreshes
SLOW_TTL      = 5     # seconds between device-info / docker refreshes
TEMP_TTL      = 2     # seconds between temperature refreshes
NET_RAM_TTL   = 1     # seconds between RAM / net refreshes
CPU_TTL       = 0.5   # seconds between CPU refreshes (controlled by event loop)

# Refresh rate steps (seconds)
REFRESH_STEPS = [0.5, 1.0, 2.0]
refresh_idx   = 0     # index into REFRESH_STEPS

# Help overlay toggle
show_help     = False

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def make_bar(percent: int, width: int = 20) -> str:
    """Return an ASCII progress bar string of given width for a 0–100 percent value."""
    percent = max(0, min(100, percent))
    filled  = round(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


def _now() -> float:
    return time.monotonic()


def _cache_get(key: str, ttl: float):
    """Return (value, hit) where hit=True when the cached value is still fresh."""
    with _cache_lock:
        entry = _cache.get(key)
    if entry and (_now() - entry["ts"]) < ttl:
        return entry["value"], True
    return None, False


def _cache_set(key: str, value):
    with _cache_lock:
        _cache[key] = {"value": value, "ts": _now()}

# ─────────────────────────────────────────────
# System Info — Static / slow-changing
# ─────────────────────────────────────────────

def _fetch_device_info():
    try:
        device_info = subprocess.check_output(
            "cat /sys/firmware/devicetree/base/compatible",
            shell=True, stderr=subprocess.DEVNULL
        ).decode("utf-8").replace("\x00", "").strip()
    except Exception:
        device_info = "N/A"

    npu_version = ""
    npu_version_path = "/sys/kernel/debug/rknpu/version"
    if os.path.exists(npu_version_path):
        try:
            with open(npu_version_path, "r") as f:
                npu_version = f.read().strip()
        except PermissionError:
            npu_version = "Permission denied — try sudo"
        except Exception:
            npu_version = ""

    try:
        uptime = subprocess.check_output("uptime -p", shell=True, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        uptime = "N/A"

    docker_status = ""
    try:
        status = subprocess.check_output(
            "systemctl is-active docker", shell=True, stderr=subprocess.PIPE
        ).decode().strip()
        if status == "active":
            docker_status = "active"
    except Exception:
        docker_status = ""

    return device_info, npu_version, uptime, docker_status


def get_device_info():
    cached, hit = _cache_get("device_info", SLOW_TTL)
    if hit:
        return cached
    val = _fetch_device_info()
    _cache_set("device_info", val)
    return val

# ─────────────────────────────────────────────
# CPU
# ─────────────────────────────────────────────

def get_cpu_info():
    global prev_cpu
    cpu_loads  = {}
    cpu_freqs  = {}
    core_count = os.cpu_count() or 1

    try:
        with open("/proc/stat", "r") as f:
            lines = f.readlines()
    except Exception:
        lines = []

    for i in range(core_count):
        line = next((l for l in lines if l.startswith(f"cpu{i} ")), None)
        if not line:
            cpu_loads[i] = 0
            cpu_freqs[i] = 0
            continue
        parts = line.split()
        try:
            user    = int(parts[1])
            nice    = int(parts[2])
            system  = int(parts[3])
            idle    = int(parts[4])
            iowait  = int(parts[5])
            irq     = int(parts[6])
            softirq = int(parts[7])
            steal   = int(parts[8]) if len(parts) > 8 else 0
        except Exception:
            cpu_loads[i] = 0
            cpu_freqs[i] = 0
            continue

        total = user + nice + system + idle + iowait + irq + softirq + steal
        if i in prev_cpu:
            prev_total, prev_idle = prev_cpu[i]
            diff_total = total - prev_total
            diff_idle  = idle  - prev_idle
            load = (100 * (diff_total - diff_idle)) // diff_total if diff_total > 0 else 0
        else:
            load = 0
        cpu_loads[i]  = max(0, min(100, load))
        prev_cpu[i]   = (total, idle)

        try:
            with open(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq", "r") as f:
                cpu_freqs[i] = int(f.read().strip()) // 1000
        except Exception:
            cpu_freqs[i] = 0

    return cpu_loads, cpu_freqs

# ─────────────────────────────────────────────
# GPU / NPU / RGA
# ─────────────────────────────────────────────

def get_gpu_info():
    load_path = "/sys/class/devfreq/fb000000.gpu/load"
    freq_path = "/sys/class/devfreq/fb000000.gpu/cur_freq"
    if not os.path.exists(load_path) or not os.path.exists(freq_path):
        return None, None
    try:
        with open(load_path, "r") as f:
            raw = f.read().strip()
        fields    = re.split(r"[@ ]+", raw)
        gpu_load  = int(fields[0].rstrip("%"))
    except Exception:
        gpu_load = 0
    try:
        with open(freq_path, "r") as f:
            gpu_freq = int(f.read().strip()) // 1_000_000
    except Exception:
        gpu_freq = 0
    return gpu_load, gpu_freq


def get_npu_info():
    load_path = "/sys/kernel/debug/rknpu/load"
    freq_path = "/sys/class/devfreq/fdab0000.npu/cur_freq"
    if not os.path.exists(load_path) or not os.path.exists(freq_path):
        return None, None
    try:
        with open(load_path, "r") as f:
            data = f.read()
        percents = re.findall(r"(\d+)%", data)
        npu_load = " ".join(p + "%" for p in percents) if percents else "0% 0% 0%"
    except Exception:
        npu_load = "0% 0% 0%"
    try:
        with open(freq_path, "r") as f:
            npu_freq = int(f.read().strip()) // 1_000_000
    except Exception:
        npu_freq = 0
    return npu_load, npu_freq


def get_rga_info():
    path = "/sys/kernel/debug/rkrga/load"
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = f.read()
        values = re.findall(r"load = (\d+)%", data)
        return " ".join(v + "%" for v in values[:3]) if values else "0% 0% 0%"
    except Exception:
        return "0% 0% 0%"

# ─────────────────────────────────────────────
# RAM / Swap — read /proc/meminfo directly
# ─────────────────────────────────────────────

def _parse_meminfo_kb(meminfo: str, key: str) -> int:
    m = re.search(rf"^{key}:\s+(\d+)", meminfo, re.MULTILINE)
    return int(m.group(1)) if m else 0


def _fmt_kb(kb: int) -> str:
    if kb >= 1_048_576:
        return f"{kb / 1_048_576:.1f}G"
    if kb >= 1024:
        return f"{kb / 1024:.1f}M"
    return f"{kb}K"


def get_ram_swap_info():
    cached, hit = _cache_get("ram_swap", NET_RAM_TTL)
    if hit:
        return cached
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = f.read()
        mem_total    = _parse_meminfo_kb(meminfo, "MemTotal")
        mem_free     = _parse_meminfo_kb(meminfo, "MemFree")
        mem_buffers  = _parse_meminfo_kb(meminfo, "Buffers")
        mem_cached   = _parse_meminfo_kb(meminfo, "Cached")
        mem_sreclam  = _parse_meminfo_kb(meminfo, "SReclaimable")
        mem_used     = mem_total - mem_free - mem_buffers - mem_cached - mem_sreclam
        swap_total   = _parse_meminfo_kb(meminfo, "SwapTotal")
        swap_free    = _parse_meminfo_kb(meminfo, "SwapFree")
        swap_used    = swap_total - swap_free
        ram_pct      = int(100 * mem_used  / mem_total)  if mem_total  > 0 else 0
        swap_pct     = int(100 * swap_used / swap_total) if swap_total > 0 else 0
        val = (
            _fmt_kb(mem_used),  _fmt_kb(mem_total),  ram_pct,
            _fmt_kb(swap_used), _fmt_kb(swap_total), swap_pct,
        )
    except Exception:
        val = ("N/A", "N/A", 0, "N/A", "N/A", 0)
    _cache_set("ram_swap", val)
    return val

# ─────────────────────────────────────────────
# Temperatures
# ─────────────────────────────────────────────

def get_temperatures():
    cached, hit = _cache_get("temps", TEMP_TTL)
    if hit:
        return cached
    try:
        output = subprocess.check_output(
            "sensors", shell=True, stderr=subprocess.DEVNULL
        ).decode()
        lines        = output.splitlines()
        temp_items   = []
        current_name = None
        for line in lines:
            if ":" not in line and len(line.split()) == 1:
                current_name = line.strip()
                continue
            if line.startswith("temp1:") or line.startswith("Composite:"):
                fields = line.split()
                if len(fields) < 2:
                    continue
                raw_temp = fields[1]
                if len(raw_temp) >= 5:
                    try:
                        temp_val = int(float(raw_temp[1:len(raw_temp) - 4]))
                    except Exception:
                        temp_val = 0
                    attr = "temp_red" if temp_val >= 70 else ("temp_yellow" if temp_val >= 60 else "temp_green")
                    sensor_name = current_name if current_name is not None else fields[0]
                    temp_items.append((attr, f"{sensor_name:<30} {temp_val:2d}°C"))
                else:
                    temp_items.append(("default", line))
        if not temp_items:
            temp_items = [("default", "No temperature data.")]
    except Exception:
        temp_items = [("default", "No temperature data.")]
    _cache_set("temps", temp_items)
    return temp_items

# ─────────────────────────────────────────────
# Network Traffic
# ─────────────────────────────────────────────

def get_network_traffic():
    global prev_net
    cached, hit = _cache_get("net", NET_RAM_TTL)
    if hit:
        return cached

    net_class  = "/sys/class/net"
    interfaces = []
    try:
        for iface in os.listdir(net_class):
            if os.path.exists(os.path.join(net_class, iface, "device")):
                interfaces.append(iface)
    except Exception:
        pass

    net_stats = {}
    try:
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()
    except Exception:
        lines = []
    for line in lines[2:]:
        if ":" not in line:
            continue
        iface_name, rest = line.split(":", 1)
        iface_name = iface_name.strip()
        if iface_name in interfaces:
            parts = rest.split()
            try:
                net_stats[iface_name] = (int(parts[0]), int(parts[8]))
            except Exception:
                net_stats[iface_name] = (0, 0)

    current_time = time.time()
    rates = {}
    for iface in interfaces:
        rx, tx = net_stats.get(iface, (0, 0))
        if iface in prev_net:
            prev_rx, prev_tx, prev_time = prev_net[iface]
            dt = current_time - prev_time
            if dt > 0:
                rx_rate = (rx - prev_rx) * 8 / (1e6 * dt)
                tx_rate = (tx - prev_tx) * 8 / (1e6 * dt)
            else:
                rx_rate = tx_rate = 0.0
        else:
            rx_rate = tx_rate = 0.0
        rates[iface] = (max(0.0, rx_rate), max(0.0, tx_rate))
        prev_net[iface] = (rx, tx, current_time)

    _cache_set("net", rates)
    return rates

# ─────────────────────────────────────────────
# Disk Usage — os.statvfs() instead of df
# ─────────────────────────────────────────────

def get_fstab_disk_usage():
    cached, hit = _cache_get("disk_usage", DISK_TTL)
    if hit:
        return cached

    mountpoints = []
    try:
        with open("/etc/fstab", "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                fields = line.split()
                if len(fields) < 2:
                    continue
                mount = fields[1]
                if mount != "/tmp" and mount not in mountpoints:
                    mountpoints.append(mount)
    except Exception:
        pass

    header       = f"{'Mount Point':<20} {'Total':>8} {'Used':>8} {'Free':>8} {'Use%':>5}"
    usage_lines  = [header]
    for m in mountpoints:
        try:
            st         = os.statvfs(m)
            total_kb   = st.f_blocks * st.f_frsize // 1024
            free_kb    = st.f_bfree  * st.f_frsize // 1024
            avail_kb   = st.f_bavail * st.f_frsize // 1024
            used_kb    = total_kb - free_kb
            pct        = int(100 * used_kb / total_kb) if total_kb > 0 else 0
            usage_lines.append(
                f"{m:<20} {_fmt_kb(total_kb):>8} {_fmt_kb(used_kb):>8} {_fmt_kb(avail_kb):>8} {pct:>4}%"
            )
        except Exception:
            usage_lines.append(f"{m:<20} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'?':>5}")

    if len(usage_lines) == 1:
        usage_lines.append("No disk usage info from /etc/fstab.")
    _cache_set("disk_usage", usage_lines)
    return usage_lines

# ─────────────────────────────────────────────
# Per-Drive I/O — read /proc/diskstats
# ─────────────────────────────────────────────

def get_drive_io():
    """Return per-physical-disk read/write MB/s and IOPS from /proc/diskstats."""
    global prev_disk_io

    stats = {}
    try:
        with open("/proc/diskstats", "r") as f:
            lines = f.readlines()
    except Exception:
        return {}

    for line in lines:
        fields = line.split()
        if len(fields) < 14:
            continue
        name        = fields[2]
        reads       = int(fields[3])
        sectors_rd  = int(fields[5])
        writes      = int(fields[7])
        sectors_wr  = int(fields[9])
        if not re.match(r"^(sd[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+|vd[a-z]+|nvme[0-9]+|hd[a-z]+)$", name):
            continue
        stats[name] = (reads, sectors_rd, writes, sectors_wr)

    now    = time.monotonic()
    result = {}
    for name, (reads, srd, writes, swr) in stats.items():
        if name in prev_disk_io:
            pr, psrd, pwr, pswr, pnow = prev_disk_io[name]
            dt = now - pnow
            if dt > 0:
                rd_mb = (srd - psrd) * 512 / (1e6 * dt)
                wr_mb = (swr - pswr) * 512 / (1e6 * dt)
                rd_iops = max(0, reads - pr) / dt
                wr_iops = max(0, writes - pwr) / dt
            else:
                rd_mb = wr_mb = rd_iops = wr_iops = 0.0
        else:
            rd_mb = wr_mb = rd_iops = wr_iops = 0.0
        result[name] = (max(0.0, rd_mb), max(0.0, wr_mb), rd_iops, wr_iops)
        prev_disk_io[name] = (reads, srd, writes, swr, now)

    return result

# ─────────────────────────────────────────────
# Top Processes
# ─────────────────────────────────────────────

def get_top_processes(n: int = 5):
    cached, hit = _cache_get("top_procs", TEMP_TTL)
    if hit:
        return cached

    procs = []
    proc_path = "/proc"
    try:
        for pid in os.listdir(proc_path):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/stat", "r") as f:
                    stat = f.read().split()
                # comm is in parentheses at index 1
                comm   = stat[1].strip("()")
                utime  = int(stat[13])
                stime  = int(stat[14])
                cpu_t  = utime + stime

                with open(f"/proc/{pid}/status", "r") as f:
                    status = f.read()
                m = re.search(r"^VmRSS:\s+(\d+)", status, re.MULTILINE)
                rss_kb  = int(m.group(1)) if m else 0
                procs.append((cpu_t, int(pid), comm, rss_kb))
            except Exception:
                continue
    except Exception:
        pass

    procs.sort(reverse=True)
    result = [(pid, comm, cpu_t, rss_kb) for cpu_t, pid, comm, rss_kb in procs[:n]]
    _cache_set("top_procs", result)
    return result

# ─────────────────────────────────────────────
# SMART Storage — background thread with cache
# ─────────────────────────────────────────────

def _run_all_smartctl(dev):
    commands = [
        f"sudo smartctl -a -d auto /dev/{dev}",
        f"sudo smartctl -a -d sat /dev/{dev}",
        f"sudo smartctl -a /dev/{dev}",
    ]
    results = {}
    for cmd in commands:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            results[cmd] = (r.stdout + "\n" + r.stderr).strip()
        except Exception as e:
            results[cmd] = f"Exception: {e}"
    return results


def _run_smartctl(dev):
    all_results = _run_all_smartctl(dev)
    for cmd, output in all_results.items():
        if output and "Usage:" not in output[:50] and "Unknown USB bridge" not in output:
            return output, cmd, all_results
    last_cmd = list(all_results)[-1] if all_results else "None"
    return all_results.get(last_cmd, "No output"), last_cmd, all_results


def _parse_nvme_info(output):
    def _search(pattern, text, fallback="N/A"):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else fallback

    return {
        "model":        _search(r"Model Number:\s+(.*)",             output),
        "temp":         _search(r"Temperature Sensor 1:\s+(\d+)",    output),
        "power_hours":  _search(r"Power On Hours:\s+([\d,]+)",       output).replace(",", ""),
        "avail_spare":  _search(r"Available Spare:\s+(\d+)%",        output) + "%"
                        if re.search(r"Available Spare:", output) else "N/A",
    }


def _parse_ata_info(output):
    info = {}
    m = re.search(r"Device Model:\s+(.*)", output)
    info["model"] = m.group(1).strip() if m else "Unknown"

    def _last_digit_token(line_keyword):
        for line in output.splitlines():
            if line_keyword in line:
                for tok in reversed(line.split()):
                    if tok.isdigit():
                        return tok
        return "N/A"

    info["power_hours"] = _last_digit_token("Power_On_Hours")
    info["temp"]        = _last_digit_token("emperature")  # matches Temperature_Celsius

    for line in output.splitlines():
        if "Wear_Leveling_Count" in line:
            for tok in reversed(line.split()):
                if tok.isdigit():
                    info["wear_level"] = tok
                    break
            break
    info.setdefault("wear_level", None)

    for line in output.splitlines():
        if "Rotation Rate:" in line:
            info["rotation"] = (
                None if "solid state" in line.lower()
                else re.search(r"Rotation Rate:\s+(.*)", line).group(1).strip()
            )
            break
    info.setdefault("rotation", None)
    return info


def _get_drive_smart_info(dev):
    output, used_cmd, all_outputs = _run_smartctl(dev)
    if not output or output.startswith("Error:"):
        return ("unknown", {"model": "Unknown", "temp": "N/A", "power_hours": "N/A", "avail_spare": "N/A"})
    if dev.startswith("nvme") or re.search(r"NVMe Version:", output, re.IGNORECASE):
        return ("nvme", _parse_nvme_info(output))
    info = _parse_ata_info(output)
    if info.get("model", "Unknown") == "Unknown":
        return ("unknown", info)
    return ("ata", info)


def _smart_line(dev, dtype, info):
    line = f"/dev/{dev} — {info.get('model', 'Unknown')}"
    if info.get("temp") not in (None, "N/A"):
        line += f"  {info['temp']}°C"
    if info.get("power_hours") not in (None, "N/A"):
        line += f"  {info['power_hours']}H"
    if dtype == "nvme" and info.get("avail_spare") not in (None, "N/A"):
        line += f"  spare:{info['avail_spare']}"
    if dtype == "ata":
        if info.get("rotation"):
            line += f"  {info['rotation']}"
        if info.get("wear_level"):
            line += f"  wear:{info['wear_level']}%"
    return line


def _fetch_smart_data():
    """Blocking SMART fetch — runs in background thread."""
    nvme_list, ata_list = [], []
    try:
        lsblk = subprocess.check_output(
            "lsblk -dno NAME", shell=True, stderr=subprocess.DEVNULL
        ).decode()
        devices = [
            l.strip() for l in lsblk.splitlines()
            if re.match(r"^sd[a-z]+$", l.strip()) or l.strip().startswith("nvme")
        ]
    except Exception:
        devices = []

    for dev in devices:
        dtype, info = _get_drive_smart_info(dev)
        line = _smart_line(dev, dtype, info)
        if dtype == "nvme":
            nvme_list.append(line)
        elif dtype == "ata":
            ata_list.append(line)
        else:
            nvme_list.append(f"/dev/{dev} — SMART unavailable")

    with _smart_lock:
        _smart_cache["nvme"] = nvme_list
        _smart_cache["ata"]  = ata_list
        _smart_cache["ts"]   = _now()


def _maybe_refresh_smart():
    """Spawn a background thread to refresh SMART data if stale."""
    global _smart_thread_running
    with _smart_lock:
        if _smart_thread_running:
            return
        if (_now() - _smart_cache["ts"]) < SMART_TTL:
            return
        _smart_thread_running = True

    def _worker():
        global _smart_thread_running
        try:
            _fetch_smart_data()
        finally:
            with _smart_lock:
                _smart_thread_running = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def get_storage_info():
    _maybe_refresh_smart()
    with _smart_lock:
        return list(_smart_cache["nvme"]), list(_smart_cache["ata"])

# ─────────────────────────────────────────────
# Dashboard Builder
# ─────────────────────────────────────────────

SEP = "─" * 52


def _load_attr(pct: int) -> str:
    return "bar_red" if pct >= 80 else ("bar_yellow" if pct >= 60 else "bar_green")


def build_dashboard():
    lines = []

    # ── Header ──────────────────────────────────
    lines.append(("header", SEP))
    lines.append(("header", "🔥  myrktop — System Monitor"))
    lines.append(("header", SEP))

    device_info, npu_version, uptime, docker_status = get_device_info()
    lines.append(("default", f"Device : {device_info}"))
    if npu_version:
        lines.append(("default", f"NPU Ver: {npu_version}"))
    lines.append(("default", f"Uptime : {uptime}"))
    if docker_status == "active":
        lines.append(("good", "Docker : Running ✅"))
    elif docker_status:
        lines.append(("bad", f"Docker : {docker_status}"))

    # ── CPU ─────────────────────────────────────
    lines.append(("header", SEP))
    lines.append(("title", "📊 CPU Usage & Frequency"))
    cpu_loads, cpu_freqs = get_cpu_info()
    cores = sorted(cpu_loads.keys())
    for i in cores:
        pct  = cpu_loads[i]
        freq = cpu_freqs[i]
        attr = _load_attr(pct)
        bar  = make_bar(pct, 16)
        markup = [
            ("label", f"Core {i:>2}: "),
            (attr,    bar),
            ("label", f" {pct:3d}%  "),
            ("freq",  f"{freq:4d} MHz"),
        ]
        lines.append(markup)

    # ── GPU ─────────────────────────────────────
    gpu_load, gpu_freq = get_gpu_info()
    if gpu_load is not None:
        attr = _load_attr(gpu_load)
        bar  = make_bar(gpu_load, 16)
        lines.append([
            ("title", "🎮 GPU  : "),
            (attr,    bar),
            ("label", f" {gpu_load:3d}%  "),
            ("freq",  f"{gpu_freq:4d} MHz"),
        ])

    # ── NPU ─────────────────────────────────────
    npu_load, npu_freq = get_npu_info()
    if npu_load is not None:
        try:
            npu_pct = int(re.search(r"(\d+)%", npu_load).group(1))
        except Exception:
            npu_pct = 0
        attr = _load_attr(npu_pct)
        lines.append([
            ("title", "🧠 NPU  : "),
            (attr,    npu_load),
            ("freq",  f"   {npu_freq:4d} MHz"),
        ])

    # ── RGA ─────────────────────────────────────
    rga_info = get_rga_info()
    if rga_info is not None:
        try:
            rga_pct = int(re.search(r"(\d+)%", rga_info).group(1))
        except Exception:
            rga_pct = 0
        attr = _load_attr(rga_pct)
        lines.append([("title", "🖼️  RGA  : "), (attr, rga_info)])

    # ── RAM & Swap ──────────────────────────────
    lines.append(("header", SEP))
    ram_used, ram_total, ram_pct, swap_used, swap_total, swap_pct = get_ram_swap_info()
    lines.append(("title", "🖥️  RAM & Swap"))
    ram_attr  = _load_attr(ram_pct)
    swap_attr = _load_attr(swap_pct)
    lines.append([
        ("label", "RAM  : "),
        (ram_attr, make_bar(ram_pct, 16)),
        ("label",  f" {ram_pct:3d}%  {ram_used} / {ram_total}"),
    ])
    lines.append([
        ("label", "Swap : "),
        (swap_attr, make_bar(swap_pct, 16)),
        ("label",   f" {swap_pct:3d}%  {swap_used} / {swap_total}"),
    ])

    # ── Temperatures ────────────────────────────
    lines.append(("header", SEP))
    lines.append(("title", "🌡️  Temperatures"))
    for attr, text in get_temperatures():
        lines.append((attr, text))

    # ── Network ─────────────────────────────────
    lines.append(("header", SEP))
    lines.append(("title", "🌐 Network Traffic"))
    rates = get_network_traffic()
    if rates:
        for iface, (rx_rate, tx_rate) in rates.items():
            lines.append(("default", f"{iface:<12}  ↓ {rx_rate:7.2f} Mbps   ↑ {tx_rate:7.2f} Mbps"))
    else:
        lines.append(("dim", "No active network interfaces."))

    # ── Disk Usage ──────────────────────────────
    lines.append(("header", SEP))
    lines.append(("title", "💾 Storage Usage (/etc/fstab)"))
    for d in get_fstab_disk_usage():
        lines.append(("default", d))

    # ── Drive I/O ───────────────────────────────
    drive_io = get_drive_io()
    if drive_io:
        lines.append(("header", SEP))
        lines.append(("title", "📈 Drive I/O"))
        lines.append(("label", f"  {'Drive':<12} {'Read':>9} {'Write':>9} {'Rd IOPS':>8} {'Wr IOPS':>8}"))
        for name, (rd_mb, wr_mb, rd_iops, wr_iops) in drive_io.items():
            lines.append(("default",
                f"  {name:<12} {rd_mb:8.2f}M {wr_mb:8.2f}M {rd_iops:8.0f} {wr_iops:8.0f}"))

    # ── SMART Info ──────────────────────────────
    lines.append(("header", SEP))
    nvme_info, ata_info = get_storage_info()
    smart_loading = _smart_cache["ts"] == 0.0
    if smart_loading:
        lines.append(("dim", "SMART: Loading in background…"))
    else:
        if nvme_info:
            lines.append(("good", "NVMe Devices:"))
            for info in nvme_info:
                lines.append(("default", info))
        if ata_info:
            lines.append(("good", "ATA Devices:"))
            for info in ata_info:
                lines.append(("default", info))
        if not nvme_info and not ata_info:
            lines.append(("bad", "No SMART devices detected."))

    # ── Top Processes ───────────────────────────
    lines.append(("header", SEP))
    lines.append(("title", "⚡ Top Processes (by CPU time)"))
    procs = get_top_processes(5)
    if procs:
        lines.append(("label", f"  {'PID':>6}  {'Name':<18}  {'CPU Ticks':>10}  {'RSS':>8}"))
        for pid, comm, cpu_t, rss_kb in procs:
            lines.append(("default", f"  {pid:>6}  {comm:<18}  {cpu_t:>10}  {_fmt_kb(rss_kb):>8}"))
    else:
        lines.append(("dim", "  (no process data)"))

    # ── Footer ──────────────────────────────────
    lines.append(("header", SEP))
    refresh_ms = int(REFRESH_STEPS[refresh_idx] * 1000)
    lines.append(("footer", f"[q] Quit   [r] Refresh {refresh_ms}ms   [h] Help   ↑↓ Scroll"))
    return lines


def build_help():
    """Return urwid markup rows for the help overlay."""
    lines = []
    lines.append(("header", SEP))
    lines.append(("title",  "  ⌨️  Keyboard Shortcuts"))
    lines.append(("header", SEP))
    lines.append(("default", "  q / Q     Quit myrktop"))
    lines.append(("default", "  r / R     Cycle refresh rate  (0.5s → 1s → 2s)"))
    lines.append(("default", "  h / H     Toggle this help overlay"))
    lines.append(("default", "  ↑ / ↓     Scroll dashboard"))
    lines.append(("default", "  PgUp/Dn   Page scroll"))
    lines.append(("header", SEP))
    lines.append(("dim",    "  SMART data refreshes automatically every 60 s"))
    lines.append(("dim",    "  Temperatures every 2 s, Disk every 5 s"))
    lines.append(("header", SEP))
    lines.append(("footer", "  Press [h] to close help"))
    return lines

# ─────────────────────────────────────────────
# Urwid Widget
# ─────────────────────────────────────────────

palette = [
    ("header",     "dark blue,bold",   ""),
    ("title",      "yellow,bold",      ""),
    ("label",      "light gray,bold",  ""),
    ("default",    "white",            ""),
    ("good",       "dark green,bold",  ""),
    ("bad",        "dark red,bold",    ""),
    ("dim",        "dark gray",        ""),
    ("bar_red",    "light red,bold",   ""),
    ("bar_yellow", "yellow,bold",      ""),
    ("bar_green",  "light green,bold", ""),
    ("temp_red",   "light red,bold",   ""),
    ("temp_yellow","yellow,bold",      ""),
    ("temp_green", "light green,bold", ""),
    ("freq",       "light cyan,bold",  ""),
    ("footer",     "dark gray",        ""),
]


class DashboardWidget(urwid.ListBox):
    def __init__(self):
        self.walker = urwid.SimpleListWalker([])
        super().__init__(self.walker)
        self.update_content()

    def update_content(self):
        _, focus_pos = self.get_focus()
        focus_pos = focus_pos or 0
        builder = build_help if show_help else build_dashboard
        self.walker[:] = [urwid.Text(item) for item in builder()]
        if focus_pos < len(self.walker):
            self.set_focus(focus_pos)

# ─────────────────────────────────────────────
# Event loop callbacks
# ─────────────────────────────────────────────

def periodic_update(loop, widget):
    widget.update_content()
    loop.set_alarm_in(REFRESH_STEPS[refresh_idx], periodic_update, widget)


def unhandled_input(key):
    global refresh_idx, show_help
    if key in ("q", "Q"):
        raise urwid.ExitMainLoop()
    if key in ("r", "R"):
        refresh_idx = (refresh_idx + 1) % len(REFRESH_STEPS)
        return
    if key in ("h", "H"):
        show_help = not show_help
        return

# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

def main():
    # Kick off the first SMART fetch immediately in background
    _smart_cache["ts"] = 0.0   # ensure stale so thread fires
    _maybe_refresh_smart()

    dashboard = DashboardWidget()
    loop = urwid.MainLoop(
        dashboard, palette,
        handle_mouse=True,
        unhandled_input=unhandled_input,
    )
    loop.set_alarm_in(REFRESH_STEPS[refresh_idx], periodic_update, dashboard)
    loop.run()


if __name__ == "__main__":
    main()
