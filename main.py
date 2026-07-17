# -*- coding: utf-8 -*-
"""
매일 아침 분야별 뉴스 요약을 이메일로 보내는 파이프라인.
GitHub Actions에서 하루 1회 실행되는 것을 전제로 한다.

흐름:
  1) feeds.py의 분야별 RSS 주소에서 오늘자 헤드라인을 수집
  2) 분야별로 Gemini에게 요약을 요청 (종합 2~3문장 + 핵심 항목 최대 4개)
  3) 요약 결과를 HTML 이메일 본문으로 조립
  4) Resend API로 발송

민감정보(GEMINI_API_KEY, RESEND_API_KEY, MAIL_TO)는
전부 환경변수로 주입한다. 코드나 저장소에 절대 하드코딩하지 않는다.
"""

import os
import sys
import json
import html
import re
import datetime

import feedparser
import resend
from google import genai

from feeds import CATEGORIES, ITEMS_PER_CATEGORY


# ---------------------------------------------------------------------------
# 1. 환경변수 로딩
# ---------------------------------------------------------------------------

def load_env():
    """필수 환경변수를 읽는다. 하나라도 비어 있으면 즉시 실패 종료한다.

    반환: dict (GEMINI_API_KEY, RESEND_API_KEY, MAIL_TO, MAIL_FROM, GEMINI_MODEL)
    """
    required = ["GEMINI_API_KEY", "RESEND_API_KEY", "MAIL_TO"]
    env = {}
    missing = []
    for key in required:
        val = os.environ.get(key, "").strip()
        if not val:
            missing.append(key)
        env[key] = val

    if missing:
        print(f"[FATAL] 환경변수 누락: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # 모델명은 선택 항목. 없으면 기본값을 쓴다.
    # 주의: Gemini 모델명은 시점에 따라 바뀌므로 최신값을 ai.google.dev에서 확인할 것.
    env["GEMINI_MODEL"] = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.0-flash"

    # 보내는 주소(From)도 선택 항목.
    # Resend에서 도메인 인증을 하지 않았다면 반드시 onboarding@resend.dev 를 써야 하며,
    # 이 경우 '가입한 Resend 계정 이메일' 한 곳으로만 발송된다(= MAIL_TO를 그 주소로 둘 것).
    # 도메인 인증을 마쳤다면 MAIL_FROM에 news@내도메인.com 형태로 넣으면 아무 수신자에게 보낼 수 있다.
    env["MAIL_FROM"] = (
        os.environ.get("MAIL_FROM", "").strip() or "뉴스 브리핑 <onboarding@resend.dev>"
    )
    return env


# ---------------------------------------------------------------------------
# 2. RSS 수집
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text):
    """RSS summary에 섞여 오는 HTML 태그를 제거한다."""
    if not text:
        return ""
    return _TAG_RE.sub("", text)


def fetch_category(url, limit):
    """단일 RSS에서 상위 limit개 기사를 (title, summary, link) dict 리스트로 반환.

    실패해도 예외를 던지지 않고 빈 리스트를 반환한다.
    한 분야 피드가 죽어도 나머지 분야는 계속 처리하기 위함이다.
    """
    parsed = feedparser.parse(url)

    # bozo=1 이면서 항목도 없으면 파싱 실패로 간주한다.
    if parsed.bozo and not parsed.entries:
        print(f"[WARN] RSS 파싱 실패: {url} ({parsed.bozo_exception})", file=sys.stderr)
        return []

    items = []
    for entry in parsed.entries[:limit]:
        items.append({
            "title": (entry.get("title") or "").strip(),
            "summary": _strip_html(entry.get("summary") or "").strip(),
            "link": (entry.get("link") or "").strip(),
        })
    return items


# ---------------------------------------------------------------------------
# 3. Gemini 요약
# ---------------------------------------------------------------------------

def _unwrap_codeblock(text):
    """Gemini가 ```json ... ``` 코드블록으로 감싸 응답하는 경우를 벗겨낸다."""
    t = (text or "").strip()
    if t.startswith("```"):
        # 첫 줄(``` 또는 ```json)을 제거
        nl = t.find("\n")
        t = t[nl + 1:] if nl != -1 else ""
        # 끝의 ``` 제거
        t = t.rstrip()
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def summarize_category(client, model, category, articles):
    """한 분야의 헤드라인 묶음을 Gemini로 요약한다.

    반환: {"overview": str, "items": [str, ...]}
    실패 시 원본 헤드라인을 items로 돌려주는 폴백을 사용한다.
    """
    if not articles:
        return {"overview": f"{category} 분야에 수집된 기사가 없습니다.", "items": []}

    headline_block = "\n".join(
        f"- {a['title']}: {a['summary']}" for a in articles
    )
    prompt = (
        f"다음은 오늘 '{category}' 분야 뉴스 헤드라인과 짧은 설명 목록이다.\n"
        f"이것을 바탕으로 한국어로 요약하라.\n"
        f"반드시 아래 JSON 형식 '하나만' 출력하라. 코드블록 표시나 다른 설명 텍스트는 넣지 마라.\n"
        f'{{"overview": "오늘 이 분야의 흐름을 2~3문장으로", '
        f'"items": ["핵심 이슈 한 문장", "...최대 4개까지"]}}\n\n'
        f"[헤드라인 목록]\n{headline_block}"
    )

    try:
        resp = client.models.generate_content(model=model, contents=prompt)
        raw = _unwrap_codeblock(resp.text)
        data = json.loads(raw)

        overview = str(data.get("overview", "")).strip()
        items = [str(x).strip() for x in data.get("items", []) if str(x).strip()]

        if not overview and not items:
            raise ValueError("요약 결과가 비어 있음")

        return {"overview": overview, "items": items[:4]}

    except Exception as e:
        # 요약이 실패해도 브리핑 자체는 나가야 하므로 원본 헤드라인으로 대체한다.
        print(f"[WARN] '{category}' 요약 실패 → 원본 헤드라인으로 대체: {e}", file=sys.stderr)
        return {
            "overview": f"{category} 자동 요약에 실패하여 원본 헤드라인을 표시합니다.",
            "items": [a["title"] for a in articles[:4]],
        }


# ---------------------------------------------------------------------------
# 4. 이메일 HTML 조립
# ---------------------------------------------------------------------------

def build_html(sections, now):
    """분야별 요약 리스트를 받아 이메일 HTML 본문 문자열을 만든다.

    sections: [{
        "category": str,
        "overview": str,
        "items": [str, ...],
        "articles": [{"title": str, "link": str}, ...]
    }, ...]
    """
    date_label = now.strftime("%Y년 %m월 %d일")

    blocks = []
    for sec in sections:
        cat = html.escape(sec["category"])
        overview = html.escape(sec["overview"])

        # 핵심 항목 목록
        item_lis = "".join(
            f'<li style="margin:5px 0;">{html.escape(it)}</li>'
            for it in sec["items"]
        ) or '<li style="margin:5px 0;color:#999;">핵심 항목 없음</li>'

        # 원문 링크 목록 (이메일에선 접기/펼치기가 안 되므로 그냥 나열)
        link_lis = "".join(
            f'<li style="margin:4px 0;">'
            f'<a href="{html.escape(a["link"])}" '
            f'style="color:#2563eb;text-decoration:none;">{html.escape(a["title"])}</a>'
            f'</li>'
            for a in sec["articles"] if a["link"]
        ) or '<li style="color:#999;">원문 링크 없음</li>'

        blocks.append(f"""
        <div style="margin-bottom:28px;">
          <h2 style="font-size:18px;margin:0 0 8px;color:#111827;
                     border-left:4px solid #2563eb;padding-left:10px;">{cat}</h2>
          <p style="margin:0 0 10px;color:#374151;line-height:1.65;">{overview}</p>
          <ul style="margin:0 0 12px;padding-left:20px;color:#1f2937;line-height:1.65;">{item_lis}</ul>
          <div style="font-size:13px;color:#6b7280;margin-bottom:4px;">원문 보기</div>
          <ul style="margin:0;padding-left:20px;font-size:14px;">{link_lis}</ul>
        </div>""")

    body = "".join(blocks)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;">
  <div style="max-width:640px;margin:0 auto;padding:24px;background:#ffffff;
              font-family:-apple-system,'Segoe UI','Malgun Gothic',sans-serif;">
    <h1 style="font-size:22px;margin:0 0 4px;color:#111827;">오늘의 뉴스 브리핑</h1>
    <div style="font-size:14px;color:#6b7280;margin-bottom:24px;">{date_label}</div>
    {body}
    <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
    <div style="font-size:12px;color:#9ca3af;line-height:1.6;">
      자동 생성된 브리핑입니다. 요약은 헤드라인과 짧은 설명을 기반으로 하며 원문과 다를 수 있습니다.
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 5. 이메일 발송
# ---------------------------------------------------------------------------

def send_email(env, subject, html_body):
    """Resend 공식 SDK로 HTML 메일을 발송한다.

    MAIL_TO는 콤마로 구분된 여러 수신자를 지원한다.
    단, Resend 도메인 인증 전(onboarding@resend.dev)에는
    '가입한 Resend 계정 이메일' 한 곳으로만 발송되고 다른 주소는 거부된다.

    발송 실패는 예외를 그대로 올려서 상위(main)에서 실패 종료하도록 둔다.
    (stdlib urllib로 api.resend.com에 직접 POST하면 앞단 Cloudflare가 403 error 1010으로
     막는 경우가 있어, 공식 SDK를 사용한다.)
    """
    resend.api_key = env["RESEND_API_KEY"]

    recipients = [r.strip() for r in env["MAIL_TO"].split(",") if r.strip()]

    result = resend.Emails.send({
        "from": env["MAIL_FROM"],
        "to": recipients,
        "subject": subject,
        "html": html_body,
    })

    email_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", "?")
    print(f"[OK] 발송 완료 (id={email_id}) → {', '.join(recipients)}")


# ---------------------------------------------------------------------------
# 6. 엔트리포인트
# ---------------------------------------------------------------------------

def main():
    env = load_env()

    # GitHub Actions 러너는 UTC로 도므로, 표기용 시각은 KST(UTC+9)로 직접 만든다.
    kst = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(kst)

    client = genai.Client(api_key=env["GEMINI_API_KEY"])

    sections = []
    for category, url in CATEGORIES.items():
        articles = fetch_category(url, ITEMS_PER_CATEGORY)
        summary = summarize_category(client, env["GEMINI_MODEL"], category, articles)
        sections.append({
            "category": category,
            "overview": summary["overview"],
            "items": summary["items"],
            "articles": articles,
        })

    html_body = build_html(sections, now)
    subject = f"[뉴스 브리핑] {now.strftime('%m/%d')} 오늘의 분야별 요약"

    try:
        send_email(env, subject, html_body)
    except Exception as e:
        print(f"[FATAL] 이메일 발송 실패: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
