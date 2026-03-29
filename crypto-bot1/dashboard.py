"""
=============================================================
  CRYPTO BOT — DASHBOARD DE MONITOREO
  Servidor local que abre en tu navegador
=============================================================

CÓMO USAR:
  1. Abrí una SEGUNDA terminal en PyCharm (el bot corre en la primera)
  2. Corré este archivo: python dashboard.py
  3. Abrí tu navegador en: http://localhost:8080
  4. El dashboard se actualiza solo cada 30 segundos

NO necesita pip install — usa solo librerías de Python estándar.
=============================================================
"""

import http.server
import json
import os
import re
from datetime import datetime

# Archivos que genera el bot (deben estar en la misma carpeta)
LEARNING_FILE   = "bot_learning.json"
TRADE_LOG_FILE  = "trade_history.log"
BOT_LOG_FILE    = "bot.log"

PORT = 8080


# =============================================================
#   LECTURA DE DATOS
# =============================================================

def read_learning() -> dict:
    if not os.path.exists(LEARNING_FILE):
        return {}
    try:
        with open(LEARNING_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def read_trades() -> list:
    """Parsea trade_history.log y devuelve lista de dicts."""
    if not os.path.exists(TRADE_LOG_FILE):
        return []
    trades = []
    try:
        with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Formato: FECHA | SIDE | SYMBOL | precio=X | qty=Y | pnl=Z% | MOTIVO
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 5:
                    continue
                trade = {
                    "time"  : parts[0] if len(parts) > 0 else "",
                    "side"  : parts[1] if len(parts) > 1 else "",
                    "symbol": parts[2] if len(parts) > 2 else "",
                    "price" : "",
                    "qty"   : "",
                    "pnl"   : "",
                    "reason": parts[5] if len(parts) > 5 else "",
                }
                for part in parts:
                    if part.startswith("precio="):
                        trade["price"] = part.replace("precio=", "").replace("$", "").replace(",", "")
                    elif part.startswith("qty="):
                        trade["qty"] = part.replace("qty=", "")
                    elif part.startswith("pnl="):
                        trade["pnl"] = part.replace("pnl=", "").replace("%", "")
                trades.append(trade)
    except Exception:
        pass
    return list(reversed(trades))   # Más recientes primero


def read_bot_status() -> dict:
    """Lee las últimas líneas del bot.log para saber si está vivo."""
    if not os.path.exists(BOT_LOG_FILE):
        return {"running": False, "last_line": "Bot no iniciado aún.", "last_time": "—"}
    try:
        with open(BOT_LOG_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        if not lines:
            return {"running": False, "last_line": "Sin actividad.", "last_time": "—"}
        last = lines[-1]
        # Extraer timestamp de la última línea
        match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", last)
        last_time = match.group(1) if match else "—"
        # El bot está "vivo" si escribió en el log en los últimos 5 minutos
        alive = False
        if match:
            try:
                t = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
                diff = (datetime.now() - t).total_seconds()
                alive = diff < 300
            except Exception:
                pass
        return {"running": alive, "last_line": last[-120:], "last_time": last_time}
    except Exception:
        return {"running": False, "last_line": "Error leyendo log.", "last_time": "—"}


def build_api_data() -> dict:
    learning = read_learning()
    trades   = read_trades()
    status   = read_bot_status()

    total  = learning.get("total_trades", 0)
    wins   = learning.get("total_wins", 0)
    wr     = round(wins / total * 100, 1) if total > 0 else 0

    # Calcular PnL acumulado desde los trades con pnl
    pnl_total = 0.0
    for t in trades:
        if t["pnl"]:
            try:
                pnl_total += float(t["pnl"])
            except Exception:
                pass
    pnl_total = round(pnl_total, 2)

    # Stats por moneda desde learning
    sym_stats = learning.get("symbol_stats", {})

    # Parámetros actuales
    params = learning.get("params", {})

    # Últimos ajustes de aprendizaje
    experiments = learning.get("param_experiments", [])
    last_exp = experiments[-1] if experiments else None

    return {
        "status"    : status,
        "total"     : total,
        "wins"      : wins,
        "win_rate"  : wr,
        "pnl_total" : pnl_total,
        "sym_stats" : sym_stats,
        "params"    : params,
        "last_exp"  : last_exp,
        "trades"    : trades[:50],    # Últimos 50
    }


# =============================================================
#   HTML DEL DASHBOARD
# =============================================================

HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crypto Bot — Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Syne:wght@400;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0b0e13;
    --surface: #111620;
    --surface2: #181e2c;
    --border: rgba(255,255,255,0.07);
    --text: #e8eaf0;
    --muted: #6b7280;
    --green: #10d98c;
    --green-dim: rgba(16,217,140,0.12);
    --red: #f05252;
    --red-dim: rgba(240,82,82,0.12);
    --blue: #5b8ff9;
    --blue-dim: rgba(91,143,249,0.12);
    --amber: #f5a623;
    --amber-dim: rgba(245,166,35,0.12);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Syne', sans-serif;
    font-size: 14px;
    min-height: 100vh;
    padding: 0 0 3rem;
  }

  /* ── Header ── */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 1.25rem 2rem;
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    background: rgba(11,14,19,0.95);
    backdrop-filter: blur(8px);
    z-index: 100;
  }
  .logo {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--text);
  }
  .logo span { color: var(--green); }
  .header-right { display: flex; align-items: center; gap: 1.5rem; }
  .refresh-info {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--muted);
  }
  .status-pill {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 700;
  }
  .dot {
    width: 7px; height: 7px;
    border-radius: 50%;
    animation: pulse 2s infinite;
  }
  .dot.on  { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot.off { background: var(--red);   animation: none; }
  @keyframes pulse {
    0%,100% { opacity: 1; }
    50%      { opacity: 0.4; }
  }

  /* ── Layout ── */
  .main { padding: 2rem; max-width: 1400px; margin: 0 auto; }

  /* ── Metric cards ── */
  .metrics {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1rem;
    margin-bottom: 1.5rem;
  }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
  }
  .card-label {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 0.6rem;
  }
  .card-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 28px;
    font-weight: 500;
    line-height: 1;
  }
  .card-sub {
    font-size: 11px;
    color: var(--muted);
    margin-top: 0.4rem;
    font-family: 'IBM Plex Mono', monospace;
  }
  .green { color: var(--green); }
  .red   { color: var(--red); }
  .blue  { color: var(--blue); }
  .amber { color: var(--amber); }

  /* ── Win rate bar ── */
  .wr-bar-wrap {
    background: rgba(255,255,255,0.06);
    border-radius: 4px;
    height: 4px;
    margin-top: 10px;
    overflow: hidden;
  }
  .wr-bar-fill {
    height: 100%;
    border-radius: 4px;
    background: var(--green);
    transition: width 0.8s ease;
  }

  /* ── Two-column grid ── */
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    margin-bottom: 1rem;
  }
  .grid-3 {
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 1rem;
    margin-bottom: 1rem;
  }

  /* ── Section titles ── */
  .section-title {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .section-title::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  /* ── Coin table ── */
  .coin-row {
    display: grid;
    grid-template-columns: 1fr 60px 60px 80px 100px;
    align-items: center;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
    gap: 8px;
  }
  .coin-row:last-child { border-bottom: none; }
  .coin-name {
    font-weight: 700;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .coin-tag {
    font-size: 10px;
    font-family: 'IBM Plex Mono', monospace;
    background: var(--surface2);
    padding: 2px 7px;
    border-radius: 4px;
    color: var(--muted);
  }
  .mono { font-family: 'IBM Plex Mono', monospace; font-size: 12px; }
  .mini-bar-wrap {
    background: rgba(255,255,255,0.06);
    border-radius: 3px;
    height: 3px;
    width: 60px;
  }
  .mini-bar-fill {
    height: 100%;
    border-radius: 3px;
  }

  /* ── Trade history ── */
  .trade-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  .trade-table th {
    text-align: left;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    padding: 0 12px 10px;
    border-bottom: 1px solid var(--border);
  }
  .trade-table td {
    padding: 9px 12px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    font-family: 'IBM Plex Mono', monospace;
    vertical-align: middle;
  }
  .trade-table tr:last-child td { border-bottom: none; }
  .trade-table tr:hover td { background: rgba(255,255,255,0.02); }
  .badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    padding: 2px 8px;
    border-radius: 4px;
    font-family: 'Syne', sans-serif;
  }
  .badge-buy  { background: var(--green-dim); color: var(--green); }
  .badge-sell { background: var(--red-dim);   color: var(--red); }

  /* ── Params box ── */
  .param-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }
  .param-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 7px 10px;
    background: var(--surface2);
    border-radius: 6px;
  }
  .param-key  { font-size: 11px; color: var(--muted); }
  .param-val  { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--blue); }

  /* ── Last log line ── */
  .log-line {
    background: var(--surface2);
    border-radius: 8px;
    padding: 12px 16px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--muted);
    word-break: break-all;
    margin-top: 1rem;
    border-left: 3px solid var(--border);
  }

  /* ── Learning alert ── */
  .learn-alert {
    background: var(--amber-dim);
    border: 1px solid rgba(245,166,35,0.2);
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 12px;
    color: var(--amber);
    margin-top: 10px;
  }

  /* ── Empty state ── */
  .empty {
    text-align: center;
    padding: 2rem;
    color: var(--muted);
    font-size: 13px;
  }

  /* ── Footer ── */
  footer {
    text-align: center;
    margin-top: 3rem;
    font-size: 11px;
    color: var(--muted);
    font-family: 'IBM Plex Mono', monospace;
  }

  @media (max-width: 900px) {
    .metrics { grid-template-columns: 1fr 1fr; }
    .grid-2, .grid-3 { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<header>
  <div class="logo">CRYPTO<span>BOT</span> · Monitor</div>
  <div class="header-right">
    <div class="refresh-info">Actualiza cada 30s · <span id="countdown">30</span>s</div>
    <div class="status-pill" id="status-pill">
      <div class="dot off" id="status-dot"></div>
      <span id="status-text">Cargando...</span>
    </div>
  </div>
</header>

<div class="main">

  <!-- ── Métricas principales ── -->
  <div class="metrics">
    <div class="card">
      <div class="card-label">Total Trades</div>
      <div class="card-value blue" id="m-trades">—</div>
      <div class="card-sub" id="m-wl">— ganados / — perdidos</div>
    </div>
    <div class="card">
      <div class="card-label">Win Rate</div>
      <div class="card-value" id="m-wr" style="color:var(--text)">—</div>
      <div class="wr-bar-wrap"><div class="wr-bar-fill" id="wr-bar" style="width:0%"></div></div>
    </div>
    <div class="card">
      <div class="card-label">PnL Acumulado</div>
      <div class="card-value" id="m-pnl">—</div>
      <div class="card-sub">suma de todos los trades cerrados</div>
    </div>
    <div class="card">
      <div class="card-label">Último ajuste</div>
      <div class="card-value amber" id="m-adj">—</div>
      <div class="card-sub" id="m-adj-sub">aprendizaje automático</div>
    </div>
  </div>

  <div class="grid-3">

    <!-- ── Historial de trades ── -->
    <div class="card">
      <div class="section-title">Últimas operaciones</div>
      <div id="trade-list">
        <div class="empty">Sin trades registrados aún.<br>El bot los irá mostrando aquí.</div>
      </div>
    </div>

    <!-- ── Columna derecha ── -->
    <div style="display:flex;flex-direction:column;gap:1rem;">

      <!-- Rendimiento por moneda -->
      <div class="card">
        <div class="section-title">Rendimiento por moneda</div>
        <div id="coin-list">
          <div class="empty">Sin datos aún.</div>
        </div>
      </div>

      <!-- Parámetros actuales del bot -->
      <div class="card">
        <div class="section-title">Parámetros actuales</div>
        <div class="param-grid" id="params-grid">
          <div class="empty" style="grid-column:1/-1">Sin datos aún.</div>
        </div>
      </div>

    </div>
  </div>

  <!-- ── Estado del bot ── -->
  <div class="card">
    <div class="section-title">Última línea del log</div>
    <div class="log-line" id="log-line">Esperando datos del bot...</div>
  </div>

</div>

<footer>
  Actualizando desde archivos locales · http://localhost:8080 · Datos en tiempo real
</footer>

<script>
const COIN_NAMES = {
  SOLUSDT:'Solana',DOGEUSDT:'Dogecoin',AVAXUSDT:'Avalanche',
  POLUSDT:'Polygon',LINKUSDT:'Chainlink',DOTUSDT:'Polkadot',
  ADAUSDT:'Cardano',LTCUSDT:'Litecoin',BTCUSDT:'Bitcoin',ETHUSDT:'Ethereum'
};

async function fetchData() {
  try {
    const r = await fetch('/api/data');
    return await r.json();
  } catch(e) {
    return null;
  }
}

function fmt(n, dec=2) {
  if (n === null || n === undefined || n === '') return '—';
  return parseFloat(n).toFixed(dec);
}

function render(data) {
  if (!data) return;

  // Status
  const alive = data.status.running;
  document.getElementById('status-dot').className  = 'dot ' + (alive ? 'on' : 'off');
  document.getElementById('status-text').textContent = alive ? 'Bot activo' : 'Bot detenido';

  // Métricas
  document.getElementById('m-trades').textContent = data.total || '0';
  document.getElementById('m-wl').textContent =
    `${data.wins} ganados / ${data.total - data.wins} perdidos`;

  const wr = data.win_rate;
  const wrEl = document.getElementById('m-wr');
  wrEl.textContent = wr + '%';
  wrEl.style.color = wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)';
  document.getElementById('wr-bar').style.width = Math.min(wr, 100) + '%';
  document.getElementById('wr-bar').style.background =
    wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--amber)' : 'var(--red)';

  const pnl = data.pnl_total;
  const pnlEl = document.getElementById('m-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmt(pnl) + '%';
  pnlEl.style.color = pnl >= 0 ? 'var(--green)' : 'var(--red)';

  // Último ajuste de aprendizaje
  const le = data.last_exp;
  if (le) {
    document.getElementById('m-adj').textContent = 'Trade #' + le.at_trade;
    document.getElementById('m-adj-sub').textContent =
      (le.adjustments && le.adjustments.length > 0)
        ? le.adjustments.join(' · ')
        : 'Sin cambios en ese ciclo';
  }

  // Log
  document.getElementById('log-line').textContent =
    data.status.last_line || 'Sin actividad reciente.';

  // Trades
  const trades = data.trades || [];
  const tradeEl = document.getElementById('trade-list');
  if (trades.length === 0) {
    tradeEl.innerHTML = '<div class="empty">Sin trades registrados aún.<br>El bot los irá mostrando aquí.</div>';
  } else {
    const rows = trades.slice(0, 20).map(t => {
      const isBuy  = t.side.trim().toUpperCase() === 'BUY';
      const pnl    = t.pnl !== '' ? parseFloat(t.pnl) : null;
      const pnlStr = pnl !== null
        ? `<span style="color:${pnl >= 0 ? 'var(--green)' : 'var(--red)'}">${pnl >= 0 ? '+' : ''}${fmt(pnl)}%</span>`
        : '<span style="color:var(--muted)">—</span>';
      const sym = (t.symbol || '').trim();
      return `<tr>
        <td style="color:var(--muted);font-size:10px">${(t.time||'').slice(11,16)}</td>
        <td><span class="badge ${isBuy ? 'badge-buy' : 'badge-sell'}">${t.side.trim()}</span></td>
        <td style="color:var(--text)">${sym.replace('USDT','')}</td>
        <td>$${parseFloat(t.price||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4})}</td>
        <td>${pnlStr}</td>
        <td style="color:var(--muted);font-size:10px">${(t.reason||'').trim().slice(0,15)}</td>
      </tr>`;
    }).join('');
    tradeEl.innerHTML = `<table class="trade-table">
      <thead><tr>
        <th>Hora</th><th>Tipo</th><th>Moneda</th><th>Precio</th><th>PnL</th><th>Motivo</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  // Coins
  const stats = data.sym_stats || {};
  const coinEl = document.getElementById('coin-list');
  const coinKeys = Object.keys(stats);
  if (coinKeys.length === 0) {
    coinEl.innerHTML = '<div class="empty">Sin datos aún.</div>';
  } else {
    const sorted = coinKeys.sort((a,b) => {
      const wrA = stats[a].trades > 0 ? stats[a].wins/stats[a].trades : 0;
      const wrB = stats[b].trades > 0 ? stats[b].wins/stats[b].trades : 0;
      return wrB - wrA;
    });
    coinEl.innerHTML = sorted.map(sym => {
      const s  = stats[sym];
      const wr = s.trades > 0 ? Math.round(s.wins / s.trades * 100) : 0;
      const pnl = s.total_pnl || 0;
      const color = wr >= 55 ? 'var(--green)' : wr >= 40 ? 'var(--amber)' : 'var(--red)';
      const name = COIN_NAMES[sym] || sym;
      return `<div class="coin-row">
        <div class="coin-name">
          ${name.slice(0,8)}
          <span class="coin-tag">${sym.replace('USDT','')}</span>
        </div>
        <div class="mono" style="color:var(--muted)">${s.trades}t</div>
        <div class="mono" style="color:${color}">${wr}%</div>
        <div class="mono" style="color:${pnl>=0?'var(--green)':'var(--red)'}">
          ${pnl >= 0 ? '+' : ''}${fmt(pnl)}%
        </div>
        <div>
          <div class="mini-bar-wrap">
            <div class="mini-bar-fill" style="width:${wr}%;background:${color}"></div>
          </div>
        </div>
      </div>`;
    }).join('');
  }

  // Params
  const params = data.params || {};
  const paramEl = document.getElementById('params-grid');
  const LABELS = {
    rsi_period:'RSI período', rsi_oversold:'RSI compra',
    rsi_overbought:'RSI venta', fast_ema:'EMA rápida',
    slow_ema:'EMA lenta', min_score:'Score mínimo',
    bb_period:'BB período', volume_factor:'Vol. factor',
  };
  const paramKeys = Object.keys(LABELS).filter(k => params[k] !== undefined);
  if (paramKeys.length === 0) {
    paramEl.innerHTML = '<div class="empty" style="grid-column:1/-1">Sin datos aún.</div>';
  } else {
    paramEl.innerHTML = paramKeys.map(k => `
      <div class="param-row">
        <span class="param-key">${LABELS[k]}</span>
        <span class="param-val">${params[k]}</span>
      </div>`).join('');
  }
}

// Countdown y refresh
let seconds = 30;
function tick() {
  seconds--;
  document.getElementById('countdown').textContent = seconds;
  if (seconds <= 0) {
    seconds = 30;
    fetchData().then(render);
  }
}

// Carga inicial
fetchData().then(render);
setInterval(tick, 1000);
</script>
</body>
</html>
"""


# =============================================================
#   SERVIDOR HTTP
# =============================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass    # Silenciar logs del servidor en la terminal

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/api/data":
            self._serve_data()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode("utf-8"))

    def _serve_data(self):
        data = build_api_data()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))


# =============================================================
#   MAIN
# =============================================================

if __name__ == "__main__":
    print("=" * 55)
    print("  CRYPTO BOT — DASHBOARD")
    print(f"  Abrí tu navegador en: http://localhost:{PORT}")
    print("  Presioná Ctrl+C para detener el dashboard.")
    print("=" * 55)
    print(f"\n  Leyendo archivos:")
    print(f"    {LEARNING_FILE}  {'✔ encontrado' if os.path.exists(LEARNING_FILE) else '— esperando que el bot lo genere'}")
    print(f"    {TRADE_LOG_FILE} {'✔ encontrado' if os.path.exists(TRADE_LOG_FILE) else '— esperando trades'}")
    print(f"    {BOT_LOG_FILE}   {'✔ encontrado' if os.path.exists(BOT_LOG_FILE) else '— bot no iniciado aún'}\n")

    server = http.server.HTTPServer(("localhost", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard detenido.")
