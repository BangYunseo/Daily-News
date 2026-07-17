# -*- coding: utf-8 -*-
"""
분야(카테고리) → RSS 주소 매핑 설정.

Google 뉴스 RSS 토픽 피드를 기본값으로 쓴다.
 - 무료이고 안정적이며 크롤링/robots 이슈가 없다.
 - hl=ko&gl=KR&ceid=KR:ko 파라미터가 한국어·한국 지역 뉴스를 고정한다.

특정 언론사 RSS로 바꾸고 싶으면 아래 주소만 교체하면 된다.
카테고리를 추가/삭제하려면 이 딕셔너리에 줄을 넣고 빼면 그대로 반영된다.
"""

# 표시 순서 = 딕셔너리 정의 순서(파이썬 3.7+ 보장).
CATEGORIES = {
    "경제": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko",
    "사회": "https://news.google.com/rss/headlines/section/topic/NATION?hl=ko&gl=KR&ceid=KR:ko",
    "세계": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ko&gl=KR&ceid=KR:ko",
    "IT/과학": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=ko&gl=KR&ceid=KR:ko",
}

# 분야별로 Gemini에 넘길 상위 기사 수.
# 너무 크게 잡으면 토큰/비용이 늘고 요약이 산만해진다. 6~10 사이 권장.
ITEMS_PER_CATEGORY = 8

# '오늘의 화제' 배너용 대표 톱뉴스 피드(사람들이 많이 보는 헤드라인 모음).
# 여기 상위 헤드라인들을 Gemini에 넘겨 '지금 가장 화제인 이슈'를 한 건 뽑는다.
TRENDING_FEED = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
TRENDING_ITEMS = 6
