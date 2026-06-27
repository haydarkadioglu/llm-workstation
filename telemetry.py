import os
import platform
import subprocess
from typing import Dict, Any

# Safe NVML Initialization
try:
    import pynvml
    pynvml.nvmlInit()
    NVML_AVAILABLE = True
    print("[NVML] NVML successfully initialized for GPU monitoring.")
except Exception as e:
    NVML_AVAILABLE = False
    print(f"[NVML] NVML not initialized (No GPU or pynvml not installed): {e}")

def get_system_telemetry() -> dict:
    telemetry = {
        "gpu_available": False,
        "gpus": [],
        "ram_used_gb": 0.0,
        "ram_total_gb": 0.0,
        "ram_pct": 0.0,
        "os": platform.system(),
        "python_version": platform.python_version(),
        "workstation_path": os.path.abspath("app.py")
    }
    
    # Process RAM (rough check using system files/commands to avoid psutil dependency)
    try:
        if platform.system().lower() == "linux":
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            mem_total = 0
            mem_free = 0
            mem_buffers = 0
            mem_cached = 0
            for line in lines:
                if "MemTotal" in line:
                    mem_total = int(line.split()[1])
                elif "MemFree" in line:
                    mem_free = int(line.split()[1])
                elif "Buffers" in line:
                    mem_buffers = int(line.split()[1])
                elif "Cached" in line:
                    mem_cached = int(line.split()[1])
            used = mem_total - mem_free - mem_buffers - mem_cached
            telemetry["ram_total_gb"] = round(mem_total / (1024 * 1024), 2)
            telemetry["ram_used_gb"] = round(used / (1024 * 1024), 2)
            telemetry["ram_pct"] = round((used / mem_total) * 100, 2)
        elif platform.system().lower() == "windows":
            out = subprocess.check_output("wmic OS get FreePhysicalMemory,TotalVisibleMemorySize /Value", shell=True, text=True)
            mem_total = 0
            mem_free = 0
            for line in out.splitlines():
                if "TotalVisibleMemorySize" in line:
                    mem_total = int(line.split("=")[1])
                elif "FreePhysicalMemory" in line:
                    mem_free = int(line.split("=")[1])
            used = mem_total - mem_free
            telemetry["ram_total_gb"] = round(mem_total / (1024 * 1024), 2)
            telemetry["ram_used_gb"] = round(used / (1024 * 1024), 2)
            telemetry["ram_pct"] = round((used / mem_total) * 100, 2)
    except Exception:
        pass

    # GPU VRAM details
    if NVML_AVAILABLE:
        try:
            device_count = pynvml.nvmlDeviceGetCount()
            telemetry["gpu_available"] = device_count > 0
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode('utf-8')
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                used_mb = mem_info.used / (1024 ** 2)
                total_mb = mem_info.total / (1024 ** 2)
                pct = (used_mb / total_mb) * 100 if total_mb > 0 else 0
                telemetry["gpus"].append({
                    "index": i,
                    "name": name,
                    "used_vram": round(used_mb, 2),
                    "total_vram": round(total_mb, 2),
                    "vram_pct": round(pct, 2)
                })
        except Exception as e:
            print(f"[NVML Error] Failed to retrieve GPU info: {e}")
            
    # Disk Usage details
    try:
        import shutil
        total, used, free = shutil.disk_usage(os.getcwd())
        telemetry["disk"] = {
            "total_gb": round(total / (1024 ** 3), 1),
            "used_gb": round(used / (1024 ** 3), 1),
            "free_gb": round(free / (1024 ** 3), 1),
            "pct": round((used / total) * 100, 1)
        }
    except Exception:
        telemetry["disk"] = {
            "total_gb": 0.0,
            "used_gb": 0.0,
            "free_gb": 0.0,
            "pct": 0.0
        }
            
    return telemetry

def close_telemetry():
    if NVML_AVAILABLE:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
