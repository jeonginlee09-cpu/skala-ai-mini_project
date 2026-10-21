# messages·updates 구분을 현재 프로젝트에 적용한 방식

수업 사진에서 `messages`는 LLM 텍스트 조각, `updates`는 노드 완료 시 상태 변화를 전달합니다. 이번 버전은 이 두 채널을 소비하고, 코드에서 직접 실행하는 도구의 즉시 진행 정보에는 `custom`을 함께 사용합니다.

## 변경

- `agent/openai_stream.py`: 기존 httpx와 langchain-core만 사용합니다. OpenAI의 실제 SSE 응답을 `AIMessageChunk`로 전달하는 `BaseChatModel` 어댑터입니다. 키 필드는 직렬화·repr에서 제외하며 토큰 수는 제공자의 마지막 usage 메시지에서 읽습니다.
- `agent/openai_llm.py`: 일반 대화는 완성된 문장을 한 번 보내는 대신 실제 provider 조각을 받습니다. 동일한 프롬프트·모델·온도를 유지합니다. 추가 모델 호출은 없습니다.
- `agent/graph.py`: 일반 대화는 네이티브 messages에 맡기고 기존 Ollama·fake 어댑터는 custom 경로를 유지합니다. 분류·계획의 동기 호출은 스레드로 분리합니다.
- 조사·검색·런북 읽기는 시작/완료/실패 정보를 호출 ID와 함께 즉시 보냅니다. 모니터링 완료를 모든 병렬 작업이 끝날 때까지 기다려 표시하지 않습니다. 재시도는 하나의 논리적 작업에 포함하며 완료 정보에 재시도 횟수를 붙입니다.
- `server.py`: `stream_mode=["messages", "updates", "custom"]`로 구독합니다. 일반 대화 노드의 텍스트만 답변 영역으로 전달하고, 노드 상태는 updates로 처리합니다. 호출 ID·이벤트 종류로 즉시 알림 중복을 제외합니다.
- `web_chat.html`: 도구 시작/완료/실패를 표시합니다. 이미 실시간 알림을 보낸 작업의 노드 완료 요약을 같은 진행 항목으로 다시 표시하지 않습니다.

## 기존 검수 유지

현재 서비스는 ReAct agent가 모든 도구를 직접 고르는 구조가 아닙니다. 수업 사진의 LLM tool-call 조각을 실제 MCP 실행의 증거로 간주하거나, 실행하지 않은 ToolMessage를 만들지 않습니다. 즉시 알림은 실제 작업 실행 경계에서 만듭니다.

장애 보고서의 짧은 절차·가설 선택, 측정값 복사, 조건 판정, 참조·수치·대상 검수와 실패 종료는 유지합니다. 검수 전 JSON·초안은 화면으로 보내지 않고 통과한 최종 보고서만 공개합니다. 이를 텍스트 생성 중 실시간 보고서 스트리밍이라고 설명하지 않습니다.

## 요청한 비교

새 스트리밍 전용 검증 항목·테스트·첫 토큰 시간 지표는 추가하지 않았습니다. 기존 60 Python 검사·4 화면 검사와 종전의 요청 완료·전체 응답시간·모델 호출·제공자 토큰·조회 대상·검수·이름 회상 지표를 사용합니다.

명령:

```bash
.venv-rag/bin/python tools/benchmark_stream_revision.py --revised-ref 03eefe9
.venv-rag/bin/python tools/report_stream_revision.py --traces
```

이 명령은 실제 API 사용량을 발생시킵니다. 이전 측정은 덮어쓰지 않습니다. 새 비교는 기존 과제, `1d903c6` 최적화본, `03eefe9` 스트리밍 수정본 각 33건으로 구성합니다. 기존 검색 캐시를 재사용한 수업 샘플이며, 완료율이 내용 정답률이나 실제 장애 복구 성공률을 의미하지는 않습니다.

공식 설명: https://docs.langchain.com/oss/python/langgraph/streaming#llm-tokens
