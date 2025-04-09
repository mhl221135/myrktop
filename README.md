# 🖥️ myrktop - Orange Pi 5 (RK3588) System Monitor COLORED BRANCH

🔥 **myrktop** is a lightweight system monitor for **Orange Pi 5 (RK3588)**, providing real-time information about **CPU, GPU, NPU, RAM, RGA, and system temperatures**.

## **📥 Installation Instructions**
### **1️⃣ Install Required Dependencies**
Before running the script, install dependencies to fetch readings:
```bash
sudo apt update && sudo apt install -y python3 python3-pip lm-sensors smartmontools nvme-cli && sudo sensors-detect --auto && pip3 install urwid
```

### **2️⃣ Download and Install myrktop**
Run the following command to download and install the script:
```bash
wget -O ~/myrktop.py https://raw.githubusercontent.com/mhl221135/myrktop/refs/heads/py-colored/myrktop.py
wget -O /usr/local/bin/myrktop https://raw.githubusercontent.com/mhl221135/myrktop/refs/heads/py-colored/myrktop
```
Then, make the script executable:
```bash
sudo chmod +x /usr/local/bin/myrktop
```

### **3️⃣ Run the Monitoring Script**
To run the script use:
```bash
myrktop
```

---

## **📊 Features**
- **Real-time CPU load & frequency monitoring (per core)**
- **Live GPU usage & frequency**
- **NPU & RGA usage**
- **RAM & Swap usage**
- **System temperature readings**
- **Network interfaces: Down/Up readings**
- **Storage Usage (/etc/fstab)**
- **NVMe & ATA Storage Info:**


---

## **📌 Example Output**
```bash
──────────────────────────────────────────────────
🔥 System Monitor
──────────────────────────────────────────────────
Device: rockchip,rk3588s-orangepi-5rockchip,rk3588
NPU Version: RKNPU driver: v0.9.8
System Uptime: up 17 hours, 30 minutes
Docker Status: Running ✅
──────────────────────────────────────────────────
📊 CPU Usage & Frequency:
Core 0:  12% 1800 MHz   Core 1:   3% 1800 MHz
Core 2:   9% 1800 MHz   Core 3:   6% 1800 MHz
Core 4:   3% 2352 MHz   Core 5:   4% 2352 MHz
Core 6:  20% 2304 MHz   Core 7:  17% 2304 MHz
──────────────────────────────────────────────────
🎮 GPU Load:   0%    300 MHz
──────────────────────────────────────────────────
🧠 NPU Load: 0% 0% 0%   1000 MHz
──────────────────────────────────────────────────
🖼️  RGA Load: 0% 0% 0%
──────────────────────────────────────────────────
🖥️  RAM & Swap Usage:
RAM Used: 2.4Gi / 15Gi
Swap Used: 5.0Mi / 7.8Gi
──────────────────────────────────────────────────
🌡️  Temperatures:
npu_thermal-virtual-0          30°C
center_thermal-virtual-0       30°C
bigcore1_thermal-virtual-0     31°C
soc_thermal-virtual-0          31°C
nvme-pci-44100                 28°C
gpu_thermal-virtual-0          30°C
littlecore_thermal-virtual-0   31°C
bigcore0_thermal-virtual-0     31°C
──────────────────────────────────────────────────
🌐 Network Traffic:
wlan0: Down 0.1 Mbps | Up 2.00 Mbps
eth0: Down 0.91 Mbps | Up 0.06 Mbps
──────────────────────────────────────────────────
💾 Storage Usage (/etc/fstab):
Mount Point             Total     Used     Free
/                         59G     7.2G      51G
/media/ssdmount          938G     387G     504G
/media/wdmount           1.8T     1.5T     233G
/media/500hdd            458G     149G     286G
──────────────────────────────────────────────────
NVMe Devices:
/dev/nvme0n1 - SPCC M.2 PCIe SSD | Temp: 29°C | Hours: 829 | Spare: 100%
ATA Devices:
/dev/sda - WDC WD20NMVW-11AV3S2 | Temp: 35°C | Hours: 17169 | 5200 rpm
/dev/sdb - WDC WD5000LPLX-00ZNTT0 | Temp: 33°C | Hours: 28406 | 7200 rpm
──────────────────────────────────────────────────
Press 'q' to exit. Use arrows or mouse to scroll.
```

---

## **🔧 How to Contribute**
If you find a bug or want to improve **myrktop**, feel free to fork the repository and submit a pull request.

📂 **GitHub Repository:** [https://github.com/mhl221135/myrktop](https://github.com/mhl221135/myrktop)

---

## **❓ Support**
If you have any issues, open an issue on GitHub, or contact me!

---

### **🔗 License**
This project is **open-source** and available under the **MIT License**.

