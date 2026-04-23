import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)

# API Keys
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "sk-ant-dummy-key-for-testing")
GOOGLE_API_KEY      = os.getenv("GOOGLE_API_KEY", "")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "0000000000:AADummy-telegram-bot-token")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",   "-1001234567890")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "dummy_cryptopanic_key")

# AI 모델 설정
CLAUDE_MODEL = "claude-haiku-4-5-20251001"

# Coins to track (CoinGecko IDs)
COIN_IDS = ["bitcoin", "ethereum", "binancecoin", "solana", "ripple",
            "cardano", "avalanche-2", "dogecoin", "polkadot", "chainlink"]

# CoinGecko
COINGECKO_BASE_URL    = "https://api.coingecko.com/api/v3"
COINGECKO_VS_CURRENCY = "usd"

# Alternative.me Fear & Greed
FEAR_GREED_URL = "https://api.alternative.me/fng/"

# CryptoPanic
CRYPTOPANIC_BASE_URL = "https://cryptopanic.com/api/v1/posts/"
CRYPTOPANIC_FILTER   = "important"

# Output
OUTPUT_BASE_DIR = str(PROJECT_ROOT / "output")

# Report sections
REPORT_SECTIONS = ["시장현황", "뉴스분석", "기술적지표", "단기예측", "유의사항"]
