# 기존 과제와 최종 수정본의 코드 설명

최종 제품 소스는 `69d58eb`입니다. 원본 과제 백업과 실제 호출 경로를 확인해, 이미 있던 기능과 새로 강화한 기능을 구분했습니다. [새 실측](final_comparison.md)과 [평가표 점검](final_review.md)을 함께 읽으면 됩니다.

## 1. 원본도 런북을 보고 답변했습니다

원본의 흐름은 다음과 같습니다.

```text
질문 → route → plan → gate → research → replan_check
     → analysis → write → validate → finalize
```

- `기존과제자료_백업_20261020/agent/graph.py`의 `plan_node()`는 모델에게 작업 계획을 받습니다. `gate_node()`는 필요하지 않은 analysis 작업을 코드로 제거합니다. AI 판단과 코드의 역할 분리가 이미 있었습니다.
- `research_node()`는 MCP 모니터링을 호출하고, `_classify_incident_type()`의 첫 키워드 일치 유형으로 런북을 읽습니다. `write_node()`는 **모니터링·런북·분석 결과를 모델에게 주고** 자유로운 답변을 받습니다. 원본이 런북 근거 없이 답했다는 설명은 틀립니다.
- 원본 `server.py`는 `thread_id`로 SQLite 대화를 읽고, 각 요청의 작업 자료를 새로 만듭니다. 다만 `history`를 주로 일반 대화에 주며 운영 라우팅·계획에는 이전 대상의 연결을 명시적으로 제공하지 않습니다.
- 원본 `validate_node()`는 글자 수와 `1)`을 검사합니다. 세 번째 실패에도 합격으로 바꿉니다. 무한 반복을 막는 횟수 제한 자체는 이미 있었습니다.

**발표 표현:** “기존 구조를 교체한 것이 아니라, 요청 대상·검색 근거·답변 검수를 구체적인 계약으로 강화했습니다.”

## 2. 후속 질문은 대상 이름만 연결하고 새로 조회합니다

핵심 함수는 [resolve_followup()](../agent/request_context.py#L13)입니다. Python 함수이며 그래프의 `resolve_context` 노드가 이를 사용합니다.

| 단계 | 하는 일 | 그렇게 하는 이유 |
|---|---|---|
| 현재 질문 확인 | 새 서비스명이 있으면 우선합니다. | 이전 대화의 대상을 새 질문에 덮어쓰지 않기 위해서입니다. |
| 지시 표현 확인 | “그 서비스”, “그 두 서비스”와 점검·비교 같은 운영 표현을 찾습니다. | 이름 등록 같은 일반 대화를 조사 경로로 보내지 않기 위해서입니다. |
| 이전 사용자 메시지 확인 | 최근 10개 메시지 중 사용자가 명시한 대상 이름을 찾습니다. | AI의 이전 답변이 잘못 언급한 이름을 새 사실로 사용하지 않기 위해서입니다. |
| 모호함 확인 | 이전 대상이 둘인데 “그 서비스”라고 하면 이름을 되묻습니다. | 임의로 하나를 고르거나 auth-service로 대체하지 않기 위해서입니다. |
| 작업 질문 구성 | `effective_query`에 대상 이름과 이번 질문만 넣습니다. | 이전 CPU·메모리 수치를 현재값으로 재사용하지 않기 위해서입니다. |
| 경로 보장 | 연결된 운영 후속 질문은 `incident`로 보냅니다. | 실제 검사에서 모델이 일반 대화로 분류해 새 조회를 생략했기 때문입니다. |

실제 보완한 위치는 [route_node()](../agent/graph.py#L127)입니다. 모델의 분류 결과가 달랐으면 `model_route`와 `override_reason`을 이벤트에 남깁니다. 모델 분류를 숨기지 않습니다. 이 가드는 **대상 연결을 끝낸 운영 후속 질문**에만 적용합니다.

```python
resolved = state.get('context_resolution', {}).get('status') == 'resolved'
if resolved:
    result['route'] = 'incident'
```

원문 질문은 `query`, 작업용 질문은 `effective_query`로 구분합니다. 후자는 route·plan·RAG·Writer·Reviewer에 전달하며, 서버가 저장하는 사용자 메시지는 원문을 유지합니다. 모호하면 `clarification_required`로 답한 뒤 종료하고 다음 사용자 입력을 기다립니다. 체크포인터로 중단 그래프를 복원하는 구현은 아닙니다.

**한계:** 몇 가지 한국어 지시 표현과 알려진 서비스 이름을 처리하는 제한된 규칙입니다. 장거리 문맥과 모든 대명사 표현을 이해한다고 주장하지 않습니다. 원본의 SQLite 저장을 LangGraph Checkpointer로 교체하지도 않았습니다.

## 3. 런북은 관련성으로 고르고 두 개에서 자르지 않습니다

[RunbookRetriever.search()](../agent/runbook_retriever.py#L75)는 서비스별 알림과 전체 질문을 각각 검색합니다. Chroma가 여섯 문서를 순위대로 주면 관련성 모델이 직접 관련된 문서를 선택합니다. Python은 선택 ID와 실제 질문·문서 인용의 존재를 검사합니다.

**직전 버전의 문제:** 전체 결과를 최대 두 문서로 잘랐습니다. CPU·메모리·디스크가 서로 다른 세 서비스에 있으면 세 번째 근거가 사라질 수 있었습니다.

**최종 변경:** 기본 `max_results=None`으로 두 문서 제한을 제거했고, 현재 전체 여섯 문서를 관련성 후보로 줍니다. `(query_id, runbook_id)` 중복만 제거하고, 같은 문서의 여러 대상은 합칩니다. 그래프도 `hits[:2]`가 아니라 반환된 전체 hits를 읽습니다. 관련 없는 문서로 개수를 채우지 않습니다.

[OpenAIRelevanceFilter](../agent/relevance.py#L18)는 독립된 관련성 프롬프트를 사용합니다. 캐시 버전을 `strict-topic-v3-coverage`로 바꾸어 예전 두 문서 선택을 그대로 재사용하지 않습니다. 예전 캐시와 실측은 삭제하지 않습니다.

최종 원문은 MCP Resource로 읽고 등록 ID·내용 해시를 확인합니다. 조건표의 해시도 원문과 맞아야 합니다. 검색은 최초 포함 최대 두 번이며 결과 없음·검색 오류·원문 실패를 성공 근거로 바꾸지 않습니다.

**한계:** 여섯 짧은 문서라는 현재 범위에 맞춘 방식입니다. 문서가 크게 늘면 전체 후보 입력·검토 비용이 커집니다. 검색 대상별 후보 축소, 토큰 예산, 부분 근거 응답 정책을 추가해야 합니다. 해시 일치는 문서의 옳음이나 최신성 보장이 아닙니다.

## 4. AI가 선택하고 코드가 검사하는 범위를 분명히 했습니다

| 판단·처리 | 담당 | 핵심 파일 |
|---|---|---|
| 일반 대화·운영 요청 분류, 초기 계획 | 모델 | `openai_llm.py`, `graph.py` |
| 명시된 대상·모호성·누락·유효한 조회값 검사 | Python | `request_context.py`, `target_scope.py` |
| 질문과 관련된 런북 선택 | 모델 + 인용 검사 코드 | `relevance.py`, `runbook_retriever.py` |
| 절차·가설 ID 선택 | Writer 모델 | `report_selection.py`, `openai_llm.py` |
| 사실·조건·출처 조립 | Python | `report_selection.py`, `grounded_report.py` |
| 참조 ID·수치·서비스·적용 조건 검수 | Python Validator | `validator.py` |
| 복잡한 질문의 누락·관련성·표현 검토 | 별도 Reviewer 역할 | `semantic_review.py`, `graph.py` |

[ReportSelection](../agent/report_selection.py#L9)은 `action_ids`, `hypothesis_ids` 두 필드만 허용합니다. 모델은 이번 요청에 주어진 A번호·H번호만 고를 수 있습니다. `expand_selection()`은 없는 ID를 거부하고 실제 조회값으로 사실을 구성합니다.

현재 측정 필드와 발생 시각 미확인 알림을 구분합니다. 원인 가설은 가능성으로 표시하고 조치 실행 여부는 `proposed`입니다. CPU가 한 번 높았다는 값으로 “높은 상태가 지속됨”을 충족시킬 수 없습니다. 미충족·확인불가 조건이 있으면 조건부 권고로 제한합니다.

[validate_report()](../agent/validator.py#L54)는 요청 서비스의 사실 누락, 임의 ID, 수치 불일치, 근거 없는 조치, 조건 누락을 검사합니다. 복잡한 요청은 [review_node()](../agent/graph.py#L430)의 별도 Reviewer가 읽습니다. 같은 모델 계열을 사용하더라도 작성·검토는 별도 지시문·출력 계약·검토 단계입니다. 모든 노드가 독립 Agent인 것은 아닙니다.

**한계:** Reviewer는 추가 측정 도구를 호출하지 않습니다. 같은 모델의 편향이 남을 수 있고 등록 조건 밖의 모든 자연어 주장을 증명하는 검사기도 아닙니다. AI 승인과 사람 승인은 다릅니다.

## 5. 반복 제한과 기억은 기존 기능에서 무엇이 달라졌나요?

| 항목 | 기존 과제 | 최종 수정본 |
|---|---|---|
| 대화 저장 | thread_id + SQLite | 유지 |
| 요청별 작업 자료 | 새 State로 시작 | 유지하고 추가 상태까지 명시적으로 초기화 |
| 이전 대상 연결 | 일반 대화의 이력과 운영 계획이 분리 | 제한된 지시 대상을 작업 질문에 연결 |
| 답변 반복 | 최대 3회 뒤 강제 통과 | Writer 최대 3회 뒤 검수 실패 보류 |
| 내용 검토 | 단순 형식 검사 | 코드 검수 후 복잡한 요청에 Reviewer 최대 2회 |
| 모니터링 보충 | 기존 재계획·재시도 | 요청 대상의 유효한 조회 결과를 대조해 누락만 제한적 보충 |
| 검색 반복 | 키워드 유형 선택 | 검색 최초 포함 최대 2회 |

첫 Reviewer 반려는 Writer로 돌아가고, 수정 답변도 Validator를 다시 받습니다. 재반려·오류·Writer 상한이면 `review_failed` 또는 `validation_failed`로 끝납니다. 기본 LangGraph recursion limit까지 무한 반복하도록 두지 않습니다.

## 6. 불필요한 코드 정리와 보존한 자료

- 운영 그래프에서 호출되지 않는 `_extract_system_name()`을 삭제했습니다.
- 현재 RAG 경로에서 쓰지 않는 옛 키워드 분류기를 `tools/baseline_runbook_selection.py`로 옮겼습니다. 기존 방식과의 검색 비교에는 필요하므로 통째로 지우지 않았습니다.
- 원본 백업, 이전 실험, fake/Ollama 어댑터와 v1 토글 경로는 원래 기능·비교·호환성을 위해 보존했습니다. 사용하지 않는 것처럼 보인다는 이유만으로 공개 어댑터 계약을 삭제하지 않았습니다.
- `doc/`의 예전 비교는 당시 버전 표시를 유지하고 현재 README에서는 최종 자료를 먼저 연결했습니다.

## 7. 수업자료에서 확인한 근거와 프로젝트 선택

아래 번호는 PDF 뷰어 기준입니다. Capstone PDF 하단에 인쇄한 번호는 뷰어 번호보다 1 작을 수 있습니다.

| 적용 내용 | 수업자료 근거 | 프로젝트에서 직접 정한 부분 |
|---|---|---|
| 역할을 나눠 작성·검토 | AI Agent 설계 및 구축 p.108, 116~118 | 복잡한 요청만 Reviewer를 호출하는 조건 |
| StateGraph와 상태 전달 | AI Agent 설계 및 구축 p.96 / Capstone p.127 | `effective_query`·대상 연결·모호성 가드 |
| 런북 검색·Chroma | AI Agent 설계 및 구축 p.41, 74~75 | 현재 여섯 문서 전체 후보·인용 대조·두 문서 제한 제거 |
| 코드가 불필요한 작업을 차단하는 게이트 | Capstone p.148 | 모든 요청 서비스의 조회 범위를 강제하는 규칙 |
| 재계획·답변 검수·반복 방지 | Capstone p.155, 165, 167, 170 | Writer 3회·Reviewer 2회·계속 실패하면 보류 |
| 대화 지속성 | Capstone p.209 | 현재는 기존 SQLite 저장 + 사용자 이름 규칙을 유지 |
| DI·모듈·테스트 | Capstone p.217, 219, 221 | `SupervisorDeps`와 통제 모델·MCP를 이용한 실패 조건 검사 |
| 추적·동일 입력 비교 | Capstone p.238~239, 245 | 고정 샘플, 소스 해시, 준비 비용 별도 집계 |

교재가 `action_ids` 출력, 특정 정규식, 정확히 3회·2회 조합, 현재 응답 문장까지 제시했다고 주장하지 않습니다. 개념적 근거와 프로젝트 구현 선택을 구분합니다.

참고 파일:
- `AI_Agent_설계및구축.pdf`: `/Users/ijeong-in/Desktop/SKALA/reference/1012-13_AI_Agent/AI_Agent_설계및구축.pdf`
- `AI캠퍼스_AI_Capstone_20261016.pdf`: `/Users/ijeong-in/Desktop/SKALA/reference/1016_AI_Capstone/AI캠퍼스_AI_Capstone_20261016.pdf`
