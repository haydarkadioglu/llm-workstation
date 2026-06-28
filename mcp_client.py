import os
import sys
import time
import json
import uuid
import queue
import shlex
import platform
import threading
import subprocess
import requests
from typing import List, Dict, Any, Optional

class StdioMcpClient:
    def __init__(self, command: str, args: List[str]):
        self.command = command
        self.args = args
        self.process = None
        self.tools = []
        self.pending_responses = {}
        self.msg_counter = 1
        self.lock = threading.Lock()
        self.active = False
        
    def connect(self):
        cmd = [self.command] + self.args
        print(f"[MCP Client] Launching local stdio server: {' '.join(cmd)}")
        
        shell_mode = platform.system().lower() == "windows"
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            shell=shell_mode
        )
        self.active = True
        
        # Spawn reader threads
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        
        # Handshake: initialize
        init_id = self.send_message("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "llm-workstation-client", "version": "1.0.0"}
        })
        resp = self.wait_for_response(init_id)
        
        # Handshake: initialized notification
        self.send_notification("notifications/initialized", {})
        
        # Retrieve tools
        list_id = self.send_message("tools/list", {})
        resp_tools = self.wait_for_response(list_id)
        if resp_tools and "result" in resp_tools:
            self.tools = resp_tools["result"].get("tools", [])
            print(f"[MCP Client] Found tools: {[t['name'] for t in self.tools]}")
            
    def _read_stdout(self):
        for line in iter(self.process.stdout.readline, ""):
            if not self.active:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                msg_id = payload.get("id")
                if msg_id in self.pending_responses:
                    self.pending_responses[msg_id].put(payload)
            except Exception as e:
                print(f"[Stdio Client Error] Failed to parse message: {e}")
        self.process.stdout.close()
        
    def _read_stderr(self):
        for line in iter(self.process.stderr.readline, ""):
            if not self.active:
                break
            print(f"[External MCP Log] {line.strip()}", file=sys.stderr)
        self.process.stderr.close()
        
    def send_message(self, method: str, params: dict) -> int:
        with self.lock:
            msg_id = self.msg_counter
            self.msg_counter += 1
        
        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params
        }
        self.pending_responses[msg_id] = queue.Queue()
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        return msg_id
        
    def send_notification(self, method: str, params: dict):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        
    def wait_for_response(self, msg_id: int, timeout: int = 15) -> Optional[dict]:
        q = self.pending_responses.get(msg_id)
        if not q:
            return None
        try:
            resp = q.get(timeout=timeout)
            self.pending_responses.pop(msg_id, None)
            return resp
        except queue.Empty:
            self.pending_responses.pop(msg_id, None)
            return None
            
    def call_tool(self, name: str, arguments: dict) -> dict:
        call_id = self.send_message("tools/call", {
            "name": name,
            "arguments": arguments
        })
        resp = self.wait_for_response(call_id)
        return resp.get("result", {}) if resp else {"error": "Timeout calling local tool"}
        
    def disconnect(self):
        self.active = False
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()

POST_PREFERRING_URLS = set()

class SseMcpClient:
    def __init__(self, url: str):
        self.url = url
        self.session_url = None
        self.pending_responses = {}
        self.tools = []
        self.thread = None
        self.active = False
        self.msg_counter = 1
        self.lock = threading.Lock()
        
    def connect(self):
        self.active = True
        self.thread = threading.Thread(target=self._read_sse, daemon=True)
        self.thread.start()
        
        # Wait for SSE session endpoint setup
        timeout = 25
        start = time.time()
        while not self.session_url:
            if time.time() - start > timeout:
                raise TimeoutError("Timeout establishing SSE connection channel")
            time.sleep(0.1)
            
        # Handshake: initialize
        init_id = self.send_message("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "llm-workstation-client", "version": "1.0.0"}
        })
        resp = self.wait_for_response(init_id)
        
        # Handshake: initialized
        self.send_notification("notifications/initialized", {})
        
        # Fetch tools
        list_id = self.send_message("tools/list", {})
        resp_tools = self.wait_for_response(list_id)
        if resp_tools and "result" in resp_tools:
            self.tools = resp_tools["result"].get("tools", [])
            print(f"[MCP SSE Client] Found tools: {[t['name'] for t in self.tools]}")
            
    def _read_sse(self):
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            # Check if this URL is known to require POST
            use_post_directly = self.url in POST_PREFERRING_URLS
            
            response = None
            if not use_post_directly:
                try:
                    # Fail-fast on GET within 3s to allow quick POST fallback if server hangs on GET
                    response = requests.get(self.url, headers=headers, stream=True, timeout=3)
                    if response.status_code != 200:
                        print(f"[SSE Client] GET returned status {response.status_code}, falling back to POST...")
                        use_post_directly = True
                except Exception as get_err:
                    print(f"[SSE Client Warning] GET request failed/timed out: {get_err}. Trying POST fallback...")
                    use_post_directly = True
            
            if use_post_directly:
                print(f"[SSE Client] Connecting to {self.url} using POST...")
                post_headers = headers.copy()
                post_headers["Content-Type"] = "application/json"
                response = requests.post(self.url, headers=post_headers, json={}, stream=True, timeout=20)
                if response.status_code == 200:
                    POST_PREFERRING_URLS.add(self.url)
                    print(f"[SSE Client Cache] Cached {self.url} as POST-preferring endpoint.")
            
            current_event = None
            for line in response.iter_lines():
                if not self.active:
                    break
                if not line:
                    continue
                decoded = line.decode('utf-8').strip()
                if decoded.startswith("event:"):
                    current_event = decoded.replace("event:", "").strip()
                elif decoded.startswith("data:"):
                    data_str = decoded.replace("data:", "").strip()
                    if current_event == "endpoint":
                        if data_str.startswith("http"):
                            self.session_url = data_str
                        else:
                            from urllib.parse import urljoin
                            self.session_url = urljoin(self.url, data_str)
                    elif current_event == "message":
                        try:
                            payload = json.loads(data_str)
                            msg_id = payload.get("id")
                            if msg_id in self.pending_responses:
                                self.pending_responses[msg_id].put(payload)
                        except Exception as e:
                            print(f"[SSE Client Error] Failed to parse message: {e}")
        except Exception as e:
            print(f"[SSE Client Error] Connection broke: {e}")
            self.active = False
            
    def send_message(self, method: str, params: dict) -> int:
        with self.lock:
            msg_id = self.msg_counter
            self.msg_counter += 1
            
        payload = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params
        }
        self.pending_responses[msg_id] = queue.Queue()
        
        headers = {"Content-Type": "application/json"}
        requests.post(self.session_url, json=payload, headers=headers, timeout=10)
        return msg_id
        
    def send_notification(self, method: str, params: dict):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        headers = {"Content-Type": "application/json"}
        requests.post(self.session_url, json=payload, headers=headers, timeout=10)
        
    def wait_for_response(self, msg_id: int, timeout: int = 15) -> Optional[dict]:
        q = self.pending_responses.get(msg_id)
        if not q:
            return None
        try:
            resp = q.get(timeout=timeout)
            self.pending_responses.pop(msg_id, None)
            return resp
        except queue.Empty:
            self.pending_responses.pop(msg_id, None)
            return None
            
    def call_tool(self, name: str, arguments: dict) -> dict:
        call_id = self.send_message("tools/call", {
            "name": name,
            "arguments": arguments
        })
        resp = self.wait_for_response(call_id)
        return resp.get("result", {}) if resp else {"error": "Timeout calling remote SSE tool"}
        
    def disconnect(self):
        self.active = False
        self.session_url = None

# Registry management
connected_clients = {}

def connect_external_mcp(config: dict) -> dict:
    conn_id = config.get("id") or str(uuid.uuid4())[:8]
    conn_type = config.get("type", "stdio").lower()
    
    if conn_type == "stdio":
        command = config.get("command")
        args_str = config.get("args", "")
        args = shlex.split(args_str) if isinstance(args_str, str) else args_str
        client = StdioMcpClient(command, args)
    elif conn_type == "sse":
        url = config.get("url")
        client = SseMcpClient(url)
    else:
        raise ValueError(f"Unknown transport type: {conn_type}")
        
    client.connect()
    connected_clients[conn_id] = {
        "id": conn_id,
        "type": conn_type,
        "config": config,
        "client": client,
        "tools": client.tools
    }
    return {
        "id": conn_id,
        "type": conn_type,
        "status": "connected",
        "tools": client.tools
    }

def disconnect_external_mcp(conn_id: str) -> bool:
    if conn_id in connected_clients:
        connected_clients[conn_id]["client"].disconnect()
        connected_clients.pop(conn_id)
        return True
    return False

def get_connected_clients_info() -> List[dict]:
    info_list = []
    for conn_id, data in connected_clients.items():
        info_list.append({
            "id": conn_id,
            "type": data["type"],
            "config": data["config"],
            "tools": data["tools"]
        })
    return info_list

def get_all_external_tools() -> List[dict]:
    all_tools = []
    for conn_id, data in connected_clients.items():
        for t in data["tools"]:
            t_copy = t.copy()
            t_copy["_server_id"] = conn_id
            all_tools.append(t_copy)
    return all_tools

def execute_external_tool(tool_name: str, arguments: dict) -> dict:
    for conn_id, data in connected_clients.items():
        for t in data["tools"]:
            if t["name"] == tool_name:
                return data["client"].call_tool(tool_name, arguments)
    return {"error": f"Tool '{tool_name}' not found on any connected external server"}
