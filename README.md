# 장애학 신간 모니터 — 세팅 가이드

## 구조
| 언제 | 누가 | 무엇을 | 비용 |
|---|---|---|---|
| 매일 07:00 | GitHub Actions (`monitor.py`) | 학술지 6곳·추적 학자의 새 글 **제목 전부**를 하루치 메시지 1개로 채널 게시 + `data/주차.json`에 누적 | 0원 |
| 매주 일 20:00 | Claude 예약 작업 (`weekly_prompt.md`) | 그 주 목록을 기준표로 채점 → 7점 이상 최대 10건 한국어 요약 게시 | Pro 사용량 내 |

수집 대상 학술지 (ISSN은 Crossref에서 확인함, 2026-11-07): Disability & Society, Disability Studies Quarterly, ALTER, Scandinavian Journal of Disability Research, Journal of Literary & Cultural Disability Studies, Canadian Journal of Disability Studies.
`한국장애학`은 Crossref에 등록돼 있지 않아 이번 버전에서는 빠졌다 (2단계: KCI 오픈API 연동 검토).

## 1단계. 텔레그램 봇·채널 (5분)
1. 텔레그램에서 **@BotFather** 검색 → `/newbot` → 이름·아이디 입력 → **토큰**(`123456:ABC…`)을 받아 둔다.
2. 새 채널 생성 (비공개 권장) → 채널 설정 › 관리자 › 방금 만든 봇 추가 (메시지 게시 권한).
3. 채널에 아무 글이나 하나 올린 뒤, 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates` 를 연다 → `"chat":{"id":-100…` 숫자가 **CHAT_ID**.
   - 공개 채널이면 `@채널아이디` 를 CHAT_ID로 써도 된다.

## 2단계. OpenAlex 무료 키 (선택이지만 권장)
- openalex.org에서 가입 후 API 키 발급 (무료). 
- 없으면: 학자 추적이 꺼지고, 초록이 거의 비게 된다 (Crossref는 Taylor & Francis 초록을 제공하지 않음) → Claude가 제목만으로 판단.

## 3단계. GitHub 저장소
1. github.com에서 **Public** 저장소 생성 (예: `jangeup-monitor`).
   - 공개인 이유: Claude 예약 작업이 로그인 없이 `data/` 파일을 읽기 위해. 올라가는 건 논문 서지정보뿐이다.
2. 압축을 푼 파일 전부 업로드 (`.github` 폴더 포함 — 숨김 폴더이니 빠지지 않게 확인).
3. Settings › Secrets and variables › Actions › **New repository secret** 로 등록:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (필수)
   - `OPENALEX_API_KEY`, `CONTACT_EMAIL` (선택)
4. Actions 탭 › daily-monitor › **Run workflow** → 채널에 최근 7일치 제목이 올라오면 성공.

## 4단계. 학자 추적 켜기 (OpenAlex 키가 있을 때)
- `python find_author.py "Dan Goodley"` → 후보 중 맞는 ID(`A123…`)를 `config.json`의 `openalex_id`에 넣는다. 동명이인 주의.
- Claude에게 키를 주고 "학자 ID 채워줘"라고 해도 된다.

## 5단계. 주간 요약 예약 작업
- `weekly_prompt.md`의 `<GITHUB_USER>` `<REPO>` `<BOT_TOKEN>` `<CHAT_ID>`를 채워 Claude 예약 작업으로 등록 (Claude에게 맡기면 대신 만들어 줌).
- ⚠️ 봇 토큰이 예약 작업 지시문에 들어간다. 유출되면 이 채널에 남이 글을 올릴 수 있다 → 이 채널 전용 봇으로만 쓰고, 문제가 생기면 @BotFather `/revoke`로 재발급.

## 수정하기
- 학술지 추가·삭제: `config.json`의 `journals` (ISSN만 있으면 됨)
- 채점 기준·번역어: `weekly_prompt.md` 2·3절을 고친 뒤 예약 작업 지시문에 다시 반영
- 실행 시각: `.github/workflows/daily.yml`의 cron (UTC 기준, KST −9시간)
