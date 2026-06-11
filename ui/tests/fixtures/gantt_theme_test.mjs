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
  },
];

const chart = new GanttChart({ prefix: '', enableStats: false });
chart.view = 'year';
chart.tasks = tasks;
chart.deadlines = {};
chart.render(tasks, 'year');

const gridLineDark = () => {
  const line = document.querySelector('#bars-svg line');
  return line ? line.getAttribute('stroke') : null;
};

const darkStroke = gridLineDark();
if (!darkStroke) throw new Error('expected grid line in dark theme');

document.body.classList.add('light');
// MutationObserver schedules renderWhenReady asynchronously via rAF.
await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

const lightStroke = gridLineDark();
if (lightStroke === darkStroke) {
  throw new Error(`grid line stroke did not update on theme change: ${darkStroke}`);
}

console.log('ok', { darkStroke, lightStroke });
