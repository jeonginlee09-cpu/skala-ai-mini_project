const test = require('node:test');
const assert = require('node:assert/strict');
const ui = require('../request_ui.js');

// Controlled display fixtures; these are not measured API results.
const facts = (target, cpu, memory) => [
  `- ${target} 상태: degraded (현재 조회) [근거 request-uuid:observation:${target}:status]`,
  `- ${target} CPU: ${cpu}% (현재 조회) [근거 request-uuid:observation:${target}:cpu_percent]`,
  `- ${target} 메모리: ${memory}% (현재 조회) [근거 request-uuid:observation:${target}:memory_percent]`,
];
const source = '- Runbook: CPU 급증 대응 절차: data/runbooks/cpu-spike.md · runbook://incident-response/cpu-spike · 해시 30796f7b5c1d';
function fixture({ targets = [['payments-service', '95.0', '57.0']], actions, warning = '' } = {}) {
  return [
    '실습 샘플 자료에 근거한 안내입니다. 실제 조치는 실행하지 않았습니다.',
    '1) 확인한 사실', ...targets.flatMap(([name, cpu, memory]) => facts(name, cpu, memory)),
    '- payments-service 알림 인용: CPU 91% (threshold 80%) (발생 시각 미확인) [근거 request-uuid:alert:0]',
    '2) 원인 가설·권고',
    '- payments-service 점검 가설: 최근 배포의 영향. 런북이 제시하는 가능성이며 원인 확인은 필요합니다.',
    ...(actions || ['- payments-service 점검 권고: get_monitoring_status로 CPU 사용률과 최근 배포/트래픽 변화 시점을 확인한다. [런북 cpu-spike 절차 1]']),
    '3) 미확인·근거',
    '- 미확보 자료: 시계열 지표·상세 로그·배포 이력·서비스 호출 관계입니다. 실제 원인과 추세는 확정하지 않았습니다.',
    warning, source,
  ].filter(Boolean).join('\n');
}

test('compact view keeps current values separate from fixed alert values', () => {
  const full = fixture();
  const shown = ui.presentReport(full);
  assert.deepEqual(shown.observations, [
    { target: 'payments-service', status: '저하', cpu: '95.0%', memory: '57.0%' },
  ]);
  assert.equal(shown.sources[0].title, 'CPU 급증 대응 절차');
  assert.equal(shown.recommendations[0].source, 1);
  assert.match(shown.recommendations[0].text, /CPU 사용률/);
  assert.doesNotMatch(JSON.stringify(shown), /request-uuid|observation:|CPU 91%/);
  assert.match(full, /CPU 91%/); // Full evidence remains available to the disclosure.
});

test('conditional recommendations retain every condition and unknown verdict', () => {
  for (const verdict of ['unknown', 'not_met']) {
    const condition = `(조건: CPU 사용률 90% 초과 / CPU가 높은 상태로 지속되는지; 판정=${verdict})`;
    const shown = ui.presentReport(fixture({ actions: [
      `- payments-service 조건부 권고: CPU가 90%를 넘고 지속되면 부하를 분산한다. ${condition} [런북 cpu-spike 절차 3]`,
    ] }));
    assert.equal(shown.recommendations[0].kind, '조건 확인 후 검토');
    assert.ok(shown.recommendations[0].text.includes(condition));
    assert.match(shown.disclaimer, /미실행/);
  }
});

test('compact view retains all requested services and all selected checks', () => {
  const targets = [['payments-service', '95.0', '57.0'], ['cache-service', '42.0', '89.0']];
  const actions = targets.flatMap(([target]) => [1, 2, 3, 4].map(n =>
    `- ${target} 점검 권고: 점검 항목 ${n}을 확인한다. [런북 cpu-spike 절차 ${n}]`));
  const shown = ui.presentReport(fixture({ targets, actions }));
  assert.deepEqual(shown.observations.map(row => row.target), targets.map(row => row[0]));
  assert.equal(shown.recommendations.length, 8); // No arbitrary truncation drops checks/targets.
});

test('missing or partial manual warnings remain visible outside the full report', () => {
  const warning = '- 일부 매뉴얼 원문을 확보하지 못했습니다. 확보한 자료 범위에서만 안내합니다.';
  assert.ok(ui.presentReport(fixture({ warning })).warnings.includes(warning.slice(2)));
});

test('ordinary chat, failure notices and incomplete report formats use original text', () => {
  for (const value of ['안녕하세요!', '검증을 통과하지 못해 답변을 보류합니다.', fixture().replace('3) 미확인·근거', '다른 형식'), null]) {
    assert.equal(ui.presentReport(value), null);
  }
});

test('unrecognized facts or action formats fall back instead of silently dropping content', () => {
  assert.equal(ui.presentReport(fixture().replace('payments-service CPU:', 'payments-service 디스크:')), null);
  assert.equal(ui.presentReport(fixture().replace('점검 권고:', '자동 실행:')), null);
});
