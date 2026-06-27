import os
import re
import sys
import time
import platform
import threading
import subprocess
import urllib.request
from typing import Optional

CLOUDFLARE_URL = None

def download_cloudflared() -> Optional[str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    bin_name = "cloudflared"
    if system == "windows":
        bin_name = "cloudflared.exe"
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    elif system == "darwin":
        if "arm" in machine or "m1" in machine or "m2" in machine:
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64"
        else:
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64"
    elif system == "linux":
        if "arm" in machine or "aarch" in machine:
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
        else:
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    else:
        print(f"[Cloudflare] Unsupported OS/Platform: {system} {machine}")
        return None

    if os.path.exists(bin_name):
        print(f"[Cloudflare] Found existing binary: {os.path.abspath(bin_name)}")
        return os.path.abspath(bin_name)

    print(f"[Cloudflare] Downloading {bin_name} from: {url}")
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            with open(bin_name, 'wb') as out_file:
                out_file.write(response.read())
        
        if system != "windows":
            os.chmod(bin_name, 0o755)
            
        print(f"[Cloudflare] Successfully downloaded: {os.path.abspath(bin_name)}")
        return os.path.abspath(bin_name)
    except Exception as e:
        print(f"[Cloudflare Error] Download failed: {e}")
        return None

def start_cloudflare_tunnel(port: int):
    global CLOUDFLARE_URL
    bin_path = download_cloudflared()
    if not bin_path:
        print("[Cloudflare] Cannot start tunnel: Binary not found or failed to download.")
        return

    cmd = [bin_path, "tunnel", "--url", f"http://localhost:{port}"]
    print(f"[Cloudflare] Starting tunnel with: {' '.join(cmd)}")
    
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if platform.system().lower() == "windows" else 0
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creation_flags
        )
    except Exception as e:
        print(f"[Cloudflare Error] Process failed to spawn: {e}")
        return

    def parse_logs():
        global CLOUDFLARE_URL
        url_regex = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        for line in iter(process.stdout.readline, ""):
            match = url_regex.search(line)
            if match:
                CLOUDFLARE_URL = match.group(0)
                print("\n" + "="*80)
                print(f"[Cloudflare] TUNNEL CREATED SUCCESSFULLY!")
                print(f"[Cloudflare] URL: {CLOUDFLARE_URL}")
                print("="*80 + "\n")
        process.stdout.close()

    threading.Thread(target=parse_logs, daemon=True).start()

def get_cloudflare_url() -> Optional[str]:
    return CLOUDFLARE_URL
