import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const ganttSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'gantt.js'), 'utf8');

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

// Real data: commessa 25033, 01-SNA — started, no actual_finish yet.
const tasks = [
  {
    commessa: '25033',
    job_id: '25033',
    activity_id: '25033-01-SNA',
    item: '01',
    activity: 'SNA',
    start: '2026-06-03',
    finish: '2026-07-03',
    actual_start: '2026-06-08',
    effective_finish: '2026-07-08',
    actual_finish: null,
    state_key: 'in_ritardo',
    delay_days: 5,
    warning_count: 0,
  },
];

const chart = new GanttChart({ prefix: '', enableStats: false, simplified: false });
chart.view = 'year';
chart.tasks = tasks;
chart.deadlines = {};
chart.render(tasks, 'year');

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

if (tailRight <= plannedRight) {
  throw new Error(`expected estimated actual tail past planned end (${tailRight} vs ${plannedRight})`);
}
if (delayLabels.length !== 1) {
  throw new Error(`expected the delay badge of a late-started activity, got ${delayLabels.length}`);
}
if (delayX < tailRight) {
  throw new Error(
    `badge overlaps actual tail (badgeX=${delayX}, tailRight=${tailRight}, plannedRight=${plannedRight})`
  );
}

console.log('ok', { plannedRight, tailRight, delayX });
