"""
collector.py — 시장 데이터 수집 모듈
요구사항 반영:
- CoinGecko / Fear&Greed / CryptoPanic 유지
- Binance: OI, 롱숏비율, 펀딩비, 24h taker 흐름
- 김치 프리미엄(업비트 + 환율 + 바이낸스)
- 고래 vs 개인 포지션(top/global long-short)
- Glassnode 거래소 순유입/유출(옵션)
- 기술지표: TA-Lib 우선, 미설치 시 ta fallback
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd
import requests

from config.settings import (
    COINGECKO_BASE_URL,
    COINGECKO_VS_CURRENCY,
    COIN_IDS,
    FEAR_GREED_URL,
    CRYPTOPANIC_BASE_URL,
    CRYPTOPANIC_API_KEY,
    CRYPTOPANIC_FILTER,
)

logger = logging.getLogger(__name__)

try:
    import talib as _talib  # type: ignore
except Exception:
    _talib = None

try:
    import ta as _ta  # type: ignore
except Exception:
    _ta = None


def _safe_get(url: str, params: dict | None = None, timeout: int = 12):
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_market_data() -> dict:
    try:
        data = _safe_get(f"{COINGECKO_BASE_URL}/global").get("data", {})
        return {
            "total_market_cap_usd": data.get("total_market_cap", {}).get("usd"),
            "total_volume_usd": data.get("total_volume", {}).get("usd"),
            "btc_dominance": data.get("market_cap_percentage", {}).get("btc"),
            "eth_dominance": data.get("market_cap_percentage", {}).get("eth"),
            "market_cap_change_24h": data.get("market_cap_change_percentage_24h_usd"),
            "active_cryptocurrencies": data.get("active_cryptocurrencies"),
        }
    except Exception as exc:
        logger.warning("[collector] CoinGecko global 실패: %s", exc)
        return {}


def fetch_coins_data() -> list[dict]:
    params = {
        "vs_currency": COINGECKO_VS_CURRENCY,
        "ids": ",".join(COIN_IDS),
        "order": "market_cap_desc",
        "per_page": len(COIN_IDS),
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "1h,24h,7d",
    }
    try:
        rows = _safe_get(f"{COINGECKO_BASE_URL}/coins/markets", params=params)
        items: list[dict] = []
        for row in rows:
            items.append(
                {
                    "id": row.get("id"),
                    "symbol": str(row.get("symbol", "")).upper(),
                    "name": row.get("name"),
                    "current_price": row.get("current_price"),
                    "market_cap": row.get("market_cap"),
                    "total_volume": row.get("total_volume"),
                    "price_change_1h": row.get("price_change_percentage_1h_in_currency"),
                    "price_change_24h": row.get("price_change_percentage_24h"),
                    "price_change_7d": row.get("price_change_percentage_7d_in_currency"),
                    "high_24h": row.get("high_24h"),
                    "low_24h": row.get("low_24h"),
                    "ath": row.get("ath"),
                    "ath_change_pct": row.get("ath_change_percentage"),
                }
            )
        return items
    except Exception as exc:
        logger.warning("[collector] CoinGecko markets 실패: %s", exc)
        return []


def fetch_fear_greed() -> dict:
    try:
        data = _safe_get(FEAR_GREED_URL, params={"limit": 7}).get("data", [])
        if not data:
            return {}

        latest = data[0]
        result = {
            "value": int(latest.get("value", 0)),
            "value_classification": latest.get("value_classification"),
            "timestamp": datetime.fromtimestamp(int(latest.get("timestamp", 0))).strftime("%Y-%m-%d"),
        }
        if len(data) >= 2:
            result["yesterday_value"] = int(data[1].get("value", 0))
            result["yesterday_class"] = data[1].get("value_classification")
        if len(data) >= 7:
            result["week_ago_value"] = int(data[6].get("value", 0))
            result["week_ago_class"] = data[6].get("value_classification")
        return result
    except Exception as exc:
        logger.warning("[collector] Fear&Greed 실패: %s", exc)
        return {}


RSS_FEEDS = [
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "CoinDesk",      "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "Decrypt",       "url": "https://decrypt.co/feed"},
]

def _fetch_rss_news(limit: int = 20) -> list[dict]:
    try:
        import feedparser
    except ImportError:
        logger.warning("[collector] feedparser 미설치 — RSS 뉴스 건너뜀")
        return []

    results: list[dict] = []
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries[:10]:
                published = ""
                if hasattr(entry, "published"):
                    published = str(entry.published)[:10]
                results.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "source": feed_info["name"],
                    "published": published,
                    "kind": "news",
                    "votes_pos": 0,
                    "votes_neg": 0,
                    "votes_imp": 0,
                    "currencies": [],
                })
        except Exception as exc:
            logger.warning("[collector] RSS(%s) 실패: %s", feed_info["name"], exc)

    logger.info("[collector] RSS 뉴스 수집 완료: %d건", len(results[:limit]))
    return results[:limit]


def fetch_news(limit: int = 20) -> list[dict]:
    # CryptoPanic 키가 있으면 CryptoPanic 우선 사용
    key = str(CRYPTOPANIC_API_KEY).strip()
    if key and not key.startswith("dummy"):
        try:
            data = _safe_get(
                CRYPTOPANIC_BASE_URL,
                params={
                    "auth_token": key,
                    "filter": CRYPTOPANIC_FILTER,
                    "public": "true",
                },
            )
            results: list[dict] = []
            for item in data.get("results", [])[:limit]:
                votes = item.get("votes") or {}
                results.append(
                    {
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "source": (item.get("source") or {}).get("title"),
                        "published": str(item.get("published_at", ""))[:10],
                        "kind": item.get("kind"),
                        "votes_pos": votes.get("positive", 0),
                        "votes_neg": votes.get("negative", 0),
                        "votes_imp": votes.get("important", 0),
                        "currencies": [c.get("code") for c in (item.get("currencies") or [])],
                    }
                )
            if results:
                return results
        except Exception as exc:
            logger.warning("[collector] CryptoPanic 실패, RSS로 전환: %s", exc)

    # CryptoPanic 키 없거나 실패 시 RSS 피드 사용
    return _fetch_rss_news(limit)


def _fetch_open_interest(symbol: str) -> dict:
    try:
        row = _safe_get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": symbol})
        return {
            "symbol": row.get("symbol", symbol),
            "open_interest": float(row.get("openInterest", 0)),
            "time": row.get("time"),
        }
    except Exception as exc:
        logger.warning("[collector] openInterest(%s) 실패: %s", symbol, exc)
        return {"symbol": symbol, "open_interest": None, "time": None}


def _fetch_global_ratio(symbol: str, period: str = "1h", limit: int = 24) -> dict:
    try:
        rows = _safe_get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": period, "limit": limit},
        )
        ratios = [float(x.get("longShortRatio", 0)) for x in rows] if rows else []
        return {
            "symbol": symbol,
            "latest": ratios[-1] if ratios else None,
            "avg_24h": (sum(ratios) / len(ratios)) if ratios else None,
            "series": rows,
        }
    except Exception as exc:
        logger.warning("[collector] globalLongShort(%s) 실패: %s", symbol, exc)
        return {"symbol": symbol, "latest": None, "avg_24h": None, "series": []}


def _fetch_top_ratio(symbol: str, period: str = "1h", limit: int = 1) -> dict:
    try:
        rows = _safe_get(
            "https://fapi.binance.com/futures/data/topLongShortAccountRatio",
            params={"symbol": symbol, "period": period, "limit": limit},
        )
        latest = rows[-1] if rows else {}
        return {
            "symbol": symbol,
            "long_short_ratio": float(latest.get("longShortRatio", 0)) if latest else None,
            "long_account": float(latest.get("longAccount", 0)) if latest else None,
            "short_account": float(latest.get("shortAccount", 0)) if latest else None,
            "timestamp": latest.get("timestamp") if latest else None,
        }
    except Exception as exc:
        logger.warning("[collector] topLongShort(%s) 실패: %s", symbol, exc)
        return {
            "symbol": symbol,
            "long_short_ratio": None,
            "long_account": None,
            "short_account": None,
            "timestamp": None,
        }


def _fetch_taker_flow_24h(symbol: str, period: str = "5m") -> dict:
    # 최근 24시간(5분 * 288)
    try:
        rows = _safe_get(
            "https://fapi.binance.com/futures/data/takerlongshortRatio",
            params={"symbol": symbol, "period": period, "limit": 288},
        )
        if not rows:
            return {
                "symbol": symbol,
                "total_buy_vol": None,
                "total_sell_vol": None,
                "avg_buy_sell_ratio": None,
                "dominant_side": "unknown",
                "series": [],
            }

        buy_sum = 0.0
        sell_sum = 0.0
        ratios: list[float] = []
        for row in rows:
            buy = float(row.get("buyVol", 0) or 0)
            sell = float(row.get("sellVol", 0) or 0)
            ratio = float(row.get("buySellRatio", 0) or 0)
            buy_sum += buy
            sell_sum += sell
            ratios.append(ratio)

        return {
            "symbol": symbol,
            "total_buy_vol": buy_sum,
            "total_sell_vol": sell_sum,
            "avg_buy_sell_ratio": (sum(ratios) / len(ratios)) if ratios else None,
            "dominant_side": "long" if buy_sum >= sell_sum else "short",
            "series": rows,
        }
    except Exception as exc:
        logger.warning("[collector] takerlongshortRatio(%s) 실패: %s", symbol, exc)
        return {
            "symbol": symbol,
            "total_buy_vol": None,
            "total_sell_vol": None,
            "avg_buy_sell_ratio": None,
            "dominant_side": "unknown",
            "series": [],
        }


def _fetch_funding_rate(symbol: str) -> dict:
    try:
        row = _safe_get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": symbol})
        return {
            "symbol": symbol,
            "last_funding_rate": float(row.get("lastFundingRate", 0)),
            "mark_price": float(row.get("markPrice", 0)) if row.get("markPrice") is not None else None,
            "index_price": float(row.get("indexPrice", 0)) if row.get("indexPrice") is not None else None,
            "next_funding_time": row.get("nextFundingTime"),
            "time": row.get("time"),
        }
    except Exception as exc:
        logger.warning("[collector] premiumIndex(%s) 실패: %s", symbol, exc)
        return {
            "symbol": symbol,
            "last_funding_rate": None,
            "mark_price": None,
            "index_price": None,
            "next_funding_time": None,
            "time": None,
        }


def fetch_binance_data() -> dict:
    return {
        "btc_open_interest": _fetch_open_interest("BTCUSDT"),
        "eth_open_interest": _fetch_open_interest("ETHUSDT"),
        "btc_long_short_ratio": _fetch_global_ratio("BTCUSDT", limit=24),
        "eth_long_short_ratio": _fetch_global_ratio("ETHUSDT", limit=24),
        "btc_funding": _fetch_funding_rate("BTCUSDT"),
        "eth_funding": _fetch_funding_rate("ETHUSDT"),
        "btc_liquidation_24h": _fetch_taker_flow_24h("BTCUSDT"),
        "eth_liquidation_24h": _fetch_taker_flow_24h("ETHUSDT"),
    }


def fetch_whale_positioning() -> dict:
    return {
        "top_trader_btc": _fetch_top_ratio("BTCUSDT", period="1h", limit=1),
        "retail_btc": _fetch_global_ratio("BTCUSDT", period="1h", limit=1),
    }


def fetch_kimchi_premium(coins_data: list[dict]) -> dict:
    try:
        upbit_rows = _safe_get("https://api.upbit.com/v1/ticker", params={"markets": "KRW-BTC"})
        usdkrw_row = _safe_get("https://api.exchangerate-api.com/v4/latest/USD")

        upbit_krw = float((upbit_rows[0] if upbit_rows else {}).get("trade_price", 0))
        usdkrw = float((usdkrw_row.get("rates") or {}).get("KRW", 0))

        binance_btc = next((c for c in coins_data if c.get("id") == "bitcoin"), {})
        binance_usd = float(binance_btc.get("current_price") or 0)

        if upbit_krw <= 0 or usdkrw <= 0 or binance_usd <= 0:
            return {
                "upbit_btc_krw": upbit_krw or None,
                "usd_krw": usdkrw or None,
                "binance_btc_usd": binance_usd or None,
                "premium_pct": None,
            }

        upbit_usd = upbit_krw / usdkrw
        premium_pct = ((upbit_usd - binance_usd) / binance_usd) * 100

        return {
            "upbit_btc_krw": upbit_krw,
            "usd_krw": usdkrw,
            "upbit_btc_usd": upbit_usd,
            "binance_btc_usd": binance_usd,
            "premium_pct": round(premium_pct, 4),
        }
    except Exception as exc:
        logger.warning("[collector] 김치프리미엄 계산 실패: %s", exc)
        return {
            "upbit_btc_krw": None,
            "usd_krw": None,
            "upbit_btc_usd": None,
            "binance_btc_usd": None,
            "premium_pct": None,
        }


def fetch_exchange_netflow() -> dict:
    api_key = os.getenv("GLASSNODE_API_KEY", "").strip()
    if not api_key:
        return {"source": "glassnode", "netflow_24h": None, "unit": "BTC", "note": "GLASSNODE_API_KEY 미설정"}

    try:
        rows = _safe_get(
            "https://api.glassnode.com/v1/metrics/transactions/transfers_volume_exchanges_net",
            params={"a": "BTC", "i": "24h", "api_key": api_key},
        )
        latest = rows[-1] if rows else {}
        return {
            "source": "glassnode",
            "netflow_24h": float(latest.get("v")) if latest.get("v") is not None else None,
            "timestamp": latest.get("t"),
            "unit": "BTC",
        }
    except Exception as exc:
        logger.warning("[collector] Glassnode netflow 실패: %s", exc)
        return {"source": "glassnode", "netflow_24h": None, "unit": "BTC", "error": str(exc)}


def _fetch_btc_close_series(days: int = 120) -> pd.Series:
    rows = _safe_get(
        f"{COINGECKO_BASE_URL}/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": days},
    ).get("prices", [])
    return pd.Series([float(item[1]) for item in rows], dtype="float64")


def _calc_ta_with_talib(close: pd.Series) -> dict:
    arr = close.values
    rsi = _talib.RSI(arr, timeperiod=14)
    macd, macd_signal, macd_hist = _talib.MACD(arr, fastperiod=12, slowperiod=26, signalperiod=9)
    bb_upper, bb_mid, bb_lower = _talib.BBANDS(arr, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)

    result = {
        "current_price": float(arr[-1]),
        "rsi": float(rsi[-1]) if not pd.isna(rsi[-1]) else None,
        "macd": float(macd[-1]) if not pd.isna(macd[-1]) else None,
        "macd_signal": float(macd_signal[-1]) if not pd.isna(macd_signal[-1]) else None,
        "macd_histogram": float(macd_hist[-1]) if not pd.isna(macd_hist[-1]) else None,
        "bb_upper": float(bb_upper[-1]) if not pd.isna(bb_upper[-1]) else None,
        "bb_mid": float(bb_mid[-1]) if not pd.isna(bb_mid[-1]) else None,
        "bb_lower": float(bb_lower[-1]) if not pd.isna(bb_lower[-1]) else None,
        "ma7": float(_talib.SMA(arr, timeperiod=7)[-1]) if len(arr) >= 7 else None,
        "ma25": float(_talib.SMA(arr, timeperiod=25)[-1]) if len(arr) >= 25 else None,
        "ma99": float(_talib.SMA(arr, timeperiod=99)[-1]) if len(arr) >= 99 else None,
        "indicator_engine": "TA-Lib",
    }
    return result


def _calc_ta_with_fallback(close: pd.Series) -> dict:
    if _ta is None:
        raise RuntimeError("TA-Lib/ta 모두 사용 불가")

    rsi = _ta.momentum.RSIIndicator(close=close, window=14).rsi()
    macd_ind = _ta.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    bb = _ta.volatility.BollingerBands(close=close, window=20, window_dev=2)

    return {
        "current_price": float(close.iloc[-1]),
        "rsi": float(rsi.iloc[-1]),
        "macd": float(macd_ind.macd().iloc[-1]),
        "macd_signal": float(macd_ind.macd_signal().iloc[-1]),
        "macd_histogram": float(macd_ind.macd_diff().iloc[-1]),
        "bb_upper": float(bb.bollinger_hband().iloc[-1]),
        "bb_mid": float(bb.bollinger_mavg().iloc[-1]),
        "bb_lower": float(bb.bollinger_lband().iloc[-1]),
        "ma7": float(close.rolling(window=7).mean().iloc[-1]),
        "ma25": float(close.rolling(window=25).mean().iloc[-1]),
        "ma99": float(close.rolling(window=99).mean().iloc[-1]),
        "indicator_engine": "ta-fallback",
    }


def calc_technical_indicators() -> dict:
    try:
        close = _fetch_btc_close_series(days=120)
        if close.empty or len(close) < 100:
            return {}

        if _talib is not None:
            result = _calc_ta_with_talib(close)
        else:
            result = _calc_ta_with_fallback(close)

        for key, value in list(result.items()):
            if isinstance(value, float):
                if pd.isna(value):
                    result[key] = None
                else:
                    result[key] = round(value, 4)

        return result
    except Exception as exc:
        logger.warning("[collector] 기술지표 계산 실패: %s", exc)
        return {}


def collect_all() -> dict:
    logger.info("[collector] 데이터 수집 시작")

    market = fetch_market_data()
    coins = fetch_coins_data()
    fear_greed = fetch_fear_greed()
    news = fetch_news()
    binance = fetch_binance_data()

    data = {
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market": market,
        "coins": coins,
        "fear_greed": fear_greed,
        "news": news,
        "binance": binance,
        "funding": {
            "btc": binance.get("btc_funding", {}),
            "eth": binance.get("eth_funding", {}),
        },
        "kimchi_premium": fetch_kimchi_premium(coins),
        "whale_positioning": fetch_whale_positioning(),
        "exchange_flow": fetch_exchange_netflow(),
        "technical": calc_technical_indicators(),
    }

    logger.info(
        "[collector] 수집 완료 — coins=%s news=%s fg=%s rsi=%s funding_btc=%s kimchi=%s",
        len(data.get("coins", [])),
        len(data.get("news", [])),
        data.get("fear_greed", {}).get("value", "N/A"),
        data.get("technical", {}).get("rsi", "N/A"),
        data.get("funding", {}).get("btc", {}).get("last_funding_rate", "N/A"),
        data.get("kimchi_premium", {}).get("premium_pct", "N/A"),
    )
    return data
