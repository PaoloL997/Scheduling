/* Shared SVG Gantt chart renderer. */
(function (global) {
  'use strict';

  const AXIS_H = 44;
  const NS = 'http://www.w3.org/2000/svg';
  const ROW_H = 38;
  const HEADER_H = 36;
  const BAR_H = 22;
  const GROUP_GAP = 16;
  const PALETTE = [
    'hsl(211,90%,60%)', 'hsl(38,95%,58%)', 'hsl(160,55%,50%)', 'hsl(280,60%,68%)',
    'hsl(0,75%,62%)', 'hsl(190,65%,52%)', 'hsl(48,90%,55%)', 'hsl(320,60%,65%)',
    'hsl(100,50%,55%)', 'hsl(230,75%,65%)', 'hsl(15,80%,60%)', 'hsl(170,55%,50%)',
  ];
  const PALETTE_LIGHT = [
    'hsl(211,84%,40%)', 'hsl(38,90%,38%)', 'hsl(160,72%,34%)', 'hsl(280,76%,42%)',
    'hsl(0,80%,40%)', 'hsl(190,78%,34%)', 'hsl(48,92%,36%)', 'hsl(320,76%,42%)',
    'hsl(100,68%,34%)', 'hsl(230,82%,42%)', 'hsl(15,88%,40%)', 'hsl(170,70%,32%)',
  ];

  function chartTheme(isLight) {
    return {
      palette: isLight ? PALETTE_LIGHT : PALETTE,
      gridLine: isLight ? 'hsl(220,12%,84%)' : 'hsl(220,10%,15%)',
      axisText: isLight ? 'hsl(220,10%,42%)' : 'hsl(220,10%,46%)',
      axisTextWeekend: isLight ? 'hsl(220,8%,52%)' : 'hsl(220,10%,32%)',
      weekendFill: isLight ? 'hsl(220,14%,88%)' : 'hsl(220,30%,80%)',
      weekendOpacity: isLight ? '0.45' : '0.06',
      todayFill: isLight ? 'hsl(0,82%,48%)' : 'hsl(0,80%,50%)',
      todayOpacity: isLight ? '0.22' : '0.10',
      todayLineColor: isLight ? 'hsl(0,88%,42%)' : 'hsl(0,90%,58%)',
      todayLineOpacity: isLight ? '0.85' : '0.60',
      todayLineWidth: isLight ? '2' : '1.5',
      deadlineColor: isLight ? 'hsl(0,55%,44%)' : 'hsl(0,80%,60%)',
      changedStroke: isLight ? 'hsl(158,60%,30%)' : 'hsl(160,70%,50%)',
      barFillOpacity: isLight ? '0.44' : '0.08',
      barChangedFillOpacity: isLight ? '0.58' : '0.18',
      barStrokeOpacity: isLight ? '1' : '0.70',
      currentFillOpacity: isLight ? '0.72' : '0.50',
      proposedFillOpacity: isLight ? '0.42' : '0.06',
    };
  }

  const _activeCharts = new Set();
  let _themeWatcherBound = false;
  let _lastThemeLight = null;

  function bindThemeWatcher() {
    if (_themeWatcherBound || typeof MutationObserver === 'undefined') return;
    if (typeof document === 'undefined' || !document.body) return;
    _themeWatcherBound = true;
    _lastThemeLight = document.body.classList.contains('light');
    const obs = new MutationObserver(() => {
      const isLight = document.body.classList.contains('light');
      if (isLight === _lastThemeLight) return;
      _lastThemeLight = isLight;
      _activeCharts.forEach(chart => {
        if (chart.tasks && chart.tasks.length) chart.renderWhenReady();
      });
    });
    obs.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  }

  let _globalTipDismissBound = false;
  function bindGlobalTipDismiss() {
    if (_globalTipDismissBound) return;
    _globalTipDismissBound = true;
    document.addEventListener('click', e => {
      document.querySelectorAll('#gantt-tip, #ott-gantt-tip, [id$="-gantt-tip"]').forEach(tip => {
        if (tip.style.display === 'none') return;
        if (!tip.contains(e.target) && !e.target.closest('[data-task]')) {
          tip.style.display = 'none';
        }
      });
    });
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      document.querySelectorAll('#gantt-tip, #ott-gantt-tip, [id$="-gantt-tip"]').forEach(tip => {
        tip.style.display = 'none';
      });
    });
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
    if (spanDays <= 14) return 'week';
    if (spanDays <= 75) return 'month';
    return 'year';
  }

  function tickViewForWindowDays(windowDays) {
    if (windowDays <= 14) return 'week';
    if (windowDays <= 75) return 'month';
    return 'year';
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
    const spanDays = Math.max(1, Math.ceil((max - min) / 86400000) + 1);
    const padDays = Math.min(7, Math.max(2, Math.ceil(spanDays * 0.05)));
    const minD = addDays(min, -padDays);
    const maxD = addDays(max, padDays);
    const windowDays = (maxD - minD) / 86400000;
    return {
      minD,
      maxD,
      spanDays,
      view: suggestViewForSpanDays(spanDays),
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
      this.highlightChanges = options.highlightChanges || null;
      this.compareMode = !!options.compareMode;
      this.compareTasks = options.compareTasks || [];
      this._dataMinD = null;
      this._dataMaxD = null;
      this._bound = false;
      _activeCharts.add(this);
      bindThemeWatcher();
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
      this.applyFitViewport(computeFitViewport(tasks));
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
      if (this.fitToData && !this._fitLocked) this.applyFitToData(this.tasks);
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
          self.render(self.tasks, self.view);
          self._observeLayout();
          return;
        }
        if (attempts < 80) requestAnimationFrame(tryRender);
        else self.render(self.tasks, self.view);
      };
      requestAnimationFrame(tryRender);
    }

    _scheduleRender() {
      const self = this;
      clearTimeout(self._resizeTimer);
      self._resizeTimer = setTimeout(() => {
        if (self.tasks.length && !self._rendering) self.render(self.tasks, self.view);
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
        const actS = t.actual_start ? new Date(t.actual_start + 'T00:00:00') : null;
        const actF = t.actual_finish ? new Date(t.actual_finish + 'T00:00:00') : null;
        const planS = t.start ? new Date(t.start + 'T00:00:00') : null;
        if (actS && actF) return;
        if (actS && !actF) wip++;
        const isLate = !!(actS && planS && actS > planS);
        const isOverdue = !!((!actS) && planS && today > planS);
        if (isLate || isOverdue) late++;
      });
      totalEl.textContent = total;
      const lateEl = this.el('stat-late');
      const wipEl = this.el('stat-wip');
      if (lateEl) lateEl.textContent = late;
      if (wipEl) wipEl.textContent = wip;
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

    _badgeAnchorX(plannedEndX, executionEnd, dateToX, totalW) {
      // Badge always after the later of planned finish and actual/estimated finish.
      const TICK_W = 3;
      const endXraw = dateToX(executionEnd);
      let executionEndX = Math.min(Math.max(endXraw, 0), totalW);
      if (endXraw >= 0 && endXraw - TICK_W <= totalW) {
        executionEndX = Math.max(
          executionEndX,
          Math.min(endXraw + Math.ceil(TICK_W / 2), totalW)
        );
      }
      return Math.max(plannedEndX, executionEndX);
    }

    _drawCompareBar(barsSvg, opts) {
      const {
        taskData, variant, barY, boxX1, boxW, colour, changed, theme,
      } = opts;
      const barH = opts.barH || BAR_H;
      const th = theme || chartTheme(false);
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

      const theme = chartTheme(document.body.classList.contains('light'));
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
      const adaptive = buildAdaptiveTicks(minD, maxD, totalW);
      const ticks = adaptive.ticks;
      const tickView = adaptive.labelMode;
      const stepDays = adaptive.stepDays;
      const pxPerMs = totalW / (maxD - minD);
      const dateToX = d => Math.round((d - minD) * pxPerMs);

      const groupRows = [];
      let yOffset = 8;
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
          return { ...t, colour, x1: Math.max(dateToX(s), 0), x2: Math.min(dateToX(f), totalW) };
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

        const summaryX1 = Math.max(dateToX(new Date(Math.min(...allStarts))), 0);
        const summaryX2 = Math.min(dateToX(new Date(Math.max(...allFinish))), totalW);
        const headerY = yOffset;
        yOffset += HEADER_H;
        const taskEntries = visibleTasks.map(row => {
          const y = yOffset;
          yOffset += ROW_H;
          return { ...row, y };
        });
        groupRows.push({
          colour, job_id: g.job_id, commessa: g.commessa, job_ids: g.job_ids,
          headerY, summaryX1, summaryX2, tasks: taskEntries, allTasks: g.tasks,
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
        if (x < 0 || x > totalW) return;
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
            if (x < totalW && x2 > 0) {
              const wk = document.createElementNS(NS, 'rect');
              wk.setAttribute('x', Math.max(x, 0));
              wk.setAttribute('y', 0);
              wk.setAttribute('width', Math.min(x2, totalW) - Math.max(x, 0));
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
        if (x < 0 || x > totalW) return;
        const ln = document.createElementNS(NS, 'line');
        ln.setAttribute('x1', x); ln.setAttribute('y1', 0);
        ln.setAttribute('x2', x); ln.setAttribute('y2', totalH);
        ln.setAttribute('stroke', gridLineColor);
        ln.setAttribute('stroke-width', '1');
        barsSvg.appendChild(ln);
      });

      if (today >= minD && today < maxD) {
        const x = dateToX(today);
        const pxPerDay = totalW / ((maxD - minD) / 86400000);
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

      const defs = document.createElementNS(NS, 'defs');
      barsSvg.appendChild(defs);
      const self = this;
      const taskPosMap = {};

      groupRows.forEach((g, gi) => {
        if (g.summaryX2 > g.summaryX1) {
          const SUMBAR_H = 6;
          const sumY = g.headerY + HEADER_H - SUMBAR_H - 4;
          const sumW = Math.max(g.summaryX2 - g.summaryX1, 20);
          const lbl = document.createElementNS(NS, 'text');
          lbl.setAttribute('x', g.summaryX1 + 2);
          lbl.setAttribute('y', sumY - 5);
          lbl.setAttribute('fill', g.colour);
          lbl.setAttribute('font-size', '12');
          lbl.setAttribute('font-weight', '700');
          lbl.setAttribute('font-family', '-apple-system,BlinkMacSystemFont,sans-serif');
          if (self.enableDeadlineEditor) {
            lbl.setAttribute('cursor', 'pointer');
            lbl.setAttribute('data-dl-job-id', g.job_id);
          }
          const jobSpan = document.createElementNS(NS, 'tspan');
          jobSpan.textContent = g.commessa || g.job_id;
          lbl.appendChild(jobSpan);
          barsSvg.appendChild(lbl);
          const sumBar = document.createElementNS(NS, 'rect');
          sumBar.setAttribute('x', g.summaryX1);
          sumBar.setAttribute('y', sumY);
          sumBar.setAttribute('width', sumW);
          sumBar.setAttribute('height', SUMBAR_H);
          sumBar.setAttribute('rx', SUMBAR_H / 2);
          sumBar.setAttribute('fill', g.colour);
          sumBar.setAttribute('opacity', '0.45');
          barsSvg.appendChild(sumBar);
        }

        const dlStr = (g.job_ids || [g.job_id]).map(jid => self.deadlines[jid]).filter(Boolean).sort().pop();
        if (dlStr) {
          const dlDate = parseDate(dlStr);
          if (dlDate >= minD && dlDate <= maxD) {
            const dlX = dateToX(dlDate);
            const dlTop = g.headerY;
            const dlBot = g.tasks.length ? g.tasks[g.tasks.length - 1].y + ROW_H : g.headerY + HEADER_H;
            const dlColor = theme.deadlineColor;

            const dlLine = document.createElementNS(NS, 'line');
            dlLine.setAttribute('x1', dlX);
            dlLine.setAttribute('y1', dlTop + HEADER_H);
            dlLine.setAttribute('x2', dlX);
            dlLine.setAttribute('y2', dlBot);
            dlLine.setAttribute('stroke', dlColor);
            dlLine.setAttribute('stroke-width', '1.5');
            dlLine.setAttribute('stroke-dasharray', '4,3');
            dlLine.setAttribute('opacity', '0.80');
            barsSvg.appendChild(dlLine);

            const tickTop = document.createElementNS(NS, 'line');
            tickTop.setAttribute('x1', dlX - 5);
            tickTop.setAttribute('y1', dlTop + HEADER_H);
            tickTop.setAttribute('x2', dlX + 5);
            tickTop.setAttribute('y2', dlTop + HEADER_H);
            tickTop.setAttribute('stroke', dlColor);
            tickTop.setAttribute('stroke-width', '2');
            tickTop.setAttribute('opacity', '0.85');
            barsSvg.appendChild(tickTop);

            const DIA = 5;
            const labelY = dlBot + 4;
            const poly = document.createElementNS(NS, 'polygon');
            poly.setAttribute('points',
              dlX + ',' + labelY + ' ' +
              (dlX + DIA) + ',' + (labelY + DIA) + ' ' +
              dlX + ',' + (labelY + DIA * 2) + ' ' +
              (dlX - DIA) + ',' + (labelY + DIA));
            poly.setAttribute('fill', dlColor);
            poly.setAttribute('opacity', '0.9');
            barsSvg.appendChild(poly);

            const dlFmt = dlDate.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit', year: '2-digit' });
            const dlLbl = document.createElementNS(NS, 'text');
            dlLbl.setAttribute('x', dlX + DIA + 4);
            dlLbl.setAttribute('y', labelY + DIA + 3.5);
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
              const boxX1 = Math.max(dateToX(currentExt.start), 0);
              const boxX2 = Math.min(dateToX(currentExt.finish), totalW);
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
                const boxX1 = Math.max(dateToX(proposedExt.start), 0);
                const boxX2 = Math.min(dateToX(proposedExt.finish), totalW);
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
                  Math.min(dateToX(currentExt.finish), totalW) - Math.max(dateToX(currentExt.start), 0),
                  4
                );
              } else if (proposed) {
                const pe = self._plannedExtents(proposed);
                if (pe.start && pe.finish) {
                  labelW = Math.max(
                    Math.min(dateToX(pe.finish), totalW) - Math.max(dateToX(pe.start), 0),
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
          const boxX1 = Math.max(dateToX(planS), 0);
          const boxX2 = Math.min(dateToX(planF), totalW);
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
            badgeAnchorX = self._badgeAnchorX(plannedEndX, actEnd, dateToX, totalW);
            const actS = parseDate(row.actual_start);
            const TAIL_H = 3, TICK_H = BAR_H + 6, TICK_W = 3;
            const actX1raw = dateToX(actS);
            const actX2raw = dateToX(actEnd);
            const tx1 = Math.max(actX1raw, 0);
            const tx2 = Math.min(actX2raw, totalW);
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
              const lx = Math.max(x1, 0);
              const rx = Math.min(x1 + TICK_W, totalW);
              if (rx > lx) drawActualPart(lx, tickY, rx - lx, TICK_H, '0.95');
            };
            if (tx2 > tx1) drawActualPart(tx1, midY - TAIL_H / 2, tx2 - tx1, TAIL_H, '0.90');
            if (actX1raw + TICK_W >= 0 && actX1raw <= totalW) drawTickAt(actX1raw);
            if (actX2raw >= 0 && actX2raw - TICK_W <= totalW) drawTickAt(actX2raw);
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
            const AMBER = 'hsl(38,90%,48%)';
            const charW = BADGE_FONT * 0.60;
            const badges = [];
            if (isCompleted) badges.push({ text: '✓ COMPLETATA', color: 'hsl(142,55%,40%)' });
            else {
              if (isLate || isOverdue) badges.push({ text: 'IN RITARDO', color: RED });
              if (isStarted) badges.push({ text: 'IN LAVORAZIONE', color: SLATE });
              else badges.push({ text: isOverdue ? 'DA INIZIARE' : 'PIANIFICATA', color: SLATE });
            }
            let bx = Math.max(badgeAnchorX, 0) + BADGE_PAD;
            badges.forEach(({ text, color }) => {
              const bw = Math.ceil(text.length * charW) + 14;
              if (bx + bw > totalW + 100) return;
              const by = midY - BADGE_H / 2;
              const bg = document.createElementNS(NS, 'rect');
              bg.setAttribute('x', bx); bg.setAttribute('y', by);
              bg.setAttribute('width', bw); bg.setAttribute('height', BADGE_H);
              bg.setAttribute('rx', BADGE_RX); bg.setAttribute('fill', color);
              bg.setAttribute('opacity', '0.92'); bg.setAttribute('pointer-events', 'none');
              barsSvg.appendChild(bg);
              const bt = document.createElementNS(NS, 'text');
              bt.setAttribute('x', bx + bw / 2); bt.setAttribute('y', by + BADGE_H / 2 + 3.5);
              bt.setAttribute('fill', 'white'); bt.setAttribute('font-size', BADGE_FONT);
              bt.setAttribute('font-weight', '700');
              bt.setAttribute('text-anchor', 'middle');
              bt.setAttribute('pointer-events', 'none');
              bt.textContent = text;
              barsSvg.appendChild(bt);
              bx += bw + GAP;
            });
            if (self.enableWarningNav && !isCompleted && row.warning_count > 0) {
              const warnText = '⚠ ' + row.warning_count;
              const bw = Math.ceil(warnText.length * charW) + 14;
              if (bx + bw <= totalW + 100) {
                const by = midY - BADGE_H / 2;
                const bg = document.createElementNS(NS, 'rect');
                bg.setAttribute('x', bx); bg.setAttribute('y', by);
                bg.setAttribute('width', bw); bg.setAttribute('height', BADGE_H);
                bg.setAttribute('rx', BADGE_RX); bg.setAttribute('fill', AMBER);
                bg.setAttribute('data-warn-ormid', row.orm_id);
                bg.style.cursor = 'pointer';
                barsSvg.appendChild(bg);
                const bt = document.createElementNS(NS, 'text');
                bt.setAttribute('x', bx + bw / 2); bt.setAttribute('y', by + BADGE_H / 2 + 3.5);
                bt.setAttribute('fill', 'white'); bt.setAttribute('font-size', BADGE_FONT);
                bt.setAttribute('font-weight', '700');
                bt.setAttribute('text-anchor', 'middle');
                bt.setAttribute('pointer-events', 'none');
                bt.textContent = warnText;
                barsSvg.appendChild(bt);
              }
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

      this._lastLayoutW = totalW;
      } finally {
        this._rendering = false;
      }
    }

    openDeadlineEditor(jobId, cx, cy) {
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
        self.render(self.tasks, self.view);
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
          self.fitToData = false;
          self._dataMinD = null;
          self._dataMaxD = null;
          self.view = btn.dataset.view;
          self.offset = 0;
          self.render(self.tasks, self.view);
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
        self.render(self.tasks, self.view);
      });
      if (next) next.addEventListener('click', () => {
        if (self.fitToData && self._dataMinD) {
          self._shiftFitWindow(1);
        } else {
          self.offset += (self.view === 'year') ? 1 : self.offsetStep(self.view);
        }
        self.render(self.tasks, self.view);
      });
      if (todayBtn) todayBtn.addEventListener('click', () => {
        if (self.fitToData) {
          if (!self._fitShifted) return;
          if (self._fitRestore) self.applyFitViewport(self._fitRestore);
          else self.applyFitToData(self.tasks);
          self.render(self.tasks, self.view);
          return;
        }
        if (self.offset === 0) return;
        self.offset = 0;
        self.render(self.tasks, self.view);
      });

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
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
          self.closeDeadlineEditor();
        }
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (e.key === 'ArrowLeft') {
          self.offset -= (self.view === 'year') ? 1 : self.offsetStep(self.view);
          self.render(self.tasks, self.view);
        } else if (e.key === 'ArrowRight') {
          self.offset += (self.view === 'year') ? 1 : self.offsetStep(self.view);
          self.render(self.tasks, self.view);
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
  GanttChart.LIGHT_PALETTE = PALETTE_LIGHT;
  global.GanttChart = GanttChart;
})(typeof window !== 'undefined' ? window : this);
