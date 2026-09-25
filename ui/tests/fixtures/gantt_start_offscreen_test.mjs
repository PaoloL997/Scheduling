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

const base = {
  commessa: 'SO01', job_id: 'SO01', activity_id: 'SO01-01-act',
  item: '01', activity: 'act',
  start: '2026-06-10', finish: '2026-06-30',
  actual_finish: '2026-06-28', warning_count: 0,
};

// Window opens on 1 June; returns the edge arrows drawn for the task.
function arrowsFor(task) {
  const chart = new GanttChart({ prefix: '', enableStats: false, simplified: false });
  chart.tasks = [task];
  chart.deadlines = {};
  chart._dataMinD = new Date('2026-06-01T00:00:00');
  chart._dataMaxD = new Date('2026-08-31T00:00:00');
  chart.fitToData = true;
  chart.render([task], 'year');
  return [...document.querySelectorAll('#bars-svg polygon[data-start-offscreen]')];
}

// Started on 20 May, before the view: the start tick is cut off, so an
// arrow on the left edge tells the activity did start.
const early = arrowsFor({ ...base, actual_start: '2026-05-20' });
if (early.length !== 1) throw new Error(`expected one edge arrow, got ${early.length}`);

// Started inside the view: its own start tick is visible, no arrow.
const inside = arrowsFor({ ...base, actual_start: '2026-06-12' });
if (inside.length !== 0) throw new Error(`unexpected edge arrow for a visible start, got ${inside.length}`);

// Never started: nothing to point at.
const notStarted = arrowsFor({ ...base, actual_finish: null });
if (notStarted.length !== 0) throw new Error(`unexpected edge arrow for a planned activity, got ${notStarted.length}`);

console.log('ok');
