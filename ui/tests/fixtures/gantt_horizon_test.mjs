import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const ganttSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'gantt.js'), 'utf8');

function loadGantt() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
  <div id="toggle-group">
    <button class="tbtn active" data-view="horizon">Panoramica</button>
    <button class="tbtn" data-view="month">Mese</button>
  </div>
  <div id="bars-scroll" style="width:800px;height:400px;overflow:auto"><svg id="bars-svg"></svg></div>
  <div id="axis-scroll"><svg id="axis-svg"></svg></div>
</body></html>`, { pretendToBeVisual: true });
  global.window = dom.window;
  global.document = dom.window.document;
  global.ResizeObserver = class { observe() {} disconnect() {} };
  global.requestAnimationFrame = (cb) => { cb(); return 1; };
  eval(ganttSrc);
  return dom.window.GanttChart;
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const GanttChart = loadGantt();
assert(typeof GanttChart.computeHorizonViewport === 'function', 'computeHorizonViewport missing');
assert(typeof GanttChart.summarizeCommessaTasks === 'function', 'summarizeCommessaTasks missing');

{
  // Counters come from the tasks; the status is the server's aggregate
  // (``jobs[job_id]`` of /api/gantt/), passed as the second argument.
  const s0 = GanttChart.summarizeCommessaTasks([
    { start: '2099-06-01', finish: '2099-06-05' },
    { start: '2099-06-08', finish: '2099-06-12' },
  ], { status: 'pianificata' });
  assert(s0.total === 2 && s0.pct === 0 && s0.status === 'Pianificata', `planned summary got ${s0.status}`);

  // Mon–Fri 2099-06-01..05 = 5 working days; completed → 100% of that activity.
  // Second activity not started → 0/5. Total = 5/10 → 50%.
  const s1 = GanttChart.summarizeCommessaTasks([
    { start: '2099-06-01', finish: '2099-06-05', actual_start: '2099-06-01', actual_finish: '2099-06-05' },
    { start: '2099-06-08', finish: '2099-06-12' },
  ], { status: 'in_tempo' });
  assert(s1.total === 2 && s1.pct === 50 && s1.status === 'In tempo', `pct/status got pct=${s1.pct} status=${s1.status}`);

  const s2 = GanttChart.summarizeCommessaTasks([{ start: '2099-06-01', finish: '2099-06-05' }], { status: 'in_ritardo' });
  assert(s2.status === 'In ritardo', `late job should read In ritardo, got ${s2.status}`);
  const s3 = GanttChart.summarizeCommessaTasks([{ start: '2099-06-01', finish: '2099-06-05' }], { status: 'rischio_penale' });
  assert(s3.status === 'Rischio penale', `penalty risk got ${s3.status}`);
  // Charts fed without the jobs payload (optimisation previews) show no status.
  const sNone = GanttChart.summarizeCommessaTasks([{ start: '2099-06-01', finish: '2099-06-05' }]);
  assert(sNone.status === '', `no job payload should give no status, got "${sNone.status}"`);

  // Delay badge days and lateness are read from the task, never recomputed.
  const d = GanttChart.taskDelayDays;
  const today = new Date(2026, 8, 25);
  assert(d({ state_key: 'in_ritardo', delay_days: 3 }) === 3, 'delay from payload');
  assert(d({ state_key: 'in_ritardo', delay_days: '10' }) === 10, 'numeric string delay');
  assert(d({ state_key: 'completata', delay_days: 0 }) === 0, 'closed is 0');
  assert(d({ start: '2026-09-20', finish: '2026-10-20' }, today) === 0, 'no payload field is 0');

  // Home filter: a commessa with every activity completed is hidden unless
  // asked for, or picked by name; an open commessa keeps its completed ones.
  {
    const ft = GanttChart.filterTasks;
    const list = [
      { commessa: 'DONE', actual_start: '2026-09-01', actual_finish: '2026-09-05' },
      { commessa: 'OPEN', actual_start: '2026-09-01', actual_finish: '2026-09-05' },
      { commessa: 'OPEN', start: '2026-10-01', finish: '2026-10-05' },
    ];
    const keys = f => ft(list, f, today).map(t => t.commessa).join(',');
    assert(keys({}) === 'OPEN,OPEN', `completed commessa must be hidden by default, got ${keys({})}`);
    assert(keys({ showCompletedJobs: true }) === 'DONE,OPEN,OPEN', 'showCompletedJobs must bring it back');
    assert(keys({ commessa: 'DONE' }) === 'DONE', 'picking the completed commessa must show it');
    assert(ft(list, null, today).length === 3, 'no filter object (other pages) keeps every task');
  }
}

const tasks = [
  {
    commessa: 'A', job_id: 'A', activity_id: 'a1',
    item: '1', activity: 'done',
    start: '2026-01-01', finish: '2026-01-10',
    actual_start: '2026-01-01', actual_finish: '2026-01-10',
  },
  {
    commessa: 'B', job_id: 'B', activity_id: 'b1',
    item: '1', activity: 'wip',
    start: '2026-03-01', finish: '2026-03-20',
    actual_start: '2026-03-05',
    effective_finish: '2026-03-25',
  },
  {
    commessa: 'C', job_id: 'C', activity_id: 'c1',
    item: '1', activity: 'later',
    start: '2026-06-01', finish: '2026-06-15',
  },
];

const fit = GanttChart.computeHorizonViewport(tasks);
assert(fit, 'horizon viewport null');
assert(fit.view === 'horizon', `view=${fit.view}`);

const days = (fit.maxD - fit.minD) / 86400000;
assert(days >= 160 && days <= 200, `expected ~6 months (~180d), got ${days}`);

// Left aligns to the earliest WIP planned start; visual left pad is applied at render time.
const earliest = new Date('2026-03-01T00:00:00').getTime();
const driftLeft = (earliest - fit.minD.getTime()) / 86400000;
assert(Math.abs(driftLeft) < 1.5, `left not anchored at earliest bar, drift=${driftLeft}`);

const chart = new GanttChart({ prefix: '', initialView: 'horizon', enableStats: false });
const barsScroll = document.getElementById('bars-scroll');
Object.defineProperty(barsScroll, 'clientWidth', { configurable: true, get: () => 800 });
Object.defineProperty(barsScroll, 'clientHeight', { configurable: true, get: () => 400 });
Object.defineProperty(barsScroll.parentElement, 'clientWidth', { configurable: true, get: () => 800 });
chart.setData(tasks, {});
assert(chart.view === 'horizon', 'chart should stay in horizon');
assert(chart.fitToData === true, 'horizon uses fit window');
assert(chart._dataMinD && chart._dataMaxD, 'horizon window missing');

const bars = [...document.querySelectorAll('rect[data-task]')];
assert(bars.length >= 1, 'expected task bars after render');
const minBarX = Math.min(...bars.map(b => Number(b.getAttribute('x'))));
assert(minBarX >= 20, `first bar too close to left edge: x=${minBarX}`);

console.log('ok');
