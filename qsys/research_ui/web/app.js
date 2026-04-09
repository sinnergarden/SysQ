const MAX_FEATURE_SELECTION = 80;
const CHART_COLORS = {
  strategy: '#0f766e',
  benchmark: '#1d4ed8',
  accent: '#c2410c',
  danger: '#b91c1c',
  neutral: '#334155',
  grid: 'rgba(31, 36, 48, 0.12)',
  axis: 'rgba(31, 36, 48, 0.46)',
  candleUp: '#0f766e',
  candleDown: '#b91c1c',
  volume: '#9f8a63',
};

const state = {
  currentView: 'case',
  loadedViews: new Set(),
  cache: new Map(),
  latestBacktestRunId: null,
  featureRegistry: [],
  caseFeatureSnapshot: {},
  featureSnapshot: {},
  selectedFeatureNames: new Set(),
};

const viewMeta = {
  case: ['Case Workspace', '单票 case、signal、feature、订单、仓位联动查看。'],
  feature: ['Feature Health', 'coverage / NaN / inf / snapshot 检查。'],
  backtest: ['Backtest Explorer', '收益、回撤、benchmark、IC / RankIC 与日级下钻。'],
  replay: ['Decision Replay', '回答为什么买 A、没买 B、卖了 C。'],
};

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

function setStatus(text) {
  document.getElementById('status-pill').textContent = text;
}

function setView(name) {
  state.currentView = name;
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === name));
  document.querySelectorAll('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${name}`));
  document.getElementById('view-title').textContent = viewMeta[name][0];
  document.getElementById('view-subtitle').textContent = viewMeta[name][1];
}

async function getJson(url, { useCache = true } = {}) {
  if (useCache && state.cache.has(url)) return state.cache.get(url);
  setStatus('Loading');
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    setStatus('API Error');
    throw new Error(payload.detail || 'request failed');
  }
  setStatus('API Ready');
  if (useCache) state.cache.set(url, payload);
  return payload;
}

function unwrapData(payload) {
  return payload && typeof payload === 'object' && 'data' in payload ? payload.data : payload;
}

function unwrapItems(payload) {
  return payload && typeof payload === 'object' && Array.isArray(payload.items) ? payload.items : [];
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  const numeric = Number(value);
  if (Math.abs(numeric) >= 100000000) return `${(numeric / 100000000).toFixed(1)}e`;
  if (Math.abs(numeric) >= 10000) return `${(numeric / 10000).toFixed(1)}w`;
  if (Number.isInteger(numeric)) return String(numeric);
  return numeric.toFixed(digits);
}

function formatPercent(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : formatNumber(value, Math.abs(value) < 1 ? 4 : 2);
  if (typeof value === 'object') return `<pre class="code-inline">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
  return escapeHtml(String(value));
}

function renderTable(containerId, rows, columns) {
  const root = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    root.innerHTML = '<div class="empty">No data</div>';
    return;
  }
  const header = columns.map((col) => `<th>${escapeHtml(col.label)}</th>`).join('');
  const body = rows.map((row) => {
    const tds = columns.map((col) => {
      if (col.render) return `<td>${col.render(row)}</td>`;
      return `<td>${formatValue(row[col.key])}</td>`;
    }).join('');
    return `<tr>${tds}</tr>`;
  }).join('');
  root.innerHTML = `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function toDateLabel(value) {
  return String(value || '').slice(0, 10);
}

function hasPlotly() {
  return typeof window !== 'undefined' && typeof window.Plotly !== 'undefined';
}

function renderChartError(containerId, message) {
  const root = document.getElementById(containerId);
  if (!root) return;
  root.innerHTML = `<div class="chart-empty">${escapeHtml(message)}</div>`;
}

function plotlyBaseLayout({ title = '', height = 320, yAxisTitle = '', hoverMode = 'x unified', showRangeSlider = false } = {}) {
  return {
    title: title ? { text: title, font: { size: 13, color: CHART_COLORS.axis } } : undefined,
    height,
    margin: { l: 56, r: 22, t: title ? 42 : 20, b: 42 },
    paper_bgcolor: 'rgba(255, 253, 248, 0)',
    plot_bgcolor: 'rgba(255, 253, 248, 0)',
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
    font: { family: 'IBM Plex Sans, Noto Sans SC, sans-serif', color: CHART_COLORS.axis },
    hoverlabel: { bgcolor: '#fffdf8', bordercolor: CHART_COLORS.grid, font: { color: CHART_COLORS.axis } },
  };
}

function renderPlotlyChart(containerId, traces, layout) {
  if (!hasPlotly()) {
    renderChartError(containerId, 'Plotly failed to load. Check CDN/network access and refresh.');
    return;
  }
  const root = document.getElementById(containerId);
  if (!root) return;
  if (!traces || traces.length === 0) {
    renderChartError(containerId, 'No chart data');
    return;
  }
  window.Plotly.react(root, traces, layout, {
    responsive: true,
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    scrollZoom: true,
  });
}

function dashToPlotlyDash(dash) {
  if (!dash) return 'solid';
  return dash.includes('2') ? 'dot' : 'dash';
}

function renderSeriesChart(containerId, { dates = [], series = [], title = '', includeZero = false, yAxisTitle = '' } = {}) {
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
        width: item.strokeWidth || 2.5,
        dash: dashToPlotlyDash(item.dash),
      },
      hovertemplate: `${item.name}<br>%{x}<br>%{y:.4f}<extra></extra>`,
    }));

  const layout = plotlyBaseLayout({ title, height: 280, yAxisTitle });
  if (includeZero) layout.yaxis.zeroline = true;
  renderPlotlyChart(containerId, traces, layout);
}

function renderVolumeChart(containerId, bars) {
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
  renderPlotlyChart(containerId, [
    {
      type: 'bar',
      name: 'Volume',
      x: points.map((item) => item.tradeDate),
      y: points.map((item) => item.value),
      marker: { color: points.map((item) => item.color), opacity: 0.75 },
      hovertemplate: 'Volume<br>%{x}<br>%{y:.0f}<extra></extra>',
    },
  ], plotlyBaseLayout({ title: 'Volume', height: 180, yAxisTitle: 'Volume' }));
}

function normalizeTradeSide(side) {
  const value = String(side || '').trim().toLowerCase();
  if (value.startsWith('s')) return 'sell';
  if (value.startsWith('b')) return 'buy';
  return value || 'buy';
}

function renderCandlestickChart(containerId, bars, markers = [], instrumentId = '') {
  const validBars = (bars || []).filter((item) => ['open', 'high', 'low', 'close'].every((key) => toNumber(item[key]) !== null));
  if (!validBars.length) {
    renderChartError(containerId, 'No OHLC data');
    return;
  }

  const buyMarkers = [];
  const sellMarkers = [];
  markers.forEach((item) => {
    const marker = {
      tradeDate: toDateLabel(item.trade_date || item.date),
      value: toNumber(item.value || item.deal_price),
      side: normalizeTradeSide(item.side),
      quantity: toNumber(item.quantity || item.filled_amount),
    };
    if (!marker.tradeDate || marker.value === null) return;
    if (marker.side === 'sell') {
      sellMarkers.push(marker);
    } else {
      buyMarkers.push(marker);
    }
  });

  const traces = [
    {
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
    },
  ];

  if (buyMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Buy',
      x: buyMarkers.map((item) => item.tradeDate),
      y: buyMarkers.map((item) => item.value),
      marker: { color: CHART_COLORS.strategy, size: 11, symbol: 'triangle-up' },
      customdata: buyMarkers.map((item) => [item.quantity]),
      hovertemplate: 'Buy<br>%{x}<br>Price %{y:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }
  if (sellMarkers.length) {
    traces.push({
      type: 'scatter',
      mode: 'markers',
      name: 'Sell',
      x: sellMarkers.map((item) => item.tradeDate),
      y: sellMarkers.map((item) => item.value),
      marker: { color: CHART_COLORS.accent, size: 11, symbol: 'triangle-down' },
      customdata: sellMarkers.map((item) => [item.quantity]),
      hovertemplate: 'Sell<br>%{x}<br>Price %{y:.2f}<br>Qty %{customdata[0]:.0f}<extra></extra>',
    });
  }

  renderPlotlyChart(containerId, traces, plotlyBaseLayout({
    title: `${instrumentId || 'Instrument'} OHLC`,
    height: 360,
    yAxisTitle: 'Price',
    hoverMode: 'x',
    showRangeSlider: false,
  }));
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

function filterFeatureRows(features, searchValue) {
  const needle = String(searchValue || '').trim().toLowerCase();
  return Object.entries(features || {})
    .map(([feature_name, value]) => ({ feature_name, value }))
    .filter((row) => !needle || row.feature_name.toLowerCase().includes(needle))
    .sort((left, right) => left.feature_name.localeCompare(right.feature_name));
}

function renderCaseFeatureTable(features) {
  const rows = filterFeatureRows(features, document.getElementById('case-feature-search').value);
  renderTable('case-feature-table', rows, [{ key: 'feature_name', label: 'Feature' }, { key: 'value', label: 'Value' }]);
}

function renderFeatureSnapshotTable(features) {
  const rows = filterFeatureRows(features, document.getElementById('feature-snapshot-search').value);
  renderTable('feature-snapshot-table', rows, [{ key: 'feature_name', label: 'Feature' }, { key: 'value', label: 'Value' }]);
}

function syncFeatureSelectionInput() {
  document.getElementById('feature-names').value = Array.from(state.selectedFeatureNames).join(', ');
}

function renderFeatureSelectionChips() {
  const root = document.getElementById('feature-selection-chips');
  const selected = Array.from(state.selectedFeatureNames);
  if (!selected.length) {
    root.innerHTML = '<div class="empty">No selected features yet. Filter the registry, then choose Use Visible.</div>';
    return;
  }
  root.innerHTML = selected.map((featureName) => [
    `<span class="chip">${escapeHtml(featureName)}`,
    `<button type="button" onclick="toggleFeatureSelection('${quoteJsString(featureName)}')">x</button>`,
    '</span>',
  ].join('')).join('');
}

function getFilteredFeatureRegistry() {
  const search = document.getElementById('feature-registry-search').value.trim().toLowerCase();
  const sourceFilter = document.getElementById('feature-source-filter').value;
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
  const summary = document.getElementById('feature-selection-summary');
  summary.textContent = `Selected ${state.selectedFeatureNames.size}/${MAX_FEATURE_SELECTION} snapshot-ready features. Registry matches: ${filteredCount}.`;
}

function updateFeatureSourceFilterOptions() {
  const select = document.getElementById('feature-source-filter');
  const currentValue = select.value || 'all';
  const options = ['all', ...Array.from(new Set(state.featureRegistry.map((item) => item.source_layer))).sort()];
  select.innerHTML = options.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
  select.value = options.includes(currentValue) ? currentValue : 'all';
}

function renderFeatureRegistry() {
  const filtered = getFilteredFeatureRegistry();
  document.getElementById('feature-registry-count').textContent = `${filtered.length} / ${state.featureRegistry.length}`;
  const rows = filtered.slice().sort((left, right) => {
    const leftSelected = state.selectedFeatureNames.has(left.feature_name) ? -1 : 0;
    const rightSelected = state.selectedFeatureNames.has(right.feature_name) ? -1 : 0;
    if (leftSelected !== rightSelected) return leftSelected - rightSelected;
    return left.feature_name.localeCompare(right.feature_name);
  });
  renderTable('feature-registry-table', rows, [
    {
      label: 'Select',
      render: (row) => {
        const active = state.selectedFeatureNames.has(row.feature_name);
        return `<span class="registry-action ${active ? 'active' : ''}" onclick="toggleFeatureSelection('${quoteJsString(row.feature_name)}')">${active ? 'Added' : 'Add'}</span>`;
      },
    },
    { key: 'feature_name', label: 'Feature' },
    { key: 'group_name', label: 'Group' },
    {
      key: 'source_layer',
      label: 'Source',
      render: (row) => `<span class="pill">${escapeHtml(row.source_layer || '-')}</span>`,
    },
    {
      key: 'supports_snapshot',
      label: 'Snapshot',
      render: (row) => `<span class="pill">${row.supports_snapshot ? 'yes' : 'no'}</span>`,
    },
    {
      key: 'tags',
      label: 'Tags',
      render: (row) => escapeHtml((row.tags || []).slice(0, 4).join(', ') || '-'),
    },
    { key: 'description', label: 'Description' },
  ]);
  updateFeatureSelectionSummary();
}

function parseFeatureSelectionFromInput() {
  const value = document.getElementById('feature-names').value;
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
  if (!state.selectedFeatureNames.size) {
    ensureFeatureSelectionSeed();
    renderFeatureRegistry();
  }
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

async function loadCase() {
  try {
    const executionDate = document.getElementById('case-date').value.trim();
    const instrumentId = document.getElementById('case-instrument').value.trim();
    const priceMode = document.getElementById('case-price-mode').value;
    const backtestRunId = document.getElementById('case-backtest-run').value.trim();
    const casePayload = await getJson(`/api/cases/${executionDate}:${instrumentId}:${priceMode}`, { useCache: false });
    const payload = unwrapData(casePayload);
    let backtestTrades = [];
    if (backtestRunId) {
      const tradePayload = await getJson(`/api/backtest-runs/${backtestRunId}/orders?instrument_id=${encodeURIComponent(instrumentId)}&limit=5000`);
      backtestTrades = unwrapItems(tradePayload);
    }

    const priceKeys = priceMode === 'fq'
      ? { open: 'adj_open', high: 'adj_high', low: 'adj_low', close: 'adj_close' }
      : { open: 'open', high: 'high', low: 'low', close: 'close' };
    const chartBars = (payload.bars || []).map((item) => ({
      trade_date: item.trade_date,
      open: item[priceKeys.open],
      high: item[priceKeys.high],
      low: item[priceKeys.low],
      close: item[priceKeys.close],
      volume: item.volume,
    }));
    const barIndex = new Map(chartBars.map((item, index) => [item.trade_date, index]));
    const tradeMarkers = backtestTrades
      .filter((item) => barIndex.has(item.date) && toNumber(item.deal_price) !== null)
      .map((item) => ({
        trade_date: item.date,
        value: toNumber(item.deal_price),
        side: item.side,
        quantity: item.filled_amount,
      }));

    renderCandlestickChart('case-bars-chart', chartBars, tradeMarkers, payload.instrument_id);
    const dates = chartBars.map((item) => item.trade_date);
    const benchmarkValues = alignSeriesByDate(dates, payload.benchmark_bars || [], 'close');
    const benchmark2Values = alignSeriesByDate(dates, payload.secondary_benchmark_bars || [], 'close');
    renderSeriesChart('case-relative-chart', {
      dates,
      title: 'Rebased to 100',
      series: [
        { name: payload.instrument_id, values: rebaseSeries(chartBars.map((item) => item.close)), color: CHART_COLORS.strategy },
        { name: payload.benchmark_label || 'CSI300', values: rebaseSeries(benchmarkValues), color: CHART_COLORS.benchmark, dash: '6 4' },
        { name: payload.secondary_benchmark_label || 'SSE', values: rebaseSeries(benchmark2Values), color: CHART_COLORS.neutral, dash: '2 5' },
      ],
      yAxisTitle: 'Rebased',
    });
    renderVolumeChart('case-volume-chart', chartBars);

    document.getElementById('case-meta').textContent = `${payload.instrument_id} / ${payload.trade_date} / ${payload.price_mode} / ${payload.benchmark_label || 'CSI300'} / ${payload.secondary_benchmark_label || 'SSE'}`;
    document.getElementById('case-signal').textContent = JSON.stringify({ signal_snapshot: payload.signal_snapshot, trade_markers: backtestTrades, links: payload.links }, null, 2);
    state.caseFeatureSnapshot = payload.feature_snapshot.features || {};
    document.getElementById('case-feature-count').textContent = `${Object.keys(state.caseFeatureSnapshot).length} features`;
    renderCaseFeatureTable(state.caseFeatureSnapshot);

    const orderRows = [...backtestTrades, ...(payload.positions || []), ...(payload.orders || [])];
    renderTable('case-orders-table', orderRows, [
      { key: 'date', label: 'Date' },
      { key: 'instrument_id', label: 'Instrument' },
      { key: 'symbol', label: 'Symbol' },
      { key: 'quantity', label: 'Qty' },
      { key: 'filled_amount', label: 'Filled Qty' },
      { key: 'side', label: 'Side' },
      { key: 'deal_price', label: 'Deal Price' },
      { key: 'price', label: 'Price' },
      { key: 'status', label: 'Status' },
    ]);
  } catch (error) {
    document.getElementById('case-signal').textContent = error.message;
    renderChartError('case-bars-chart', error.message);
    renderChartError('case-relative-chart', error.message);
    renderChartError('case-volume-chart', error.message);
  }
}

async function loadFeatureRegistry() {
  const registryPayload = await getJson('/api/feature-registry');
  const items = unwrapItems(registryPayload);
  state.featureRegistry = items;
  updateFeatureSourceFilterOptions();
  ensureFeatureSelectionSeed();
  renderFeatureSelectionChips();
  renderFeatureRegistry();
}

async function loadFeatureHealth() {
  try {
    const tradeDate = document.getElementById('feature-date').value.trim();
    const instrumentId = document.getElementById('feature-instrument').value.trim();
    const featureNames = resolveFeatureNames();
    const healthParams = new URLSearchParams({ trade_date: tradeDate, universe: 'csi300' });
    featureNames.forEach((name) => healthParams.append('feature_names', name));
    const healthPayload = await getJson(`/api/feature-health?${healthParams.toString()}`, { useCache: false });
    const health = unwrapData(healthPayload);
    renderTable('feature-health-table', health.features || [], [
      { key: 'feature_name', label: 'Feature' },
      { key: 'coverage_ratio', label: 'Coverage', render: (row) => formatPercent(row.coverage_ratio) },
      { key: 'nan_ratio', label: 'NaN', render: (row) => formatPercent(row.nan_ratio) },
      { key: 'inf_ratio', label: 'Inf', render: (row) => formatPercent(row.inf_ratio) },
      { key: 'status', label: 'Status' },
    ]);
    document.getElementById('feature-health-summary').textContent = [
      `Feature count ${health.feature_count}`,
      `instrument coverage ${health.instrument_count}`,
      `overall missing ${formatPercent(health.overall_missing_ratio)}`,
      ...(health.warnings || []).slice(0, 2),
      ...(health.blockers || []).slice(0, 2),
    ].join(' | ');

    const snapshotParams = new URLSearchParams({ trade_date: tradeDate, instrument_id: instrumentId });
    const snapshotPayload = await getJson(`/api/feature-snapshot?${snapshotParams.toString()}`, { useCache: false });
    const snapshot = unwrapData(snapshotPayload);
    state.featureSnapshot = snapshot.features || {};
    document.getElementById('feature-snapshot-count').textContent = `${Object.keys(state.featureSnapshot).length} features`;
    renderFeatureSnapshotTable(state.featureSnapshot);
  } catch (error) {
    document.getElementById('feature-health-summary').textContent = error.message;
    document.getElementById('feature-health-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    document.getElementById('feature-snapshot-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function loadBacktest() {
  try {
    const runId = document.getElementById('backtest-run-id').value.trim();
    const summaryPayload = await getJson(`/api/backtest-runs/${runId}/summary`);
    const dailyPayload = await getJson(`/api/backtest-runs/${runId}/daily`);
    const summary = unwrapData(summaryPayload);
    const dailyItems = unwrapItems(dailyPayload);
    document.getElementById('metric-total-return').textContent = summary.metrics.total_return || '-';
    document.getElementById('metric-sharpe').textContent = summary.metrics.sharpe || '-';
    document.getElementById('metric-max-drawdown').textContent = summary.metrics.max_drawdown || '-';

    const dates = dailyItems.map((item) => item.trade_date);
    renderSeriesChart('backtest-equity-chart', {
      dates,
      title: 'Strategy / Zero-cost / CSI300 / SSE',
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
      title: 'Drawdown, IC and RankIC',
      series: [
        { name: 'Drawdown', values: dailyItems.map((item) => item.drawdown), color: CHART_COLORS.danger },
        { name: 'IC', values: dailyItems.map((item) => item.ic), color: CHART_COLORS.accent },
        { name: 'RankIC', values: dailyItems.map((item) => item.rank_ic), color: CHART_COLORS.neutral, dash: '5 4' },
      ],
      yAxisTitle: 'Ratio / IC',
    });
    renderTable('backtest-daily-table', dailyItems, [
      { key: 'trade_date', label: 'Trade Date' },
      { key: 'equity', label: 'Equity', render: (row) => formatNumber(row.equity, 0) },
      { key: 'zero_cost_equity', label: 'Zero-cost', render: (row) => formatNumber(row.zero_cost_equity, 0) },
      { key: 'benchmark_equity', label: 'CSI300', render: (row) => formatNumber(row.benchmark_equity, 0) },
      { key: 'benchmark2_equity', label: 'SSE', render: (row) => formatNumber(row.benchmark2_equity, 0) },
      { key: 'drawdown', label: 'Drawdown', render: (row) => formatPercent(row.drawdown) },
      { key: 'turnover', label: 'Turnover', render: (row) => formatNumber(row.turnover, 0) },
      { key: 'ic', label: 'IC', render: (row) => formatNumber(row.ic, 4) },
      { key: 'rank_ic', label: 'RankIC', render: (row) => formatNumber(row.rank_ic, 4) },
      { key: 'trade_count', label: 'Trades' },
      { label: 'Orders', render: (row) => `<span class="action-link" onclick="loadBacktestOrders('${quoteJsString(row.trade_date)}')">Open</span>` },
      { label: 'Replay', render: (row) => `<span class="action-link" onclick="jumpToReplay('${quoteJsString(row.trade_date)}')">Open</span>` },
    ]);
    const seedDate = dailyItems[0]?.trade_date;
    if (seedDate) await loadBacktestOrders(seedDate);
  } catch (error) {
    document.getElementById('backtest-daily-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    renderChartError('backtest-equity-chart', error.message);
    renderChartError('backtest-diagnostics-chart', error.message);
  }
}

async function loadReplay() {
  try {
    const executionDate = document.getElementById('replay-date').value.trim();
    const accountName = document.getElementById('replay-account').value.trim();
    const replayPayload = await getJson(`/api/decision-replay?execution_date=${executionDate}&account_name=${accountName}`, { useCache: false });
    const replay = unwrapData(replayPayload);
    renderTable('replay-positions-table', replay.previous_positions, [
      { key: 'instrument_id', label: 'Instrument' },
      { key: 'quantity', label: 'Qty' },
      { key: 'price', label: 'Price' },
      { key: 'cost_basis', label: 'Cost Basis' },
    ]);
    renderTable('replay-orders-table', replay.final_orders, [
      { key: 'instrument_id', label: 'Instrument' },
      { key: 'side', label: 'Side' },
      { key: 'quantity', label: 'Qty' },
      { key: 'price', label: 'Price' },
      { key: 'status', label: 'Status' },
      { label: 'Case', render: (row) => `<span class="action-link" onclick="jumpToCase('${quoteJsString(row.instrument_id)}')">Open</span>` },
    ]);
    renderTable('replay-candidates-table', replay.scored_candidates, [
      { key: 'instrument_id', label: 'Instrument' },
      { key: 'raw_score', label: 'Raw Score' },
      { key: 'adjusted_score', label: 'Adjusted Score' },
      { key: 'rank', label: 'Rank' },
      { key: 'selected', label: 'Selected' },
      { label: 'Exclusion Reasons', render: (row) => escapeHtml((row.exclusion_reasons || []).join(', ') || '-') },
    ]);
  } catch (error) {
    document.getElementById('replay-candidates-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

window.jumpToReplay = function jumpToReplay(tradeDate) {
  document.getElementById('replay-date').value = tradeDate || document.getElementById('replay-date').value;
  setView('replay');
  loadReplay();
};

window.jumpToCase = function jumpToCase(instrumentId, tradeDate) {
  if (instrumentId) document.getElementById('case-instrument').value = instrumentId;
  if (tradeDate) document.getElementById('case-date').value = tradeDate;
  document.getElementById('case-backtest-run').value = document.getElementById('backtest-run-id').value.trim();
  setView('case');
  loadCase();
};

window.loadBacktestOrders = async function loadBacktestOrders(tradeDate) {
  try {
    const runId = document.getElementById('backtest-run-id').value.trim();
    const payload = await getJson(`/api/backtest-runs/${runId}/orders?trade_date=${tradeDate}`);
    const items = unwrapItems(payload);
    renderTable('backtest-orders-table', items, [
      { key: 'date', label: 'Trade Date' },
      { key: 'symbol', label: 'Instrument' },
      { key: 'side', label: 'Side' },
      { key: 'filled_amount', label: 'Filled Qty' },
      { key: 'deal_price', label: 'Deal Price' },
      { key: 'fee', label: 'Fee' },
      { key: 'status', label: 'Status' },
      { key: 'reason', label: 'Reason' },
      { label: 'Case', render: (row) => `<span class="action-link" onclick="jumpToCase('${quoteJsString(row.symbol)}', '${quoteJsString(row.date)}')">Open</span>` },
    ]);
  } catch (error) {
    document.getElementById('backtest-orders-table').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
};

async function bootstrapDefaults() {
  try {
    const runsPayload = await getJson('/api/backtest-runs?limit=1', { useCache: false });
    const latestRun = unwrapItems(runsPayload)[0];
    if (latestRun && latestRun.run_id) {
      state.latestBacktestRunId = latestRun.run_id;
      if (!document.getElementById('backtest-run-id').value.trim()) document.getElementById('backtest-run-id').value = latestRun.run_id;
      if (!document.getElementById('case-backtest-run').value.trim()) document.getElementById('case-backtest-run').value = latestRun.run_id;
      const endDate = latestRun.test_range?.end;
      if (endDate) {
        document.getElementById('case-date').value = endDate;
        document.getElementById('feature-date').value = endDate;
        document.getElementById('replay-date').value = endDate;
      }
    }
  } catch (error) {
    console.error('bootstrap backtest runs failed', error);
  }

  try {
    await loadFeatureRegistry();
  } catch (error) {
    console.error('bootstrap feature registry failed', error);
  }
}

async function loadViewIfNeeded(name, { force = false } = {}) {
  if (!force && state.loadedViews.has(name)) return;
  if (name === 'case') await loadCase();
  if (name === 'feature') await loadFeatureHealth();
  if (name === 'backtest') await loadBacktest();
  if (name === 'replay') await loadReplay();
  state.loadedViews.add(name);
}

function bindFeatureRegistryEvents() {
  document.getElementById('case-feature-search').addEventListener('input', () => renderCaseFeatureTable(state.caseFeatureSnapshot));
  document.getElementById('feature-registry-search').addEventListener('input', () => renderFeatureRegistry());
  document.getElementById('feature-source-filter').addEventListener('change', () => renderFeatureRegistry());
  document.getElementById('feature-snapshot-search').addEventListener('input', () => renderFeatureSnapshotTable(state.featureSnapshot));
  document.getElementById('feature-names').addEventListener('change', () => parseFeatureSelectionFromInput());
  document.getElementById('select-visible-features').addEventListener('click', () => window.selectVisibleFeatures());
  document.getElementById('clear-feature-selection').addEventListener('click', () => window.clearFeatureSelection());
}

function bindEvents() {
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.addEventListener('click', async () => {
    setView(btn.dataset.view);
    await loadViewIfNeeded(btn.dataset.view);
  }));
  document.getElementById('load-case').addEventListener('click', () => loadViewIfNeeded('case', { force: true }));
  document.getElementById('load-feature').addEventListener('click', () => loadViewIfNeeded('feature', { force: true }));
  document.getElementById('load-backtest').addEventListener('click', () => loadViewIfNeeded('backtest', { force: true }));
  document.getElementById('load-replay').addEventListener('click', () => loadViewIfNeeded('replay', { force: true }));
  bindFeatureRegistryEvents();
}

bindEvents();
bootstrapDefaults().then(() => loadViewIfNeeded('case'));
