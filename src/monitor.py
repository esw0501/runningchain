"""
monitor.py — 실시간 시장 모니터링
10분마다 실행, 조건 충족 시에만 텔레그램 알림 발송 (Claude API 호출 없음)

알림 트리거:
  - BTC/ETH 1시간 변동 ±3% 이상
  - RSI ≥70 (과매수) / ≤30 (과매도)
  - 골든크로스 / 데드크로스 (MA7 vs MA25)
  - 볼린저밴드 상/하단 이탈
  - 공포탐욕지수 ≥85 (극탐욕) / ≤15 (극공포)
"""

from __future__ import annotations

import io
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# Windows 인코딩
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("monitor")

# ── 설정값 ────────────────────────────────────────────────────────────────────
PRICE_CHANGE_THRESHOLD = 3.0     # 1시간 변동률 ±%
RSI_OVERBOUGHT        = 70
RSI_OVERSOLD          = 30
FG_EXTREME_GREED      = 85
FG_EXTREME_FEAR       = 15
COOLDOWN_HOURS        = 1        # 가격/RSI/FG 알림 재발송 대기 시간
CROSS_COOLDOWN_HOURS  = 24       # 골든/데드크로스는 일봉 기준이므로 24h 쿨다운

STATE_FILE = Path(__file__).parent.parent / "monitor_state.json"


# ── 상태 관리 ─────────────────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"alerts": {}}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _on_cooldown(state: dict, key: str, hours: int = COOLDOWN_HOURS) -> bool:
    last_str = state.get("alerts", {}).get(key)
    if not last_str:
        return False
    try:
        return datetime.now() - datetime.fromisoformat(last_str) < timedelta(hours=hours)
    except Exception:
        return False


def _mark(state: dict, key: str) -> None:
    state.setdefault("alerts", {})[key] = datetime.now().isoformat()


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def _safe_get(url: str, params: dict | None = None, timeout: int = 12):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _get_price_1h(symbol: str) -> dict:
    """Binance에서 현재가 및 1시간 변동률 조회"""
    try:
        rows = _safe_get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1h", "limit": 2},
        )
        if not rows or len(rows) < 2:
            return {}
        prev_close = float(rows[0][4])
        curr_close = float(rows[1][4])
        change_1h  = ((curr_close - prev_close) / prev_close) * 100
        return {
            "price":     round(curr_close, 2),
            "change_1h": round(change_1h, 2),
        }
    except Exception as exc:
        logger.warning("가격 조회 실패(%s): %s", symbol, exc)
        return {}


def _get_technicals(symbol: str = "BTCUSDT") -> dict:
    """Binance 일봉 klines로 RSI / BB / MA 계산"""
    try:
        rows = _safe_get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": 120},
        )
        if not rows or len(rows) < 30:
            return {}

        closes = pd.Series([float(r[4]) for r in rows], dtype="float64")
        current_price = float(closes.iloc[-1])

        try:
            import talib as _talib  # type: ignore
            rsi_arr = _talib.RSI(closes.values, timeperiod=14)
            rsi = float(rsi_arr[-1]) if not pd.isna(rsi_arr[-1]) else None

            bb_u, _, bb_l = _talib.BBANDS(closes.values, timeperiod=20)
            bb_upper = float(bb_u[-1])
            bb_lower = float(bb_l[-1])

            sma7  = _talib.SMA(closes.values, timeperiod=7)
            sma25 = _talib.SMA(closes.values, timeperiod=25)
            ma7,      ma25      = float(sma7[-1]),  float(sma25[-1])
            prev_ma7, prev_ma25 = float(sma7[-2]),  float(sma25[-2])

        except Exception:
            import ta as _ta  # type: ignore
            rsi = float(_ta.momentum.RSIIndicator(close=closes, window=14).rsi().iloc[-1])

            bb  = _ta.volatility.BollingerBands(close=closes, window=20, window_dev=2)
            bb_upper = float(bb.bollinger_hband().iloc[-1])
            bb_lower = float(bb.bollinger_lband().iloc[-1])

            sma7_s  = closes.rolling(7).mean()
            sma25_s = closes.rolling(25).mean()
            ma7,      ma25      = float(sma7_s.iloc[-1]),  float(sma25_s.iloc[-1])
            prev_ma7, prev_ma25 = float(sma7_s.iloc[-2]),  float(sma25_s.iloc[-2])

        return {
            "current_price": round(current_price, 2),
            "rsi":           round(rsi, 2) if rsi is not None else None,
            "bb_upper":      round(bb_upper, 2),
            "bb_lower":      round(bb_lower, 2),
            "ma7":           round(ma7, 2),
            "ma25":          round(ma25, 2),
            "prev_ma7":      round(prev_ma7, 2),
            "prev_ma25":     round(prev_ma25, 2),
        }
    except Exception as exc:
        logger.warning("기술지표 계산 실패: %s", exc)
        return {}


def _get_upbit_prices() -> dict:
    """업비트에서 BTC/ETH 원화 시세 조회"""
    try:
        rows = _safe_get(
            "https://api.upbit.com/v1/ticker",
            params={"markets": "KRW-BTC,KRW-ETH"},
        )
        result = {}
        for row in rows:
            market = row.get("market", "")
            price  = row.get("trade_price")
            change = row.get("signed_change_rate")  # 전일 대비 변동률 (소수점)
            if market == "KRW-BTC":
                result["btc_krw"]        = int(price) if price else None
                result["btc_change_24h"] = round(float(change) * 100, 2) if change is not None else None
            elif market == "KRW-ETH":
                result["eth_krw"]        = int(price) if price else None
                result["eth_change_24h"] = round(float(change) * 100, 2) if change is not None else None
        return result
    except Exception as exc:
        logger.warning("업비트 가격 조회 실패: %s", exc)
        return {}


def _get_fear_greed() -> int | None:
    try:
        data = _safe_get("https://api.alternative.me/fng/", params={"limit": 1}).get("data", [])
        return int(data[0]["value"]) if data else None
    except Exception:
        return None


# ── 텔레그램 ──────────────────────────────────────────────────────────────────

def _send_alert(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("0000000000"):
        logger.info("[Telegram] 더미 토큰 — 발송 건너뜀")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text":    text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        r.raise_for_status()
        logger.info("[Telegram] 알림 발송 완료")
        return True
    except Exception as exc:
        logger.warning("[Telegram] 발송 실패: %s", exc)
        return False


# ── 메인 체크 로직 ────────────────────────────────────────────────────────────

def check_and_alert() -> None:
    state     = _load_state()
    now_str   = datetime.now().strftime("%Y-%m-%d %H:%M")
    triggered = []   # 발송할 메시지 조각들

    # 데이터 수집
    btc    = _get_price_1h("BTCUSDT")
    eth    = _get_price_1h("ETHUSDT")
    upbit  = _get_upbit_prices()
    tech   = _get_technicals("BTCUSDT")
    fg     = _get_fear_greed()

    btc_price  = btc.get("price")
    btc_change = btc.get("change_1h")
    eth_price  = eth.get("price")
    eth_change = eth.get("change_1h")

    # ── 1. BTC 가격 급변 ──────────────────────────────────────────
    if btc_change is not None and btc_price is not None:
        if btc_change >= PRICE_CHANGE_THRESHOLD:
            key = "btc_surge"
            if not _on_cooldown(state, key):
                triggered.append(f"📈 BTC 급등  +{btc_change:.1f}% (1h)\n   현재가: ${btc_price:,.0f}")
                _mark(state, key)
        elif btc_change <= -PRICE_CHANGE_THRESHOLD:
            key = "btc_drop"
            if not _on_cooldown(state, key):
                triggered.append(f"📉 BTC 급락  {btc_change:.1f}% (1h)\n   현재가: ${btc_price:,.0f}")
                _mark(state, key)

    # ── 2. ETH 가격 급변 ──────────────────────────────────────────
    if eth_change is not None and eth_price is not None:
        if eth_change >= PRICE_CHANGE_THRESHOLD:
            key = "eth_surge"
            if not _on_cooldown(state, key):
                triggered.append(f"📈 ETH 급등  +{eth_change:.1f}% (1h)\n   현재가: ${eth_price:,.0f}")
                _mark(state, key)
        elif eth_change <= -PRICE_CHANGE_THRESHOLD:
            key = "eth_drop"
            if not _on_cooldown(state, key):
                triggered.append(f"📉 ETH 급락  {eth_change:.1f}% (1h)\n   현재가: ${eth_price:,.0f}")
                _mark(state, key)

    # ── 3. RSI 신호 ───────────────────────────────────────────────
    rsi = tech.get("rsi")
    if rsi is not None:
        if rsi >= RSI_OVERBOUGHT:
            key = "rsi_overbought"
            if not _on_cooldown(state, key):
                triggered.append(f"🔴 RSI 과매수  {rsi:.1f} (≥{RSI_OVERBOUGHT})\n   단기 조정 가능성 확인")
                _mark(state, key)
        elif rsi <= RSI_OVERSOLD:
            key = "rsi_oversold"
            if not _on_cooldown(state, key):
                triggered.append(f"🟢 RSI 과매도  {rsi:.1f} (≤{RSI_OVERSOLD})\n   반등 구간 진입 탐색")
                _mark(state, key)

    # ── 4. 골든크로스 / 데드크로스 ───────────────────────────────
    ma7      = tech.get("ma7")
    ma25     = tech.get("ma25")
    prev_ma7 = tech.get("prev_ma7")
    prev_ma25= tech.get("prev_ma25")

    if all(v is not None for v in [ma7, ma25, prev_ma7, prev_ma25]):
        if prev_ma7 < prev_ma25 and ma7 > ma25:
            key = "golden_cross"
            if not _on_cooldown(state, key, hours=CROSS_COOLDOWN_HOURS):
                triggered.append(
                    f"✨ 골든크로스 발생!\n"
                    f"   MA7 ${ma7:,.0f} > MA25 ${ma25:,.0f}"
                )
                _mark(state, key)
        elif prev_ma7 > prev_ma25 and ma7 < ma25:
            key = "dead_cross"
            if not _on_cooldown(state, key, hours=CROSS_COOLDOWN_HOURS):
                triggered.append(
                    f"💀 데드크로스 발생!\n"
                    f"   MA7 ${ma7:,.0f} < MA25 ${ma25:,.0f}"
                )
                _mark(state, key)

    # ── 5. 볼린저밴드 이탈 ────────────────────────────────────────
    bb_upper      = tech.get("bb_upper")
    bb_lower      = tech.get("bb_lower")
    current_price = tech.get("current_price")

    if all(v is not None for v in [bb_upper, bb_lower, current_price]):
        if current_price > bb_upper:
            key = "bb_upper_break"
            if not _on_cooldown(state, key):
                triggered.append(
                    f"⚡ 볼린저밴드 상단 이탈\n"
                    f"   현재 ${current_price:,.0f} > 상단 ${bb_upper:,.0f}"
                )
                _mark(state, key)
        elif current_price < bb_lower:
            key = "bb_lower_break"
            if not _on_cooldown(state, key):
                triggered.append(
                    f"⚡ 볼린저밴드 하단 이탈\n"
                    f"   현재 ${current_price:,.0f} < 하단 ${bb_lower:,.0f}"
                )
                _mark(state, key)

    # ── 6. 공포탐욕 극단 ─────────────────────────────────────────
    if fg is not None:
        if fg >= FG_EXTREME_GREED:
            key = "fg_extreme_greed"
            if not _on_cooldown(state, key):
                triggered.append(f"🤑 공포탐욕 극탐욕  {fg} (≥{FG_EXTREME_GREED})\n   시장 과열 경계")
                _mark(state, key)
        elif fg <= FG_EXTREME_FEAR:
            key = "fg_extreme_fear"
            if not _on_cooldown(state, key):
                triggered.append(f"😱 공포탐욕 극공포  {fg} (≤{FG_EXTREME_FEAR})\n   역발상 매수 구간 탐색")
                _mark(state, key)

    # ── 발송 ─────────────────────────────────────────────────────
    if triggered:
        btc_krw = upbit.get("btc_krw")
        eth_krw = upbit.get("eth_krw")
        btc_c24 = upbit.get("btc_change_24h")
        eth_c24 = upbit.get("eth_change_24h")

        # 코인별 시세 블록
        btc_block = "BTC (비트코인)\n"
        if btc_krw:
            btc_block += f"  업비트 : ₩{btc_krw:,}"
            if btc_c24 is not None:
                btc_block += f" ({btc_c24:+.1f}%)"
            btc_block += "\n"
        if btc_price:
            btc_block += f"  바이낸스: ${btc_price:,.0f}"
            if btc_change is not None:
                btc_block += f" ({btc_change:+.1f}%)"

        eth_block = "ETH (이더리움)\n"
        if eth_krw:
            eth_block += f"  업비트 : ₩{eth_krw:,}"
            if eth_c24 is not None:
                eth_block += f" ({eth_c24:+.1f}%)"
            eth_block += "\n"
        if eth_price:
            eth_block += f"  바이낸스: ${eth_price:,.0f}"
            if eth_change is not None:
                eth_block += f" ({eth_change:+.1f}%)"

        msg = (
            f"🚨 [러닝체인 알림]  {now_str}\n"
            f"{'─' * 28}\n"
            + "\n\n".join(triggered)
            + f"\n{'─' * 28}\n"
            f"📊 현재 시세\n"
            f"{btc_block}\n\n"
            f"{eth_block}\n"
            f"⚠️ 투자 권유 아님"
        )
        _send_alert(msg)
    else:
        logger.info(
            "이상 없음 — BTC %s%% | ETH %s%% | RSI %s | FG %s",
            btc_change, eth_change, rsi, fg,
        )

    _save_state(state)


if __name__ == "__main__":
    check_and_alert()
