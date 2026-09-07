/* Shared SVG Gantt chart renderer. */
(function (global) {
  'use strict';

  const AXIS_H = 44;
  const NS = 'http://www.w3.org/2000/svg';
  const ROW_H = 38;
  const HEADER_H = 36;
  const BAR_H = 22;
  const GROUP_GAP = 16;
  // Inset the plot from the card edge so bars never sit flush (~5 mm at 96 dpi).
  const CHART_PAD_LEFT = 20;
  const CHART_PAD_RIGHT = 8;
  const PALETTE = [
    'hsl(211,90%,60%)', 'hsl(38,95%,58%)', 'hsl(160,55%,50%)', 'hsl(280,60%,68%)',
    'hsl(0,75%,62%)', 'hsl(190,65%,52%)', 'hsl(48,90%,55%)', 'hsl(320,60%,65%)',
    'hsl(100,50%,55%)', 'hsl(230,75%,65%)', 'hsl(15,80%,60%)', 'hsl(170,55%,50%)',
  ];

  function chartTheme() {
    return {
      palette: PALETTE,
      gridLine: 'hsl(220,10%,15%)',
      axisText: 'hsl(220,10%,46%)',
      axisTextWeekend: 'hsl(220,10%,32%)',
      metaText: 'hsl(220,14%,86%)',
      statusColors: {
        'In tempo': '#ffffff',
        'Pianificata': 'hsl(220,8%,58%)',
        'In ritardo': 'hsl(28,95%,55%)',
        'Rischio penale': 'hsl(0,80%,58%)',
      },
      weekendFill: 'hsl(220,30%,80%)',
      weekendOpacity: '0.06',
      todayFill: 'hsl(0,80%,50%)',
      todayOpacity: '0.10',
      todayLineColor: 'hsl(0,90%,58%)',
      todayLineOpacity: '0.60',
      todayLineWidth: '1.5',
      deadlineColor: 'hsl(0,80%,60%)',
      changedStroke: 'hsl(160,70%,50%)',
      barFillOpacity: '0.08',
      barChangedFillOpacity: '0.18',
      barStrokeOpacity: '0.70',
      currentFillOpacity: '0.50',
      proposedFillOpacity: '0.06',
    };
  }

  let _globalTipDismissBound = false;
  function bindGlobalTipDismiss() {
    if (_globalTipDismissBound) return;
    _globalTipDismissBound = true;
    document.addEventListener('click', e => {
      document.querySelectorAll('#gantt-tip, #ott-gantt-tip, #gantt-proposal-tip, [id$="-gantt-tip"]').forEach(tip => {
        if (tip.style.display === 'none') return;
        if (!tip.contains(e.target) && !e.target.closest('[data-task]')) {
          tip.style.display = 'none';
        }
      });
    });
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      document.querySelectorAll('#gantt-tip, #ott-gantt-tip, #gantt-proposal-tip, [id$="-gantt-tip"]').forEach(tip => {
        tip.style.display = 'none';
      });
    });
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS(NS, name);
    if (attrs) Object.keys(attrs).forEach(k => el.setAttribute(k, attrs[k]));
    return el;
  }

  function appendProblemsBadge(parent, x, y, count, ormId) {
    const label = String(count);
    const h = 18;
    const tri = 12;
    const countW = Math.max(8, label.length * 6.6);
    const w = 6 + tri + 4 + countW + 8;
    const g = svgEl('g', {
      class: 'gantt-problems-badge',
      'data-warn-ormid': String(ormId),
      transform: 'translate(' + x + ',' + y + ')',
    });
    const tip = svgEl('title');
    tip.textContent = count === 1 ? '1 problema aperto' : count + ' problemi aperti';
    g.appendChild(tip);
    g.appendChild(svgEl('rect', {
      class: 'gantt-problems-badge__pill',
      width: String(w),
      height: String(h),
      rx: String(h / 2),
      fill: 'hsla(36,90%,50%,0.18)',
      stroke: 'hsla(36,85%,58%,0.55)',
      'stroke-width': '1',
    }));
    const tg = svgEl('g', { transform: 'translate(5,3) scale(0.6)' });
    tg.appendChild(svgEl('path', {
      d: 'M10 2.15c.47 0 .9.25 1.14.66l7.2 12.85A1.32 1.32 0 0 1 17.2 17.8H2.8a1.32 1.32 0 0 1-1.14-1.98L8.86 2.81c.24-.41.67-.66 1.14-.66z',
      fill: 'hsl(36,92%,54%)',
    }));
    tg.appendChild(svgEl('path', {
      d: 'M10 7.05c.36 0 .65.28.65.64v3.55c0 .36-.29.64-.65.64s-.65-.28-.65-.64V7.69c0-.36.29-.64.65-.64zm0 6.2a.82.82 0 1 1 0 1.64.82.82 0 0 1 0-1.64z',
      fill: 'hsl(28,28%,10%)',
    }));
    g.appendChild(tg);
    const t = svgEl('text', {
      x: String(6 + tri + 4),
      y: '12.6',
      fill: 'hsl(38,100%,82%)',
      'font-size': '10',
      'font-weight': '700',
      'font-family': 'Inter,system-ui,sans-serif',
      'pointer-events': 'none',
    });
    t.textContent = label;
    g.appendChild(t);
    parent.appendChild(g);
    return w;
  }

  function parseDate(s) {
    if (!s) return new Date(NaN);
    const [y, m, d] = String(s).slice(0, 10).split('-').map(Number);
    if (!y || !m || !d) return new Date(NaN);
    return new Date(y, m - 1, d);
  }
  function startOfWeek(d) {
    const r = new Date(d);
    const day = r.getDay() || 7;
    r.setDate(r.getDate() - (day - 1));
    r.setHours(0, 0, 0, 0);
    return r;
  }
  function floorMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
  function floorYear(d) { return new Date(d.getFullYear(), 0, 1); }
  function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
  function addMonths(d, n) { const r = new Date(d); r.setMonth(r.getMonth() + n); return r; }
  function addYears(d, n) { const r = new Date(d); r.setFullYear(r.getFullYear() + n); return r; }
  function buildTicks(minD, maxD, view) {
    const ticks = [];
    let cur = new Date(minD);
    const step = (view === 'year') ? addMonths : addDays;
    while (cur < maxD) { ticks.push(new Date(cur)); cur = step(cur, 1); }
    return ticks;
  }

  function chooseStepDays(spanDays, totalW) {
    const minLabelPx = 76;
    const maxLabels = Math.max(3, Math.floor(totalW / minLabelPx));
    const candidates = [1, 2, 7, 14, 21, 30, 60, 90, 180, 365];
    for (let i = 0; i < candidates.length; i++) {
      if (spanDays / candidates[i] <= maxLabels) return candidates[i];
    }
    return 365;
  }

  function buildAdaptiveTicks(minD, maxD, totalW) {
    const spanDays = Math.max(1, (maxD - minD) / 86400000);
    const stepDays = chooseStepDays(spanDays, totalW);
    const ticks = [];
    let cur = new Date(minD);
    cur.setHours(0, 0, 0, 0);
    if (stepDays >= 28) cur = floorMonth(cur);
    else if (stepDays >= 7) cur = startOfWeek(cur);
    const advance = (d) => (stepDays >= 28 ? addMonths(d, 1) : addDays(d, stepDays));
    while (cur < maxD) {
      if (cur >= minD) ticks.push(new Date(cur));
      cur = advance(cur);
    }
    const labelMode = stepDays >= 28 ? 'year' : (stepDays >= 7 ? 'month' : 'week');
    return { ticks, stepDays, labelMode };
  }

  function fmtLabel(d, view) {
    if (view === 'week') return d.toLocaleDateString('it-IT', { weekday: 'short', day: 'numeric' });
    if (view === 'month') return d.toLocaleDateString('it-IT', { day: 'numeric', month: 'short' });
    return d.toLocaleDateString('it-IT', { month: 'short', year: '2-digit' });
  }
  function fmtDate(s) {
    if (!s) return '—';
    return new Date(s + 'T00:00:00').toLocaleDateString('it-IT', { day: '2-digit', month: 'short', year: 'numeric' });
  }
  function getCookie(name) {
    const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return m ? m.pop() : '';
  }

  function taskActivityExtents(tasks) {
    let min = null;
    let max = null;
    const consider = (d) => {
      if (!d || isNaN(d)) return;
      min = min ? (d < min ? d : min) : new Date(d);
      max = max ? (d > max ? d : max) : new Date(d);
    };
    (tasks || []).forEach(t => {
      const s = parseDate(t.start);
      const f = parseDate(t.finish);
      consider(s);
      consider(f);
      if (!isNaN(s) && !isNaN(f)) {
        const durMs = f - s;
        const actS = t.actual_start ? parseDate(t.actual_start) : null;
        const actEnd = t.actual_finish ? parseDate(t.actual_finish)
          : (actS ? new Date(actS.getTime() + durMs) : null);
        if (actS) consider(actS);
        if (actEnd) consider(actEnd);
      }
    });
    return { min, max };
  }

  function suggestViewForSpanDays(spanDays) {
    if (spanDays <= 45) return 'week';
    if (spanDays <= 280) return 'month';
    return 'year';
  }

  function tickViewForWindowDays(windowDays) {
    if (windowDays <= 14) return 'week';
    if (windowDays <= 100) return 'month';
    return 'year';
  }

  function earliestCommessaStartMs(tasks) {
    let min = Infinity;
    (tasks || []).forEach(t => {
      const s = parseDate(t && (t.start || t.actual_start));
      if (!isNaN(s)) {
        const ms = s.getTime();
        if (ms < min) min = ms;
      }
    });
    return min;
  }

  function taskIsWip(t) {
    return !!(t && t.actual_start && !t.actual_finish);
  }

  function taskIsIncomplete(t) {
    return !!(t && !t.actual_finish);
  }

  function taskIsCompleted(t) {
    return !!(t && t.actual_finish);
  }

  /** Same late/overdue rules as the home ``updateStats`` counters. */
  function taskIsLate(t, today) {
    if (!t) return false;
    const actS = t.actual_start ? new Date(t.actual_start + 'T00:00:00') : null;
    const actF = t.actual_finish ? new Date(t.actual_finish + 'T00:00:00') : null;
    const planS = t.start ? new Date(t.start + 'T00:00:00') : null;
    if (actS && actF) return false;
    const startedLate = !!(actS && planS && actS > planS);
    // Server uses ``today >= data_inizio`` (ui/views.py); keep both in step.
    const overdue = !!(!actS && planS && today >= planS);
    return startedLate || overdue;
  }

  function parseIsoNoon(raw) {
    const s = String(raw || '').trim().slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
    const d = new Date(s + 'T12:00:00');
    return isNaN(d.getTime()) ? null : d;
  }

  function isWorkingDay(day) {
    const wd = day.getDay();
    return wd >= 1 && wd <= 5;
  }

  function countWorkingDaysInclusive(start, end) {
    if (!start || !end) return 0;
    if (end < start) return 1;
    let working = 0;
    const cur = new Date(start.getTime());
    const last = new Date(end.getTime());
    while (cur <= last) {
      if (isWorkingDay(cur)) working += 1;
      cur.setDate(cur.getDate() + 1);
    }
    return Math.max(1, working);
  }

  // Same formula as pianificazione `_activityWorkProgress` / `_pianJobStats`.
  function activityWorkProgress(task) {
    const planStart = parseIsoNoon(task && task.start);
    const planFinish = parseIsoNoon(task && task.finish);
    if (!planStart || !planFinish) return { done: 0, total: 0 };
    const total = countWorkingDaysInclusive(planStart, planFinish);
    if (parseIsoNoon(task.actual_finish)) return { done: total, total };
    const actualStart = parseIsoNoon(task.actual_start);
    if (!actualStart) return { done: 0, total };
    const today = new Date();
    today.setHours(12, 0, 0, 0);
    if (today < actualStart) return { done: 0, total };
    const worked = countWorkingDaysInclusive(actualStart, today);
    return { done: Math.min(total, worked), total };
  }

  function summarizeCommessaTasks(tasks, deadlineStr) {
    const list = tasks || [];
    const total = list.length;
    let completed = 0;
    let wip = 0;
    let workDone = 0;
    let workTotal = 0;
    let anyLate = false;
    let anyPenaltyRisk = false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const deadline = parseIsoNoon(deadlineStr) || parseDate(deadlineStr);
    const hasDeadline = deadline && !isNaN(deadline.getTime());

    list.forEach(t => {
      if (taskIsCompleted(t)) completed += 1;
      else if (taskIsWip(t)) wip += 1;
      const progress = activityWorkProgress(t);
      workDone += progress.done;
      workTotal += progress.total;

      const planS = parseDate(t.start);
      const planF = parseDate(t.finish);
      const actS = t.actual_start ? parseDate(t.actual_start) : null;
      const actF = t.actual_finish ? parseDate(t.actual_finish) : null;
      if (!isNaN(planS)) {
        if (actS && actS > planS) anyLate = true;
        else if (!actS && !actF && today >= planS) anyLate = true;
      }

      if (hasDeadline && !isNaN(planS) && !isNaN(planF)) {
        const durMs = planF - planS;
        let end = null;
        if (actF && !isNaN(actF)) end = actF;
        else if (actS && !isNaN(actS)) end = new Date(actS.getTime() + durMs);
        if (end && end > deadline) anyPenaltyRisk = true;
      }
    });

    const pct = workTotal ? Math.round(workDone / workTotal * 100) : 0;
    let status = 'Pianificata';
    if (anyPenaltyRisk) status = 'Rischio penale';
    else if (anyLate) status = 'In ritardo';
    else if (wip > 0 || completed > 0) status = 'In tempo';
    return { total, completed, wip, pct, status, workDone, workTotal };
  }

  function computeHorizonViewport(tasks) {
    // Wide default window: left = first in-progress activity, right ≈ +6 months.
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    let left = null;

    (tasks || []).forEach(t => {
      if (!taskIsWip(t)) return;
      const s = parseDate(t.actual_start);
      if (!isNaN(s) && (!left || s < left)) left = new Date(s);
    });

    if (!left) {
      // Fallback: earliest incomplete activity (planned or actual start).
      (tasks || []).forEach(t => {
        if (!taskIsIncomplete(t)) return;
        const s = parseDate(t.actual_start || t.start);
        if (!isNaN(s) && (!left || s < left)) left = new Date(s);
      });
    }

    if (!left) left = today;

    // Align to the leftmost bar start; visual left pad is applied in render().
    (tasks || []).forEach(t => {
      if (!taskIsWip(t) && !taskIsIncomplete(t)) return;
      const planned = parseDate(t.start);
      if (!isNaN(planned) && planned < left) left = new Date(planned);
    });
    const minD = new Date(left);
    const maxD = addMonths(minD, 6);
    const windowDays = Math.max(1, (maxD - minD) / 86400000);

    return {
      minD,
      maxD,
      spanDays: windowDays,
      view: 'horizon',
      tickView: tickViewForWindowDays(windowDays),
    };
  }

  function computeFitViewport() {
    let min = null;
    let max = null;
    for (let i = 0; i < arguments.length; i++) {
      const tasks = arguments[i];
      const ext = taskActivityExtents(tasks);
      if (ext.min) min = min ? (ext.min < min ? ext.min : min) : ext.min;
      if (ext.max) max = max ? (ext.max > max ? ext.max : max) : ext.max;
    }
    if (!min || !max) return null;
    if (max < min) { const tmp = min; min = max; max = tmp; }

    const spanDays = Math.max(1, Math.ceil((max - min) / 86400000) + 1);
    const mid = new Date(min.getTime() + (max.getTime() - min.getTime()) / 2);

    // Pad so activities sit near the centre of a months window (not glued to the edges).
    const sidePad = Math.max(21, Math.ceil(spanDays * 0.25));
    let minD = floorMonth(addDays(min, -sidePad));
    let maxD = addMonths(floorMonth(addDays(max, sidePad)), 1);

    // Prefer at least a 3-month window centred on the activity midpoint.
    const minMonths = 3;
    let windowDays = (maxD - minD) / 86400000;
    if (windowDays < minMonths * 28) {
      minD = addMonths(floorMonth(mid), -1);
      maxD = addMonths(floorMonth(mid), 2);
      windowDays = (maxD - minD) / 86400000;
    }

    return {
      minD,
      maxD,
      spanDays,
      view: suggestViewForSpanDays(windowDays),
      tickView: tickViewForWindowDays(windowDays),
    };
  }

  class GanttChart {
    constructor(options) {
      options = options || {};
      this.prefix = options.prefix || '';
      this.tooltipId = options.tooltipId || (this.prefix ? this.prefix + '-tip' : 'gantt-tip');
      this.view = options.initialView || 'year';
      this.offset = 0;
      this.tasks = [];
      this.deadlines = {};
      this.enableStats = !!options.enableStats;
      this.enableDeadlineEditor = !!options.enableDeadlineEditor;
      this.enableWarningNav = !!options.enableWarningNav;
      this.simplified = !!options.simplified;
      this.fitToData = options.fitToData ?? !!options.simplified;
      this.collapsibleGroups = !!options.collapsibleGroups;
      this.highlightChanges = options.highlightChanges || null;
      this.compareMode = !!options.compareMode;
      this.compareTasks = options.compareTasks || [];
      // Home stats filter: null | 'late' | 'wip' (totals card clears the filter).
      this.statFilter = null;
      this._dataMinD = null;
      this._dataMaxD = null;
      this._bound = false;
      // Keys = commessa (or job_id). Empty = all collapsed when collapsibleGroups.
      this._expandedGroups = new Set();
      if (this.view === 'horizon') this.fitToData = true;
    }

    _groupKey(gOrTask) {
      return gOrTask.commessa || gOrTask.job_id || 'unknown';
    }

    _isGroupExpanded(key) {
      if (!this.collapsibleGroups) return true;
      return this._expandedGroups.has(key);
    }

    _allGroupKeys() {
      const keys = [];
      const seen = {};
      this.displayTasks().forEach(t => {
        const k = this._groupKey(t);
        if (!seen[k]) { seen[k] = true; keys.push(k); }
      });
      return keys;
    }

    displayTasks() {
      const all = this.tasks || [];
      if (!this.statFilter) return all;
      if (this.statFilter === 'wip') return all.filter(taskIsWip);
      if (this.statFilter === 'late') {
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        return all.filter(t => taskIsLate(t, today));
      }
      return all;
    }

    _renderCurrent() {
      this.render(this.displayTasks(), this.view);
    }

    setStatFilter(filterKey) {
      if (!this.enableStats) return;
      let next = filterKey || null;
      if (next === 'total') next = null;
      if (next !== null && next !== 'late' && next !== 'wip') next = null;
      // Toggle off when clicking the active filter again.
      if (next && this.statFilter === next) next = null;
      this.statFilter = next;
      this._syncStatFilterUI();
      if (!this._fitLocked) {
        const visible = this.displayTasks();
        const basis = visible.length ? visible : this.tasks;
        if (this.view === 'horizon') this.applyHorizonToData(basis);
        else if (this.fitToData) this.applyFitToData(basis);
      }
      this._renderCurrent();
    }

    _syncStatFilterUI() {
      document.querySelectorAll('#stats-row .stat-card[data-stat-filter]').forEach(card => {
        const key = card.getAttribute('data-stat-filter');
        const active = this.statFilter
          ? key === this.statFilter
          : key === 'total';
        card.classList.toggle('is-active', active);
        card.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
    }

    toggleGroup(key) {
      if (!this.collapsibleGroups || !key) return;
      if (this._expandedGroups.has(key)) this._expandedGroups.delete(key);
      else this._expandedGroups.add(key);
      this._syncExpandToggleBtn();
      this._renderCurrent();
    }

    expandAllGroups() {
      if (!this.collapsibleGroups) return;
      this._allGroupKeys().forEach(k => this._expandedGroups.add(k));
      this._syncExpandToggleBtn();
      this._renderCurrent();
    }

    collapseAllGroups() {
      if (!this.collapsibleGroups) return;
      this._expandedGroups.clear();
      this._syncExpandToggleBtn();
      this._renderCurrent();
    }

    _syncExpandToggleBtn() {
      const btn = this.el('nav-expand-toggle');
      if (!btn || !this.collapsibleGroups) return;
      const keys = this._allGroupKeys();
      const allOpen = keys.length > 0 && keys.every(k => this._expandedGroups.has(k));
      btn.textContent = allOpen ? 'Collassa tutto' : 'Espandi tutto';
      btn.dataset.mode = allOpen ? 'collapse' : 'expand';
    }

    id(name) {
      return this.prefix ? (this.prefix + '-' + name) : name;
    }

    el(name) {
      return document.getElementById(this.id(name));
    }

    _layoutRoot(barsEl) {
      return (barsEl && barsEl.parentElement) ? barsEl.parentElement : barsEl;
    }

    _chartWidth(barsEl) {
      const root = this._layoutRoot(barsEl);
      const w = (root && root.clientWidth) || (barsEl && barsEl.clientWidth) || 0;
      return w > 0 ? w : 800;
    }

    tipEl() {
      return document.getElementById(this.tooltipId);
    }

    buildTipHtml(t) {
      const durMs = parseDate(t.finish) - parseDate(t.start);
      const durDays = Math.round(durMs / 86400000);
      const dur = durDays > 0 ? durDays + (durDays === 1 ? ' giorno' : ' giorni') : '—';
      const activityLabel = (t.item ? t.item + ' · ' : '') + (t.activity || '—');
      const commessa = t.commessa || '—';
      const variantLabel = t.variant === 'proposed' ? 'Proposta'
        : (t.variant === 'current' ? 'Attuale' : null);
      let rows = '';
      const row = (lbl, val, cls) =>
        '<div class="tip-row"><span class="tip-lbl">' + lbl + '</span><span class="tip-val' + (cls ? ' ' + cls : '') + '">' + val + '</span></div>';
      if (variantLabel) rows += row('Variante', variantLabel, 'tip-highlight');
      rows += row('Inizio previsto', fmtDate(t.start));
      rows += row('Fine prevista', fmtDate(t.finish));
      rows += row('Durata stimata', dur);
      if (!this.simplified) {
        const hasActual = t.actual_start || t.actual_finish;
        if (hasActual) {
          rows += '<div class="tip-divider"></div>';
          if (t.actual_start) rows += row('Inizio effettivo', fmtDate(t.actual_start), 'tip-highlight');
          if (t.actual_finish) rows += row('Fine effettiva', fmtDate(t.actual_finish), 'tip-highlight');
        }
      }
      rows += '<div class="tip-divider"></div>';
      rows += row('Stabilimento', t.plant
        ? '<span class="stab-badge"' + (t.plant_color ? ' style="--stab-c:' + t.plant_color + '"' : '') + '>' + t.plant + '</span>'
        : '—');
      if (!this.simplified) rows += row('Responsabile', t.responsible || '—');
      const resList = (t.assigned_resources && t.assigned_resources.length)
        ? t.assigned_resources
        : (t.resources
          ? t.resources.split(/[;,]/).map(r => r.trim()).filter(Boolean)
          : []);
      if (resList.length) {
        rows += row('Risorse', resList.join(';'), '');
      }
      return (
        '<div class="gantt-tip-header">' +
          '<div class="gantt-tip-header-text">' +
            '<div class="tip-commessa">' + commessa + '</div>' +
            '<div class="tip-activity">' + activityLabel + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="gantt-tip-body">' + rows + '</div>'
      );
    }

    offsetStep(view) {
      if (view === 'week') return 1;
      if (view === 'month') return 7;
      return 31;
    }

    updateNavState() {
      const todayBtn = this.el('nav-today');
      if (!todayBtn) return;
      const atOrigin = this.fitToData ? !this._fitShifted : this.offset === 0;
      if (atOrigin) todayBtn.classList.add('at-today');
      else todayBtn.classList.remove('at-today');
    }

    _syncViewButtons() {
      const panel = this.prefix ? document.getElementById(this.prefix + '-panel') : null;
      const root = panel || document;
      root.querySelectorAll('.tbtn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.view === this.view);
      });
    }

    applyFitViewport(fit) {
      if (!fit) {
        this._dataMinD = null;
        this._dataMaxD = null;
        this._fitTickView = null;
        this._fitShifted = false;
        return;
      }
      this.fitToData = true;
      this._dataMinD = new Date(fit.minD.getTime());
      this._dataMaxD = new Date(fit.maxD.getTime());
      this._fitOriginMinD = new Date(fit.minD.getTime());
      this._fitOriginMaxD = new Date(fit.maxD.getTime());
      const windowDays = (this._dataMaxD - this._dataMinD) / 86400000;
      this._fitTickView = fit.tickView || tickViewForWindowDays(windowDays);
      this._fitShifted = false;
      this.view = fit.view || suggestViewForSpanDays(fit.spanDays || 1);
      this.offset = 0;
      this._syncViewButtons();
    }

    applyFitToData(tasks) {
      const fit = computeFitViewport(tasks);
      this._fitRestore = fit;
      this.applyFitViewport(fit);
    }

    applyHorizonToData(tasks) {
      const fit = computeHorizonViewport(tasks);
      this._fitRestore = fit;
      this.applyFitViewport(fit);
      this.view = 'horizon';
      this.fitToData = true;
      this._syncViewButtons();
    }

    _shiftFitWindow(direction) {
      if (!this._dataMinD || !this._dataMaxD) return;
      const shiftDays = Math.max(1, Math.ceil((this._dataMaxD - this._dataMinD) / 86400000 * 0.45));
      const delta = direction < 0 ? -shiftDays : shiftDays;
      this._dataMinD = addDays(this._dataMinD, delta);
      this._dataMaxD = addDays(this._dataMaxD, delta);
      this._fitShifted = !(
        this._dataMinD.getTime() === this._fitOriginMinD.getTime() &&
        this._dataMaxD.getTime() === this._fitOriginMaxD.getTime()
      );
    }

    setData(tasks, deadlines) {
      this.tasks = tasks || [];
      this.deadlines = deadlines || {};
      if (!this._fitLocked) {
        if (this.view === 'horizon') this.applyHorizonToData(this.tasks);
        else if (this.fitToData) this.applyFitToData(this.tasks);
      }
      if (this.enableStats) this.updateStats(this.tasks);
      this.renderWhenReady();
    }

    renderWhenReady() {
      const self = this;
      let attempts = 0;
      const tryRender = () => {
        if (!self.tasks.length) return;
        const barsEl = self.el('bars-scroll');
        if (!barsEl) return;
        attempts += 1;
        if (barsEl.clientWidth > 0 && barsEl.clientHeight > 0) {
          self._renderCurrent();
          self._observeLayout();
          return;
        }
        if (attempts < 80) requestAnimationFrame(tryRender);
        else self._renderCurrent();
      };
      requestAnimationFrame(tryRender);
    }

    _scheduleRender() {
      const self = this;
      clearTimeout(self._resizeTimer);
      self._resizeTimer = setTimeout(() => {
        if (self.tasks.length && !self._rendering) self._renderCurrent();
      }, 120);
    }

    _observeLayout() {
      if (this._layoutObs) return;
      const barsEl = this.el('bars-scroll');
      if (!barsEl || typeof ResizeObserver === 'undefined') return;
      const self = this;
      const layoutEl = self._layoutRoot(barsEl);
      this._layoutObs = new ResizeObserver(() => {
        const w = layoutEl.clientWidth;
        if (w <= 0) return;
        const last = self._lastLayoutW || 0;
        if (Math.abs(w - last) < 3) return;
        self._scheduleRender();
      });
      this._layoutObs.observe(layoutEl);
    }

    updateStats(tasks) {
      const totalEl = this.el('stat-total');
      if (!totalEl) return;
      const today = new Date(); today.setHours(0, 0, 0, 0);
      let total = 0, late = 0, wip = 0;
      tasks.forEach(t => {
        total++;
        if (taskIsWip(t)) wip++;
        if (taskIsLate(t, today)) late++;
      });
      totalEl.textContent = total;
      const lateEl = this.el('stat-late');
      const wipEl = this.el('stat-wip');
      if (lateEl) lateEl.textContent = late;
      if (wipEl) wipEl.textContent = wip;
      this._syncStatFilterUI();
    }

    showTip(taskData, clientX, clientY) {
      const tipEl = this.tipEl();
      if (!tipEl) return;
      tipEl.innerHTML = this.buildTipHtml(taskData);
      tipEl.style.display = 'block';
      tipEl.style.animation = 'none';
      tipEl.offsetHeight;
      tipEl.style.animation = '';
      const tipW = 308;
      const tipH = tipEl.offsetHeight || 280;
      let left = clientX + 16;
      if (left + tipW + 10 > window.innerWidth) left = clientX - tipW - 10;
      let top = clientY - 20;
      if (top + tipH + 10 > window.innerHeight) top = window.innerHeight - tipH - 16;
      tipEl.style.left = left + 'px';
      tipEl.style.top = top + 'px';
    }

    _compareTasksById() {
      const map = {};
      (this.compareTasks || []).forEach(t => {
        if (t && t.activity_id) map[t.activity_id] = t;
      });
      return map;
    }

    _plannedExtents(task) {
      if (!task) return { start: null, finish: null };
      const s = parseDate(task.start);
      const f = parseDate(task.finish);
      if (isNaN(s) || isNaN(f)) return { start: null, finish: null };
      return { start: s, finish: f };
    }

    _executionEndDate(row, planS, planF) {
      if (!row.actual_start) return null;
      const actS = parseDate(row.actual_start);
      if (row.actual_finish) return parseDate(row.actual_finish);
      return new Date(actS.getTime() + (planF - planS));
    }

    _badgeAnchorX(plannedEndX, executionEnd, dateToX, clipL, clipR) {
      // Badge always after the later of planned finish and actual/estimated finish.
      const TICK_W = 3;
      const endXraw = dateToX(executionEnd);
      let executionEndX = Math.min(Math.max(endXraw, clipL), clipR);
      if (endXraw >= clipL && endXraw - TICK_W <= clipR) {
        executionEndX = Math.max(
          executionEndX,
          Math.min(endXraw + Math.ceil(TICK_W / 2), clipR)
        );
      }
      return Math.max(plannedEndX, executionEndX);
    }

    _drawCompareBar(barsSvg, opts) {
      const {
        taskData, variant, barY, boxX1, boxW, colour, changed, theme,
      } = opts;
      const barH = opts.barH || BAR_H;
      const th = theme || chartTheme();
      if (boxW <= 0) return null;
      const isCurrent = variant === 'current';
      const rect = document.createElementNS(NS, 'rect');
      rect.setAttribute('x', boxX1);
      rect.setAttribute('y', barY);
      rect.setAttribute('width', boxW);
      rect.setAttribute('height', barH);
      rect.setAttribute('rx', '5');
      rect.setAttribute('fill', colour);
      if (isCurrent) {
        rect.setAttribute('fill-opacity', th.currentFillOpacity);
        rect.setAttribute('stroke', colour);
        rect.setAttribute('stroke-width', '1');
      } else {
        rect.setAttribute('fill-opacity', th.proposedFillOpacity);
        rect.setAttribute('stroke', changed ? th.changedStroke : colour);
        rect.setAttribute('stroke-width', changed ? '2' : '1.5');
        rect.setAttribute('stroke-dasharray', '4,3');
      }
      rect.setAttribute('stroke-opacity', th.barStrokeOpacity || '0.85');
      rect.setAttribute('class', isCurrent ? 'gantt-bar--current' : 'gantt-bar--proposed');
      rect.setAttribute('data-task', JSON.stringify({ ...taskData, variant }));
      rect.style.cursor = 'pointer';
      barsSvg.appendChild(rect);
      return {
        x1: boxX1,
        x2: boxX1 + boxW,
        midY: barY + barH / 2,
        barBotY: barY + barH,
        row: opts.row,
      };
    }

    render(tasks, view) {
      if (!tasks.length || this._rendering) return;
      const barsEl = this.el('bars-scroll');
      const axisSvg = this.el('axis-svg');
      const barsSvg = this.el('bars-svg');
      if (!barsEl || !axisSvg || !barsSvg) return;
      this._rendering = true;
      try {

      const theme = chartTheme();
      const gridLineColor = theme.gridLine;
      const compareTasksById = this.compareMode ? this._compareTasksById() : {};

      const groups = [];
      const gmap = {};
      tasks.forEach(t => {
        const key = t.commessa || t.job_id || 'unknown';
        if (!gmap[key]) {
          gmap[key] = { commessa: key, job_id: t.job_id || key, job_ids: [], tasks: [] };
          groups.push(gmap[key]);
        }
        gmap[key].tasks.push(t);
        if (t.job_id && !gmap[key].job_ids.includes(t.job_id)) gmap[key].job_ids.push(t.job_id);
      });
      // Top → bottom by earliest planned start (then by commessa name).
      groups.sort((a, b) => {
        const startA = earliestCommessaStartMs(a.tasks);
        const startB = earliestCommessaStartMs(b.tasks);
        if (startA !== startB) return startA - startB;
        return String(a.commessa || '').localeCompare(String(b.commessa || ''), 'it');
      });
      const colourOf = {};
      groups.forEach((g, i) => { colourOf[g.commessa] = theme.palette[i % theme.palette.length]; });

      const today = new Date(); today.setHours(0, 0, 0, 0);
      let minD, maxD;
      if (this.fitToData && this._dataMinD && this._dataMaxD) {
        minD = new Date(this._dataMinD);
        maxD = new Date(this._dataMaxD);
      } else if (view === 'week') {
        minD = addDays(startOfWeek(today), this.offset);
        maxD = addDays(minD, 7);
      } else if (view === 'month') {
        minD = addDays(floorMonth(today), this.offset);
        maxD = addMonths(minD, 1);
      } else {
        minD = addMonths(floorYear(today), this.offset);
        maxD = addYears(minD, 1);
      }

      this.updateNavState();
      const totalW = this._chartWidth(barsEl);
      const padL = CHART_PAD_LEFT;
      const padR = CHART_PAD_RIGHT;
      const plotW = Math.max(1, totalW - padL - padR);
      // Extra empty time before the first bar in fit/horizon (~1 cm at 96 dpi).
      if (this.fitToData && plotW > 0 && maxD > minD) {
        const edgePadPx = 38;
        const padMs = (edgePadPx / plotW) * (maxD.getTime() - minD.getTime());
        minD = new Date(minD.getTime() - padMs);
      }
      const adaptive = buildAdaptiveTicks(minD, maxD, plotW);
      const ticks = adaptive.ticks;
      const tickView = adaptive.labelMode;
      const stepDays = adaptive.stepDays;
      const pxPerMs = plotW / (maxD - minD);
      const dateToX = d => Math.round((d - minD) * pxPerMs) + padL;
      const clipL = padL;
      const clipR = totalW - padR;

      const groupRows = [];
      let yOffset = 8;
      const pendingGroups = [];
      groups.forEach(g => {
        const colour = colourOf[g.commessa];
        const visibleTasks = g.tasks.map(t => {
          const proposed = compareTasksById[t.activity_id];
          if (this.compareMode) {
            const starts = [];
            const finishes = [];
            [t, proposed].filter(Boolean).forEach(task => {
              const ext = this._plannedExtents(task);
              if (ext.start) starts.push(ext.start);
              if (ext.finish) finishes.push(ext.finish);
            });
            if (!starts.length || !finishes.length) return null;
            const visStart = new Date(Math.min(...starts.map(d => d.getTime())));
            const visEnd = new Date(Math.max(...finishes.map(d => d.getTime())));
            if (visStart >= maxD || visEnd <= minD) return null;
            return { ...t, colour, proposed };
          }
          const s = parseDate(t.start), f = parseDate(t.finish);
          if (isNaN(s) || isNaN(f)) return null;
          const durMs = f - s;
          const barStart = t.actual_start ? parseDate(t.actual_start) : s;
          const barEnd = t.actual_finish ? parseDate(t.actual_finish)
            : (t.actual_start ? new Date(barStart.getTime() + durMs) : f);
          const visStart = new Date(Math.min(s, barStart));
          const visEnd = new Date(Math.max(f, barEnd));
          if (visStart >= maxD || visEnd <= minD) return null;
          return { ...t, colour, x1: Math.max(dateToX(s), clipL), x2: Math.min(dateToX(f), clipR) };
        }).filter(Boolean);

        const allStarts = [];
        const allFinish = [];
        g.tasks.forEach(t => {
          const proposed = compareTasksById[t.activity_id];
          [t, proposed].filter(Boolean).forEach(task => {
            const ext = this._plannedExtents(task);
            if (ext.start) allStarts.push(ext.start);
            if (ext.finish) {
              if (this.compareMode) {
                allFinish.push(ext.finish);
              } else {
                const s = ext.start;
                const f = ext.finish;
                const durMs = f - s;
                const actS = task.actual_start ? parseDate(task.actual_start) : null;
                const actEnd = task.actual_finish ? parseDate(task.actual_finish)
                  : (actS ? new Date(actS.getTime() + durMs) : f);
                allFinish.push(new Date(Math.max(f.getTime(), actEnd.getTime())));
              }
            }
          });
        });
        if (!allStarts.length || !allFinish.length) return;

        const spanStart = new Date(Math.min(...allStarts.map(d => d.getTime())));
        const spanEnd = new Date(Math.max(...allFinish.map(d => d.getTime())));
        const summaryX1 = Math.max(dateToX(spanStart), clipL);
        const summaryX2 = Math.min(dateToX(spanEnd), clipR);
        const groupKey = g.commessa || g.job_id;
        const expanded = this._isGroupExpanded(groupKey);
        const layoutTasks = expanded ? visibleTasks : [];
        pendingGroups.push({
          colour, job_id: g.job_id, commessa: g.commessa, job_ids: g.job_ids,
          groupKey, expanded,
          layoutTasks, allTasks: g.tasks,
          summaryX1, summaryX2,
        });
      });

      pendingGroups.forEach(meta => {
        const headerY = yOffset;
        yOffset += HEADER_H;
        const taskEntries = meta.layoutTasks.map(row => {
          const y = yOffset;
          yOffset += ROW_H;
          return { ...row, y };
        });
        groupRows.push({
          colour: meta.colour,
          job_id: meta.job_id,
          commessa: meta.commessa,
          job_ids: meta.job_ids,
          groupKey: meta.groupKey,
          expanded: meta.expanded,
          headerY,
          summaryX1: meta.summaryX1,
          summaryX2: meta.summaryX2,
          tasks: taskEntries,
          allTasks: meta.allTasks,
        });
        yOffset += GROUP_GAP;
      });

      const contentH = yOffset + 8;
      const containerH = barsEl.clientHeight || 0;
      const totalH = Math.max(contentH, containerH);

      axisSvg.setAttribute('width', totalW);
      axisSvg.setAttribute('height', AXIS_H);
      axisSvg.innerHTML = '';
      ticks.forEach((tick) => {
        const x = dateToX(tick);
        if (x < clipL || x > clipR) return;
        const ln = document.createElementNS(NS, 'line');
        ln.setAttribute('x1', x); ln.setAttribute('y1', 0);
        ln.setAttribute('x2', x); ln.setAttribute('y2', AXIS_H);
        ln.setAttribute('stroke', gridLineColor);
        ln.setAttribute('stroke-width', '1');
        axisSvg.appendChild(ln);
        const txt = document.createElementNS(NS, 'text');
        txt.setAttribute('x', x + 6);
        txt.setAttribute('y', AXIS_H / 2 + 5);
        const isWeekend = stepDays === 1 && (tick.getDay() === 0 || tick.getDay() === 6);
        txt.setAttribute('fill', isWeekend ? theme.axisTextWeekend : theme.axisText);
        txt.setAttribute('font-size', '11');
        txt.setAttribute('font-family', '-apple-system,BlinkMacSystemFont,sans-serif');
        txt.textContent = fmtLabel(tick, tickView);
        axisSvg.appendChild(txt);
      });

      barsSvg.setAttribute('width', totalW);
      barsSvg.setAttribute('height', totalH);
      barsSvg.innerHTML = '';

      if (stepDays === 1) {
        let cur = new Date(minD);
        cur.setHours(0, 0, 0, 0);
        while (cur < maxD) {
          const dow = cur.getDay();
          if (dow === 0 || dow === 6) {
            const x = dateToX(cur);
            const x2 = dateToX(addDays(cur, 1));
            if (x < clipR && x2 > clipL) {
              const wk = document.createElementNS(NS, 'rect');
              wk.setAttribute('x', Math.max(x, clipL));
              wk.setAttribute('y', 0);
              wk.setAttribute('width', Math.min(x2, clipR) - Math.max(x, clipL));
              wk.setAttribute('height', totalH);
              wk.setAttribute('fill', theme.weekendFill);
              wk.setAttribute('opacity', theme.weekendOpacity);
              barsSvg.appendChild(wk);
            }
          }
          cur = addDays(cur, 1);
        }
      }

      ticks.forEach(tick => {
        const x = dateToX(tick);
        if (x < clipL || x > clipR) return;
        const ln = document.createElementNS(NS, 'line');
        ln.setAttribute('x1', x); ln.setAttribute('y1', 0);
        ln.setAttribute('x2', x); ln.setAttribute('y2', totalH);
        ln.setAttribute('stroke', gridLineColor);
        ln.setAttribute('stroke-width', '1');
        barsSvg.appendChild(ln);
      });

      if (today >= minD && today < maxD) {
        const x = dateToX(today);
        const pxPerDay = plotW / ((maxD - minD) / 86400000);
        const strip = document.createElementNS(NS, 'rect');
        strip.setAttribute('x', x);
        strip.setAttribute('y', 0);
        strip.setAttribute('width', Math.max(pxPerDay, 3));
        strip.setAttribute('height', totalH);
        strip.setAttribute('fill', theme.todayFill);
        strip.setAttribute('opacity', theme.todayOpacity);
        barsSvg.appendChild(strip);
        const todayLine = document.createElementNS(NS, 'line');
        todayLine.setAttribute('x1', x);
        todayLine.setAttribute('y1', 0);
        todayLine.setAttribute('x2', x);
        todayLine.setAttribute('y2', totalH);
        todayLine.setAttribute('stroke', theme.todayLineColor);
        todayLine.setAttribute('stroke-width', theme.todayLineWidth);
        todayLine.setAttribute('opacity', theme.todayLineOpacity);
        barsSvg.appendChild(todayLine);
        const axisTodayLine = document.createElementNS(NS, 'line');
        axisTodayLine.setAttribute('x1', x);
        axisTodayLine.setAttribute('y1', 0);
        axisTodayLine.setAttribute('x2', x);
        axisTodayLine.setAttribute('y2', AXIS_H);
        axisTodayLine.setAttribute('stroke', theme.todayLineColor);
        axisTodayLine.setAttribute('stroke-width', theme.todayLineWidth);
        axisTodayLine.setAttribute('opacity', theme.todayLineOpacity);
        axisSvg.appendChild(axisTodayLine);
      }

      const self = this;
      const taskPosMap = {};

      groupRows.forEach((g, gi) => {
        if (g.summaryX2 > g.summaryX1) {
          const SUMBAR_H = 6;
          const sumY = g.headerY + HEADER_H - SUMBAR_H - 4;
          const sumW = Math.max(g.summaryX2 - g.summaryX1, 20);
          const toggleKey = g.groupKey || g.commessa || g.job_id;
          const lbl = document.createElementNS(NS, 'text');
          lbl.setAttribute('x', g.summaryX1 + 6);
          lbl.setAttribute('y', sumY - 5);
          lbl.setAttribute('fill', g.colour);
          lbl.setAttribute('font-size', '12');
          lbl.setAttribute('font-weight', '700');
          lbl.setAttribute('font-family', '-apple-system,BlinkMacSystemFont,sans-serif');
          if (self.collapsibleGroups) {
            lbl.setAttribute('cursor', 'pointer');
            lbl.setAttribute('data-toggle-group', toggleKey);
          }
          const nameSpan = document.createElementNS(NS, 'tspan');
          nameSpan.textContent = g.commessa || g.job_id;
          lbl.appendChild(nameSpan);
          // Home (collapsible) shows count / progress / status; compare/opt charts keep the name only.
          if (self.collapsibleGroups) {
            const dlForStats = (g.job_ids || [g.job_id]).map(jid => self.deadlines[jid]).filter(Boolean).sort().pop();
            const stats = summarizeCommessaTasks(g.allTasks || [], dlForStats);
            const attLabel = stats.total === 1 ? '1 att.' : (stats.total + ' att.');
            const metaSpan = document.createElementNS(NS, 'tspan');
            metaSpan.setAttribute('fill', theme.metaText);
            metaSpan.setAttribute('font-size', '11');
            metaSpan.setAttribute('font-weight', '650');
            metaSpan.setAttribute('letter-spacing', '0.01em');
            metaSpan.textContent = '  ·  ' + attLabel + '  ·  ' + stats.pct + '%';
            lbl.appendChild(metaSpan);
            const statusSpan = document.createElementNS(NS, 'tspan');
            statusSpan.setAttribute(
              'fill',
              (theme.statusColors && theme.statusColors[stats.status]) || theme.metaText
            );
            statusSpan.setAttribute('font-size', '11');
            statusSpan.setAttribute('font-weight', '650');
            statusSpan.setAttribute('letter-spacing', '0.01em');
            statusSpan.textContent = '  ·  ' + stats.status;
            lbl.appendChild(statusSpan);
          }
          barsSvg.appendChild(lbl);

          const sumBar = document.createElementNS(NS, 'rect');
          sumBar.setAttribute('x', g.summaryX1);
          sumBar.setAttribute('y', sumY);
          sumBar.setAttribute('width', sumW);
          sumBar.setAttribute('height', SUMBAR_H);
          sumBar.setAttribute('rx', SUMBAR_H / 2);
          sumBar.setAttribute('fill', g.colour);
          sumBar.setAttribute('opacity', '0.45');
          if (self.collapsibleGroups) {
            sumBar.setAttribute('cursor', 'pointer');
            sumBar.setAttribute('data-toggle-group', toggleKey);
          }
          barsSvg.appendChild(sumBar);
        }

        const dlStr = (g.job_ids || [g.job_id]).map(jid => self.deadlines[jid]).filter(Boolean).sort().pop();
        const showDeadline = !(self.collapsibleGroups && !g.expanded);
        if (showDeadline && dlStr) {
          const dlDate = parseDate(dlStr);
          if (dlDate >= minD && dlDate <= maxD) {
            const dlX = dateToX(dlDate);
            const dlColor = theme.deadlineColor;
            const dlFmt = dlDate.toLocaleDateString('it-IT', {
              day: '2-digit', month: '2-digit', year: '2-digit',
            });
            const ownH = HEADER_H + (g.tasks.length ? g.tasks.length * ROW_H : 0);
            const dlTop = g.headerY + HEADER_H;
            const dlBot = g.headerY + ownH;

            if (dlBot > dlTop) {
              const dlLine = document.createElementNS(NS, 'line');
              dlLine.setAttribute('x1', dlX);
              dlLine.setAttribute('y1', dlTop);
              dlLine.setAttribute('x2', dlX);
              dlLine.setAttribute('y2', dlBot);
              dlLine.setAttribute('stroke', dlColor);
              dlLine.setAttribute('stroke-width', '1.5');
              dlLine.setAttribute('stroke-dasharray', '4,3');
              dlLine.setAttribute('opacity', '0.80');
              barsSvg.appendChild(dlLine);

              const tickTop = document.createElementNS(NS, 'line');
              tickTop.setAttribute('x1', dlX - 5);
              tickTop.setAttribute('y1', dlTop);
              tickTop.setAttribute('x2', dlX + 5);
              tickTop.setAttribute('y2', dlTop);
              tickTop.setAttribute('stroke', dlColor);
              tickTop.setAttribute('stroke-width', '2');
              tickTop.setAttribute('opacity', '0.85');
              barsSvg.appendChild(tickTop);
            }

            const DIA = 4;
            const markerY = dlBot;
            const poly = document.createElementNS(NS, 'polygon');
            poly.setAttribute('points',
              dlX + ',' + markerY + ' ' +
              (dlX + DIA) + ',' + (markerY + DIA) + ' ' +
              dlX + ',' + (markerY + DIA * 2) + ' ' +
              (dlX - DIA) + ',' + (markerY + DIA));
            poly.setAttribute('fill', dlColor);
            poly.setAttribute('opacity', '0.9');
            barsSvg.appendChild(poly);

            const dlLbl = document.createElementNS(NS, 'text');
            dlLbl.setAttribute('x', dlX + DIA + 4);
            dlLbl.setAttribute('y', markerY + DIA + 3);
            dlLbl.setAttribute('fill', dlColor);
            dlLbl.setAttribute('font-size', '9');
            dlLbl.setAttribute('font-weight', '700');
            dlLbl.setAttribute('font-family', '-apple-system,BlinkMacSystemFont,sans-serif');
            dlLbl.setAttribute('letter-spacing', '0.04em');
            dlLbl.setAttribute('opacity', '0.9');
            dlLbl.textContent = dlFmt;
            barsSvg.appendChild(dlLbl);
          }
        }

        g.tasks.forEach((row, ti) => {
          const C = colourOf[row.commessa] || 'hsl(220,12%,52%)';
          const proposed = row.proposed || compareTasksById[row.activity_id];

          if (self.compareMode) {
            const barY = row.y + Math.floor((ROW_H - BAR_H) / 2);
            let labelX = null;
            const posKeys = [
              row.activity_id,
              (row.commessa || '') + (row.item ? '-' + row.item : '') + (row.activity ? '-' + row.activity : ''),
              (row.commessa || '') + (row.item ? '_' + row.item : '') + (row.activity ? '_' + row.activity : ''),
            ];
            const registerPos = (pos) => {
              if (!pos) return;
              posKeys.forEach(k => { if (k) taskPosMap[k] = pos; });
            };

            const currentExt = self._plannedExtents(row);
            if (currentExt.start && currentExt.finish) {
              const boxX1 = Math.max(dateToX(currentExt.start), clipL);
              const boxX2 = Math.min(dateToX(currentExt.finish), clipR);
              const boxW = Math.max(boxX2 - boxX1, 4);
              labelX = boxX1;
              registerPos(self._drawCompareBar(barsSvg, {
                taskData: row,
                variant: 'current',
                barY,
                boxX1,
                boxW,
                colour: C,
                changed: false,
                row,
                theme,
              }));
            }

            if (proposed) {
              const proposedExt = self._plannedExtents(proposed);
              if (proposedExt.start && proposedExt.finish) {
                const boxX1 = Math.max(dateToX(proposedExt.start), clipL);
                const boxX2 = Math.min(dateToX(proposedExt.finish), clipR);
                const boxW = Math.max(boxX2 - boxX1, 4);
                if (labelX === null) labelX = boxX1;
                const changed = !!(self.highlightChanges && self.highlightChanges[row.activity_id]);
                registerPos(self._drawCompareBar(barsSvg, {
                  taskData: proposed,
                  variant: 'proposed',
                  barY,
                  boxX1,
                  boxW,
                  colour: C,
                  changed,
                  row,
                  theme,
                }));
              }
            }

            if (labelX !== null) {
              let labelW = 40;
              if (currentExt.start && currentExt.finish) {
                labelW = Math.max(
                  Math.min(dateToX(currentExt.finish), clipR) - Math.max(dateToX(currentExt.start), clipL),
                  4
                );
              } else if (proposed) {
                const pe = self._plannedExtents(proposed);
                if (pe.start && pe.finish) {
                  labelW = Math.max(
                    Math.min(dateToX(pe.finish), clipR) - Math.max(dateToX(pe.start), clipL),
                    4
                  );
                }
              }
              const txt = document.createElementNS(NS, 'text');
              txt.setAttribute('x', labelX + (labelW < 40 ? 2 : 10));
              txt.setAttribute('y', barY - 4);
              txt.setAttribute('fill', C);
              txt.setAttribute('opacity', '0.90');
              txt.setAttribute('font-size', '10');
              txt.setAttribute('font-weight', '700');
              txt.setAttribute('font-family', '-apple-system,BlinkMacSystemFont,sans-serif');
              txt.setAttribute('pointer-events', 'none');
              txt.textContent = (row.item ? row.item + ' · ' : '') + (row.activity || '');
              barsSvg.appendChild(txt);
            }
            return;
          }

          const barY = row.y + Math.floor((ROW_H - BAR_H) / 2);
          const midY = barY + BAR_H / 2;
          const planS = parseDate(row.start);
          const planF = parseDate(row.finish);
          if (isNaN(planS) || isNaN(planF)) return;
          const boxX1 = Math.max(dateToX(planS), clipL);
          const boxX2 = Math.min(dateToX(planF), clipR);
          const boxW = Math.max(boxX2 - boxX1, 4);
          const changed = self.highlightChanges && self.highlightChanges[row.activity_id];
          const RED = 'hsl(4,90%,62%)';

          const taskPos = { x1: boxX1, x2: boxX2, midY, barBotY: barY + BAR_H, row };
          const posKeys = [
            row.activity_id,
            (row.commessa || '') + (row.item ? '-' + row.item : '') + (row.activity ? '-' + row.activity : ''),
            (row.commessa || '') + (row.item ? '_' + row.item : '') + (row.activity ? '_' + row.activity : ''),
          ];
          posKeys.forEach(k => { if (k) taskPosMap[k] = taskPos; });

          const isStarted = !!(row.actual_start);
          const isCompleted = !!(row.actual_start && row.actual_finish);
          const isOverdue = !isStarted && today >= planS;
          const isLate = !!(row.actual_start && parseDate(row.actual_start) > planS);
          // Never started and already past the planned finish: the plan is
          // stale. Mirrors _compute_activity_status in ui/views.py.
          const isExpired = !isStarted && !row.actual_finish && today > planF;
          const boxStrokeW = (!self.simplified && isOverdue) ? '1.5' : (changed ? '2' : '1');
          const boxStrokeOp = theme.barStrokeOpacity;

          const rect = document.createElementNS(NS, 'rect');
          rect.setAttribute('x', boxX1);
          rect.setAttribute('y', barY);
          rect.setAttribute('width', boxW);
          rect.setAttribute('height', BAR_H);
          rect.setAttribute('rx', '5');
          rect.setAttribute('fill', C);
          rect.setAttribute('fill-opacity', changed ? theme.barChangedFillOpacity : theme.barFillOpacity);
          rect.setAttribute('stroke', changed ? theme.changedStroke : C);
          rect.setAttribute('stroke-width', boxStrokeW);
          rect.setAttribute('stroke-opacity', boxStrokeOp);
          rect.setAttribute('data-task', JSON.stringify(row));
          rect.style.cursor = 'pointer';
          barsSvg.appendChild(rect);

          const plannedEndX = boxX1 + boxW;
          let badgeAnchorX = plannedEndX;
          if (!self.simplified && isStarted) {
            const actEnd = self._executionEndDate(row, planS, planF);
            badgeAnchorX = self._badgeAnchorX(plannedEndX, actEnd, dateToX, clipL, clipR);
            const actS = parseDate(row.actual_start);
            const TAIL_H = 3, TICK_H = BAR_H + 6, TICK_W = 3;
            const actX1raw = dateToX(actS);
            const actX2raw = dateToX(actEnd);
            const tx1 = Math.max(actX1raw, clipL);
            const tx2 = Math.min(actX2raw, clipR);
            const tickY = midY - TICK_H / 2;
            const drawActualPart = (x, y, w, h, opacity) => {
              if (w <= 0) return;
              const part = document.createElementNS(NS, 'rect');
              part.setAttribute('x', x);
              part.setAttribute('y', y);
              part.setAttribute('width', w);
              part.setAttribute('height', h);
              part.setAttribute('rx', '1.5');
              part.setAttribute('fill', C);
              part.setAttribute('opacity', opacity);
              part.setAttribute('pointer-events', 'none');
              barsSvg.appendChild(part);
            };
            const drawTickAt = (centerX) => {
              const x1 = centerX - Math.floor(TICK_W / 2);
              const lx = Math.max(x1, clipL);
              const rx = Math.min(x1 + TICK_W, clipR);
              if (rx > lx) drawActualPart(lx, tickY, rx - lx, TICK_H, '0.95');
            };
            if (tx2 > tx1) drawActualPart(tx1, midY - TAIL_H / 2, tx2 - tx1, TAIL_H, '0.90');
            if (actX1raw + TICK_W >= clipL && actX1raw <= clipR) drawTickAt(actX1raw);
            if (actX2raw >= clipL && actX2raw - TICK_W <= clipR) drawTickAt(actX2raw);
          }

          if (boxW > 0) {
            const txt = document.createElementNS(NS, 'text');
            txt.setAttribute('x', boxX1 + (boxW < 40 ? 2 : 10));
            txt.setAttribute('y', barY - 4);
            txt.setAttribute('fill', C);
            txt.setAttribute('opacity', '0.90');
            txt.setAttribute('font-size', '10');
            txt.setAttribute('font-weight', '700');
            txt.setAttribute('font-family', '-apple-system,BlinkMacSystemFont,sans-serif');
            txt.setAttribute('pointer-events', 'none');
            txt.textContent = (row.item ? row.item + ' · ' : '') + (row.activity || '');
            barsSvg.appendChild(txt);
          }

          if (!self.simplified) {
            const BADGE_H = 16, BADGE_RX = 5, BADGE_PAD = 10, GAP = 6, BADGE_FONT = 9;
            const SLATE = 'hsl(220,14%,38%)';
            const charW = BADGE_FONT * 0.60;
            const badges = [];
            if (isCompleted) badges.push({ text: '✓ COMPLETATA', color: 'hsl(142,55%,40%)' });
            else {
              if (isLate || isOverdue) badges.push({ text: 'IN RITARDO', color: RED });
              if (isStarted) badges.push({ text: 'IN LAVORAZIONE', color: SLATE });
              else if (isExpired) badges.push({ text: 'NON INIZIATA · SCADUTA', color: 'hsl(18,90%,48%)' });
              else badges.push({ text: isOverdue ? 'DA INIZIARE' : 'PIANIFICATA', color: SLATE });
            }
            let bx = Math.max(badgeAnchorX, clipL) + BADGE_PAD;
            badges.forEach(({ text, color, plain, keep }) => {
              const bw = Math.ceil(text.length * charW) + (plain ? 0 : 14);
              if (!keep && bx + bw > totalW + 100) return;
              const by = midY - BADGE_H / 2;
              if (!plain) {
                const bg = document.createElementNS(NS, 'rect');
                bg.setAttribute('x', bx); bg.setAttribute('y', by);
                bg.setAttribute('width', bw); bg.setAttribute('height', BADGE_H);
                bg.setAttribute('rx', BADGE_RX); bg.setAttribute('fill', color);
                bg.setAttribute('opacity', '0.92'); bg.setAttribute('pointer-events', 'none');
                barsSvg.appendChild(bg);
              }
              const bt = document.createElementNS(NS, 'text');
              bt.setAttribute('x', plain ? bx : bx + bw / 2);
              bt.setAttribute('y', by + BADGE_H / 2 + 3.5);
              bt.setAttribute('fill', plain ? 'hsl(220,10%,62%)' : 'white');
              bt.setAttribute('font-size', BADGE_FONT);
              bt.setAttribute('font-weight', plain ? '500' : '700');
              bt.setAttribute('text-anchor', plain ? 'start' : 'middle');
              bt.setAttribute('pointer-events', 'none');
              bt.textContent = text;
              barsSvg.appendChild(bt);
              bx += bw + GAP;
            });
            if (self.enableWarningNav && !isCompleted && row.warning_count > 0) {
              // Always surface unresolved problems: the warning badge is drawn
              // even when the bar sits near the right edge (it stays within the
              // scrollable timeline). Clicking it opens the reference modal.
              const by = midY - 9;
              bx += appendProblemsBadge(barsSvg, bx, by, row.warning_count, row.orm_id) + GAP;
            }
          }
        });
      });

      // Dependency arrows (predecessors)
      const drawnPredRows = new Set();
      Object.values(taskPosMap).forEach(pos => {
        const row = pos.row;
        const rowKey = row.activity_id || (row.commessa + '|' + row.item + '|' + row.activity);
        if (drawnPredRows.has(rowKey)) return;
        drawnPredRows.add(rowKey);
        if (!row.predecessors) return;
        row.predecessors.split(/[;,]/).forEach(predRef => {
          const predKey = predRef.trim();
          if (!predKey) return;
          const predPos = taskPosMap[predKey];
          if (!predPos || predPos.x1 === null || pos.x1 === null) return;
          const fx = predPos.x1 + (predPos.x2 - predPos.x1) / 2;
          const fy = predPos.barBotY;
          const tx = pos.x1;
          const ty = pos.midY;
          const R = Math.min(8, Math.abs(tx - fx) * 0.5, Math.abs(ty - fy) * 0.5);
          const signY = ty >= fy ? 1 : -1;
          const signX = tx >= fx ? 1 : -1;
          const d = [
            'M ' + fx + ' ' + fy,
            'L ' + fx + ' ' + (ty - signY * R),
            'Q ' + fx + ' ' + ty + ' ' + (fx + signX * R) + ' ' + ty,
            'L ' + tx + ' ' + ty,
          ].join(' ');
          const path = document.createElementNS(NS, 'path');
          path.setAttribute('d', d);
          path.setAttribute('fill', 'none');
          path.setAttribute('stroke', colourOf[row.commessa] || 'hsl(0,0%,100%)');
          path.setAttribute('stroke-width', '1.5');
          path.setAttribute('stroke-dasharray', '5,4');
          path.setAttribute('stroke-linecap', 'round');
          path.setAttribute('stroke-linejoin', 'round');
          path.setAttribute('opacity', '0.55');
          path.setAttribute('pointer-events', 'none');
          barsSvg.appendChild(path);
        });
      });

      const axisScroll = this.el('axis-scroll');
      if (axisScroll) axisScroll.style.overflowX = 'hidden';

      if (!this._clickBound) {
        this._clickBound = true;
        barsSvg.addEventListener('click', e => {
          if (self.enableDeadlineEditor) {
            const dlEl = e.target.closest('[data-dl-job-id]');
            if (dlEl) { self.openDeadlineEditor(dlEl.dataset.dlJobId, e.clientX, e.clientY); return; }
          }
          if (self.collapsibleGroups) {
            const toggleEl = e.target.closest('[data-toggle-group]');
            if (toggleEl) {
              self.toggleGroup(toggleEl.getAttribute('data-toggle-group'));
              return;
            }
          }
          if (self.enableWarningNav) {
            const warnEl = e.target.closest('[data-warn-ormid]');
            if (warnEl) { window.location.href = '/pianificazione/?focus=' + warnEl.dataset.warnOrmid; return; }
          }
          const taskEl = e.target.closest('[data-task]');
          if (taskEl) {
            try { self.showTip(JSON.parse(taskEl.dataset.task), e.clientX, e.clientY); } catch (err) {}
          }
        });
      }

      this._syncExpandToggleBtn();
      this._lastLayoutW = totalW;
      } finally {
        this._rendering = false;
      }
    }

    openDeadlineEditor(jobId, cx, cy) {
      // Read-only accounts may not change delivery dates (see ui.permissions).
      if (window.CAN_WRITE === false) return;
      const pop = this.el('dl-popover');
      if (!pop) return;
      const nameEl = this.el('dl-job-id-name');
      const inputEl = this.el('dl-input');
      if (nameEl) nameEl.textContent = jobId;
      if (inputEl) inputEl.value = this.deadlines[jobId] || '';
      pop.dataset.jobId = jobId;
      pop.style.left = Math.min(cx + 8, window.innerWidth - 252) + 'px';
      pop.style.top = Math.min(cy + 8, window.innerHeight - 132) + 'px';
      pop.removeAttribute('hidden');
      if (inputEl) inputEl.focus();
    }

    closeDeadlineEditor() {
      const pop = this.el('dl-popover');
      if (pop) pop.setAttribute('hidden', '');
    }

    saveDeadline() {
      const pop = this.el('dl-popover');
      if (!pop) return;
      const jobId = pop.dataset.jobId;
      const inputEl = this.el('dl-input');
      const deadline = inputEl ? (inputEl.value || null) : null;
      const self = this;
      fetch('/api/commesse/deadline/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify({ job_id: jobId, deadline }),
      }).then(r => r.json()).then(() => {
        if (deadline) self.deadlines[jobId] = deadline;
        else delete self.deadlines[jobId];
        self.closeDeadlineEditor();
        self._renderCurrent();
      });
    }

    bindControls() {
      if (this._bound) return;
      this._bound = true;
      bindGlobalTipDismiss();
      const self = this;
      const panel = this.prefix ? document.getElementById(this.prefix + '-panel') : null;
      const tbtnRoot = panel || document;
      const tbtnSelector = this.prefix ? '.tbtn' : '#toggle-group .tbtn';

      tbtnRoot.querySelectorAll(tbtnSelector).forEach(btn => {
        btn.addEventListener('click', () => {
          tbtnRoot.querySelectorAll('.tbtn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          self.view = btn.dataset.view;
          self.offset = 0;
          if (btn.dataset.view === 'horizon') {
            self.applyHorizonToData(self.tasks);
          } else {
            self.fitToData = false;
            self._dataMinD = null;
            self._dataMaxD = null;
            self._fitRestore = null;
          }
          self._renderCurrent();
        });
      });

      const prev = this.el('nav-prev');
      const next = this.el('nav-next');
      const todayBtn = this.el('nav-today');
      if (prev) prev.addEventListener('click', () => {
        if (self.fitToData && self._dataMinD) {
          self._shiftFitWindow(-1);
        } else {
          self.offset -= (self.view === 'year') ? 1 : self.offsetStep(self.view);
        }
        self._renderCurrent();
      });
      if (next) next.addEventListener('click', () => {
        if (self.fitToData && self._dataMinD) {
          self._shiftFitWindow(1);
        } else {
          self.offset += (self.view === 'year') ? 1 : self.offsetStep(self.view);
        }
        self._renderCurrent();
      });
      if (todayBtn) todayBtn.addEventListener('click', () => {
        if (self.view === 'horizon' || self.fitToData) {
          if (self.view === 'horizon') self.applyHorizonToData(self.tasks);
          else if (self._fitRestore) self.applyFitViewport(self._fitRestore);
          else self.applyFitToData(self.tasks);
          self._renderCurrent();
          return;
        }
        if (self.offset === 0) return;
        self.offset = 0;
        self._renderCurrent();
      });

      const expandToggle = this.el('nav-expand-toggle');
      if (expandToggle && this.collapsibleGroups) {
        expandToggle.addEventListener('click', () => {
          if (expandToggle.dataset.mode === 'collapse') self.collapseAllGroups();
          else self.expandAllGroups();
        });
        this._syncExpandToggleBtn();
      }

      const barsScroll = this.el('bars-scroll');
      const axisScroll = this.el('axis-scroll');
      if (barsScroll && axisScroll) {
        barsScroll.addEventListener('scroll', () => {
          axisScroll.scrollLeft = barsScroll.scrollLeft;
        }, { passive: true });
      }

      window.addEventListener('resize', () => self._scheduleRender());
    }

    bindIndexPage() {
      this.bindControls();
      const self = this;
      document.querySelectorAll('#stats-row .stat-card[data-stat-filter]').forEach(card => {
        card.addEventListener('click', () => {
          self.setStatFilter(card.getAttribute('data-stat-filter'));
        });
        card.addEventListener('keydown', e => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            self.setStatFilter(card.getAttribute('data-stat-filter'));
          }
        });
      });
      this._syncStatFilterUI();
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
          self.closeDeadlineEditor();
        }
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (e.key === 'ArrowLeft') {
          self.offset -= (self.view === 'year') ? 1 : self.offsetStep(self.view);
          self._renderCurrent();
        } else if (e.key === 'ArrowRight') {
          self.offset += (self.view === 'year') ? 1 : self.offsetStep(self.view);
          self._renderCurrent();
        }
      });
      const saveBtn = document.getElementById('dl-save-btn');
      if (saveBtn) saveBtn.addEventListener('click', () => self.saveDeadline());
      const closeBtn = document.getElementById('dl-close-btn');
      if (closeBtn) closeBtn.addEventListener('click', () => self.closeDeadlineEditor());
    }

    loadFromUrl(url) {
      const self = this;
      return fetch(url, { cache: 'no-store' })
        .then(r => r.json())
        .then(data => {
          self.setData(data.tasks || [], data.deadlines || {});
        });
    }
  }

  GanttChart.computeFitViewport = computeFitViewport;
  GanttChart.computeHorizonViewport = computeHorizonViewport;
  GanttChart.summarizeCommessaTasks = summarizeCommessaTasks;
  global.GanttChart = GanttChart;
})(typeof window !== 'undefined' ? window : this);
