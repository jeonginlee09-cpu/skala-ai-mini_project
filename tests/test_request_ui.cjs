const test = require('node:test');
const assert = require('node:assert/strict');
const ui = require('../request_ui.js');

test('events from another request or closed request are rejected', () => {
  assert.equal(ui.accepts({request_id:'old'}, 'new', 'new'), false);
  assert.equal(ui.accepts({request_id:'new'}, 'new', 'next'), false);
  assert.equal(ui.accepts({request_id:'new'}, 'new', 'new'), true);
});
test('final text replaces partial tokens and duplicate final cannot change it', () => {
  let state=ui.newReply();
  state=ui.reduceReply(state,{type:'AnswerToken',delta:'초안',attempt:1});
  state=ui.reduceReply(state,{type:'FinalAnswer',reply:'확정 답변'});
  assert.equal(state.text,'확정 답변');
  assert.equal(ui.reduceReply(state,{type:'FinalAnswer',reply:'다른 답변'}).text,'확정 답변');
});
test('rewrite clears earlier attempt text instead of appending it', () => {
  let state=ui.reduceReply(ui.newReply(),{type:'AnswerToken',delta:'첫 초안',attempt:1});
  state=ui.reduceReply(state,{type:'AnswerToken',delta:'새 초안',attempt:2});
  assert.equal(state.text,'새 초안');
});
