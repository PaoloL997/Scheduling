import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const ganttSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'gantt.js'), 'utf8');

function loadGantt() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
  <div id="stats-row">
    <div class="stat-card is-active" data-stat-filter="total" role="button" aria-pressed="true"></div>
    <div class="stat-card" data-stat-filter="late" role="button" aria-pressed="false">
      <span id="stat-late">–</span>
    </div>
    <div class="stat-card" data-stat-filter="wip" role="button" aria-pressed="false">
      <span id="stat-wip">–</span>
    </div>
    <span id="stat-total">–</span>
  </div>
  <div id="card">
    <div id="bars-scroll" style="width:800px;height:600px;overflow:auto">
      <svg id="bars-svg"></svg>
    </div>
    <div id="axis-scroll"><svg id="axis-svg"></svg></div>
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
  return { GanttChart: dom.window.GanttChart, dom };
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const { GanttChart } = loadGantt();

const tasks = [
  {
    // planned future → not late, not wip
    commessa: 'C1', job_id: 'C1', activity_id: 'C1-planned',
    item: '1', activity: 'planned',
    start: '2099-06-01', finish: '2099-06-10',
  },
  {
    // started on time, still open → wip only
    commessa: 'C1', job_id: 'C1', activity_id: 'C1-wip',
    item: '1', activity: 'wip',
    start: '2020-01-01', finish: '2020-01-10',
    actual_start: '2020-01-01',
  },
  {
    // never started, plan already past → late only
    commessa: 'C2', job_id: 'C2', activity_id: 'C2-late',
    item: '1', activity: 'late',
    start: '2020-02-01', finish: '2020-02-10',
  },
  {
    // started late, still open → late + wip
    commessa: 'C2', job_id: 'C2', activity_id: 'C2-late-wip',
    item: '1', activity: 'late-wip',
    start: '2020-03-01', finish: '2020-03-10',
    actual_start: '2020-03-05',
  },
];

const chart = new GanttChart({
  prefix: '',
  enableStats: true,
  collapsibleGroups: false,
});
chart.setData(tasks, {});

assert(chart.displayTasks().length === 4, 'default shows all tasks');
assert(document.getElementById('stat-total').textContent === '4', 'total count');
assert(document.getElementById('stat-wip').textContent === '2', 'wip count');
assert(document.getElementById('stat-late').textContent === '2', 'late count');

chart.setStatFilter('wip');
assert(chart.statFilter === 'wip', 'wip filter active');
const wipIds = chart.displayTasks().map(t => t.activity_id).sort();
assert(
  JSON.stringify(wipIds) === JSON.stringify(['C1-wip', 'C2-late-wip']),
  `wip filter got ${wipIds.join(',')}`
);
assert(
  document.querySelector('[data-stat-filter="wip"]').classList.contains('is-active'),
  'wip card should be active'
);

chart.setStatFilter('late');
const lateIds = chart.displayTasks().map(t => t.activity_id).sort();
assert(
  JSON.stringify(lateIds) === JSON.stringify(['C2-late', 'C2-late-wip']),
  `late filter got ${lateIds.join(',')}`
);

chart.setStatFilter('late'); // toggle off
assert(chart.statFilter === null, 're-click clears filter');
assert(chart.displayTasks().length === 4, 'cleared filter shows all');

chart.setStatFilter('wip');
chart.setStatFilter('total');
assert(chart.statFilter === null, 'total clears filter');
assert(
  document.querySelector('[data-stat-filter="total"]').classList.contains('is-active'),
  'total card active when unfiltered'
);

console.log('ok');
