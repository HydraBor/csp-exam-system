/* Shared, DOM-free filtering/ranking rules for the leaderboard and regression checks. */
(function (root) {
  'use strict';
  function matches(value, selected) {
    return !selected || (selected === '~' ? !value : selected === '=' + (value || ''));
  }
  function rows(state, filters, privateView) {
    const term = (filters.search || '').trim().toLocaleLowerCase();
    const selected = (state.rows || []).filter(r =>
      matches(r.campus, filters.campus) && matches(r.teacher, filters.teacher) &&
      (r.name + ' ' + (r.campus || '') + ' ' + (r.teacher || '') + ' ' +
        (privateView ? r.kaohao || '' : '')).toLocaleLowerCase().includes(term));
    let position = 0, rank = null, last = null;
    return selected.map(r => {
      if (r.rank == null) return {...r, filtered_rank: null};
      position += 1;
      if (last !== r.total) { rank = position; last = r.total; }
      return {...r, filtered_rank: rank};
    });
  }
  function options(state, field, campus) {
    return [...new Set((state.rows || []).filter(r => field !== 'teacher' || matches(r.campus, campus))
      .map(r => r[field] || ''))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  }
  const api = {matches, rows, options};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CSPBoardFilters = api;
})(typeof window !== 'undefined' ? window : globalThis);
