from flask import Flask, render_template, request, jsonify, Response
from database import init_db, get_db, get_setting
from monitor import start_background_thread, evaluate_status, run_traceroute, get_lan_devices
import csv
import io
import time

app = Flask(__name__)
init_db()
start_background_thread()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/stats')
def api_stats():
    conn = get_db()
    hosts = conn.execute("SELECT * FROM hosts").fetchall()
    deg_thresh = get_setting('threshold_degraded', 2)
    down_thresh = get_setting('threshold_down', 10)
    
    host_data = []
    total_loss, active_count = 0, 0
    worst_host = {"name": "None", "loss": -1}
    up_count = 0
    global_status = "Up"
    
    for h in hosts:
        m = conn.execute("SELECT * FROM metrics WHERE host_id = ? ORDER BY timestamp DESC LIMIT 1", (h['id'],)).fetchone()
        
        total_checks = conn.execute("SELECT COUNT(*) as c FROM metrics WHERE host_id = ?", (h['id'],)).fetchone()['c']
        up_checks = conn.execute("SELECT COUNT(*) as c FROM metrics WHERE host_id = ? AND loss_pct < ?", (h['id'], deg_thresh)).fetchone()['c']
        uptime_pct = (up_checks / total_checks * 100) if total_checks > 0 else 100.0

        recent = conn.execute("SELECT loss_pct FROM metrics WHERE host_id = ? ORDER BY timestamp DESC LIMIT 15", (h['id'],)).fetchall()
        sparkline = [evaluate_status(r['loss_pct'], deg_thresh, down_thresh).lower() for r in reversed(recent)]

        if m and h['active']:
            loss, rtt_min, rtt_avg, rtt_max, jitter, ts = m['loss_pct'], m['rtt_min'], m['rtt_avg'], m['rtt_max'], m['jitter'], m['timestamp']
            status = evaluate_status(loss, deg_thresh, down_thresh)
            total_loss += loss
            active_count += 1
            if status == "Up": up_count += 1
            if loss > worst_host['loss']:
                worst_host = {"name": h['name'], "loss": loss}
            
            if status == "Down": global_status = "Down"
            elif status == "Degraded" and global_status != "Down": global_status = "Degraded"
        else:
            loss = rtt_min = rtt_avg = rtt_max = jitter = ts = 0
            status = "Paused" if not h['active'] else "Waiting"
            
        host_data.append({
            "id": h['id'], "name": h['name'], "address": h['address'], "category": h['category'], "active": bool(h['active']),
            "status": status, "loss": round(loss,1), "rtt_min": round(rtt_min,1), "rtt_avg": round(rtt_avg,1), 
            "rtt_max": round(rtt_max,1), "jitter": round(jitter,1), 
            "last_checked": ts, "uptime": round(uptime_pct, 2), "sparkline": sparkline
        })
        
    alerts = conn.execute("SELECT COUNT(*) as c FROM alerts WHERE acknowledged = 0").fetchone()['c']
    conn.close()
    
    summary = {
        "total": len(hosts), "up": up_count,
        "avg_loss": round(total_loss / active_count, 1) if active_count else 0,
        "worst": worst_host['name'] if worst_host['loss'] >= 0 else "N/A",
        "alerts": alerts, "global_status": global_status
    }
    return jsonify({"summary": summary, "hosts": host_data})

@app.route('/api/history/<int:host_id>')
def api_history(host_id):
    range_sec = int(request.args.get('range', 3600))
    min_ts = 0 if range_sec == 0 else int(time.time()) - range_sec
    conn = get_db()
    metrics = conn.execute("SELECT timestamp, loss_pct, rtt_avg FROM metrics WHERE host_id = ? AND timestamp >= ? ORDER BY timestamp ASC", (host_id, min_ts)).fetchall()
    conn.close()
    return jsonify([dict(m) for m in metrics])

@app.route('/api/heatmap/<int:host_id>')
def api_heatmap(host_id):
    conn = get_db()
    cutoff = int(time.time()) - (90 * 86400)
    data = conn.execute('''
        SELECT strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime') as day, MAX(loss_pct) as max_loss
        FROM metrics WHERE host_id = ? AND timestamp >= ? GROUP BY day ORDER BY day ASC
    ''', (host_id, cutoff)).fetchall()
    conn.close()
    
    deg = get_setting('threshold_degraded', 2)
    down = get_setting('threshold_down', 10)
    
    result = {}
    for d in data:
        status = evaluate_status(d['max_loss'], deg, down).lower()
        result[d['day']] = status
    return jsonify(result)

# --- NEW: ADVANCED LAB FEATURES ---

@app.route('/api/scan_lan')
def api_scan_lan():
    """Returns a list of IPs found on the local network via ARP."""
    return jsonify(get_lan_devices())

@app.route('/api/traceroute/<int:host_id>')
def api_traceroute(host_id):
    """Executes a traceroute to the selected host address."""
    conn = get_db()
    host = conn.execute("SELECT address FROM hosts WHERE id = ?", (host_id,)).fetchone()
    conn.close()
    if not host:
        return jsonify({"error": "Host not found"})
    
    trace_output = run_traceroute(host['address'])
    return jsonify({"trace": trace_output})

@app.route('/api/explorer', methods=['POST'])
def api_explorer():
    """Advanced SQL Query handler for the Telemetry Explorer."""
    data = request.json
    
    # Safe SQL querying using parameterized WHERE clauses
    query = '''SELECT m.timestamp, h.name as host, m.loss_pct, m.rtt_avg, m.jitter 
               FROM metrics m JOIN hosts h ON m.host_id = h.id WHERE 1=1'''
    params = []
    
    if data.get('host_id') and data['host_id'] != 'all':
        query += " AND m.host_id = ?"
        params.append(int(data['host_id']))
    if data.get('min_loss'):
        query += " AND m.loss_pct >= ?"
        params.append(float(data['min_loss']))
    if data.get('min_jitter'):
        query += " AND m.jitter >= ?"
        params.append(float(data['min_jitter']))
        
    query += " ORDER BY m.timestamp DESC LIMIT 100"
    
    conn = get_db()
    results = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in results])

# ----------------------------------

@app.route('/api/hosts', methods=['POST', 'PUT'])
def save_host():
    data = request.json
    conn = get_db()
    if request.method == 'POST':
        conn.execute("INSERT INTO hosts (name, address, category) VALUES (?, ?, ?)", 
                     (data['name'], data['address'], data.get('category', 'General')))
    else:
        conn.execute("UPDATE hosts SET name=?, address=?, category=?, active=? WHERE id=?", 
                     (data['name'], data['address'], data.get('category', 'General'), int(data['active']), data['id']))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

@app.route('/api/hosts/<int:host_id>', methods=['DELETE'])
def delete_host(host_id):
    conn = get_db()
    conn.execute("DELETE FROM hosts WHERE id=?", (host_id,))
    conn.execute("DELETE FROM metrics WHERE host_id=?", (host_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})

@app.route('/api/alerts', methods=['GET', 'POST'])
def handle_alerts():
    conn = get_db()
    if request.method == 'GET':
        alerts = conn.execute('''SELECT a.*, h.name as host_name FROM alerts a 
                                 LEFT JOIN hosts h ON a.host_id = h.id 
                                 ORDER BY a.timestamp DESC LIMIT 50''').fetchall()
        conn.close()
        return jsonify([dict(a) for a in alerts])
    else:
        data = request.json
        if data.get('action') == 'clear_all':
            conn.execute("UPDATE alerts SET acknowledged = 1")
        else:
            conn.execute("UPDATE alerts SET acknowledged = 1 WHERE id=?", (data['id'],))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    conn = get_db()
    if request.method == 'GET':
        settings = conn.execute("SELECT * FROM settings").fetchall()
        conn.close()
        return jsonify({s['key']: s['value'] for s in settings})
    else:
        data = request.json
        for k, v in data.items():
            conn.execute("UPDATE settings SET value=? WHERE key=?", (str(v), k))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})

@app.route('/api/export/csv/<int:host_id>')
def export_csv(host_id):
    conn = get_db()
    metrics = conn.execute('''SELECT h.name, m.timestamp, m.loss_pct, m.rtt_min, m.rtt_avg, m.rtt_max, m.jitter 
                              FROM metrics m JOIN hosts h ON m.host_id = h.id 
                              WHERE m.host_id = ? ORDER BY timestamp DESC''', (host_id,)).fetchall()
    conn.close()
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['Host', 'Timestamp', 'Loss (%)', 'Min RTT', 'Avg RTT', 'Max RTT', 'Jitter (ms)'])
    for m in metrics:
        cw.writerow([m['name'], m['timestamp'], m['loss_pct'], m['rtt_min'], m['rtt_avg'], m['rtt_max'], m['jitter']])
    return Response(si.getvalue(), mimetype='text/csv', 
                    headers={"Content-Disposition": f"attachment; filename=host_{host_id}_export.csv"})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)