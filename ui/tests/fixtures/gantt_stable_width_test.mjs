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
  },
];

const chart = new GanttChart({ prefix: '', enableStats: false });
chart.view = 'year';
chart.tasks = tasks;
chart.deadlines = {};
chart.render(tasks, 'year');

const barsScroll = document.getElementById('bars-scroll');
const bar = document.querySelector('rect[data-task]');
if (!bar) throw new Error('expected a task bar');
const x1 = bar.getAttribute('x');

// Simulate scrollbar shrinking the scroll area without changing the card width.
Object.defineProperty(barsScroll, 'clientWidth', { configurable: true, get: () => 783 });
chart.render(tasks, 'year');

const x2 = bar.getAttribute('x');
if (x1 !== x2) {
  throw new Error(`bar shifted after scrollbar simulation: ${x1} -> ${x2}`);
}

console.log('ok', { x: x1 });
