/* Shared with Node tests; rejects stale events before Vue renders them. */
(function (root) {
  // Presentation only: the API reply and full validated evidence stay unchanged.
  // Unknown formats fall back to the original text rather than lose a warning.
  function presentReport(text) {
    const intro = '실습 샘플 자료에 근거한 안내입니다. 실제 조치는 실행하지 않았습니다.';
    if (typeof text !== 'string' || !text.startsWith(intro + '\n1) 확인한 사실\n')) return null;
    const sections = text.split(/\n[123]\) (?:확인한 사실|원인 가설·권고|미확인·근거)\n/);
    if (sections.length !== 4 || !text.includes('\n2) 원인 가설·권고\n') || !text.includes('\n3) 미확인·근거\n')) return null;
    const observed = new Map();
    const statusLabels = { healthy: '정상', degraded: '저하', down: '중단', unknown: '미확인' };
    for (const line of sections[1].split('\n').filter(Boolean)) {
      if (/^- .+ 알림 인용:/.test(line)) continue; // Alert numbers are never current measurements.
      const match = line.match(/^- (.+?) (상태|CPU|메모리): (.+?) \(현재 조회\) \[근거 [^\]\n]+\]$/);
      if (!match) return null;
      const [, target, field, value] = match;
      if (!observed.has(target)) observed.set(target, { target });
      const row = observed.get(target);
      if (field === '상태') row.status = statusLabels[value] || value;
      else row[field === 'CPU' ? 'cpu' : 'memory'] = value;
    }
    const observations = Array.from(observed.values());
    if (!observations.length || observations.some(row => !row.status || !row.cpu || !row.memory)) return null;
    const sources = [];
    const warnings = [];
    for (const line of sections[3].split('\n').filter(Boolean)) {
      const source = line.match(/^- (.+): (data\/runbooks\/[^ ]+) · runbook:\/\/incident-response\/([^ ]+) · 해시 [a-f0-9]+$/);
      if (source) {
        const [, title, , id] = source;
        sources.push({ id, title: title.replace(/^Runbook:\s*/, '') });
      } else if (/^- (일부 매뉴얼|관련 매뉴얼|매뉴얼 근거가 부족한 대상:)/.test(line)) {
        warnings.push(line.slice(2));
      } else if (!/^- (근거 범위:|미확보 자료:|미확인:|추가 원인 확인)/.test(line)) {
        return null;
      }
    }
    const recommendations = [];
    const labels = { '점검 권고': '추가 점검', '조건부 권고': '조건 확인 후 검토', '런북 조건 충족에 따른 조치 검토': '조치 검토 · 미실행' };
    for (const line of sections[2].split('\n').filter(Boolean)) {
      if (!line.includes('[런북 ')) continue;
      const match = line.match(/^- (.+?) (점검 권고|조건부 권고|런북 조건 충족에 따른 조치 검토): (.+) \[런북 ([^ ]+) 절차 (\d+)\]$/);
      if (!match) return null;
      const [, target, kind, action, id, step] = match;
      const index = sources.findIndex(source => source.id === id);
      if (index < 0 || !observed.has(target)) return null;
      // Remove the technical function name, but retain the whole condition suffix.
      const readable = action.replace(/get_monitoring_status(?:로|와) /g, '').replace(/한다\./g, '합니다.');
      recommendations.push({ target, kind: labels[kind], text: readable, source: index + 1, step });
    }
    return { observations, recommendations, sources, warnings,
      limitation: '원인은 아직 확인되지 않았습니다. 시계열·로그·배포 이력 등 추가 자료가 필요합니다.',
      disclaimer: '실습 샘플 · 실제 조치 미실행' };
  }

  const api = {
    presentReport,
    accepts(event, requestId, activeRequestId) {
      return requestId === activeRequestId && event.request_id === requestId;
    },
    newReply() { return {text:'', attempt:null, completed:false}; },
    reduceReply(state, event) {
      if (state.completed) return state;
      if (event.type === 'ValidationFailed' || event.type === 'ReviewerFailed') return {...state,text:event.reply,completed:true};
      if (event.type === 'FinalAnswer') return {...state, text:event.reply, completed:true};
      if (event.type !== 'AnswerToken') return state;
      const attempt = event.attempt ?? 0;
      const text = state.attempt === attempt ? state.text : '';
      return {text:text + (event.delta || ''), attempt, completed:false};
    }
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.RequestUI = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
