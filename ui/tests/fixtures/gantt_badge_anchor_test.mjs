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
    commessa: 'HM01',
    job_id: 'j1',
    activity_id: 'a1',
    item: '01',
    activity: 'prep',
    start: '2026-06-10',
    finish: '2026-06-20',
    actual_start: '2026-06-10',
    actual_finish: '2026-06-28',
    warning_count: 0,
  },
];

const chart = new GanttChart({ prefix: '', enableStats: false, simplified: false });
chart.view = 'year';
chart.tasks = tasks;
chart.deadlines = {};
chart.render(tasks, 'year');

const planned = document.querySelector('#bars-svg rect[data-task]');
if (!planned) throw new Error('expected planned bar');

const plannedRight = Number(planned.getAttribute('x')) + Number(planned.getAttribute('width'));
const badgeRects = [...document.querySelectorAll('#bars-svg rect')]
  .filter((r) => !r.getAttribute('data-task') && Number(r.getAttribute('height')) === 16);

if (badgeRects.length < 1) throw new Error('expected status badge rects');

const firstBadgeX = Math.min(...badgeRects.map((r) => Number(r.getAttribute('x'))));
if (firstBadgeX <= plannedRight) {
  throw new Error(
    `badge should start after actual finish, not planned end (badgeX=${firstBadgeX}, plannedRight=${plannedRight})`
  );
}

console.log('ok', { plannedRight, firstBadgeX, badgeCount: badgeRects.length });
