# Claude Widget

Claude Code 세션 상태를 데스크톱 캐릭터로 보여주는 윈도우용 위젯.

맥 전용인 Masko Code / Clabotch / Varie Claude Avatar 같은 도구를 윈도우에서 쓰려고 만들었다.
캐릭터가 화면 위에 떠 있다가, Claude Code 가 도구를 실행하거나 응답을 마치면 말풍선으로 알려준다.
캐릭터를 클릭하면 아래로 사용량 패널이 펴진다.

![구성](docs/preview.png)

## 필요한 것

- 윈도우 + Python 3.9 이상
- Pillow (`pip install pillow`)
- Claude Code (훅 설정 가능해야 함)

## 설치

VS Code 에서 이 저장소를 열고 Claude Code 에게 `INSTALL.md 대로 설치해줘` 라고 하면
파이썬 확인부터 훅 등록까지 알아서 처리한다.
개발 지식 없는 사람에게 전달할 때는 [GUIDE.md](GUIDE.md) 를 보내면 된다.

직접 하려면 아래 순서를 따른다.

1. 저장소를 받아 원하는 위치에 둔다.

   ```bash
   git clone https://github.com/yoonsimon/claudewidget.git
   ```

2. Pillow 를 설치한다.

   ```bash
   pip install pillow
   ```

3. `~/.claude/settings.json` 의 `hooks` 에 아래 4개 이벤트를 추가한다.
   `<PYTHON>` 은 `where python` 으로 확인한 경로, `<WIDGET>` 은 이 폴더 경로.

   ```json
   {
     "hooks": {
       "PreToolUse": [
         { "matcher": "", "hooks": [{ "type": "command", "command": "\"<PYTHON>\" \"<WIDGET>\\hook.py\"" }] }
       ],
       "PostToolUse": [
         { "matcher": "", "hooks": [{ "type": "command", "command": "\"<PYTHON>\" \"<WIDGET>\\hook.py\"" }] }
       ],
       "Notification": [
         { "matcher": "", "hooks": [{ "type": "command", "command": "\"<PYTHON>\" \"<WIDGET>\\hook.py\"" }] }
       ],
       "Stop": [
         { "matcher": "", "hooks": [{ "type": "command", "command": "\"<PYTHON>\" \"<WIDGET>\\hook.py\"" }] }
       ]
     }
   }
   ```

4. Claude Code 를 다시 시작한다. 첫 훅이 울리면 위젯이 알아서 뜬다.

바로 띄워보고 싶으면 `pythonw widget.py` 로 직접 실행해도 된다.

## 사용법

| 동작 | 방법 |
|---|---|
| 위치 이동 | 캐릭터를 드래그 |
| 사용량 패널 펴기/접기 | 캐릭터를 클릭 |
| 이미지 변경 | 우클릭 → 이미지 변경 (PNG · JPG · GIF · WebP) |
| 크기 변경 | 우클릭 → 크기 (96 / 128 / 180 / 240) |
| 항상 위 끄기 | 우클릭 → 항상 위 끄기 |
| 종료 | 우클릭 → 종료 |

캐릭터 이미지는 **배경이 투명한 PNG 또는 GIF** 를 권장한다. 움직이는 GIF 도 그대로 재생된다.
바꾼 이미지는 `config.json` 에 경로로만 저장되므로 원본 파일을 지우면 기본 캐릭터로 돌아온다.

## 말풍선

말풍선 위에는 세션이 돌고 있는 **프로젝트 폴더명**이 붙는다.
하위 폴더에서 작업하더라도 `.git` · `.claude` · `CLAUDE.md` 가 있는 상위 폴더까지 거슬러 올라가므로
`myproject/src` 에서 작업해도 `폴더 myproject` 로 표시된다.

| 상태 | 표시 | 언제 |
|---|---|---|
| 도구 실행 중 | `실행중: Bash` | Claude 가 파일을 읽거나 명령을 실행할 때 |
| 입력 대기 | 알림 문구 | 권한 승인이나 입력을 기다릴 때 |
| 응답 완료 | 마지막 응답 요약 | 답변이 끝났을 때 (8초간 표시) |

여러 폴더에서 Claude Code 를 동시에 돌리면 **폴더마다 말풍선이 하나씩** 생겨 위로 쌓인다.
가장 최근 것이 캐릭터 바로 위에 꼬리를 달고 붙는다. 30분간 조용한 세션은 목록에서 빠진다.

## 사용량 패널

`~/.claude/.credentials.json` 의 OAuth 토큰으로 `api.anthropic.com/api/oauth/usage` 를 조회해
5시간 세션·주간 전체·모델별 사용률을 막대로 보여준다. 패널이 열려 있는 동안 5분마다 자동 갱신한다.

토큰은 조회 시점에만 읽고 어디에도 저장하거나 기록하지 않는다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `widget.py` | 캐릭터 창, 말풍선, 사용량 패널 (실행 진입점) |
| `hook.py` | 훅이 실제로 실행하는 래퍼. 대상이 깨져도 항상 0으로 끝난다 |
| `hook_bridge.py` | 상태를 기록하고 위젯을 띄운다 |
| `usage.py` | 사용량 조회 |
| `markdown.py` | 말풍선용 마크다운 파서 |
| `assets/default.png` | 기본 캐릭터 |
| `config.json` | 위치·이미지·크기 설정 (자동 생성) |
| `state/<프로젝트>.json` | 프로젝트별 세션 상태 (자동 생성) |

## 말풍선 마크다운

응답에 섞인 마크다운을 그대로 그린다. 지원 범위는 말풍선 크기에서 읽히는 것까지다.

| 문법 | 표시 |
|---|---|
| `**굵게**` | 굵은 글씨 |
| `` `코드` `` | 회색 배경 + 붉은 글씨 |
| `# 제목` | 큰 굵은 글씨 |
| `- 항목` / `1. 항목` | 불릿 · 번호 목록 |
| `> 인용` | 왼쪽 선 + 들여쓰기 |
| `[텍스트](주소)` | 파란 글씨 (주소는 숨김) |
| `---` | 가로 구분선 |

표와 이미지는 지원하지 않고, `*기울임*` 과 `~~취소~~` 는 기호만 떼고 평문으로 그린다.

## 구현 메모

tkinter 는 픽셀별 알파를 지원하지 않고 **하나의 색만 투명 키로 지정**할 수 있다 (`-transparentcolor`).
그래서 반투명 픽셀을 그대로 두면 키 색(마젠타)과 섞여 가장자리에 분홍 테두리가 생긴다.
이를 막기 위해 이미지와 말풍선 모두

1. 투명 영역에 가장자리 색을 번지게 해서 축소할 때 검정이 딸려오지 않게 하고,
2. 알파를 0 또는 255 로 잘라낸 뒤 키 색 위에 얹는다.

사용량은 로컬 JSONL 을 집계하지 않는다. `~/.claude/.credentials.json` 의 토큰으로
비공개 엔드포인트를 한 번 호출할 뿐이라, 그 응답 형식이 바뀌면 패널이 먼저 깨진다.
`limits` 배열을 우선 읽고 없을 때만 구식 `five_hour` · `seven_day` 필드로 떨어지게 해 두었다.

## 라이선스

MIT. 기본 캐릭터 이미지도 이 저장소에서 직접 그린 것이라 같은 조건으로 쓸 수 있다.
