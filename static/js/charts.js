/* Charts — inline SVG, no library.
 *
 * The trend and sparkline are SINGLE-SERIES, so they carry no legend (the card
 * title names what is plotted) and use categorical slot 1 for the mark. Specs
 * follow the mark contract: 2px line, >=8px end marker with a 2px surface ring,
 * area wash at ~10%, hairline solid gridlines.
 *
 * The donut is the one multi-series form, so it is the only one with a legend.
 * It uses four validated colour slots and merges the tail into a neutral arc
 * rather than inventing a fifth colour.
 *
 * Every chart ships a hover layer AND a table-view twin, so no value is
 * reachable only by hovering.
 */

import { escapeHtml } from './api.js';

const NS = 'http://www.w3.org/2000/svg';

function el(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

/* Round an axis maximum up to a clean number (0 / 1,000 / 2,000 …). */
function niceMax(value) {
  if (value <= 0) return 10;
  const pow = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (value <= step * pow) return step * pow;
  }
  return 10 * pow;
}

function tickValues(max, count = 4) {
  return Array.from({ length: count + 1 }, (_, i) => (max / count) * i);
}

function shortNumber(n) {
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}K`;
  return String(Math.round(n));
}

/* Shared floating tooltip. Tooltips enhance; the table view is the guarantee. */
let tipEl = null;
function tooltip() {
  if (!tipEl) {
    tipEl = document.createElement('div');
    tipEl.className = 'chart-tip';
    tipEl.hidden = true;
    document.body.appendChild(tipEl);
  }
  return tipEl;
}

function showTip(html, x, y) {
  const tip = tooltip();
  tip.innerHTML = html;
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  // Keep it on screen near the cursor.
  let left = x + 14;
  if (left + box.width > window.innerWidth - 8) left = x - box.width - 14;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, y - box.height - 10)}px`;
}

function hideTip() {
  if (tipEl) tipEl.hidden = true;
}

/* -------------------------------------------------------------------------
 * Trend over time — line + area wash, one series.
 * points: [{ label, value, tipLabel }]
 * ---------------------------------------------------------------------- */
export function trendChart(container, points, { formatValue = shortNumber, height = 210 } = {}) {
  container.innerHTML = '';
  if (!points.length) {
    container.innerHTML = '<div class="empty">No data for this period yet.</div>';
    return;
  }

  const pad = { top: 14, right: 16, bottom: 26, left: 46 };
  const width = Math.max(container.clientWidth || 640, 320);
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const max = niceMax(Math.max(...points.map(p => p.value), 0));
  const x = i => pad.left + (points.length === 1 ? plotW / 2 : (plotW * i) / (points.length - 1));
  const y = v => pad.top + plotH - (max === 0 ? 0 : (v / max) * plotH);

  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${width} ${height}`,
    width: '100%', height, role: 'img',
    'aria-label': 'Revenue trend. The same values are available in the table view.',
  });

  // Gridlines + y ticks (solid hairlines, recessive).
  for (const t of tickValues(max)) {
    svg.appendChild(el('line', {
      class: 'grid-line', x1: pad.left, x2: width - pad.right, y1: y(t), y2: y(t),
    }));
    const label = el('text', {
      class: 'axis-text', x: pad.left - 8, y: y(t) + 4, 'text-anchor': 'end',
    });
    label.textContent = shortNumber(t);
    svg.appendChild(label);
  }

  // Area wash then line, so the stroke sits on top.
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p.value)}`).join(' ');
  svg.appendChild(el('path', {
    class: 'series-area',
    d: `${line} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z`,
  }));
  svg.appendChild(el('path', { class: 'series-line', d: line }));

  // End marker only — never a dot on every point.
  const last = points.length - 1;
  svg.appendChild(el('circle', { class: 'end-dot', cx: x(last), cy: y(points[last].value), r: 4.5 }));

  // X labels: first, middle, last only, so they never collide.
  for (const i of new Set([0, Math.floor(last / 2), last])) {
    const label = el('text', {
      class: 'axis-text', x: x(i), y: height - 8,
      'text-anchor': i === 0 ? 'start' : i === last ? 'end' : 'middle',
    });
    label.textContent = points[i].label;
    svg.appendChild(label);
  }

  // Hover layer: a crosshair plus a band per point, each at least 24px wide.
  const crosshair = el('line', { class: 'crosshair', y1: pad.top, y2: pad.top + plotH, opacity: 0 });
  svg.appendChild(crosshair);
  const marker = el('circle', { class: 'end-dot', r: 4.5, opacity: 0 });
  svg.appendChild(marker);

  const bandW = plotW / Math.max(points.length, 1);
  points.forEach((p, i) => {
    const band = el('rect', {
      class: 'hover-band',
      x: x(i) - Math.max(bandW, 24) / 2, y: pad.top,
      width: Math.max(bandW, 24), height: plotH,
      tabindex: '0', role: 'button',
      'aria-label': `${p.tipLabel || p.label}: ${formatValue(p.value)}`,
    });
    const enter = event => {
      crosshair.setAttribute('x1', x(i));
      crosshair.setAttribute('x2', x(i));
      crosshair.setAttribute('opacity', 1);
      marker.setAttribute('cx', x(i));
      marker.setAttribute('cy', y(p.value));
      marker.setAttribute('opacity', 1);
      const box = (event.target.getBoundingClientRect && event.target.getBoundingClientRect()) || {};
      const cx = event.clientX ?? (box.left + box.width / 2);
      const cy = event.clientY ?? box.top;
      showTip(
        `<span class="tip-date">${escapeHtml(p.tipLabel || p.label)}</span>` +
        `<span class="tip-value">${escapeHtml(formatValue(p.value))}</span>`,
        cx, cy,
      );
    };
    const leave = () => {
      crosshair.setAttribute('opacity', 0);
      marker.setAttribute('opacity', 0);
      hideTip();
    };
    band.addEventListener('mousemove', enter);
    band.addEventListener('mouseleave', leave);
    band.addEventListener('focus', enter);   // keyboard parity with hover
    band.addEventListener('blur', leave);
    svg.appendChild(band);
  });

  container.appendChild(svg);
}

/* -------------------------------------------------------------------------
 * Composition — donut, one arc per category.
 *
 * This is the ONE multi-series form here, so it is the only one that carries a
 * legend. Four validated colour slots; anything past the fourth category is
 * merged into a single neutral "Other" arc rather than reaching for a fifth
 * colour that would not hold up under CVD. Every arc also states its share in
 * the legend, so colour is never the only carrier.
 *
 * items: [{ label, value }] — assumed already sorted, descending.
 * ---------------------------------------------------------------------- */
export function donutChart(container, items, { formatValue = shortNumber, size = 210 } = {}) {
  container.innerHTML = '';
  const positive = items.filter(i => Number(i.value) > 0);
  if (!positive.length) {
    container.innerHTML = '<div class="empty">Nothing to show yet.</div>';
    return;
  }

  // Cap at four coloured slots; the tail becomes one neutral arc.
  const MAX_SLOTS = 4;
  const slices = positive.slice(0, MAX_SLOTS).map((item, i) => ({
    label: item.label, value: Number(item.value), cls: `arc-${i + 1}`,
  }));
  const tail = positive.slice(MAX_SLOTS);
  if (tail.length) {
    slices.push({
      label: `Other (${tail.length})`,
      value: tail.reduce((sum, i) => sum + Number(i.value), 0),
      cls: 'arc-rest',
    });
  }

  const total = slices.reduce((sum, s) => sum + s.value, 0);
  const cx = size / 2;
  const cy = size / 2;
  const outer = size / 2 - 4;
  const inner = outer * 0.6;             // donut hole, ~60% keeps the ring readable

  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${size} ${size}`,
    width: size, height: size, role: 'img',
    'aria-label': 'Share by category. The same values are listed in the table view.',
  });
  svg.style.maxWidth = '100%';

  let angle = -Math.PI / 2;              // start at 12 o'clock
  slices.forEach(slice => {
    const sweep = (slice.value / total) * Math.PI * 2;
    const end = angle + sweep;

    // A full-circle slice cannot be drawn as an arc (start meets end), so draw
    // the ring as two half-circles instead.
    const path = sweep >= Math.PI * 2 - 1e-6
      ? ringPath(cx, cy, outer, inner)
      : arcPath(cx, cy, outer, inner, angle, end);

    const node = el('path', { class: `arc ${slice.cls}`, d: path, tabindex: '0', role: 'button' });
    const share = `${((slice.value / total) * 100).toFixed(1)}%`;
    node.setAttribute('aria-label', `${slice.label}: ${formatValue(slice.value)}, ${share}`);

    const enter = event => showTip(
      `<span class="tip-date">${escapeHtml(slice.label)}</span>` +
      `<span class="tip-value">${escapeHtml(formatValue(slice.value))} · ${share}</span>`,
      event.clientX ?? 0, event.clientY ?? 0,
    );
    node.addEventListener('mousemove', enter);
    node.addEventListener('mouseleave', hideTip);
    node.addEventListener('focus', enter);
    node.addEventListener('blur', hideTip);
    svg.appendChild(node);

    angle = end;
  });

  const totalText = el('text', { class: 'donut-total', x: cx, y: cy - 1, 'text-anchor': 'middle' });
  totalText.textContent = formatValue(total);
  svg.appendChild(totalText);
  const caption = el('text', { class: 'donut-caption', x: cx, y: cy + 15, 'text-anchor': 'middle' });
  caption.textContent = 'total';
  svg.appendChild(caption);

  const wrap = document.createElement('div');
  wrap.style.display = 'flex';
  wrap.style.justifyContent = 'center';
  wrap.appendChild(svg);
  container.appendChild(wrap);

  const legend = document.createElement('div');
  legend.className = 'legend';
  legend.innerHTML = slices.map(s => `
    <span class="legend-item">
      <span class="legend-swatch ${s.cls}"></span>
      <span class="legend-label">${escapeHtml(s.label)}</span>
      <span class="legend-value">${escapeHtml(((s.value / total) * 100).toFixed(0))}%</span>
    </span>`).join('');
  container.appendChild(legend);
}

/* Annular wedge between two angles. */
function arcPath(cx, cy, outer, inner, start, end) {
  const large = end - start > Math.PI ? 1 : 0;
  const p = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const [x1, y1] = p(outer, start);
  const [x2, y2] = p(outer, end);
  const [x3, y3] = p(inner, end);
  const [x4, y4] = p(inner, start);
  return `M${x1},${y1} A${outer},${outer} 0 ${large} 1 ${x2},${y2} ` +
         `L${x3},${y3} A${inner},${inner} 0 ${large} 0 ${x4},${y4} Z`;
}

/* Complete ring, for the single-category case. */
function ringPath(cx, cy, outer, inner) {
  return `M${cx - outer},${cy} a${outer},${outer} 0 1 0 ${outer * 2},0 a${outer},${outer} 0 1 0 ${-outer * 2},0 ` +
         `M${cx - inner},${cy} a${inner},${inner} 0 1 1 ${inner * 2},0 a${inner},${inner} 0 1 1 ${-inner * 2},0 Z`;
}

/* -------------------------------------------------------------------------
 * Sparkline for a stat tile — de-emphasis line, current point in the accent.
 * ---------------------------------------------------------------------- */
export function sparkline(container, values) {
  container.innerHTML = '';
  const points = values.filter(v => Number.isFinite(v));
  if (points.length < 2) return;

  const width = Math.max(container.clientWidth || 160, 80);
  const height = 34;
  const padX = 0;                        // bleeds to the card edge
  const padY = 5;
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const span = max - min || 1;

  const x = i => padX + ((width - padX * 2) * i) / (points.length - 1);
  const y = v => height - padY - ((v - min) / span) * (height - padY * 2);

  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${width} ${height}`,
    width: '100%', height, preserveAspectRatio: 'none', 'aria-hidden': 'true',
  });

  const line = points.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join(' ');
  // Wash under the line, closed along the baseline.
  svg.appendChild(el('path', {
    d: `${line} L${x(points.length - 1)},${height} L${x(0)},${height} Z`,
    class: 'series-area',
  }));
  svg.appendChild(el('path', {
    d: line, fill: 'none', stroke: 'var(--series-1)', 'stroke-width': 1.6,
    'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'vector-effect': 'non-scaling-stroke',
  }));
  container.appendChild(svg);
}

/* -------------------------------------------------------------------------
 * Table view — the WCAG-clean twin every chart must have.
 * ---------------------------------------------------------------------- */
export function tableView(container, columns, rowsData) {
  const head = columns.map((c, i) => `<th${i ? ' class="num"' : ''}>${escapeHtml(c)}</th>`).join('');
  const body = rowsData.map(r => (
    `<tr>${r.map((cell, i) => `<td${i ? ' class="num"' : ''}>${escapeHtml(cell)}</td>`).join('')}</tr>`
  )).join('');
  container.innerHTML = `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* Wires the "Show table"/"Show chart" toggle on a card. Idempotent: callers
 * re-render on every refetch, and a second listener would flip the card twice
 * per click. */
export function bindTableToggle(button, chartEl, tableEl) {
  if (!button || button.dataset.toggleBound) return;
  button.dataset.toggleBound = '1';
  button.addEventListener('click', () => {
    const showingTable = !tableEl.hidden;
    tableEl.hidden = showingTable;
    chartEl.hidden = !showingTable;
    button.textContent = showingTable ? 'Show table' : 'Show chart';
  });
}
