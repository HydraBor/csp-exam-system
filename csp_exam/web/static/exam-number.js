(function () {
  'use strict';
  const form = document.getElementById('number-search-form');
  if (!form) return;
  const name = document.getElementById('number-name');
  const campus = document.getElementById('number-campus');
  const teacher = document.getElementById('number-teacher');
  const message = document.getElementById('number-message');
  const section = document.getElementById('number-results');
  const list = document.getElementById('number-result-list');
  const button = document.getElementById('number-search-button');
  let searchedName = '', sequence = 0, pending = null;
  function clearResults() { section.hidden = true; list.replaceChildren(); }
  function options(select, values, prompt, previous) {
    select.replaceChildren(new Option(prompt, ''));
    for (const value of values) select.add(new Option(value || '未填写', JSON.stringify(value)));
    select.disabled = !values.length;
    if (previous !== '' && values.some(v => JSON.stringify(v) === previous)) select.value = previous;
  }
  function text(tag, className, value) {
    const el = document.createElement(tag); el.className = className; el.textContent = value; return el;
  }
  function date(value) {
    if (!value) return '待设置';
    return new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value * 1000));
  }
  function results(rows) {
    clearResults(); section.hidden = false;
    document.getElementById('number-result-count').textContent = rows.length + ' 场考试';
    for (const row of rows) {
      const card = text('article', 'card card-body p-4', '');
      const head = text('div', 'd-flex flex-wrap justify-content-between align-items-start gap-2 mb-3', '');
      head.append(text('h3', 'h3 mb-0', row.title));
      head.append(text('span', 'badge ' + (row.status === 'running' ? 'bg-green-lt' : row.status === 'pending' ? 'bg-blue-lt' : 'bg-secondary-lt'), row.status_label));
      card.append(head);
      const times = text('div', 'text-secondary small mb-3', '');
      times.append(text('div', 'mb-1', (row.exam_mode === 'flexible' ? '可开始考试：' : '考试时间：') + date(row.start_at) + ' 至 ' + date(row.end_at)));
      times.append(text('div', '', (row.exam_mode === 'flexible' ? '个人时长：' : '考试时长：') + row.duration_minutes + ' 分钟 · 北京时间'));
      card.append(times);
      const footer = text('div', 'd-flex flex-wrap justify-content-between align-items-center gap-3', '');
      const number = text('div', 'd-flex flex-wrap align-items-center gap-2', '');
      number.append(text('span', 'text-secondary', '考号'));
      number.append(text('strong', 'font-monospace fs-3', row.kaohao));
      const copy = text('button', 'btn btn-sm btn-outline-secondary', '复制'); copy.type = 'button';
      copy.addEventListener('click', async function () {
        try { await navigator.clipboard.writeText(row.kaohao); copy.textContent = '已复制'; }
        catch (_) { message.textContent = '复制未成功，请选中考号手动复制。'; }
      });
      number.append(copy);
      const entry = text('a', 'btn btn-primary btn-sm', row.status === 'ended' ? '查看本场考试' : '前往考试登录');
      entry.href = row.entry_url;
      footer.append(number, entry); card.append(footer); list.append(card);
    }
  }
  async function query(filters) {
    const id = ++sequence;
    if (pending) pending.abort();
    pending = new AbortController();
    clearResults(); message.textContent = '正在查询…'; button.disabled = true;
    try {
      const response = await fetch('/api/exam-numbers', {method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',signal:pending.signal,body:JSON.stringify({name:searchedName,...filters})});
      const data = await response.json();
      if (id !== sequence) return;
      if (!response.ok || !data.ok) throw new Error(data.error || '查询暂时不可用，请稍后重试。');
      options(campus, data.campuses, '请选择校区', campus.value);
      options(teacher, data.teachers, '请选择指导老师', teacher.value);
      if (!data.matched) { message.textContent = '未查到该姓名的报名记录，请核对姓名或联系指导老师。'; return; }
      if (data.selection_required) message.textContent = '请选择自己的校区和指导老师，确认同名身份。';
      else if (!data.results.length) message.textContent = '未找到所选校区和指导老师的考试记录，请重新选择。';
      else { message.textContent = searchedName + ' · ' + (filters.campus || '未填写') + ' · ' + (filters.teacher || '未填写'); results(data.results); }
    } catch (error) {
      if (id !== sequence || error.name === 'AbortError') return;
      message.textContent = error.message || '网络异常，请重试。';
    } finally { if (id === sequence) button.disabled = false; }
  }
  function cancel() {
    ++sequence; if (pending) pending.abort(); button.disabled = false; clearResults();
  }
  name.addEventListener('input', function () {
    cancel(); searchedName = ''; options(campus, [], '请先查询姓名', ''); options(teacher, [], '请先查询姓名', ''); message.textContent = '';
  });
  form.addEventListener('submit', function (event) {
    event.preventDefault(); searchedName = name.value.trim(); if (!searchedName) { name.focus(); return; }
    options(campus, [], '正在加载…', ''); options(teacher, [], '正在加载…', ''); query({});
  });
  campus.addEventListener('change', function () {
    teacher.value = ''; query(campus.value === '' ? {} : {campus:JSON.parse(campus.value)});
  });
  teacher.addEventListener('change', function () {
    const filters = {};
    if (campus.value !== '') filters.campus = JSON.parse(campus.value);
    if (teacher.value !== '') filters.teacher = JSON.parse(teacher.value);
    query(filters);
  });
}());
