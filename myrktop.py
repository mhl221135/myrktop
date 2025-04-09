#!/usr/bin/env python3
import urwid
import subprocess
import re
import os
import time

# -------------------------------
# Basic System Info Functions
# -------------------------------

def get_device_info():
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
            npu_version = "Permission denied - try sudo"
        except Exception:
            npu_version = ""
    try:
        uptime = subprocess.check_output("uptime -p", shell=True).decode("utf-8").strip()
    except Exception:
        uptime = "N/A"
    docker_status = ""
    try:
        status = subprocess.check_output("systemctl is-active docker", shell=True, stderr=subprocess.PIPE).decode("utf-8").strip()
        if status == "active":
            docker_status = status
    except Exception:
        docker_status = ""
    return device_info, npu_version, uptime, docker_status

def get_cpu_info():
    cpu_loads = {}
    core_count = os.cpu_count() or 1
    global prev_cpu
    try:
        with open("/proc/stat", "r") as f:
            lines = f.readlines()
    except Exception:
        lines = []
    for i in range(core_count):
        line = next((l for l in lines if l.startswith(f"cpu{i} ")), None)
        if not line:
            continue
        parts = line.split()
        try:
            user   = int(parts[1])
            nice   = int(parts[2])
            system = int(parts[3])
            idle   = int(parts[4])
            iowait = int(parts[5])
            irq    = int(parts[6])
            softirq= int(parts[7])
            steal  = int(parts[8]) if len(parts) > 8 else 0
        except Exception:
            continue
        total = user + nice + system + idle + iowait + irq + softirq + steal
        if i in prev_cpu:
            prev_total, prev_idle = prev_cpu[i]
            diff_total = total - prev_total
            diff_idle  = idle - prev_idle
            load = (100 * (diff_total - diff_idle)) // diff_total if diff_total > 0 else 0
        else:
            load = 0
        cpu_loads[i] = load
        prev_cpu[i] = (total, idle)
    cpu_freqs = {}
    for i in range(core_count):
        try:
            with open(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq", "r") as f:
                freq_str = f.read().strip()
            freq = int(freq_str) // 1000
        except Exception:
            freq = 0
        cpu_freqs[i] = freq
    return cpu_loads, cpu_freqs

def get_gpu_info():
    gpu_load_path = "/sys/class/devfreq/fb000000.gpu/load"
    gpu_freq_path = "/sys/class/devfreq/fb000000.gpu/cur_freq"
    if not os.path.exists(gpu_load_path) or not os.path.exists(gpu_freq_path):
        return None, None
    try:
        with open(gpu_load_path, "r") as f:
            raw_line = f.read().strip()
        fields = re.split(r'[@ ]+', raw_line)
        load_str = fields[0].rstrip('%')
        gpu_load = int(load_str)
    except Exception:
        gpu_load = 0
    try:
        with open(gpu_freq_path, "r") as f:
            gpu_freq_str = f.read().strip()
        gpu_freq = int(gpu_freq_str) // 1000000
    except Exception:
        gpu_freq = 0
    return gpu_load, gpu_freq

def get_npu_info():
    npu_load_path = "/sys/kernel/debug/rknpu/load"
    npu_freq_path = "/sys/class/devfreq/fdab0000.npu/cur_freq"
    if not os.path.exists(npu_load_path) or not os.path.exists(npu_freq_path):
        return None, None
    try:
        with open(npu_load_path, "r") as f:
            data = f.read()
        percents = re.findall(r'(\d+)%', data)
        if percents:
            npu_load = " ".join([p + "%" for p in percents])
        else:
            npu_load = "0% 0% 0%"
    except Exception:
        npu_load = "0% 0% 0%"
    try:
        with open(npu_freq_path, "r") as f:
            npu_freq_str = f.read().strip()
        npu_freq = int(npu_freq_str) // 1000000
    except Exception:
        npu_freq = 0
    return npu_load, npu_freq

def get_rga_info():
    rga_load_path = "/sys/kernel/debug/rkrga/load"
    if not os.path.exists(rga_load_path):
        return None
    try:
        with open(rga_load_path, "r") as f:
            data = f.read()
        rga_values = re.findall(r'load = (\d+)%', data)
        if rga_values:
            rga_values = " ".join([v + "%" for v in rga_values[:3]])
        else:
            rga_values = "0% 0% 0%"
    except Exception:
        rga_values = "0% 0% 0%"
    return rga_values

def get_ram_swap_info():
    try:
        free_output = subprocess.check_output("free -h", shell=True).decode("utf-8")
        lines = free_output.splitlines()
        ram_line = next((l for l in lines if l.startswith("Mem:")), None)
        swap_line = next((l for l in lines if l.startswith("Swap:")), None)
        if ram_line:
            parts = ram_line.split()
            ram_total = parts[1]; ram_used = parts[2]
        else:
            ram_total = ram_used = "N/A"
        if swap_line:
            parts = swap_line.split()
            swap_total = parts[1]; swap_used = parts[2]
        else:
            swap_total = swap_used = "N/A"
    except Exception:
        ram_used, ram_total, swap_used, swap_total = "N/A", "N/A", "N/A", "N/A"
    return ram_used, ram_total, swap_used, swap_total

def get_temperatures():
    try:
        output = subprocess.check_output("sensors", shell=True, stderr=subprocess.DEVNULL).decode("utf-8")
        lines = output.splitlines()
        temp_items = []
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
                        temp_val = int(float(raw_temp[1:len(raw_temp)-4]))
                    except Exception:
                        temp_val = 0
                    if temp_val >= 70:
                        attr = 'temp_red'
                    elif temp_val >= 60:
                        attr = 'temp_yellow'
                    else:
                        attr = 'temp_green'
                    sensor_name = current_name if current_name is not None else fields[0]
                    formatted = f"{sensor_name:<30} {temp_val:2d}°C"
                    temp_items.append((attr, formatted))
                else:
                    temp_items.append(("default", line))
        if not temp_items:
            temp_items = [("default", "No temperature data.")]
    except Exception:
        temp_items = [("default", "No temperature data.")]
    return temp_items

def get_network_traffic():
    global prev_net
    interfaces = []
    net_class = "/sys/class/net"
    try:
        for iface in os.listdir(net_class):
            if os.path.exists(os.path.join(net_class, iface, "device")):
                interfaces.append(iface)
    except Exception:
        interfaces = []
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
                rx = int(parts[0]); tx = int(parts[8])
            except Exception:
                rx = tx = 0
            net_stats[iface_name] = (rx, tx)
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
        rates[iface] = (rx_rate, tx_rate)
        prev_net[iface] = (rx, tx, current_time)
    return rates

def get_fstab_disk_usage():
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
                if mount == "/tmp":
                    continue
                if mount not in mountpoints:
                    mountpoints.append(mount)
    except Exception:
        mountpoints = []
    usage_lines = []
    header = f"{'Mount Point':<20} {'Total':>8} {'Used':>8} {'Free':>8}"
    usage_lines.append(header)
    for m in mountpoints:
        try:
            df_output = subprocess.check_output(f"df -h {m}", shell=True, stderr=subprocess.DEVNULL).decode("utf-8")
            df_lines = df_output.splitlines()
            if len(df_lines) >= 2:
                parts = df_lines[1].split()
                if len(parts) >= 6:
                    mp = parts[5]
                    total = parts[1]
                    used = parts[2]
                    free = parts[3]
                    line_usage = f"{mp:<20} {total:>8} {used:>8} {free:>8}"
                    usage_lines.append(line_usage)
                else:
                    usage_lines.append(f"{m}: No info")
            else:
                usage_lines.append(f"{m}: No info")
        except Exception:
            usage_lines.append(f"{m}: No info")
    if not usage_lines:
        usage_lines = ["No disk usage info from /etc/fstab."]
    return usage_lines

# -------------------------------
# New SMART/Storage Debug Code
# -------------------------------

def run_all_smartctl(dev):
    """Run all three smartctl command variants and return a dict mapping command to output (full output)."""
    commands = [
        f"sudo smartctl -a -d auto /dev/{dev}",
        f"sudo smartctl -a -d sat /dev/{dev}",
        f"sudo smartctl -a /dev/{dev}"
    ]
    results = {}
    for cmd in commands:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            output = (result.stdout + "\n" + result.stderr).strip()
            results[cmd] = output  # Store full output without truncation.
        except Exception as e:
            results[cmd] = f"Exception: {str(e)}"
    return results

def run_smartctl(dev):
    # Run all commands and choose one that looks usable; also return full outputs for debug.
    all_results = run_all_smartctl(dev)
    chosen_output = None
    chosen_cmd = None
    for cmd, output in all_results.items():
        if output and "Usage:" not in output[:50] and "Unknown USB bridge" not in output:
            chosen_output = output
            chosen_cmd = cmd
            break
    if not chosen_output:
        chosen_cmd = list(all_results.keys())[-1] if all_results else "None"
        chosen_output = all_results.get(chosen_cmd, "No output")
    return chosen_output, chosen_cmd, all_results

def parse_nvme_info(output):
    info = {}
    m = re.search(r"Model Number:\s+(.*)", output)
    info["model"] = m.group(1).strip() if m else "Unknown"
    m = re.search(r"Temperature Sensor 1:\s+(\d+)\s*Celsius", output)
    info["temp"] = m.group(1).strip() if m else "N/A"
    m = re.search(r"Power On Hours:\s+(\d+)", output)
    info["power_hours"] = m.group(1).strip() if m else "N/A"
    m = re.search(r"Available Spare:\s+(\d+)%", output)
    info["avail_spare"] = m.group(1).strip() + "%" if m else "N/A"
    return info

def parse_ata_info(output):
    info = {}
    m = re.search(r"Device Model:\s+(.*)", output)
    info["model"] = m.group(1).strip() if m else "Unknown"
    # Get Power_On_Hours by iterating over lines
    power_hours = "N/A"
    for line in output.splitlines():
        if "Power_On_Hours" in line:
            tokens = line.split()
            for token in reversed(tokens):
                if token.isdigit():
                    power_hours = token
                    break
            if power_hours != "N/A":
                break
    info["power_hours"] = power_hours
    # Temperature: look for a line with "Temperature_Celsius"
    temp = "N/A"
    for line in output.splitlines():
        if "Temperature_Celsius" in line:
            tokens = line.split()
            for token in reversed(tokens):
                if token.isdigit():
                    temp = token
                    break
            if temp != "N/A":
                break
    info["temp"] = temp
    # Wear_Leveling_Count
    wear = None
    for line in output.splitlines():
        if "Wear_Leveling_Count" in line:
            tokens = line.split()
            for token in reversed(tokens):
                if token.isdigit():
                    wear = token
                    break
            break
    info["wear_level"] = wear
    # Rotation Rate
    rotation = None
    for line in output.splitlines():
        if "Rotation Rate:" in line:
            if "solid state device" in line.lower():
                rotation = None
            else:
                m = re.search(r"Rotation Rate:\s+(.*)", line)
                if m:
                    rotation = m.group(1).strip()
            break
    info["rotation"] = rotation
    return info

def get_drive_smart_info(dev):
    output, used_cmd, all_outputs = run_smartctl(dev)
    debug_data = {"cmd": used_cmd if used_cmd else "None", "all": {k: v[:300] + "..." if len(v) > 300 else v for k, v in all_outputs.items()}}
    if output is None or output.startswith("Error:"):
        debug_data["raw"] = output if output else "No output"
        return ("unknown", {"model": "Unknown", "temp": "N/A", "power_hours": "N/A", 
                             "avail_spare": "N/A", "debug": debug_data})
    # For parsing we use the full output.
    debug_data["raw"] = output[:300] + "..." if len(output) > 300 else output
    # If device name starts with "nvme", treat it as NVMe.
    if dev.startswith("nvme") or re.search(r"NVMe Version:", output, re.IGNORECASE):
        info = parse_nvme_info(output)
        info["debug"] = debug_data
        return ("nvme", info)
    else:
        info = parse_ata_info(output)
        info["debug"] = debug_data
        if info.get("model", "Unknown") == "Unknown":
            return ("unknown", info)
        return ("ata", info)

def get_storage_info():
    devices = []
    try:
        lsblk_output = subprocess.check_output("lsblk -dno NAME", shell=True, stderr=subprocess.DEVNULL).decode("utf-8")
        for line in lsblk_output.splitlines():
            name = line.strip()
            if re.match(r"^sd[a-z]+$", name) or name.startswith("nvme"):
                devices.append(name)
    except Exception:
        devices = []
    nvme_list = []
    ata_list = []
    for dev in devices:
        dtype, info = get_drive_smart_info(dev)
        if dtype == "nvme":
            line = f"/dev/{dev} - {info.get('model', 'Unknown')}"
            if info.get("temp") and info.get("temp") != "N/A":
                line += f" | Temp: {info.get('temp')}°C"
            if info.get("power_hours") and info.get("power_hours") != "N/A":
                line += f" | Hours: {info.get('power_hours')}"
            if info.get("avail_spare") and info.get("avail_spare") != "N/A":
                line += f" | Spare: {info.get('avail_spare')}"
            nvme_list.append(line)
        elif dtype == "ata":
            line = f"/dev/{dev} - {info.get('model', 'Unknown')}"
            if info.get("temp") and info.get("temp") != "N/A":
                line += f" | Temp: {info.get('temp')}°C"
            if info.get("power_hours") and info.get("power_hours") != "N/A":
                line += f" | Hours: {info.get('power_hours')}"
            if info.get("rotation"):
                line += f" | {info.get('rotation')}"
            if info.get("wear_level"):
                line += f" | Spare: {info.get('wear_level')}%"
            ata_list.append(line)
        else:
            debug_info = info.get("debug", {})
            all_cmds = debug_info.get("all", {})
            debug_str = ""
            for cmd, out in all_cmds.items():
                debug_str += f"\n(cmd: {cmd}) -> {out}"
            line = f"/dev/{dev} - Unknown SMART data (Selected cmd: {debug_info.get('cmd','')}){debug_str}"
            ata_list.append(line)
    return nvme_list, ata_list

# -------------------------------
# Dashboard Display (Urwid)
# -------------------------------

palette = [
    ('header', 'dark blue,bold', ''),
    ('title', 'yellow,bold', ''),
    ('default', 'default,bold', ''),
    ('good', 'dark green,bold', ''),
    ('bad', 'dark red,bold', ''),
    ('temp_red', 'light red,bold', ''),
    ('temp_yellow', 'yellow,bold', ''),
    ('temp_green', 'light green,bold', ''),
    ('freq', 'light green,bold', ''),
    ('footer', 'dark gray,bold', '')
]

class DashboardWidget(urwid.ListBox):
    def __init__(self):
        self.walker = urwid.SimpleListWalker([])
        super().__init__(self.walker)
        self.update_content()
    def update_content(self):
        focus_widget, focus_pos = self.get_focus()
        if focus_pos is None:
            focus_pos = 0
        new_items = []
        for item in build_dashboard():
            new_items.append(urwid.Text(item))
        self.walker[:] = new_items
        if focus_pos < len(new_items):
            self.set_focus(focus_pos)

def build_dashboard():
    lines = []
    sep = "─" * 50
    # Header
    lines.append(("header", sep))
    lines.append(("header", "🔥 System Monitor"))
    lines.append(("header", sep))
    device_info, npu_version, uptime, docker_status = get_device_info()
    lines.append(("default", f"Device: {device_info}"))
    if npu_version:
        lines.append(("default", f"NPU Version: {npu_version}"))
    lines.append(("default", f"System Uptime: {uptime}"))
    if docker_status == "active":
        lines.append(("good", "Docker Status: Running ✅"))
    elif docker_status:
        lines.append(("bad", f"Docker Status: {docker_status}"))
    lines.append(("header", sep))
    cpu_loads, cpu_freqs = get_cpu_info()
    lines.append(("title", "📊 CPU Usage & Frequency:"))
    cores = sorted(cpu_loads.keys())
    for i in range(0, len(cores), 2):
        if i + 1 < len(cores):
            attr1 = 'temp_red' if cpu_loads[cores[i]] >= 80 else ('temp_yellow' if cpu_loads[cores[i]] >= 60 else 'default')
            attr2 = 'temp_red' if cpu_loads[cores[i+1]] >= 80 else ('temp_yellow' if cpu_loads[cores[i+1]] >= 60 else 'default')
            markup = [
                ("default", f"Core {cores[i]}: "), (attr1, f"{cpu_loads[cores[i]]:3d}%"), ("default", " "),
                ("freq", f"{cpu_freqs[cores[i]]:4d} MHz   "),
                ("default", f"Core {cores[i+1]}: "), (attr2, f"{cpu_loads[cores[i+1]]:3d}%"), ("default", " "),
                ("freq", f"{cpu_freqs[cores[i+1]]:4d} MHz")
            ]
            lines.append(markup)
        else:
            attr1 = 'temp_red' if cpu_loads[cores[i]] >= 70 else ('temp_yellow' if cpu_loads[cores[i]] >= 60 else 'default')
            markup = [
                ("default", f"Core {cores[i]}: "), (attr1, f"{cpu_loads[cores[i]]:3d}%"), ("default", " "),
                ("freq", f"{cpu_freqs[cores[i]]:4d} MHz")
            ]
            lines.append(markup)
    lines.append(("header", sep))
    gpu_load, gpu_freq = get_gpu_info()
    if gpu_load is not None and gpu_freq is not None:
        gpu_attr = 'temp_red' if gpu_load >= 80 else ('temp_yellow' if gpu_load >= 60 else 'default')
        gpu_markup = [
            ("title", "🎮 GPU Load: "), (gpu_attr, f"{gpu_load:3d}%"),
            ("default", "   "), ("freq", f"{gpu_freq:4d} MHz")
        ]
        lines.append(gpu_markup)
        lines.append(("header", sep))
    npu_load, npu_freq = get_npu_info()
    if npu_load is not None and npu_freq is not None:
        try:
            npu_numeric = int(re.search(r'(\d+)%', npu_load).group(1))
        except Exception:
            npu_numeric = 0
        npu_attr = 'temp_red' if npu_numeric >= 80 else ('temp_yellow' if npu_numeric >= 60 else 'default')
        npu_markup = [
            ("title", "🧠 NPU Load: "), (npu_attr, f"{npu_load}"),
            ("default", "   "), ("freq", f"{npu_freq:4d} MHz")
        ]
        lines.append(npu_markup)
        lines.append(("header", sep))
    rga_info = get_rga_info()
    if rga_info is not None:
        try:
            rga_numeric = int(re.search(r'(\d+)%', rga_info).group(1))
        except Exception:
            rga_numeric = 0
        rga_attr = 'temp_red' if rga_numeric >= 80 else ('temp_yellow' if rga_numeric >= 60 else 'default')
        rga_markup = [("title", "🖼️  RGA Load: "), (rga_attr, f"{rga_info}")]
        lines.append(rga_markup)
        lines.append(("header", sep))
    ram_used, ram_total, swap_used, swap_total = get_ram_swap_info()
    lines.append(("title", "🖥️  RAM & Swap Usage:"))
    lines.append(("default", f"RAM Used: {ram_used} / {ram_total}"))
    lines.append(("default", f"Swap Used: {swap_used} / {swap_total}"))
    lines.append(("header", sep))
    temp_items = get_temperatures()
    lines.append(("title", "🌡️  Temperatures:"))
    for attr, text in temp_items:
        lines.append((attr, text))
    lines.append(("header", sep))
    rates = get_network_traffic()
    lines.append(("title", "🌐 Network Traffic:"))
    for iface, (rx_rate, tx_rate) in rates.items():
        lines.append(("default", f"{iface}: Down {rx_rate:.2f} Mbps | Up {tx_rate:.2f} Mbps"))
    lines.append(("header", sep))
    disk_lines = get_fstab_disk_usage()
    lines.append(("title", "💾 Storage Usage (/etc/fstab):"))
    for d in disk_lines:
        lines.append(("default", d))
    lines.append(("header", sep))
    nvme_info, ata_info = get_storage_info()
    if nvme_info:
        lines.append(("good", "NVMe Devices:"))
        for info in nvme_info:
            lines.append(("default", info))
    if ata_info:
        lines.append(("good", "ATA Devices:"))
        for info in ata_info:
            lines.append(("default", info))
    else:
        lines.append(("bad", "No ATA devices detected."))
    lines.append(("header", sep))
    lines.append(("footer", "Press 'q' to exit. Use arrows or mouse to scroll."))
    return lines

# -------------------------------
# Urwid Dashboard Classes
# -------------------------------

class DashboardWidget(urwid.ListBox):
    def __init__(self):
        self.walker = urwid.SimpleListWalker([])
        super().__init__(self.walker)
        self.update_content()
    def update_content(self):
        focus_widget, focus_pos = self.get_focus()
        if focus_pos is None:
            focus_pos = 0
        new_items = []
        for item in build_dashboard():
            new_items.append(urwid.Text(item))
        self.walker[:] = new_items
        if focus_pos < len(new_items):
            self.set_focus(focus_pos)

def periodic_update(loop, widget):
    widget.update_content()
    loop.set_alarm_in(0.5, periodic_update, widget)

def unhandled_input(key):
    if key in ('q', 'Q'):
        raise urwid.ExitMainLoop()

def main():
    global prev_cpu, prev_net
    prev_cpu = {}
    prev_net = {}
    dashboard = DashboardWidget()
    loop = urwid.MainLoop(dashboard, palette, handle_mouse=True, unhandled_input=unhandled_input)
    loop.set_alarm_in(0.5, periodic_update, dashboard)
    loop.run()

if __name__ == '__main__':
    main()
