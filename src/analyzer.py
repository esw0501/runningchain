"""
analyzer.py — Claude 기반 리포트/요약 생성
요구사항 반영:
- 기존 섹션 + 추가 섹션(펀딩비, 김치프리미엄, 고래vs개인, 트레이딩포인트, 교육포인트)
- telegram_summary를 지정된 포맷으로 생성
- 기존 publisher 호환을 위한 REPORT_SECTIONS 매핑 유지
"""

from __future__ import annotations

import json
import logging

import anthropic

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, REPORT_SECTIONS

logger = logging.getLogger(__name__)

EXPANDED_SECTION_KEYS = [
    "시장 전체 현황",
    "선물 시장 분석",
    "기술적 지표 분석",
    "주요 뉴스 분석",
    "단기 예측",
    "투자자 유의사항",
    "펀딩비 분석",
    "김치 프리미엄 분석",
    "고래 vs 개인 포지션",
    "오늘의 트레이딩 포인트",
    "오늘의 교육 포인트",
]


def _fmt_money(v):
    if v is None:
        return "N/A"
    try:
        v = float(v)
    except Exception:
        return "N/A"
    if v >= 1_000_000_000_000:
        return f"${v/1_000_000_000_000:.2f}T"
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    return f"${v:,.2f}"


def _build_prompt(data: dict) -> str:
    market = data.get("market", {})
    coins = data.get("coins", [])
    news = data.get("news", [])
    fg = data.get("fear_greed", {})
    technical = data.get("technical", {})
    funding = data.get("funding", {})
    whale = data.get("whale_positioning", {})
    kimchi = data.get("kimchi_premium", {})
    exchange_flow = data.get("exchange_flow", {})
    binance = data.get("binance", {})

    top5 = []
    for coin in coins[:5]:
        top5.append(
            f"- {coin.get('symbol', '').upper()}: ${coin.get('current_price', 0):,.2f}, "
            f"24h {coin.get('price_change_24h', 0):+.2f}%, 거래량 {_fmt_money(coin.get('total_volume'))}"
        )

    news_lines = []
    for item in news[:8]:
        news_lines.append(f"- [{item.get('published', 'N/A')}] {item.get('title', '')} (출처: {item.get('source', 'N/A')})")

    prompt = f"""
너는 암호화폐 리서치 데스크의 수석 애널리스트다.
아래 데이터로 한국어 심화 브리핑을 작성해라.
반드시 숫자/근거를 포함해라.

[수집 시각]
- {data.get('collected_at', 'N/A')}

[시장 지표]
- 전체 시가총액: {_fmt_money(market.get('total_market_cap_usd'))}
- 시총 24h 변화: {market.get('market_cap_change_24h', 0):+.2f}%
- BTC 도미넌스: {market.get('btc_dominance', 0):.2f}%
- ETH 도미넌스: {market.get('eth_dominance', 0):.2f}%

[주요 코인 5개]
{chr(10).join(top5) if top5 else '- 데이터 없음'}

[선물]
- BTC OI: {binance.get('btc_open_interest', {}).get('open_interest', 'N/A')}
- ETH OI: {binance.get('eth_open_interest', {}).get('open_interest', 'N/A')}
- BTC global long/short latest: {binance.get('btc_long_short_ratio', {}).get('latest', 'N/A')}
- ETH global long/short latest: {binance.get('eth_long_short_ratio', {}).get('latest', 'N/A')}
- BTC 24h taker buy/sell: {binance.get('btc_liquidation_24h', {}).get('total_buy_vol', 'N/A')} / {binance.get('btc_liquidation_24h', {}).get('total_sell_vol', 'N/A')}
- ETH 24h taker buy/sell: {binance.get('eth_liquidation_24h', {}).get('total_buy_vol', 'N/A')} / {binance.get('eth_liquidation_24h', {}).get('total_sell_vol', 'N/A')}

[펀딩비]
- BTC lastFundingRate: {funding.get('btc', {}).get('last_funding_rate', 'N/A')}
- ETH lastFundingRate: {funding.get('eth', {}).get('last_funding_rate', 'N/A')}

[김치 프리미엄]
- upbit KRW-BTC: {kimchi.get('upbit_btc_krw', 'N/A')}
- USD/KRW: {kimchi.get('usd_krw', 'N/A')}
- binance BTC(USD): {kimchi.get('binance_btc_usd', 'N/A')}
- premium(%): {kimchi.get('premium_pct', 'N/A')}

[고래 vs 개인]
- 상위 트레이더 BTC ratio: {whale.get('top_trader_btc', {}).get('long_short_ratio', 'N/A')}
- 상위 트레이더 long_account / short_account: {whale.get('top_trader_btc', {}).get('long_account', 'N/A')} / {whale.get('top_trader_btc', {}).get('short_account', 'N/A')}
- 일반 투자자 BTC ratio(latest): {whale.get('retail_btc', {}).get('latest', 'N/A')}

[거래소 순유입/유출]
- source: {exchange_flow.get('source', 'N/A')}
- netflow_24h(BTC): {exchange_flow.get('netflow_24h', 'N/A')}

[기술 지표]
- engine: {technical.get('indicator_engine', 'N/A')}
- RSI: {technical.get('rsi', 'N/A')}
- MACD/SIGNAL/HIST: {technical.get('macd', 'N/A')} / {technical.get('macd_signal', 'N/A')} / {technical.get('macd_histogram', 'N/A')}
- BB upper/mid/lower: {technical.get('bb_upper', 'N/A')} / {technical.get('bb_mid', 'N/A')} / {technical.get('bb_lower', 'N/A')}
- MA7/25/99: {technical.get('ma7', 'N/A')} / {technical.get('ma25', 'N/A')} / {technical.get('ma99', 'N/A')}

[뉴스 후보]
{chr(10).join(news_lines) if news_lines else '- 데이터 없음'}

반드시 JSON만 출력해라. 코드블록 금지.

{{
  "headline": "😨 임팩트 한줄",
  "one_liner": "오늘 핵심 한줄",
  "sections": {{
    "시장 전체 현황": "...",
    "선물 시장 분석": "...",
    "기술적 지표 분석": "...",
    "주요 뉴스 분석": "...",
    "단기 예측": "...",
    "투자자 유의사항": "...",
    "펀딩비 분석": "현재 펀딩비 수치, 양/음수 의미, 비용 영향",
    "김치 프리미엄 분석": "현재 수치, 과열/침체 해석, 한국 투자자 관점",
    "고래 vs 개인 포지션": "상위 트레이더 vs 일반 투자자 비율 차이와 역발상 전략",
    "오늘의 트레이딩 포인트": "롱 관점/숏 관점/주요 지지저항",
    "오늘의 교육 포인트": "초보자도 이해 가능한 교육 포인트 1개"
  }},
  "telegram_summary": "😨 헤드라인 한줄 (임팩트 있게)\\n\\n📊 날짜 러닝체인 브리핑\\n\\n💰 시장 현황\\n- BTC $XX,XXX (+X.X%)\\n- ETH $X,XXX (+X.X%)\\n- 시총 $X.XXT\\n\\n⚡ 선물 시장 신호\\n- 펀딩비: X.XXX% (롱과열/숏과열/중립)\\n- 고래 롱/숏: XX:XX\\n- 개인 롱/숏: XX:XX\\n- 24h 청산: 롱 $XXXM / 숏 $XXXM\\n\\n🌶️ 김치 프리미엄: +X.X%\\n\\n📈 기술적 신호\\n- RSI XX (과매수/과매도/중립)\\n- MACD (상승/하락 추세)\\n- 공포탐욕 XX (상태)\\n\\n📌 오늘의 트레이딩 포인트\\n롱: (핵심 한줄)\\n숏: (핵심 한줄)\\n\\n📚 오늘의 교육 포인트\\n(한줄 요약)\\n\\n📄 전체 보고서 → learningchain.com\\n⚠️ 투자 권유 아님"
}}
"""
    return prompt


def _extract_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        return text[start : end + 1]
    return text


def _placeholder(error_message: str) -> dict:
    mapped = {k: f"[{k}] {error_message}" for k in REPORT_SECTIONS}
    detailed = {k: error_message for k in EXPANDED_SECTION_KEYS}
    return {
        "sections": mapped,
        "detailed_sections": detailed,
        "raw": "",
        "model": CLAUDE_MODEL,
        "headline": "😨 데이터 분석 실패",
        "one_liner": error_message,
        "telegram_summary": (
            "😨 데이터 수집/분석 오류\n\n"
            "📊 러닝체인 브리핑\n\n"
            "💰 시장 현황\n- 데이터 확인 필요\n\n"
            "⚡ 선물 시장 신호\n- 데이터 확인 필요\n\n"
            "🌶️ 김치 프리미엄: N/A\n\n"
            "📈 기술적 신호\n- 데이터 확인 필요\n\n"
            "📌 오늘의 트레이딩 포인트\n롱: 보수적 접근\n숏: 변동성 주의\n\n"
            "📚 오늘의 교육 포인트\n데이터 품질 점검이 먼저입니다.\n\n"
            "📄 전체 보고서 → learningchain.com\n"
            "⚠️ 투자 권유 아님"
        ),
        "error": error_message,
    }


def analyze(data: dict) -> dict:
    if ANTHROPIC_API_KEY.startswith("sk-ant-dummy"):
        return _placeholder("ANTHROPIC_API_KEY 미설정")

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": _build_prompt(data)}],
        )
        raw = message.content[0].text
        payload = json.loads(_extract_json(raw))

        detailed = payload.get("sections", {}) or {}

        # 기존 publisher html 섹션 구조와 호환
        mapped_sections = {
            "시장현황": (
                f"{detailed.get('시장 전체 현황', '')}\n\n"
                f"### 펀딩비 분석\n{detailed.get('펀딩비 분석', '')}\n\n"
                f"### 김치 프리미엄 분석\n{detailed.get('김치 프리미엄 분석', '')}\n\n"
                f"### 고래 vs 개인 포지션\n{detailed.get('고래 vs 개인 포지션', '')}"
            ).strip(),
            "뉴스분석": detailed.get("주요 뉴스 분석", ""),
            "기술적지표": (
                f"{detailed.get('기술적 지표 분석', '')}\n\n"
                f"### 오늘의 교육 포인트\n{detailed.get('오늘의 교육 포인트', '')}"
            ).strip(),
            "단기예측": (
                f"{detailed.get('단기 예측', '')}\n\n"
                f"### 오늘의 트레이딩 포인트\n{detailed.get('오늘의 트레이딩 포인트', '')}"
            ).strip(),
            "유의사항": detailed.get("투자자 유의사항", ""),
        }

        for section_name in REPORT_SECTIONS:
            mapped_sections.setdefault(section_name, "")

        result = {
            "sections": mapped_sections,
            "detailed_sections": detailed,
            "raw": raw,
            "model": CLAUDE_MODEL,
            "headline": str(payload.get("headline", "")).strip() or "😨 변동성 경계 구간",
            "one_liner": str(payload.get("one_liner", "")).strip() or "핵심 지표 기반의 보수적 대응이 필요합니다.",
            "telegram_summary": str(payload.get("telegram_summary", "")).strip(),
            "error": None,
        }

        if not result["telegram_summary"]:
            result["telegram_summary"] = (
                "😨 헤드라인 생성 실패\n\n"
                "📊 날짜 러닝체인 브리핑\n\n"
                "💰 시장 현황\n- BTC/ETH 및 시총 변동 확인\n\n"
                "⚡ 선물 시장 신호\n- 펀딩비/롱숏비율 점검\n\n"
                "🌶️ 김치 프리미엄: N/A\n\n"
                "📈 기술적 신호\n- RSI/MACD 점검\n\n"
                "📌 오늘의 트레이딩 포인트\n롱: 추세 확인 후 접근\n숏: 과열 구간 분할 대응\n\n"
                "📚 오늘의 교육 포인트\n리스크 관리가 수익보다 먼저입니다.\n\n"
                "📄 전체 보고서 → learningchain.com\n"
                "⚠️ 투자 권유 아님"
            )

        return result
    except Exception as exc:
        logger.error("[analyzer] 분석 실패: %s", exc)
        return _placeholder(str(exc))
