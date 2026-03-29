import os
import json
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import anthropic
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timedelta
from slack_sdk import WebClient

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY")
GOOGLE_TOKEN = os.environ.get("GOOGLE_TOKEN")
SLACK_TOKEN = os.environ.get("SLACK_TOKEN")

client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
slack = WebClient(token=SLACK_TOKEN)

def get_creds():
    token_data = json.loads(GOOGLE_TOKEN)
    return Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        scopes=token_data["scopes"]
    )

def get_calendar_events():
    try:
        service = build('calendar', 'v3', credentials=get_creds())
        now = datetime.utcnow().isoformat() + 'Z'
        week_later = (datetime.utcnow() + timedelta(days=7)).isoformat() + 'Z'
        events = service.events().list(calendarId='primary', timeMin=now, timeMax=week_later, maxResults=10, singleEvents=True, orderBy='startTime').execute().get('items', [])
        if not events:
            return "이번주 일정 없음"
        return "\n".join([f"{e['start'].get('dateTime', e['start'].get('date'))}: {e['summary']}" for e in events])
    except Exception as e:
        return f"캘린더 조회 실패: {e}"

def search_sheets(keyword):
    try:
        drive = build('drive', 'v3', credentials=get_creds())
        sheets = build('sheets', 'v4', credentials=get_creds())
        results = drive.files().list(
            q=f"mimeType='application/vnd.google-apps.spreadsheet' and name contains '{keyword}'",
            fields="files(id, name)"
        ).execute().get('files', [])
        if not results:
            return f"'{keyword}' 관련 시트 없음"
        output = []
        for f in results[:3]:
            data = sheets.spreadsheets().values().get(spreadsheetId=f['id'], range='A1:Z50').execute()
            rows = data.get('values', [])
            output.append(f"[{f['name']}]\n" + "\n".join(["\t".join(r) for r in rows[:10]]))
        return "\n\n".join(output)
    except Exception as e:
        return f"시트 검색 실패: {e}"

def get_all_slack_messages():
    try:
        result = slack.conversations_list(types="public_channel,private_channel")
        channels = result.get('channels', [])
        output = []
        for ch in channels[:5]:
            try:
                history = slack.conversations_history(channel=ch['id'], limit=5)
                messages = history.get('messages', [])
                if messages:
                    msgs = "\n".join([f"- {m.get('text','')}" for m in messages if m.get('text')])
                    output.append(f"[#{ch['name']}]\n{msgs}")
            except:
                pass
        return "\n\n".join(output) if output else "메시지 없음"
    except Exception as e:
        return f"슬랙 조회 실패: {e}"

def send_slack_message(channel, text):
    try:
        slack.chat_postMessage(channel=f"#{channel}", text=text)
        return f"#{channel} 채널에 메시지 전송 완료"
    except Exception as e:
        return f"슬랙 전송 실패: {e}"

AGENTS = {
    "schedule": {"keywords": ["일정", "스케줄", "미팅", "캘린더", "약속", "행사", "촬영"], "prompt": "당신은 어센트스포츠 스케줄 전담 에이전트입니다. 한국어로만, 짧고 핵심만, 마크다운 기호 절대 사용 금지."},
    "finance": {"keywords": ["정산", "재무", "비용", "청구", "VAT", "세금", "수익", "매출", "지출"], "prompt": "당신은 어센트스포츠 재무/정산 전담 에이전트입니다. 한국어로만, 짧고 핵심만, 마크다운 기호 절대 사용 금지."},
    "sales": {"keywords": ["견적", "영업", "제안서", "계약", "클라이언트", "수주"], "prompt": "당신은 어센트스포츠 영업/견적 전담 에이전트입니다. 한국어로만, 짧고 핵심만, 마크다운 기호 절대 사용 금지."},
    "marketing": {"keywords": ["마케팅", "캠페인", "광고", "콘텐츠", "SNS", "바이메이더"], "prompt": "당신은 어센트스포츠 마케팅 전담 에이전트입니다. 한국어로만, 짧고 핵심만, 마크다운 기호 절대 사용 금지."},
    "ambassador": {"keywords": ["이강인", "앰버서더", "선수", "매니지먼트", "얼티밋"], "prompt": "당신은 어센트스포츠 앰버서더 전담 에이전트입니다. 한국어로만, 짧고 핵심만, 마크다운 기호 절대 사용 금지."},
    "ir": {"keywords": ["투자", "IR", "투자자", "Series", "펀딩", "해외"], "prompt": "당신은 어센트스포츠 IR/해외 전담 에이전트입니다. 한국어로만, 짧고 핵심만, 마크다운 기호 절대 사용 금지."}
}

MASTER_PROMPT = """당신은 어센트스포츠(Ascent Sports)의 총괄 비서 AI입니다. 대표 Ryan Shin을 보좌합니다.
회사: 스포츠 마케팅, 선수 매니지먼트. 앰버서더: 이강인. 클라이언트: 바이메이더. 목표: Series A 투자 유치.
답변 규칙: 한국어로만, 짧고 핵심만, 마크다운 기호 절대 사용 금지"""

conversation_history = {}

def get_agent(message):
    for agent_name, agent in AGENTS.items():
        if any(kw in message for kw in agent["keywords"]):
            return agent_name, agent["prompt"]
    return "master", MASTER_PROMPT

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text
    if user_id not in conversation_history:
        conversation_history[user_id] = []
    agent_name, system_prompt = get_agent(user_message)
    extra = ""

    if any(w in user_message for w in ["일정", "스케줄", "미팅", "캘린더"]):
        extra += f"\n\n[캘린더 일정]\n{get_calendar_events()}"
    if any(w in user_message for w in ["시트", "정산", "데이터", "현황", "목록", "파이프라인", "투자사"]):
        words = user_message.split()
        keyword = next((w for w in words if len(w) > 1), "")
        extra += f"\n\n[구글 시트 데이터]\n{search_sheets(keyword)}"
    if any(w in user_message for w in ["슬랙", "slack"]):
        if any(w in user_message for w in ["보내", "전송"]):
            words = user_message.split()
            channel = next((w.replace('#','') for w in words if '#' in w), 'general')
            text = user_message.split("보내")[-1].strip() if "보내" in user_message else user_message
            extra += f"\n\n[슬랙 전송 결과]\n{send_slack_message(channel, text)}"
        else:
            extra += f"\n\n[슬랙 최근 메시지]\n{get_all_slack_messages()}"

    conversation_history[user_id].append({"role": "user", "content": user_message + extra})
    if len(conversation_history[user_id]) > 20:
        conversation_history[user_id] = conversation_history[user_id][-20:]

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=system_prompt,
        messages=conversation_history[user_id]
    )
    assistant_message = response.content[0].text
    conversation_history[user_id].append({"role": "assistant", "content": assistant_message})
    agent_labels = {"schedule": "스케줄", "finance": "재무", "sales": "영업", "marketing": "마케팅", "ambassador": "앰버서더", "ir": "IR", "master": "총괄"}
    label = agent_labels.get(agent_name, "총괄")
    await update.message.reply_text(f"[{label} 에이전트]\n{assistant_message}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("봇 시작됨!")
    app.run_polling()

if __name__ == "__main__":
    main()
