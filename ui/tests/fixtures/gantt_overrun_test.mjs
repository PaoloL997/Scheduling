import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const ganttSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'gantt.js'), 'utf8');

function makeChart() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="card" style="width:800px">
      <div id="axis-row"><div id="axis-scroll"><svg id="axis-svg"></svg></div></div>
      <div id="bars-scroll" style="height:320px;overflow:auto">
        <svg id="bars-svg"></svg>
      </div>
    </div>
  </body></html>`, { pretendToBeVisual: true });

  global.window = dom.window;
  global.document = dom.window.document;
  global.MutationObserver = dom.window.MutationObserver;
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  global.requestAnimationFrame = (cb) => { cb(); return 1; };

  eval(ganttSrc);
  const GanttChart = dom.window.GanttChart;
  const chart = new GanttChart({ prefix: '', enableStats: false, simplified: false });
  chart.view = 'year';
  chart.deadlines = {};
  return chart;
}

function iso(d) {
  return [
    d.getFullYear(),
    String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0'),
  ].join('-');
}

function shiftDays(days) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() + days);
  return d;
}

// The server's working-day estimate of the end of a started activity.
function effectiveFinish(start, finish, actualStart) {
  const wd = (a, b) => {
    let n = 0;
    for (const d = new Date(a); d <= b; d.setDate(d.getDate() + 1)) {
      if (d.getDay() >= 1 && d.getDay() <= 5) n += 1;
    }
    return Math.max(1, n);
  };
  const duration = wd(start, finish);
  const d = new Date(actualStart);
  let left = duration;
  while (true) {
    if (d.getDay() >= 1 && d.getDay() <= 5) {
      left -= 1;
      if (left === 0) return iso(d);
    }
    d.setDate(d.getDate() + 1);
  }
}

function render(task) {
  const chart = makeChart();
  chart.tasks = [task];
  chart.render([task], 'year');
  const rects = [...document.querySelectorAll('#bars-svg rect')];
  // Execution ticks are 28px tall (bar height + 6); the today strip spans the
  // whole chart, so height alone separates them.
  const ticks = rects.filter((r) => !r.getAttribute('data-task')
    && Number(r.getAttribute('height')) === 28
    && Number(r.getAttribute('width')) <= 3);
  const dashes = [...document.querySelectorAll('#bars-svg line')]
    .filter((l) => l.getAttribute('stroke-dasharray')
      && Number(l.getAttribute('y1')) === Number(l.getAttribute('y2')));
  const delayLabels = [...document.querySelectorAll('#bars-svg text')]
    .filter((t) => /^\+\d+ gg$/.test(t.textContent));
  const badgeTexts = [...document.querySelectorAll('#bars-svg text')]
    .map((t) => t.textContent);
  return { ticks, dashes, delayLabels, badgeTexts };
}

const base = {
  commessa: 'T01',
  job_id: 'T01',
  activity_id: 'T01-02-qualifica',
  item: '02',
  activity: 'Qualifica',
  warning_count: 0,
};

// Started 40 days ago on a 10-day activity, still open: the estimated end is
// 30 days behind us, so a dashed tail must reach today and close with a tick.
const over = render({
  ...base,
  start: iso(shiftDays(-40)),
  finish: iso(shiftDays(-30)),
  actual_start: iso(shiftDays(-40)),
  actual_finish: null,
  effective_finish: effectiveFinish(shiftDays(-40), shiftDays(-30), shiftDays(-40)),
  state_key: 'in_ritardo',
  delay_days: 30,
});
if (over.dashes.length !== 1) {
  throw new Error(`expected one dashed overrun tail, got ${over.dashes.length}`);
}
// Two ticks only: the actual start and today. The estimated end is where
// the dashes begin, and drawing a bar there too read as a second end.
if (over.ticks.length !== 2) {
  throw new Error(`expected start and today ticks only, got ${over.ticks.length}`);
}
const dash = over.dashes[0];
const dashRight = Number(dash.getAttribute('x2'));
const todayTick = Math.max(...over.ticks.map((t) => Number(t.getAttribute('x'))));
if (Math.abs(todayTick + 1 - dashRight) > 2) {
  throw new Error(`today tick not at dash end (tick=${todayTick}, dash=${dashRight})`);
}
const delayX = Math.min(...over.delayLabels.map((r) => Number(r.getAttribute('x'))));
// Open 30 days past the planned finish: flagged with the delay only.
if (!over.badgeTexts.includes('+30 gg')) {
  throw new Error(`overrunning activity is not flagged +30 gg: ${over.badgeTexts}`);
}
if (over.delayLabels.length !== 1) {
  throw new Error(`expected only the delay badge, got ${over.delayLabels.length}`);
}
if (delayX < dashRight) {
  throw new Error(`badge before overrun end (badge=${delayX}, dash=${dashRight})`);
}

// The estimated-end tick is only dropped while overrunning: an activity
// inside its window still closes on its estimate.
// Still inside the estimated window: no dashed tail at all.
const within = render({
  ...base,
  start: iso(shiftDays(-5)),
  finish: iso(shiftDays(25)),
  actual_start: iso(shiftDays(-5)),
  actual_finish: null,
  effective_finish: effectiveFinish(shiftDays(-5), shiftDays(25), shiftDays(-5)),
  state_key: 'in_lavorazione',
  delay_days: 0,
});
if (within.dashes.length !== 0) {
  throw new Error('unexpected overrun tail for an activity inside its estimate');
}
if (within.ticks.length !== 2) {
  throw new Error(`expected start and estimated-end ticks, got ${within.ticks.length}`);
}
if (within.delayLabels.length !== 0) {
  throw new Error('activity inside its estimate must not be flagged late');
}

// Closed activity: the overrun tail never applies, even when it ran long.
const closed = render({
  ...base,
  start: iso(shiftDays(-40)),
  finish: iso(shiftDays(-30)),
  actual_start: iso(shiftDays(-40)),
  actual_finish: iso(shiftDays(-20)),
  state_key: 'completata',
  delay_days: 0,
});
if (closed.dashes.length !== 0) {
  throw new Error('unexpected overrun tail for a completed activity');
}

console.log('ok', {
  overrunTicks: over.ticks.length,
  withinTicks: within.ticks.length,
  dashRight,
  delayX,
});
