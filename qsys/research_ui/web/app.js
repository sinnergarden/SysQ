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
  _syncingZoom: false,
  defaultBacktestRunId: null,
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
    universe: 'csi300',
  },
  backtest: {
    summary: null,
    daily: [],
    groupReturns: [],
    sections: [],
    sectionArtifacts: {},
    selectedDate: '',
    selectedInstrument: '',
    ordersByDate: new Map(),
    positionsByDate: new Map(),
    episodes: null,
    episodeSummary: null,
    episodeMeta: null,
    episodesLoaded: false,
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
    universe: byId('context-universe').value.trim() || 'csi300',
  };
}

function syncInputsFromContext() {
  byId('context-trade-date').value = state.context.tradeDate || '';
  byId('context-instrument').value = state.context.instrumentId || '';
  byId('context-feature-id').value = state.context.featureId || '';
  byId('context-account').value = state.context.account || 'shadow';
  byId('context-price-mode').value = state.context.priceMode || 'fq';
  byId('context-universe').value = state.context.universe || 'csi300';
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
  if (!next.universe) next.universe = 'csi300';
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
  if (state.context.universe) params.set('universe', state.context.universe);
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
    universe: params.get('universe') || state.context.universe,
  }, { syncInputs: true, syncHash: false });
  const view = params.get('view');
  if (view && viewMeta[view]) state.currentView = view;
}

async function refreshInstrumentList() {
  try {
    const universe = state.context.universe || 'csi300';
    const response = await getJson(`/api/instruments?universe=${encodeURIComponent(universe)}&limit=5000`, { useCache: true });
    const items = unwrapItems(response);
    byId('instrument-list').innerHTML = items.map((item) =>
      `<option value="${escapeHtml(item.ts_code)}">${escapeHtml(item.name || item.ts_code)}</option>`
    ).join('');
  } catch (_e) { /* instrument list is non-critical */ }
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
    renderContextCard('Universe', state.context.universe || 'csi300', state.context.universe),
  ].join('');

  byId('context-strip').innerHTML = [
    renderContextCard('Run', runMarkup, state.context.runId),
    renderContextCard('Date', tradeDateMarkup, state.context.tradeDate),
    renderContextCard('Instrument', instrumentMarkup, state.context.instrumentId),
    renderContextCard('Account / Price', `<span class="entity-pill">${escapeHtml(state.context.account)} / ${escapeHtml(state.context.priceMode)}</span>`, JSON.stringify({ account: state.context.account, price_mode: state.context.priceMode })),
    renderContextCard('Universe', state.context.universe || 'csi300', state.context.universe),
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
    root.removeAllListeners('plotly_relayout');
  }
  if (handlers.onClick) root.on('plotly_click', handlers.onClick);
  if (handlers.onRelayout) root.on('plotly_relayout', handlers.onRelayout);
}

function renderPlotlyChart(containerId, traces, layout, handlers = {}, configOverrides = {}) {
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
    ...configOverrides,
  });
  Promise.resolve(renderResult).then(() => bindPlotlyHandlers(root, handlers));
}

function dashToPlotlyDash(dash) {
  if (!dash) return 'solid';
  return dash.includes('2') ? 'dot' : 'dash';
}

function renderSeriesChart(containerId, { dates = [], series = [], title = '', includeZero = false, yAxisTitle = '', height = 320, selectedDate = '', onClick = null, onRelayout = null } = {}) {
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
  const handlers = {
    onClick: (event) => {
      const tradeDate = toDateLabel(event?.points?.[0]?.x);
      if (tradeDate && onClick) onClick(tradeDate);
    },
  };
  if (onRelayout) handlers.onRelayout = onRelayout;
  renderPlotlyChart(containerId, traces, layout, handlers);
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

function renderCandlestickChart(containerId, bars, markers = [], instrumentId = '', { selectedDate = '', volumeBars = null } = {}) {
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

  // Lookup for bar high by date, used to offset markers above candle tops
  const highByDate = new Map(validBars.map((item) => [item.trade_date, Number(item.high)]));

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

  // Volume subplot (shares x-axis)
  const volumePoints = (volumeBars || [])
    .map((item) => ({
      tradeDate: item.trade_date,
      value: toNumber(item.volume),
      color: toNumber(item.close) >= toNumber(item.open) ? CHART_COLORS.candleUp : CHART_COLORS.candleDown,
    }))
    .filter((item) => item.value !== null);
  if (volumePoints.length) {
    traces.push({
      type: 'bar',
      name: 'Volume',
      xaxis: 'x',
      yaxis: 'y2',
      x: volumePoints.map((item) => item.tradeDate),
      y: volumePoints.map((item) => item.value),
      marker: { color: volumePoints.map((item) => item.color), opacity: 0.78 },
      hovertemplate: 'Volume<br>%{x}<br>%{y:.0f}<extra></extra>',
    });
  }

  if (holdingSpans.length) {
    traces.push({
      type: 'scatter',
      mode: 'lines',
      name: 'Holding Span',
      x: holdingSpans.flatMap((item) => [item.startDate, item.endDate, null]),
      y: holdingSpans.flatMap((item) => {
        const y0 = (highByDate.get(item.startDate) || item.startPrice) * 1.015;
        const y1 = (highByDate.get(item.endDate) || item.endPrice) * 1.015;
        return [y0, y1, null];
      }),
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
      y: orderBuyMarkers.map((item) => (highByDate.get(item.tradeDate) || item.value) * 1.015),
      marker: { color: CHART_COLORS.strategy, size: 9, symbol: 'circle-open', line: { color: CHART_COLORS.strategy, width: 1.8 } },
      customdata: orderBuyMarkers.map((item) => [item.quantity, item.value]),
      hovertemplate: 'Replay Buy Order<br>%{x}<br>Price %{customdata[1]:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }
  if (orderSellMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Replay Sell Order',
      x: orderSellMarkers.map((item) => item.tradeDate),
      y: orderSellMarkers.map((item) => (highByDate.get(item.tradeDate) || item.value) * 1.015),
      marker: { color: CHART_COLORS.accent, size: 9, symbol: 'circle-open', line: { color: CHART_COLORS.accent, width: 1.8 } },
      customdata: orderSellMarkers.map((item) => [item.quantity, item.value]),
      hovertemplate: 'Replay Sell Order<br>%{x}<br>Price %{customdata[1]:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }
  if (buyMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Filled Buy',
      x: buyMarkers.map((item) => item.tradeDate),
      y: buyMarkers.map((item) => (highByDate.get(item.tradeDate) || item.value) * 1.015),
      marker: { color: CHART_COLORS.strategy, size: 10, symbol: 'triangle-up-open', line: { color: CHART_COLORS.strategy, width: 2 } },
      customdata: buyMarkers.map((item) => [item.quantity, item.value]),
      hovertemplate: 'Filled Buy<br>%{x}<br>Price %{customdata[1]:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }
  if (sellMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Filled Sell',
      x: sellMarkers.map((item) => item.tradeDate),
      y: sellMarkers.map((item) => (highByDate.get(item.tradeDate) || item.value) * 1.015),
      marker: { color: CHART_COLORS.accent, size: 10, symbol: 'triangle-down-open', line: { color: CHART_COLORS.accent, width: 2 } },
      customdata: sellMarkers.map((item) => [item.quantity, item.value]),
      hovertemplate: 'Filled Sell<br>%{x}<br>Price %{customdata[1]:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }

  const hasVolume = volumePoints && volumePoints.length > 0;
  const layout = plotlyBaseLayout({
    title: `${instrumentId || 'Instrument'} OHLC`,
    height: hasVolume ? 580 : 460,
    yAxisTitle: 'Price',
    hoverMode: 'x',
    showRangeSlider: false,
    selectedDate,
  });
  layout.yaxis.fixedrange = true;
  layout.xaxis.fixedrange = false;
  layout.dragmode = 'pan';
  if (hasVolume) {
    layout.yaxis.domain = [0.3, 1];
    layout.yaxis2 = {
      domain: [0, 0.28],
      title: { text: 'Volume', font: { size: 11, color: CHART_COLORS.axis } },
      gridcolor: CHART_COLORS.grid,
      zerolinecolor: CHART_COLORS.grid,
      tickfont: { color: CHART_COLORS.axis },
      fixedrange: true,
    };
  }
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

  renderPlotlyChart(containerId, traces, layout, {
    onRelayout: (event) => {
      // After x-axis zoom/pan, auto-fit y-axes to visible data
      if (!event || !('xaxis.range[0]' in event || 'xaxis.range[1]' in event)) return;
      const root = byId(containerId);
      if (!root || !root.data) return;
      const x0 = new Date(event['xaxis.range[0]']).getTime();
      const x1 = new Date(event['xaxis.range[1]']).getTime();
      if (!x0 || !x1) return;

      // Find min/max of visible OHLC data
      let yMin = Infinity, yMax = -Infinity;
      let volMax = 0;
      const dates = root.data[0].x;
      const highs = root.data[0].high;
      const lows = root.data[0].low;
      const volumes = volumePoints.length ? root.data[1].y : null;
      for (let i = 0; i < dates.length; i++) {
        const t = new Date(dates[i]).getTime();
        if (t >= x0 && t <= x1) {
          if (highs[i] > yMax) yMax = highs[i];
          if (lows[i] < yMin) yMin = lows[i];
          if (volumes && volumes[i] > volMax) volMax = volumes[i];
        }
      }
      const pad = (yMax - yMin) * 0.08 || yMax * 0.05;
      const update = { 'yaxis.range': [yMin - pad, yMax + pad] };
      if (volMax > 0) update['yaxis2.range'] = [0, volMax * 1.15];
      window.Plotly.relayout(root, update).catch(() => {});
    },
  }, { scrollZoom: true });
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
  const rawRows = [
    ['Version', params.version_label || summary.display_label || '-'],
    ['Model', summary.model_name || '-'],
    ['Feature Set', params.feature_set || summary.feature_set || '-'],
    ['Universe', params.universe || summary.universe || '-'],
    ['Top K', params.top_k ?? summary.top_k ?? '-'],
    ['Label Type', params.label_type || '-'],
    ['Strategy Type', params.strategy_type || '-'],
    ['Rebalance', [params.rebalance_mode || '-', params.rebalance_freq || ''].filter(Boolean).join(' ')],
    ['Signal Date', params.signal_date || summary.train_range?.start || '-'],
    ['Execution Date', params.execution_date || summary.test_range?.end || '-'],
  ];
  const rows = rawRows.filter(([key, value]) => {
    const v = String(value).trim();
    return v !== '-' && v !== '' && v !== 'None' && v !== 'null' && v !== 'undefined';
  });
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
  const listed = (runs || []).some((item) => item.run_id === currentValue);
  const explicitOption = currentValue && !listed
    ? `<option value="${escapeHtml(currentValue)}">${escapeHtml(currentValue)} · explicit artifact</option>`
    : '';
  const options = explicitOption + (runs || []).map((item) => `<option value="${escapeHtml(item.run_id)}">${escapeHtml(item.display_label || item.run_id)}</option>`).join('');
  select.innerHTML = options || '<option value="">no backtest runs</option>';
  if (currentValue) {
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

function renderBacktestSections() {
  const runId = state.context.runId || byId('backtest-run-select').value;
  if (!runId) {
    renderChartError('backtest-monthly-chart', 'No run selected');
    renderChartError('backtest-windows-chart', 'No run selected');
    renderChartError('backtest-signal-windows-chart', 'No run selected');
    renderChartError('backtest-return-dist-chart', 'No run selected');
    renderChartError('backtest-ic-dist-chart', 'No run selected');
    return;
  }
  getJson(`/api/backtest-runs/${runId}/sections`, { useCache: false })
    .then((payload) => {
      const data = unwrapData(payload) || {};
      const artifacts = data.artifacts || {};
      const sections = data.sections || [];
      state.backtest.sections = sections;
      state.backtest.sectionArtifacts = artifacts;
      renderBacktestMonthlyHeatmap(artifacts);
      renderBacktestWindowsChart(artifacts);
      renderBacktestCostGrid(sections, artifacts);
      renderBacktestSignalWindowsChart(artifacts);
      renderBacktestReturnDistChart(artifacts);
      renderBacktestICDistChart(artifacts);
    })
    .catch(() => {
      renderChartError('backtest-monthly-chart', 'Sections endpoint not available for this run');
      renderChartError('backtest-windows-chart', 'Sections endpoint not available');
      renderChartError('backtest-signal-windows-chart', 'Sections endpoint not available');
      renderChartError('backtest-return-dist-chart', 'Sections endpoint not available');
      renderChartError('backtest-ic-dist-chart', 'Sections endpoint not available');
    });
}

function renderBacktestMonthlyHeatmap(artifacts) {
  // Monthly return heatmap (years × months)
  let data = artifacts.monthly_returns;
  if (!data || !data.length) {
    data = artifacts.weekly_returns;
    if (data && data.length) {
      // Aggregate weekly -> monthly
      const monthMap = {};
      data.forEach((item) => {
        const week = item.week || '';
        const month = week.slice(0, 7);
        if (month && month.length === 7) {
          if (!monthMap[month]) monthMap[month] = [];
          monthMap[month].push(Number(item.return));
        }
      });
      data = Object.entries(monthMap).map(([month, returns]) => ({
        month,
        return: returns.reduce((s, v) => s + v, 0) / returns.length,
      }));
    }
  }
  if (!data || !data.length) {
    renderChartError('backtest-monthly-chart', 'Returns not available');
    return;
  }
  // Parse months into year/month grid
  const months = Array.from(new Set(data.map((item) => item.month.slice(0, 7)))).sort();
  const years = Array.from(new Set(months.map((m) => m.slice(0, 4)))).sort();
  const monthLabels = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12'];
  const lookup = {};
  months.forEach((m) => { lookup[m] = true; });
  const yearRange = [];
  for (let y = parseInt(years[0], 10); y <= parseInt(years[years.length - 1], 10); y++) {
    yearRange.push(String(y));
  }
  const z = yearRange.map((year) =>
    monthLabels.map((mon) => {
      const key = `${year}-${mon}`;
      if (!lookup[key]) return null;
      const entry = data.find((d) => d.month === key);
      return entry ? Number(entry.return) * 100 : null;
    })
  );
  const colorscale = [
    [0, '#7f1d1d'],
    [0.35, '#e57373'],
    [0.48, '#e8ddd0'],
    [0.52, '#e8ddd0'],
    [0.65, '#81c784'],
    [1, '#1b5e20'],
  ];
  const maxAbs = Math.max(Math.abs(Math.min(...z.flat().filter((v) => v !== null))), Math.abs(Math.max(...z.flat().filter((v) => v !== null))));
  const zBound = Math.max(5, Math.ceil(maxAbs / 5) * 5);
  const hoverText = yearRange.map((year, yi) =>
    monthLabels.map((mon, xi) => {
      const val = z[yi][xi];
      return `${year}-${mon}<br>Return: ${val !== null ? val.toFixed(2) + '%' : 'N/A'}`;
    })
  );
  const layout = plotlyBaseLayout({
    title: 'Monthly Return Heatmap (%)',
    height: Math.max(200, yearRange.length * 50 + 80),
  });
  layout.xaxis = {
    type: 'category',
    tickvals: monthLabels,
    ticktext: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
    gridcolor: 'transparent',
    side: 'top',
  };
  layout.yaxis = {
    type: 'category',
    tickvals: yearRange,
    autorange: 'reversed',
    gridcolor: 'transparent',
  };
  layout.margin = { l: 40, r: 16, t: 44, b: 16 };
  layout.dragmode = false;
  delete layout.hovermode;
  renderPlotlyChart('backtest-monthly-chart', [{
    type: 'heatmap',
    z,
    x: monthLabels,
    y: yearRange,
    text: hoverText,
    hovertemplate: '%{text}<extra></extra>',
    colorscale,
    zmin: -zBound,
    zmax: zBound,
    xgap: 3,
    ygap: 3,
    connectgaps: false,
  }], layout);
}

function getBacktestWindowRows(artifacts) {
  if (Array.isArray(artifacts?.rolling_metrics) && artifacts.rolling_metrics.length) return artifacts.rolling_metrics;
  if (Array.isArray(artifacts?.rolling_windows) && artifacts.rolling_windows.length) return artifacts.rolling_windows;
  return [];
}

function renderBacktestWindowsChart(artifacts) {
  const windowsData = getBacktestWindowRows(artifacts);
  if (!windowsData || !windowsData.length) {
    renderChartError('backtest-windows-chart', 'Rolling windows not available');
    return;
  }
  const labels = windowsData.map((item) => item.window_id || item.test_start || '');
  const returns = windowsData.map((item) => Number(item.total_return) * 100);
  const colors = returns.map((v) => v >= 0 ? CHART_COLORS.strategy : CHART_COLORS.danger);
  const layout = plotlyBaseLayout({
    title: 'Per-Window Total Return (%)',
    height: 280,
    yAxisTitle: 'Return %',
  });
  layout.xaxis = { type: 'category' };
  layout.yaxis.zeroline = true;
  layout.yaxis.zerolinecolor = 'rgba(31,41,51,0.5)';
  renderPlotlyChart('backtest-windows-chart', [{
    type: 'bar',
    x: labels,
    y: returns,
    marker: { color: colors },
    hovertemplate: '%{x}<br>Return: %{y:.2f}%<extra></extra>',
  }], layout);
}

function renderBacktestCostGrid(sections, artifacts = {}) {
  const root = byId('backtest-cost-grid');
  if (!root) return;
  const costSection = sections.find((s) => s.name === 'Cost Analysis');
  let rows = [];
  if (costSection && costSection.metrics) {
    const metrics = costSection.metrics;
    rows = [
      ['Total Fees', metrics.total_fees != null ? formatNumber(Number(metrics.total_fees), 2) : '-'],
      ['Annualized Turnover', metrics.annualized_turnover != null ? String(metrics.annualized_turnover) : '-'],
    ];
    if (metrics.total_turnover != null) rows.push(['Total Turnover', formatNumber(metrics.total_turnover, 2)]);
    if (metrics.fee_ratio != null) rows.push(['Fee Ratio', formatPercent(metrics.fee_ratio, 3)]);
    if (metrics.fees_as_pct_of_initial != null) rows.push(['Fees % of Initial', formatPercent(metrics.fees_as_pct_of_initial, 2)]);
    if (metrics.avg_daily_fee != null) rows.push(['Avg Daily Fee', formatNumber(metrics.avg_daily_fee, 2)]);
  } else if (artifacts.rolling_stability) {
    const stability = artifacts.rolling_stability || {};
    rows = [
      ['Avg Turnover', formatPercent(stability.turnover_mean, 2)],
      ['Turnover Std', formatPercent(stability.turnover_std, 2)],
      ['Positive Return Weeks', formatPercent(stability.positive_return_ratio, 1)],
      ['Positive RankIC Weeks', formatPercent(stability.positive_rankic_ratio, 1)],
      ['Return Std', formatPercent(stability.return_std, 2)],
    ];
  }
  if (!rows.length) {
    root.innerHTML = '<div class="empty">Cost analysis not available</div>';
    return;
  }
  root.innerHTML = rows.map(([key, value]) => `
    <div class="kv-item">
      <span>${escapeHtml(key)}</span>
      <strong>${escapeHtml(String(value))}</strong>
    </div>
  `).join('');
}

function renderBacktestSignalWindowsChart(artifacts) {
  const signalData = artifacts.signal_metrics;
  if (!signalData || !signalData.aggregate) {
    renderChartError('backtest-signal-windows-chart', 'Signal metrics not available');
    return;
  }
  const agg = signalData.aggregate;
  const traces = [];
  const palette = [CHART_COLORS.accent, CHART_COLORS.neutral, CHART_COLORS.strategy];
  let idx = 0;
  for (const key of ['IC', 'RankIC', 'long_short_spread']) {
    const data = agg[key];
    if (!data || !data.values) continue;
    traces.push({
      type: 'scatter',
      mode: 'lines+markers',
      name: key,
      x: data.values.map((_, i) => String(i + 1)),
      y: data.values,
      line: { color: palette[idx % palette.length], width: 2 },
      marker: { color: palette[idx % palette.length], size: 5 },
      hovertemplate: `${key}<br>Window %{x}<br>%{y:.6f}<extra></extra>`,
    });
    idx++;
  }
  if (!traces.length) {
    renderChartError('backtest-signal-windows-chart', 'No per-window signal data');
    return;
  }
  const layout = plotlyBaseLayout({
    title: 'IC / RankIC per Window',
    height: 280,
    yAxisTitle: 'Value',
  });
  layout.xaxis = { type: 'category', title: { text: 'Window' } };
  layout.yaxis.zeroline = true;
  layout.legend = { orientation: 'h', y: 1.08 };
  renderPlotlyChart('backtest-signal-windows-chart', traces, layout);
}

function renderBacktestReturnDistChart(artifacts) {
  const windowsData = getBacktestWindowRows(artifacts);
  if (!windowsData || !windowsData.length) {
    renderChartError('backtest-return-dist-chart', 'Rolling windows not available');
    return;
  }
  const rawReturns = windowsData.map((item) => Number(item.total_return) * 100).filter((v) => v !== null && !isNaN(v));
  if (!rawReturns.length) {
    renderChartError('backtest-return-dist-chart', 'No return data');
    return;
  }
  const returns = rawReturns.map((v) => Math.round(v * 10) / 10);
  const layout = plotlyBaseLayout({
    title: 'Return Distribution (% , per window)',
    height: 260,
    yAxisTitle: 'Count',
  });
  layout.xaxis = { title: { text: 'Return %' } };
  renderPlotlyChart('backtest-return-dist-chart', [{
    type: 'histogram',
    x: returns,
    nbinsx: 50,
    marker: { color: CHART_COLORS.strategy, line: { color: '#fff', width: 0.5 } },
    hovertemplate: 'Return %{x:.1f}%<br>Count: %{y}<extra></extra>',
  }], layout);
}

function renderBacktestICDistChart(artifacts) {
  const signalData = artifacts.signal_metrics;
  if (!signalData || !signalData.aggregate) {
    renderChartError('backtest-ic-dist-chart', 'Signal metrics not available');
    return;
  }
  const traces = [];
  for (const key of ['IC', 'RankIC']) {
    const data = signalData.aggregate[key];
    if (!data || !data.values) continue;
    const icPct = data.values.map((v) => Math.round(Number(v) * 1000) / 10);
    traces.push({
      type: 'histogram',
      name: key,
      x: icPct,
      nbinsx: 50,
      opacity: 0.7,
      marker: { color: key === 'IC' ? CHART_COLORS.accent : CHART_COLORS.neutral, line: { width: 0.3 } },
      hovertemplate: `${key}<br>%{x:.1f}%<br>Count: %{y}<extra></extra>`,
    });
  }
  if (!traces.length) {
    renderChartError('backtest-ic-dist-chart', 'No IC data');
    return;
  }
  const layout = plotlyBaseLayout({
    title: 'IC / RankIC Distribution',
    height: 260,
    yAxisTitle: 'Count',
  });
  layout.xaxis = { title: { text: 'IC Value' } };
  layout.barmode = 'overlay';
  layout.legend = { orientation: 'h', y: 1.08 };
  renderPlotlyChart('backtest-ic-dist-chart', traces, layout);
}

function syncChartZoom(sourceId, targetId, eventData) {
  if (state._syncingZoom) return;
  if (!eventData || !eventData['xaxis.range']) return;
  state._syncingZoom = true;
  const range = eventData['xaxis.range'];
  try {
    window.Plotly.relayout(targetId, { 'xaxis.range': range });
  } catch (_e) { /* ignore */ }
  state._syncingZoom = false;
}

function makeChartRelayoutHandler(sourceId, targetId) {
  return function (eventData) {
    syncChartZoom(sourceId, targetId, eventData);
  };
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
  const equityChartId = 'backtest-equity-chart';
  const diagChartId = 'backtest-diagnostics-chart';
  renderSeriesChart(equityChartId, {
    dates,
    title: 'Strategy / Zero-cost / CSI300 / SSE',
    height: 480,
    selectedDate,
    onClick: (tradeDate) => selectBacktestDate(tradeDate),
    onRelayout: makeChartRelayoutHandler(equityChartId, diagChartId),
    series: [
      { name: 'Strategy', values: dailyItems.map((item) => item.equity), color: CHART_COLORS.strategy },
      { name: 'Zero-cost', values: dailyItems.map((item) => item.zero_cost_equity), color: CHART_COLORS.accent, dash: '3 4' },
      { name: 'CSI300', values: dailyItems.map((item) => item.benchmark_equity), color: CHART_COLORS.benchmark, dash: '6 4' },
      { name: 'SSE', values: dailyItems.map((item) => item.benchmark2_equity), color: CHART_COLORS.neutral, dash: '2 5' },
    ],
    yAxisTitle: 'Equity',
  });
  renderSeriesChart(diagChartId, {
    dates,
    includeZero: true,
    title: 'Drawdown / IC / RankIC',
    height: 300,
    selectedDate,
    onClick: (tradeDate) => selectBacktestDate(tradeDate),
    onRelayout: makeChartRelayoutHandler(diagChartId, equityChartId),
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
  const stability = state.backtest.sectionArtifacts?.rolling_stability || null;
  const buyCount = (orders || []).filter((row) => normalizeTradeSide(row.side) !== 'sell').length;
  const sellCount = (orders || []).filter((row) => normalizeTradeSide(row.side) === 'sell').length;
  const tradedValue = (orders || []).reduce((sum, row) => {
    const quantity = toNumber(row.filled_amount || row.quantity || row.amount) || 0;
    const price = toNumber(row.deal_price || row.price) || 0;
    return sum + Math.abs(quantity * price);
  }, 0);
  const cards = [
    { label: 'Orders', value: String((orders || []).length) },
    { label: 'Buy / Sell', value: `${buyCount} / ${sellCount}` },
    { label: 'Gross Traded', value: formatNumber(tradedValue, 0) },
  ];
  if ((!orders || !orders.length) && stability) {
    cards.splice(0, cards.length,
      { label: 'Positive Return', value: formatPercent(stability.positive_return_ratio, 1) },
      { label: 'Positive RankIC', value: formatPercent(stability.positive_rankic_ratio, 1) },
      { label: 'Best Week', value: `${stability.best_window?.window_id || '-'} · ${formatPercent(stability.best_window?.total_return, 2)}` },
      { label: 'Worst Week', value: `${stability.worst_window?.window_id || '-'} · ${formatPercent(stability.worst_window?.total_return, 2)}` },
    );
  }
  root.innerHTML = cards.map((item) => `
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

function renderBacktestPositionsTable(positions) {
  const rows = positions || [];
  renderDataTable('backtest-positions-table', rows, [
    {
      id: 'instrument',
      key: 'instrument',
      label: 'Instrument',
      render: (row) => renderInstrumentLink(row.instrument, toDateLabel(state.backtest.selectedDate)),
      sortValue: (row) => row.instrument,
    },
    {
      key: 'qty',
      label: 'Qty',
      render: (row) => formatNumber(row.qty, 0),
      sortValue: (row) => toNumber(row.qty),
    },
    {
      key: 'avg_cost',
      label: 'Avg Cost',
      render: (row) => formatNumber(row.avg_cost, 3),
      sortValue: (row) => toNumber(row.avg_cost),
    },
    {
      key: 'last_close',
      label: 'Last Close',
      render: (row) => row.last_close != null ? formatNumber(row.last_close, 3) : '-',
      sortValue: (row) => toNumber(row.last_close),
    },
    {
      key: 'market_value',
      label: 'Market Value',
      render: (row) => row.market_value != null ? formatNumber(row.market_value, 0) : '-',
      sortValue: (row) => toNumber(row.market_value),
    },
    {
      key: 'unrealized_pnl',
      label: 'Unrealized PnL',
      render: (row) => row.unrealized_pnl != null ? formatNumber(row.unrealized_pnl, 0) : '-',
      sortValue: (row) => toNumber(row.unrealized_pnl),
    },
    {
      key: 'realized_pnl',
      label: 'Realized PnL',
      render: (row) => formatNumber(row.realized_pnl, 0),
      sortValue: (row) => toNumber(row.realized_pnl),
    },
  ], {
    tableKey: 'backtest-positions',
    emptyMessage: 'No positions on this date',
  });
}

// Monotonic request token: a slow /behavior/episodes response must never
// overwrite a newer run's panel (P0.7 stale-async guard).
let episodeRequestSeq = 0;

// Pure decision — kept argument-only so it is unit-testable in Node.  A
// response may write state only when BOTH the request token still matches AND
// the run it was requested for is still the active run.  The second check
// covers the window where a run switch is in flight but has not yet fired a
// fresh episodes request (e.g. loadBacktest is still fetching summary/daily).
function isEpisodeResponseStale({ seq, requestedRunId, requestSeq, activeRunId }) {
  return seq !== requestSeq || requestedRunId !== activeRunId;
}

async function loadBacktestEpisodes({ force = false } = {}) {
  const runId = state.context.runId || byId('backtest-run-select').value;
  if (!runId) return;
  if (!force && state.backtest.episodesLoaded) return;
  state.backtest.episodesLoaded = true;
  const seq = ++episodeRequestSeq;
  const requestedRunId = runId;
  renderEpisodeLoading();
  try {
    const payload = await getJson(`/api/backtest-runs/${runId}/behavior/episodes`, { useCache: false });
    const activeRunId = state.context.runId || byId('backtest-run-select').value;
    if (isEpisodeResponseStale({ seq, requestedRunId, requestSeq: episodeRequestSeq, activeRunId })) return;
    const data = unwrapData(payload) || {};
    state.backtest.episodes = data.episodes || [];
    state.backtest.episodeSummary = data.summary || {};
    state.backtest.episodeMeta = (payload && payload.meta) || {};
  } catch (error) {
    const activeRunId = state.context.runId || byId('backtest-run-select').value;
    if (isEpisodeResponseStale({ seq, requestedRunId, requestSeq: episodeRequestSeq, activeRunId })) return;
    state.backtest.episodes = null;
    state.backtest.episodeSummary = null;
    state.backtest.episodeMeta = null;
    renderEpisodeAnalyticsError(error);
    return;
  }
  renderEpisodeAnalytics();
}

function renderEpisodeLoading() {
  // Clear the previous run's panel so a slow first load doesn't read as stale.
  byId('backtest-episode-count').textContent = 'Loading episodes…';
  const noteEl = byId('episode-capture-eligible-note');
  if (noteEl) noteEl.textContent = '(MFE > 10%)';
  renderMetricCard('episode-total', '…', 'episode-total-note', 'total / closed / open');
  renderMetricCard('episode-win-rate', '…', 'episode-win-rate-note', 'closed episodes');
  renderMetricCard('episode-avg-return', '…', 'episode-avg-return-note', 'cash-weighted,含费');
  renderMetricCard('episode-median-return', '…', 'episode-median-return-note', 'closed');
  renderMetricCard('episode-avg-holding', '…', 'episode-avg-holding-note', 'trading days');
  const loading = '<div class="empty">Loading episodes…</div>';
  byId('backtest-episode-capture-scatter').innerHTML = loading;
  byId('backtest-episode-capture-ratio-dist').innerHTML = loading;
  byId('backtest-episode-mfe-distribution').innerHTML = loading;
  byId('backtest-episode-stop-quality').innerHTML = loading;
  byId('backtest-episode-horizon-buckets').innerHTML = loading;
  byId('backtest-episode-pnl-concentration').innerHTML = loading;
  byId('backtest-episode-return-dist').innerHTML = loading;
  byId('backtest-episode-holding-dist').innerHTML = loading;
  byId('backtest-episode-mfe-mae-scatter').innerHTML = loading;
  byId('backtest-episode-exit-reason-table').innerHTML = loading;
  byId('backtest-episode-detail-table').innerHTML = loading;
}

function renderEpisodeAnalytics() {
  const episodes = state.backtest.episodes || [];
  const summary = state.backtest.episodeSummary || {};
  let countText = summary.total_episodes != null ? `${summary.total_episodes} episodes` : '';
  // When the API truncated the per-episode detail rows, the charts and tables
  // are computed over a sample of the full episode set — make that explicit so
  // the denominator is not read as "all episodes".
  const meta = state.backtest.episodeMeta || {};
  if (meta.truncated) {
    const shown = meta.returned_episodes != null ? String(meta.returned_episodes) : String(episodes.length);
    const total = meta.total_episodes != null ? String(meta.total_episodes) : (summary.total_episodes != null ? String(summary.total_episodes) : '');
    if (total) countText += ` · detail charts use ${shown} / ${total}`;
  }
  byId('backtest-episode-count').textContent = countText;
  renderEpisodeSummaryRow(summary);
  renderEpisodeCaptureScatter(episodes);
  renderEpisodeCaptureRatioDist(summary);
  renderEpisodeMfeDistribution(summary);
  renderEpisodeStopQuality(summary);
  renderEpisodeHorizonBuckets(summary);
  renderEpisodePnlConcentration(summary);
  renderEpisodeReturnDist(episodes);
  renderEpisodeHoldingDist(episodes);
  renderEpisodeMfeMaeScatter(episodes);
  renderEpisodeExitReasonTable(summary);
  renderEpisodeDetailTable(episodes);
}

function renderEpisodeAnalyticsError(error) {
  byId('backtest-episode-count').textContent = '';
  const noteEl = byId('episode-capture-eligible-note');
  if (noteEl) noteEl.textContent = '(MFE > 10%)';
  renderMetricCard('episode-total', '-', 'episode-total-note', 'total / closed / open');
  renderMetricCard('episode-win-rate', '-', 'episode-win-rate-note', 'closed episodes');
  renderMetricCard('episode-avg-return', '-', 'episode-avg-return-note', 'cash-weighted,含费');
  renderMetricCard('episode-median-return', '-', 'episode-median-return-note', 'closed');
  renderMetricCard('episode-avg-holding', '-', 'episode-avg-holding-note', 'trading days');
  const message = `<div class="empty">${escapeHtml(error.message || 'Episodes unavailable')}</div>`;
  renderChartError('backtest-episode-capture-scatter', 'No episode data');
  renderChartError('backtest-episode-pnl-concentration', 'No episode data');
  renderChartError('backtest-episode-return-dist', 'No episode data');
  renderChartError('backtest-episode-holding-dist', 'No episode data');
  renderChartError('backtest-episode-mfe-mae-scatter', 'No episode data');
  byId('backtest-episode-capture-ratio-dist').innerHTML = message;
  byId('backtest-episode-mfe-distribution').innerHTML = message;
  byId('backtest-episode-stop-quality').innerHTML = message;
  byId('backtest-episode-horizon-buckets').innerHTML = message;
  byId('backtest-episode-exit-reason-table').innerHTML = message;
  byId('backtest-episode-detail-table').innerHTML = message;
}

function renderEpisodeSummaryRow(summary) {
  const total = summary.total_episodes != null ? String(summary.total_episodes) : '-';
  renderMetricCard('episode-total', total, 'episode-total-note', `total / closed ${summary.closed_episodes ?? 0} / open ${summary.open_episodes ?? 0}`);
  renderMetricCard('episode-win-rate', formatPercent(summary.win_rate, 1), 'episode-win-rate-note', 'closed episodes');
  renderMetricCard('episode-avg-return', formatPercent(summary.avg_return, 1), 'episode-avg-return-note', 'cash-weighted,含费');
  renderMetricCard('episode-median-return', formatPercent(summary.median_return, 1), 'episode-median-return-note', 'closed');
  renderMetricCard('episode-avg-holding', summary.avg_holding_days != null ? formatNumber(summary.avg_holding_days, 1) : '-', 'episode-avg-holding-note', 'trading days');
}

// The MFE → Realized Capture scatter plots MFE vs realized_return and draws a
// 100% capture (y=x) reference.  That comparison is only valid on
// capture-eligible episodes — one-buy / one-full-sell simple round trips where
// the cashflow return and the avg_cost excursion are directly comparable.
// Complex episodes (any partial sell, any re-add, or an open position) must
// never appear, so the filter is a pure predicate that the node unit test can
// lock down.
function isCaptureScatterEligible(item) {
  return item.exit_reason !== 'open'
    && item.capture_eligible === true
    && item.MFE != null
    && item.realized_return != null;
}

function renderEpisodeCaptureScatter(episodes) {
  const points = (episodes || []).filter(isCaptureScatterEligible);
  if (!points.length) {
    renderChartError('backtest-episode-capture-scatter', 'No capture-eligible closed episodes with excursion data');
    return;
  }
  const palette = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'];
  const reasons = [...new Set(points.map((p) => p.exit_reason || 'open'))];
  const color = (reason) => palette[reasons.indexOf(reason) % palette.length];
  const layout = plotlyBaseLayout({
    title: 'MFE → Realized Capture',
    height: 320,
    yAxisTitle: 'Final Return %',
    hoverMode: 'closest',
  });
  layout.xaxis = { title: { text: 'MFE %' }, type: 'linear' };
  const mfeMax = Math.max(...points.map((p) => Number(p.MFE) * 100), 1);
  const traces = reasons.map((reason) => {
    const pts = points.filter((p) => (p.exit_reason || 'open') === reason);
    return {
      type: 'scatter',
      mode: 'markers',
      name: reason,
      x: pts.map((p) => Number(p.MFE) * 100),
      y: pts.map((p) => Number(p.realized_return) * 100),
      customdata: pts.map((p) => [p.symbol, p.entry_date || '', p.exit_reason || 'open']),
      marker: { size: 8, color: color(reason), line: { color: 'rgba(31,41,51,0.35)', width: 0.5 }, opacity: 0.85 },
      hovertemplate: '%{customdata[0]}<br>MFE %{x:.1f}%<br>Final %{y:.1f}%<br>Reason %{customdata[2]}<extra></extra>',
    };
  });
  traces.push({
    type: 'scatter',
    mode: 'lines',
    name: '100% capture',
    x: [0, mfeMax],
    y: [0, mfeMax],
    line: { color: 'rgba(127,127,127,0.6)', width: 1, dash: 'dot' },
    hoverinfo: 'skip',
  });
  renderPlotlyChart('backtest-episode-capture-scatter', traces, layout, {
    onClick: (evt) => {
      const pt = evt && evt.points && evt.points[0];
      if (!pt || !pt.customdata) return;
      const [symbol, entryDate] = pt.customdata;
      if (symbol) jumpToCase(symbol, toDateLabel(entryDate || state.backtest.selectedDate));
    },
  });
}

function renderEpisodeCaptureRatioDist(summary) {
  const buckets = summary.capture_ratio_distribution || [];
  // Make the capture/giveback sample denominator explicit: only one-buy /
  // one-full-sell simple round trips are eligible for the excursion-vs-cashflow
  // comparison, so these buckets are computed over `capture_eligible_count`.
  const eligible = summary.capture_eligible_count;
  const total = summary.total_episodes;
  const noteEl = byId('episode-capture-eligible-note');
  if (noteEl) {
    const parts = ['MFE > 10%'];
    if (eligible != null && total != null) parts.push(`eligible ${eligible} / ${total}`);
    noteEl.textContent = `(${parts.join('; ')})`;
  }
  renderDataTable('backtest-episode-capture-ratio-dist', buckets, [
    { key: 'bucket', label: 'Capture', sortValue: (row) => row.bucket },
    { key: 'count', label: 'Episodes', sortValue: (row) => toNumber(row.count) },
    { key: 'win_rate', label: 'Win Rate', render: (row) => formatPercent(row.win_rate, 1), sortValue: (row) => toNumber(row.win_rate) },
    { key: 'avg_return', label: 'Avg Return', render: (row) => formatPercent(row.avg_return, 1), sortValue: (row) => toNumber(row.avg_return) },
  ], {
    tableKey: 'backtest-episode-capture-ratio-dist',
    emptyMessage: 'No winners with MFE > 10%',
  });
}

function renderEpisodeMfeDistribution(summary) {
  const buckets = summary.mfe_distribution || [];
  renderDataTable('backtest-episode-mfe-distribution', buckets, [
    { key: 'bucket', label: 'MFE Bucket', sortValue: (row) => row.bucket },
    { key: 'count', label: 'Episodes', sortValue: (row) => toNumber(row.count) },
    { key: 'median_mfe', label: 'Median MFE', render: (row) => formatPercent(row.median_mfe, 1), sortValue: (row) => toNumber(row.median_mfe) },
    { key: 'median_final_return', label: 'Median Final Ret', render: (row) => formatPercent(row.median_final_return, 1), sortValue: (row) => toNumber(row.median_final_return) },
    { key: 'median_giveback_return', label: 'Median Giveback', render: (row) => formatPercent(row.median_giveback_return, 1), sortValue: (row) => toNumber(row.median_giveback_return) },
    { key: 'median_capture_ratio', label: 'Median Capture', render: (row) => formatPercent(row.median_capture_ratio, 1), sortValue: (row) => toNumber(row.median_capture_ratio) },
    { key: 'median_holding_days', label: 'Median Days', sortValue: (row) => toNumber(row.median_holding_days) },
  ], {
    tableKey: 'backtest-episode-mfe-distribution',
    emptyMessage: 'No MFE data',
  });
}

function renderEpisodeStopQuality(summary) {
  const sq = summary.stop_quality || {};
  const rows = [
    { metric: 'Hard-Stop Exits', value: sq.hard_stop_count != null ? String(sq.hard_stop_count) : '-', type: 'num' },
    { metric: 'Post-Exit Positive 20d', value: formatPercent(sq.post_exit_positive_rate_20d, 1), type: 'pct' },
    { metric: 'Post-Exit Positive 60d', value: formatPercent(sq.post_exit_positive_rate_60d, 1), type: 'pct' },
    { metric: 'Avg Post-Exit 20d', value: formatPercent(sq.avg_post_exit_20d, 1), type: 'pct' },
    { metric: 'Avg Post-Exit 60d', value: formatPercent(sq.avg_post_exit_60d, 1), type: 'pct' },
    { metric: 'Avg MFE', value: formatPercent(sq.avg_mfe, 1), type: 'pct' },
    { metric: 'Avg MAE', value: formatPercent(sq.avg_mae, 1), type: 'pct' },
    { metric: 'Avg Return', value: formatPercent(sq.avg_return, 1), type: 'pct' },
  ];
  renderDataTable('backtest-episode-stop-quality', rows, [
    { key: 'metric', label: 'Metric', sortable: false },
    { key: 'value', label: 'Value', render: (row) => (row.type === 'pct' ? row.value : `<span class="muted-text">${escapeHtml(row.value)}</span>`), sortable: false },
  ], {
    tableKey: 'backtest-episode-stop-quality',
    hideToolbar: true,
    emptyMessage: 'No hard-stop exits',
  });
}

function renderEpisodeHorizonBuckets(summary) {
  const horizons = (summary.holding_horizons || [])
    .map((row) => ({
      ...row,
      reasons: Object.entries(row.exit_reasons || {})
        .sort((a, b) => b[1] - a[1])
        .map(([reason, count]) => `${reason} × ${count}`)
        .join(', '),
    }));
  renderDataTable('backtest-episode-horizon-buckets', horizons, [
    { key: 'bucket', label: 'Holding Days', sortValue: (row) => row.bucket },
    { key: 'count', label: 'Episodes', sortValue: (row) => toNumber(row.count) },
    { key: 'win_rate', label: 'Win Rate', render: (row) => formatPercent(row.win_rate, 1), sortValue: (row) => toNumber(row.win_rate) },
    { key: 'median_return', label: 'Median Return', render: (row) => formatPercent(row.median_return, 1), sortValue: (row) => toNumber(row.median_return) },
    { key: 'median_mfe', label: 'Median MFE', render: (row) => formatPercent(row.median_mfe, 1), sortValue: (row) => toNumber(row.median_mfe) },
    { key: 'median_mae', label: 'Median MAE', render: (row) => formatPercent(row.median_mae, 1), sortValue: (row) => toNumber(row.median_mae) },
    { key: 'reasons', label: 'Exit Reasons', render: (row) => `<span class="muted-text">${escapeHtml(row.reasons)}</span>`, sortable: false },
  ], {
    tableKey: 'backtest-episode-horizon-buckets',
    emptyMessage: 'No holding-day data',
  });
}

function renderEpisodePnlConcentration(summary) {
  const pc = summary.pnl_concentration;
  if (!pc || !pc.cumulative_curve || !pc.cumulative_curve.length) {
    renderChartError('backtest-episode-pnl-concentration', 'No realized PnL data');
    return;
  }
  const curve = pc.cumulative_curve;
  const fmtShare = (v) => v == null ? '-' : formatPercent(v, 0);
  const fmtYuan = (v) => v == null ? '-' : formatNumber(v, 0);
  const shares = [
    `Top1% ${fmtShare(pc.top_1pct_share)}`,
    `Top5% ${fmtShare(pc.top_5pct_share)}`,
    `Top10% ${fmtShare(pc.top_10pct_share)}`,
    `Top1 ep ${fmtShare(pc.top_1_episode_share)}`,
    `Top5 ep ${fmtShare(pc.top_5_episode_share)}`,
  ].join(' · ');
  const excluding = [
    `ex-Top1 ¥${fmtYuan(pc.pnl_ex_top1)}`,
    `ex-Top5 ¥${fmtYuan(pc.pnl_ex_top5)}`,
    `ex-Top10% ¥${fmtYuan(pc.pnl_ex_top10pct)}`,
  ].join(' · ');
  const layout = plotlyBaseLayout({
    title: `Cumulative PnL (¥) · ${shares}<br>${excluding} · n=${pc.curve_points}`,
    height: 260,
    yAxisTitle: 'Cumulative PnL (¥)',
    hoverMode: 'closest',
  });
  layout.xaxis = { title: { text: 'Episode rank (best → worst)' }, type: 'linear' };
  renderPlotlyChart('backtest-episode-pnl-concentration', [
    {
      type: 'bar',
      name: 'Episode PnL',
      x: curve.map((p) => p.rank),
      y: curve.map((p) => p.pnl),
      marker: { color: CHART_COLORS.accent, opacity: 0.7 },
      hovertemplate: 'Rank %{x}<br>PnL %{y:,.0f}<extra></extra>',
    },
    {
      type: 'scatter',
      mode: 'lines',
      name: 'Cumulative',
      x: curve.map((p) => p.rank),
      y: curve.map((p) => p.cumulative),
      line: { color: CHART_COLORS.strategy, width: 2 },
      hovertemplate: 'Rank %{x}<br>Cumulative %{y:,.0f}<extra></extra>',
    },
  ], layout);
}

function renderEpisodeReturnDist(episodes) {
  const returns = (episodes || [])
    .filter((item) => item.exit_reason !== 'open' && item.realized_return != null)
    .map((item) => Number(item.realized_return) * 100);
  if (!returns.length) {
    renderChartError('backtest-episode-return-dist', 'No closed episodes');
    return;
  }
  const layout = plotlyBaseLayout({
    title: 'Realized Return Distribution (%)',
    height: 240,
    yAxisTitle: 'Count',
  });
  layout.xaxis = { title: { text: 'Return %' } };
  renderPlotlyChart('backtest-episode-return-dist', [{
    type: 'histogram',
    x: returns,
    nbinsx: 40,
    marker: { color: CHART_COLORS.strategy, line: { color: '#fff', width: 0.5 } },
    hovertemplate: 'Return %{x:.1f}%<br>Count: %{y}<extra></extra>',
  }], layout);
}

function renderEpisodeHoldingDist(episodes) {
  const days = (episodes || [])
    .map((item) => item.holding_days)
    .filter((value) => value != null && Number.isFinite(Number(value)));
  if (!days.length) {
    renderChartError('backtest-episode-holding-dist', 'No holding-day data');
    return;
  }
  const layout = plotlyBaseLayout({
    title: 'Holding Days Distribution',
    height: 240,
    yAxisTitle: 'Count',
  });
  layout.xaxis = { title: { text: 'Trading days' } };
  renderPlotlyChart('backtest-episode-holding-dist', [{
    type: 'histogram',
    x: days,
    nbinsx: 30,
    marker: { color: CHART_COLORS.accent, line: { color: '#fff', width: 0.5 } },
    hovertemplate: 'Days %{x}<br>Count: %{y}<extra></extra>',
  }], layout);
}

function renderEpisodeMfeMaeScatter(episodes) {
  const closed = (episodes || []).filter((item) => item.exit_reason !== 'open' && item.realized_return != null);
  const points = closed.filter((item) => item.MFE != null && item.MAE != null);
  if (!points.length) {
    renderChartError('backtest-episode-mfe-mae-scatter', 'No closed episodes with excursion data');
    return;
  }
  const colors = points.map((item) => Number(item.realized_return) * 100);
  const colorMin = Math.min(...colors);
  const colorMax = Math.max(...colors);
  const layout = plotlyBaseLayout({
    title: 'MFE vs MAE (color = realized return %)',
    height: 280,
    yAxisTitle: 'MAE %',
  });
  layout.xaxis = { title: { text: 'MFE %' } };
  renderPlotlyChart('backtest-episode-mfe-mae-scatter', [{
    type: 'scatter',
    mode: 'markers',
    x: points.map((item) => Number(item.MFE) * 100),
    y: points.map((item) => Number(item.MAE) * 100),
    text: points.map((item) => item.symbol),
    customdata: colors,
    marker: {
      size: 8,
      color: colors,
      colorscale: 'RdYlGn',
      cmin: colorMin,
      cmax: colorMax,
      showscale: true,
      colorbar: { title: { text: 'Return %' }, thickness: 12 },
      line: { color: 'rgba(31,41,51,0.4)', width: 0.5 },
    },
    hovertemplate: '%{text}<br>MFE %{x:.1f}%<br>MAE %{y:.1f}%<br>Return %{customdata:.1f}%<extra></extra>',
  }], layout);
}

function renderEpisodeExitReasonTable(summary) {
  const rows = (summary.by_exit_reason || [])
    .map((item) => ({ ...item, reason: item.exit_reason }))
    .filter((item) => item.count != null);
  renderDataTable('backtest-episode-exit-reason-table', rows, [
    {
      key: 'reason',
      label: 'Exit Reason',
      render: (row) => makeBadge(row.reason || row.exit_reason, row.reason === 'open' ? 'neutral' : 'success'),
      sortValue: (row) => row.reason || row.exit_reason,
    },
    {
      key: 'count',
      label: 'Count',
      sortValue: (row) => toNumber(row.count),
    },
    {
      key: 'win_rate',
      label: 'Win Rate',
      render: (row) => formatPercent(row.win_rate, 1),
      sortValue: (row) => toNumber(row.win_rate),
    },
    {
      key: 'avg_return',
      label: 'Avg Return',
      render: (row) => formatPercent(row.avg_return, 1),
      sortValue: (row) => toNumber(row.avg_return),
    },
    {
      key: 'median_return',
      label: 'Median Return',
      render: (row) => formatPercent(row.median_return, 1),
      sortValue: (row) => toNumber(row.median_return),
    },
    {
      key: 'avg_mfe',
      label: 'Avg MFE',
      render: (row) => formatPercent(row.avg_mfe, 1),
      sortValue: (row) => toNumber(row.avg_mfe),
    },
    {
      key: 'median_mfe',
      label: 'Median MFE',
      render: (row) => formatPercent(row.median_mfe, 1),
      sortValue: (row) => toNumber(row.median_mfe),
    },
    {
      key: 'median_mae',
      label: 'Median MAE',
      render: (row) => formatPercent(row.median_mae, 1),
      sortValue: (row) => toNumber(row.median_mae),
    },
    {
      key: 'median_capture',
      label: 'Median Capture',
      render: (row) => formatPercent(row.median_capture, 1),
      sortValue: (row) => toNumber(row.median_capture),
    },
    {
      key: 'avg_giveback_return',
      label: 'Avg Giveback Ret',
      render: (row) => formatPercent(row.avg_giveback_return, 1),
      sortValue: (row) => toNumber(row.avg_giveback_return),
    },
    {
      key: 'median_giveback_return',
      label: 'Median Giveback Ret',
      render: (row) => formatPercent(row.median_giveback_return, 1),
      sortValue: (row) => toNumber(row.median_giveback_return),
    },
    {
      key: 'avg_giveback_ratio',
      label: 'Avg Giveback Ratio',
      render: (row) => formatPercent(row.avg_giveback_ratio, 1),
      sortValue: (row) => toNumber(row.avg_giveback_ratio),
    },
    {
      key: 'median_giveback_ratio',
      label: 'Median Giveback Ratio',
      render: (row) => formatPercent(row.median_giveback_ratio, 1),
      sortValue: (row) => toNumber(row.median_giveback_ratio),
    },
    {
      key: 'avg_post_exit_20d',
      label: 'Avg Post 20d',
      render: (row) => row.post_exit_20d_count ? `${formatPercent(row.avg_post_exit_20d, 1)} (${row.post_exit_20d_count})` : '-',
      sortValue: (row) => toNumber(row.avg_post_exit_20d),
    },
  ], {
    tableKey: 'backtest-episode-exit-reason',
    emptyMessage: 'No closed episodes',
  });
}

function renderEpisodeDetailTable(episodes) {
  renderDataTable('backtest-episode-detail-table', episodes || [], [
    {
      id: 'symbol',
      key: 'symbol',
      label: 'Symbol',
      render: (row) => renderInstrumentLink(row.symbol, toDateLabel(row.entry_date || state.backtest.selectedDate)),
      sortValue: (row) => row.symbol,
      filterValue: (row) => row.symbol,
    },
    {
      key: 'exit_reason',
      label: 'Exit',
      render: (row) => makeBadge(row.exit_reason || 'open', row.exit_reason === 'open' ? 'neutral' : row.exit_reason === 'hard_stop' ? 'danger' : 'success'),
      sortValue: (row) => row.exit_reason,
    },
    {
      key: 'entry_date',
      label: 'Entry',
      sortValue: (row) => toDateLabel(row.entry_date),
    },
    {
      key: 'exit_date',
      label: 'Exit Date',
      render: (row) => row.exit_date != null ? toDateLabel(row.exit_date) : '-',
      sortValue: (row) => toDateLabel(row.exit_date || ''),
    },
    {
      key: 'holding_days',
      label: 'Days',
      sortValue: (row) => toNumber(row.holding_days),
    },
    {
      id: 'return',
      label: 'Return',
      render: (row) => {
        if (row.exit_reason === 'open' && row.unrealized_return != null) {
          return `<span class="badge badge-warning">${escapeHtml(formatPercent(row.unrealized_return, 1))} (open)</span>`;
        }
        if (row.realized_return != null) {
          return makeBadge(formatPercent(row.realized_return, 1), row.realized_return >= 0 ? 'success' : 'danger');
        }
        return '-';
      },
      sortValue: (row) => row.exit_reason === 'open' ? toNumber(row.unrealized_return) : toNumber(row.realized_return),
    },
    {
      key: 'episode_pnl',
      label: 'PnL (¥)',
      render: (row) => row.episode_pnl != null ? `${row.episode_pnl >= 0 ? '+' : ''}${formatNumber(row.episode_pnl, 0)}` : '-',
      sortValue: (row) => toNumber(row.episode_pnl),
    },
    {
      key: 'capture_ratio',
      label: 'Capture',
      render: (row) => formatPercent(row.capture_ratio, 1),
      sortValue: (row) => toNumber(row.capture_ratio),
    },
    {
      key: 'giveback_return',
      label: 'Giveback Ret',
      render: (row) => formatPercent(row.giveback_return, 1),
      sortValue: (row) => toNumber(row.giveback_return),
    },
    {
      key: 'giveback_ratio',
      label: 'Giveback Ratio',
      render: (row) => formatPercent(row.giveback_ratio, 1),
      sortValue: (row) => toNumber(row.giveback_ratio),
    },
    {
      key: 'valuation_date',
      label: 'Val. Date',
      render: (row) => row.valuation_date != null ? toDateLabel(row.valuation_date) : '-',
      sortValue: (row) => toDateLabel(row.valuation_date || ''),
    },
    {
      key: 'MFE',
      label: 'MFE',
      render: (row) => formatPercent(row.MFE, 1),
      sortValue: (row) => toNumber(row.MFE),
    },
    {
      key: 'MAE',
      label: 'MAE',
      render: (row) => formatPercent(row.MAE, 1),
      sortValue: (row) => toNumber(row.MAE),
    },
    {
      key: 'max_drawdown_from_peak',
      label: 'Max DD',
      render: (row) => formatPercent(row.max_drawdown_from_peak, 1),
      sortValue: (row) => toNumber(row.max_drawdown_from_peak),
    },
    {
      key: 'entry_score',
      label: 'Entry Score',
      render: (row) => row.entry_score != null ? formatNumber(row.entry_score, 3) : '-',
      sortValue: (row) => toNumber(row.entry_score),
    },
    {
      key: 'exit_score',
      label: 'Exit Score',
      render: (row) => row.exit_score != null ? formatNumber(row.exit_score, 3) : '-',
      sortValue: (row) => toNumber(row.exit_score),
    },
    {
      key: 'score_delta_5d',
      label: 'Δ5d',
      render: (row) => row.score_delta_5d != null ? formatNumber(row.score_delta_5d, 3) : '-',
      sortValue: (row) => toNumber(row.score_delta_5d),
    },
    {
      key: 'score_delta_20d',
      label: 'Δ20d',
      render: (row) => row.score_delta_20d != null ? formatNumber(row.score_delta_20d, 3) : '-',
      sortValue: (row) => toNumber(row.score_delta_20d),
    },
    {
      key: 'post_exit_return_20d',
      label: 'Post 20d',
      render: (row) => row.post_exit_return_20d != null ? formatPercent(row.post_exit_return_20d, 1) : '-',
      sortValue: (row) => toNumber(row.post_exit_return_20d),
    },
    {
      key: 'post_exit_return_60d',
      label: 'Post 60d',
      render: (row) => row.post_exit_return_60d != null ? formatPercent(row.post_exit_return_60d, 1) : '-',
      sortValue: (row) => toNumber(row.post_exit_return_60d),
    },
  ], {
    tableKey: 'backtest-episode-detail',
    onRowClick: (row) => jumpToCase(row.symbol, toDateLabel(row.entry_date || state.backtest.selectedDate)),
    emptyMessage: 'No episodes for this backtest run',
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
    const positions = state.backtest.positionsByDate.get(tradeDate) || [];
    renderBacktestContextMeta(orders);
    renderBacktestOrdersTable(orders);
    renderBacktestContributors(orders);
    renderBacktestContextLinks();
    renderBacktestPositionsTable(positions);
    return orders;
  }
  try {
    const [payload, positionsPayload] = await Promise.all([
      getJson(`/api/backtest-runs/${runId}/orders?trade_date=${tradeDate}`),
      getJson(`/api/backtest-runs/${runId}/positions?trade_date=${tradeDate}`).catch(() => null),
    ]);
    const items = unwrapItems(payload);
    const positions = positionsPayload ? unwrapItems(positionsPayload) : [];
    state.backtest.ordersByDate.set(tradeDate, items);
    state.backtest.positionsByDate.set(tradeDate, positions);
    renderBacktestContextMeta(items);
    renderBacktestOrdersTable(items);
    renderBacktestContributors(items);
    renderBacktestContextLinks();
    renderBacktestPositionsTable(positions);
    return items;
  } catch (error) {
    byId('backtest-orders-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    byId('backtest-contributors').innerHTML = '<div class="empty">Contributor list unavailable</div>';
    byId('backtest-positions-table').innerHTML = '<div class="empty">Positions unavailable</div>';
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
    state.defaultBacktestRunId = state.backtestRuns[0]?.run_id || '';

    let runId = state.context.runId || byId('backtest-run-select').value;
    if (!runId) {
      runId = byId('backtest-run-select').value || state.defaultBacktestRunId || state.backtestRuns[0]?.run_id || '';
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
    state.backtest.sections = [];
    state.backtest.sectionArtifacts = {};
    state.backtest.ordersByDate = new Map();
    state.backtest.positionsByDate = new Map();
    state.backtest.episodes = null;
    state.backtest.episodeSummary = null;
    state.backtest.episodeMeta = null;
    state.backtest.episodesLoaded = false;
    renderParameterSummary(summary);
    renderBacktestRunContext();
    renderBacktestSignalMetrics(summary);
    summarizeBacktestMetrics(summary, dailyItems);
    renderBacktestDailyTable();
    renderBacktestSections();

    const selectedDate = dailyItems.some((item) => item.trade_date === state.context.tradeDate)
      ? state.context.tradeDate
      : dailyItems[dailyItems.length - 1]?.trade_date || '';
    if (!selectedDate) throw new Error('No daily backtest payload found');
    selectBacktestDate(selectedDate);
    loadBacktestEpisodes();
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
    renderChartError('backtest-monthly-chart', error.message);
    renderChartError('backtest-windows-chart', error.message);
    renderChartError('backtest-signal-windows-chart', error.message);
    renderChartError('backtest-return-dist-chart', error.message);
    renderChartError('backtest-ic-dist-chart', error.message);
    renderEpisodeAnalyticsError(error);
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

    // Fire bars (fast) and case bundle (slow) in parallel
    const barsPromise = getJson(`/api/bars?instrument_id=${encodeURIComponent(instrumentId)}&price_mode=${priceMode}`, { useCache: false }).catch(() => null);
    const casePromise = getJson(`/api/cases/${executionDate}:${instrumentId}:${priceMode}`, { useCache: false });

    let backtestTrades = [];
    if (backtestRunId) {
      const tradePayload = await getJson(`/api/backtest-runs/${backtestRunId}/orders?instrument_id=${encodeURIComponent(instrumentId)}&limit=5000`, { useCache: false });
      backtestTrades = unwrapItems(tradePayload);
    }

    const priceKeys = priceMode === 'fq'
      ? { open: 'adj_open', high: 'adj_high', low: 'adj_low', close: 'adj_close' }
      : { open: 'open', high: 'high', low: 'low', close: 'close' };

    // ---- STEP 1: render candlestick chart from fast bars endpoint ----
    const barsResponse = await barsPromise;
    const rawBars = barsResponse ? unwrapItems(barsResponse) : [];
    const chartBars = rawBars.map((item) => ({
      trade_date: toDateLabel(item.trade_date),
      open: item[priceKeys.open],
      high: item[priceKeys.high],
      low: item[priceKeys.low],
      close: item[priceKeys.close],
      volume: item.volume,
    }));
    if (chartBars.length) {
      renderCandlestickChart('case-bars-chart', chartBars, [], instrumentId, {
        selectedDate: toDateLabel(executionDate),
        volumeBars: chartBars,
      });
      const loadedStart = chartBars[0].trade_date;
      const loadedEnd = chartBars[chartBars.length - 1].trade_date;
      byId('case-meta').innerHTML = [
        renderInstrumentLink(instrumentId, executionDate),
        makeBadge(priceMode),
        renderTradeDateLink(toDateLabel(executionDate)),
        `<span class="muted-text">${escapeHtml(loadedStart)} -> ${escapeHtml(loadedEnd)}</span>`,
        renderActionButton('Raw', `setCasePriceMode('raw')`, priceMode === 'raw' ? 'primary' : 'ghost'),
        renderActionButton('FQ', `setCasePriceMode('fq')`, priceMode === 'fq' ? 'primary' : 'ghost'),
      ].filter(Boolean).join(' ');
    }

    // ---- STEP 2: wait for full case bundle (features, signal, benchmarks) ----
    const caseResponse = await casePromise;
    const payload = unwrapData(caseResponse);
    state.caseData = payload;

    const replayOrderMarkers = (payload.orders || []).map((item) => ({
      trade_date: payload.trade_date,
      value: toNumber(item.price),
      side: item.side,
      quantity: item.quantity,
      source: 'order',
    }));
    const tradeMarkers = [...backtestTrades.map((item) => ({
      trade_date: item.date || item.trade_date,
      value: toNumber(item.deal_price ?? item.price),
      side: item.side,
      quantity: toNumber(item.filled_amount ?? item.amount ?? item.filled_qty),
      source: 'fill',
    })), ...replayOrderMarkers];

    // Update candlestick chart with trade markers if any
    if (chartBars.length) {
      renderCandlestickChart('case-bars-chart', chartBars, tradeMarkers, payload.instrument_id, {
        selectedDate: toDateLabel(payload.trade_date),
        volumeBars: chartBars,
      });
    }
    state.caseFeatureSnapshot = payload.feature_snapshot.features || {};
    byId('case-feature-count').textContent = `${Object.keys(state.caseFeatureSnapshot).length} features`;
    renderCaseFeatureTable(state.caseFeatureSnapshot);
    renderCaseLoop(payload, backtestTrades);
    renderCaseSignalSummary(payload, backtestTrades);
    renderCaseLinks(payload);
    byId('case-explanation').innerHTML = buildCaseExplanation(payload, backtestTrades);
    byId('case-signal').textContent = JSON.stringify({ signal_snapshot: payload.signal_snapshot, trade_markers: backtestTrades, links: payload.links }, null, 2);

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
    const healthParams = new URLSearchParams({ trade_date: tradeDate, universe: state.context.universe || 'csi300' });
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
    { label: 'Intent Count', value: String(summary.intent_count || (replay.final_orders || []).length || 0), note: 'reported by order intents' },
    { label: 'Previous Positions', value: String((replay.previous_positions || []).length), note: 'account carry-over' },
    { label: 'Candidates', value: String((replay.scored_candidates || []).length), note: 'ranking payload' },
    { label: 'Selected Targets', value: String((replay.selected_targets || []).length), note: 'filtered output' },
    { label: 'Final Orders', value: String((replay.final_orders || []).length), note: 'execution list' },
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
      value: (replay.scored_candidates || []).length,
      note: 'candidate rows with scores',
    },
    {
      stage: 'Filtered',
      value: (replay.selected_targets || []).length || (replay.final_orders || []).length || 0,
      note: (replay.selected_targets || []).length ? 'selected targets' : 'fallback to final orders count',
    },
    {
      stage: 'Portfolio',
      value: (replay.previous_positions || []).length,
      note: 'previous positions linked on right',
    },
    {
      stage: 'Orders',
      value: (replay.final_orders || []).length,
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
    renderCandlestickChart('replay-instrument-chart', chartBars, replayOrderMarkers, instrumentId, {
      selectedDate: toDateLabel(replay.trade_date),
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
    const finalOrders = replay.final_orders || [];
    const scoredCandidates = replay.scored_candidates || [];
    const previousPositions = replay.previous_positions || [];
    const selectedInstrument = finalOrders.some((row) => row.instrument_id === state.context.instrumentId)
      ? state.context.instrumentId
      : finalOrders[0]?.instrument_id || scoredCandidates[0]?.instrument_id || previousPositions[0]?.instrument_id || '';
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
    const defaultRun = runs[0];
    if (defaultRun?.run_id) {
      state.defaultBacktestRunId = defaultRun.run_id;
      if (!state.context.runId) state.context.runId = defaultRun.run_id;
      const endDate = defaultRun.test_range?.end;
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
  byId('context-universe').addEventListener('change', () => {
    updateContext({ universe: byId('context-universe').value.trim() || 'csi300' }, { syncInputs: false, syncHash: true });
    refreshInstrumentList();
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
  refreshInstrumentList();
  return loadViewIfNeeded(state.currentView || 'backtest', { force: true });
});
