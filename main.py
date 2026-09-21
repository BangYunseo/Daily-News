# -*- coding: utf-8 -*-
"""
당일 뉴스 요약

[Flow]
  1) feeds.py의 분야별 RSS 주소에서 헤드라인 수집
  2) 분야별 Gemini에 요약 요청 (종합 2~3문장 + 핵심 항목 최대 4개)
  3) 요약 결과 HTML 이메일 본문으로 조립
  4) Resend API로 발송

민감정보(GEMINI_API_KEY, RESEND_API_KEY, MAIL_TO)는 Github 환경변수
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
from google.genai import types
from feeds import CATEGORIES, ITEMS_PER_CATEGORY, TRENDING_FEED, TRENDING_ITEMS
from dotenv import load_dotenv

load_dotenv()

# 1. 환경변수

def load_env():
    """필수 환경변수 설정

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
        print(f"[ERROR] 환경변수 누락: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # 모델 선택 (https://ai.google.dev/gemini-api/docs/models)
    env["GEMINI_MODEL"] = "gemini-3.5-flash-lite"
    env["MAIL_FROM"] = "뉴스 브리핑 <onboarding@resend.dev>"
    return env



# 2. RSS 수집

# feedparser 기본 User-Agent("feedparser/6.x +https://github.com/...")로는
# Google 뉴스가 GitHub Actions 러너 IP에 봇 차단 페이지(HTML)를 돌려줄 때가 있다.
# 그러면 XML 파싱이 통째로 깨진다(2026-09-21 전 분야 0건 장애).
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(text):
    if not text:
        return ""
    return _TAG_RE.sub("", text)



def fetch_category(url, limit):
    """단일 RSS에서 상위 limit개 기사를 (title, summary, link) dict 리스트로 반환.

    실패해도 예외를 던지지 않고 빈 리스트를 반환한다.
    한 분야 피드가 죽어도 나머지 분야는 계속 처리하기 위함이다.
    """
    parsed = feedparser.parse(url, agent=_UA)

    # bozo=1 이면서 항목이 없다면 파싱 실패
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


# 3. Gemini 요약

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


# 구조화 출력(structured output) 스키마
# 파싱 실패 차단
_CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "items"],
}

_TRENDING_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["headline", "why"],
}


def summarize_category(client, model, category, articles):
    if not articles:
        return {"overview": f"{category} 분야에 수집된 기사가 없습니다.", "items": []}

    headline_block = "\n".join(
        f"- {a['title']}: {a['summary']}" for a in articles
    )
    prompt = (
        f"다음은 오늘 '{category}' 분야 뉴스 헤드라인과 짧은 설명 목록이다.\n"
        f"이것을 바탕으로 한국어로 요약하라.\n"
        f"제공된 헤드라인과 설명에 실제로 있는 내용만으로 요약하라.\n"
        f"목록에 없는 인물명·직함·숫자·날짜·소속은 추측하거나 채워 넣지 마라.\n"
        f"특히 직함(대통령 등)은 원문에 명시된 경우에만 쓰고, 없으면 직함 없이 쓰거나 생략하라.\n"
        f"네 배경지식으로 원문을 보정하거나 바꾸지 마라.\n"
        f"반드시 아래 JSON 형식 '하나만' 출력하라. 코드블록 표시나 다른 설명 텍스트는 넣지 마라.\n"
        f'{{"overview": "오늘 이 분야의 흐름을 2~3문장으로", '
        f'"items": ["핵심 이슈 한 문장", "...최대 4개까지"]}}\n\n'
        f"[헤드라인 목록]\n{headline_block}"
    )

    try:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_CATEGORY_SCHEMA,
                # 요약은 창작이 아니라 원문 압축이다. 생성 다양성을 0으로 낮춰
                # 모델이 사실을 각색·윤색하지 못하게 한다(정확성 우선).
                temperature=0,
            ),
        )
        raw = _unwrap_codeblock(resp.text)
        data = json.loads(raw)

        overview = str(data.get("overview", "")).strip()
        items = [str(x).strip() for x in data.get("items", []) if str(x).strip()]

        if not overview and not items:
            raise ValueError("요약 결과가 비어 있음")

        return {"overview": overview, "items": items[:4]}

    except Exception as e:
        # 요약이 실패해도 브리핑 자체는 나가야 한다.
        # 단, 원본 헤드라인을 항목으로 나열하지 않고(요청사항) 아래 '원문 보기' 링크만 남긴다.
        print(f"[WARN] '{category}' 요약 실패 → 원문 링크만 표시: {e}", file=sys.stderr)
        return {
            "overview": "자동 요약에 실패했습니다. 아래 원문 링크를 확인하세요.",
            "items": [],
        }


def summarize_trending(client, model, articles):
    if not articles:
        return None

    headline_block = "\n".join(f"- {a['title']}" for a in articles)
    prompt = (
        "다음은 오늘 한국 주요 톱뉴스 헤드라인 목록이다.\n"
        "이 중 지금 사람들의 관심을 가장 많이 받는 '화제의 이슈' 하나를 고르고,\n"
        "무엇이 화제이며 왜 화제가 되는지 한국어로 설명하라.\n"
        "제공된 헤드라인에 실제로 있는 내용만으로 설명하라.\n"
        "목록에 없는 인물명·직함·숫자·날짜·소속은 추측하거나 채워 넣지 마라.\n"
        "특히 직함(대통령 등)은 원문에 명시된 경우에만 쓰고, 없으면 직함 없이 쓰거나 생략하라.\n"
        "네 배경지식으로 원문을 보정하거나 바꾸지 마라.\n"
        "반드시 아래 JSON 형식 하나만 출력하라(코드블록·다른 설명 금지).\n"
        '{"headline": "화제 이슈를 한 문장으로", "why": "왜 화제인지 2~3문장"}\n\n'
        f"[톱뉴스 헤드라인]\n{headline_block}"
    )

    try:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_TRENDING_SCHEMA,
                # 배너는 제목만 받아 근거가 더 얕다. 각색 여지를 없애려 temperature=0.
                temperature=0,
            ),
        )
        data = json.loads(_unwrap_codeblock(resp.text))
        headline = str(data.get("headline", "")).strip()
        why = str(data.get("why", "")).strip()
        if not headline and not why:
            return None
        return {"headline": headline, "why": why}
    except Exception as e:
        print(f"[WARN] 화제 뉴스 요약 실패 → 화제 배너 생략: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# 4. 이메일 HTML 조립
# ---------------------------------------------------------------------------

def build_html(sections, now, trending=None):
    """분야별 요약 리스트를 받아 이메일 HTML 본문 문자열을 만든다.

    sections: [{
        "category": str,
        "overview": str,
        "items": [str, ...],
        "articles": [{"title": str, "link": str}, ...]
    }, ...]
    trending: {"headline": str, "why": str, "link": str} 또는 None.
              값이 있으면 맨 아래 '오늘의 화제' 강조 배너를 추가한다.
    """
    date_label = now.strftime("%Y년 %m월 %d일")

    # 카드별 강조색을 인덱스로 순환한다(feeds.py에서 분야를 바꿔도 안전).
    # 분야 수만큼(현재 7개) 색을 두어 카드끼리 색이 겹치지 않게 한다.
    accents = ["#0f766e", "#b45309", "#1d4ed8", "#6d28d9", "#be123c", "#0369a1", "#4d7c0f"]

    def _card(sec, accent):
        cat = html.escape(sec["category"])
        overview = html.escape(sec["overview"])

        # 핵심 항목: 요약이 있을 때만 렌더링한다.
        # (자동 요약 실패 시엔 원본 헤드라인을 나열하지 않고 아래 '원문 보기'만 남긴다.)
        if sec["items"]:
            item_lis = "".join(
                f'<li style="margin:0 0 6px;">{html.escape(it)}</li>'
                for it in sec["items"]
            )
            items_block = (
                '<ul style="margin:0 0 14px;padding-left:18px;'
                f'color:#374151;font-size:14px;line-height:1.6;">{item_lis}</ul>'
            )
        else:
            items_block = ""

        # 원문 링크 목록 (이메일에선 접기/펼치기가 안 되므로 그냥 나열)
        link_lis = "".join(
            f'<li style="margin:0 0 5px;">'
            f'<a href="{html.escape(a["link"])}" '
            f'style="color:{accent};text-decoration:none;">{html.escape(a["title"])}</a></li>'
            for a in sec["articles"] if a["link"]
        ) or '<li style="color:#9ca3af;">원문 링크 없음</li>'

        link_count = sum(1 for a in sec["articles"] if a["link"])

        # 카드 한 장(이메일 호환을 위해 table 기반, 스타일은 전부 인라인).
        # 원문 보기는 <details> 토글: 지원 클라이언트(Apple Mail·웹메일)에선 접기/펼치기,
        # 미지원(Gmail 등)에선 펼쳐진 목록으로 안전하게 폴백된다(링크가 사라지지 않음).
        # 토글 요약은 카드 우측에 정렬한다(list-style:none으로 좌측 삼각형 마커 제거).
        return (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
            ' style="background:#ffffff;border:1px solid #e6e8ec;border-radius:12px;">'
            '<tr><td style="padding:18px 18px 16px;">'
            f'<div style="font-size:17px;font-weight:700;color:{accent};margin:0 0 6px;">{cat}</div>'
            f'<div style="height:2px;width:32px;background:{accent};opacity:0.4;'
            'margin:0 0 12px;font-size:0;line-height:0;">&nbsp;</div>'
            f'<p style="margin:0 0 12px;color:#3f4650;font-size:14px;line-height:1.65;">{overview}</p>'
            f'{items_block}'
            f'<details style="margin:0;"><summary style="cursor:pointer;list-style:none;'
            f'text-align:right;font-size:11px;letter-spacing:0.08em;'
            f'text-transform:uppercase;color:{accent};font-weight:600;margin:0;">'
            f'원문 보기 ({link_count}) &#9662;</summary>'
            f'<ul style="margin:8px 0 0;padding-left:18px;font-size:13px;line-height:1.55;'
            f'text-align:left;">{link_lis}</ul>'
            '</details>'
            '</td></tr></table>'
        )

    # 세로 1열 카드 스택: 분야마다 카드 한 장을 한 행(tr)으로 쌓는다.
    # (2열 그리드에서 생기던 좌우 카드 높이 불일치 문제를 근본적으로 제거하고,
    #  네이버 등 모바일 메일에서 토글 펼침 시 하단 스크롤이 막히던 현상도 완화된다.)
    rows = []
    for i, sec in enumerate(sections):
        card = _card(sec, accents[i % len(accents)])
        rows.append(f'<tr><td valign="top" style="padding:0 0 14px 0;">{card}</td></tr>')

    grid = "".join(rows)

    # 맨 아래 '오늘의 화제' 강조 배너(어두운 풀와이드). trending이 있을 때만.
    trending_block = ""
    if trending and (trending.get("headline") or trending.get("why")):
        t_head = html.escape(trending.get("headline", ""))
        t_why = html.escape(trending.get("why", ""))
        t_link = html.escape(trending.get("link", ""))
        t_link_html = (
            f'<a href="{t_link}" style="color:#93c5fd;text-decoration:none;'
            'font-weight:600;font-size:13px;">톱뉴스 보기 &rarr;</a>'
        ) if t_link else ""
        trending_block = (
            '<tr><td style="padding:2px 1px 0;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
            ' style="background:#111827;border-radius:12px;">'
            '<tr><td style="padding:20px 22px;">'
            '<div style="font-size:11px;letter-spacing:0.12em;text-transform:uppercase;'
            'color:#fbbf24;margin:0 0 8px;">지금 가장 화제</div>'
            f'<div style="font-size:18px;font-weight:700;color:#ffffff;line-height:1.4;margin:0 0 8px;">{t_head}</div>'
            f'<p style="font-size:14px;color:#cbd5e1;line-height:1.65;margin:0 0 12px;">{t_why}</p>'
            f'{t_link_html}'
            '</td></tr></table>'
            '</td></tr>'
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  /* 원문 보기 토글의 기본 삼각형 마커를 숨겨 우측 정렬이 깔끔하게 보이도록 한다.
     (미지원 클라이언트에선 마커가 남을 수 있으나 기능·레이아웃엔 영향 없음) */
  details > summary {{ list-style: none; }}
  details > summary::-webkit-details-marker {{ display: none; }}
</style>
</head>
<body style="margin:0;padding:0;background:#eef0f3;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef0f3;">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0"
             style="width:640px;max-width:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic','Apple SD Gothic Neo',sans-serif;">
        <tr><td style="padding:2px 8px 20px;">
          <div style="font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#8b93a0;margin:0 0 7px;">Daily Briefing &middot; {date_label}</div>
          <h1 style="font-size:24px;line-height:1.25;margin:0;color:#111827;font-weight:800;">오늘의 뉴스 브리핑</h1>
        </td></tr>
        <tr><td>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            {grid}
          </table>
        </td></tr>
        {trending_block}
        <tr><td style="padding:14px 8px 4px;">
          <div style="border-top:1px solid #dfe2e7;padding-top:14px;font-size:12px;color:#9aa1ac;line-height:1.6;">
            자동 생성된 브리핑입니다. 요약은 헤드라인과 짧은 설명을 기반으로 하며 원문과 다를 수 있습니다.
          </div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 5. 이메일 발송
# ---------------------------------------------------------------------------

def send_email(env, subject, html_body):
    resend.api_key = env["RESEND_API_KEY"]

    recipients = [r.strip() for r in env["MAIL_TO"].split(",") if r.strip()]

    result = resend.Emails.send({
        "from": env["MAIL_FROM"],
        "to": recipients,
        "subject": subject,
        "html": html_body,
    })

    email_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", "?")
    print(f"[OK] 발송 완료 (id={email_id})")


# 6. 실행

def main():
    # 진행 상황을 콘솔에 단계별로 찍는다. flush=True로 즉시 출력해
    # 로컬 실행(run.bat) 중 "지금 어디까지 됐는지"가 실시간으로 보이게 한다.
    print("[준비] 환경설정 로딩 중...", flush=True)
    env = load_env()

    # GitHub Actions 러너는 UTC로 도므로, 표기용 시각은 KST(UTC+9)로 직접 만든다.
    kst = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(kst)

    # 503 UNAVAILABLE(모델 일시 과부하)는 재시도하면 대개 붙는다.
    # SDK 기본값은 재시도 0회(attempts=1)라 명시적으로 켜준다.
    # 기본 설정: 5회, 지수 백오프 약 1/2/4/8초, 408·429·5xx 대상.
    client = genai.Client(
        api_key=env["GEMINI_API_KEY"],
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions()),
    )
    print(f"[준비] Gemini 모델: {env['GEMINI_MODEL']}", flush=True)

    total = len(CATEGORIES)
    sections = []
    for idx, (category, url) in enumerate(CATEGORIES.items(), start=1):
        print(f"[{idx}/{total}] '{category}' 수집 중...", flush=True)
        articles = fetch_category(url, ITEMS_PER_CATEGORY)
        print(f"[{idx}/{total}] '{category}' 기사 {len(articles)}건 Gemini 요약...", flush=True)
        summary = summarize_category(client, env["GEMINI_MODEL"], category, articles)
        sections.append({
            "category": category,
            "overview": summary["overview"],
            "items": summary["items"],
            "articles": articles,
        })

    # 맨 아래 '오늘의 화제' 배너: 대표 톱뉴스에서 가장 화제인 이슈를 뽑아 요약한다.
    # 실패하면 None이 되어 배너만 생략될 뿐, 본문 발송은 그대로 진행된다.
    print("[화제] 오늘의 화제 이슈 분석 중...", flush=True)
    trending_articles = fetch_category(TRENDING_FEED, TRENDING_ITEMS)
    trending = summarize_trending(client, env["GEMINI_MODEL"], trending_articles)
    if trending:
        top = next((a for a in trending_articles if a["link"]), None)
        trending["link"] = top["link"] if top else ""

    # 전 분야 수집 0건 = 피드 차단/장애. 빈 브리핑을 보내면 Actions가 초록불로 끩어
    # 장애를 놓친다. 발송 대신 실패 종료해서 빨간불로 알리게 한다.
    if not any(sec["articles"] for sec in sections):
        print("[ERROR] 전 분야 기사 0건 — 발송 중단", file=sys.stderr)
        sys.exit(1)

    print("[조립] 이메일 본문 생성 중...", flush=True)
    html_body = build_html(sections, now, trending)
    subject = f"[뉴스 브리핑] {now.strftime('%m/%d')} 오늘의 분야별 요약"

    print("[발송] 이메일 발송 중...", flush=True)
    try:
        send_email(env, subject, html_body)
    except Exception as e:
        print(f"[ERROR] 이메일 발송 실패: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
