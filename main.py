"""
LearningChain — 코인 시장 분석 보고서 자동화
실행: python main.py
"""
import io
import logging
import sys
from datetime import datetime
from pathlib import Path

# Windows CP949 환경에서 한국어 출력 보장
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 프로젝트 루트를 sys.path 에 추가
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import OUTPUT_BASE_DIR
from src.collector import collect_all
from src.analyzer  import analyze
from src.publisher import publish


def setup_logging() -> logging.Logger:
    log_dir  = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger("main")


def main():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("LearningChain Crypto Report — 시작")
    logger.info("=" * 60)

    # ── STEP 1: 데이터 수집 ──────────────────────────────────────
    logger.info("[1/3] 데이터 수집 중...")
    try:
        data = collect_all()
    except Exception as e:
        logger.error(f"데이터 수집 실패: {e}", exc_info=True)
        sys.exit(1)

    logger.info(
        f"  완료 — 코인 {len(data.get('coins', []))}개 "
        f"/ 뉴스 {len(data.get('news', []))}건 "
        f"/ 공포탐욕지수 {data.get('fear_greed', {}).get('value', 'N/A')}"
    )

    # ── STEP 2: AI 분석 ──────────────────────────────────────────
    logger.info("[2/3] AI 분석 중 (Claude)...")
    try:
        analysis = analyze(data)
    except Exception as e:
        logger.error(f"AI 분석 실패: {e}", exc_info=True)
        analysis = {
            "sections": {},
            "raw":      "",
            "model":    "N/A",
            "error":    str(e),
        }

    if analysis.get("error"):
        logger.warning(f"  주의: {analysis['error']}")
    else:
        logger.info("  AI 분석 완료")

    # ── STEP 3: 보고서 발행 ──────────────────────────────────────
    logger.info("[3/3] 보고서 생성 및 발송 중...")
    try:
        report_path = publish(data, analysis)
    except Exception as e:
        logger.error(f"보고서 발행 실패: {e}", exc_info=True)
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("완료!")
    logger.info(f"  보고서: {report_path.resolve()}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
