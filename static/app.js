let selectedHostId = null;
let lossChart = null;
let latencyChart = null;
let lastAlertId = 0;
let refreshInterval = 5000;

document.addEventListener('DOMContentLoaded', () => {
    initCharts();
    fetchStats();
    checkForNewAlerts();
    
    setInterval(() => {
        fetchStats();
        checkForNewAlerts();
    }, refreshInterval);
});

function triggerRefreshBar() {
    const bar = document.getElementById('refresh-bar');
    bar.style.transition = 'none';
    bar.style.width = '0%';
    void bar.offsetWidth; 
    bar.style.transition = `width ${refreshInterval}ms linear`;
    bar.style.width = '100%';
}

function toggleTheme() { document.body.classList.toggle('dark-mode'); }

function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + tabId).classList.add('active');
    if (tabId === 'settings') loadSettings();
    if (tabId === 'alerts') fetchAlerts();
}

async function fetchStats() {
    triggerRefreshBar();
    const res = await fetch('/api/stats');
    const data = await res.json();
    
    const banner = document.getElementById('global-status-banner');
    banner.className = 'status-banner ' + data.summary.global_status.toLowerCase();
    if(data.summary.global_status === 'Up') banner.innerText = "All Systems Operational";
    else if(data.summary.global_status === 'Degraded') banner.innerText = "Minor Service Degradation";
    else banner.innerText = "Partial System Outage";

    document.getElementById('sum-total').innerText = data.summary.total;
    document.getElementById('sum-up').innerText = data.summary.up;
    document.getElementById('sum-loss').innerText = data.summary.avg_loss + '%';
    document.getElementById('sum-worst').innerText = data.summary.worst;
    document.getElementById('sum-alerts').innerText = data.summary.alerts;

    const grouped = {};
    const explorerSelect = document.getElementById('explorer-host');
    // Save current selection to restore after refresh
    const currentExpSelection = explorerSelect.value;
    explorerSelect.innerHTML = '<option value="all">All Hosts</option>';

    data.hosts.forEach(h => {
        if(!grouped[h.category]) grouped[h.category] = [];
        grouped[h.category].push(h);
        
        // Populate Explorer Dropdown
        const opt = document.createElement('option');
        opt.value = h.id;
        opt.innerText = h.name;
        explorerSelect.appendChild(opt);
    });
    explorerSelect.value = currentExpSelection;

    const tbody = document.getElementById('hosts-tbody');
    tbody.innerHTML = '';
    
    Object.keys(grouped).sort().forEach(category => {
        const catRow = document.createElement('tr');
        catRow.className = 'category-row';
        catRow.innerHTML = `<td colspan="9">${category}</td>`;
        tbody.appendChild(catRow);
        
        grouped[category].forEach(host => {
            const tr = document.createElement('tr');
            tr.className = 'row-host';
            if (host.id === selectedHostId) tr.classList.add('selected');
            tr.onclick = (e) => {
                if(e.target.tagName !== 'BUTTON') selectHost(host.id, host.name);
            };
            
            let dotClass = host.status.toLowerCase();
            let sparkHTML = '<div class="sparkline">';
            const emptyBlocks = 15 - host.sparkline.length;
            for(let i=0; i < emptyBlocks; i++) sparkHTML += '<div class="spark-block empty"></div>';
            host.sparkline.forEach(st => { sparkHTML += `<div class="spark-block ${st}"></div>`; });
            sparkHTML += '</div>';
            
            const latString = `${host.rtt_min.toFixed(1)} / ${host.rtt_avg.toFixed(1)} / ${host.rtt_max.toFixed(1)} ms`;
            
            tr.innerHTML = `
                <td><span class="dot ${dotClass}"></span>${host.status}</td>
                <td>${host.name}</td>
                <td class="mono">${host.address}</td>
                <td class="mono">${host.uptime}%</td>
                <td>${sparkHTML}</td>
                <td class="mono">${host.loss.toFixed(1)}%</td>
                <td class="mono">${latString}</td>
                <td class="mono">${host.jitter.toFixed(1)}ms</td>
                <td class="no-print">
                    <button onclick="executeTrace(${host.id}, '${host.name}')">Trace</button>
                    <button onclick="toggleHost(${host.id}, '${host.name}', '${host.address}', '${host.category}', ${host.active})">${host.active ? 'Pause' : 'Resume'}</button>
                    <button onclick="deleteHost(${host.id})">Delete</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    });

    if (selectedHostId) updateCharts();
}

function selectHost(id, name) {
    selectedHostId = id;
    document.getElementById('chart-title').innerText = `Metrics: ${name}`;
    document.querySelectorAll('.row-host').forEach(tr => tr.classList.remove('selected'));
    updateCharts();
    renderHeatmap(id);
}

function initCharts() {
    const commonOpts = { responsive: true, maintainAspectRatio: false, animation: false };
    lossChart = new Chart(document.getElementById('lossChart'), {
        type: 'line', data: { labels: [], datasets: [{ label: 'Loss %', borderColor: '#e53935', borderWidth: 1, pointRadius: 0, data: [] }] },
        options: { ...commonOpts, scales: { y: { min: 0, max: 100 } } }
    });
    latencyChart = new Chart(document.getElementById('latencyChart'), {
        type: 'line', data: { labels: [], datasets: [{ label: 'Avg RTT (ms)', borderColor: '#fb8c00', borderWidth: 1, pointRadius: 0, data: [] }] },
        options: { ...commonOpts, scales: { y: { beginAtZero: true } } }
    });
}

async function updateCharts() {
    if (!selectedHostId) return;
    const range = document.getElementById('time-range').value;
    const res = await fetch(`/api/history/${selectedHostId}?range=${range}`);
    const data = await res.json();
    
    const labels = data.map(m => new Date(m.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    lossChart.data.labels = labels;
    lossChart.data.datasets[0].data = data.map(m => m.loss_pct);
    lossChart.update();
    
    latencyChart.data.labels = labels;
    latencyChart.data.datasets[0].data = data.map(m => m.rtt_avg);
    latencyChart.update();
}

async function renderHeatmap(hostId) {
    const res = await fetch(`/api/heatmap/${hostId}`);
    const data = await res.json();
    const container = document.getElementById('heatmap-container');
    container.innerHTML = '';
    
    const today = new Date();
    for (let i = 89; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(d.getDate() - i);
        const dateStr = d.toISOString().split('T')[0]; 
        
        const block = document.createElement('div');
        const status = data[dateStr] || 'empty';
        block.className = `heat-day ${status}`;
        block.title = `${dateStr}: ${status.toUpperCase()}`;
        container.appendChild(block);
    }
}

// --- NEW FEATURE LOGIC --- //

async function executeTrace(hostId, hostName) {
    const modal = document.getElementById('trace-modal');
    document.getElementById('trace-title').innerText = `Traceroute: ${hostName}`;
    document.getElementById('trace-output').innerText = "Running trace... Please wait. This may take up to 20 seconds.";
    modal.showModal();

    const res = await fetch(`/api/traceroute/${hostId}`);
    const data = await res.json();
    document.getElementById('trace-output').innerText = data.trace || data.error;
}

async function scanLan() {
    const btn = document.getElementById('btn-scan-lan');
    const resultDiv = document.getElementById('lan-results');
    btn.innerText = "Scanning ARP Table...";
    
    const res = await fetch('/api/scan_lan');
    const ips = await res.json();
    
    resultDiv.style.display = 'block';
    resultDiv.innerHTML = '';
    
    if (ips.length === 0) {
        resultDiv.innerHTML = '<div style="padding: 4px; font-size: 11px;">No devices found.</div>';
    } else {
        ips.forEach(ip => {
            const div = document.createElement('div');
            div.className = 'lan-item';
            div.innerText = ip;
            div.onclick = () => {
                document.getElementById('new-host-ip').value = ip;
                document.getElementById('new-host-name').value = `LAN Device (${ip})`;
                document.getElementById('new-host-category').value = 'Local Network';
            };
            resultDiv.appendChild(div);
        });
    }
    btn.innerText = "🔍 Auto-Discover LAN Devices";
}

async function runExplorerQuery() {
    const hostId = document.getElementById('explorer-host').value;
    const minLoss = document.getElementById('explorer-loss').value;
    const minJitter = document.getElementById('explorer-jitter').value;
    
    const tbody = document.getElementById('explorer-tbody');
    tbody.innerHTML = '<tr><td colspan="5">Executing SQL Query...</td></tr>';
    
    const res = await fetch('/api/explorer', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({host_id: hostId, min_loss: minLoss, min_jitter: minJitter})
    });
    const data = await res.json();
    
    tbody.innerHTML = '';
    if(data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5">0 rows returned.</td></tr>';
        return;
    }
    
    data.forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${new Date(row.timestamp * 1000).toLocaleString()}</td>
            <td>${row.host}</td>
            <td class="mono">${row.loss_pct.toFixed(1)}%</td>
            <td class="mono">${row.rtt_avg.toFixed(1)}ms</td>
            <td class="mono">${row.jitter.toFixed(1)}ms</td>
        `;
        tbody.appendChild(tr);
    });
}
// ----------------------- //

function exportCSV() { if (selectedHostId) window.location.href = `/api/export/csv/${selectedHostId}`; }

function openAddHostModal() {
    document.getElementById('new-host-name').value = '';
    document.getElementById('new-host-ip').value = '';
    document.getElementById('new-host-category').value = 'General';
    document.getElementById('lan-results').style.display = 'none';
    document.getElementById('add-host-modal').showModal();
}

async function submitAddHost(e) {
    const name = document.getElementById('new-host-name').value;
    const address = document.getElementById('new-host-ip').value;
    const category = document.getElementById('new-host-category').value || 'General';
    
    await fetch('/api/hosts', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, address, category})
    });
    fetchStats();
}

async function deleteHost(id) {
    if(confirm("Delete this host?")) {
        await fetch(`/api/hosts/${id}`, { method: 'DELETE' });
        if(selectedHostId === id) {
            selectedHostId = null;
            document.getElementById('heatmap-container').innerHTML = ''; 
        }
        fetchStats();
    }
}

async function toggleHost(id, name, address, category, currentActive) {
    await fetch('/api/hosts', {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id, name, address, category, active: !currentActive})
    });
    fetchStats();
}

async function loadSettings() {
    const res = await fetch('/api/settings');
    const data = await res.json();
    const form = document.getElementById('settings-form');
    Object.keys(data).forEach(k => { if(form.elements[k]) form.elements[k].value = data[k]; });
}

async function saveSettings(e) {
    e.preventDefault();
    const formData = new FormData(e.target);
    const data = Object.fromEntries(formData.entries());
    await fetch('/api/settings', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    });
    alert("Settings saved.");
}

async function fetchAlerts() {
    const res = await fetch('/api/alerts');
    const data = await res.json();
    const tbody = document.getElementById('alerts-tbody');
    tbody.innerHTML = '';
    data.forEach(a => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${new Date(a.timestamp * 1000).toLocaleString()}</td>
            <td>${a.host_name}</td>
            <td><span class="dot ${a.severity === 'high' ? 'down' : 'degraded'}"></span>${a.severity}</td>
            <td>${a.message}</td>
            <td class="no-print">${a.acknowledged ? 'Acked' : `<button onclick="ackAlert(${a.id})">Acknowledge</button>`}</td>
        `;
        tbody.appendChild(tr);
    });
}

async function ackAlert(id) {
    await fetch('/api/alerts', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}) });
    fetchAlerts();
}

async function clearAllAlerts() {
    await fetch('/api/alerts', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action: 'clear_all'}) });
    fetchAlerts();
}

async function checkForNewAlerts() {
    const res = await fetch('/api/alerts');
    const alerts = await res.json();
    
    if (alerts.length > 0) {
        const maxId = Math.max(...alerts.map(a => a.id));
        if (lastAlertId === 0) { lastAlertId = maxId; } 
        else if (maxId > lastAlertId) {
            const newAlerts = alerts.filter(a => a.id > lastAlertId);
            newAlerts.forEach(a => showToast(a));
            lastAlertId = maxId;
            if (document.getElementById('tab-alerts').classList.contains('active')) fetchAlerts();
        }
    }
}

function showToast(alert) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${alert.severity}`;
    toast.innerHTML = `
        <div class="toast-content"><strong>${alert.host_name}</strong>${alert.message}</div>
        <div class="toast-progress"></div>
    `;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('toast-fadeout');
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}