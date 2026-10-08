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
    effective_finish: '2026-06-19',
  },
];

const chart = new GanttChart({ prefix: '', enableStats: false, simplified: false });
chart.view = 'year';
chart.tasks = tasks;
chart.deadlines = {};
chart.render(tasks, 'year');

const rects = [...document.querySelectorAll('#bars-svg rect')];
const planned = rects.find((r) => r.getAttribute('data-task'));
const actualParts = rects.filter((r) => !r.getAttribute('data-task'));
const thinRects = actualParts.filter((r) => Number(r.getAttribute('height')) <= 3);
const tallRects = actualParts.filter((r) => Number(r.getAttribute('height')) > 10 && Number(r.getAttribute('width')) <= 3);

if (thinRects.length < 1) throw new Error('expected horizontal tail for actual window');
if (tallRects.length < 2) throw new Error('expected start/end ticks for actual window');

const plannedX = Number(planned.getAttribute('x'));
const leftTick = tallRects.find((r) => Number(r.getAttribute('x')) <= plannedX + 2);
if (!leftTick) throw new Error('expected left tick near actual start');

console.log('ok', { thinRects: thinRects.length, tallRects: tallRects.length, leftTickX: leftTick.getAttribute('x') });
