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

function renderTask(chart, task) {
  chart.tasks = [task];
  chart.render([task], 'year');
  const planned = document.querySelector('#bars-svg rect[data-task]');
  const plannedRight = Number(planned.getAttribute('x')) + Number(planned.getAttribute('width'));
  const tail = [...document.querySelectorAll('#bars-svg rect')]
    .find((r) => !r.getAttribute('data-task') && Number(r.getAttribute('height')) === 3);
  const tailRight = tail
    ? Number(tail.getAttribute('x')) + Number(tail.getAttribute('width'))
    : 0;
  const badgeRects = [...document.querySelectorAll('#bars-svg rect')]
    .filter((r) => !r.getAttribute('data-task') && Number(r.getAttribute('height')) === 16);
  const firstBadgeX = Math.min(...badgeRects.map((r) => Number(r.getAttribute('x'))));
  const latestBarRight = Math.max(plannedRight, tailRight);
  if (firstBadgeX < latestBarRight) {
    throw new Error(
      `badge before latest bar end (badgeX=${firstBadgeX}, latestBarRight=${latestBarRight})`
    );
  }
  return { plannedRight, tailRight, firstBadgeX };
}

const base = {
  commessa: 'T01',
  job_id: 'T01',
  activity_id: 'T01-01-act',
  item: '01',
  activity: 'act',
  start: '2026-06-10',
  finish: '2026-06-20',
  warning_count: 0,
};

// Actual finish after planned finish → badge after actual end.
const late = renderTask(makeChart(), {
  ...base,
  actual_start: '2026-06-10',
  actual_finish: '2026-06-28',
});
if (late.firstBadgeX <= late.plannedRight) {
  throw new Error('expected badge after actual finish when late');
}

// Actual finish before planned finish → badge after planned end.
const early = renderTask(makeChart(), {
  ...base,
  actual_start: '2026-06-10',
  actual_finish: '2026-06-15',
});
if (early.firstBadgeX <= early.plannedRight) {
  throw new Error('expected badge after planned finish when completed early');
}

// No actual finish: estimated end (actual_start + duration) after planned → badge after estimate.
const inProgress = renderTask(makeChart(), {
  ...base,
  start: '2026-06-03',
  finish: '2026-07-03',
  actual_start: '2026-06-08',
  actual_finish: null,
});
if (inProgress.tailRight <= inProgress.plannedRight) {
  throw new Error('expected estimated actual tail past planned end');
}
if (inProgress.firstBadgeX <= inProgress.tailRight) {
  throw new Error('expected badge after estimated actual end when in progress');
}

console.log('ok', { late, early, inProgress });
