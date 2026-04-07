const state = {
  currentView: 'case',
};

const viewMeta = {
  case: ['Case Workspace', '单票 case、signal、feature、订单、仓位联动查看。'],
  feature: ['Feature Health', 'coverage / NaN / inf / snapshot 检查。'],
  backtest: ['Backtest Explorer', '收益、回撤、turnover、IC / RankIC 与日级下钻。'],
  replay: ['Decision Replay', '回答为什么买 A、没买 B、卖了 C。'],
};

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

async function getJson(url) {
  setStatus('Loading');
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    setStatus('API Error');
    throw new Error(payload.detail || 'request failed');
  }
  setStatus('API Ready');
  return payload;
}

function renderTable(containerId, rows, columns) {
  const root = document.getElementById(containerId);
  if (!rows || rows.length === 0) {
    root.innerHTML = '<div class="empty">No data</div>';
    return;
  }
  const header = columns.map((col) => `<th>${col.label}</th>`).join('');
  const body = rows.map((row) => {
    const tds = columns.map((col) => `<td>${col.render ? col.render(row) : formatValue(row[col.key])}</td>`).join('');
    return `<tr>${tds}</tr>`;
  }).join('');
  root.innerHTML = `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (typeof value === 'object') return `<pre class="code-inline">${JSON.stringify(value, null, 2)}</pre>`;
  return String(value);
}

function polylinePath(values, width, height, padding = 20) {
  const valid = values.filter((v) => typeof v === 'number' && !Number.isNaN(v));
  if (!valid.length) return '';
  const min = Math.min(...valid);
  const max = Math.max(...valid);
  const span = max - min || 1;
  return values.map((value, index) => {
    const x = padding + (index * (width - padding * 2)) / Math.max(values.length - 1, 1);
    const y = height - padding - (((value ?? min) - min) / span) * (height - padding * 2);
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(' ');
}

function renderLineChart(svgId, primary, secondary = null, markers = []) {
  const svg = document.getElementById(svgId);
  const width = 760;
  const height = svg.viewBox.baseVal.height || 260;
  const padding = 20;
  const bg = `<rect x="0" y="0" width="${width}" height="${height}" rx="16" fill="transparent"></rect>`;
  const grid = [0.2, 0.4, 0.6, 0.8].map((ratio) => {
    const y = padding + ratio * (height - padding * 2);
    return `<line x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}" stroke="rgba(31,36,48,0.08)" stroke-dasharray="4 4" />`;
  }).join('');
  const pathA = polylinePath(primary, width, height, padding);
  const pathB = secondary ? polylinePath(secondary, width, height, padding) : '';

  const allValues = [...primary, ...(secondary || []), ...markers.map((item) => item.value)].filter((v) => typeof v === 'number' && !Number.isNaN(v));
  const min = allValues.length ? Math.min(...allValues) : 0;
  const max = allValues.length ? Math.max(...allValues) : 1;
  const span = max - min || 1;
  const markerSvg = markers.map((item) => {
    const x = padding + (item.index * (width - padding * 2)) / Math.max(primary.length - 1, 1);
    const y = height - padding - (((item.value ?? min) - min) / span) * (height - padding * 2);
    const color = item.side === 'sell' ? '#c2410c' : '#0f766e';
    const label = item.side === 'sell' ? 'S' : 'B';
    return `<g><circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="5" fill="${color}" /><text x="${x.toFixed(2)}" y="${(y - 10).toFixed(2)}" text-anchor="middle" font-size="11" font-weight="700" fill="${color}">${label}</text></g>`;
  }).join('');

  svg.innerHTML = `${bg}${grid}${pathA ? `<path d="${pathA}" fill="none" stroke="#0f766e" stroke-width="3" />` : ''}${pathB ? `<path d="${pathB}" fill="none" stroke="#c2410c" stroke-width="2" />` : ''}${markerSvg}`;
}

async function loadCase() {
  try {
    const executionDate = document.getElementById('case-date').value.trim();
    const instrumentId = document.getElementById('case-instrument').value.trim();
    const priceMode = document.getElementById('case-price-mode').value;
    const backtestRunId = document.getElementById('case-backtest-run').value.trim();
    const payload = await getJson(`/api/cases/${executionDate}:${instrumentId}:${priceMode}`);
    let tradeMarkers = [];
    let backtestTrades = [];
    if (backtestRunId) {
      const tradePayload = await getJson(`/api/backtest-runs/${backtestRunId}/orders?instrument_id=${encodeURIComponent(instrumentId)}&limit=5000`);
      backtestTrades = tradePayload.items || [];
    }
    document.getElementById('case-meta').textContent = `${payload.instrument_id} / ${payload.trade_date} / ${payload.price_mode}`;
    const priceKey = priceMode === 'fq' ? 'adj_close' : 'close';
    const barIndex = new Map(payload.bars.map((item, index) => [item.trade_date, index]));
    tradeMarkers = backtestTrades
      .filter((item) => barIndex.has(item.date) && typeof item.deal_price === 'number')
      .map((item) => ({ index: barIndex.get(item.date), value: item.deal_price, side: item.side, date: item.date }));
    renderLineChart('case-bars-chart', payload.bars.map((item) => item[priceKey]), null, tradeMarkers);
    renderLineChart('case-volume-chart', payload.bars.map((item) => item.volume));
    document.getElementById('case-signal').textContent = JSON.stringify({ signal_snapshot: payload.signal_snapshot, trade_markers: backtestTrades, links: payload.links }, null, 2);
    const featureRows = Object.entries(payload.feature_snapshot.features || {}).map(([feature_name, value]) => ({ feature_name, value }));
    renderTable('case-feature-table', featureRows, [{ key: 'feature_name', label: 'Feature' }, { key: 'value', label: 'Value' }]);
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

async function loadFeatureHealth() {
  try {
    const tradeDate = document.getElementById('feature-date').value.trim();
    const instrumentId = document.getElementById('feature-instrument').value.trim();
    const featureNames = document.getElementById('feature-names').value.split(',').map((item) => item.trim()).filter(Boolean);
    const healthParams = new URLSearchParams({ trade_date: tradeDate, universe: 'csi300' });
    featureNames.forEach((name) => healthParams.append('feature_names', name));
    const health = await getJson(`/api/feature-health?${healthParams.toString()}`);
    renderTable('feature-health-table', health.features, [
      { key: 'feature_name', label: 'Feature' },
      { key: 'coverage_ratio', label: 'Coverage' },
      { key: 'nan_ratio', label: 'NaN' },
      { key: 'inf_ratio', label: 'Inf' },
      { key: 'status', label: 'Status' },
    ]);

    const snapshotParams = new URLSearchParams({ trade_date: tradeDate, instrument_id: instrumentId });
    featureNames.forEach((name) => snapshotParams.append('feature_names', name));
    const snapshot = await getJson(`/api/feature-snapshot?${snapshotParams.toString()}`);
    const rows = Object.entries(snapshot.features || {}).map(([feature_name, value]) => ({ feature_name, value }));
    renderTable('feature-snapshot-table', rows, [{ key: 'feature_name', label: 'Feature' }, { key: 'value', label: 'Value' }]);
  } catch (error) {
    document.getElementById('feature-health-table').innerHTML = `<div class="empty">${error.message}</div>`;
  }
}

async function loadBacktest() {
  try {
    const runId = document.getElementById('backtest-run-id').value.trim();
    const summary = await getJson(`/api/backtest-runs/${runId}/summary`);
    const daily = await getJson(`/api/backtest-runs/${runId}/daily`);
    document.getElementById('metric-total-return').textContent = summary.metrics.total_return || '-';
    document.getElementById('metric-sharpe').textContent = summary.metrics.sharpe || '-';
    document.getElementById('metric-max-drawdown').textContent = summary.metrics.max_drawdown || '-';
    renderLineChart('backtest-equity-chart', daily.items.map((item) => item.equity), daily.items.map((item) => item.drawdown));
    renderLineChart('backtest-diagnostics-chart', daily.items.map((item) => item.turnover), daily.items.map((item) => item.ic));
    renderTable('backtest-daily-table', daily.items, [
      { key: 'trade_date', label: 'Trade Date' },
      { key: 'equity', label: 'Equity' },
      { key: 'drawdown', label: 'Drawdown' },
      { key: 'turnover', label: 'Turnover' },
      { key: 'ic', label: 'IC' },
      { key: 'rank_ic', label: 'RankIC' },
      { key: 'trade_count', label: 'Trades' },
      { label: 'Orders', render: (row) => `<span class="action-link" onclick="loadBacktestOrders('${row.trade_date}')">Open</span>` },
      { label: 'Replay', render: (row) => `<span class="action-link" onclick="jumpToReplay('${row.trade_date}')">Open</span>` },
    ]);
    const seedDate = daily.items[0]?.trade_date;
    if (seedDate) {
      await loadBacktestOrders(seedDate);
    }
  } catch (error) {
    document.getElementById('backtest-daily-table').innerHTML = `<div class="empty">${error.message}</div>`;
  }
}

async function loadReplay() {
  try {
    const executionDate = document.getElementById('replay-date').value.trim();
    const accountName = document.getElementById('replay-account').value.trim();
    const replay = await getJson(`/api/decision-replay?execution_date=${executionDate}&account_name=${accountName}`);
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
      { label: 'Case', render: (row) => `<span class="action-link" onclick="jumpToCase('${row.instrument_id}')">Open</span>` },
    ]);
    renderTable('replay-candidates-table', replay.scored_candidates, [
      { key: 'instrument_id', label: 'Instrument' },
      { key: 'raw_score', label: 'Raw Score' },
      { key: 'adjusted_score', label: 'Adjusted Score' },
      { key: 'rank', label: 'Rank' },
      { key: 'selected', label: 'Selected' },
      { label: 'Exclusion Reasons', render: (row) => (row.exclusion_reasons || []).join(', ') || '-' },
    ]);
  } catch (error) {
    document.getElementById('replay-candidates-table').innerHTML = `<div class="empty">${error.message}</div>`;
  }
}

window.jumpToReplay = function jumpToReplay(tradeDate) {
  document.getElementById('replay-date').value = tradeDate || document.getElementById('replay-date').value;
  setView('replay');
  loadReplay();
}

window.jumpToCase = function jumpToCase(instrumentId, tradeDate) {
  if (instrumentId) document.getElementById('case-instrument').value = instrumentId;
  if (tradeDate) document.getElementById('case-date').value = tradeDate;
  document.getElementById('case-backtest-run').value = document.getElementById('backtest-run-id').value.trim();
  setView('case');
  loadCase();
}

window.loadBacktestOrders = async function loadBacktestOrders(tradeDate) {
  try {
    const runId = document.getElementById('backtest-run-id').value.trim();
    const payload = await getJson(`/api/backtest-runs/${runId}/orders?trade_date=${tradeDate}`);
    renderTable('backtest-orders-table', payload.items, [
      { key: 'date', label: 'Trade Date' },
      { key: 'symbol', label: 'Instrument' },
      { key: 'side', label: 'Side' },
      { key: 'filled_amount', label: 'Filled Qty' },
      { key: 'deal_price', label: 'Deal Price' },
      { key: 'fee', label: 'Fee' },
      { key: 'status', label: 'Status' },
      { key: 'reason', label: 'Reason' },
      { label: 'Case', render: (row) => `<span class="action-link" onclick="jumpToCase('${row.symbol}', '${row.date}')">Open</span>` },
    ]);
  } catch (error) {
    document.getElementById('backtest-orders-table').innerHTML = `<div class="empty">${error.message}</div>`;
  }
}

function bindEvents() {
  document.querySelectorAll('.nav-btn').forEach((btn) => btn.addEventListener('click', () => setView(btn.dataset.view)));
  document.getElementById('load-case').addEventListener('click', loadCase);
  document.getElementById('load-feature').addEventListener('click', loadFeatureHealth);
  document.getElementById('load-backtest').addEventListener('click', loadBacktest);
  document.getElementById('load-replay').addEventListener('click', loadReplay);
}

bindEvents();
loadCase();
loadFeatureHealth();
loadBacktest();
loadReplay();
