import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const ganttSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'gantt.js'), 'utf8');

function render(tasks) {
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
  global.ResizeObserver = class { observe() {} disconnect() {} };
  global.requestAnimationFrame = (cb) => { cb(); return 1; };

  eval(ganttSrc);
  const GanttChart = dom.window.GanttChart;
  const chart = new GanttChart({ prefix: '', enableStats: false, simplified: false });
  chart.view = 'year';
  chart.deadlines = {};
  chart.tasks = tasks;
  chart.render(tasks, 'year');
  // Dependency arrows are the only dashed <path> elements in the chart.
  return [...document.querySelectorAll('#bars-svg path')]
    .filter(el => el.getAttribute('stroke-dasharray'));
}

const qualifica = {
  commessa: '25078',
  job_id: '25078',
  activity_id: '25078-02-Qualifica',
  item: '02',
  activity: 'Qualifica',
  start: '2026-07-22',
  finish: '2026-08-17',
  actual_start: '2026-07-22',
  warning_count: 0,
};

function successor(item, predecessors) {
  return {
    commessa: '25078',
    job_id: '25078',
    activity_id: `25078-${item}-produzione`,
    item,
    activity: 'produzione',
    start: '2026-08-17',
    finish: '2026-08-26',
    predecessors,
    warning_count: 0,
  };
}

// Exact reference: the baseline case.
const exact = render([qualifica, successor('02-SNA', '25078-02-Qualifica')]);
if (exact.length !== 1) {
  throw new Error(`expected one dependency arrow, got ${exact.length}`);
}

// Real data: renaming the activity rebuilt its id as "…-Qualifica" while the
// predecessors kept the old "…-qualifica" spelling. The arrow must survive.
const mixedCase = render([qualifica, successor('02-SNB', '25078-02-qualifica')]);
if (mixedCase.length !== 1) {
  throw new Error(`case-different predecessor lost its arrow, got ${mixedCase.length}`);
}

// Padding around the reference is just as harmless.
const padded = render([qualifica, successor('02-SNC', ' 25078-02-QUALIFICA ')]);
if (padded.length !== 1) {
  throw new Error(`padded predecessor lost its arrow, got ${padded.length}`);
}

// An unknown predecessor still draws nothing.
const unknown = render([qualifica, successor('02-SND', '25078-02-collaudo')]);
if (unknown.length !== 0) {
  throw new Error(`unknown predecessor must not draw an arrow, got ${unknown.length}`);
}

console.log('ok', { exact: exact.length, mixedCase: mixedCase.length, padded: padded.length });
