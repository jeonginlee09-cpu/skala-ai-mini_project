# 기존 과제와 최종 개선본의 기능 대조

원본 `0d8405e`와 최종 제품 `69d58eb`의 실제 소스를 대조했습니다. 실행 성능 수치는 [실측 보고서](final_comparison.md)에 따로 보관합니다. 아래 표는 구현 차이이며 정답률 개선의 실측값이 아닙니다.

| 기능 | 기존 과제 | 최종 개선본의 실제 차이 | 분류 |
|---|---|---|---|
| FastAPI·lifespan | 서버 수명 동안 의존성·모델·MCP·그래프를 준비했습니다. | 기존 수명 관리 안에서 검색기·Reviewer도 준비합니다. | 기존 유지 |
| MCP 별도 프로세스·서버 탐색 | stdio MCP 서버의 Tool·Resource·Prompt와 서버 탐색을 사용했습니다. | 같은 MCP 클라이언트로 지표·원문·절차를 가져옵니다. | 기존 유지 |
| 의존성 주입(DI) | SupervisorDeps를 노드에 전달했습니다. | 검색기와 Reviewer 필드를 추가해 같은 주입 방식을 사용합니다. | 기존 유지 |
| LangSmith 추적 | 서버·그래프·모델·재시도에 traceable이 있었습니다. | 추가 노드도 추적하고 실제 Run·제공자 usage를 대조합니다. | 기존 유지 |
| 일반 대화·운영 라우팅 | 인사·잡담은 일반 대화 경로로 보냈습니다. | 기존 라우팅을 유지하고 대상이 연결된 운영 후속 질문은 조회 경로로 보냅니다. | 기존 기능 강화 |
| 복수 서비스 계획·대상 정규화 | 서비스마다 작업을 만들고 중복·잘못된 이름을 보정했습니다. 연구 작업이 0개일 때는 질문의 대상을 복구했습니다. | 요청 대상 전체와 계획을 대조해 일부 누락·중복·범위 밖 조회를 보정합니다. | 기존 기능 강화 |
| 병렬 조회·일시 오류 재시도 | asyncio.gather 병렬 실행과 TimeoutError의 최대 3회 시도가 있었습니다. | 이 방식을 유지하며 조회값의 대상·유효성을 추가 검사합니다. | 기존 유지 |
| 재계획·조사 충분성 | 런북이 없으면 추가 조회 작업을 만들고 재계획 횟수를 제한했습니다. | 요청 서비스별로 유효한 지표를 얻었는지 검사하고 누락 대상만 한 차례 보충합니다. | 기존 기능 강화 |
| 런북 기반 답변 | 런북을 MCP로 읽고 모니터링·분석 결과와 함께 Writer에게 전달했습니다. | 검색해 실제 읽은 런북의 ID·원문 해시·조건표와 권고를 연결합니다. | 기존 기능 강화 |
| 런북 RAG·Chroma | 알림의 첫 일치 키워드로 장애 유형 하나를 고르며 미일치 시 db-down으로 대체했습니다. | 질문·서비스별 알림을 임베딩·Chroma·관련성 판정으로 검색하고 인용을 대조합니다. | 새 기능 추가 |
| Writer와 코드의 역할 | AI 계획·작성과 Python 게이트가 이미 역할을 나눴으며 Writer는 자유로운 문장을 생성했습니다. | Writer는 절차·가설 ID를 고르고 Python이 사실·수치·조건·출처·제안 상태를 구성합니다. | 기존 기능 강화 |
| 답변 Validator | 30자 이상인지와 1) 포함 여부를 검사했습니다. | 근거 ID·대상·수치·서비스별 사실 누락·절차·조건을 검사합니다. | 기존 기능 강화 |
| 독립 Reviewer 역할 | 계획·조사·분석·작성 역할은 있었지만 별도 내용 검토 노드는 없었습니다. | 코드 검수 후 복잡한 요청에 별도 지시문·출력 계약의 Reviewer를 적용합니다. | 새 기능 추가 |
| 반복 제한·실패 정책 | Writer 최대 3회 제한이 있었으며 세 번째 실패는 강제 합격했습니다. | Writer 최초 포함 최대 3회·Reviewer 최대 2회이며 계속 실패하면 답변을 보류합니다. | 기존 기능 강화 |
| 대화 저장·요청별 State | SQLite·thread_id로 대화를 저장하고 요청마다 그래프 입력을 새로 만들었습니다. | 이를 유지하고 대화 쌍 저장을 원자화하며 같은 대화 요청을 순차 처리합니다. | 기존 기능 강화 |
| 운영 후속 질문의 대상 연결 | history를 일반 대화에 주며 운영 라우팅·계획에는 현재 질문을 사용했습니다. | 최근 사용자 메시지의 대상 이름만 연결하고 모호하면 되묻고 현재 지표를 새로 조회합니다. | 새 기능 추가 |
| 화면의 답변·요청 구분 | 시도 번호로 재작성 말풍선을 나누고 FinalAnswer로 최종 문자열을 맞췄습니다. | request_id로 늦은 이벤트를 거르고 실패를 종료 상태로 처리하며 검수 전 장애 초안을 공개하지 않습니다. | 기존 기능 강화 |
| SSE 스트리밍 | SSE와 updates·custom을 사용했습니다. 원본 OpenAI chat은 완성 문자열을 한 번 전달했습니다. | 일반 대화의 실제 조각을 messages로 받고 노드 완료 updates·직접 MCP 진행 custom을 구분합니다. | 기존 기능 강화 |
| 비교 분석 처리 | 별도 모델이 수집 결과의 패턴을 요약했습니다. | 검증된 현재값을 코드로 비교하고 시계열·배포·호출 관계가 없어 원인을 확정하지 않습니다. | 처리 방식 변경 |

## 근거 위치와 설명할 때 주의할 점

### A01 FastAPI·lifespan

서버·싱글턴을 새로 도입한 것이 아니라 새 의존성을 기존 초기화에 연결했습니다.

- 원본: [server.py:63](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/server.py#L63)
- 최종: [server.py:100](../server.py#L100)

### A02 MCP 별도 프로세스·서버 탐색

MCP 신규 분리 과제가 아닙니다. agent/mcp_client.py는 원본과 같습니다.

- 원본: [agent/mcp_client.py:44](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/mcp_client.py#L44)
- 최종: [agent/mcp_client.py:44](../agent/mcp_client.py#L44)

### A03 의존성 주입(DI)

DI를 처음 만든 것이 아니라 기존 계약을 확장했습니다.

- 원본: [agent/deps.py:12](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/deps.py#L12)
- 최종: [agent/deps.py:18](../agent/deps.py#L18)

### A04 LangSmith 추적

기존 관측 도구를 활용해 비교 증빙을 확보했습니다.

- 원본: [agent/graph.py:75](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L75)
- 최종: [agent/graph.py:429](../agent/graph.py#L429)

### A05 일반 대화·운영 라우팅

대화 분류 자체가 새 기능은 아닙니다.

- 원본: [agent/graph.py:76](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L76)
- 최종: [agent/graph.py:140](../agent/graph.py#L140)

### A06 복수 서비스 계획·대상 정규화

복수 서비스가 처음 가능해진 것이 아니라 부분 누락까지 코드로 보완합니다.

- 원본: [agent/openai_llm.py:168](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/openai_llm.py#L168), [agent/openai_llm.py:157](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/openai_llm.py#L157)
- 최종: [agent/target_scope.py:12](../agent/target_scope.py#L12)
- 한계: 명시하지 않은 의존 서비스의 자동 발견은 구현하지 않았습니다.

### A07 병렬 조회·일시 오류 재시도

병렬 처리·재시도 자체를 신규 기능으로 소개하지 않습니다.

- 원본: [agent/graph.py:172](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L172), [agent/retry.py:11](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/retry.py#L11)
- 최종: [agent/graph.py:260](../agent/graph.py#L260), [agent/retry.py:11](../agent/retry.py#L11)

### A08 재계획·조사 충분성

재계획을 새로 만든 것이 아니라 충분성 기준을 대상·지표까지 확장했습니다.

- 원본: [agent/graph.py:217](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L217)
- 최종: [agent/graph.py:283](../agent/graph.py#L283), [agent/target_scope.py:26](../agent/target_scope.py#L26)
- 한계: 끝내 지표를 못 얻으면 미확인·부분 결과로 처리합니다.

### A09 런북 기반 답변

근거가 전혀 없던 답변에 런북을 처음 붙인 것이 아닙니다.

- 원본: [agent/graph.py:255](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L255), [agent/openai_llm.py:212](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/openai_llm.py#L212)
- 최종: [agent/graph.py:336](../agent/graph.py#L336), [agent/validator.py:26](../agent/validator.py#L26)

### A10 런북 RAG·Chroma

키워드 유형 선택에서 복합 증상에 맞는 복수 근거 검색으로 변경했습니다.

- 원본: [agent/graph.py:205](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L205)
- 최종: [agent/runbook_retriever.py:75](../agent/runbook_retriever.py#L75), [agent/relevance.py:18](../agent/relevance.py#L18)
- 한계: 현재 여섯 문서 전체가 관련성 후보입니다. 대규모 Vector DB 우위를 입증한 실험은 아닙니다.

### A11 Writer와 코드의 역할

AI와 코드의 분리를 처음 만든 것이 아니라 생성 책임을 더 좁혔습니다.

- 원본: [agent/graph.py:123](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L123), [agent/graph.py:263](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L263)
- 최종: [agent/report_selection.py:9](../agent/report_selection.py#L9), [agent/report_selection.py:64](../agent/report_selection.py#L64)
- 한계: 이번 검증의 ID 선택 방식은 OpenAI 실행 경로 기준입니다.

### A12 답변 Validator

기존 형식 검사를 근거·조건 검수로 강화했습니다.

- 원본: [agent/graph.py:279](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L279)
- 최종: [agent/validator.py:54](../agent/validator.py#L54)
- 한계: 등록된 참조·조건의 검사이며 모든 자연어 의미를 증명하지는 않습니다.

### A13 독립 Reviewer 역할

전체 Multi-Agent 기반 신규 도입보다 작성·검토 역할 분리 추가라고 설명합니다.

- 원본: [agent/graph.py:310](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L310)
- 최종: [agent/semantic_review.py:25](../agent/semantic_review.py#L25), [agent/openai_llm.py:225](../agent/openai_llm.py#L225)
- 한계: 같은 모델의 판단 오류가 남으며 사람 승인이 아닙니다.

### A14 반복 제한·실패 정책

무한 루프 방지 신규 도입이 아니라 한도 도달 후 처리 정책과 검토 상한을 변경했습니다.

- 원본: [agent/graph.py:281](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L281)
- 최종: [agent/graph.py:35](../agent/graph.py#L35), [agent/graph.py:500](../agent/graph.py#L500)

### A15 대화 저장·요청별 State

기억 기능의 첫 구현이 아니라 저장·동시 요청·작업 자료 경계를 보완했습니다.

- 원본: [server.py:128](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/server.py#L128), [agent/session_store.py:29](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/session_store.py#L29)
- 최종: [agent/session_store.py:39](../agent/session_store.py#L39), [server.py:194](../server.py#L194)

### A16 운영 후속 질문의 대상 연결

SQLite 기억과 별도로 운영 조사에 필요한 지시 대상 처리를 추가했습니다.

- 원본: [agent/graph.py:94](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L94), [agent/graph.py:78](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L78)
- 최종: [agent/request_context.py:13](../agent/request_context.py#L13), [agent/graph.py:114](../agent/graph.py#L114)
- 한계: 등록한 한국어 표현과 최근 10개 메시지 범위이며 범용 대명사 해석은 아닙니다.

### A17 화면의 답변·요청 구분

원본에도 최종 답변 표시가 있었습니다. 요청 간 혼입과 공개 시점을 보완했습니다.

- 원본: [web_chat.html:182](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/web_chat.html#L182)
- 최종: [request_ui.js:4](../request_ui.js#L4), [agent/graph.py:389](../agent/graph.py#L389)
- 한계: 과거 혼입 현상의 원인을 State 한 곳으로 확정하지 않습니다.

### A18 SSE 스트리밍

SSE 신규 도입이 아니라 수신 종류와 실제 OpenAI 조각 중계를 보완했습니다.

- 원본: [server.py:173](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/server.py#L173), [agent/openai_llm.py:194](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/openai_llm.py#L194)
- 최종: [agent/openai_stream.py:49](../agent/openai_stream.py#L49), [server.py:248](../server.py#L248)
- 한계: 스트리밍 전용 새 검사·첫 토큰 지표는 추가하지 않았습니다.

### A19 비교 분석 처리

모델 추정 요약을 현재값 비교와 한계 표시로 제한했습니다.

- 원본: [agent/graph.py:243](https://github.com/jeonginlee09-cpu/skala-ai-mini_project/blob/0d8405e060188e7bd88a4c93a101ba4b40c1beb9/agent/graph.py#L243)
- 최종: [agent/graph.py:381](../agent/graph.py#L381)
- 한계: 정교한 근본 원인 분석 성능 향상을 입증한 변경은 아닙니다.

## 읽는 기준

원본 소스 링크는 저장소의 원본 커밋을 가리킵니다. 로컬 원본 백업도 그대로 보존했습니다. [기계 확인용 근거](evidence/original_final_feature_audit.json)에는 실제 코드 구절·줄 번호·파일 해시를 함께 저장했습니다.

이번 작업은 설명자료 수정입니다. 운영 코드는 바꾸지 않았고 새 API 실험도 실행하지 않았습니다. 최종 제품 소스와 동일한 기존 실측만 사용합니다.
