import os
import platform
import shutil
from typing import Optional

import psutil

try:
    import pynvml

    pynvml.nvmlInit()
    NVML_AVAILABLE = True
except Exception:
    NVML_AVAILABLE = False


def _gpus() -> list:
    if not NVML_AVAILABLE:
        return []
    gpus = []
    try:
        for i in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            used_mb = memory.used / (1024 ** 2)
            total_mb = memory.total / (1024 ** 2)
            gpus.append({
                "index": i,
                "name": name,
                "used_vram": round(used_mb, 2),
                "total_vram": round(total_mb, 2),
                "vram_pct": round(used_mb / total_mb * 100, 2) if total_mb else 0,
            })
    except Exception as err:
        print(f"[NVML] Failed to read GPU info: {err}")
    return gpus


def free_vram_mb() -> Optional[float]:
    gpus = _gpus()
    return max((g["total_vram"] - g["used_vram"] for g in gpus), default=None)


def get_system_telemetry() -> dict:
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(os.getcwd())
    gpus = _gpus()
    return {
        "gpu_available": bool(gpus),
        "gpus": gpus,
        "ram_used_gb": round(memory.used / (1024 ** 3), 2),
        "ram_total_gb": round(memory.total / (1024 ** 3), 2),
        "ram_pct": memory.percent,
        "os": platform.system(),
        "python_version": platform.python_version(),
        "disk": {
            "total_gb": round(disk.total / (1024 ** 3), 1),
            "used_gb": round(disk.used / (1024 ** 3), 1),
            "free_gb": round(disk.free / (1024 ** 3), 1),
            "pct": round(disk.used / disk.total * 100, 1),
        },
    }


def close_telemetry() -> None:
    if NVML_AVAILABLE:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
