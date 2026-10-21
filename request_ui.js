/* Shared with Node tests; rejects stale events before Vue renders them. */
(function (root) {
  const api = {
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
