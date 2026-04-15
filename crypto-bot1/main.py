"""
=============================================================
  BOT ADAPTATIVO v3.1 - Altcoins Volatiles
  Cambios vs v3 basados en analisis de 22 trades reales:

  NUEVO EN v3.1:
  - sell_signal SOLO cierra si PnL >= 1.5% (antes cerraba a 0.3%)
    Razon: trades cerraban con 0.024-0.42% promedio, lo que no
    cubre las comisiones de Binance (0.2% por trade completo).
  - MAX_HOLD_HOURS subido de 4 a 8 horas
    Razon: TP en 8% necesita mas tiempo para alcanzarse.
  - MIN_SELL_PNL = 1.5% — umbral minimo para cerrar por señal
    (SL, TP y MAX_HOLD siguen funcionando sin restriccion)

  HEREDADO DE v3:
  - Solo SOL, LINK, DOGE (LTC y DOT eliminados por perdedores)
  - TP 8%, SL 3%, Trade 20% del portfolio
  - Horas H1, H3 bloqueadas (mal historial)
  - Circuit breaker sobre portfolio total
  - Recovery de posiciones fantasma (-2010)
  - No compra doble de la misma moneda
  - Trailing stop loss
  - Learning corregido (no baja min_score < 3)
=============================================================
"""

import time, logging, sys, json, os, math, re, threading
import http.server, socketserver
from datetime import datetime, timedelta

try:
    from binance.client import Client
    from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
except ImportError:
    print("Falta instalar: pip install python-binance")
    sys.exit(1)

API_KEY     = os.environ.get("API_KEY", "")
API_SECRET  = os.environ.get("API_SECRET", "")
USE_TESTNET = False

SYMBOLS = [
    "SOLUSDT",   # 66% WR historico
    "LINKUSDT",  # 66% WR historico
    "DOGEUSDT",  # 86% WR en v3
]

BLOCKED_HOURS = {1, 3}  # 0% y 20% WR con trades suficientes

INTERVAL         = "5m"
MAX_TRADE_PCT    = 0.20
BASE_STOP_LOSS   = 0.03
BASE_TAKE_PROFIT = 0.08
MAX_OPEN_TRADES  = 2

MAX_HOLD_HOURS    = 8      # subido de 4 — da tiempo al TP de 8%
COIN_COOLDOWN_MIN = 15
CIRCUIT_BREAKER_PCT = 0.15
TRAIL_TRIGGER_PCT   = 0.02
TRAIL_DISTANCE_PCT  = 0.02

# CRITICO: sell_signal solo cierra si hay ganancia minima
# Evita cerrar trades con 0.3% que no cubren comisiones
MIN_SELL_SIGNAL_PNL = 1.5  # porcentaje minimo de ganancia

INITIAL_PARAMS = {
    "rsi_period"     : 14,
    "rsi_oversold"   : 28,
    "rsi_overbought" : 72,
    "fast_ema"       : 9,
    "slow_ema"       : 21,
    "bb_period"      : 20,
    "bb_std"         : 2.0,
    "macd_fast"      : 12,
    "macd_slow"      : 26,
    "macd_signal"    : 9,
    "atr_period"     : 14,
    "min_score"      : 3,
    "volume_factor"  : 1.2,
}

POLL_SECONDS   = 60
LEARNING_FILE  = "bot_learning.json"
POSITIONS_FILE = "bot_positions.json"
TRADE_LOG_FILE = "trade_history.log"
BOT_LOG_FILE   = "bot.log"
PORT           = int(os.environ.get("PORT", 8080))

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
#   LEARNING SYSTEM
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
                    log.info("Aprendizaje cargado: %d trades.", loaded.get("total_trades", 0))
                    return loaded
            except Exception:
                pass
        return {
            "total_trades": 0, "total_wins": 0,
            "params": dict(INITIAL_PARAMS),
            "symbol_stats": {}, "hour_stats": {},
            "param_experiments": [], "last_adjusted": None,
        }

    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(self.data, f, indent=2)

    def get_params(self):
        return dict(self.data["params"])

    def record_trade(self, symbol, pnl_pct, params_used, won):
        hour = str(datetime.now().hour)
        self.data["total_trades"] += 1
        if won:
            self.data["total_wins"] += 1
        if symbol not in self.data["symbol_stats"]:
            self.data["symbol_stats"][symbol] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        s = self.data["symbol_stats"][symbol]
        s["trades"] += 1
        s["total_pnl"] = round(s["total_pnl"] + pnl_pct, 4)
        if won:
            s["wins"] += 1
        if hour not in self.data["hour_stats"]:
            self.data["hour_stats"][hour] = {"trades": 0, "wins": 0}
        self.data["hour_stats"][hour]["trades"] += 1
        if won:
            self.data["hour_stats"][hour]["wins"] += 1
        if self.data["total_trades"] % 10 == 0:
            self._adapt_params()
        self._save()

    def _adapt_params(self):
        total = self.data["total_trades"]
        wins  = self.data["total_wins"]
        wr    = wins / total if total > 0 else 0
        p     = self.data["params"]
        adj   = []
        log.info("[LEARNING] Win rate: %.1f%% en %d trades.", wr * 100, total)

        # min_score: nunca baja de 3
        if wr < 0.50 and p["min_score"] < 5:
            p["min_score"] = min(p["min_score"] + 1, 5)
            adj.append("min_score up")
        elif wr > 0.65 and p["min_score"] > 3:
            p["min_score"] = max(p["min_score"] - 1, 3)
            adj.append("min_score down")

        # RSI: solo relaja si WR > 0.60
        if wr <= 0.50:
            p["rsi_oversold"]   = max(22, p["rsi_oversold"] - 2)
            p["rsi_overbought"] = min(78, p["rsi_overbought"] + 2)
            adj.append("RSI stricter")
        elif wr > 0.60:
            p["rsi_oversold"]   = min(32, p["rsi_oversold"] + 1)
            p["rsi_overbought"] = max(68, p["rsi_overbought"] - 1)
            adj.append("RSI looser")

        self.data["param_experiments"].append({
            "at_trade": total, "win_rate": round(wr, 3),
            "adjustments": adj, "new_params": dict(p),
        })
        if adj:
            self.data["last_adjusted"] = datetime.now().isoformat()
            log.info("[LEARNING] Ajustes: %s", ", ".join(adj))
        else:
            log.info("[LEARNING] Sin ajustes.")
        self._save()

    def get_best_symbols(self):
        stats  = self.data["symbol_stats"]
        ranked = []
        for sym in SYMBOLS:
            if sym in stats and stats[sym]["trades"] >= 5:
                wr = stats[sym]["wins"] / stats[sym]["trades"]
            else:
                wr = 0.5
            ranked.append((sym, wr))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in ranked]

    def get_hour_quality(self, hour):
        h = str(hour)
        s = self.data["hour_stats"]
        return s[h]["wins"] / s[h]["trades"] if h in s and s[h]["trades"] >= 5 else 0.5

    def print_summary(self):
        t = self.data["total_trades"]
        w = self.data["total_wins"]
        log.info("=" * 55)
        log.info("  BOT v3.1 | %d trades | WR %.1f%%",
                 t, w / t * 100 if t > 0 else 0)
        log.info("  TP %.0f%% | SL %.0f%% | Hold %dh | MinSell %.1f%%",
                 BASE_TAKE_PROFIT * 100, BASE_STOP_LOSS * 100,
                 MAX_HOLD_HOURS, MIN_SELL_SIGNAL_PNL)
        log.info("=" * 55)


# =============================================================
#   POSITION PERSISTENCE
# =============================================================

def load_positions():
    if not os.path.exists(POSITIONS_FILE):
        return {}
    try:
        with open(POSITIONS_FILE, "r") as f:
            positions = json.load(f)
            log.info("Posiciones cargadas: %d.", len(positions))
            return positions
    except Exception as e:
        log.error("Error cargando posiciones: %s", e)
        return {}

def save_positions(positions):
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump(positions, f, indent=2)
    except Exception as e:
        log.error("Error guardando posiciones: %s", e)

def reconcile_positions(client, positions):
    if not positions:
        return positions
    try:
        account  = client.get_account()
        balances = {
            b["asset"]: float(b["free"]) + float(b["locked"])
            for b in account["balances"]
            if float(b["free"]) + float(b["locked"]) > 0
        }
        to_remove = []
        for symbol, pos in positions.items():
            asset       = symbol.replace("USDT", "")
            held        = balances.get(asset, 0)
            qty_tracked = float(pos["qty"])
            if held < qty_tracked * 0.95:
                log.warning("  Reconcile: %s no encontrado (%.4f vs %.4f) — eliminando.",
                            symbol, qty_tracked, held)
                to_remove.append(symbol)
            elif held > qty_tracked * 1.05:
                log.warning("  Reconcile: %s actualizando qty %.4f -> %.4f.",
                            symbol, qty_tracked, held)
                positions[symbol]["qty"] = held
            else:
                log.info("  Reconcile: %s OK (%.4f).", symbol, held)
        for symbol in to_remove:
            del positions[symbol]
        save_positions(positions)
        log.info("Reconciliacion lista. %d posicion(es).", len(positions))
    except Exception as e:
        log.error("Error en reconciliacion: %s", e)
    return positions


def get_portfolio_value(client, positions, analyses):
    usdt      = get_balance(client, "USDT")
    portfolio = usdt
    for symbol, pos in positions.items():
        qty = float(pos["qty"])
        if symbol in analyses:
            price = analyses[symbol]["price"]
        else:
            try:
                raw   = client.get_symbol_ticker(symbol=symbol)
                price = float(raw["price"])
            except Exception:
                price = float(pos["entry"])
        portfolio += price * qty
    return portfolio


# =============================================================
#   INDICATORS
# =============================================================

def ema(prices, period):
    k = 2 / (period + 1)
    r = [prices[0]]
    for p in prices[1:]:
        r.append(p * k + r[-1] * (1 - k))
    return r

def calculate_rsi(prices, period):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    recent = deltas[-period:]
    ag = sum(d for d in recent if d > 0) / period
    al = sum(-d for d in recent if d < 0) / period
    if ag == 0 and al == 0:
        return 50.0
    if al == 0:
        return 50.0
    rsi = round(100 - 100 / (1 + ag / al), 2)
    if rsi <= 1.0 or rsi >= 99.0:
        return 50.0
    return rsi

def calculate_bollinger(prices, period, std_mult):
    if len(prices) < period:
        return prices[-1], prices[-1], prices[-1]
    w   = prices[-period:]
    mid = sum(w) / period
    std = math.sqrt(sum((p - mid)**2 for p in w) / period)
    return mid + std * std_mult, mid, mid - std * std_mult

def calculate_macd(prices, fast, slow, sig):
    if len(prices) < slow + sig:
        return 0, 0, 0
    ml = [f - s for f, s in zip(ema(prices, fast), ema(prices, slow))]
    sl = ema(ml, sig)
    return ml[-1], sl[-1], ml[-1] - sl[-1]

def calculate_atr(highs, lows, closes, period):
    if len(closes) < 2:
        return closes[-1] * 0.02
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    return sum(trs[-period:]) / min(period, len(trs))

def calculate_volume_ratio(volumes, period=20):
    if len(volumes) < 2:
        return 1.0
    avg = sum(volumes[-period-1:-1]) / min(period, len(volumes)-1)
    return volumes[-1] / avg if avg > 0 else 1.0


# =============================================================
#   SIGNAL ANALYSIS
# =============================================================

def analyze_symbol(candles, params):
    closes  = candles["closes"]
    highs   = candles["highs"]
    lows    = candles["lows"]
    volumes = candles["volumes"]
    price   = closes[-1]

    if len(set(closes[-5:])) == 1:
        return None

    rsi           = calculate_rsi(closes, params["rsi_period"])
    fe            = ema(closes, params["fast_ema"])
    se            = ema(closes, params["slow_ema"])
    bb_u, _, bb_l = calculate_bollinger(closes, params["bb_period"], params["bb_std"])
    _, _, mh      = calculate_macd(closes, params["macd_fast"], params["macd_slow"], params["macd_signal"])
    _, _, ph      = calculate_macd(closes[:-1], params["macd_fast"], params["macd_slow"], params["macd_signal"]) \
                    if len(closes) > 30 else (0, 0, 0)
    atr           = calculate_atr(highs, lows, closes, params["atr_period"])
    vol_r         = calculate_volume_ratio(volumes)

    score, detail = 0, []
    if rsi < params["rsi_oversold"]:
        score += 1; detail.append("RSI=%.1f" % rsi)
    if len(fe) >= 2 and len(se) >= 2 and fe[-2] <= se[-2] and fe[-1] > se[-1]:
        score += 1; detail.append("EMA cross")
    bb_range = bb_u - bb_l
    if bb_range > 0 and (price - bb_l) / bb_range < 0.25:
        score += 1; detail.append("BB lower")
    if mh > ph and mh < 0:
        score += 1; detail.append("MACD")
    if vol_r >= params["volume_factor"]:
        score += 1; detail.append("Vol x%.1f" % vol_r)

    sell = (rsi > params["rsi_overbought"] or
            (len(fe) >= 2 and len(se) >= 2 and fe[-2] >= se[-2] and fe[-1] < se[-1]) or
            (price > bb_u and mh < 0))

    atr_pct = atr / price
    return {
        "price":       price,
        "rsi":         rsi,
        "score":       score,
        "buy_signal":  score >= params["min_score"],
        "sell_signal": sell,
        "detail":      detail,
        "sl_pct":      max(BASE_STOP_LOSS, atr_pct * 1.5),
        "tp_pct":      max(BASE_TAKE_PROFIT, atr_pct * 4.5),
        "atr":         atr,
        "vol_ratio":   vol_r,
        "macd_hist":   mh,
    }


# =============================================================
#   EXCHANGE
# =============================================================

def create_client():
    if not API_KEY or not API_SECRET:
        log.error("Faltan las API keys!")
        sys.exit(1)
    client = Client(API_KEY, API_SECRET)
    if USE_TESTNET:
        client.API_URL = "https://testnet.binance.vision/api"
        log.info("Conectado al TESTNET")
    else:
        log.warning("*** Conectado a Binance REAL ***")
    return client

def fetch_candles(client, symbol, interval, limit=120):
    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return {
        "opens":   [float(c[1]) for c in raw],
        "highs":   [float(c[2]) for c in raw],
        "lows":    [float(c[3]) for c in raw],
        "closes":  [float(c[4]) for c in raw],
        "volumes": [float(c[5]) for c in raw],
    }

def get_balance(client, asset="USDT"):
    info = client.get_asset_balance(asset=asset)
    return float(info["free"]) if info else 0.0

def get_lot_rules(client, symbol):
    try:
        info = client.get_symbol_info(symbol)
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                return {"min_qty": float(f["minQty"]), "step_size": float(f["stepSize"])}
    except Exception:
        pass
    return {"min_qty": 0.001, "step_size": 0.001}

def round_step(qty, step_size):
    if step_size == 0:
        return qty
    factor = 10 ** round(-math.log10(step_size))
    return math.floor(qty * factor) / factor

def place_order(client, symbol, side, qty):
    return client.create_order(
        symbol=symbol,
        side=SIDE_BUY if side == "BUY" else SIDE_SELL,
        type=ORDER_TYPE_MARKET, quantity=qty,
    )

def log_trade(symbol, side, price, qty, pnl_pct=None, reason=""):
    if pnl_pct is not None:
        line = "%s | %-4s | %-12s | precio=$%.4f | qty=%s | pnl=%+.2f%% | %s\n" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), side, symbol, price, qty, pnl_pct, reason)
    else:
        line = "%s | %-4s | %-12s | precio=$%.4f | qty=%s\n" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), side, symbol, price, qty)
    with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


# =============================================================
#   DASHBOARD HTML
# =============================================================

DASHBOARD_HTML = (
'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
'<meta name="viewport" content="width=device-width,initial-scale=1">'
'<title>Crypto Bot v3.1</title>'
'<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Syne:wght@400;700&display=swap" rel="stylesheet">'
'<style>'
':root{--bg:#0b0e13;--sur:#111620;--sur2:#181e2c;--bor:rgba(255,255,255,0.07);--txt:#e8eaf0;--mut:#6b7280;--grn:#10d98c;--red:#f05252;--blu:#5b8ff9;--amb:#f5a623}'
'*{box-sizing:border-box;margin:0;padding:0}'
'body{background:var(--bg);color:var(--txt);font-family:Syne,sans-serif;font-size:14px;padding-bottom:3rem}'
'header{display:flex;align-items:center;justify-content:space-between;padding:1rem 1.5rem;border-bottom:1px solid var(--bor);position:sticky;top:0;background:rgba(11,14,19,0.96);z-index:100}'
'.logo{font-size:15px;font-weight:700}.logo span{color:var(--grn)}'
'.pill{display:flex;align-items:center;gap:6px;background:var(--sur);border:1px solid var(--bor);border-radius:20px;padding:4px 12px;font-size:12px}'
'.dot{width:7px;height:7px;border-radius:50%;display:inline-block}.dot.on{background:var(--grn)}.dot.off{background:var(--red)}'
'.tabs{display:flex;gap:6px;padding:.75rem 1.5rem;border-bottom:1px solid var(--bor)}'
'.tab{padding:5px 14px;border-radius:20px;font-size:12px;font-weight:700;cursor:pointer;border:1px solid var(--bor);background:transparent;color:var(--mut)}'
'.tab.active{background:var(--txt);color:var(--bg);border-color:transparent}'
'.panel{display:none;padding:1.5rem;max-width:1300px;margin:0 auto}.panel.active{display:block}'
'.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:1rem}'
'.mcard{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:1rem 1.25rem}'
'.mlabel{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin-bottom:.5rem}'
'.mval{font-family:IBM Plex Mono,monospace;font-size:26px;font-weight:500}'
'.msub{font-size:11px;color:var(--mut);margin-top:.3rem;font-family:IBM Plex Mono,monospace}'
'.wr-bar{background:rgba(255,255,255,.07);border-radius:3px;height:4px;margin-top:8px}'
'.wr-fill{height:100%;border-radius:3px;background:var(--grn);transition:width .6s}'
'.card{background:var(--sur);border:1px solid var(--bor);border-radius:10px;padding:1rem 1.25rem;margin-bottom:10px}'
'.sec{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--mut);margin-bottom:.75rem}'
'table{width:100%;border-collapse:collapse;font-size:12px}'
'th{text-align:left;font-size:10px;font-weight:700;text-transform:uppercase;color:var(--mut);padding:0 8px 8px;border-bottom:1px solid var(--bor)}'
'td{padding:7px 8px;border-bottom:1px solid rgba(255,255,255,.03);font-family:IBM Plex Mono,monospace}'
'.badge{display:inline-block;font-size:10px;font-weight:700;padding:2px 7px;border-radius:4px}'
'.buy{background:rgba(16,217,140,.12);color:var(--grn)}.sell{background:rgba(240,82,82,.12);color:var(--red)}'
'.crow{display:grid;grid-template-columns:1fr 45px 50px 70px 70px;align-items:center;padding:7px 0;border-bottom:1px solid var(--bor);gap:6px;font-size:12px}'
'.ctag{font-size:10px;background:var(--sur2);padding:1px 6px;border-radius:4px;color:var(--mut);margin-left:4px}'
'.prow{display:flex;justify-content:space-between;padding:5px 8px;background:var(--sur2);border-radius:5px;margin-bottom:4px}'
'.pk{font-size:11px;color:var(--mut)}.pv{font-size:11px;color:var(--blu);font-family:IBM Plex Mono,monospace}'
'.logline{background:var(--sur2);border-radius:7px;padding:10px 14px;font-family:IBM Plex Mono,monospace;font-size:11px;color:var(--mut);word-break:break-all;border-left:2px solid var(--bor);margin-top:10px}'
'.pgrid{display:grid;grid-template-columns:1fr 1fr;gap:4px}'
'.empty{text-align:center;padding:1.5rem;color:var(--mut);font-size:12px}'
'.dl-btn{display:flex;align-items:center;gap:8px;background:var(--sur2);border:1px solid var(--bor);border-radius:8px;padding:10px 16px;color:var(--txt);text-decoration:none;font-size:13px;font-weight:700;margin-bottom:8px;width:100%}'
'.copy-area{background:var(--sur2);border:1px solid var(--bor);border-radius:8px;padding:12px;font-family:IBM Plex Mono,monospace;font-size:11px;color:var(--mut);margin-top:8px;word-break:break-all;max-height:200px;overflow:auto}'
'.copy-btn{background:var(--blu);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:12px;font-weight:700;cursor:pointer;margin-top:8px}'
'.alert-crit{background:rgba(240,82,82,.1);border-left:3px solid var(--red);color:var(--red);border-radius:8px;padding:10px 14px;font-size:12px;margin-bottom:8px}'
'footer{text-align:center;margin-top:2rem;font-size:11px;color:var(--mut);font-family:IBM Plex Mono,monospace}'
'@media(max-width:800px){.metrics{grid-template-columns:1fr 1fr}}'
'</style></head><body>'
'<header><div class="logo">CRYPTO<span>BOT</span> v3.1</div>'
'<div style="display:flex;align-items:center;gap:12px">'
'<div style="font-size:11px;color:var(--mut)">Refresh <span id="cd">30</span>s</div>'
'<div class="pill"><span class="dot off" id="sdot"></span>&nbsp;<span id="stxt">Loading</span></div>'
'</div></header>'
'<div class="tabs">'
'<button class="tab active" id="tab-overview" onclick="showTab(\'overview\')">Overview</button>'
'<button class="tab" id="tab-trades" onclick="showTab(\'trades\')">Trades</button>'
'<button class="tab" id="tab-coins" onclick="showTab(\'coins\')">Coins</button>'
'<button class="tab" id="tab-params" onclick="showTab(\'params\')">Parameters</button>'
'<button class="tab" id="tab-files" onclick="showTab(\'files\')">Download Files</button>'
'</div>'
'<div id="t-overview" class="panel active">'
'<div id="alerts-box"></div>'
'<div class="metrics">'
'<div class="mcard"><div class="mlabel">Total trades</div><div class="mval" id="m1" style="color:var(--blu)">0</div><div class="msub" id="m1s">0W / 0L</div></div>'
'<div class="mcard"><div class="mlabel">Win rate</div><div class="mval" id="m2">0%</div><div class="wr-bar"><div class="wr-fill" id="wrb" style="width:0%"></div></div></div>'
'<div class="mcard"><div class="mlabel">Portfolio</div><div class="mval" id="m3">$0.00</div><div class="msub" id="m3s">inicio: $0.00</div></div>'
'<div class="mcard"><div class="mlabel">Ultimo ajuste</div><div class="mval" id="m4" style="color:var(--amb);font-size:16px">-</div><div class="msub" id="m4s">auto learning</div></div>'
'</div>'
'<div class="card"><div class="sec">Last log line</div><div class="logline" id="logline">Waiting...</div></div>'
'</div>'
'<div id="t-trades" class="panel"><div class="card"><div class="sec">Trade history</div><div id="trade-list"><div class="empty">No trades yet</div></div></div></div>'
'<div id="t-coins" class="panel"><div class="card"><div class="sec">Performance by coin</div><div id="coin-list"><div class="empty">No data yet</div></div></div></div>'
'<div id="t-params" class="panel"><div class="card"><div class="sec">Current parameters</div><div class="pgrid" id="params-grid"><div class="empty" style="grid-column:1/-1">No data yet</div></div></div></div>'
'<div id="t-files" class="panel"><div class="card" style="max-width:500px">'
'<div class="sec">Download bot files</div>'
'<a class="dl-btn" href="/api/learning" download="bot_learning.json">bot_learning.json</a>'
'<a class="dl-btn" href="/api/trades" download="trade_history.log">trade_history.log</a>'
'<a class="dl-btn" href="/api/logs" download="bot.log">bot.log</a>'
'<div style="margin-top:1.5rem"><div class="sec">Reset all bot data</div>'
'<p style="font-size:12px;color:var(--mut);margin-bottom:.75rem">Sell everything on Binance first.</p>'
'<button class="copy-btn" style="background:var(--red)" onclick="doReset()">Reset all files</button>'
'<div id="reset-msg" style="font-size:12px;margin-top:8px;color:var(--grn)"></div></div>'
'<div style="margin-top:1.5rem"><div class="sec">Copy learning data</div>'
'<div class="copy-area" id="json-preview">Loading...</div>'
'<button class="copy-btn" onclick="copyJson()">Copy to clipboard</button>'
'</div></div></div>'
'<footer>v3.1 | SOL+LINK+DOGE | TP 8% | SL 3% | Hold 8h | MinSell 1.5% | auto-refresh 30s</footer>'
'<script>'
'var CN={SOLUSDT:"Solana",DOGEUSDT:"Dogecoin",LINKUSDT:"Chainlink"};'
'var PL={rsi_period:"RSI period",rsi_oversold:"RSI buy",rsi_overbought:"RSI sell",fast_ema:"Fast EMA",slow_ema:"Slow EMA",min_score:"Min score",volume_factor:"Vol factor"};'
'var rawJson="";'
'function showTab(id){document.querySelectorAll(".panel").forEach(function(p){p.classList.remove("active");});document.querySelectorAll(".tab").forEach(function(t){t.classList.remove("active");});document.getElementById("t-"+id).classList.add("active");document.getElementById("tab-"+id).classList.add("active");if(id==="files")loadJson();}'
'function loadJson(){fetch("/api/learning").then(function(r){return r.text();}).then(function(t){rawJson=t;document.getElementById("json-preview").textContent=t.length>600?t.slice(0,600)+"...":t;}).catch(function(){document.getElementById("json-preview").textContent="Not available.";});}'
'function copyJson(){navigator.clipboard.writeText(rawJson).then(function(){var b=document.querySelector(".copy-btn");b.textContent="Copied!";setTimeout(function(){b.textContent="Copy to clipboard";},2000);});}'
'function doReset(){if(!confirm("Sell everything on Binance first! Delete all data?"))return;fetch("/api/reset").then(function(r){return r.json();}).then(function(d){document.getElementById("reset-msg").textContent=d.ok?"Reset done. Restart the bot now.":"Failed.";});}'
'function fmtNum(n,d){return n===null||n===undefined||n===""?"-":parseFloat(n).toFixed(d!==undefined?d:2);}'
'function renderTrades(trades){var el=document.getElementById("trade-list");if(!trades||!trades.length){el.innerHTML="<div class=\\"empty\\">No trades yet.</div>";return;}var rows="";for(var i=0;i<Math.min(trades.length,30);i++){var t=trades[i];var ib=t.side.trim().toUpperCase()==="BUY";var pv=t.pnl!==""?parseFloat(t.pnl):null;var ps="-";if(pv!==null){var pc=pv>=0?"var(--grn)":"var(--red)";ps="<span style=\\"color:"+pc+"\\">"+(pv>=0?"+":"")+fmtNum(pv)+"%</span>";}rows+="<tr><td style=\\"color:var(--mut)\\">"+(t.time||"").slice(11,16)+"</td><td><span class=\\"badge "+(ib?"buy":"sell")+"\\">"+t.side.trim()+"</span></td><td>"+(t.symbol||"").trim().replace("USDT","")+"</td><td>$"+parseFloat(t.price||0).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:4})+"</td><td>"+ps+"</td><td style=\\"color:var(--mut);font-size:10px\\">"+(t.reason||"").slice(0,18)+"</td></tr>";}el.innerHTML="<table><thead><tr><th>Time</th><th>Type</th><th>Coin</th><th>Price</th><th>PnL</th><th>Reason</th></tr></thead><tbody>"+rows+"</tbody></table>";}'
'function renderCoins(stats){var el=document.getElementById("coin-list");var keys=Object.keys(stats||{});if(!keys.length){el.innerHTML="<div class=\\"empty\\">No data</div>";return;}keys.sort(function(a,b){return(stats[b].trades>0?stats[b].wins/stats[b].trades:0)-(stats[a].trades>0?stats[a].wins/stats[a].trades:0);});var html="";keys.forEach(function(s){var st=stats[s];var cwr=st.trades>0?Math.round(st.wins/st.trades*100):0;var p2=st.total_pnl||0;var c=cwr>=60?"var(--grn)":cwr>=45?"var(--amb)":"var(--red)";var pc=p2>=0?"var(--grn)":"var(--red)";html+="<div class=\\"crow\\"><div style=\\"font-weight:600\\">"+(CN[s]||s).slice(0,9)+"<span class=\\"ctag\\">"+s.replace("USDT","")+"</span></div><div style=\\"color:var(--mut)\\">"+st.trades+"t</div><div style=\\"color:"+c+"\\">"+cwr+"%</div><div style=\\"color:"+pc+"\\">"+( p2>=0?"+":"")+fmtNum(p2)+"%</div><div style=\\"background:rgba(255,255,255,.07);border-radius:3px;height:4px;width:60px\\"><div style=\\"width:"+Math.min(cwr,100)+"%;height:100%;border-radius:3px;background:"+c+"\\"></div></div></div>";});el.innerHTML=html;}'
'function renderParams(p){var el=document.getElementById("params-grid");var keys=Object.keys(PL).filter(function(k){return p[k]!==undefined;});if(!keys.length){el.innerHTML="<div class=\\"empty\\" style=\\"grid-column:1/-1\\">No data</div>";return;}el.innerHTML=keys.map(function(k){return"<div class=\\"prow\\"><span class=\\"pk\\">"+PL[k]+"</span><span class=\\"pv\\">"+p[k]+"</span></div>";}).join("");}'
'function load(){fetch("/api/data").then(function(r){return r.json();}).then(function(d){var alive=d.status&&d.status.running;document.getElementById("sdot").className="dot "+(alive?"on":"off");document.getElementById("stxt").textContent=alive?"Bot active":"Bot stopped";document.getElementById("m1").textContent=d.total||"0";document.getElementById("m1s").textContent=(d.wins||0)+"W / "+((d.total||0)-(d.wins||0))+"L";var wr=d.win_rate||0;var we=document.getElementById("m2");we.textContent=wr+"%";we.style.color=wr>=60?"var(--grn)":wr>=50?"var(--amb)":"var(--red)";document.getElementById("wrb").style.width=Math.min(wr,100)+"%";document.getElementById("wrb").style.background=wr>=60?"var(--grn)":wr>=50?"var(--amb)":"var(--red)";var pv=d.portfolio_value||0;document.getElementById("m3").textContent="$"+pv.toFixed(2);document.getElementById("m3").style.color=pv>=(d.start_balance||0)?"var(--grn)":"var(--red)";document.getElementById("m3s").textContent="inicio: $"+(d.start_balance||0).toFixed(2);var le=d.last_exp;if(le){document.getElementById("m4").textContent="Trade #"+le.at_trade;document.getElementById("m4s").textContent=(le.adjustments&&le.adjustments.length)?le.adjustments.join(" / "):"No changes";}document.getElementById("logline").textContent=(d.status&&d.status.last_line)||"-";document.getElementById("alerts-box").innerHTML=d.circuit_breaker?"<div class=\\"alert-crit\\">Circuit breaker activo — portfolio bajo 15%.</div>":"";renderTrades(d.trades);renderCoins(d.sym_stats);renderParams(d.params||{});});}'
'var s=30;function tick(){s--;document.getElementById("cd").textContent=s;if(s<=0){s=30;load();}}load();setInterval(tick,1000);'
'</script></body></html>'
)


# =============================================================
#   DASHBOARD DATA
# =============================================================

def read_learning():
    if not os.path.exists(LEARNING_FILE):
        return {}
    try:
        with open(LEARNING_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def read_trades():
    if not os.path.exists(TRADE_LOG_FILE):
        return []
    trades = []
    try:
        with open(TRADE_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                parts = [p.strip() for p in line.strip().split("|")]
                if len(parts) < 5:
                    continue
                t = {"time": parts[0], "side": parts[1], "symbol": parts[2],
                     "price": "", "qty": "", "pnl": "",
                     "reason": parts[5] if len(parts) > 5 else ""}
                for p in parts:
                    if p.startswith("precio="):
                        t["price"] = p.replace("precio=", "").replace("$", "")
                    elif p.startswith("qty="):
                        t["qty"] = p.replace("qty=", "")
                    elif p.startswith("pnl="):
                        t["pnl"] = p.replace("pnl=", "").replace("%", "")
                trades.append(t)
    except Exception:
        pass
    return list(reversed(trades))

def read_status():
    if not os.path.exists(BOT_LOG_FILE):
        return {"running": False, "last_line": "Bot not started.", "last_time": "-"}
    try:
        with open(BOT_LOG_FILE, encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        if not lines:
            return {"running": False, "last_line": "No activity.", "last_time": "-"}
        last = lines[-1]
        m    = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", last)
        lt   = m.group(1) if m else "-"
        alive = False
        if m:
            try:
                alive = (datetime.now() - datetime.strptime(lt, "%Y-%m-%d %H:%M:%S")).total_seconds() < 300
            except Exception:
                pass
        return {"running": alive, "last_line": last[-120:], "last_time": lt}
    except Exception:
        return {"running": False, "last_line": "Error.", "last_time": "-"}

_circuit_breaker_active = False
_start_balance          = 0.0
_portfolio_value        = 0.0

def build_api_data():
    learning = read_learning()
    trades   = read_trades()
    status   = read_status()
    total    = learning.get("total_trades", 0)
    wins     = learning.get("total_wins", 0)
    wr       = round(wins / total * 100, 1) if total > 0 else 0
    exps     = learning.get("param_experiments", [])
    return {
        "status":          status,
        "total":           total,
        "wins":            wins,
        "win_rate":        wr,
        "portfolio_value": round(_portfolio_value, 2),
        "start_balance":   round(_start_balance, 2),
        "sym_stats":       learning.get("symbol_stats", {}),
        "params":          learning.get("params", {}),
        "last_exp":        exps[-1] if exps else None,
        "trades":          trades[:50],
        "circuit_breaker": _circuit_breaker_active,
    }


# =============================================================
#   HTTP HANDLER
# =============================================================

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, ctype, body, dl=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if dl:
            self.send_header("Content-Disposition", "attachment; filename=" + dl)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", DASHBOARD_HTML)
        elif p == "/api/data":
            self._send(200, "application/json", json.dumps(build_api_data()))
        elif p == "/api/learning":
            if os.path.exists(LEARNING_FILE):
                with open(LEARNING_FILE) as f:
                    self._send(200, "application/json", f.read(), "bot_learning.json")
            else:
                self._send(404, "text/plain", "Not available yet.")
        elif p == "/api/trades":
            if os.path.exists(TRADE_LOG_FILE):
                with open(TRADE_LOG_FILE, encoding="utf-8") as f:
                    self._send(200, "text/plain", f.read(), "trade_history.log")
            else:
                self._send(404, "text/plain", "No trades yet.")
        elif p == "/api/logs":
            if os.path.exists(BOT_LOG_FILE):
                with open(BOT_LOG_FILE, encoding="utf-8") as f:
                    self._send(200, "text/plain", f.read()[-50000:], "bot.log")
            else:
                self._send(404, "text/plain", "Not found.")
        elif p == "/api/reset":
            for f in [LEARNING_FILE, POSITIONS_FILE, TRADE_LOG_FILE]:
                if os.path.exists(f):
                    os.remove(f)
                    log.info("Reset: %s eliminado.", f)
            self._send(200, "application/json", json.dumps({"ok": True}))
        else:
            self._send(404, "text/plain", "Not found.")


def start_dashboard():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        log.info("Dashboard on port %d", PORT)
        httpd.serve_forever()


# =============================================================
#   BOT MAIN LOOP
# =============================================================

def run():
    global _circuit_breaker_active, _start_balance, _portfolio_value

    log.info("=" * 55)
    log.info("  ADAPTIVE BOT v3.1")
    log.info("  Coins : %s", ", ".join(SYMBOLS))
    log.info("  TP %.0f%% | SL %.0f%% | Hold %dh | MinSell %.1f%%",
             BASE_TAKE_PROFIT * 100, BASE_STOP_LOSS * 100,
             MAX_HOLD_HOURS, MIN_SELL_SIGNAL_PNL)
    log.info("  Blocked hours: %s", sorted(BLOCKED_HOURS))
    log.info("  Mode: %s", "TESTNET" if USE_TESTNET else "*** LIVE ***")
    log.info("=" * 55)

    client    = create_client()
    learning  = LearningSystem()
    learning.print_summary()

    positions = load_positions()
    positions = reconcile_positions(client, positions)
    cooldowns = {}

    _start_balance = get_balance(client, "USDT")
    for symbol, pos in positions.items():
        try:
            raw = client.get_symbol_ticker(symbol=symbol)
            _start_balance += float(raw["price"]) * float(pos["qty"])
        except Exception:
            _start_balance += float(pos["entry"]) * float(pos["qty"])

    log.info("Starting portfolio: $%.2f (CB a $%.2f)",
             _start_balance, _start_balance * (1 - CIRCUIT_BREAKER_PCT))
    cycle = 0

    while True:
        try:
            cycle  += 1
            now     = datetime.now()
            params  = learning.get_params()
            balance = get_balance(client, "USDT")

            analyses = {}
            for symbol in learning.get_best_symbols():
                try:
                    candles = fetch_candles(client, symbol, INTERVAL)
                    result  = analyze_symbol(candles, params)
                    if result is None:
                        log.warning("  %s: candles congelados.", symbol)
                        continue
                    result["hour_q"] = learning.get_hour_quality(now.hour)
                    analyses[symbol] = result
                except Exception as e:
                    log.warning("  Error on %s: %s", symbol, e)

            _portfolio_value        = get_portfolio_value(client, positions, analyses)
            drop                    = (_start_balance - _portfolio_value) / _start_balance if _start_balance > 0 else 0
            _circuit_breaker_active = drop >= CIRCUIT_BREAKER_PCT

            log.info("\n-- Cycle #%d | %s | H%d%s | USDT: $%.2f | Portfolio: $%.2f | Pos: %d/%d%s --",
                     cycle, now.strftime("%H:%M:%S"), now.hour,
                     "(BLK)" if now.hour in BLOCKED_HOURS else "",
                     balance, _portfolio_value,
                     len(positions), MAX_OPEN_TRADES,
                     " | CB!" if _circuit_breaker_active else "")

            if _circuit_breaker_active:
                log.warning("  CB: portfolio bajo %.1f%% desde $%.2f.", drop * 100, _start_balance)

            for symbol, result in analyses.items():
                log.info("  %-12s $%10.4f | RSI=%5.1f | Score=%d/6 | Vol x%.1f",
                         symbol, result["price"], result["rsi"],
                         result["score"], result["vol_ratio"])

            # Gestionar posiciones abiertas
            for symbol, pos in list(positions.items()):
                if symbol not in analyses:
                    continue
                a     = analyses[symbol]
                price = a["price"]
                entry = float(pos["entry"])
                qty   = float(pos["qty"])
                pnl   = (price - entry) / entry * 100

                # Trailing stop
                if price > float(pos.get("peak_price", entry)):
                    pos["peak_price"] = price
                peak = float(pos.get("peak_price", entry))
                if (peak - entry) / entry >= TRAIL_TRIGGER_PCT:
                    trail_sl = peak * (1 - TRAIL_DISTANCE_PCT)
                    if trail_sl > float(pos["stop_loss"]):
                        pos["stop_loss"] = trail_sl
                        log.info("  Trailing SL %s -> $%.4f", symbol, trail_sl)
                        save_positions(positions)

                held_h = (now - datetime.fromisoformat(pos["open_time"])).total_seconds() / 3600
                why = None
                if price <= float(pos["stop_loss"]):
                    why = "STOP LOSS"
                elif price >= float(pos["take_profit"]):
                    why = "TAKE PROFIT"
                elif a["sell_signal"] and pnl >= MIN_SELL_SIGNAL_PNL:
                    # CRITICO: solo cierra por señal si hay ganancia real suficiente
                    why = "SELL SIGNAL %.1f%%" % pnl
                elif a["sell_signal"] and pnl < MIN_SELL_SIGNAL_PNL:
                    log.info("  %s: sell_signal pero PnL %.2f%% < %.1f%% minimo — manteniendo.",
                             symbol, pnl, MIN_SELL_SIGNAL_PNL)
                elif held_h >= MAX_HOLD_HOURS:
                    why = "MAX HOLD %.1fh" % held_h

                if why:
                    try:
                        place_order(client, symbol, "SELL", qty)
                        won = price > entry
                        log.info("  %s CLOSE %s @ $%.4f | PnL: %+.2f%% | %s",
                                 "OK" if won else "X", symbol, price, pnl, why)
                        log_trade(symbol, "SELL", price, qty, pnl, why)
                        learning.record_trade(symbol, pnl, params, won)
                        cooldowns[symbol] = now + timedelta(minutes=COIN_COOLDOWN_MIN)
                        del positions[symbol]
                        save_positions(positions)
                    except Exception as e:
                        log.error("  Error closing %s: %s", symbol, e)
                        if "-2010" in str(e):
                            try:
                                asset    = symbol.replace("USDT", "")
                                real_qty = get_balance(client, asset)
                                lot      = get_lot_rules(client, symbol)
                                real_qty = round_step(real_qty, lot["step_size"])
                                if real_qty * price > 1.0:
                                    log.warning("  %s: recovery qty real: %.6f", symbol, real_qty)
                                    place_order(client, symbol, "SELL", real_qty)
                                    won = price > entry
                                    log.info("  %s CLOSE(recovery) %s @ $%.4f | PnL: %+.2f%%",
                                             "OK" if won else "X", symbol, price, pnl)
                                    log_trade(symbol, "SELL", price, real_qty, pnl, "recovery")
                                    learning.record_trade(symbol, pnl, params, won)
                                    cooldowns[symbol] = now + timedelta(minutes=COIN_COOLDOWN_MIN)
                                else:
                                    log.warning("  %s: nada que vender, eliminando.", symbol)
                            except Exception as e2:
                                log.error("  %s: recovery fallida: %s", symbol, e2)
                            finally:
                                if symbol in positions:
                                    del positions[symbol]
                                save_positions(positions)

            # Abrir nuevas posiciones
            hora_bloqueada = now.hour in BLOCKED_HOURS
            if (len(positions) < MAX_OPEN_TRADES
                    and balance > 20
                    and not _circuit_breaker_active
                    and not hora_bloqueada):

                candidates = sorted(
                    [(s, a) for s, a in analyses.items()
                     if a["buy_signal"]
                     and s not in positions
                     and now >= cooldowns.get(s, datetime.min)],
                    key=lambda x: x[1]["score"], reverse=True
                )
                for symbol, a in candidates:
                    if len(positions) >= MAX_OPEN_TRADES:
                        break
                    pr  = a["price"]
                    sl  = round(pr * (1 - a["sl_pct"]), 6)
                    tp  = round(pr * (1 + a["tp_pct"]), 6)
                    lot = get_lot_rules(client, symbol)
                    trade_val = balance * MAX_TRADE_PCT / (MAX_OPEN_TRADES - len(positions))
                    qty = round_step(trade_val / pr, lot["step_size"])

                    if qty < lot["min_qty"]:
                        log.warning("  %s: qty bajo minimo.", symbol)
                        continue
                    if qty * pr < 6.0:
                        log.warning("  %s: notional $%.2f bajo minimo.", symbol, qty * pr)
                        continue

                    asset        = symbol.replace("USDT", "")
                    already_held = get_balance(client, asset)
                    if already_held * pr > 5.0:
                        log.warning("  %s: ya tenemos $%.2f, skip.", symbol, already_held * pr)
                        continue

                    try:
                        place_order(client, symbol, "BUY", qty)
                        positions[symbol] = {
                            "entry":       pr,
                            "qty":         qty,
                            "stop_loss":   sl,
                            "take_profit": tp,
                            "open_time":   now.isoformat(),
                            "peak_price":  pr,
                        }
                        save_positions(positions)
                        log.info("  OPEN %s @ $%.4f | Qty:%s | SL:$%.4f | TP:$%.4f | Score:%d/6",
                                 symbol, pr, qty, sl, tp, a["score"])
                        log_trade(symbol, "BUY", pr, qty)
                    except Exception as e:
                        log.error("  Error opening %s: %s", symbol, e)

            elif hora_bloqueada:
                log.info("  H%d bloqueada — sin nuevas compras.", now.hour)

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            log.info("\nBot detenido.")
            learning.print_summary()
            break
        except Exception as e:
            log.error("Error inesperado: %s — reintentando en 30s...", e)
            time.sleep(30)


if __name__ == "__main__":
    threading.Thread(target=start_dashboard, daemon=True).start()
    run()
