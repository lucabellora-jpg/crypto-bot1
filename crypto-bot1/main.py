"""
=============================================================
  BOT DE TRADING ADAPTATIVO — Altcoins Volátiles
  Exchange : Binance
  Python   : 3.8+
  Estrategia: RSI + EMA + Bollinger Bands + MACD + ATR
  Aprendizaje: Ajuste automático de parámetros por resultados
=============================================================

INSTALACIÓN (pegar en terminal de PyCharm):
    pip install python-binance pandas numpy

PASOS:
  1. Instalar dependencias (arriba)
  2. Ir a https://testnet.binance.vision → crear API keys
  3. Pegar las keys abajo en CONFIGURACIÓN
  4. Correr el archivo → Right click → Run
=============================================================
"""

import time, logging, sys, json, os, math
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
#   CONFIGURACIÓN  ← Completá tus datos aquí
# =============================================================

API_KEY    = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")

# True = paper trading (dinero falso, para testear)
# False = trading real (¡solo cuando ya probaste!)
USE_TESTNET = True

# ── Monedas a operar (altcoins volátiles) ──────────────────
# El bot analiza TODAS y elige la mejor oportunidad en cada ciclo
SYMBOLS = [
    "SOLUSDT",    # Solana       — alta volatilidad
    "DOGEUSDT",   # Dogecoin     — muy volátil, movimientos bruscos
    "AVAXUSDT",   # Avalanche    — alta liquidez y volatilidad
    "POLUSDT",      # Polygon (ex-MATIC) — altcoin popular
    "LINKUSDT",   # Chainlink    — buena volatilidad
    "DOTUSDT",    # Polkadot     — movimientos fuertes
    "ADAUSDT",    # Cardano      — volumen alto
    "LTCUSDT",    # Litecoin     — responde rápido a BTC
]

# Timeframe de velas
INTERVAL = "5m"    # era "15m" → velas más cortas = más movimiento = más señales

# ── Riesgo por operación ────────────────────────────────────
MAX_TRADE_PCT   = 0.08   # Usar 8% del balance por trade
BASE_STOP_LOSS  = 0.03   # Stop loss base: -3% (ajustado por ATR)
BASE_TAKE_PROFIT= 0.06   # Take profit base: +6% (ajustado por ATR)
MAX_OPEN_TRADES = 3      # Máximo de posiciones abiertas al mismo tiempo

# ── Parámetros iniciales de estrategia (el bot los va ajustando) ──
INITIAL_PARAMS = {
    "rsi_period"     : 14,
    "rsi_oversold"   : 32,
    "rsi_overbought" : 68,
    "fast_ema"       : 9,
    "slow_ema"       : 21,
    "bb_period"      : 20,    # Bollinger Bands período
    "bb_std"         : 2.0,   # Bollinger Bands desviación estándar
    "macd_fast"      : 12,
    "macd_slow"      : 26,
    "macd_signal"    : 9,
    "atr_period"     : 14,
    "min_score"      : 2,     # era 3 → bajamos a 2 para testnet
    "volume_factor"  : 0.5,   # era 1.2 → el testnet tiene poco volumen
}

POLL_SECONDS      = 60    # Chequear mercado cada 60 segundos
LEARNING_FILE     = "bot_learning.json"   # Donde guarda el aprendizaje
TRADE_LOG_FILE    = "trade_history.log"   # Historial de operaciones


# =============================================================
#   LOGGING
# =============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("AdaptiveBot")


# =============================================================
#   SISTEMA DE APRENDIZAJE ADAPTATIVO
# =============================================================

class LearningSystem:
    """
    Guarda el historial de trades y ajusta los parámetros
    automáticamente según qué configuraciones dieron mejor resultado.

    Aprende:
    - Cuáles monedas tuvieron mejor win rate
    - Qué valores de RSI/EMA funcionaron mejor
    - A qué hora del día hay mejores oportunidades
    - Si ajustar stop loss / take profit según volatilidad
    """

    def __init__(self, filepath=LEARNING_FILE):
        self.filepath = filepath
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    loaded = json.load(f)
                    log.info(f"Aprendizaje previo cargado: {loaded.get('total_trades', 0)} trades en historial.")
                    return loaded
            except Exception:
                pass
        # Estructura inicial
        return {
            "total_trades"     : 0,
            "total_wins"       : 0,
            "params"           : dict(INITIAL_PARAMS),
            "symbol_stats"     : {},    # Stats por moneda
            "hour_stats"       : {},    # Stats por hora del día
            "param_experiments": [],    # Historial de ajustes de parámetros
            "last_adjusted"    : None,
        }

    def _save(self):
        with open(self.filepath, "w") as f:
            json.dump(self.data, f, indent=2)

    def get_params(self):
        return dict(self.data["params"])

    def record_trade(self, symbol: str, pnl_pct: float, params_used: dict, won: bool):
        """Registrar el resultado de un trade y aprender de él."""
        hour = str(datetime.now().hour)

        # Stats globales
        self.data["total_trades"] += 1
        if won:
            self.data["total_wins"] += 1

        # Stats por moneda
        if symbol not in self.data["symbol_stats"]:
            self.data["symbol_stats"][symbol] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        self.data["symbol_stats"][symbol]["trades"] += 1
        self.data["symbol_stats"][symbol]["total_pnl"] = round(
            self.data["symbol_stats"][symbol]["total_pnl"] + pnl_pct, 4
        )
        if won:
            self.data["symbol_stats"][symbol]["wins"] += 1

        # Stats por hora
        if hour not in self.data["hour_stats"]:
            self.data["hour_stats"][hour] = {"trades": 0, "wins": 0}
        self.data["hour_stats"][hour]["trades"] += 1
        if won:
            self.data["hour_stats"][hour]["wins"] += 1

        # Intentar ajustar parámetros cada 10 trades
        if self.data["total_trades"] % 10 == 0:
            self._adapt_params()

        self._save()

    def _adapt_params(self):
        """
        Ajuste automático de parámetros basado en resultados.
        Lógica gradual y conservadora para no sobre-optimizar.
        """
        total   = self.data["total_trades"]
        wins    = self.data["total_wins"]
        win_rate = wins / total if total > 0 else 0
        p = self.data["params"]

        log.info(f"[APRENDIZAJE] Win rate actual: {win_rate*100:.1f}% en {total} trades.")

        adjustments = []

        # Si win rate < 40% → ser más selectivo (subir min_score)
        if win_rate < 0.40 and p["min_score"] < 5:
            p["min_score"] = min(p["min_score"] + 1, 5)
            adjustments.append("min_score ↑ (más selectivo)")

        # Si win rate > 65% → podemos ser un poco más agresivos
        elif win_rate > 0.65 and p["min_score"] > 2:
            p["min_score"] = max(p["min_score"] - 1, 2)
            adjustments.append("min_score ↓ (más trades)")

        # Si win rate < 45% → ampliar zona de sobrecompra/sobreventa
        if win_rate < 0.45:
            p["rsi_oversold"]   = max(25, p["rsi_oversold"] - 2)
            p["rsi_overbought"] = min(75, p["rsi_overbought"] + 2)
            adjustments.append("RSI más estricto")

        # Si win rate > 60% → podemos relajar RSI un poco
        elif win_rate > 0.60:
            p["rsi_oversold"]   = min(35, p["rsi_oversold"] + 1)
            p["rsi_overbought"] = max(65, p["rsi_overbought"] - 1)
            adjustments.append("RSI más flexible")

        # Guardar registro del ajuste
        self.data["param_experiments"].append({
            "at_trade"  : total,
            "win_rate"  : round(win_rate, 3),
            "adjustments": adjustments,
            "new_params": dict(p),
        })

        if adjustments:
            log.info(f"[APRENDIZAJE] Parámetros ajustados: {', '.join(adjustments)}")
        else:
            log.info("[APRENDIZAJE] Parámetros sin cambios por ahora.")

    def get_best_symbols(self) -> list:
        """Devuelve lista de símbolos ordenada por win rate histórico."""
        stats = self.data["symbol_stats"]
        ranked = []
        for sym in SYMBOLS:
            if sym in stats and stats[sym]["trades"] >= 3:
                wr = stats[sym]["wins"] / stats[sym]["trades"]
                ranked.append((sym, wr))
            else:
                ranked.append((sym, 0.5))   # Sin historial → neutral
        ranked.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in ranked]

    def get_hour_quality(self, hour: int) -> float:
        """Retorna qué tan buena es la hora actual para operar (0.0–1.0)."""
        h = str(hour)
        stats = self.data["hour_stats"]
        if h in stats and stats[h]["trades"] >= 5:
            return stats[h]["wins"] / stats[h]["trades"]
        return 0.5     # Sin datos → neutral

    def print_summary(self):
        total = self.data["total_trades"]
        wins  = self.data["total_wins"]
        log.info("=" * 55)
        log.info(f"  RESUMEN DE APRENDIZAJE")
        log.info(f"  Total trades  : {total}")
        log.info(f"  Win rate      : {wins/total*100:.1f}%" if total > 0 else "  Sin trades aún")
        log.info(f"  Parámetros actuales: {self.data['params']}")
        if self.data["symbol_stats"]:
            log.info("  Stats por moneda:")
            for sym, s in self.data["symbol_stats"].items():
                wr = s["wins"]/s["trades"]*100 if s["trades"] > 0 else 0
                log.info(f"    {sym:<12} trades={s['trades']}  win={wr:.0f}%  pnl={s['total_pnl']:+.2f}%")
        log.info("=" * 55)


# =============================================================
#   INDICADORES TÉCNICOS
# =============================================================

def ema(prices: list, period: int) -> list:
    """EMA completo para toda la serie."""
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
    """Retorna (upper, middle, lower)."""
    if len(prices) < period:
        mid = prices[-1]
        return mid, mid, mid
    window = prices[-period:]
    mid    = sum(window) / period
    std    = math.sqrt(sum((p - mid)**2 for p in window) / period)
    return mid + std * std_mult, mid, mid - std * std_mult


def calculate_macd(prices: list, fast: int, slow: int, signal_p: int):
    """Retorna (macd_line, signal_line, histogram)."""
    if len(prices) < slow + signal_p:
        return 0, 0, 0
    fast_ema   = ema(prices, fast)
    slow_ema   = ema(prices, slow)
    macd_line  = [f - s for f, s in zip(fast_ema, slow_ema)]
    signal_line = ema(macd_line, signal_p)
    hist        = macd_line[-1] - signal_line[-1]
    return macd_line[-1], signal_line[-1], hist


def calculate_atr(highs: list, lows: list, closes: list, period: int) -> float:
    """Average True Range — mide la volatilidad real."""
    if len(closes) < 2:
        return closes[-1] * 0.02
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i]  - closes[i-1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs))


def calculate_volume_ratio(volumes: list, period: int = 20) -> float:
    """Volumen actual vs promedio reciente."""
    if len(volumes) < 2:
        return 1.0
    avg = sum(volumes[-period-1:-1]) / min(period, len(volumes)-1)
    return volumes[-1] / avg if avg > 0 else 1.0


# =============================================================
#   ANÁLISIS DE SEÑAL (Multi-indicador con puntaje)
# =============================================================

def analyze_symbol(candles: dict, params: dict) -> dict:
    """
    Analiza una moneda con todos los indicadores.
    Devuelve un dict con señal, puntaje (0–6), y detalles.

    Sistema de puntaje:
      +1 RSI oversold
      +1 EMA bullish crossover
      +1 Precio cerca de banda inferior de Bollinger
      +1 MACD cruzando al alza
      +1 Volumen alto (confirmación)
      +1 Hora históricamente buena

    Señal BUY solo si score >= params["min_score"]
    """
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

    atr    = calculate_atr(highs, lows, closes, params["atr_period"])
    vol_r  = calculate_volume_ratio(volumes)
    hour_q = 0.5    # Se actualiza desde learning

    # ── Puntaje BUY ─────────────────────────────────────────
    score  = 0
    detail = []

    if rsi < params["rsi_oversold"]:
        score += 1
        detail.append(f"RSI={rsi:.1f} oversold ✓")

    if fast_prev <= slow_prev and fast_now > slow_prev:
        score += 1
        detail.append("EMA bullish cross ✓")

    bb_range = bb_upper - bb_lower
    if bb_range > 0 and (price - bb_lower) / bb_range < 0.25:
        score += 1
        detail.append("Cerca de BB inferior ✓")

    prev_hist = 0
    if len(closes) > 30:
        _, _, prev_hist = calculate_macd(closes[:-1], params["macd_fast"], params["macd_slow"], params["macd_signal"])
    if macd_hist > prev_hist and macd_hist < 0:
        score += 1
        detail.append("MACD convergiendo ✓")

    if vol_r >= params["volume_factor"]:
        score += 1
        detail.append(f"Volumen x{vol_r:.1f} ✓")

    if hour_q >= 0.55:
        score += 1
        detail.append("Hora favorable ✓")

    # ── Señal SELL ───────────────────────────────────────────
    sell_signal = (
        rsi > params["rsi_overbought"] or
        (fast_prev >= slow_prev and fast_now < slow_prev) or
        (price > bb_upper and macd_hist < 0)
    )

    # ── Stop Loss y Take Profit ajustados por ATR ────────────
    atr_pct     = atr / price
    sl_dynamic  = max(BASE_STOP_LOSS, atr_pct * 1.5)
    tp_dynamic  = max(BASE_TAKE_PROFIT, atr_pct * 3.0)

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
        log.error("¡Faltan las API keys! Completá API_KEY y API_SECRET arriba.")
        sys.exit(1)
    client = Client(API_KEY, API_SECRET)
    if USE_TESTNET:
        client.API_URL = "https://testnet.binance.vision/api"
        log.info("Conectado al TESTNET de Binance (paper trading)")
    else:
        log.warning("*** Conectado a Binance REAL — dinero real en juego ***")
    return client


def fetch_candles(client: Client, symbol: str, interval: str, limit: int = 120) -> dict:
    """Descarga velas y devuelve OHLCV separado por listas."""
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
    """Devuelve minQty y stepSize exactos para el símbolo."""
    try:
        info = client.get_symbol_info(symbol)
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                return {
                    "min_qty"  : float(f["minQty"]),
                    "step_size": float(f["stepSize"]),
                }
    except Exception:
        pass
    return {"min_qty": 0.001, "step_size": 0.001}


def round_step(qty: float, step_size: float) -> float:
    """Redondea qty al step_size exacto que exige Binance."""
    if step_size == 0:
        return qty
    precision = round(-math.log10(step_size))
    factor    = 10 ** precision
    return math.floor(qty * factor) / factor

def place_order(client: Client, symbol: str, side: str, qty: float) -> dict:
    binance_side = SIDE_BUY if side == "BUY" else SIDE_SELL
    return client.create_order(
        symbol=symbol,
        side=binance_side,
        type=ORDER_TYPE_MARKET,
        quantity=qty,
    )


# =============================================================
#   LOGGER DE TRADES
# =============================================================

def log_trade(symbol, side, price, qty, pnl_pct=None, reason=""):
    line = (
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"{side:<4} | {symbol:<12} | "
        f"precio=${price:,.4f} | qty={qty} | "
        f"pnl={pnl_pct:+.2f}% | {reason}\n"
        if pnl_pct is not None else
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"{side:<4} | {symbol:<12} | "
        f"precio=${price:,.4f} | qty={qty}\n"
    )
    with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)


# =============================================================
#   LOOP PRINCIPAL
# =============================================================

def run():
    log.info("=" * 55)
    log.info("  BOT ADAPTATIVO — ALTCOINS VOLÁTILES")
    log.info(f"  Monedas  : {', '.join(SYMBOLS)}")
    log.info(f"  Intervalo: {INTERVAL}  |  Modo: {'TESTNET' if USE_TESTNET else '*** LIVE ***'}")
    log.info(f"  Max posiciones simultáneas: {MAX_OPEN_TRADES}")
    log.info("=" * 55)

    client   = create_client()
    learning = LearningSystem()
    learning.print_summary()

    # Posiciones abiertas: { symbol: { entry, qty, sl, tp, open_time } }
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

            # Ordenar símbolos por historial (los mejores primero)
            ranked_symbols = learning.get_best_symbols()

            analyses = {}
            for symbol in ranked_symbols:
                try:
                    candles = fetch_candles(client, symbol, INTERVAL)
                    result  = analyze_symbol(candles, params)
                    result["hour_q"] = learning.get_hour_quality(hour_now)
                    analyses[symbol] = result

                    in_pos = symbol in positions
                    log.info(
                        f"  {symbol:<12} ${result['price']:>10,.4f} | "
                        f"RSI={result['rsi']:>5.1f} | "
                        f"Score={result['score']}/6 | "
                        f"Vol x{result['vol_ratio']:.1f} | "
                        f"{'[EN POSICIÓN]' if in_pos else ''}"
                    )
                except Exception as e:
                    log.warning(f"  Error analizando {symbol}: {e}")

            # ── GESTIÓN DE POSICIONES ABIERTAS ────────────────
            for symbol, pos in list(positions.items()):
                if symbol not in analyses:
                    continue

                a     = analyses[symbol]
                price = a["price"]
                entry = pos["entry"]
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
                        log.info(
                            f"  {'✔' if won else '✘'} CERRAR {symbol} @ ${price:,.4f} | "
                            f"PnL: {pnl_pct:+.2f}% | Motivo: {exit_reason}"
                        )
                        log_trade(symbol, "SELL", price, pos["qty"], pnl_pct, exit_reason)
                        learning.record_trade(symbol, pnl_pct, params, won)
                        del positions[symbol]
                    except Exception as e:
                        log.error(f"  Error cerrando {symbol}: {e}")

            # ── ABRIR NUEVAS POSICIONES ────────────────────────
            if len(positions) < MAX_OPEN_TRADES and balance > 20:
                # Buscar la mejor oportunidad (mayor score entre los que dan BUY)
                candidates = [
                    (sym, a) for sym, a in analyses.items()
                    if a["buy_signal"] and sym not in positions
                ]
                # Ordenar por score descendente
                candidates.sort(key=lambda x: x[1]["score"], reverse=True)

                for symbol, a in candidates:
                    if len(positions) >= MAX_OPEN_TRADES:
                        break

                    price    = a["price"]
                    sl_price = round(price * (1 - a["sl_pct"]), 6)
                    tp_price = round(price * (1 + a["tp_pct"]), 6)
                    lot      = get_lot_rules(client, symbol)
                    trade_val = balance * MAX_TRADE_PCT / (MAX_OPEN_TRADES - len(positions))
                    qty      = round_step(trade_val / price, lot["step_size"])

                    if qty < lot["min_qty"]:
                        log.warning(f"  {symbol}: cantidad {qty} menor al mínimo ({lot['min_qty']}), saltando.")
                        continue

                    try:
                        place_order(client, symbol, "BUY", qty)
                        positions[symbol] = {
                            "entry"      : price,
                            "qty"        : qty,
                            "stop_loss"  : sl_price,
                            "take_profit": tp_price,
                            "open_time"  : now.isoformat(),
                        }
                        log.info(
                            f"  ✔ ABRIENDO {symbol} @ ${price:,.4f} | "
                            f"Qty: {qty} | SL: ${sl_price:,.4f} | TP: ${tp_price:,.4f} | "
                            f"Score: {a['score']}/6 | {' | '.join(a['detail'])}"
                        )
                        log_trade(symbol, "BUY", price, qty)
                    except Exception as e:
                        log.error(f"  Error abriendo {symbol}: {e}")

            # ── Esperar al próximo ciclo ───────────────────────
            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            log.info("\nBot detenido por el usuario (Ctrl+C).")
            learning.print_summary()
            break

        except Exception as e:
            log.error(f"Error inesperado: {e} — reintentando en 30s...")
            time.sleep(30)


# =============================================================
#   ENTRADA
# =============================================================

if __name__ == "__main__":
    run()