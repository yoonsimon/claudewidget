# 설치 지시서 (Claude Code 용)

이 파일은 **사람이 아니라 Claude Code 가 읽고 실행하는 설치 절차**다.
VS Code 에서 이 저장소를 열고 Claude Code 에게 이렇게 말하면 된다.

> INSTALL.md 대로 설치해줘

---

## Claude 에게: 아래 순서대로 실행할 것

### 0. 전제 확인

- OS 를 먼저 확인한다. **윈도우는 검증된 경로**이고, **macOS 는 실험적**이다.
  Linux 면 여기서 멈추고 사용자에게 알릴 것 (투명 창 처리를 하지 않았다).
- macOS 라면 설치 전에 사용자에게 알릴 것. 코드에 맥 분기는 들어 있으나 실기 검증을 못 했다.
  안 되는 부분이 나오면 고쳐 달라고 요청하고, 아래 **맥에서 확인할 것** 항목을 함께 본다.
- 이 저장소 폴더의 절대 경로를 확인해 둔다. 이하 `<WIDGET>` 으로 부른다.
  설치 후 폴더를 옮기면 훅이 깨지므로, 최종 위치에 둔 상태인지 사용자에게 먼저 확인할 것.
- 경로 표기는 OS 를 따른다. 윈도우는 `<WIDGET>\hook.py`, 맥은 `<WIDGET>/hook.py` 다.
  JSON 안에서 윈도우 역슬래시는 두 번 겹쳐 써야 하고, 맥은 그럴 필요가 없다.

### 1. 파이썬 확인

윈도우:

```bash
where python
where pythonw
```

맥:

```bash
which python3
```

- 3.9 이상이어야 한다. 없으면 사용자에게 설치를 요청하고 멈춘다.
- 나온 경로 중 실제로 동작하는 것을 `<PYTHON>` 으로 삼는다.
  `python --version` 으로 확인할 것. Windows 스토어 별칭(`WindowsApps`)은 껍데기인 경우가 있으니
  버전이 안 나오면 다른 후보를 쓴다.
- 맥에는 `pythonw` 가 없을 수 있다. 없으면 `python3` 를 그대로 쓴다.

### 2. Pillow 설치

```bash
<PYTHON> -m pip install pillow
```

이미 있으면 그대로 넘어간다.

### 3. 동작 확인 (훅을 건드리기 전에)

```bash
<PYTHON> -c "import sys; sys.path.insert(0, r'<WIDGET>'); import widget, usage, markdown, hook_bridge; print('OK')"
```

`OK` 가 안 나오면 훅 설정으로 넘어가지 말 것. 원인을 먼저 해결한다.

### 4. 훅 등록 (가장 조심할 단계)

`~/.claude/settings.json` 의 `hooks` 에 4개 이벤트를 추가한다.

**반드시 지킬 것:**

- **기존 설정을 덮어쓰지 말 것.** 파일에는 `permissions`, `mcpServers`, 다른 훅 등이 이미 들어 있다.
  JSON 을 읽어 `hooks` 안에 **병합**하고 다시 쓴다. 파일 전체를 새로 쓰는 방식은 금지.
- **`hook.py` 를 가리킬 것. `hook_bridge.py` 가 아니다.**
  `hook.py` 는 대상이 없거나 깨져도 항상 0으로 끝나는 래퍼다.
  PreToolUse 훅이 0이 아닌 값으로 끝나면 **그 머신의 모든 Claude Code 세션에서 모든 도구가 차단된다.**
- 이미 같은 경로의 훅이 있으면 중복 추가하지 말 것.
- 쓰기 전에 `settings.json` 을 백업하고, 쓴 뒤에 반드시 JSON 파싱으로 유효성을 검사한다.

추가할 내용 (`<PYTHON>`, `<WIDGET>` 은 실제 경로로 치환. 윈도우 경로의 역슬래시는 JSON 에서 `\\` 로 이스케이프):

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

### 5. 훅이 안전한지 직접 검증

훅을 등록했으면, 등록한 명령이 **실패해도 0으로 끝나는지** 확인한다.

```bash
echo {} | <PYTHON> "<WIDGET>\hook.py"
echo "exit code should be 0"
```

0 이 아니면 즉시 방금 추가한 훅을 되돌리고 사용자에게 알린다.

### 6. 위젯 실행

```bash
<PYTHONW> "<WIDGET>\widget.py"
```

윈도우에서는 `pythonw` 로 실행해야 콘솔 창이 안 뜬다.
맥에서는 `python3 "<WIDGET>/widget.py" &` 로 실행한다.
화면에 캐릭터가 나타나면 성공이다.
훅이 등록됐으므로 다음부터는 Claude Code 가 도구를 쓸 때 위젯이 자동으로 뜬다.

### 7. 사용자에게 안내

설치가 끝나면 아래를 알려준다.

- 캐릭터 **드래그** = 위치 이동, **클릭** = 사용량 패널 펴기/접기
- **우클릭** = 이미지 변경, 크기, 항상 위, 종료
- 캐릭터 이미지는 배경이 투명한 PNG 나 GIF 를 권장
- 창을 닫으려면 우클릭 → 종료. 다음 훅이 울리면 다시 뜬다

---

## 제거

1. `~/.claude/settings.json` 에서 `hook.py` 를 참조하는 훅 항목 4개를 지운다.
2. 실행 중인 위젯을 종료한다. (우클릭 → 종료, 또는 `taskkill /F /IM pythonw.exe`)
3. 저장소 폴더를 지운다.

## 맥에서 확인할 것 (실기 검증 전)

맥에서 처음 돌린다면 아래를 하나씩 보고, 어긋나면 고친 뒤 사용자에게 보고할 것.

1. **투명 배경.** `widget.py` 는 맥에서 `-transparent` 와 `systemTransparent` 를 쓴다.
   배경이 검은 사각형으로 나오면 Tk 버전 문제이니 다른 방식을 찾아야 한다.
2. **폰트.** 한글이 네모로 나오면 `FONT_REGULAR` 후보를 실제 존재하는 파일로 바꾼다.
   `.ttc` 는 여러 서체가 묶여 있어 굵은 글씨가 안 잡히면 인덱스 지정이 필요하다.
3. **우클릭 메뉴.** 맥 Tk 는 `Button-2` 를 쓴다. 이미 함께 바인딩해 두었으나 실제로 뜨는지 본다.
4. **Dock 아이콘.** 실행할 때 Dock 에 파이썬 아이콘이 뜨면 거슬린다.
   숨기려면 `.app` 번들로 감싸고 `LSUIElement` 를 켜야 한다.

## 문제가 생겼을 때

**모든 도구가 훅 오류로 막힌 경우.** 훅이 참조하는 파일이 사라졌을 때 생긴다.
Claude Code 는 스스로 고칠 수 없다 (설정 파일을 읽는 것조차 막히기 때문). 사용자가 직접
`~/.claude/settings.json` 에서 해당 훅 항목을 지우거나, 파일을 원래 경로로 되돌려야 한다.

**말풍선이 안 뜨는 경우.** `<WIDGET>\state\` 에 프로젝트별 JSON 이 생기는지 확인한다.
파일이 없으면 훅이 안 불리는 것이고, 파일은 있는데 말풍선이 없으면 위젯 프로세스가 죽은 것이다.

**사용량 패널이 비는 경우.** `<PYTHON> "<WIDGET>\usage.py"` 를 직접 실행해 원인을 본다.
`no-credentials` 면 Claude Code 로그인이 안 된 것이고, `http-401` 이면 토큰이 만료된 것이다.
