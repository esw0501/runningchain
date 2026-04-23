import json
import os
import re
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string

PROJECT_ROOT = Path(r"C:\LearningChain")
OUTPUT_DIR = PROJECT_ROOT / "output"
ENV_FILE = PROJECT_ROOT / ".env"
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

app = Flask(__name__)


INDEX_HTML = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LearningChain Admin</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; background: #f7f8fa; color: #111; }
    .card { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
    button { padding: 8px 14px; border: 0; border-radius: 6px; cursor: pointer; background: #0b5fff; color: #fff; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { border-bottom: 1px solid #e5e5e5; padding: 10px; text-align: left; }
    .muted { color: #666; font-size: 14px; }
  </style>
</head>
<body>
  <div class="card">
    <h2>보고서 관리</h2>
    <p class="muted">output 폴더의 report.json, report.html 실제 파일을 읽습니다.</p>
    <button id="sendNowBtn">지금 바로 텔레그램 발송</button>
    <div id="sendResult" class="muted" style="margin-top:10px;"></div>
  </div>

  <div class="card">
    <h3>보고서 목록</h3>
    <table>
      <thead>
        <tr>
          <th>날짜 폴더</th>
          <th>제목(generated_at)</th>
          <th>요약 일부</th>
          <th>HTML 파일</th>
        </tr>
      </thead>
      <tbody id="reportRows"></tbody>
    </table>
  </div>

  <script>
    async function loadReports() {
      const res = await fetch('/api/reports');
      const data = await res.json();
      const rows = document.getElementById('reportRows');

      if (!data.length) {
        rows.innerHTML = '<tr><td colspan="4">표시할 보고서가 없습니다.</td></tr>';
        return;
      }

      rows.innerHTML = data.map(r => `
        <tr>
          <td>${r.report_date}</td>
          <td>${r.title}</td>
          <td>${(r.telegram_summary || '').slice(0, 60)}</td>
          <td>${r.has_report_html ? '있음' : '없음'}</td>
        </tr>
      `).join('');
    }

    async function sendNow() {
      const out = document.getElementById('sendResult');
      out.textContent = '발송 중...';
      const res = await fetch('/api/reports/send-now', { method: 'POST' });
      const data = await res.json();
      out.textContent = data.message || '완료';
    }

    document.getElementById('sendNowBtn').addEventListener('click', sendNow);
    loadReports();
  </script>
</body>
</html>
"""


def read_env_vars() -> dict:
    values = {}
    if not ENV_FILE.exists() or not ENV_FILE.is_file():
        return values

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, value = item.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_date_folders_desc() -> list[Path]:
    if not OUTPUT_DIR.exists() or not OUTPUT_DIR.is_dir():
        return []
    folders = [f for f in OUTPUT_DIR.iterdir() if f.is_dir() and DATE_PATTERN.match(f.name)]
    folders.sort(key=lambda p: p.name, reverse=True)
    return folders


def read_report_bundle(folder: Path):
    report_json = folder / "report.json"
    report_html = folder / "report.html"

    if not report_json.exists() or not report_json.is_file():
        return None

    try:
        payload = json.loads(report_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    telegram_summary = str(payload.get("telegram_summary", "")).strip()

    return {
        "folder": folder,
        "report_date": folder.name,
        "report_json": report_json,
        "report_html": report_html,
        "has_report_html": report_html.exists() and report_html.is_file(),
        "generated_at": str(payload.get("generated_at", "")).strip() or "generated_at 없음",
        "telegram_summary": telegram_summary,
        "payload": payload,
    }


def list_reports() -> list[dict]:
    items = []
    for folder in get_date_folders_desc():
        bundle = read_report_bundle(folder)
        if bundle is None:
            continue
        items.append(
            {
                "report_date": bundle["report_date"],
                "title": bundle["generated_at"],
                "telegram_summary": bundle["telegram_summary"],
                "has_report_html": bundle["has_report_html"],
            }
        )
    return items


def get_latest_bundle():
    for folder in get_date_folders_desc():
        bundle = read_report_bundle(folder)
        if bundle is None:
            continue
        if not bundle["telegram_summary"]:
            continue
        if not bundle["has_report_html"]:
            continue
        return bundle
    return None


def send_telegram_message(token: str, chat_id: str, message: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=15)
    response.raise_for_status()


def send_telegram_file(token: str, chat_id: str, report_html_path: Path):
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with open(report_html_path, "rb") as fp:
        response = requests.post(
            url,
            data={"chat_id": chat_id},
            files={"document": (report_html_path.name, fp, "text/html")},
            timeout=30,
        )
    response.raise_for_status()


@app.get("/")
def index():
    return render_template_string(INDEX_HTML)


@app.get("/api/reports")
def api_reports():
    return jsonify(list_reports())


@app.post("/api/reports/send-now")
def api_send_now():
    env = read_env_vars()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        return jsonify({"message": "C:/LearningChain/.env 에서 텔레그램 토큰/채널을 찾을 수 없습니다."}), 500

    bundle = get_latest_bundle()
    if bundle is None:
        return jsonify({"message": "발송 가능한 최신 report.json/report.html 세트를 찾을 수 없습니다."}), 404

    try:
        send_telegram_message(token, chat_id, bundle["telegram_summary"])
        send_telegram_file(token, chat_id, bundle["report_html"])
    except Exception as exc:
        return jsonify({"message": f"텔레그램 발송 실패: {exc}"}), 500

    return jsonify(
        {
            "message": "telegram_summary + report.html 발송 완료",
            "report_date": bundle["report_date"],
            "generated_at": bundle["generated_at"],
        }
    )


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    app.run(host="127.0.0.1", port=5000, debug=True)
