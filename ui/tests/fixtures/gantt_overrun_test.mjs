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
  const badgeRects = rects.filter((r) => !r.getAttribute('data-task')
    && Number(r.getAttribute('height')) === 16);
  const badgeTexts = [...document.querySelectorAll('#bars-svg text')]
    .map((t) => t.textContent);
  return { ticks, dashes, badgeRects, badgeTexts };
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
});
if (over.dashes.length !== 1) {
  throw new Error(`expected one dashed overrun tail, got ${over.dashes.length}`);
}
if (over.ticks.length !== 3) {
  throw new Error(`expected start/estimate/today ticks, got ${over.ticks.length}`);
}
const dash = over.dashes[0];
const dashRight = Number(dash.getAttribute('x2'));
const todayTick = Math.max(...over.ticks.map((t) => Number(t.getAttribute('x'))));
if (Math.abs(todayTick + 1 - dashRight) > 2) {
  throw new Error(`today tick not at dash end (tick=${todayTick}, dash=${dashRight})`);
}
const firstBadgeX = Math.min(...over.badgeRects.map((r) => Number(r.getAttribute('x'))));
if (!over.badgeTexts.includes('IN RITARDO')) {
  throw new Error(`overrunning activity is not flagged late: ${over.badgeTexts}`);
}
if (!over.badgeTexts.includes('IN LAVORAZIONE')) {
  throw new Error(`overrunning activity lost its in-progress badge: ${over.badgeTexts}`);
}
if (firstBadgeX < dashRight) {
  throw new Error(`badge before overrun end (badge=${firstBadgeX}, dash=${dashRight})`);
}

// Still inside the estimated window: no dashed tail at all.
const within = render({
  ...base,
  start: iso(shiftDays(-5)),
  finish: iso(shiftDays(25)),
  actual_start: iso(shiftDays(-5)),
  actual_finish: null,
});
if (within.dashes.length !== 0) {
  throw new Error('unexpected overrun tail for an activity inside its estimate');
}
if (within.badgeTexts.includes('IN RITARDO')) {
  throw new Error('activity inside its estimate must not be flagged late');
}

// Closed activity: the overrun tail never applies, even when it ran long.
const closed = render({
  ...base,
  start: iso(shiftDays(-40)),
  finish: iso(shiftDays(-30)),
  actual_start: iso(shiftDays(-40)),
  actual_finish: iso(shiftDays(-20)),
});
if (closed.dashes.length !== 0) {
  throw new Error('unexpected overrun tail for a completed activity');
}

console.log('ok', {
  overrunTicks: over.ticks.length,
  dashRight,
  firstBadgeX,
});
