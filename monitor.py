import subprocess
import platform
import re
import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from database import get_db, get_setting, save_metric

last_status = {}

def get_lan_devices():
    """Scans the local ARP table to instantly find active LAN devices."""
    try:
        result = subprocess.run(["arp", "-a"], capture_output=True, text=True)
        # Regex to extract valid IPv4 addresses
        ips = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', result.stdout)
        
        # Filter out broadcast and multicast addresses
        valid_ips = set()
        for ip in ips:
            if not ip.startswith('224.') and not ip.startswith('239.') and not ip.endswith('.255'):
                valid_ips.add(ip)
        return list(valid_ips)
    except Exception:
        return []

def run_traceroute(address):
    """Runs OS-specific traceroute limited to 15 hops for speed."""
    os_type = platform.system()
    if os_type == "Windows":
        cmd = ["tracert", "-d", "-h", "15", "-w", "500", address]
    else:
        cmd = ["traceroute", "-n", "-m", "15", "-w", "1", address]
        
    try:
        # Timeout at 20s so a bad trace doesn't hang the server
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return result.stdout
    except subprocess.TimeoutExpired:
        return "Traceroute timed out after 20 seconds."
    except Exception as e:
        return f"Traceroute error: {str(e)}"

def parse_ping_output(stdout, os_type):
    loss, min_rtt, avg_rtt, max_rtt, jitter = 100.0, 0.0, 0.0, 0.0, 0.0
    if not stdout: return loss, min_rtt, avg_rtt, max_rtt, jitter

    try:
        if os_type == "Windows":
            loss_match = re.search(r'Lost = \d+ \((\d+)% loss\)', stdout)
            if loss_match: loss = float(loss_match.group(1))
            
            rtt_match = re.search(r'Minimum = (\d+)ms, Maximum = (\d+)ms, Average = (\d+)ms', stdout)
            if rtt_match:
                min_rtt = float(rtt_match.group(1))
                max_rtt = float(rtt_match.group(2))
                avg_rtt = float(rtt_match.group(3))
                jitter = (max_rtt - min_rtt) / 2.0
        else:
            loss_match = re.search(r'(\d+(?:\.\d+)?)% packet loss', stdout)
            if loss_match: loss = float(loss_match.group(1))
                
            rtt_match = re.search(r'= ([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+)', stdout)
            if rtt_match:
                min_rtt = float(rtt_match.group(1))
                avg_rtt = float(rtt_match.group(2))
                max_rtt = float(rtt_match.group(3))
                jitter = float(rtt_match.group(4))
    except Exception: pass
    return loss, min_rtt, avg_rtt, max_rtt, jitter

def ping_host(address, count):
    os_type = platform.system()
    if os_type == "Windows":
        cmd = ["ping", "-n", str(count), "-w", "1000", address]
    elif os_type == "Darwin":
        cmd = ["ping", "-c", str(count), "-W", "1000", address]
    else:
        cmd = ["ping", "-c", str(count), "-W", "1", address]
        
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=count * 2)
        return parse_ping_output(result.stdout, os_type)
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        return 100.0, 0.0, 0.0, 0.0, 0.0

def evaluate_status(loss, deg_thresh, down_thresh):
    if loss > down_thresh: return "Down"
    if loss >= deg_thresh: return "Degraded"
    return "Up"

def check_host(host, count, is_sim, deg_thresh, down_thresh):
    host_id, name, address = host['id'], host['name'], host['address']
    
    if is_sim:
        loss = 0.0 if random.random() > 0.1 else random.uniform(2, 20)
        if random.random() > 0.95: loss = 100.0 
        avg_rtt = random.uniform(10, 50) + (100 if loss > 0 else 0)
        min_rtt = max(0, avg_rtt - random.uniform(2, 10))
        max_rtt = avg_rtt + random.uniform(5, 30)
        jitter = random.uniform(1, 10)
    else:
        loss, min_rtt, avg_rtt, max_rtt, jitter = ping_host(address, int(count))
        
    save_metric(host_id, loss, min_rtt, avg_rtt, max_rtt, jitter)
    
    current_status = evaluate_status(loss, deg_thresh, down_thresh)
    prev_status = last_status.get(host_id, "Up")
    
    if current_status != prev_status:
        conn = get_db()
        msg = f"{name} transitioned from {prev_status} to {current_status} ({loss:.1f}% loss)"
        severity = "high" if current_status == "Down" else "medium" if current_status == "Degraded" else "info"
        conn.execute("INSERT INTO alerts (timestamp, host_id, severity, message) VALUES (?, ?, ?, ?)",
                     (int(time.time()), host_id, severity, msg))
        conn.commit()
        conn.close()
        last_status[host_id] = current_status

def monitoring_loop():
    while True:
        conn = get_db()
        hosts = conn.execute("SELECT * FROM hosts WHERE active = 1").fetchall()
        conn.close()
        
        sim_mode = get_setting('simulation_mode', 0) == 1
        interval = get_setting('check_interval', 10)
        count = get_setting('ping_count', 10)
        deg = get_setting('threshold_degraded', 2)
        down = get_setting('threshold_down', 10)
        
        if hosts:
            with ThreadPoolExecutor(max_workers=min(10, len(hosts))) as executor:
                for host in hosts:
                    executor.submit(check_host, host, count, sim_mode, deg, down)
                    
        time.sleep(interval)

def start_background_thread():
    t = threading.Thread(target=monitoring_loop, daemon=True)
    t.start()