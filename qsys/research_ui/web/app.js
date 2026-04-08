const MAX_FEATURE_SELECTION = 80;
const CHART_COLORS = {
  strategy: '#0f766e',
  benchmark: '#1d4ed8',
  accent: '#c2410c',
  danger: '#b91c1c',
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

function getSvgSize(svg) {
  const box = svg.viewBox.baseVal;
  return { width: box.width || 760, height: box.height || 260 };
}

function toNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function niceTicks(minValue, maxValue, count = 5) {
  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) return [0];
  if (minValue === maxValue) {
    const delta = minValue === 0 ? 1 : Math.abs(minValue) * 0.1;
    minValue -= delta;
    maxValue += delta;
  }
  const rawStep = Math.abs(maxValue - minValue) / Math.max(count - 1, 1);
  const magnitude = 10 ** Math.floor(Math.log10(rawStep || 1));
  const normalized = rawStep / magnitude;
  const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10) * magnitude;
  const start = Math.floor(minValue / step) * step;
  const end = Math.ceil(maxValue / step) * step;
  const ticks = [];
  for (let tick = start; tick <= end + step * 0.5; tick += step) {
    ticks.push(Number(tick.toFixed(8)));
  }
  return ticks;
}

function buildLinePath(values, xAt, yAt) {
  let path = '';
  let drawing = false;
  values.forEach((value, index) => {
    const numeric = toNumber(value);
    if (numeric === null) {
      drawing = false;
      return;
    }
    const x = xAt(index);
    const y = yAt(numeric);
    path += `${drawing ? ' L' : 'M'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    drawing = true;
  });
  return path.trim();
}

function buildDateTickIndices(length, maxTicks = 6) {
  if (!length) return [];
  if (length <= maxTicks) return Array.from({ length }, (_, index) => index);
  const step = Math.max(Math.floor((length - 1) / (maxTicks - 1)), 1);
  const ticks = [];
  for (let index = 0; index < length; index += step) ticks.push(index);
  if (ticks[ticks.length - 1] !== length - 1) ticks.push(length - 1);
  return ticks;
}

function renderSeriesChart(svgId, { dates = [], series = [], valueFormatter = formatNumber, includeZero = false, title = '' } = {}) {
  const svg = document.getElementById(svgId);
  const { width, height } = getSvgSize(svg);
  const margin = { top: 28, right: 24, bottom: 34, left: 62 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const visibleSeries = series.filter((item) => item.values.some((value) => toNumber(value) !== null));
  const values = visibleSeries.flatMap((item) => item.values.map((value) => toNumber(value)).filter((value) => value !== null));

  if (!visibleSeries.length || !values.length) {
    svg.innerHTML = `<text x="24" y="36" fill="${CHART_COLORS.axis}" font-size="12">No chart data</text>`;
    return;
  }

  let minValue = Math.min(...values);
  let maxValue = Math.max(...values);
  if (includeZero) {
    minValue = Math.min(minValue, 0);
    maxValue = Math.max(maxValue, 0);
  }
  const ticks = niceTicks(minValue, maxValue, 5);
  minValue = ticks[0];
  maxValue = ticks[ticks.length - 1];
  const span = maxValue - minValue || 1;
  const xAt = (index) => margin.left + (index * plotWidth) / Math.max(dates.length - 1, 1);
  const yAt = (value) => margin.top + plotHeight - ((value - minValue) / span) * plotHeight;

  const grid = ticks.map((tick) => {
    const y = yAt(tick);
    return [
      `<line x1="${margin.left}" y1="${y.toFixed(2)}" x2="${width - margin.right}" y2="${y.toFixed(2)}" stroke="${CHART_COLORS.grid}" stroke-dasharray="4 4" />`,
      `<text x="${margin.left - 10}" y="${(y + 4).toFixed(2)}" text-anchor="end" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml(valueFormatter(tick))}</text>`,
    ].join('');
  }).join('');

  const xTicks = buildDateTickIndices(dates.length).map((index) => {
    const x = xAt(index);
    const label = dates[index] || '';
    return [
      `<line x1="${x.toFixed(2)}" y1="${height - margin.bottom}" x2="${x.toFixed(2)}" y2="${height - margin.bottom + 6}" stroke="${CHART_COLORS.axis}" />`,
      `<text x="${x.toFixed(2)}" y="${height - 10}" text-anchor="middle" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml(label.slice(5))}</text>`,
    ].join('');
  }).join('');

  const paths = visibleSeries.map((item, index) => {
    const path = buildLinePath(item.values, xAt, yAt);
    const color = item.color || (index === 0 ? CHART_COLORS.strategy : CHART_COLORS.benchmark);
    if (!path) return '';
    return `<path d="${path}" fill="none" stroke="${color}" stroke-width="${item.strokeWidth || 2.6}" stroke-linecap="round" stroke-linejoin="round" ${item.dash ? `stroke-dasharray="${item.dash}"` : ''} />`;
  }).join('');

  const legend = visibleSeries.map((item, index) => {
    const color = item.color || (index === 0 ? CHART_COLORS.strategy : CHART_COLORS.benchmark);
    const y = 16 + index * 16;
    return [
      `<line x1="${margin.left}" y1="${y}" x2="${margin.left + 18}" y2="${y}" stroke="${color}" stroke-width="3" ${item.dash ? `stroke-dasharray="${item.dash}"` : ''} />`,
      `<text x="${margin.left + 24}" y="${y + 4}" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml(item.name)}</text>`,
    ].join('');
  }).join('');

  const titleLabel = title ? `<text x="${width - margin.right}" y="16" text-anchor="end" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml(title)}</text>` : '';

  svg.innerHTML = [
    `<rect x="0" y="0" width="${width}" height="${height}" rx="16" fill="transparent"></rect>`,
    grid,
    `<line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="${CHART_COLORS.axis}" />`,
    `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${height - margin.bottom}" stroke="${CHART_COLORS.axis}" />`,
    xTicks,
    paths,
    legend,
    titleLabel,
  ].join('');
}

function renderBarChart(svgId, { dates = [], values = [], label = 'Volume', color = CHART_COLORS.volume } = {}) {
  const svg = document.getElementById(svgId);
  const { width, height } = getSvgSize(svg);
  const margin = { top: 24, right: 24, bottom: 30, left: 62 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const validValues = values.map((value) => toNumber(value)).filter((value) => value !== null);

  if (!validValues.length) {
    svg.innerHTML = `<text x="24" y="36" fill="${CHART_COLORS.axis}" font-size="12">No chart data</text>`;
    return;
  }

  const maxValue = Math.max(...validValues, 0);
  const ticks = niceTicks(0, maxValue, 4);
  const scaledMax = ticks[ticks.length - 1] || 1;
  const barWidth = plotWidth / Math.max(dates.length, 1) * 0.68;
  const xAt = (index) => margin.left + (index * plotWidth) / Math.max(dates.length - 1, 1);
  const yAt = (value) => margin.top + plotHeight - (value / scaledMax) * plotHeight;

  const grid = ticks.map((tick) => {
    const y = yAt(tick);
    return [
      `<line x1="${margin.left}" y1="${y.toFixed(2)}" x2="${width - margin.right}" y2="${y.toFixed(2)}" stroke="${CHART_COLORS.grid}" stroke-dasharray="4 4" />`,
      `<text x="${margin.left - 10}" y="${(y + 4).toFixed(2)}" text-anchor="end" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml(formatNumber(tick, 1))}</text>`,
    ].join('');
  }).join('');

  const bars = values.map((value, index) => {
    const numeric = toNumber(value);
    if (numeric === null) return '';
    const x = xAt(index) - barWidth / 2;
    const y = yAt(numeric);
    const barHeight = height - margin.bottom - y;
    return `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${Math.max(barHeight, 1).toFixed(2)}" fill="${color}" opacity="0.78" rx="2" />`;
  }).join('');

  const xTicks = buildDateTickIndices(dates.length, 5).map((index) => {
    const x = xAt(index);
    return `<text x="${x.toFixed(2)}" y="${height - 8}" text-anchor="middle" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml((dates[index] || '').slice(5))}</text>`;
  }).join('');

  svg.innerHTML = [
    grid,
    `<line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="${CHART_COLORS.axis}" />`,
    `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${height - margin.bottom}" stroke="${CHART_COLORS.axis}" />`,
    bars,
    xTicks,
    `<text x="${width - margin.right}" y="16" text-anchor="end" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml(label)}</text>`,
  ].join('');
}

function renderCandlestickChart(svgId, bars, markers = []) {
  const svg = document.getElementById(svgId);
  const { width, height } = getSvgSize(svg);
  const margin = { top: 28, right: 24, bottom: 34, left: 62 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const validBars = bars.filter((item) => toNumber(item.high) !== null && toNumber(item.low) !== null);

  if (!validBars.length) {
    svg.innerHTML = `<text x="24" y="36" fill="${CHART_COLORS.axis}" font-size="12">No chart data</text>`;
    return;
  }

  const allPrices = [];
  validBars.forEach((item) => {
    ['open', 'high', 'low', 'close'].forEach((key) => {
      const numeric = toNumber(item[key]);
      if (numeric !== null) allPrices.push(numeric);
    });
  });
  markers.forEach((item) => {
    const numeric = toNumber(item.value);
    if (numeric !== null) allPrices.push(numeric);
  });
  const ticks = niceTicks(Math.min(...allPrices), Math.max(...allPrices), 5);
  const minValue = ticks[0];
  const maxValue = ticks[ticks.length - 1];
  const span = maxValue - minValue || 1;
  const dates = bars.map((item) => item.trade_date);
  const xAt = (index) => margin.left + (index * plotWidth) / Math.max(bars.length - 1, 1);
  const yAt = (value) => margin.top + plotHeight - ((value - minValue) / span) * plotHeight;
  const candleWidth = Math.min((plotWidth / Math.max(bars.length, 1)) * 0.62, 12);

  const grid = ticks.map((tick) => {
    const y = yAt(tick);
    return [
      `<line x1="${margin.left}" y1="${y.toFixed(2)}" x2="${width - margin.right}" y2="${y.toFixed(2)}" stroke="${CHART_COLORS.grid}" stroke-dasharray="4 4" />`,
      `<text x="${margin.left - 10}" y="${(y + 4).toFixed(2)}" text-anchor="end" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml(formatNumber(tick, 2))}</text>`,
    ].join('');
  }).join('');

  const candles = bars.map((item, index) => {
    const open = toNumber(item.open);
    const high = toNumber(item.high);
    const low = toNumber(item.low);
    const close = toNumber(item.close);
    if ([open, high, low, close].some((value) => value === null)) return '';
    const x = xAt(index);
    const color = close >= open ? CHART_COLORS.candleUp : CHART_COLORS.candleDown;
    const bodyTop = yAt(Math.max(open, close));
    const bodyBottom = yAt(Math.min(open, close));
    const bodyHeight = Math.max(bodyBottom - bodyTop, 1.2);
    return [
      `<line x1="${x.toFixed(2)}" y1="${yAt(high).toFixed(2)}" x2="${x.toFixed(2)}" y2="${yAt(low).toFixed(2)}" stroke="${color}" stroke-width="1.4" />`,
      `<rect x="${(x - candleWidth / 2).toFixed(2)}" y="${bodyTop.toFixed(2)}" width="${candleWidth.toFixed(2)}" height="${bodyHeight.toFixed(2)}" fill="${color}" fill-opacity="0.9" rx="1.5" />`,
    ].join('');
  }).join('');

  const markerSvg = markers.map((item) => {
    const x = xAt(item.index);
    const numeric = toNumber(item.value);
    if (numeric === null) return '';
    const y = yAt(numeric);
    const color = item.side === 'sell' ? CHART_COLORS.accent : CHART_COLORS.strategy;
    const label = item.side === 'sell' ? 'S' : 'B';
    return [
      `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="4.5" fill="${color}" stroke="#fffdf8" stroke-width="1.4" />`,
      `<text x="${x.toFixed(2)}" y="${(y - 10).toFixed(2)}" text-anchor="middle" fill="${color}" font-size="10" font-weight="700">${label}</text>`,
    ].join('');
  }).join('');

  const xTicks = buildDateTickIndices(dates.length).map((index) => {
    const x = xAt(index);
    return [
      `<line x1="${x.toFixed(2)}" y1="${height - margin.bottom}" x2="${x.toFixed(2)}" y2="${height - margin.bottom + 6}" stroke="${CHART_COLORS.axis}" />`,
      `<text x="${x.toFixed(2)}" y="${height - 10}" text-anchor="middle" fill="${CHART_COLORS.axis}" font-size="11">${escapeHtml((dates[index] || '').slice(5))}</text>`,
    ].join('');
  }).join('');

  const legend = [
    `<rect x="${margin.left}" y="10" width="10" height="10" rx="2" fill="${CHART_COLORS.candleUp}" />`,
    `<text x="${margin.left + 16}" y="19" fill="${CHART_COLORS.axis}" font-size="11">Up Day</text>`,
    `<rect x="${margin.left + 72}" y="10" width="10" height="10" rx="2" fill="${CHART_COLORS.candleDown}" />`,
    `<text x="${margin.left + 88}" y="19" fill="${CHART_COLORS.axis}" font-size="11">Down Day</text>`,
    `<circle cx="${margin.left + 172}" cy="15" r="4.5" fill="${CHART_COLORS.strategy}" />`,
    `<text x="${margin.left + 184}" y="19" fill="${CHART_COLORS.axis}" font-size="11">Trade Markers</text>`,
  ].join('');

  svg.innerHTML = [
    grid,
    `<line x1="${margin.left}" y1="${height - margin.bottom}" x2="${width - margin.right}" y2="${height - margin.bottom}" stroke="${CHART_COLORS.axis}" />`,
    `<line x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${height - margin.bottom}" stroke="${CHART_COLORS.axis}" />`,
    candles,
    markerSvg,
    xTicks,
    legend,
  ].join('');
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

function renderCaseFeatureTable(features) {
  const rows = Object.entries(features || {})
    .map(([feature_name, value]) => ({ feature_name, value }))
    .sort((left, right) => left.feature_name.localeCompare(right.feature_name));
  renderTable('case-feature-table', rows, [{ key: 'feature_name', label: 'Feature' }, { key: 'value', label: 'Value' }]);
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
      .filter((item) => barIndex.has(item.date) && typeof item.deal_price === 'number')
      .map((item) => ({ index: barIndex.get(item.date), value: item.deal_price, side: item.side, date: item.date }));

    renderCandlestickChart('case-bars-chart', chartBars, tradeMarkers);
    const dates = chartBars.map((item) => item.trade_date);
    const benchmarkValues = alignSeriesByDate(dates, payload.benchmark_bars || [], 'close');
    const benchmark2Values = alignSeriesByDate(dates, payload.secondary_benchmark_bars || [], 'close');
    renderSeriesChart('case-relative-chart', {
      dates,
      title: 'Rebased to 100',
      series: [
        { name: payload.instrument_id, values: rebaseSeries(chartBars.map((item) => item.close)), color: CHART_COLORS.strategy },
        { name: payload.benchmark_label || 'CSI300', values: rebaseSeries(benchmarkValues), color: CHART_COLORS.benchmark, dash: '6 4' },
        { name: payload.secondary_benchmark_label || 'SSE', values: rebaseSeries(benchmark2Values), color: '#7c3aed', dash: '2 5' },
      ],
      valueFormatter: (value) => formatNumber(value, 1),
    });
    renderBarChart('case-volume-chart', {
      dates,
      values: chartBars.map((item) => item.volume),
      label: 'Volume',
      color: CHART_COLORS.volume,
    });

    document.getElementById('case-meta').textContent = `${payload.instrument_id} / ${payload.trade_date} / ${payload.price_mode} / ${payload.benchmark_label || 'CSI300'} / ${payload.secondary_benchmark_label || 'SSE'}`;
    document.getElementById('case-signal').textContent = JSON.stringify({ signal_snapshot: payload.signal_snapshot, trade_markers: backtestTrades, links: payload.links }, null, 2);
    renderCaseFeatureTable(payload.feature_snapshot.features || {});

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
    featureNames.forEach((name) => snapshotParams.append('feature_names', name));
    const snapshotPayload = await getJson(`/api/feature-snapshot?${snapshotParams.toString()}`, { useCache: false });
    const snapshot = unwrapData(snapshotPayload);
    const rows = Object.entries(snapshot.features || {})
      .map(([feature_name, value]) => ({ feature_name, value }))
      .sort((left, right) => left.feature_name.localeCompare(right.feature_name));
    renderTable('feature-snapshot-table', rows, [{ key: 'feature_name', label: 'Feature' }, { key: 'value', label: 'Value' }]);
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
        { name: 'SSE', values: dailyItems.map((item) => item.benchmark2_equity), color: '#7c3aed', dash: '2 5' },
      ],
      valueFormatter: (value) => formatNumber(value, 0),
    });
    renderSeriesChart('backtest-diagnostics-chart', {
      dates,
      includeZero: true,
      title: 'Drawdown, IC and RankIC',
      series: [
        { name: 'Drawdown', values: dailyItems.map((item) => item.drawdown), color: CHART_COLORS.danger },
        { name: 'IC', values: dailyItems.map((item) => item.ic), color: CHART_COLORS.accent },
        { name: 'RankIC', values: dailyItems.map((item) => item.rank_ic), color: '#7c3aed', dash: '5 4' },
      ],
      valueFormatter: (value) => formatPercent(value),
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
  document.getElementById('feature-registry-search').addEventListener('input', () => renderFeatureRegistry());
  document.getElementById('feature-source-filter').addEventListener('change', () => renderFeatureRegistry());
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
