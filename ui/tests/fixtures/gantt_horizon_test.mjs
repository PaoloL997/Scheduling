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
  // Use a far-future window so "today" cannot mark planned tasks as late.
  const s0 = GanttChart.summarizeCommessaTasks([
    { start: '2099-06-01', finish: '2099-06-05' },
    { start: '2099-06-08', finish: '2099-06-12' },
  ]);
  assert(s0.total === 2 && s0.pct === 0 && s0.status === 'Pianificata', `planned summary got ${s0.status}`);

  // Mon–Fri 2099-06-01..05 = 5 working days; completed → 100% of that activity.
  // Second activity not started → 0/5. Total = 5/10 → 50%.
  const s1 = GanttChart.summarizeCommessaTasks([
    { start: '2099-06-01', finish: '2099-06-05', actual_start: '2099-06-01', actual_finish: '2099-06-05' },
    { start: '2099-06-08', finish: '2099-06-12' },
  ]);
  assert(s1.total === 2 && s1.pct === 50 && s1.status === 'In tempo', `pct/status got pct=${s1.pct} status=${s1.status}`);

  const sWip = GanttChart.summarizeCommessaTasks([
    { start: '2099-06-01', finish: '2099-06-05', actual_start: '2099-06-01', actual_finish: null },
  ]);
  assert(sWip.status === 'In tempo', `started on time should be In tempo, got ${sWip.status}`);

  const s2 = GanttChart.summarizeCommessaTasks([
    { start: '2099-06-01', finish: '2099-06-05', actual_start: '2099-06-03', actual_finish: null },
  ]);
  assert(s2.status === 'In ritardo', `late start should be In ritardo, got ${s2.status}`);

  // Duration 4 calendar days; actual start 01/06 → projected end 05/06 > deadline 04/06.
  const s3 = GanttChart.summarizeCommessaTasks([
    { start: '2099-06-01', finish: '2099-06-05', actual_start: '2099-06-01' },
  ], '2099-06-04');
  assert(s3.status === 'Rischio penale', `penalty risk got ${s3.status}`);
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

// Left aligns to the earliest WIP planned start; the ~1 cm pad is applied at render time.
const earliest = new Date('2026-03-01T00:00:00').getTime();
const driftLeft = (earliest - fit.minD.getTime()) / 86400000;
assert(Math.abs(driftLeft) < 1.5, `left not anchored at earliest bar, drift=${driftLeft}`);

const chart = new GanttChart({ prefix: '', initialView: 'horizon', enableStats: false });
chart.setData(tasks, {});
assert(chart.view === 'horizon', 'chart should stay in horizon');
assert(chart.fitToData === true, 'horizon uses fit window');
assert(chart._dataMinD && chart._dataMaxD, 'horizon window missing');

console.log('ok');
