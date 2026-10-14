/* AG Grid Community adapter. Ranking, filtering and access remain with the platform.
 * The server-rendered table is also the fallback if the locally hosted library fails.
 */
(() => {
  'use strict';
  const source = document.querySelector('.board .scroll table');
  const content = document.getElementById('board-content');
  if (!source || !content) return;
  const scroll = source.parentElement;
  const host = document.createElement('div');
  host.id = 'board-grid';
  host.className = 'board-grid';
  host.hidden = true;
  scroll.append(host);
  let api = null, signature = '', library = null, scheduled = false;

  function loadLibrary() {
    if (window.agGrid) return Promise.resolve(window.agGrid);
    if (library) return library;
    library = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = '/static/vendor/ag-grid/36.2.0/ag-grid-community.min.js';
      script.onload = () => window.agGrid ? resolve(window.agGrid) : reject(new Error('AG Grid unavailable'));
      script.onerror = () => reject(new Error('AG Grid load failed'));
      document.head.append(script);
    });
    return library;
  }

  function readRows() {
    return Array.from(source.tBodies[0].rows).filter(row => !row.querySelector('.empty')).map((row, index) => {
      const cells = Array.from(row.cells);
      // Display labels cannot identify students: blank and literal "未填写" look alike.
      // Keep public projection order; only the private view has a stable account link.
      const id = cells[2].querySelector('a')?.getAttribute('href') || 'public-row-' + index;
      return {id, cells: cells.map(cell => ({
        text: cell.textContent.trim(),
        node: cell.cloneNode(true),
      }))};
    });
  }

  function cellRenderer(index) {
    return params => {
      const cell = params.data.cells[index];
      const value = document.createElement('div');
      value.className = 'board-cell' + (index >= 2 && index <= 4 ? ' board-cell-wrap' : '');
      for (const name of ['rank', 'leader', 'total', 'waiting']) {
        if (cell.node.classList.contains(name)) value.classList.add(name);
      }
      // Clone already escaped DOM nodes; never interpolate names into markup.
      for (const child of cell.node.childNodes) value.append(child.cloneNode(true));
      if (index >= 2 && index <= 4) value.title = cell.text;
      return value;
    };
  }

  function columns() {
    const widths = [64, 84, 108, 100, 88, 76];
    return Array.from(source.tHead.rows[0].cells).map((header, index) => ({
      colId: 'c' + index,
      headerName: header.textContent.trim(),
      headerTooltip: header.title || header.textContent.trim(),
      valueGetter: params => params.data.cells[index].text,
      cellRenderer: cellRenderer(index),
      cellClass: index >= 2 && index <= 4 ? 'board-identity-cell' : 'board-score-cell',
      headerClass: index >= 2 && index <= 4 ? 'board-identity-header' : 'board-score-header',
      ...(index < 6 ? {width: widths[index], minWidth: index === 2 ? 88 : widths[index],
        autoHeight: index >= 2 && index <= 4} : {flex: 1, minWidth: 88}),
    }));
  }

  async function sync() {
    scheduled = false;
    if (content.hidden) {
      if (api) api.setGridOption('rowData', []);
      host.hidden = true;
      return;
    }
    try {
      const ag = await loadLibrary();
      if (content.hidden) return;
      const defs = columns(), nextSignature = JSON.stringify(defs.map(c => [c.headerName, c.headerTooltip]));
      const rows = readRows();
      host.hidden = false;
      if (!api) {
        const theme = ag.themeQuartz.withParams({
          fontFamily: 'inherit', fontSize: 13, headerFontSize: 12, headerFontWeight: 500,
          accentColor: '#206bc4', headerBackgroundColor: '#f8fafc',
          headerTextColor: '#66758a', borderColor: '#e7edf4',
          cellHorizontalPadding: 8, wrapperBorder: false, wrapperBorderRadius: 0,
        });
        api = ag.createGrid(host, {
          theme, columnDefs: defs, rowData: rows, getRowId: params => params.data.id,
          defaultColDef: {sortable: false, resizable: true, suppressMovable: true, cellDataType: false},
          rowHeight: 54, headerHeight: 44, animateRows: false,
          enableCellTextSelection: true, ensureDomOrder: true,
          loadThemeGoogleFonts: false, suppressNoRowsOverlay: false,
          localeText: {noRowsToShow: '暂无符合条件的学生'},
        });
        api.setGridAriaProperty('label', '学生分数');
      } else {
        if (signature !== nextSignature) api.setGridOption('columnDefs', defs);
        api.setGridOption('rowData', rows);
        // Same visible text can still have new score-state classes or private links.
        api.refreshCells({force: true});
      }
      signature = nextSignature;
      source.hidden = true;
      source.setAttribute('aria-hidden', 'true');
      scroll.classList.add('board-grid-enhanced');
    } catch (error) {
      if (api) { api.destroy(); api = null; }
      library = null;
      host.hidden = true;
      source.hidden = false;
      source.removeAttribute('aria-hidden');
      scroll.classList.remove('board-grid-enhanced');
      console.warn('排行榜组件未加载，保留基础成绩表。', error);
    }
  }

  function schedule() {
    if (!scheduled) { scheduled = true; queueMicrotask(sync); }
  }
  // Existing 15-second polling and all three filters update this same authorised table.
  new MutationObserver(schedule).observe(source, {childList: true, subtree: true});
  new MutationObserver(schedule).observe(content, {attributes: true, attributeFilter: ['hidden']});
  schedule();
})();
