# AI 채팅 (Streamlit + Gemini / OpenAI)

ChatGPT / Gemini 스타일의 웹 채팅 앱입니다.

- 🔀 **제공자 전환**: 사이드바에서 Gemini ↔ OpenAI 선택
- 🧠 **모델 선택**: 제공자별 모델 드롭다운
- ⌨️ **스트리밍 응답**: 답변이 실시간으로 타이핑됨
- 🗂️ **대화 기록**: 사이드바에서 이전 대화 전환 / 삭제
- 💾 **영구 저장**: 대화가 로컬 `conversations/` 폴더에 JSON으로 저장됨

## 1. API 키 발급

사용할 제공자의 키만 있으면 됩니다(둘 다 필요 없음).

- **Gemini** (무료 등급 있음): https://aistudio.google.com/app/apikey
- **OpenAI**: https://platform.openai.com/api-keys

## 2. 설치

```bash
cd /Users/idonghun/myProject/chat
/usr/local/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. 키 설정

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

그런 다음 `.streamlit/secrets.toml` 을 열어 가진 키를 채웁니다:

```toml
GEMINI_API_KEY = "AI..."
OPENAI_API_KEY = "sk-..."
```

> `secrets.toml` 과 `conversations/` 는 `.gitignore` 에 포함되어 커밋되지 않습니다.

## 4. 실행

```bash
streamlit run app.py
```

브라우저에서 http://localhost:8501 이 열립니다.

## 파일 구조

| 파일 | 역할 |
|------|------|
| `app.py` | Streamlit UI (사이드바 + 채팅) |
| `llm.py` | LLM 호출 추상화 (제공자/모델). 새 제공자는 여기만 수정 |
| `storage.py` | 대화 JSON 저장/로드 |
| `.streamlit/secrets.toml` | API 키 (직접 생성) |

## 제공자/모델 추가하기

`llm.py` 의 `PROVIDERS` 딕셔너리에 모델을 추가하거나, 새 제공자 항목과
`_stream_<provider>()` 함수를 추가하면 됩니다. `app.py` 는 수정할 필요가 없습니다.
