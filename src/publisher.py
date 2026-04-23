"""
publisher.py — 보고서 저장 + 텔레그램 발송 모듈
  - output/YYYY-MM-DD/report.html 저장
  - 텔레그램 채널로 요약 + HTML 파일 발송
"""

import logging
import json
import requests
from datetime import datetime
from pathlib import Path
from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    OUTPUT_BASE_DIR, REPORT_SECTIONS,
)

logger = logging.getLogger(__name__)


# ── HTML 스타일 ───────────────────────────────────────────────────────────────

_STYLE = """
  :root{--bg:#0d1117;--sur:#161b22;--bdr:#30363d;--txt:#e6edf3;--muted:#8b949e;
        --green:#3fb950;--red:#f85149;--blue:#58a6ff;--yellow:#e3b341}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--txt);font-family:'Segoe UI',sans-serif;
       line-height:1.7;padding:24px}
  header{border-bottom:1px solid var(--bdr);padding-bottom:16px;margin-bottom:32px}
  header h1{font-size:1.6rem;color:var(--blue)}
  .meta{color:var(--muted);font-size:.85rem;margin-top:4px}
  .stats-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
              gap:12px;margin-bottom:32px}
  .stat-card{background:var(--sur);border:1px solid var(--bdr);border-radius:8px;padding:14px}
  .stat-card .lbl{font-size:.75rem;color:var(--muted);text-transform:uppercase}
  .stat-card .val{font-size:1.25rem;font-weight:700;margin-top:4px}
  .stat-card .val.up{color:var(--green)}.stat-card .val.dn{color:var(--red)}
  .coin-tbl{width:100%;border-collapse:collapse;margin-bottom:32px;font-size:.9rem}
  .coin-tbl th{background:var(--sur);color:var(--muted);text-align:right;
               padding:8px 12px;border-bottom:1px solid var(--bdr);font-weight:500}
  .coin-tbl th:first-child{text-align:left}
  .coin-tbl td{padding:8px 12px;border-bottom:1px solid var(--bdr);text-align:right}
  .coin-tbl td:first-child{text-align:left;font-weight:600}
  .up{color:var(--green)}.dn{color:var(--red)}
  .sec{background:var(--sur);border:1px solid var(--bdr);border-radius:8px;
       padding:20px 24px;margin-bottom:20px}
  .sec h2{color:var(--yellow);font-size:1.1rem;margin-bottom:12px;
          padding-bottom:8px;border-bottom:1px solid var(--bdr)}
  .sec p{margin-bottom:8px}
  footer{text-align:center;color:var(--muted);font-size:.8rem;
         margin-top:40px;padding-top:16px;border-top:1px solid var(--bdr)}
"""


# ── 포맷 헬퍼 ─────────────────────────────────────────────────────────────────

def _fmt_usd(v) -> str:
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    return f"${v:,.2f}"


def _fmt_pct(v) -> str:
    if v is None:
        return "<span>N/A</span>"
    cls = "up" if v >= 0 else "dn"
    return f'<span class="{cls}">{v:+.2f}%</span>'


# ── HTML 빌더 ─────────────────────────────────────────────────────────────────

def build_html(data: dict, analysis: dict) -> str:
    market     = data.get("market", {})
    coins      = data.get("coins", [])
    fg         = data.get("fear_greed", {})
    date_str   = datetime.now().strftime("%Y-%m-%d")

    # stat cards
    cap_ch = market.get("market_cap_change_24h", 0) or 0
    cards = [
        ("총 시가총액",  _fmt_usd(market.get("total_market_cap_usd")), cap_ch),
        ("24H 거래량",   _fmt_usd(market.get("total_volume_usd")),     None),
        ("BTC 도미넌스", f"{market.get('btc_dominance', 0) or 0:.1f}%", None),
        ("ETH 도미넌스", f"{market.get('eth_dominance', 0) or 0:.1f}%", None),
        ("상장 코인 수", str(market.get("active_cryptocurrencies", "N/A")), None),
    ]
    stat_html = ""
    for lbl, val, ch in cards:
        cls = extra = ""
        if ch is not None:
            cls   = "up" if ch >= 0 else "dn"
            extra = f'<div style="font-size:.8rem;color:var(--muted)">{ch:+.2f}%</div>'
        stat_html += (
            f'<div class="stat-card">'
            f'<div class="lbl">{lbl}</div>'
            f'<div class="val {cls}">{val}</div>{extra}</div>\n'
        )

    # coin rows
    coin_rows = ""
    for c in coins:
        p = c.get("current_price") or 0
        coin_rows += (
            f"<tr><td>{c['symbol']}</td>"
            f"<td>${p:,.2f}</td>"
            f"<td>{_fmt_pct(c.get('price_change_1h'))}</td>"
            f"<td>{_fmt_pct(c.get('price_change_24h'))}</td>"
            f"<td>{_fmt_pct(c.get('price_change_7d'))}</td>"
            f"<td>${c.get('high_24h') or 0:,.2f}</td>"
            f"<td>${c.get('low_24h') or 0:,.2f}</td>"
            f"<td>{_fmt_usd(c.get('market_cap'))}</td></tr>\n"
        )

    # fear & greed
    fg_val  = fg.get("value", "N/A")
    fg_cls  = fg.get("value_classification", "N/A")
    fg_yday = fg.get("yesterday_value", "N/A")
    fg_week = fg.get("week_ago_value",  "N/A")

    # analysis sections
    sec_html = ""
    for s in REPORT_SECTIONS:
        content = analysis["sections"].get(s, "")
        paras = "\n".join(
            f"<p>{line}</p>" for line in content.split("\n") if line.strip()
        )
        sec_html += f'<div class="sec"><h2>{s}</h2>\n{paras}\n</div>\n'

    err_banner = ""
    if analysis.get("error"):
        err_banner = (
            f'<div style="background:#f8514922;border:1px solid var(--red);'
            f'border-radius:8px;padding:12px;margin-bottom:20px;color:var(--red)">'
            f'⚠️ {analysis["error"]}</div>\n'
        )

    return (
        f"<!DOCTYPE html>\n<html lang='ko'>\n<head>\n"
        f"<meta charset='UTF-8'>\n"
        f"<meta name='viewport' content='width=device-width,initial-scale=1.0'>\n"
        f"<title>코인 시장 분석 보고서 — {date_str}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        f"<header>\n"
        f"  <h1>📊 코인 시장 분석 보고서</h1>\n"
        f"  <div class='meta'>수집 시각: {data.get('collected_at','N/A')} "
        f"| 모델: {analysis.get('model','N/A')}</div>\n"
        f"</header>\n"
        f"<div class='stats-grid'>\n{stat_html}</div>\n"
        f"{err_banner}"
        f"<div class='sec'><h2>😨 공포탐욕지수</h2>\n"
        f"<p>현재: <strong>{fg_val} — {fg_cls}</strong> "
        f"| 어제: {fg_yday} | 7일 전: {fg_week}</p></div>\n"
        f"<div class='sec'><h2>💰 주요 코인 시세</h2>\n"
        f"<table class='coin-tbl'>\n"
        f"<thead><tr><th>코인</th><th>현재가</th><th>1H</th><th>24H</th><th>7D</th>"
        f"<th>24H 고가</th><th>24H 저가</th><th>시가총액</th></tr></thead>\n"
        f"<tbody>\n{coin_rows}</tbody>\n</table>\n</div>\n"
        f"{sec_html}"
        f"<footer>LearningChain · 자동 생성 보고서 · {date_str} "
        f"| 본 보고서는 투자 조언이 아닙니다.</footer>\n"
        f"</body></html>"
    )


def save_report(html: str) -> Path:
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir  = Path(OUTPUT_BASE_DIR) / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(html, encoding="utf-8")
    logger.info(f"보고서 저장: {path.resolve()}")
    return path


# ── 텔레그램 ──────────────────────────────────────────────────────────────────

def _tg_api(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def send_telegram_text(text: str, parse_mode: str | None = "HTML") -> bool:
    if TELEGRAM_BOT_TOKEN.startswith("0000000000"):
        logger.info("[Telegram] 더미 토큰 — 발송 건너뜀")
        return False

    try:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        r = requests.post(_tg_api("sendMessage"), json=payload, timeout=10)
        r.raise_for_status()
        logger.info("[Telegram] 텍스트 메시지 발송 완료")
        return True
    except Exception as e:
        logger.warning(f"[Telegram] 텍스트 발송 실패: {e}")
        return False


def send_telegram_summary(data: dict, analysis: dict, report_path: Path) -> bool:
    prebuilt_summary = str(analysis.get("telegram_summary", "")).strip()
    if prebuilt_summary:
        return send_telegram_text(prebuilt_summary, parse_mode=None)

    market = data.get("market", {})
    fg     = data.get("fear_greed", {})
    tech   = data.get("technical", {})
    coins  = data.get("coins", [])
    date_str = datetime.now().strftime("%Y년 %m월 %d일")

    btc = next((c for c in coins if c.get("id") == "bitcoin"),  {})
    eth = next((c for c in coins if c.get("id") == "ethereum"), {})

    cap_t    = market.get("total_market_cap_usd")
    cap_str  = f"${cap_t/1e12:.2f}T" if cap_t else "N/A"
    cap_ch   = market.get("market_cap_change_24h", 0) or 0
    btc_p    = btc.get("current_price", 0) or 0
    btc_ch   = btc.get("price_change_24h", 0) or 0
    eth_p    = eth.get("current_price", 0) or 0
    eth_ch   = eth.get("price_change_24h", 0) or 0
    fg_val   = fg.get("value", "N/A")
    fg_cls   = fg.get("value_classification", "N/A")
    rsi      = tech.get("rsi", "N/A")
    bb_lo    = tech.get("bb_lower", "N/A")
    bb_hi    = tech.get("bb_upper", "N/A")

    # RSI 상태 텍스트
    rsi_state = ""
    if isinstance(rsi, float):
        if rsi >= 70:
            rsi_state = " 과매수"
        elif rsi <= 30:
            rsi_state = " 과매도"
        else:
            rsi_state = " 중립"

    # BB 포맷
    bb_str = f"${bb_lo:,}~${bb_hi:,}" if isinstance(bb_lo, float) else "N/A"

    headline  = analysis.get("headline", "📊 오늘의 암호화폐 시장 분석")
    one_liner = analysis.get("one_liner", "")

    import html as htmllib

    def h(s) -> str:
        """HTML 이스케이프"""
        return htmllib.escape(str(s))

    text = (
        f"<b>{h(headline)}</b>\n"
        f"\n"
        f"📊 {h(date_str)} 러닝체인 브리핑\n"
        f"\n"
        f"💰 <b>시장 현황</b>\n"
        f"- BTC ${btc_p:,.0f} ({btc_ch:+.1f}%)\n"
        f"- ETH ${eth_p:,.0f} ({eth_ch:+.1f}%)\n"
        f"- 시가총액 {h(cap_str)} ({cap_ch:+.1f}%)\n"
        f"\n"
        f"😱 공포탐욕지수 {fg_val} — {h(fg_cls)}\n"
        f"📈 RSI {rsi}{h(rsi_state)} | 볼린저 {h(bb_str)}\n"
        f"\n"
        f"📌 <b>오늘의 핵심 한줄</b>\n"
        f"{h(one_liner)}\n"
        f"\n"
        f"📄 전체 보고서 → learningchain.com\n"
        f"⚠️ 투자 권유 아님"
    )

    return send_telegram_text(text, parse_mode="HTML")


def send_telegram_file(report_path: Path) -> bool:
    if TELEGRAM_BOT_TOKEN.startswith("0000000000"):
        return False
    try:
        with open(report_path, "rb") as f:
            r = requests.post(
                _tg_api("sendDocument"),
                data={"chat_id": TELEGRAM_CHAT_ID},
                files={"document": (report_path.name, f, "text/html")},
                timeout=30,
            )
        r.raise_for_status()
        logger.info("[Telegram] HTML 파일 발송 완료")
        return True
    except Exception as e:
        logger.warning(f"[Telegram] 파일 발송 실패: {e}")
        return False


def send_telegram_fallback_summary(report_path: Path) -> bool:
    date_text = report_path.parent.name
    message = (
        f"러닝체인 보고서 요약\n"
        f"생성일: {date_text}\n"
        f"파일명: {report_path.name}\n"
        f"아래 report.html 파일을 확인해 주세요."
    )
    return send_telegram_text(message, parse_mode="HTML")


def resolve_latest_report_bundle() -> tuple[Path, Path, dict] | None:
    base_dir = Path(OUTPUT_BASE_DIR)
    if not base_dir.exists() or not base_dir.is_dir():
        return None

    report_json_files = [path for path in base_dir.glob("*/report.json") if path.is_file()]
    if not report_json_files:
        return None

    latest_json = max(report_json_files, key=lambda p: p.stat().st_mtime)
    report_html = latest_json.with_name("report.html")
    if not report_html.exists() or not report_html.is_file():
        logger.warning(f"[Telegram] report.html 파일이 없습니다: {report_html}")
        return None

    try:
        payload = json.loads(latest_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"[Telegram] report.json 읽기 실패: {exc}")
        return None

    return latest_json, report_html, payload


def resolve_report_path() -> Path | None:
    base_dir = Path(OUTPUT_BASE_DIR)
    if not base_dir.exists() or not base_dir.is_dir():
        return None

    today_report = base_dir / datetime.now().strftime("%Y-%m-%d") / "report.html"
    if today_report.exists() and today_report.is_file():
        return today_report

    candidates = sorted(
        (
            path
            for path in base_dir.glob("*/report.html")
            if path.is_file()
        ),
        key=lambda p: p.parent.name,
        reverse=True,
    )

    if not candidates:
        return None

    return candidates[0]


def send_telegram(data: dict | None = None, analysis: dict | None = None, report_path: Path | None = None) -> bool:
    """요약 텍스트 먼저, report.html 파일 다음 순서로 항상 세트 발송한다."""
    if data is not None and analysis is not None:
        target_report = report_path or resolve_report_path()
        if target_report is None:
            logger.warning("[Telegram] 발송할 report.html 파일이 없습니다.")
            return False
        summary_ok = send_telegram_summary(data, analysis, target_report)
    else:
        bundle = resolve_latest_report_bundle()
        if bundle is None:
            logger.warning("[Telegram] 최신 report.json/report.html 세트를 찾을 수 없습니다.")
            return False

        _latest_json, target_report, payload = bundle
        telegram_summary = str(payload.get("telegram_summary", "")).strip()
        if not telegram_summary:
            logger.warning("[Telegram] report.json에 telegram_summary 필드가 없습니다.")
            return False

        summary_ok = send_telegram_text(telegram_summary, parse_mode=None)

    if not summary_ok:
        logger.warning("[Telegram] 텍스트 요약 발송 실패로 파일 발송을 중단합니다.")
        return False

    file_ok = send_telegram_file(target_report)
    return summary_ok and file_ok


def publish(data: dict, analysis: dict) -> Path:
    """HTML 생성 → 저장 → 텔레그램 발송. 저장 경로 반환."""
    html        = build_html(data, analysis)
    report_path = save_report(html)
    send_telegram(data=data, analysis=analysis, report_path=report_path)
    return report_path
