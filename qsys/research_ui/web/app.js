const MAX_FEATURE_SELECTION = 80;
const CHART_COLORS = {
  strategy: '#0d6e6e',
  benchmark: '#1d4ed8',
  accent: '#d97706',
  danger: '#b42318',
  neutral: '#344054',
  grid: 'rgba(31, 41, 51, 0.12)',
  axis: 'rgba(31, 41, 51, 0.72)',
  candleUp: '#0b6b58',
  candleDown: '#b42318',
  volume: '#8b6f47',
};

const state = {
  currentView: 'backtest',
  loadedViews: new Set(),
  cache: new Map(),
  tableState: new Map(),
  tableRegistry: new Map(),
  latestBacktestRunId: null,
  backtestRuns: [],
  featureRegistry: [],
  caseFeatureSnapshot: {},
  featureSnapshot: {},
  selectedFeatureNames: new Set(),
  context: {
    tradeDate: '',
    instrumentId: '',
    runId: '',
    featureId: '',
    account: 'shadow',
    priceMode: 'fq',
  },
  backtest: {
    summary: null,
    daily: [],
    groupReturns: [],
    selectedDate: '',
    selectedInstrument: '',
    ordersByDate: new Map(),
  },
  caseData: null,
  featureData: {
    health: null,
    selectedFeatureName: '',
  },
  replayData: {
    payload: null,
    selectedInstrument: '',
  },
};

const viewMeta = {
  backtest: ['Backtest Explorer', '收益、回撤、日级 drill-down 和跨页研究链路。'],
  case: ['Case Workspace', '单票工作台，围绕价格、信号、特征和订单做闭环排查。'],
  feature: ['Feature Health', '问题队列、snapshot、registry 和诊断占位区。'],
  replay: ['Decision Replay', '从 candidate pool 到 final orders 的决策流水线。'],
};

function byId(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function quoteJsString(value) {
  return String(value).replaceAll('\\', '\\\\').replaceAll("'", "\\'");
}

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function toDateLabel(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  return text.slice(0, 10);
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  const numeric = Number(value);
  if (Math.abs(numeric) >= 100000000) return `${(numeric / 100000000).toFixed(2)}e`;
  if (Math.abs(numeric) >= 10000) return `${(numeric / 10000).toFixed(2)}w`;
  if (Number.isInteger(numeric) && digits === 0) return String(numeric);
  if (Number.isInteger(numeric) && Math.abs(numeric) >= 10) return String(numeric);
  return numeric.toFixed(digits);
}

function formatPercent(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function formatMaybePercent(value, digits = 2) {
  if (typeof value === 'string') return escapeHtml(value);
  const numeric = toNumber(value);
  if (numeric === null) return '-';
  if (Math.abs(numeric) <= 2) return formatPercent(numeric, digits);
  return formatNumber(numeric, digits);
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : formatNumber(value, Math.abs(value) < 1 ? 4 : 2);
  if (typeof value === 'object') return `<pre class="code-inline">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  return escapeHtml(String(value));
}

function setStatus(text, tone = 'ready') {
  const pill = byId('status-pill');
  if (!pill) return;
  pill.textContent = text;
  pill.dataset.tone = tone;
}

function getTableState(tableKey) {
  if (!state.tableState.has(tableKey)) {
    state.tableState.set(tableKey, { filter: '', sortKey: '', sortDir: 'asc' });
  }
  return state.tableState.get(tableKey);
}

function setView(name) {
  state.currentView = name;
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === name));
  document.querySelectorAll('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
  byId('view-title').textContent = viewMeta[name][0];
  byId('view-subtitle').textContent = viewMeta[name][1];
  updateLocationHash();
}

function readContextFromInputs() {
  return {
    tradeDate: byId('context-trade-date').value.trim(),
    instrumentId: byId('context-instrument').value.trim(),
    runId: byId('backtest-run-select').value.trim(),
    featureId: byId('context-feature-id').value.trim(),
    account: byId('context-account').value.trim() || 'shadow',
    priceMode: byId('context-price-mode').value,
  };
}

function syncInputsFromContext() {
  byId('context-trade-date').value = state.context.tradeDate || '';
  byId('context-instrument').value = state.context.instrumentId || '';
  byId('context-feature-id').value = state.context.featureId || '';
  byId('context-account').value = state.context.account || 'shadow';
  byId('context-price-mode').value = state.context.priceMode || 'fq';
  if (state.context.runId && Array.from(byId('backtest-run-select').options || []).some((option) => option.value === state.context.runId)) {
    byId('backtest-run-select').value = state.context.runId;
  }
}

function updateContext(updates, { syncInputs = true, syncHash = true } = {}) {
  const next = { ...state.context };
  Object.entries(updates || {}).forEach(([key, value]) => {
    if (value === undefined || value === null) return;
    next[key] = typeof value === 'string' ? value.trim() : value;
  });
  if (!next.account) next.account = 'shadow';
  if (!next.priceMode) next.priceMode = 'fq';
  state.context = next;
  if (syncInputs) syncInputsFromContext();
  renderContextPresentation();
  if (syncHash) updateLocationHash();
}

function updateLocationHash() {
  const params = new URLSearchParams();
  params.set('view', state.currentView);
  if (state.context.tradeDate) params.set('trade_date', state.context.tradeDate);
  if (state.context.instrumentId) params.set('instrument_id', state.context.instrumentId);
  if (state.context.runId) params.set('run_id', state.context.runId);
  if (state.context.featureId) params.set('feature_id', state.context.featureId);
  if (state.context.account) params.set('account', state.context.account);
  if (state.context.priceMode) params.set('price_mode', state.context.priceMode);
  const hashValue = params.toString();
  if (window.location.hash.slice(1) === hashValue) return;
  history.replaceState(null, '', `${window.location.pathname}${hashValue ? `#${hashValue}` : ''}`);
}

function applyHashContext() {
  const hash = window.location.hash.replace(/^#/, '');
  if (!hash) return;
  const params = new URLSearchParams(hash);
  updateContext({
    tradeDate: params.get('trade_date') || state.context.tradeDate,
    instrumentId: params.get('instrument_id') || state.context.instrumentId,
    runId: params.get('run_id') || state.context.runId,
    featureId: params.get('feature_id') || state.context.featureId,
    account: params.get('account') || state.context.account,
    priceMode: params.get('price_mode') || state.context.priceMode,
  }, { syncInputs: true, syncHash: false });
  const view = params.get('view');
  if (view && viewMeta[view]) state.currentView = view;
}

function renderKeyValueItem(label, value) {
  return `<div class="stack-item"><div><span>${escapeHtml(label)}</span><strong>${value}</strong></div></div>`;
}

function renderContextCard(label, value, copyValue = '') {
  const display = value || '-';
  const copyButton = copyValue ? `<button type="button" class="copy-mini" onclick="copyText('${quoteJsString(copyValue)}')">Copy</button>` : '';
  return `
    <div class="context-card">
      <div>
        <span class="section-label">${escapeHtml(label)}</span>
        <div class="value">${display}</div>
      </div>
      ${copyButton}
    </div>
  `;
}

function renderContextPresentation() {
  const runMarkup = state.context.runId
    ? renderRunLink(state.context.runId)
    : '<span class="muted-text">No run selected</span>';
  const tradeDateMarkup = state.context.tradeDate
    ? renderTradeDateLink(state.context.tradeDate)
    : '<span class="muted-text">No date</span>';
  const instrumentMarkup = state.context.instrumentId
    ? renderInstrumentLink(state.context.instrumentId, state.context.tradeDate)
    : '<span class="muted-text">No instrument</span>';
  const featureMarkup = state.context.featureId
    ? renderFeatureLink(state.context.featureId, state.context.instrumentId, state.context.tradeDate)
    : '<span class="muted-text">No feature</span>';

  byId('sidebar-context').innerHTML = [
    renderContextCard('Run ID', runMarkup, state.context.runId),
    renderContextCard('Trade Date', tradeDateMarkup, state.context.tradeDate),
    renderContextCard('Instrument', instrumentMarkup, state.context.instrumentId),
    renderContextCard('Feature ID', featureMarkup, state.context.featureId),
  ].join('');

  byId('context-strip').innerHTML = [
    renderContextCard('Run', runMarkup, state.context.runId),
    renderContextCard('Date', tradeDateMarkup, state.context.tradeDate),
    renderContextCard('Instrument', instrumentMarkup, state.context.instrumentId),
    renderContextCard('Account / Price', `<span class="entity-pill">${escapeHtml(state.context.account)} / ${escapeHtml(state.context.priceMode)}</span>`, JSON.stringify({ account: state.context.account, price_mode: state.context.priceMode })),
  ].join('');
}

function hasPlotly() {
  return typeof window !== 'undefined' && typeof window.Plotly !== 'undefined';
}

function renderChartError(containerId, message) {
  const root = byId(containerId);
  if (!root) return;
  root.innerHTML = `<div class="chart-empty">${escapeHtml(message)}</div>`;
}

function plotlyBaseLayout({ title = '', height = 320, yAxisTitle = '', hoverMode = 'x unified', showRangeSlider = false, selectedDate = '' } = {}) {
  const layout = {
    title: title ? { text: title, font: { size: 13, color: CHART_COLORS.axis } } : undefined,
    height,
    margin: { l: 56, r: 26, t: title ? 44 : 18, b: 40 },
    paper_bgcolor: 'rgba(255, 252, 245, 0)',
    plot_bgcolor: 'rgba(255, 252, 245, 0)',
    hovermode: hoverMode,
    dragmode: 'pan',
    showlegend: true,
    legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'left', x: 0 },
    xaxis: {
      type: 'date',
      gridcolor: CHART_COLORS.grid,
      linecolor: CHART_COLORS.grid,
      tickfont: { color: CHART_COLORS.axis },
      rangeslider: { visible: showRangeSlider },
      fixedrange: false,
    },
    yaxis: {
      title: yAxisTitle ? { text: yAxisTitle, font: { size: 11, color: CHART_COLORS.axis } } : undefined,
      gridcolor: CHART_COLORS.grid,
      zerolinecolor: CHART_COLORS.grid,
      tickfont: { color: CHART_COLORS.axis },
      fixedrange: false,
    },
    font: { family: 'Space Grotesk, IBM Plex Sans, Noto Sans SC, sans-serif', color: CHART_COLORS.axis },
    hoverlabel: { bgcolor: '#fffdf8', bordercolor: CHART_COLORS.grid, font: { color: CHART_COLORS.axis } },
  };
  if (selectedDate) {
    layout.shapes = [{
      type: 'line',
      x0: selectedDate,
      x1: selectedDate,
      xref: 'x',
      y0: 0,
      y1: 1,
      yref: 'paper',
      line: { color: CHART_COLORS.accent, width: 2, dash: 'dot' },
    }];
  }
  return layout;
}

function bindPlotlyHandlers(root, handlers = {}) {
  if (!root || !root.on) return;
  if (typeof root.removeAllListeners === 'function') {
    root.removeAllListeners('plotly_click');
  }
  if (handlers.onClick) root.on('plotly_click', handlers.onClick);
}

function renderPlotlyChart(containerId, traces, layout, handlers = {}) {
  if (!hasPlotly()) {
    renderChartError(containerId, 'Plotly failed to load. Check CDN/network access and refresh.');
    return;
  }
  const root = byId(containerId);
  if (!root) return;
  if (!traces || traces.length === 0) {
    renderChartError(containerId, 'No chart data');
    return;
  }
  const renderResult = window.Plotly.react(root, traces, layout, {
    responsive: true,
    displaylogo: false,
    displayModeBar: 'hover',
    modeBarButtonsToRemove: ['lasso2d', 'select2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'resetScale2d'],
    scrollZoom: false,
    doubleClick: 'reset',
  });
  Promise.resolve(renderResult).then(() => bindPlotlyHandlers(root, handlers));
}

function dashToPlotlyDash(dash) {
  if (!dash) return 'solid';
  return dash.includes('2') ? 'dot' : 'dash';
}

function renderSeriesChart(containerId, { dates = [], series = [], title = '', includeZero = false, yAxisTitle = '', height = 320, selectedDate = '', onClick = null } = {}) {
  const traces = series
    .map((item) => ({
      ...item,
      points: dates.map((tradeDate, index) => ({ tradeDate, value: toNumber(item.values[index]) }))
        .filter((point) => point.value !== null),
    }))
    .filter((item) => item.points.length > 0)
    .map((item) => ({
      type: 'scatter',
      mode: 'lines',
      name: item.name,
      x: item.points.map((point) => point.tradeDate),
      y: item.points.map((point) => point.value),
      line: {
        color: item.color || CHART_COLORS.strategy,
        width: item.width || 2.2,
        dash: dashToPlotlyDash(item.dash),
      },
      hovertemplate: `${escapeHtml(item.name)}<br>%{x}<br>%{y:.4f}<extra></extra>`,
    }));
  if (!traces.length) {
    renderChartError(containerId, 'No chart data');
    return;
  }
  const layout = plotlyBaseLayout({ title, height, yAxisTitle, selectedDate });
  if (includeZero) layout.yaxis.zeroline = true;
  renderPlotlyChart(containerId, traces, layout, {
    onClick: (event) => {
      const tradeDate = toDateLabel(event?.points?.[0]?.x);
      if (tradeDate && onClick) onClick(tradeDate);
    },
  });
}

function renderVolumeChart(containerId, bars, { selectedDate = '' } = {}) {
  const points = (bars || [])
    .map((item) => ({
      tradeDate: item.trade_date,
      value: toNumber(item.volume),
      color: toNumber(item.close) >= toNumber(item.open) ? CHART_COLORS.candleUp : CHART_COLORS.candleDown,
    }))
    .filter((item) => item.value !== null);
  if (!points.length) {
    renderChartError(containerId, 'No volume data');
    return;
  }
  renderPlotlyChart(containerId, [{
    type: 'bar',
    name: 'Volume',
    x: points.map((item) => item.tradeDate),
    y: points.map((item) => item.value),
    marker: { color: points.map((item) => item.color), opacity: 0.78 },
    hovertemplate: 'Volume<br>%{x}<br>%{y:.0f}<extra></extra>',
  }], plotlyBaseLayout({ title: 'Volume', height: 170, yAxisTitle: 'Volume', selectedDate }));
}

function normalizeTradeSide(side) {
  const value = String(side || '').trim().toLowerCase();
  if (value.startsWith('s')) return 'sell';
  if (value.startsWith('b')) return 'buy';
  return value || 'buy';
}

function buildHoldingSpans(markers = []) {
  const fills = (markers || [])
    .filter((item) => (item.source || 'fill') === 'fill')
    .map((item) => ({
      tradeDate: toDateLabel(item.trade_date || item.date),
      value: toNumber(item.value || item.deal_price || item.price),
      side: normalizeTradeSide(item.side),
      quantity: toNumber(item.quantity || item.filled_amount || item.amount) || 0,
    }))
    .filter((item) => item.tradeDate && item.value !== null && item.quantity > 0)
    .sort((a, b) => `${a.tradeDate}-${a.side}`.localeCompare(`${b.tradeDate}-${b.side}`));

  const buyQueue = [];
  const spans = [];
  fills.forEach((item) => {
    if (item.side === 'buy') {
      buyQueue.push({ ...item, remaining: item.quantity });
      return;
    }
    let remainingSell = item.quantity;
    while (remainingSell > 0 && buyQueue.length) {
      const buyLot = buyQueue[0];
      const matchedQty = Math.min(buyLot.remaining, remainingSell);
      spans.push({
        startDate: buyLot.tradeDate,
        startPrice: buyLot.value,
        endDate: item.tradeDate,
        endPrice: item.value,
        quantity: matchedQty,
        pnlPct: buyLot.value ? (item.value - buyLot.value) / buyLot.value : null,
      });
      buyLot.remaining -= matchedQty;
      remainingSell -= matchedQty;
      if (buyLot.remaining <= 0) buyQueue.shift();
    }
  });
  return spans;
}

function renderCandlestickChart(containerId, bars, markers = [], instrumentId = '', { selectedDate = '', annotations = [] } = {}) {
  const validBars = (bars || []).filter((item) => ['open', 'high', 'low', 'close'].every((key) => toNumber(item[key]) !== null));
  if (!validBars.length) {
    renderChartError(containerId, 'No OHLC data');
    return;
  }

  const buyMarkers = [];
  const sellMarkers = [];
  const orderBuyMarkers = [];
  const orderSellMarkers = [];
  markers.forEach((item) => {
    const marker = {
      tradeDate: toDateLabel(item.trade_date || item.date),
      value: toNumber(item.value || item.deal_price || item.price),
      side: normalizeTradeSide(item.side),
      quantity: toNumber(item.quantity || item.filled_amount || item.amount),
      source: item.source || 'fill',
    };
    if (!marker.tradeDate || marker.value === null) return;
    const bucket = marker.source === 'order'
      ? (marker.side === 'sell' ? orderSellMarkers : orderBuyMarkers)
      : (marker.side === 'sell' ? sellMarkers : buyMarkers);
    bucket.push(marker);
  });

  const holdingSpans = buildHoldingSpans(markers);

  const traces = [{
    type: 'candlestick',
    name: instrumentId || 'OHLC',
    x: validBars.map((item) => item.trade_date),
    open: validBars.map((item) => Number(item.open)),
    high: validBars.map((item) => Number(item.high)),
    low: validBars.map((item) => Number(item.low)),
    close: validBars.map((item) => Number(item.close)),
    increasing: { line: { color: CHART_COLORS.candleUp }, fillcolor: CHART_COLORS.candleUp },
    decreasing: { line: { color: CHART_COLORS.candleDown }, fillcolor: CHART_COLORS.candleDown },
    showlegend: true,
    hoverlabel: { namelength: -1 },
  }];

  if (holdingSpans.length) {
    traces.push({
      type: 'scatter',
      mode: 'lines',
      name: 'Holding Span',
      x: holdingSpans.flatMap((item) => [item.startDate, item.endDate, null]),
      y: holdingSpans.flatMap((item) => [item.startPrice, item.endPrice, null]),
      line: { color: '#7d8f69', width: 2, dash: 'dot' },
      customdata: holdingSpans.flatMap((item) => [[item.quantity, item.pnlPct], [item.quantity, item.pnlPct], [null, null]]),
      hovertemplate: 'Holding Span<br>%{x}<br>Price %{y:.2f}<br>Qty %{customdata[0]:.0f}<br>PnL %{customdata[1]:+.2%}<extra></extra>',
    });
  }
  if (orderBuyMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Replay Buy Order',
      x: orderBuyMarkers.map((item) => item.tradeDate),
      y: orderBuyMarkers.map((item) => item.value),
      marker: { color: CHART_COLORS.strategy, size: 9, symbol: 'circle-open' },
      customdata: orderBuyMarkers.map((item) => [item.quantity]),
      hovertemplate: 'Replay Buy Order<br>%{x}<br>Price %{y:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }
  if (orderSellMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Replay Sell Order',
      x: orderSellMarkers.map((item) => item.tradeDate),
      y: orderSellMarkers.map((item) => item.value),
      marker: { color: CHART_COLORS.accent, size: 9, symbol: 'circle-open' },
      customdata: orderSellMarkers.map((item) => [item.quantity]),
      hovertemplate: 'Replay Sell Order<br>%{x}<br>Price %{y:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }
  if (buyMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Filled Buy',
      x: buyMarkers.map((item) => item.tradeDate),
      y: buyMarkers.map((item) => item.value),
      marker: { color: CHART_COLORS.strategy, size: 11, symbol: 'triangle-up' },
      customdata: buyMarkers.map((item) => [item.quantity]),
      hovertemplate: 'Filled Buy<br>%{x}<br>Price %{y:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }
  if (sellMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Filled Sell',
      x: sellMarkers.map((item) => item.tradeDate),
      y: sellMarkers.map((item) => item.value),
      marker: { color: CHART_COLORS.accent, size: 11, symbol: 'triangle-down' },
      customdata: sellMarkers.map((item) => [item.quantity]),
      hovertemplate: 'Filled Sell<br>%{x}<br>Price %{y:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }

  const layout = plotlyBaseLayout({
    title: `${instrumentId || 'Instrument'} OHLC`,
    height: 460,
    yAxisTitle: 'Price',
    hoverMode: 'x',
    showRangeSlider: false,
    selectedDate,
  });
  const annotationShapes = (annotations || [])
    .map((item) => ({
      type: 'line',
      x0: toDateLabel(item.trade_date),
      x1: toDateLabel(item.trade_date),
      xref: 'x',
      y0: 0,
      y1: 1,
      yref: 'paper',
      line: {
        color: item.type === 'signal_date' ? '#7d8f69' : '#c27b4f',
        width: item.type === 'signal_date' ? 1.5 : 2,
        dash: item.type === 'signal_date' ? 'dash' : 'dot',
      },
    }))
    .filter((item) => item.x0);
  layout.shapes = [...(layout.shapes || []), ...annotationShapes];
  layout.annotations = (annotations || [])
    .map((item) => ({
      x: toDateLabel(item.trade_date),
      y: 1,
      yref: 'paper',
      xref: 'x',
      text: escapeHtml(item.label || item.type || ''),
      showarrow: false,
      yshift: 12,
      font: { size: 10, color: item.type === 'signal_date' ? '#7d8f69' : '#c27b4f' },
      bgcolor: 'rgba(255,252,245,0.92)',
      bordercolor: 'rgba(194,123,79,0.22)',
      borderwidth: 1,
    }))
    .filter((item) => item.x);

  renderPlotlyChart(containerId, traces, layout);
}

function rebaseSeries(values, base = 100) {
  const first = values.map((value) => toNumber(value)).find((value) => value !== null && value !== 0);
  if (first === undefined) return values.map(() => null);
  return values.map((value) => {
    const numeric = toNumber(value);
    return numeric === null ? null : base * (numeric / first);
  });
}

function alignSeriesByDate(targetDates, sourceRows, valueKey = 'close') {
  const lookup = new Map((sourceRows || []).map((item) => [item.trade_date, item]));
  return targetDates.map((tradeDate) => {
    const matched = lookup.get(tradeDate);
    return matched ? matched[valueKey] : null;
  });
}

function stripHtml(value) {
  return String(value || '').replace(/<[^>]+>/g, ' ');
}

function compareValues(left, right) {
  if (left === right) return 0;
  if (left === null || left === undefined || left === '') return 1;
  if (right === null || right === undefined || right === '') return -1;
  const leftNumber = toNumber(left);
  const rightNumber = toNumber(right);
  if (leftNumber !== null && rightNumber !== null) return leftNumber - rightNumber;
  return String(left).localeCompare(String(right));
}

function matchTableRow(row, columns, filter) {
  if (!filter) return true;
  const needle = filter.toLowerCase();
  return columns.some((column) => {
    if (column.filterValue) return String(column.filterValue(row) || '').toLowerCase().includes(needle);
    if (column.key) return String(row[column.key] || '').toLowerCase().includes(needle);
    if (column.render) return stripHtml(column.render(row)).toLowerCase().includes(needle);
    return false;
  });
}

function renderDataTable(containerId, rows, columns, options = {}) {
  const root = byId(containerId);
  if (!root) return;
  const tableKey = options.tableKey || containerId;
  const tableState = getTableState(tableKey);
  const rerender = () => renderDataTable(containerId, rows, columns, options);
  state.tableRegistry.set(tableKey, { rows, columns, options, rerender, visibleRows: [] });

  const filterValue = (tableState.filter || '').trim();
  let visibleRows = (rows || []).filter((row) => matchTableRow(row, columns, filterValue));
  const sortKey = tableState.sortKey;
  if (sortKey) {
    const sortColumn = columns.find((column, index) => (column.id || column.key || `col-${index}`) === sortKey);
    if (sortColumn) {
      visibleRows = visibleRows.slice().sort((left, right) => {
        const leftValue = sortColumn.sortValue ? sortColumn.sortValue(left) : (sortColumn.key ? left[sortColumn.key] : '');
        const rightValue = sortColumn.sortValue ? sortColumn.sortValue(right) : (sortColumn.key ? right[sortColumn.key] : '');
        const direction = tableState.sortDir === 'desc' ? -1 : 1;
        return compareValues(leftValue, rightValue) * direction;
      });
    }
  }
  state.tableRegistry.get(tableKey).visibleRows = visibleRows;

  const toolbar = options.hideToolbar
    ? ''
    : `
      <div class="table-tools">
        <input class="table-search" value="${escapeHtml(filterValue)}" placeholder="Quick filter" oninput="updateTableFilter('${quoteJsString(tableKey)}', this.value)" />
        <span class="table-count">${visibleRows.length} rows</span>
      </div>
    `;

  if (!visibleRows.length) {
    root.innerHTML = `${toolbar}<div class="empty">${escapeHtml(options.emptyMessage || 'No data')}</div>`;
    return;
  }

  const header = columns.map((column, index) => {
    const columnId = column.id || column.key || `col-${index}`;
    const sortable = column.sortable !== false;
    const active = tableState.sortKey === columnId;
    const sortMark = active ? (tableState.sortDir === 'asc' ? '&#8593;' : '&#8595;') : '';
    return `<th class="${sortable ? 'sortable' : ''}" ${sortable ? `onclick="toggleTableSort('${quoteJsString(tableKey)}', '${quoteJsString(columnId)}')"` : ''}>${escapeHtml(column.label)}<span class="sort-mark">${sortMark}</span></th>`;
  }).join('');

  const selectedResolver = options.selectedRowId || (() => '');
  const body = visibleRows.map((row, index) => {
    const rowId = selectedResolver(row);
    const isSelected = rowId && rowId === (options.selectedValue || '');
    const rowClass = [options.onRowClick ? 'interactive' : '', isSelected ? 'selected-row' : ''].filter(Boolean).join(' ');
    const cells = columns.map((column) => {
      if (column.render) return `<td>${column.render(row)}</td>`;
      return `<td>${formatValue(row[column.key])}</td>`;
    }).join('');
    const onClick = options.onRowClick ? `onclick="selectTableRow('${quoteJsString(tableKey)}', ${index})"` : '';
    return `<tr class="${rowClass}" ${onClick}>${cells}</tr>`;
  }).join('');

  root.innerHTML = `${toolbar}<div class="table-scroll"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
}

window.updateTableFilter = function updateTableFilter(tableKey, value) {
  const tableState = getTableState(tableKey);
  tableState.filter = value;
  const registry = state.tableRegistry.get(tableKey);
  if (registry) registry.rerender();
};

window.toggleTableSort = function toggleTableSort(tableKey, columnId) {
  const tableState = getTableState(tableKey);
  if (tableState.sortKey === columnId) {
    tableState.sortDir = tableState.sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    tableState.sortKey = columnId;
    tableState.sortDir = 'asc';
  }
  const registry = state.tableRegistry.get(tableKey);
  if (registry) registry.rerender();
};

window.selectTableRow = function selectTableRow(tableKey, index) {
  const registry = state.tableRegistry.get(tableKey);
  if (!registry || !registry.options.onRowClick) return;
  const row = registry.visibleRows[index];
  if (!row) return;
  registry.options.onRowClick(row);
};

function makeBadge(text, tone = 'neutral') {
  const toneClass = tone === 'danger' ? 'badge-danger' : tone === 'warning' ? 'badge-warning' : tone === 'success' ? 'badge-success' : '';
  return `<span class="badge ${toneClass}">${escapeHtml(String(text || '-'))}</span>`;
}

function makeStackItems(items) {
  if (!items || !items.length) return '<div class="empty">No details</div>';
  return items.map((item) => renderKeyValueItem(item.label, item.value)).join('');
}

function copyText(value) {
  const text = String(value || '');
  if (!text) return;
  const onSuccess = () => setStatus('Copied', 'ready');
  const onFailure = () => setStatus('Copy unavailable', 'error');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onSuccess).catch(onFailure);
    return;
  }
  try {
    const input = document.createElement('textarea');
    input.value = text;
    document.body.appendChild(input);
    input.select();
    document.execCommand('copy');
    document.body.removeChild(input);
    onSuccess();
  } catch (error) {
    onFailure();
  }
}

window.copyText = copyText;

function renderEntityWrap(mainAction, copyValue = '') {
  return `<span class="entity-wrap">${mainAction}${copyValue ? `<button type="button" class="copy-mini" onclick="copyText('${quoteJsString(copyValue)}')">Copy</button>` : ''}</span>`;
}

function renderRunLink(runId, label = '') {
  if (!runId) return '<span class="muted-text">-</span>';
  return renderEntityWrap(`<button type="button" class="entity-link" onclick="jumpToBacktest('${quoteJsString(runId)}', '${quoteJsString(state.context.tradeDate || '')}')">${escapeHtml(label || runId)}</button>`, runId);
}

function renderTradeDateLink(tradeDate, view = 'backtest') {
  if (!tradeDate) return '<span class="muted-text">-</span>';
  const handler = view === 'replay'
    ? `jumpToReplay('${quoteJsString(tradeDate)}', '${quoteJsString(state.context.instrumentId || '')}')`
    : `jumpToBacktest('${quoteJsString(state.context.runId || '')}', '${quoteJsString(tradeDate)}')`;
  return renderEntityWrap(`<button type="button" class="entity-link" onclick="${handler}">${escapeHtml(tradeDate)}</button>`, tradeDate);
}

function renderInstrumentLink(instrumentId, tradeDate = '') {
  if (!instrumentId) return '<span class="muted-text">-</span>';
  return renderEntityWrap(`<button type="button" class="entity-link" onclick="jumpToCase('${quoteJsString(instrumentId)}', '${quoteJsString(tradeDate || state.context.tradeDate || '')}')">${escapeHtml(instrumentId)}</button>`, instrumentId);
}

function renderFeatureLink(featureId, instrumentId = '', tradeDate = '') {
  if (!featureId) return '<span class="muted-text">-</span>';
  return renderEntityWrap(`<button type="button" class="entity-link" onclick="jumpToFeature('${quoteJsString(featureId)}', '${quoteJsString(instrumentId || state.context.instrumentId || '')}', '${quoteJsString(tradeDate || state.context.tradeDate || '')}')">${escapeHtml(featureId)}</button>`, featureId);
}

function renderActionButton(label, onClick, kind = 'ghost') {
  const klass = kind === 'secondary' ? 'secondary-btn' : kind === 'primary' ? '' : 'ghost-btn';
  return `<button type="button" class="${klass}" onclick="${onClick}">${escapeHtml(label)}</button>`;
}

async function getJson(url, { useCache = true } = {}) {
  if (useCache && state.cache.has(url)) return state.cache.get(url);
  setStatus('Loading', 'loading');
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    setStatus('API Error', 'error');
    throw new Error(payload.detail || 'request failed');
  }
  setStatus('API Ready', 'ready');
  if (useCache) state.cache.set(url, payload);
  return payload;
}

function unwrapData(payload) {
  return payload && typeof payload === 'object' && 'data' in payload ? payload.data : payload;
}

function unwrapItems(payload) {
  return payload && typeof payload === 'object' && Array.isArray(payload.items) ? payload.items : [];
}

function renderParameterSummary(summary) {
  const root = byId('backtest-params');
  const label = byId('backtest-run-label');
  if (!root || !label) return;
  if (!summary) {
    label.textContent = '';
    root.innerHTML = '<div class="empty">No parameter summary</div>';
    return;
  }
  label.textContent = summary.display_label || summary.run_id || '';
  const params = summary.parameter_summary || {};
  const rows = [
    ['Version', params.version_label || summary.display_label || '-'],
    ['Internal ID', params.internal_run_id || summary.run_id || '-'],
    ['Model', summary.model_name || '-'],
    ['Feature Set', params.feature_set || summary.feature_set || '-'],
    ['Model Type', params.model_type || '-'],
    ['Label Type', params.label_type || '-'],
    ['Strategy Type', params.strategy_type || '-'],
    ['Feature Count', params.feature_count ?? '-'],
    ['Universe', params.universe || summary.universe || '-'],
    ['Top K', params.top_k ?? summary.top_k ?? '-'],
    ['Rebalance Mode', params.rebalance_mode || '-'],
    ['Rebalance Freq', params.rebalance_freq || '-'],
    ['Inference Freq', params.inference_freq || '-'],
    ['Retrain Freq', params.retrain_freq || '-'],
    ['Signal Date', params.signal_date || summary.train_range?.start || '-'],
    ['Execution Date', params.execution_date || summary.test_range?.end || '-'],
    ['Training Mode', params.training_mode || '-'],
    ['Train End Requested', params.train_end_requested || '-'],
    ['Train End Effective', params.train_end_effective || '-'],
    ['Infer Date', params.infer_date || '-'],
    ['Last Train Sample', params.last_train_sample_date || '-'],
    ['Max Label Date', params.max_label_date_used || '-'],
    ['Label Mature', params.is_label_mature_at_infer_time ?? '-'],
    ['Shadow Rejects', params.shadow_reject_count ?? '-'],
    ['Suspicious Trades', params.suspicious_trade_count ?? '-'],
    ['Price Mode', params.price_mode || summary.price_mode || '-'],
    ['Model Path', params.model_path || '-'],
  ];
  root.innerHTML = rows.map(([key, value]) => `
    <div class="kv-item">
      <span>${escapeHtml(key)}</span>
      <strong>${escapeHtml(String(value))}</strong>
    </div>
  `).join('');
}

function renderBacktestRunOptions(runs) {
  const select = byId('backtest-run-select');
  if (!select) return;
  const currentValue = state.context.runId || select.value || '';
  const options = (runs || []).map((item) => `<option value="${escapeHtml(item.run_id)}">${escapeHtml(item.display_label || item.run_id)}</option>`).join('');
  select.innerHTML = options || '<option value="">no backtest runs</option>';
  if (currentValue && (runs || []).some((item) => item.run_id === currentValue)) {
    select.value = currentValue;
  } else if ((runs || []).length) {
    select.value = runs[0].run_id;
    updateContext({ runId: runs[0].run_id }, { syncInputs: false, syncHash: true });
  }
}

function average(values) {
  const numbers = values.map((value) => toNumber(value)).filter((value) => value !== null);
  if (!numbers.length) return null;
  return numbers.reduce((sum, value) => sum + value, 0) / numbers.length;
}

function renderMetricCard(id, value, noteId, note) {
  byId(id).textContent = value;
  if (noteId && byId(noteId)) byId(noteId).textContent = note;
}

function summarizeBacktestMetrics(summary, dailyItems) {
  const metrics = summary?.metrics || {};
  const signalMetrics = summary?.signal_metrics || {};
  renderMetricCard('metric-total-return', metrics.total_return || '-', 'metric-total-return-note', 'strategy total return');
  renderMetricCard('metric-sharpe', metrics.sharpe || '-', 'metric-sharpe-note', 'reported by backtest');
  renderMetricCard('metric-max-drawdown', metrics.max_drawdown || '-', 'metric-max-drawdown-note', 'reported drawdown');
  renderMetricCard('metric-rank-ic', formatNumber(toNumber(signalMetrics.RankIC) ?? average(dailyItems.map((item) => item.rank_ic)), 4), 'metric-rank-ic-note', 'signal evaluator mean');
  renderMetricCard('metric-turnover', formatNumber(average(dailyItems.map((item) => item.turnover)), 0), 'metric-turnover-note', 'avg daily turnover');
  renderMetricCard('metric-trade-days', String(dailyItems.length || signalMetrics.days || 0), 'metric-trade-days-note', 'loaded daily rows');
}

function renderBacktestSignalMetrics(summary) {
  const root = byId('backtest-signal-metrics');
  if (!root) return;
  const signalMetrics = summary?.signal_metrics || {};
  const rows = [
    ['Status', signalMetrics.status || 'not_available'],
    ['IC', signalMetrics.IC === undefined || signalMetrics.IC === null ? 'not_available' : formatNumber(signalMetrics.IC, 4)],
    ['RankIC', signalMetrics.RankIC === undefined || signalMetrics.RankIC === null ? 'not_available' : formatNumber(signalMetrics.RankIC, 4)],
    ['ICIR', signalMetrics.ICIR === undefined || signalMetrics.ICIR === null ? 'not_available' : formatNumber(signalMetrics.ICIR, 4)],
    ['RankICIR', signalMetrics.RankICIR === undefined || signalMetrics.RankICIR === null ? 'not_available' : formatNumber(signalMetrics.RankICIR, 4)],
    ['Long-Short Spread', signalMetrics.long_short_spread === undefined || signalMetrics.long_short_spread === null ? 'not_available' : formatPercent(signalMetrics.long_short_spread, 2)],
    ['Label Horizon', signalMetrics.label_horizon || 'not_available'],
  ];
  root.innerHTML = rows.map(([key, value]) => `
    <div class="kv-item">
      <span>${escapeHtml(key)}</span>
      <strong>${escapeHtml(String(value))}</strong>
    </div>
  `).join('');
}

function renderBacktestGroupReturns() {
  const items = state.backtest.groupReturns || [];
  if (!items.length) {
    renderChartError('backtest-group-returns-chart', 'group_returns not_available');
    byId('backtest-group-returns-table').innerHTML = '<div class="empty">group_returns not_available</div>';
    return;
  }
  const dates = Array.from(new Set(items.map((item) => item.date))).sort();
  const groups = Array.from(new Set(items.map((item) => item.group))).sort((left, right) => Number(left) - Number(right));
  renderSeriesChart('backtest-group-returns-chart', {
    dates,
    title: 'Group NAV 1-5',
    height: 300,
    series: groups.map((group, idx) => ({
      name: `Group ${group}`,
      values: dates.map((date) => {
        const row = items.find((item) => item.date === date && Number(item.group) === Number(group));
        return row ? toNumber(row.nav) : null;
      }),
      color: [CHART_COLORS.strategy, CHART_COLORS.accent, CHART_COLORS.benchmark, CHART_COLORS.neutral, CHART_COLORS.danger][idx % 5],
    })),
    yAxisTitle: 'NAV',
  });
  const latestByGroup = groups.map((group) => {
    const rows = items.filter((item) => Number(item.group) === Number(group));
    return rows[rows.length - 1] || { group, nav: null, mean_return: null, label_horizon: 'not_available' };
  });
  renderDataTable('backtest-group-returns-table', latestByGroup, [
    { key: 'group', label: 'Group', render: (row) => escapeHtml(String(row.group)) },
    { key: 'nav', label: 'Last NAV', render: (row) => formatNumber(row.nav, 4), sortValue: (row) => toNumber(row.nav) },
    { key: 'mean_return', label: 'Last Mean Return', render: (row) => formatPercent(row.mean_return), sortValue: (row) => toNumber(row.mean_return) },
    { key: 'label_horizon', label: 'Label Horizon', render: (row) => escapeHtml(String(row.label_horizon || 'not_available')) },
  ], {
    tableKey: 'backtest-group-returns',
    emptyMessage: 'group_returns not_available',
  });
}

function renderBacktestCharts() {
  const dailyItems = state.backtest.daily;
  if (!dailyItems.length) {
    renderChartError('backtest-equity-chart', 'No daily backtest data');
    renderChartError('backtest-diagnostics-chart', 'No diagnostics data');
    renderBacktestGroupReturns();
    return;
  }
  const dates = dailyItems.map((item) => item.trade_date);
  const selectedDate = state.backtest.selectedDate;
  renderSeriesChart('backtest-equity-chart', {
    dates,
    title: 'Strategy / Zero-cost / CSI300 / SSE',
    height: 480,
    selectedDate,
    onClick: (tradeDate) => selectBacktestDate(tradeDate),
    series: [
      { name: 'Strategy', values: dailyItems.map((item) => item.equity), color: CHART_COLORS.strategy },
      { name: 'Zero-cost', values: dailyItems.map((item) => item.zero_cost_equity), color: CHART_COLORS.accent, dash: '3 4' },
      { name: 'CSI300', values: dailyItems.map((item) => item.benchmark_equity), color: CHART_COLORS.benchmark, dash: '6 4' },
      { name: 'SSE', values: dailyItems.map((item) => item.benchmark2_equity), color: CHART_COLORS.neutral, dash: '2 5' },
    ],
    yAxisTitle: 'Equity',
  });
  renderSeriesChart('backtest-diagnostics-chart', {
    dates,
    includeZero: true,
    title: 'Drawdown / IC / RankIC',
    height: 300,
    selectedDate,
    onClick: (tradeDate) => selectBacktestDate(tradeDate),
    series: [
      { name: 'Drawdown', values: dailyItems.map((item) => item.drawdown), color: CHART_COLORS.danger },
      { name: 'IC', values: dailyItems.map((item) => item.ic), color: CHART_COLORS.accent },
      { name: 'RankIC', values: dailyItems.map((item) => item.rank_ic), color: CHART_COLORS.neutral, dash: '5 4' },
    ],
    yAxisTitle: 'Ratio / IC',
  });
  renderBacktestGroupReturns();
}

function renderBacktestDailyTable() {
  const rows = state.backtest.daily;
  renderDataTable('backtest-daily-table', rows, [
    {
      id: 'trade_date',
      key: 'trade_date',
      label: 'Trade Date',
      render: (row) => renderTradeDateLink(row.trade_date),
    },
    {
      key: 'equity',
      label: 'Equity',
      render: (row) => formatNumber(row.equity, 0),
      sortValue: (row) => toNumber(row.equity),
    },
    {
      key: 'daily_return',
      label: 'Daily Return',
      render: (row) => formatPercent(row.daily_return),
      sortValue: (row) => toNumber(row.daily_return),
    },
    {
      key: 'drawdown',
      label: 'Drawdown',
      render: (row) => formatPercent(row.drawdown),
      sortValue: (row) => toNumber(row.drawdown),
    },
    {
      key: 'turnover',
      label: 'Turnover',
      render: (row) => formatNumber(row.turnover, 0),
      sortValue: (row) => toNumber(row.turnover),
    },
    {
      key: 'ic',
      label: 'IC',
      render: (row) => formatNumber(row.ic, 4),
      sortValue: (row) => toNumber(row.ic),
    },
    {
      key: 'rank_ic',
      label: 'RankIC',
      render: (row) => formatNumber(row.rank_ic, 4),
      sortValue: (row) => toNumber(row.rank_ic),
    },
    {
      key: 'trade_count',
      label: 'Trades',
      render: (row) => formatNumber(row.trade_count, 0),
      sortValue: (row) => toNumber(row.trade_count),
    },
    {
      id: 'replay',
      label: 'Replay',
      sortable: false,
      render: (row) => `<button type="button" class="action-link" onclick="jumpToReplay('${quoteJsString(row.trade_date)}', '${quoteJsString(state.backtest.selectedInstrument || state.context.instrumentId || '')}')">Open Replay</button>`,
      filterValue: () => '',
    },
  ], {
    tableKey: 'backtest-daily',
    selectedValue: state.backtest.selectedDate,
    selectedRowId: (row) => row.trade_date,
    onRowClick: (row) => selectBacktestDate(row.trade_date),
    emptyMessage: 'No daily drill-down data',
  });
}

function buildBacktestSelectionSummary(day) {
  if (!day) return '<div class="empty">Choose a date from the chart or daily table.</div>';
  return makeStackItems([
    { label: 'Strategy Equity', value: formatNumber(day.equity, 0) },
    { label: 'Daily Return', value: formatPercent(day.daily_return) },
    { label: 'Drawdown', value: formatPercent(day.drawdown) },
    { label: 'Turnover', value: formatNumber(day.turnover, 0) },
    { label: 'IC / RankIC', value: `${formatNumber(day.ic, 4)} / ${formatNumber(day.rank_ic, 4)}` },
    { label: 'Trade Count', value: formatNumber(day.trade_count, 0) },
  ]);
}

function computeContributorRows(orders) {
  const grouped = new Map();
  (orders || []).forEach((row) => {
    const instrumentId = row.symbol || row.instrument_id || '-';
    const quantity = toNumber(row.filled_amount || row.quantity || row.amount) || 0;
    const price = toNumber(row.deal_price || row.price) || 0;
    const tradedValue = Math.abs(quantity * price);
    const current = grouped.get(instrumentId) || { instrument_id: instrumentId, orders: 0, traded_value: 0, buy_qty: 0, sell_qty: 0 };
    current.orders += 1;
    current.traded_value += tradedValue;
    if (normalizeTradeSide(row.side) === 'sell') current.sell_qty += quantity;
    else current.buy_qty += quantity;
    grouped.set(instrumentId, current);
  });
  return Array.from(grouped.values()).sort((left, right) => right.traded_value - left.traded_value);
}

function renderBacktestContributors(orders) {
  const contributors = computeContributorRows(orders).slice(0, 6);
  const root = byId('backtest-contributors');
  if (!contributors.length) {
    root.innerHTML = '<div class="empty">No per-instrument contributors yet. Current UI derives them from order notional.</div>';
    return;
  }
  root.innerHTML = contributors.map((row) => `
    <div class="stack-item">
      <div>
        <span>Instrument</span>
        <strong>${renderInstrumentLink(row.instrument_id, state.backtest.selectedDate)}</strong>
      </div>
      <div>
        <span>Traded Value</span>
        <strong>${formatNumber(row.traded_value, 0)}</strong>
      </div>
    </div>
  `).join('');
}

function renderBacktestContextMeta(orders) {
  const root = byId('backtest-context-meta');
  const buyCount = (orders || []).filter((row) => normalizeTradeSide(row.side) !== 'sell').length;
  const sellCount = (orders || []).filter((row) => normalizeTradeSide(row.side) === 'sell').length;
  const tradedValue = (orders || []).reduce((sum, row) => {
    const quantity = toNumber(row.filled_amount || row.quantity || row.amount) || 0;
    const price = toNumber(row.deal_price || row.price) || 0;
    return sum + Math.abs(quantity * price);
  }, 0);
  root.innerHTML = [
    { label: 'Orders', value: String((orders || []).length) },
    { label: 'Buy / Sell', value: `${buyCount} / ${sellCount}` },
    { label: 'Gross Traded', value: formatNumber(tradedValue, 0) },
  ].map((item) => `
    <div class="kv-item">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
    </div>
  `).join('');
}

function renderBacktestOrdersTable(orders) {
  renderDataTable('backtest-orders-table', orders || [], [
    {
      key: 'date',
      label: 'Date',
      render: (row) => renderTradeDateLink(toDateLabel(row.date || state.backtest.selectedDate)),
      sortValue: (row) => toDateLabel(row.date || state.backtest.selectedDate),
    },
    {
      id: 'instrument',
      label: 'Instrument',
      render: (row) => renderInstrumentLink(row.symbol || row.instrument_id, toDateLabel(row.date || state.backtest.selectedDate)),
      sortValue: (row) => row.symbol || row.instrument_id,
      filterValue: (row) => row.symbol || row.instrument_id,
    },
    {
      key: 'side',
      label: 'Side',
      render: (row) => makeBadge(row.side || '-', normalizeTradeSide(row.side) === 'sell' ? 'warning' : 'success'),
      sortValue: (row) => row.side,
    },
    {
      key: 'filled_amount',
      label: 'Filled Qty',
      render: (row) => formatNumber(row.filled_amount || row.quantity || row.amount, 0),
      sortValue: (row) => toNumber(row.filled_amount || row.quantity || row.amount),
    },
    {
      key: 'deal_price',
      label: 'Deal Price',
      render: (row) => formatNumber(row.deal_price || row.price, 2),
      sortValue: (row) => toNumber(row.deal_price || row.price),
    },
    {
      key: 'status',
      label: 'Status',
      render: (row) => makeBadge(row.status || '-', row.status && String(row.status).toLowerCase().includes('reject') ? 'danger' : 'neutral'),
      sortValue: (row) => row.status,
    },
    {
      id: 'case',
      label: 'Case',
      sortable: false,
      render: (row) => `<button type="button" class="action-link" onclick="jumpToCase('${quoteJsString(row.symbol || row.instrument_id)}', '${quoteJsString(toDateLabel(row.date || state.backtest.selectedDate))}')">Open Case</button>`,
      filterValue: () => '',
    },
  ], {
    tableKey: 'backtest-orders',
    selectedValue: state.backtest.selectedInstrument,
    selectedRowId: (row) => row.symbol || row.instrument_id,
    onRowClick: (row) => selectBacktestInstrument(row.symbol || row.instrument_id),
    emptyMessage: 'Select a trade date to load orders',
  });
}

function renderBacktestContextLinks() {
  const selectedDate = state.backtest.selectedDate;
  const orders = state.backtest.ordersByDate.get(selectedDate) || [];
  const primaryInstrument = state.backtest.selectedInstrument || computeContributorRows(orders)[0]?.instrument_id || state.context.instrumentId || '';
  byId('backtest-context-links').innerHTML = [
    renderActionButton('Open Replay', `jumpToReplay('${quoteJsString(selectedDate || state.context.tradeDate || '')}', '${quoteJsString(primaryInstrument)}')`, 'secondary'),
    renderActionButton('Open Case', `jumpToCase('${quoteJsString(primaryInstrument)}', '${quoteJsString(selectedDate || state.context.tradeDate || '')}')`, 'primary'),
    renderActionButton('Copy Date', `copyText('${quoteJsString(selectedDate || '')}')`, 'ghost'),
  ].join('');
}

function renderBacktestRunContext() {
  const summary = state.backtest.summary;
  byId('backtest-run-context').innerHTML = summary
    ? `${renderRunLink(summary.run_id, summary.display_label || summary.run_id)} ${makeBadge(summary.feature_set || '-', 'neutral')}`
    : '<span class="muted-text">No run loaded</span>';
}

async function loadBacktestOrders(tradeDate, { force = false } = {}) {
  const runId = state.context.runId || byId('backtest-run-select').value;
  if (!runId || !tradeDate) return [];
  if (!force && state.backtest.ordersByDate.has(tradeDate)) {
    const orders = state.backtest.ordersByDate.get(tradeDate) || [];
    renderBacktestContextMeta(orders);
    renderBacktestOrdersTable(orders);
    renderBacktestContributors(orders);
    renderBacktestContextLinks();
    return orders;
  }
  try {
    const payload = await getJson(`/api/backtest-runs/${runId}/orders?trade_date=${tradeDate}`);
    const items = unwrapItems(payload);
    state.backtest.ordersByDate.set(tradeDate, items);
    renderBacktestContextMeta(items);
    renderBacktestOrdersTable(items);
    renderBacktestContributors(items);
    renderBacktestContextLinks();
    return items;
  } catch (error) {
    byId('backtest-orders-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('backtest-contributors').innerHTML = '<div class="empty">Contributor list unavailable</div>';
    return [];
  }
}

function selectBacktestInstrument(instrumentId) {
  state.backtest.selectedInstrument = instrumentId || '';
  updateContext({ instrumentId: instrumentId || state.context.instrumentId });
  const orders = state.backtest.ordersByDate.get(state.backtest.selectedDate) || [];
  renderBacktestOrdersTable(orders);
  renderBacktestContextLinks();
}

function selectBacktestDate(tradeDate) {
  if (!tradeDate) return;
  state.backtest.selectedDate = tradeDate;
  updateContext({ tradeDate });
  const day = state.backtest.daily.find((item) => item.trade_date === tradeDate);
  byId('backtest-selected-date-badge').textContent = tradeDate;
  byId('backtest-selected-summary').innerHTML = buildBacktestSelectionSummary(day);
  renderBacktestDailyTable();
  renderBacktestCharts();
  loadBacktestOrders(tradeDate);
}

async function loadBacktest() {
  try {
    updateContext(readContextFromInputs());
    const runsPayload = await getJson('/api/backtest-runs?limit=50', { useCache: false });
    state.backtestRuns = unwrapItems(runsPayload);
    renderBacktestRunOptions(state.backtestRuns);
    state.latestBacktestRunId = state.backtestRuns[0]?.run_id || '';

    let runId = state.context.runId || byId('backtest-run-select').value;
    if (!state.backtestRuns.some((item) => item.run_id === runId)) {
      runId = state.latestBacktestRunId || state.backtestRuns[0]?.run_id || '';
    }
    if (!runId) {
      runId = byId('backtest-run-select').value || state.latestBacktestRunId || state.backtestRuns[0]?.run_id || '';
    }
    if (!runId) throw new Error('No backtest version available');
    updateContext({ runId }, { syncInputs: true, syncHash: true });

    const summaryPayload = await getJson(`/api/backtest-runs/${runId}/summary`, { useCache: false });
    const dailyPayload = await getJson(`/api/backtest-runs/${runId}/daily`, { useCache: false });
    let groupReturnsPayload = null;
    try {
      groupReturnsPayload = await getJson(`/api/backtest-runs/${runId}/group-returns`, { useCache: false });
    } catch (_error) {
      groupReturnsPayload = null;
    }
    const summary = unwrapData(summaryPayload);
    const dailyItems = unwrapItems(dailyPayload);
    const groupReturns = unwrapItems(groupReturnsPayload);
    state.backtest.summary = summary;
    state.backtest.daily = dailyItems;
    state.backtest.groupReturns = groupReturns;
    state.backtest.ordersByDate = new Map();
    renderParameterSummary(summary);
    renderBacktestRunContext();
    renderBacktestSignalMetrics(summary);
    summarizeBacktestMetrics(summary, dailyItems);
    renderBacktestDailyTable();

    const selectedDate = dailyItems.some((item) => item.trade_date === state.context.tradeDate)
      ? state.context.tradeDate
      : dailyItems[dailyItems.length - 1]?.trade_date || '';
    if (!selectedDate) throw new Error('No daily backtest payload found');
    selectBacktestDate(selectedDate);
  } catch (error) {
    renderParameterSummary(null);
    renderChartError('backtest-equity-chart', error.message);
    renderChartError('backtest-diagnostics-chart', error.message);
    byId('backtest-daily-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('backtest-orders-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('backtest-contributors').innerHTML = '<div class="empty">No contributors</div>';
    byId('backtest-selected-summary').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('backtest-signal-metrics').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    renderChartError('backtest-group-returns-chart', error.message);
    byId('backtest-group-returns-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function filterFeatureRows(features, searchValue) {
  const needle = String(searchValue || '').trim().toLowerCase();
  return Object.entries(features || {})
    .map(([feature_name, value]) => ({ feature_name, value }))
    .filter((row) => !needle || row.feature_name.toLowerCase().includes(needle))
    .sort((left, right) => left.feature_name.localeCompare(right.feature_name));
}

function renderCaseFeatureTable(features) {
  const rows = filterFeatureRows(features, '');
  renderDataTable('case-feature-table', rows, [
    {
      key: 'feature_name',
      label: 'Feature ID',
      render: (row) => renderFeatureLink(row.feature_name, state.context.instrumentId, state.context.tradeDate),
      filterValue: (row) => row.feature_name,
    },
    {
      key: 'value',
      label: 'Value',
      render: (row) => formatValue(row.value),
      sortValue: (row) => toNumber(row.value) ?? String(row.value || ''),
    },
  ], {
    tableKey: 'case-feature-table',
    selectedValue: state.context.featureId,
    selectedRowId: (row) => row.feature_name,
    onRowClick: (row) => jumpToFeature(row.feature_name, state.context.instrumentId, state.context.tradeDate),
    emptyMessage: 'No feature snapshot',
  });
}

function renderCaseOrdersTable(orderRows) {
  renderDataTable('case-orders-table', orderRows, [
    { key: 'record_type', label: 'Type', render: (row) => makeBadge(row.record_type || '-', row.record_type && row.record_type.includes('Sell') ? 'warning' : 'neutral') },
    {
      key: 'date',
      label: 'Date',
      render: (row) => renderTradeDateLink(toDateLabel(row.date || row.trade_date || row.as_of_date || state.context.tradeDate)),
      sortValue: (row) => toDateLabel(row.date || row.trade_date || row.as_of_date || state.context.tradeDate),
    },
    {
      key: 'instrument_id',
      label: 'Instrument',
      render: (row) => renderInstrumentLink(row.instrument_id || row.symbol, toDateLabel(row.date || row.trade_date || state.context.tradeDate)),
      filterValue: (row) => row.instrument_id || row.symbol,
    },
    {
      key: 'quantity',
      label: 'Qty',
      render: (row) => formatNumber(row.quantity || row.filled_amount || row.amount, 0),
      sortValue: (row) => toNumber(row.quantity || row.filled_amount || row.amount),
    },
    {
      key: 'side',
      label: 'Side',
      render: (row) => makeBadge(row.side || row.status || '-', normalizeTradeSide(row.side) === 'sell' ? 'warning' : 'success'),
      sortValue: (row) => row.side || row.status,
    },
    {
      key: 'price',
      label: 'Price',
      render: (row) => formatNumber(row.deal_price || row.price, 2),
      sortValue: (row) => toNumber(row.deal_price || row.price),
    },
    {
      key: 'status',
      label: 'Status',
      render: (row) => makeBadge(row.status || row.note || '-', row.status && String(row.status).toLowerCase().includes('planned') ? 'warning' : 'neutral'),
      sortValue: (row) => row.status || row.note,
    },
  ], {
    tableKey: 'case-orders-table',
    emptyMessage: 'No positions / orders for this case',
  });
}

function pickSignalField(signalSnapshot, keys) {
  for (const key of keys) {
    if (signalSnapshot && signalSnapshot[key] !== undefined && signalSnapshot[key] !== null && signalSnapshot[key] !== '') {
      return signalSnapshot[key];
    }
  }
  return null;
}

function buildCaseExplanation(payload, backtestTrades) {
  const signalSnapshot = payload.signal_snapshot || {};
  const score = pickSignalField(signalSnapshot, ['adjusted_score', 'raw_score', 'score']);
  const rank = pickSignalField(signalSnapshot, ['rank', 'score_rank']);
  const position = (payload.positions || [])[0];
  const order = (backtestTrades || payload.orders || [])[0];
  const parts = [];
  parts.push(`Instrument ${payload.instrument_id} is anchored to trade_date ${payload.trade_date} and signal_date ${payload.signal_date || payload.trade_date}.`);
  if (score !== null) parts.push(`Latest signal score is ${formatValue(score)}${rank !== null ? ` with rank ${formatValue(rank)}` : ''}.`);
  if (position) parts.push(`Previous position snapshot shows quantity ${formatNumber(position.quantity || position.amount || position.total_amount, 0)} at price ${formatNumber(position.price, 2)}.`);
  if (order) parts.push(`Execution trace shows ${escapeHtml(String(order.side || 'planned'))} ${formatNumber(order.quantity || order.filled_amount || order.amount, 0)} around ${formatNumber(order.deal_price || order.price, 2)}.`);
  if (!order) parts.push('No matched replay/backtest order was found for the current context, so execution impact stays as a placeholder.');
  return parts.map((item) => `<p>${item}</p>`).join('');
}

function renderCaseLoop(payload, backtestTrades) {
  const signalSnapshot = payload.signal_snapshot || {};
  const score = pickSignalField(signalSnapshot, ['adjusted_score', 'raw_score', 'score']);
  const rank = pickSignalField(signalSnapshot, ['rank', 'score_rank']);
  const positionQty = (payload.positions || []).reduce((sum, row) => sum + (toNumber(row.quantity || row.amount || row.total_amount) || 0), 0);
  const featureCount = Object.keys(state.caseFeatureSnapshot || {}).length;
  byId('case-loop-summary').innerHTML = [
    { stage: 'Feature', value: featureCount, note: 'snapshot values loaded' },
    { stage: 'Signal', value: score === null ? 'n/a' : formatValue(score), note: rank === null ? 'rank unavailable' : `rank ${formatValue(rank)}` },
    { stage: 'Position', value: formatNumber(positionQty, 0), note: 'previous position qty' },
    { stage: 'Orders', value: String((payload.orders || []).length + (backtestTrades || []).length), note: 'replay + backtest fills' },
    { stage: 'Explain', value: payload.trade_date, note: 'current loop date' },
  ].map((item) => `
    <div class="pipeline-card">
      <span>${escapeHtml(item.stage)}</span>
      <strong>${escapeHtml(String(item.value))}</strong>
      <em>${escapeHtml(item.note)}</em>
    </div>
  `).join('');
}

function renderCaseSignalSummary(payload, backtestTrades) {
  const signalSnapshot = payload.signal_snapshot || {};
  const summaryItems = [
    { label: 'Run', value: renderRunLink(state.context.runId || payload.run_id || '') },
    { label: 'Trade Date', value: renderTradeDateLink(payload.trade_date) },
    { label: 'Signal Date', value: payload.signal_date ? renderTradeDateLink(payload.signal_date) : '-' },
    { label: 'Benchmark', value: escapeHtml(payload.benchmark_label || 'CSI300') },
    { label: 'Replay Orders', value: String((payload.orders || []).length) },
    { label: 'Backtest Fills', value: String((backtestTrades || []).length) },
  ];
  byId('case-signal-summary').innerHTML = makeStackItems(summaryItems);
}

function renderCaseLinks(payload) {
  const links = [
    renderActionButton('Open Replay', `jumpToReplay('${quoteJsString(payload.trade_date)}', '${quoteJsString(payload.instrument_id)}')`, 'secondary'),
    renderActionButton('Open Feature Health', `jumpToFeature('${quoteJsString(state.context.featureId || Object.keys(state.caseFeatureSnapshot || {})[0] || '')}', '${quoteJsString(payload.instrument_id)}', '${quoteJsString(payload.trade_date)}')`, 'ghost'),
    renderActionButton('Open Backtest', `jumpToBacktest('${quoteJsString(state.context.runId || payload.run_id || '')}', '${quoteJsString(payload.trade_date)}')`, 'ghost'),
  ];
  (payload.links || []).forEach((link) => {
    links.push(renderActionButton(`Copy ${link.label}`, `copyText('${quoteJsString(link.target)}')`, 'ghost'));
  });
  byId('case-links').innerHTML = links.join('');
}

async function loadCase() {
  try {
    updateContext(readContextFromInputs());
    const executionDate = state.context.tradeDate;
    const instrumentId = state.context.instrumentId;
    const priceMode = state.context.priceMode;
    const backtestRunId = state.context.runId;
    if (!executionDate || !instrumentId) throw new Error('Trade date and instrument are required');
    const casePayload = await getJson(`/api/cases/${executionDate}:${instrumentId}:${priceMode}`, { useCache: false });
    const payload = unwrapData(casePayload);
    let backtestTrades = [];
    if (backtestRunId) {
      const tradePayload = await getJson(`/api/backtest-runs/${backtestRunId}/orders?instrument_id=${encodeURIComponent(instrumentId)}&limit=5000`, { useCache: false });
      backtestTrades = unwrapItems(tradePayload);
    }
    state.caseData = payload;

    const priceKeys = priceMode === 'fq'
      ? { open: 'adj_open', high: 'adj_high', low: 'adj_low', close: 'adj_close' }
      : { open: 'open', high: 'high', low: 'low', close: 'close' };
    const chartBars = (payload.bars || []).map((item) => ({
      trade_date: toDateLabel(item.trade_date),
      open: item[priceKeys.open],
      high: item[priceKeys.high],
      low: item[priceKeys.low],
      close: item[priceKeys.close],
      volume: item.volume,
    }));
    const replayOrderMarkers = (payload.orders || []).map((item) => ({
      trade_date: payload.trade_date,
      value: toNumber(item.price),
      side: item.side,
      quantity: item.quantity,
      source: 'order',
    }));
    const tradeMarkers = [...backtestTrades.map((item) => ({
      trade_date: item.date,
      value: toNumber(item.deal_price),
      side: item.side,
      quantity: item.filled_amount,
      source: 'fill',
    })), ...replayOrderMarkers];

    renderCandlestickChart('case-bars-chart', chartBars, tradeMarkers, payload.instrument_id, {
      selectedDate: toDateLabel(payload.trade_date),
      annotations: payload.annotations || [],
    });
    const dates = chartBars.map((item) => item.trade_date);
    const benchmarkValues = alignSeriesByDate(dates, payload.benchmark_bars || [], 'close');
    const secondaryBenchmarkValues = alignSeriesByDate(dates, payload.secondary_benchmark_bars || [], 'close');
    renderSeriesChart('case-relative-chart', {
      dates,
      title: 'Relative to 100',
      height: 290,
      selectedDate: payload.trade_date,
      series: [
        { name: payload.instrument_id, values: rebaseSeries(chartBars.map((item) => item.close)), color: CHART_COLORS.strategy },
        { name: payload.benchmark_label || 'CSI300', values: rebaseSeries(benchmarkValues), color: CHART_COLORS.benchmark, dash: '6 4' },
        { name: payload.secondary_benchmark_label || 'SSE', values: rebaseSeries(secondaryBenchmarkValues), color: CHART_COLORS.neutral, dash: '2 5' },
      ],
      yAxisTitle: 'Rebased',
    });
    renderVolumeChart('case-volume-chart', chartBars, { selectedDate: payload.trade_date });

    const loadedStart = chartBars[0]?.trade_date || '';
    const loadedEnd = chartBars[chartBars.length - 1]?.trade_date || '';
    byId('case-meta').innerHTML = [
      renderInstrumentLink(payload.instrument_id, payload.trade_date),
      makeBadge(payload.price_mode || state.context.priceMode),
      renderTradeDateLink(toDateLabel(payload.trade_date)),
      loadedStart && loadedEnd ? `<span class="muted-text">${escapeHtml(loadedStart)} -> ${escapeHtml(loadedEnd)}</span>` : '',
      renderActionButton('Raw', `setCasePriceMode('raw')`, priceMode === 'raw' ? 'primary' : 'ghost'),
      renderActionButton('FQ', `setCasePriceMode('fq')`, priceMode === 'fq' ? 'primary' : 'ghost'),
    ].filter(Boolean).join(' ');
    byId('case-signal').textContent = JSON.stringify({ signal_snapshot: payload.signal_snapshot, trade_markers: backtestTrades, links: payload.links }, null, 2);
    state.caseFeatureSnapshot = payload.feature_snapshot.features || {};
    byId('case-feature-count').textContent = `${Object.keys(state.caseFeatureSnapshot).length} features`;
    renderCaseFeatureTable(state.caseFeatureSnapshot);
    renderCaseLoop(payload, backtestTrades);
    renderCaseSignalSummary(payload, backtestTrades);
    renderCaseLinks(payload);
    byId('case-explanation').innerHTML = buildCaseExplanation(payload, backtestTrades);

    const orderRows = [
      ...(payload.positions || []).map((item) => ({ ...item, record_type: 'Previous Position', date: item.as_of_date || payload.trade_date, instrument_id: item.instrument_id || item.symbol })),
      ...(payload.orders || []).map((item) => ({ ...item, record_type: 'Replay Order', date: payload.trade_date, instrument_id: item.instrument_id || item.symbol || payload.instrument_id })),
      ...backtestTrades.map((item) => ({ ...item, record_type: 'Backtest Fill', instrument_id: item.symbol || item.instrument_id || payload.instrument_id })),
    ];
    renderCaseOrdersTable(orderRows);
  } catch (error) {
    byId('case-signal').textContent = error.message;
    byId('case-feature-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('case-orders-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('case-explanation').innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    renderChartError('case-bars-chart', error.message);
    renderChartError('case-relative-chart', error.message);
    renderChartError('case-volume-chart', error.message);
  }
}

function syncFeatureSelectionInput() {
  byId('feature-names').value = Array.from(state.selectedFeatureNames).join(', ');
}

function renderFeatureSelectionChips() {
  const root = byId('feature-selection-chips');
  const selected = Array.from(state.selectedFeatureNames);
  if (!selected.length) {
    root.innerHTML = '<div class="empty">No selected features yet. Filter the registry, then choose Use Visible.</div>';
    return;
  }
  root.innerHTML = selected.map((featureName) => [
    `<span class="chip">${escapeHtml(featureName)}`,
    `<button type="button" onclick="event.stopPropagation(); toggleFeatureSelection('${quoteJsString(featureName)}')">x</button>`,
    '</span>',
  ].join('')).join('');
}

function getFilteredFeatureRegistry() {
  const search = byId('feature-registry-search').value.trim().toLowerCase();
  const sourceFilter = byId('feature-source-filter').value;
  return state.featureRegistry.filter((item) => {
    if (sourceFilter !== 'all' && item.source_layer !== sourceFilter) return false;
    if (!search) return true;
    const haystack = [
      item.feature_name,
      item.group_name,
      item.source_layer,
      item.description,
      ...(item.tags || []),
    ].join(' ').toLowerCase();
    return haystack.includes(search);
  });
}

function updateFeatureSelectionSummary() {
  const filteredCount = getFilteredFeatureRegistry().length;
  byId('feature-selection-summary').textContent = `Selected ${state.selectedFeatureNames.size}/${MAX_FEATURE_SELECTION} features. Registry matches: ${filteredCount}. The current detail panel follows context feature_id when available.`;
}

function updateFeatureSourceFilterOptions() {
  const select = byId('feature-source-filter');
  const currentValue = select.value || 'all';
  const options = ['all', ...Array.from(new Set(state.featureRegistry.map((item) => item.source_layer))).sort()];
  select.innerHTML = options.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
  select.value = options.includes(currentValue) ? currentValue : 'all';
}

function renderFeatureRegistry() {
  const filtered = getFilteredFeatureRegistry();
  byId('feature-registry-count').textContent = `${filtered.length} / ${state.featureRegistry.length}`;
  const rows = filtered.slice().sort((left, right) => {
    const leftSelected = state.selectedFeatureNames.has(left.feature_name) ? -1 : 0;
    const rightSelected = state.selectedFeatureNames.has(right.feature_name) ? -1 : 0;
    if (leftSelected !== rightSelected) return leftSelected - rightSelected;
    return left.feature_name.localeCompare(right.feature_name);
  });
  renderDataTable('feature-registry-table', rows, [
    {
      id: 'select',
      label: 'Select',
      sortable: false,
      render: (row) => {
        const active = state.selectedFeatureNames.has(row.feature_name);
        return `<button type="button" class="registry-action ${active ? 'active' : ''}" onclick="event.stopPropagation(); toggleFeatureSelection('${quoteJsString(row.feature_name)}')">${active ? 'Added' : 'Add'}</button>`;
      },
      filterValue: () => '',
    },
    {
      key: 'feature_name',
      label: 'Feature ID',
      render: (row) => renderFeatureLink(row.feature_name, state.context.instrumentId, state.context.tradeDate),
      filterValue: (row) => row.feature_name,
    },
    { key: 'group_name', label: 'Group' },
    { key: 'source_layer', label: 'Source', render: (row) => makeBadge(row.source_layer || '-', 'neutral') },
    { key: 'supports_snapshot', label: 'Snapshot', render: (row) => makeBadge(row.supports_snapshot ? 'yes' : 'no', row.supports_snapshot ? 'success' : 'warning') },
    { key: 'tags', label: 'Tags', render: (row) => escapeHtml((row.tags || []).slice(0, 4).join(', ') || '-') },
    { key: 'description', label: 'Description' },
  ], {
    tableKey: 'feature-registry',
    selectedValue: state.featureData.selectedFeatureName,
    selectedRowId: (row) => row.feature_name,
    onRowClick: (row) => jumpToFeature(row.feature_name, state.context.instrumentId, state.context.tradeDate),
    emptyMessage: 'No feature registry entries',
  });
  updateFeatureSelectionSummary();
}

function parseFeatureSelectionFromInput() {
  const value = byId('feature-names').value;
  const names = value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean);
  state.selectedFeatureNames = new Set(names.slice(0, MAX_FEATURE_SELECTION));
  syncFeatureSelectionInput();
  renderFeatureSelectionChips();
  renderFeatureRegistry();
}

function ensureFeatureSelectionSeed() {
  if (state.selectedFeatureNames.size || !state.featureRegistry.length) return;
  getFilteredFeatureRegistry().slice(0, 12).forEach((item) => state.selectedFeatureNames.add(item.feature_name));
  syncFeatureSelectionInput();
  renderFeatureSelectionChips();
  updateFeatureSelectionSummary();
}

function resolveFeatureNames() {
  parseFeatureSelectionFromInput();
  if (state.context.featureId && state.selectedFeatureNames.size < MAX_FEATURE_SELECTION) {
    state.selectedFeatureNames.add(state.context.featureId);
  }
  if (!state.selectedFeatureNames.size) ensureFeatureSelectionSeed();
  syncFeatureSelectionInput();
  renderFeatureSelectionChips();
  return Array.from(state.selectedFeatureNames);
}

window.toggleFeatureSelection = function toggleFeatureSelection(featureName) {
  if (state.selectedFeatureNames.has(featureName)) {
    state.selectedFeatureNames.delete(featureName);
  } else if (state.selectedFeatureNames.size < MAX_FEATURE_SELECTION) {
    state.selectedFeatureNames.add(featureName);
  }
  syncFeatureSelectionInput();
  renderFeatureSelectionChips();
  renderFeatureRegistry();
};

window.selectVisibleFeatures = function selectVisibleFeatures() {
  const selected = new Set(state.selectedFeatureNames);
  getFilteredFeatureRegistry().forEach((item) => {
    if (selected.size < MAX_FEATURE_SELECTION) selected.add(item.feature_name);
  });
  state.selectedFeatureNames = selected;
  syncFeatureSelectionInput();
  renderFeatureSelectionChips();
  renderFeatureRegistry();
};

window.clearFeatureSelection = function clearFeatureSelection() {
  state.selectedFeatureNames = new Set();
  syncFeatureSelectionInput();
  renderFeatureSelectionChips();
  renderFeatureRegistry();
};

async function loadFeatureRegistry() {
  if (state.featureRegistry.length) {
    renderFeatureRegistry();
    return;
  }
  const registryPayload = await getJson('/api/feature-registry');
  state.featureRegistry = unwrapItems(registryPayload);
  updateFeatureSourceFilterOptions();
  ensureFeatureSelectionSeed();
  renderFeatureSelectionChips();
  renderFeatureRegistry();
}

function renderFeatureSummaryRow(health) {
  const warningCount = (health.features || []).filter((item) => item.status !== 'ok').length;
  byId('feature-health-summary-row').innerHTML = [
    { label: 'Feature Count', value: String(health.feature_count || 0), note: 'selected health rows' },
    { label: 'Instrument Coverage', value: String(health.instrument_count || 0), note: 'loaded qlib rows' },
    { label: 'Overall Missing', value: formatPercent(health.overall_missing_ratio), note: 'health summary' },
    { label: 'Warnings', value: String((health.warnings || []).length), note: 'top-level warnings' },
    { label: 'Blockers', value: String((health.blockers || []).length), note: 'must fix first' },
    { label: 'Problem Queue', value: String(warningCount), note: 'feature rows not ok' },
  ].map((item) => `
    <article class="panel stat-card">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      <small>${escapeHtml(item.note)}</small>
    </article>
  `).join('');
}

function renderFeatureProblemQueue(health) {
  const items = [];
  (health.blockers || []).slice(0, 4).forEach((text) => items.push({ tone: 'danger', title: 'Blocker', text }));
  (health.warnings || []).slice(0, 4).forEach((text) => items.push({ tone: 'warning', title: 'Warning', text }));
  (health.features || []).filter((item) => item.status !== 'ok').slice(0, 6).forEach((item) => {
    items.push({ tone: 'warning', title: item.feature_name, text: `coverage ${formatPercent(item.coverage_ratio)}, nan ${formatPercent(item.nan_ratio)}` });
  });
  byId('feature-problem-queue').innerHTML = items.length
    ? items.map((item) => `<div class="problem-item ${item.tone}"><span>${escapeHtml(item.title)}</span><strong>${escapeHtml(item.text)}</strong></div>`).join('')
    : '<div class="empty">No blockers or warnings in current feature health payload.</div>';
}

function selectFeatureDetail(featureName) {
  state.featureData.selectedFeatureName = featureName || '';
  updateContext({ featureId: featureName || state.context.featureId });
  renderFeatureHealthTable();
  renderFeatureDetail();
}

function renderFeatureHealthTable() {
  const health = state.featureData.health;
  renderDataTable('feature-health-table', health?.features || [], [
    {
      key: 'feature_name',
      label: 'Feature ID',
      render: (row) => renderFeatureLink(row.feature_name, state.context.instrumentId, state.context.tradeDate),
      filterValue: (row) => row.feature_name,
    },
    { key: 'coverage_ratio', label: 'Coverage', render: (row) => formatPercent(row.coverage_ratio), sortValue: (row) => toNumber(row.coverage_ratio) },
    { key: 'nan_ratio', label: 'NaN', render: (row) => formatPercent(row.nan_ratio), sortValue: (row) => toNumber(row.nan_ratio) },
    { key: 'inf_ratio', label: 'Inf', render: (row) => formatPercent(row.inf_ratio), sortValue: (row) => toNumber(row.inf_ratio) },
    { key: 'status', label: 'Status', render: (row) => makeBadge(row.status, row.status === 'ok' ? 'success' : 'warning') },
  ], {
    tableKey: 'feature-health-table',
    selectedValue: state.featureData.selectedFeatureName,
    selectedRowId: (row) => row.feature_name,
    onRowClick: (row) => selectFeatureDetail(row.feature_name),
    emptyMessage: 'No feature health rows',
  });
}

function renderFeatureSnapshotDetailTable(selectedFeatureName) {
  const value = state.featureSnapshot[selectedFeatureName];
  renderDataTable('feature-snapshot-table', selectedFeatureName ? [{
    feature_name: selectedFeatureName,
    instrument_id: state.context.instrumentId,
    trade_date: state.context.tradeDate,
    value,
  }] : [], [
    { key: 'feature_name', label: 'Feature ID', render: (row) => renderFeatureLink(row.feature_name, row.instrument_id, row.trade_date) },
    { key: 'instrument_id', label: 'Instrument', render: (row) => renderInstrumentLink(row.instrument_id, row.trade_date) },
    { key: 'trade_date', label: 'Trade Date', render: (row) => renderTradeDateLink(row.trade_date) },
    { key: 'value', label: 'Snapshot Value', render: (row) => formatValue(row.value), sortValue: (row) => toNumber(row.value) ?? String(row.value || '') },
  ], {
    tableKey: 'feature-snapshot-detail',
    hideToolbar: true,
    emptyMessage: 'Select a feature to inspect snapshot value',
  });
}

function renderFeatureDetail() {
  const selectedFeatureName = state.featureData.selectedFeatureName;
  const health = state.featureData.health;
  if (!selectedFeatureName || !health) {
    byId('feature-selected-summary').innerHTML = '<div class="empty">Select a feature from the left health table.</div>';
    byId('feature-detail-registry').innerHTML = '<div class="empty">Registry detail will appear here.</div>';
    byId('feature-distribution').innerHTML = 'API gap: no distribution section is exposed by the current feature-health endpoint.';
    byId('feature-drift').innerHTML = 'API gap: no drift summary is exposed by the current feature-health endpoint.';
    renderFeatureSnapshotDetailTable('');
    return;
  }
  const entry = (health.features || []).find((item) => item.feature_name === selectedFeatureName) || {};
  const registryEntry = state.featureRegistry.find((item) => item.feature_name === selectedFeatureName) || {};
  byId('feature-selected-summary').innerHTML = makeStackItems([
    { label: 'Feature ID', value: renderFeatureLink(selectedFeatureName, state.context.instrumentId, state.context.tradeDate) },
    { label: 'Coverage', value: formatPercent(entry.coverage_ratio) },
    { label: 'NaN / Inf', value: `${formatPercent(entry.nan_ratio)} / ${formatPercent(entry.inf_ratio)}` },
    { label: 'Status', value: makeBadge(entry.status || '-', entry.status === 'ok' ? 'success' : 'warning') },
    { label: 'Snapshot Value', value: formatValue(state.featureSnapshot[selectedFeatureName]) },
  ]);
  byId('feature-detail-registry').innerHTML = makeStackItems([
    { label: 'Group', value: escapeHtml(registryEntry.group_name || '-') },
    { label: 'Source', value: makeBadge(registryEntry.source_layer || '-', 'neutral') },
    { label: 'Description', value: escapeHtml(registryEntry.description || 'No description') },
    { label: 'Dependencies', value: escapeHtml((registryEntry.dependencies || []).join(', ') || 'No dependencies declared') },
    { label: 'Tags', value: escapeHtml((registryEntry.tags || []).join(', ') || '-') },
  ]);
  byId('feature-distribution').innerHTML = `Current API only returns point-in-time health ratios. Distribution chart for <strong>${escapeHtml(selectedFeatureName)}</strong> is a backend data gap.`;
  byId('feature-drift').innerHTML = `Current API does not provide rolling drift windows for <strong>${escapeHtml(selectedFeatureName)}</strong>. Add a dedicated endpoint before replacing this placeholder.`;
  renderFeatureSnapshotDetailTable(selectedFeatureName);
}

async function loadFeatureHealth() {
  try {
    updateContext(readContextFromInputs());
    const tradeDate = state.context.tradeDate;
    const instrumentId = state.context.instrumentId;
    if (!tradeDate || !instrumentId) throw new Error('Trade date and instrument are required');
    const featureNames = resolveFeatureNames();
    const healthParams = new URLSearchParams({ trade_date: tradeDate, universe: 'csi300' });
    featureNames.forEach((name) => healthParams.append('feature_names', name));
    const healthPayload = await getJson(`/api/feature-health?${healthParams.toString()}`, { useCache: false });
    const health = unwrapData(healthPayload);
    state.featureData.health = health;
    byId('feature-health-summary').textContent = [
      `trade_date ${health.trade_date}`,
      `feature count ${health.feature_count}`,
      `instrument coverage ${health.instrument_count}`,
      `overall missing ${formatPercent(health.overall_missing_ratio)}`,
    ].join(' | ');
    renderFeatureSummaryRow(health);
    renderFeatureProblemQueue(health);
    renderFeatureHealthTable();

    const snapshotParams = new URLSearchParams({ trade_date: tradeDate, instrument_id: instrumentId });
    const snapshotPayload = await getJson(`/api/feature-snapshot?${snapshotParams.toString()}`, { useCache: false });
    const snapshot = unwrapData(snapshotPayload);
    state.featureSnapshot = snapshot.features || {};

    const nextFeature = health.features.some((item) => item.feature_name === state.context.featureId)
      ? state.context.featureId
      : health.features[0]?.feature_name || featureNames[0] || '';
    selectFeatureDetail(nextFeature);
  } catch (error) {
    byId('feature-health-summary').textContent = error.message;
    byId('feature-health-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('feature-problem-queue').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('feature-selected-summary').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('feature-detail-registry').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('feature-distribution').innerHTML = escapeHtml(error.message);
    byId('feature-drift').innerHTML = escapeHtml(error.message);
    byId('feature-snapshot-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function buildReplaySummaryCards(replay) {
  const summary = replay.summary || {};
  byId('replay-summary-row').innerHTML = [
    { label: 'Intent Count', value: String(summary.intent_count || replay.final_orders.length || 0), note: 'reported by order intents' },
    { label: 'Previous Positions', value: String(replay.previous_positions.length || 0), note: 'account carry-over' },
    { label: 'Candidates', value: String(replay.scored_candidates.length || 0), note: 'ranking payload' },
    { label: 'Selected Targets', value: String(replay.selected_targets.length || 0), note: 'filtered output' },
    { label: 'Final Orders', value: String(replay.final_orders.length || 0), note: 'execution list' },
    { label: 'Account', value: replay.account_name || state.context.account || '-', note: replay.execution_date || replay.trade_date || '-' },
  ].map((item) => `
    <article class="panel stat-card">
      <span>${escapeHtml(item.label)}</span>
      <strong>${escapeHtml(item.value)}</strong>
      <small>${escapeHtml(item.note)}</small>
    </article>
  `).join('');
}

function buildReplayPipeline(replay) {
  const summary = replay.summary || {};
  const modelInfo = summary.model_info || {};
  byId('replay-pipeline').innerHTML = [
    {
      stage: 'Universe',
      value: modelInfo.universe_size ?? 'n/a',
      note: modelInfo.universe_size ? 'reported by backend' : 'placeholder: universe size not in current payload',
    },
    {
      stage: 'Ranking',
      value: replay.scored_candidates.length || 0,
      note: 'candidate rows with scores',
    },
    {
      stage: 'Filtered',
      value: replay.selected_targets.length || replay.final_orders.length || 0,
      note: replay.selected_targets.length ? 'selected targets' : 'fallback to final orders count',
    },
    {
      stage: 'Portfolio',
      value: replay.previous_positions.length || 0,
      note: 'previous positions linked on right',
    },
    {
      stage: 'Orders',
      value: replay.final_orders.length || 0,
      note: 'final output for execution',
    },
  ].map((item) => `
    <div class="pipeline-card">
      <span>${escapeHtml(item.stage)}</span>
      <strong>${escapeHtml(String(item.value))}</strong>
      <em>${escapeHtml(item.note)}</em>
    </div>
  `).join('');
}

function selectReplayInstrument(instrumentId) {
  state.replayData.selectedInstrument = instrumentId || '';
  updateContext({ instrumentId: instrumentId || state.context.instrumentId });
  renderReplayTables();
  renderReplaySelection();
}

async function renderReplayInstrumentChart(replay, instrumentId) {
  const root = byId('replay-instrument-chart');
  if (!root) return;
  if (!replay || !instrumentId) {
    root.innerHTML = '<div class="empty">Select an instrument to view replay timeline.</div>';
    return;
  }
  try {
    const priceMode = state.context.priceMode || 'fq';
    const casePayload = await getJson(`/api/cases/${replay.trade_date}:${instrumentId}:${priceMode}`, { useCache: false });
    const payload = unwrapData(casePayload);
    const priceKeys = priceMode === 'fq'
      ? { open: 'adj_open', high: 'adj_high', low: 'adj_low', close: 'adj_close' }
      : { open: 'open', high: 'high', low: 'low', close: 'close' };
    const chartBars = (payload.bars || []).map((item) => ({
      trade_date: toDateLabel(item.trade_date),
      open: item[priceKeys.open],
      high: item[priceKeys.high],
      low: item[priceKeys.low],
      close: item[priceKeys.close],
      volume: item.volume,
    }));
    const replayOrderMarkers = (replay.final_orders || [])
      .filter((item) => item.instrument_id === instrumentId)
      .map((item) => ({
        trade_date: replay.trade_date,
        value: toNumber(item.price),
        side: item.side,
        quantity: item.quantity,
        source: 'order',
      }));
    const replayAnnotations = [
      { type: 'signal_date', trade_date: replay.signal_date || replay.trade_date, label: 'Signal' },
      { type: 'execution_date', trade_date: replay.execution_date || replay.trade_date, label: 'Execution' },
    ];
    renderCandlestickChart('replay-instrument-chart', chartBars, replayOrderMarkers, instrumentId, {
      selectedDate: toDateLabel(replay.trade_date),
      annotations: replayAnnotations,
    });
  } catch (error) {
    root.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderReplayTables() {
  const replay = state.replayData.payload;
  if (!replay) return;
  renderDataTable('replay-candidates-table', replay.scored_candidates || [], [
    {
      key: 'instrument_id',
      label: 'Instrument',
      render: (row) => renderInstrumentLink(row.instrument_id, replay.trade_date),
      filterValue: (row) => row.instrument_id,
    },
    { key: 'raw_score', label: 'Raw Score', render: (row) => formatNumber(row.raw_score, 4), sortValue: (row) => toNumber(row.raw_score) },
    { key: 'adjusted_score', label: 'Adjusted Score', render: (row) => formatNumber(row.adjusted_score, 4), sortValue: (row) => toNumber(row.adjusted_score) },
    { key: 'rank', label: 'Rank', render: (row) => formatNumber(row.rank, 0), sortValue: (row) => toNumber(row.rank) },
    { key: 'selected', label: 'Selected', render: (row) => makeBadge(row.selected ? 'yes' : 'no', row.selected ? 'success' : 'warning'), sortValue: (row) => row.selected ? 1 : 0 },
    { key: 'exclusion_reasons', label: 'Exclusion Reasons', render: (row) => escapeHtml((row.exclusion_reasons || []).join(', ') || '-') },
  ], {
    tableKey: 'replay-candidates',
    selectedValue: state.replayData.selectedInstrument,
    selectedRowId: (row) => row.instrument_id,
    onRowClick: (row) => selectReplayInstrument(row.instrument_id),
    emptyMessage: 'No candidate pool',
  });

  renderDataTable('replay-positions-table', replay.previous_positions || [], [
    { key: 'instrument_id', label: 'Instrument', render: (row) => renderInstrumentLink(row.instrument_id, replay.trade_date), filterValue: (row) => row.instrument_id },
    { key: 'quantity', label: 'Qty', render: (row) => formatNumber(row.quantity, 0), sortValue: (row) => toNumber(row.quantity) },
    { key: 'price', label: 'Price', render: (row) => formatNumber(row.price, 2), sortValue: (row) => toNumber(row.price) },
    { key: 'cost_basis', label: 'Cost Basis', render: (row) => formatNumber(row.cost_basis, 2), sortValue: (row) => toNumber(row.cost_basis) },
    { key: 'as_of_date', label: 'As Of', render: (row) => renderTradeDateLink(row.as_of_date || replay.trade_date) },
  ], {
    tableKey: 'replay-positions',
    selectedValue: state.replayData.selectedInstrument,
    selectedRowId: (row) => row.instrument_id,
    onRowClick: (row) => selectReplayInstrument(row.instrument_id),
    emptyMessage: 'No previous positions',
  });

  renderDataTable('replay-orders-table', replay.final_orders || [], [
    { key: 'instrument_id', label: 'Instrument', render: (row) => renderInstrumentLink(row.instrument_id, replay.trade_date), filterValue: (row) => row.instrument_id },
    { key: 'side', label: 'Side', render: (row) => makeBadge(row.side || '-', normalizeTradeSide(row.side) === 'sell' ? 'warning' : 'success'), sortValue: (row) => row.side },
    { key: 'quantity', label: 'Qty', render: (row) => formatNumber(row.quantity, 0), sortValue: (row) => toNumber(row.quantity) },
    { key: 'price', label: 'Price', render: (row) => formatNumber(row.price, 2), sortValue: (row) => toNumber(row.price) },
    { key: 'status', label: 'Status', render: (row) => makeBadge(row.status || '-', row.status && String(row.status).toLowerCase().includes('planned') ? 'warning' : 'neutral'), sortValue: (row) => row.status },
    { key: 'note', label: 'Note', render: (row) => escapeHtml(row.note || '-') },
    { id: 'case', label: 'Case', sortable: false, render: (row) => `<button type="button" class="action-link" onclick="jumpToCase('${quoteJsString(row.instrument_id)}', '${quoteJsString(replay.trade_date)}')">Open Case</button>`, filterValue: () => '' },
  ], {
    tableKey: 'replay-orders',
    selectedValue: state.replayData.selectedInstrument,
    selectedRowId: (row) => row.instrument_id,
    onRowClick: (row) => selectReplayInstrument(row.instrument_id),
    emptyMessage: 'No final orders',
  });
}

async function renderReplaySelection() {
  const replay = state.replayData.payload;
  const instrumentId = state.replayData.selectedInstrument;
  if (!replay || !instrumentId) {
    byId('replay-selection-summary').innerHTML = '<div class="empty">Select an instrument from candidates, positions, or orders.</div>';
    byId('replay-instrument-chart').innerHTML = '<div class="empty">Select an instrument to view replay timeline.</div>';
    byId('replay-explanation').innerHTML = 'Current API does not expose full pipeline stage artifacts. Use the selected rows to inspect the available subset.';
    byId('replay-context-links').innerHTML = '';
    return;
  }
  const candidate = (replay.scored_candidates || []).find((row) => row.instrument_id === instrumentId);
  const position = (replay.previous_positions || []).find((row) => row.instrument_id === instrumentId);
  const order = (replay.final_orders || []).find((row) => row.instrument_id === instrumentId);
  byId('replay-selection-summary').innerHTML = makeStackItems([
    { label: 'Instrument', value: renderInstrumentLink(instrumentId, replay.trade_date) },
    { label: 'Replay Date', value: renderTradeDateLink(replay.trade_date, 'replay') },
    { label: 'Candidate Rank', value: candidate ? formatNumber(candidate.rank, 0) : 'n/a' },
    { label: 'Adjusted Score', value: candidate ? formatNumber(candidate.adjusted_score, 4) : 'n/a' },
    { label: 'Previous Qty', value: position ? formatNumber(position.quantity, 0) : '0' },
    { label: 'Final Order', value: order ? `${order.side} ${formatNumber(order.quantity, 0)}` : 'not ordered' },
  ]);

  const explanation = [];
  explanation.push(`Instrument ${escapeHtml(instrumentId)} is linked across candidate, portfolio, and order tables for ${escapeHtml(replay.trade_date)}.`);
  if (candidate) {
    explanation.push(`Raw score is the original model score; adjusted score is the post-selection score shown by the replay payload${candidate.adjusted_score === candidate.raw_score ? ' (currently unchanged from raw score)' : ''}.`);
    explanation.push(`Current replay row shows adjusted_score ${formatNumber(candidate.adjusted_score, 4)} and rank ${formatNumber(candidate.rank, 0)}.`);
    if (candidate.exclusion_reasons?.length) {
      explanation.push(`Exclusion reasons: ${escapeHtml(candidate.exclusion_reasons.join(', '))}.`);
    }
  } else {
    explanation.push('No scored candidate row exists for this instrument in the current payload.');
  }
  if (position) explanation.push(`Previous position carries ${formatNumber(position.quantity, 0)} shares from ${escapeHtml(position.as_of_date || 'prior snapshot')}.`);
  if (order) explanation.push(`Final order is ${escapeHtml(order.side)} ${formatNumber(order.quantity, 0)} shares at ${formatNumber(order.price, 2)} with status ${escapeHtml(order.status || '-')}.`);
  if (!order) explanation.push('No final order exists, so the filter/portfolio decision likely stopped before execution output.');
  explanation.push('Pipeline stage details such as full universe members and post-filter rejection buckets are backend data gaps and remain placeholders in this UI.');
  byId('replay-explanation').innerHTML = explanation.map((item) => `<p>${item}</p>`).join('');
  byId('replay-context-links').innerHTML = [
    renderActionButton('Open Case', `jumpToCase('${quoteJsString(instrumentId)}', '${quoteJsString(replay.trade_date)}')`, 'primary'),
    renderActionButton('Open Feature Health', `jumpToFeature('${quoteJsString(state.context.featureId || '')}', '${quoteJsString(instrumentId)}', '${quoteJsString(replay.trade_date)}')`, 'secondary'),
    renderActionButton('Copy Instrument', `copyText('${quoteJsString(instrumentId)}')`, 'ghost'),
  ].join('');
  await renderReplayInstrumentChart(replay, instrumentId);
}

async function loadReplay() {
  try {
    updateContext(readContextFromInputs());
    const executionDate = state.context.tradeDate;
    const accountName = state.context.account || 'shadow';
    if (!executionDate) throw new Error('Trade date is required');
    const replayPayload = await getJson(`/api/decision-replay?execution_date=${executionDate}&account_name=${accountName}`, { useCache: false });
    const replay = unwrapData(replayPayload);
    state.replayData.payload = replay;
    buildReplaySummaryCards(replay);
    buildReplayPipeline(replay);
    const selectedInstrument = replay.final_orders.some((row) => row.instrument_id === state.context.instrumentId)
      ? state.context.instrumentId
      : replay.final_orders[0]?.instrument_id || replay.scored_candidates[0]?.instrument_id || replay.previous_positions[0]?.instrument_id || '';
    state.replayData.selectedInstrument = selectedInstrument;
    renderReplayTables();
    renderReplaySelection();
  } catch (error) {
    byId('replay-summary-row').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('replay-pipeline').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('replay-candidates-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('replay-positions-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('replay-orders-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('replay-selection-summary').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('replay-explanation').innerHTML = escapeHtml(error.message);
  }
}

window.jumpToBacktest = function jumpToBacktest(runId, tradeDate) {
  if (runId) updateContext({ runId }, { syncInputs: true, syncHash: true });
  if (tradeDate) updateContext({ tradeDate }, { syncInputs: true, syncHash: true });
  setView('backtest');
  loadViewIfNeeded('backtest', { force: true });
};

window.jumpToReplay = function jumpToReplay(tradeDate, instrumentId) {
  const updates = { tradeDate: tradeDate || state.context.tradeDate, instrumentId: instrumentId || state.context.instrumentId };
  updateContext(updates, { syncInputs: true, syncHash: true });
  setView('replay');
  loadViewIfNeeded('replay', { force: true });
};

window.jumpToCase = function jumpToCase(instrumentId, tradeDate) {
  const updates = { instrumentId: instrumentId || state.context.instrumentId, tradeDate: tradeDate || state.context.tradeDate };
  updateContext(updates, { syncInputs: true, syncHash: true });
  setView('case');
  loadViewIfNeeded('case', { force: true });
};

window.setCasePriceMode = function setCasePriceMode(priceMode) {
  updateContext({ priceMode }, { syncInputs: true, syncHash: true });
  if (state.currentView === 'case') loadViewIfNeeded('case', { force: true });
};

window.jumpToFeature = function jumpToFeature(featureId, instrumentId, tradeDate) {
  if (featureId && state.selectedFeatureNames.size < MAX_FEATURE_SELECTION) {
    state.selectedFeatureNames.add(featureId);
    syncFeatureSelectionInput();
    renderFeatureSelectionChips();
  }
  updateContext({
    featureId: featureId || state.context.featureId,
    instrumentId: instrumentId || state.context.instrumentId,
    tradeDate: tradeDate || state.context.tradeDate,
  }, { syncInputs: true, syncHash: true });
  setView('feature');
  loadViewIfNeeded('feature', { force: true });
};

async function bootstrapDefaults() {
  try {
    const runsPayload = await getJson('/api/backtest-runs?limit=50', { useCache: false });
    const runs = unwrapItems(runsPayload);
    state.backtestRuns = runs;
    renderBacktestRunOptions(runs);
    const latestRun = runs[0];
    if (latestRun?.run_id) {
      state.latestBacktestRunId = latestRun.run_id;
      if (!state.context.runId) state.context.runId = latestRun.run_id;
      const endDate = latestRun.test_range?.end;
      if (endDate && !state.context.tradeDate) state.context.tradeDate = endDate;
    }
    syncInputsFromContext();
    renderContextPresentation();
  } catch (error) {
    console.error('bootstrap backtest runs failed', error);
  }
}

async function loadViewIfNeeded(name, { force = false } = {}) {
  if (!force && state.loadedViews.has(name)) return;
  if (name === 'backtest') await loadBacktest();
  if (name === 'case') await loadCase();
  if (name === 'feature') {
    await loadFeatureRegistry();
    await loadFeatureHealth();
  }
  if (name === 'replay') await loadReplay();
  state.loadedViews.add(name);
}

function bindFeatureRegistryEvents() {
  byId('feature-registry-search').addEventListener('input', () => renderFeatureRegistry());
  byId('feature-source-filter').addEventListener('change', () => renderFeatureRegistry());
  byId('feature-names').addEventListener('change', () => parseFeatureSelectionFromInput());
  byId('select-visible-features').addEventListener('click', () => window.selectVisibleFeatures());
  byId('clear-feature-selection').addEventListener('click', () => window.clearFeatureSelection());
}

function bindEvents() {
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.addEventListener('click', async () => {
    setView(btn.dataset.view);
    await loadViewIfNeeded(btn.dataset.view);
  }));
  byId('apply-context').addEventListener('click', async () => {
    updateContext(readContextFromInputs());
    await loadViewIfNeeded(state.currentView, { force: true });
  });
  byId('refresh-view').addEventListener('click', async () => {
    await loadViewIfNeeded(state.currentView, { force: true });
  });
  byId('copy-context').addEventListener('click', () => copyText(JSON.stringify(state.context, null, 2)));
  byId('backtest-run-select').addEventListener('change', async (event) => {
    updateContext({ runId: event.target.value }, { syncInputs: false, syncHash: true });
    if (state.currentView === 'backtest') await loadViewIfNeeded('backtest', { force: true });
  });
  byId('context-price-mode').addEventListener('change', async (event) => {
    updateContext({ priceMode: event.target.value }, { syncInputs: false, syncHash: true });
    if (state.currentView === 'case') await loadViewIfNeeded('case', { force: true });
  });
  bindFeatureRegistryEvents();
}

updateContext(readContextFromInputs(), { syncInputs: true, syncHash: false });
applyHashContext();
bindEvents();
bootstrapDefaults().then(() => {
  syncInputsFromContext();
  renderContextPresentation();
  setView(state.currentView || 'backtest');
  return loadViewIfNeeded(state.currentView || 'backtest', { force: true });
});
