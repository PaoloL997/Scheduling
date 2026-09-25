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

const tasks = [
  {
    commessa: '25021',
    job_id: '25021',
    activity_id: '25021-01-Qualifica',
    item: '01',
    activity: 'Qualifica',
    start: '2026-06-01',
    finish: '2026-06-22',
    actual_start: '2026-06-14',
    actual_finish: '2026-06-14',
    warning_count: 0,
  },
];

const chart = new GanttChart({ prefix: '', enableStats: false, simplified: false });
chart.view = 'year';
chart.tasks = tasks;
chart.deadlines = {};
chart.render(tasks, 'year');

const planned = document.querySelector('#bars-svg rect[data-task]');
const plannedX = Number(planned.getAttribute('x'));
const plannedRight = plannedX + Number(planned.getAttribute('width'));
const tail = [...document.querySelectorAll('#bars-svg rect')]
  .find((r) => !r.getAttribute('data-task') && Number(r.getAttribute('height')) === 3);
const tailRight = tail
  ? Number(tail.getAttribute('x')) + Number(tail.getAttribute('width'))
  : 0;
const delayLabels = [...document.querySelectorAll('#bars-svg text')]
    .filter((t) => /^\+\d+ gg$/.test(t.textContent));
const delayX = Math.min(...delayLabels.map((r) => Number(r.getAttribute('x'))));

console.log({
  plannedX,
  plannedRight,
  tailRight,
  delayX,
  badgeOnPlanned: delayX < plannedRight,
  badgeBeforeTailEnd: delayX < tailRight,
});

// Started late but closed: no "+N gg", only a plain green "Completata"
// placed after the bars, with no card behind it.
if (delayLabels.length !== 0) {
  throw new Error(`completed activity must carry no delay, got ${delayLabels.length}`);
}
const done = [...document.querySelectorAll('#bars-svg text')].filter((t) => t.textContent === 'Completata');
if (done.length !== 1) throw new Error(`expected one "Completata" label, got ${done.length}`);
const doneX = Number(done[0].getAttribute('x'));
if (doneX < plannedRight || doneX < tailRight) {
  throw new Error(`"Completata" overlaps the bars (x=${doneX}, planned=${plannedRight}, tail=${tailRight})`);
}
const cards = [...document.querySelectorAll('#bars-svg rect')]
  .filter((r) => !r.getAttribute('data-task') && Number(r.getAttribute('height')) === 16);
if (cards.length !== 0) throw new Error(`"Completata" must have no card, got ${cards.length}`);

console.log('ok', { plannedRight, tailRight, delayX });
