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
  const delayLabels = [...document.querySelectorAll('#bars-svg text')]
    .filter((t) => /^\+\d+ gg$/.test(t.textContent));
  const delayX = Math.min(...delayLabels.map((r) => Number(r.getAttribute('x'))));
  const latestBarRight = Math.max(plannedRight, tailRight);
  if (delayX < latestBarRight) {
    throw new Error(
      `badge before latest bar end (badgeX=${delayX}, latestBarRight=${latestBarRight})`
    );
  }
  return { plannedRight, tailRight, delayX, badgeCount: delayLabels.length };
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

// Closed activities are never late, so they carry no badge at all.
const late = renderTask(makeChart(), {
  ...base,
  actual_start: '2026-06-10',
  actual_finish: '2026-06-28',
  state_key: 'completata',
  delay_days: 0,
});
const early = renderTask(makeChart(), {
  ...base,
  actual_start: '2026-06-10',
  actual_finish: '2026-06-15',
  state_key: 'completata',
  delay_days: 0,
});
if (late.badgeCount !== 0 || early.badgeCount !== 0) {
  throw new Error(`completed activities must carry no badge (late=${late.badgeCount}, early=${early.badgeCount})`);
}

// No actual finish: estimated end (actual_start + duration) after planned → badge after estimate.
const inProgress = renderTask(makeChart(), {
  ...base,
  start: '2026-06-03',
  finish: '2026-07-03',
  actual_start: '2026-06-08',
  effective_finish: '2026-07-08',
  actual_finish: null,
  state_key: 'in_ritardo',
  delay_days: 5,
});
if (inProgress.tailRight <= inProgress.plannedRight) {
  throw new Error('expected estimated actual tail past planned end');
}
// Started five days late: the "+N gg" badge sits after the estimated end.
if (inProgress.badgeCount !== 1) {
  throw new Error(`expected one delay badge when in progress late, got ${inProgress.badgeCount}`);
}
if (inProgress.delayX <= inProgress.tailRight) {
  throw new Error('expected badge after estimated actual end when in progress');
}

console.log('ok', { late, early, inProgress });
