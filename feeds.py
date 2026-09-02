# -*- coding: utf-8 -*-
"""
분야(카테고리) → RSS 주소 매핑

Google 뉴스 RSS 토픽 피드
 - 한국어 고정
"""

# Google 뉴스 표준 topic
# WORLD / NATION / BUSINESS / TECHNOLOGY / ENTERTAINMENT / SPORTS / SCIENCE / HEALTH
CATEGORIES = {
    "사회": "https://news.google.com/rss/headlines/section/topic/NATION?hl=ko&gl=KR&ceid=KR:ko",
    "경제": "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko",
    "세계": "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ko&gl=KR&ceid=KR:ko",
    "기술": "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=ko&gl=KR&ceid=KR:ko",
    "과학": "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=ko&gl=KR&ceid=KR:ko",
    "연예": "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=ko&gl=KR&ceid=KR:ko",
    "건강": "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=ko&gl=KR&ceid=KR:ko",
}

# 분야별 상위 기사 수
ITEMS_PER_CATEGORY = 8

# 대표 뉴스 피드(헤드라인)
TRENDING_FEED = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
TRENDING_ITEMS = 7
