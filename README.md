# 매일 뉴스 브리핑 (GitHub Actions + Gemini)

분야별 뉴스를 매일 아침 07:00(KST)에 요약해 이메일로 보내는 최소 파이프라인.

```
GitHub Actions (cron)
   └─ main.py
        1) RSS 수집        (feeds.py + feedparser)
        2) Gemini 요약     (google-genai)
        3) HTML 조립
        4) Gmail SMTP 발송
```

## 구성 파일

| 파일 | 역할 |
|------|------|
| `feeds.py` | 분야 → RSS 주소 매핑, 분야별 기사 수 |
| `main.py` | 수집 → 요약 → 조립 → 발송 전체 로직 |
| `requirements.txt` | 의존성 (`feedparser`, `google-genai`) |
| `.github/workflows/daily-brief.yml` | 매일 실행 스케줄 + 수동 실행 |

## 설정 순서

### 1) 저장소 준비
이 폴더를 그대로 GitHub 저장소로 올린다. (private/public 무관. private면 무료 Actions 분량 안에서 충분)

### 2) Gemini API 키 발급
- https://aistudio.google.com 에서 API 키를 만든다.

### 3) Gmail 앱 비밀번호 발급
- 보내는 Gmail 계정에 **2단계 인증**을 먼저 켠다.
- Google 계정 > 보안 > 앱 비밀번호에서 16자리 앱 비밀번호를 발급받는다.
- 일반 로그인 비밀번호로는 SMTP 로그인이 되지 않는다.

### 4) GitHub Secrets 등록  ← 민감정보는 전부 여기에만 넣는다
저장소 > Settings > Secrets and variables > Actions > **New repository secret** 으로 아래 4개를 등록한다.

| 이름 | 값 |
|------|-----|
| `GEMINI_API_KEY` | 2)에서 발급한 키 |
| `GMAIL_USER` | 보내는 Gmail 주소 (예: `me@gmail.com`) |
| `GMAIL_APP_PASSWORD` | 3)에서 발급한 앱 비밀번호 |
| `MAIL_TO` | 받는 주소. 콤마로 여러 명 가능 (예: `me@gmail.com, other@x.com`) |

(선택) 모델명을 바꾸려면 Variables 탭에 `GEMINI_MODEL`을 넣는다. 없으면 코드 기본값(`gemini-2.0-flash`)을 쓴다.

> **주의:** API 키·앱 비밀번호·이메일 주소는 코드나 커밋에 절대 넣지 말고, 반드시 GitHub Secrets에만 저장한다. 어떤 챗봇/외부 도구에도 이 값들을 붙여넣지 않는다.

### 5) 동작 확인
- 저장소 > Actions 탭 > `daily-news-brief` > **Run workflow** 로 수동 실행해 본다.
- 로그가 초록불이고 메일이 오면 성공. 이후엔 매일 07:00 KST에 자동 실행된다.

## 알아둘 제약 (정직한 한계)

- **요약 근거가 얕다.** RSS는 원문 전체가 아니라 제목 + 짧은 설명만 준다. 요약은 그 범위 안에서만 이뤄진다. 원문 전체 요약이 필요하면 기사별 본문 크롤링을 추가해야 하는데, 페이월/robots 문제가 붙는다.
- **cron은 정확하지 않다.** GitHub 예약 실행은 부하에 따라 수 분~수십 분 지연되거나 드물게 건너뛴다. 분 단위 정시 보장이 필요하면 별도 스케줄러가 필요하다.
- **60일 규칙.** 저장소에 60일간 아무 활동이 없으면 예약 워크플로가 자동 비활성화된다. 커밋이나 수동 실행 한 번으로 다시 켜진다.
- **이메일엔 접기/펼치기가 없다.** 요약 아래 원문 링크를 나열하는 방식이다. 진짜 "펼쳐보기"는 이후 웹 페이지 레이어에서 붙일 예정.
