"""
=============================================================
  BOT DE TRADING ADAPTATIVO — Altcoins Volátiles
  Exchange : Binance  |  Python: 3.8+
  Incluye dashboard web integrado en el mismo proceso
=============================================================
"""
 
import time, logging, sys, json, os, math, re, threading
import http.server, socketserver
from datetime import datetime
from collections import deque
 
try:
    from binance.client import Client
    from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
except ImportError:
    print("Falta instalar: pip install python-binance")
    sys.exit(1)
 
try:
    import numpy as np
except ImportError:
    print("Falta instalar: pip install numpy")
    sys.exit(1)
 
 
# =============================================================
#   CONFIGURACIÓN
# =============================================================
 
API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
USE_TESTNET = True
 
SYMBOLS = [
    "SOLUSDT", "DOGEUSDT", "AVAXUSDT", "POLUSDT",
    "LINKUSDT", "DOTUSDT", "ADAUSDT", "LTCUSDT",
]
 
INTERVAL        = "5m"
MAX_TRADE_PCT   = 0.08
BASE_STOP_LOSS  = 0.03
BASE_TAKE_PROFIT= 0.06
MAX_OPEN_TRADES = 3
 
INITIAL_PARAMS = {
    "rsi_period"     : 14,
    "rsi_oversold"   : 32,
    "rsi_overbought" : 68,
    "fast_ema"       : 9,
    "slow_ema"       : 21,
    "bb_period"      : 20,
    "bb_std"         : 2.0,
    "macd_fast"      : 12,
    "macd_slow"      : 26,
    "macd_signal"    : 9,
    "atr_period"     : 14,
    "min_score"      : 2,
    "volume_factor"  : 0.5,
}
 
POLL_SECONDS   = 60
LEARNING_FILE  = "bot_learning.json"
TRADE_LOG_FILE = "trade_history.log"
BOT_LOG_FILE   = "bot.log"
PORT           = int(os.environ.get("PORT", 8080))
 
 
# =============================================================
#   LOGGING
# =============================================================
 
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BOT_LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("AdaptiveBot")
 
 
# =============================================================
#   SISTEMA DE APRENDIZAJE ADAPTATIVO
# =============================================================
 
class LearningSystem:
    def __init__(self, filepath=LEARNING_FILE):
        self.filepath = filepath
        self.data = self._load()
 
    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    loaded = json.load(f)
                    log.info(f"Aprendizaje previo cargado: {loaded.get('total_trades', 0)} trades.")
                    return loaded
            except Exception:
                pass
        return {
            "total_trades"     : 0,
            "total_wins"       : 0,
            "params"           : dict(INITIAL_PARAMS),
            "symbol_stats"     : {},
            "hour_stats"       : {},
            "param_experiments": [],
            "last_adjusted"    : None,
        }
 
    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(self.data, f, indent=2)
 
    def get_params(self):
        return dict(self.data["params"])
 
    def record_trade(self, symbol: str, pnl_pct: float, params_used: dict, won: bool):
        hour = str(datetime.now().hour)
        self.data["total_trades"] += 1
        if won:
            self.data["total_wins"] += 1
        if symbol not in self.data["symbol_stats"]:
            self.data["symbol_stats"][symbol] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        self.data["symbol_stats"][symbol]["trades"] += 1
        self.data["symbol_stats"][symbol]["total_pnl"] = round(
            self.data["symbol_stats"][symbol]["total_pnl"] + pnl_pct, 4)
        if won:
            self.data["symbol_stats"][symbol]["wins"] += 1
        if hour not in self.data["hour_stats"]:
            self.data["hour_stats"][hour] = {"trades": 0, "wins": 0}
        self.data["hour_stats"][hour]["trades"] += 1
        if won:
            self.data["hour_stats"][hour]["wins"] += 1
        if self.data["total_trades"] % 10 == 0:
            self._adapt_params()
        self._save()
 
    def _adapt_params(self):
        total    = self.data["total_trades"]
        wins     = self.data["total_wins"]
        win_rate = wins / total if total > 0 else 0
        p = self.data["params"]
        log.info(f"[APRENDIZAJE] Win rate: {win_rate*100:.1f}% en {total} trades.")
        adjustments = []
        if win_rate < 0.40 and p["min_score"] < 5:
            p["min_score"] = min(p["min_score"] + 1, 5)
            adjustments.append("min_score ↑")
        elif win_rate > 0.65 and p["min_score"] > 2:
            p["min_score"] = max(p["min_score"] - 1, 2)
            adjustments.append("min_score ↓")
        if win_rate < 0.45:
            p["rsi_oversold"]   = max(25, p["rsi_oversold"] - 2)
            p["rsi_overbought"] = min(75, p["rsi_overbought"] + 2)
            adjustments.append("RSI más estricto")
        elif win_rate > 0.60:
            p["rsi_oversold"]   = min(35, p["rsi_oversold"] + 1)
            p["rsi_overbought"] = max(65, p["rsi_overbought"] - 1)
            adjustments.append("RSI más flexible")
        self.data["param_experiments"].append({
            "at_trade"   : total,
            "win_rate"   : round(win_rate, 3),
            "adjustments": adjustments,
            "new_params" : dict(p),
        })
        if adjustments:
            log.info(f"[APRENDIZAJE] Ajustes: {', '.join(adjustments)}")
        self._save()
 
    def get_best_symbols(self) -> list:
        stats = self.data["symbol_stats"]
        ranked = []
        for sym in SYMBOLS:
            if sym in stats and stats[sym]["trades"] >= 3:
                wr = stats[sym]["wins"] / stats[sym]["trades"]
                ranked.append((sym, wr))
            else:
                ranked.append((sym, 0.5))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in ranked]
 
    def get_hour_quality(self, hour: int) -> float:
        h = str(hour)
        stats = self.data["hour_stats"]
        if h in stats and stats[h]["trades"] >= 5:
            return stats[h]["wins"] / stats[h]["trades"]
        return 0.5
 
    def print_summary(self):
        total = self.data["total_trades"]
        wins  = self.data["total_wins"]
        log.info("=" * 55)
        log.info(f"  Total trades: {total}")
        if total > 0:
            log.info(f"  Win rate: {wins/total*100:.1f}%")
        else:
            log.info("  Sin trades aún")
        log.info("=" * 55)
 
 
# =============================================================
#   INDICADORES TÉCNICOS
# =============================================================
 
def ema(prices: list, period: int) -> list:
    k = 2 / (period + 1)
    result = [prices[0]]
    for p in prices[1:]:
        result.append(p * k + result[-1] * (1 - k))
    return result
 
def calculate_rsi(prices: list, period: int) -> float:
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    gains  = sum(d for d in recent if d > 0)
    losses = sum(-d for d in recent if d < 0)
    avg_g  = gains / period
    avg_l  = losses / period
    if avg_l == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 2)
 
def calculate_bollinger(prices: list, period: int, std_mult: float):
    if len(prices) < period:
        mid = prices[-1]
        return mid, mid, mid
    window = prices[-period:]
    mid    = sum(window) / period
    std    = math.sqrt(sum((p - mid)**2 for p in window) / period)
    return mid + std * std_mult, mid, mid - std * std_mult
 
def calculate_macd(prices: list, fast: int, slow: int, signal_p: int):
    if len(prices) < slow + signal_p:
        return 0, 0, 0
    fast_ema_s  = ema(prices, fast)
    slow_ema_s  = ema(prices, slow)
    macd_line   = [f - s for f, s in zip(fast_ema_s, slow_ema_s)]
    signal_line = ema(macd_line, signal_p)
    hist        = macd_line[-1] - signal_line[-1]
    return macd_line[-1], signal_line[-1], hist
 
def calculate_atr(highs: list, lows: list, closes: list, period: int) -> float:
    if len(closes) < 2:
        return closes[-1] * 0.02
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs))
 
def calculate_volume_ratio(volumes: list, period: int = 20) -> float:
    if len(volumes) < 2:
        return 1.0
    avg = sum(volumes[-period-1:-1]) / min(period, len(volumes)-1)
    return volumes[-1] / avg if avg > 0 else 1.0
 
 
# =============================================================
#   ANÁLISIS DE SEÑAL
# =============================================================
 
def analyze_symbol(candles: dict, params: dict) -> dict:
    closes  = candles["closes"]
    highs   = candles["highs"]
    lows    = candles["lows"]
    volumes = candles["volumes"]
    price   = closes[-1]
 
    rsi = calculate_rsi(closes, params["rsi_period"])
    fast_ema_series = ema(closes, params["fast_ema"])
    slow_ema_series = ema(closes, params["slow_ema"])
    fast_now,  slow_now  = fast_ema_series[-1], slow_ema_series[-1]
    fast_prev, slow_prev = fast_ema_series[-2], slow_ema_series[-2]
    bb_upper, bb_mid, bb_lower = calculate_bollinger(closes, params["bb_period"], params["bb_std"])
    macd_val, macd_sig, macd_hist = calculate_macd(closes, params["macd_fast"], params["macd_slow"], params["macd_signal"])
    atr   = calculate_atr(highs, lows, closes, params["atr_period"])
    vol_r = calculate_volume_ratio(volumes)
 
    score  = 0
    detail = []
    if rsi < params["rsi_oversold"]:
        score += 1; detail.append(f"RSI={rsi:.1f} ✓")
    if fast_prev <= slow_prev and fast_now > slow_prev:
        score += 1; detail.append("EMA cross ✓")
    bb_range = bb_upper - bb_lower
    if bb_range > 0 and (price - bb_lower) / bb_range < 0.25:
        score += 1; detail.append("BB inferior ✓")
    prev_hist = 0
    if len(closes) > 30:
        _, _, prev_hist = calculate_macd(closes[:-1], params["macd_fast"], params["macd_slow"], params["macd_signal"])
    if macd_hist > prev_hist and macd_hist < 0:
        score += 1; detail.append("MACD ✓")
    if vol_r >= params["volume_factor"]:
        score += 1; detail.append(f"Vol x{vol_r:.1f} ✓")
 
    sell_signal = (
        rsi > params["rsi_overbought"] or
        (fast_prev >= slow_prev and fast_now < slow_prev) or
        (price > bb_upper and macd_hist < 0)
    )
 
    atr_pct    = atr / price
    sl_dynamic = max(BASE_STOP_LOSS, atr_pct * 1.5)
    tp_dynamic = max(BASE_TAKE_PROFIT, atr_pct * 3.0)
 
    return {
        "price"      : price,
        "rsi"        : rsi,
        "score"      : score,
        "buy_signal" : score >= params["min_score"],
        "sell_signal": sell_signal,
        "detail"     : detail,
        "sl_pct"     : sl_dynamic,
        "tp_pct"     : tp_dynamic,
        "atr"        : atr,
        "vol_ratio"  : vol_r,
        "macd_hist"  : macd_hist,
    }
 
 
# =============================================================
#   CLIENTE DE EXCHANGE
# =============================================================
 
def create_client() -> Client:
    if not API_KEY or not API_SECRET:
        log.error("¡Faltan las API keys!")
        sys.exit(1)
    client = Client(API_KEY, API_SECRET)
    if USE_TESTNET:
        client.API_URL = "https://testnet.binance.vision/api"
        log.info("Conectado al TESTNET de Binance (paper trading)")
    else:
        log.warning("*** Conectado a Binance REAL ***")
    return client
 
def fetch_candles(client: Client, symbol: str, interval: str, limit: int = 120) -> dict:
    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return {
        "opens"  : [float(c[1]) for c in raw],
        "highs"  : [float(c[2]) for c in raw],
        "lows"   : [float(c[3]) for c in raw],
        "closes" : [float(c[4]) for c in raw],
        "volumes": [float(c[5]) for c in raw],
    }
 
def get_balance(client: Client, asset: str = "USDT") -> float:
    info = client.get_asset_balance(asset=asset)
    return float(info["free"]) if info else 0.0
 
def get_lot_rules(client: Client, symbol: str) -> dict:
    try:
        info = client.get_symbol_info(symbol)
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                return {"min_qty": float(f["minQty"]), "step_size": float(f["stepSize"])}
    except Exception:
        pass
    return {"min_qty": 0.001, "step_size": 0.001}
 
def round_step(qty: float, step_size: float) -> float:
    if step_size == 0:
        return qty
    precision = round(-math.log10(step_size))
    factor    = 10 ** precision
    return math.floor(qty * factor) / factor
 
def place_order(client: Client, symbol: str, side: str, qty: float) -> dict:
    binance_side = SIDE_BUY if side == "BUY" else SIDE_SELL
    return client.create_order(symbol=symbol, side=binance_side, type=ORDER_TYPE_MARKET, quantity=qty)
 
def log_trade(symbol, side, price, qty, pnl_pct=None, reason=""):
    line = (
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {side:<4} | {symbol:<12} | "
        f"precio=${price:,.4f} | qty={qty} | pnl={pnl_pct:+.2f}% | {reason}\n"
        if pnl_pct is not None else
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {side:<4} | {symbol:<12} | "
        f"precio=${price:,.4f} | qty={qty}\n"
    )
    with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)
 
 
# =============================================================
#   DASHBOARD WEB (corre en hilo separado)
# =============================================================
 
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crypto Bot — Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Syne:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0b0e13;--sur:#111620;--sur2:#181e2c;--bor:rgba(255,255,255,0.07);--txt:#e8eaf0;--mut:#6b7280;--grn:#10d98c;--red:#f05252;--blu:#5b8ff9;--amb:#f5a623}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:'Syne',sans-serif;font-size:14px;padding-bottom:3rem}
header{display:flex;align-items:center;justify-content:space-between;padding:1rem 1.5rem;border-bottom:1px solid var(--bor);position:sticky;top:0;background:rgba(11,14,19,0.96);backdrop-filter:blur(8px);z-index:100}
.logo{font-size:15px;font-weight:700;letter-spacing:.05em}.logo span{color:var(--grn)}
.pill{display:flex;align-items:center;gap:6px;background:var(--sur);border:1px solid var(--bor);border-radius:20px;padding:4px 12px;font-size:12px}
.dot{width:7px;height:7px;border-radius:50%}
.dot.on{background:var(--grn);animation:pulse 2s infinite}
.dot.off{background:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.main{padding:1.5rem;max-width:1300px;margin:0 auto}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:1rem}
.mcard{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:1rem 1.25rem}
.mlabel{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin-bottom:.5rem}
.mval{font-family:'IBM Plex Mono',monospace;font-size:26px;font-weight:500}
.msub{font-size:11px;color:var(--mut);margin-top:.3rem;font-family:'IBM Plex Mono',monospace}
.wr-bar{background:rgba(255,255,255,0.07);border-radius:3px;height:4px;margin-top:8px}
.wr-fill{height:100%;border-radius:3px;background:var(--grn);transition:width .6s}
.grid{display:grid;grid-template-columns:1.8fr 1fr;gap:10px}
.card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px}
.sec{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin-bottom:.75rem;display:flex;align-items:center;gap:6px}
.sec::after{content:'';flex:1;height:1px;background:var(--bor)}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);padding:0 8px 8px;border-bottom:1px solid var(--bor)}
td{padding:7px 8px;border-bottom:1px solid rgba(255,255,255,0.03);font-family:'IBM Plex Mono',monospace}
.badge{display:inline-block;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px}
.buy{background:rgba(16,217,140,0.12);color:var(--grn)}
.sell{background:rgba(240,82,82,0.12);color:var(--red)}
.crow{display:grid;grid-template-columns:1fr 45px 50px 65px 70px;align-items:center;padding:7px 0;border-bottom:1px solid var(--bor);gap:6px;font-size:12px}
.crow:last-child{border-bottom:none}
.ctag{font-size:10px;background:var(--sur2);padding:1px 6px;border-radius:4px;color:var(--mut);margin-left:4px}
.prow{display:flex;justify-content:space-between;padding:5px 8px;background:var(--sur2);border-radius:5px;margin-bottom:4px}
.pk{font-size:11px;color:var(--mut)}.pv{font-size:11px;color:var(--blu);font-family:'IBM Plex Mono',monospace}
.logline{background:var(--sur2);border-radius:7px;padding:10px 14px;font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--mut);word-break:break-all;border-left:2px solid var(--bor);margin-top:10px}
.empty{text-align:center;padding:1.5rem;color:var(--mut);font-size:12px}
.pgrid{display:grid;grid-template-columns:1fr 1fr;gap:4px}
footer{text-align:center;margin-top:2rem;font-size:11px;color:var(--mut);font-family:'IBM Plex Mono',monospace}
@media(max-width:800px){.metrics{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <div class="logo">CRYPTO<span>BOT</span> · Monitor</div>
  <div style="display:flex;align-items:center;gap:12px">
    <div style="font-size:11px;color:var(--mut)">Actualiza cada 30s · <span id="cd">30</span>s</div>
    <div class="pill"><div class="dot off" id="sdot"></div><span id="stxt">Cargando...</span></div>
  </div>
</header>
<div class="main">
  <div class="metrics">
    <div class="mcard"><div class="mlabel">Total trades</div><div class="mval" id="m1" style="color:var(--blu)">—</div><div class="msub" id="m1s">— / —</div></div>
    <div class="mcard"><div class="mlabel">Win rate</div><div class="mval" id="m2">—</div><div class="wr-bar"><div class="wr-fill" id="wrb" style="width:0%"></div></div></div>
    <div class="mcard"><div class="mlabel">PnL acumulado</div><div class="mval" id="m3">—</div><div class="msub">todos los trades cerrados</div></div>
    <div class="mcard"><div class="mlabel">Último ajuste</div><div class="mval" id="m4" style="color:var(--amb);font-size:16px">—</div><div class="msub" id="m4s">aprendizaje automático</div></div>
  </div>
  <div class="grid">
    <div class="card"><div class="sec">Últimas operaciones</div><div id="trades"><div class="empty">Sin trades aún</div></div></div>
    <div>
      <div class="card"><div class="sec">Rendimiento por moneda</div><div id="coins"><div class="empty">Sin datos aún</div></div></div>
      <div class="card"><div class="sec">Parámetros actuales</div><div class="pgrid" id="params"><div class="empty" style="grid-column:1/-1">Sin datos aún</div></div></div>
    </div>
  </div>
  <div class="card"><div class="sec">Última línea del log</div><div class="logline" id="logline">Esperando...</div></div>
</div>
<footer>Dashboard en vivo · actualización automática cada 30s</footer>
<script>
const CN={SOLUSDT:'Solana',DOGEUSDT:'Dogecoin',AVAXUSDT:'Avalanche',POLUSDT:'Polygon',LINKUSDT:'Chainlink',DOTUSDT:'Polkadot',ADAUSDT:'Cardano',LTCUSDT:'Litecoin'};
const PL={rsi_period:'RSI período',rsi_oversold:'RSI compra',rsi_overbought:'RSI venta',fast_ema:'EMA rápida',slow_ema:'EMA lenta',min_score:'Score mín.',volume_factor:'Vol. factor',bb_period:'BB período'};
 
async function load(){
  try{
    const d=await(await fetch('/api/data')).json();
    const alive=d.status.running;
    document.getElementById('sdot').className='dot '+(alive?'on':'off');
    document.getElementById('stxt').textContent=alive?'Bot activo':'Bot detenido';
    document.getElementById('m1').textContent=d.total||'0';
    document.getElementById('m1s').textContent=`${d.wins} ganados / ${d.total-d.wins} perdidos`;
    const wr=d.win_rate;
    const we=document.getElementById('m2');
    we.textContent=wr+'%';
    we.style.color=wr>=55?'var(--grn)':wr>=45?'var(--amb)':'var(--red)';
    document.getElementById('wrb').style.width=Math.min(wr,100)+'%';
    document.getElementById('wrb').style.background=wr>=55?'var(--grn)':wr>=45?'var(--amb)':'var(--red)';
    const pnl=d.pnl_total;
    const pe=document.getElementById('m3');
    pe.textContent=(pnl>=0?'+':'')+pnl.toFixed(2)+'%';
    pe.style.color=pnl>=0?'var(--grn)':'var(--red)';
    const le=d.last_exp;
    if(le){document.getElementById('m4').textContent='Trade #'+le.at_trade;document.getElementById('m4s').textContent=le.adjustments&&le.adjustments.length?le.adjustments.join(' · '):'Sin cambios';}
    document.getElementById('logline').textContent=d.status.last_line||'—';
    const trades=d.trades||[];
    document.getElementById('trades').innerHTML=trades.length===0?'<div class="empty">Sin trades aún.<br>El bot los irá mostrando aquí.</div>':
      `<table><thead><tr><th>Hora</th><th>Tipo</th><th>Moneda</th><th>Precio</th><th>PnL</th><th>Motivo</th></tr></thead><tbody>
      ${trades.slice(0,20).map(t=>{
        const b=t.side.trim().toUpperCase()==='BUY';
        const p=t.pnl!==''?parseFloat(t.pnl):null;
        const ps=p!==null?`<span style="color:${p>=0?'var(--grn)':'var(--red)'}">${p>=0?'+':''}${p.toFixed(2)}%</span>`:'—';
        return `<tr><td style="color:var(--mut)">${(t.time||'').slice(11,16)}</td><td><span class="badge ${b?'buy':'sell'}">${t.side.trim()}</span></td><td>${(t.symbol||'').replace('USDT','')}</td><td>$${parseFloat(t.price||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4})}</td><td>${ps}</td><td style="color:var(--mut);font-size:10px">${(t.reason||'').slice(0,14)}</td></tr>`;
      }).join('')}</tbody></table>`;
    const stats=d.sym_stats||{};
    const sk=Object.keys(stats);
    document.getElementById('coins').innerHTML=sk.length===0?'<div class="empty">Sin datos aún</div>':
      sk.sort((a,b)=>(stats[b].trades>0?stats[b].wins/stats[b].trades:0)-(stats[a].trades>0?stats[a].wins/stats[a].trades:0))
        .map(s=>{const st=stats[s];const wr2=st.trades>0?Math.round(st.wins/st.trades*100):0;const pnl2=st.total_pnl||0;const c=wr2>=55?'var(--grn)':wr2>=40?'var(--amb)':'var(--red)';
          return `<div class="crow"><div>${(CN[s]||s).slice(0,9)}<span class="ctag">${s.replace('USDT','')}</span></div><div style="color:var(--mut)">${st.trades}t</div><div style="color:${c}">${wr2}%</div><div style="color:${pnl2>=0?'var(--grn)':'var(--red)'}">${pnl2>=0?'+':''}${pnl2.toFixed(2)}%</div><div style="background:rgba(255,255,255,.07);border-radius:3px;height:3px;width:60px"><div style="width:${wr2}%;height:100%;border-radius:3px;background:${c}"></div></div></div>`;
        }).join('');
    const params=d.params||{};
    const pk=Object.keys(PL).filter(k=>params[k]!==undefined);
    document.getElementById('params').innerHTML=pk.length===0?'<div class="empty" style="grid-column:1/-1">Sin datos aún</div>':
      pk.map(k=>`<div class="prow"><span class="pk">${PL[k]}</span><span class="pv">${params[k]}</span></div>`).join('');
  }catch(e){console.error(e);}
}
 
let s=30;
function tick(){s--;document.getElementById('cd').textContent=s;if(s<=0){s=30;load();}}
load();setInterval(tick,1000);
</script>
</body>
</html>"""
 
 
def read_learning():
    if not os.path.exists(LEARNING_FILE):
        return {}
    try:
        with open(LEARNING_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}
 
def read_trades():
    if not os.path.exists(TRADE_LOG_FILE):
        return []
    trades = []
    try:
        with open(TRADE_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 5:
                    continue
                trade = {"time": parts[0], "side": parts[1], "symbol": parts[2], "price": "", "qty": "", "pnl": "", "reason": parts[5] if len(parts) > 5 else ""}
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
    return list(reversed(trades))
 
def read_status():
    if not os.path.exists(BOT_LOG_FILE):
        return {"running": False, "last_line": "Bot no iniciado.", "last_time": "—"}
    try:
        with open(BOT_LOG_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        if not lines:
            return {"running": False, "last_line": "Sin actividad.", "last_time": "—"}
        last = lines[-1]
        match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", last)
        last_time = match.group(1) if match else "—"
        alive = False
        if match:
            try:
                t = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
                alive = (datetime.now() - t).total_seconds() < 300
            except Exception:
                pass
        return {"running": alive, "last_line": last[-120:], "last_time": last_time}
    except Exception:
        return {"running": False, "last_line": "Error.", "last_time": "—"}
 
def build_api_data():
    learning = read_learning()
    trades   = read_trades()
    status   = read_status()
    total    = learning.get("total_trades", 0)
    wins     = learning.get("total_wins", 0)
    wr       = round(wins / total * 100, 1) if total > 0 else 0
    pnl_total = sum(float(t["pnl"]) for t in trades if t["pnl"])
    experiments = learning.get("param_experiments", [])
    return {
        "status"   : status,
        "total"    : total,
        "wins"     : wins,
        "win_rate" : wr,
        "pnl_total": round(pnl_total, 2),
        "sym_stats": learning.get("symbol_stats", {}),
        "params"   : learning.get("params", {}),
        "last_exp" : experiments[-1] if experiments else None,
        "trades"   : trades[:50],
    }
 
 
class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
        elif self.path == "/api/data":
            data = build_api_data()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()
 
def start_dashboard():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), DashboardHandler) as httpd:
        log.info(f"Dashboard corriendo en puerto {PORT}")
        httpd.serve_forever()
 
 
# =============================================================
#   LOOP PRINCIPAL DEL BOT
# =============================================================
 
def run():
    log.info("=" * 55)
    log.info("  BOT ADAPTATIVO — ALTCOINS VOLÁTILES")
    log.info(f"  Monedas  : {', '.join(SYMBOLS)}")
    log.info(f"  Intervalo: {INTERVAL}  |  Modo: {'TESTNET' if USE_TESTNET else '*** LIVE ***'}")
    log.info(f"  Max posiciones: {MAX_OPEN_TRADES}")
    log.info("=" * 55)
 
    client   = create_client()
    learning = LearningSystem()
    learning.print_summary()
    positions = {}
    cycle = 0
 
    while True:
        try:
            cycle += 1
            now      = datetime.now()
            hour_now = now.hour
            params   = learning.get_params()
            balance  = get_balance(client, "USDT")
            log.info(f"\n── Ciclo #{cycle} | {now.strftime('%H:%M:%S')} | Balance: ${balance:.2f} | Posiciones: {len(positions)}/{MAX_OPEN_TRADES} ──")
 
            ranked_symbols = learning.get_best_symbols()
            analyses = {}
            for symbol in ranked_symbols:
                try:
                    candles = fetch_candles(client, symbol, INTERVAL)
                    result  = analyze_symbol(candles, params)
                    result["hour_q"] = learning.get_hour_quality(hour_now)
                    analyses[symbol] = result
                    log.info(f"  {symbol:<12} ${result['price']:>10,.4f} | RSI={result['rsi']:>5.1f} | Score={result['score']}/6 | Vol x{result['vol_ratio']:.1f}")
                except Exception as e:
                    log.warning(f"  Error analizando {symbol}: {e}")
 
            for symbol, pos in list(positions.items()):
                if symbol not in analyses:
                    continue
                a       = analyses[symbol]
                price   = a["price"]
                entry   = pos["entry"]
                pnl_pct = (price - entry) / entry * 100
                exit_reason = None
                if price <= pos["stop_loss"]:
                    exit_reason = "STOP LOSS"
                elif price >= pos["take_profit"]:
                    exit_reason = "TAKE PROFIT"
                elif a["sell_signal"]:
                    exit_reason = "SEÑAL SELL"
                if exit_reason:
                    try:
                        place_order(client, symbol, "SELL", pos["qty"])
                        won = price > entry
                        log.info(f"  {'✔' if won else '✘'} CERRAR {symbol} @ ${price:,.4f} | PnL: {pnl_pct:+.2f}% | {exit_reason}")
                        log_trade(symbol, "SELL", price, pos["qty"], pnl_pct, exit_reason)
                        learning.record_trade(symbol, pnl_pct, params, won)
                        del positions[symbol]
                    except Exception as e:
                        log.error(f"  Error cerrando {symbol}: {e}")
 
            if len(positions) < MAX_OPEN_TRADES and balance > 20:
                candidates = [(s, a) for s, a in analyses.items() if a["buy_signal"] and s not in positions]
                candidates.sort(key=lambda x: x[1]["score"], reverse=True)
                for symbol, a in candidates:
                    if len(positions) >= MAX_OPEN_TRADES:
                        break
                    price     = a["price"]
                    sl_price  = round(price * (1 - a["sl_pct"]), 6)
                    tp_price  = round(price * (1 + a["tp_pct"]), 6)
                    lot       = get_lot_rules(client, symbol)
                    trade_val = balance * MAX_TRADE_PCT / (MAX_OPEN_TRADES - len(positions))
                    qty       = round_step(trade_val / price, lot["step_size"])
                    if qty < lot["min_qty"]:
                        log.warning(f"  {symbol}: qty {qty} menor al mínimo, saltando.")
                        continue
                    try:
                        place_order(client, symbol, "BUY", qty)
                        positions[symbol] = {"entry": price, "qty": qty, "stop_loss": sl_price, "take_profit": tp_price, "open_time": now.isoformat()}
                        log.info(f"  ✔ ABRIENDO {symbol} @ ${price:,.4f} | Qty: {qty} | SL: ${sl_price:,.4f} | TP: ${tp_price:,.4f} | Score: {a['score']}/6")
                        log_trade(symbol, "BUY", price, qty)
                    except Exception as e:
                        log.error(f"  Error abriendo {symbol}: {e}")
 
            time.sleep(POLL_SECONDS)
 
        except KeyboardInterrupt:
            log.info("\nBot detenido.")
            learning.print_summary()
            break
        except Exception as e:
            log.error(f"Error inesperado: {e} — reintentando en 30s...")
            time.sleep(30)
 
 
# =============================================================
#   ENTRADA — arranca dashboard + bot juntos
# =============================================================
 
if __name__ == "__main__":
    # Arranca el dashboard en un hilo separado
    t = threading.Thread(target=start_dashboard, daemon=True)
    t.start()
    # Arranca el bot en el hilo principal
    run()
 
