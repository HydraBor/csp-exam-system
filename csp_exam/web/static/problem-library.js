/* Native ZIP download; selection survives table refreshes after library actions. */
(function () {
  'use strict';
  const rows = document.getElementById('prob-rows');
  const form = document.getElementById('csp-export-form');
  const all = document.getElementById('csp-export-all');
  if (!rows || !form || !all) return;
  const selected = new Set();
  const field = document.getElementById('csp-export-pids');
  const button = document.getElementById('csp-export-selected');
  const count = document.getElementById('csp-export-count');
  function sync() {
    const boxes = Array.from(rows.querySelectorAll('[data-export-pid]'));
    const available = new Set(boxes.map(box => box.dataset.exportPid));
    selected.forEach(pid => { if (!available.has(pid)) selected.delete(pid); });
    boxes.forEach(box => { box.checked = selected.has(box.dataset.exportPid); });
    const chosen = boxes.filter(box => box.checked).map(box => box.dataset.exportPid);
    field.value = chosen.join(',');
    button.disabled = !chosen.length;
    count.textContent = '已选 ' + chosen.length + ' 道';
    all.disabled = !boxes.length;
    all.checked = boxes.length > 0 && chosen.length === boxes.length;
    all.indeterminate = chosen.length > 0 && chosen.length < boxes.length;
  }
  rows.addEventListener('change', event => {
    const box = event.target.closest('[data-export-pid]');
    if (!box) return;
    if (box.checked) selected.add(box.dataset.exportPid);
    else selected.delete(box.dataset.exportPid);
    sync();
  });
  all.addEventListener('change', () => {
    rows.querySelectorAll('[data-export-pid]').forEach(box => {
      if (all.checked) selected.add(box.dataset.exportPid);
      else selected.delete(box.dataset.exportPid);
    });
    sync();
  });
  form.addEventListener('submit', event => {
    sync();
    if (!field.value) event.preventDefault();
  });
  new MutationObserver(sync).observe(rows, {childList: true});
  sync();
})();
