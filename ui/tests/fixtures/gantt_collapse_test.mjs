import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const ganttSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'gantt.js'), 'utf8');

function loadGantt() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
  <div id="card">
    <div id="bars-scroll" style="width:800px;height:600px;overflow:auto">
      <svg id="bars-svg"></svg>
    </div>
    <div id="axis-scroll"><svg id="axis-svg"></svg></div>
  </div>
  <button id="nav-expand-toggle" type="button" data-mode="expand">Espandi tutto</button>
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

function taskBars() {
  return [...document.querySelectorAll('rect[data-task]')];
}

const { GanttChart } = loadGantt();

const tasks = [
  {
    commessa: 'C1', job_id: 'C1', activity_id: 'C1-a',
    item: '1', activity: 'a',
    start: '2026-06-01', finish: '2026-06-05',
  },
  {
    commessa: 'C1', job_id: 'C1', activity_id: 'C1-b',
    item: '1', activity: 'b',
    start: '2026-06-06', finish: '2026-06-10',
  },
  {
    commessa: 'C2', job_id: 'C2', activity_id: 'C2-a',
    item: '1', activity: 'a',
    start: '2026-07-01', finish: '2026-07-08',
  },
];

const chart = new GanttChart({
  prefix: '',
  collapsibleGroups: true,
  enableStats: false,
});
chart.tasks = tasks;
chart.deadlines = { C1: '2026-06-20', C2: '2026-07-15' };
chart.jobs = { C1: { status: 'pianificata' }, C2: { status: 'pianificata' } };
chart._dataMinD = new Date('2026-01-01T00:00:00');
chart._dataMaxD = new Date('2026-12-31T00:00:00');
chart.fitToData = true;
chart.render(tasks, 'year');

// Default: collapsed → summary/header only, no activity bars
assert(taskBars().length === 0, `default collapsed should hide task bars, got ${taskBars().length}`);
assert(
  document.querySelectorAll('[data-toggle-group]').length >= 2,
  'expected toggle hits on collapsed groups'
);
const labels = [...document.querySelectorAll('text')].map(t => t.textContent);
assert(labels.some(t => t === 'C1' || t.includes('C1')), 'expected C1 label without chevron');
assert(
  labels.some(t => t.includes('att.') && t.includes('%') && (
    t.includes('Pianificata') || t.includes('In tempo') || t.includes('In ritardo') || t.includes('Rischio penale')
  )),
  'expected activity count, percent and timing status next to commessa'
);
assert(!labels.some(t => t.includes('▶') || t.includes('▼')), 'chevron should not appear');
assert(!labels.some(t => t.includes('◷')), 'deadline edit affordance should not appear');
assert(
  !labels.some(t => /\d{2}\/\d{2}\/\d{2}/.test(t)),
  'collapsed groups should hide deadline labels'
);
assert(
  document.querySelectorAll('#bars-svg polygon').length === 0,
  'collapsed groups should hide deadline diamonds'
);

// Expand one commessa
chart.toggleGroup('C1');
assert(taskBars().length === 2, `expand C1 should show 2 bars, got ${taskBars().length}`);
assert(
  [...document.querySelectorAll('[data-toggle-group]')].some(el => el.textContent.includes('C1') || el.getAttribute('data-toggle-group') === 'C1'),
  'C1 name should remain the toggle target'
);
assert(
  [...document.querySelectorAll('text')].some(t => /\d{2}\/\d{2}\/\d{2}/.test(t.textContent)),
  'expanded group should show deadline date'
);
assert(
  document.querySelectorAll('#bars-svg polygon').length >= 1,
  'expanded group should show deadline diamond'
);

// Expand all
chart.expandAllGroups();
assert(taskBars().length === 3, `expand all should show 3 bars, got ${taskBars().length}`);
const btn = document.getElementById('nav-expand-toggle');
assert(btn.dataset.mode === 'collapse', 'button should switch to collapse mode');
assert(btn.textContent === 'Collassa tutto', `button label, got ${btn.textContent}`);

// Collapse all
chart.collapseAllGroups();
assert(taskBars().length === 0, `collapse all should hide bars, got ${taskBars().length}`);
assert(btn.dataset.mode === 'expand', 'button should switch to expand mode');
assert(btn.textContent === 'Espandi tutto', `button label, got ${btn.textContent}`);

// Without collapsibleGroups, bars stay visible (compat)
{
  const chart2 = new GanttChart({ prefix: '', enableStats: false });
  chart2.tasks = tasks;
  chart2.deadlines = {};
  chart2._dataMinD = new Date('2026-01-01T00:00:00');
  chart2._dataMaxD = new Date('2026-12-31T00:00:00');
  chart2.fitToData = true;
  chart2.render(tasks, 'year');
  assert(taskBars().length === 3, `non-collapsible should show all bars, got ${taskBars().length}`);
}

// A commessa wholly outside the window takes no row: the visible collapsed
// summaries sit exactly where they would without it, with no empty gap.
{
  const visible = [
    { ...tasks[0] },
    { ...tasks[2] },
    { commessa: 'C3', job_id: 'C3', activity_id: 'C3-a', item: '1', activity: 'a',
      start: '2026-08-01', finish: '2026-08-08' },
  ];
  const old = { commessa: 'OLD', job_id: 'OLD', activity_id: 'OLD-a', item: '1', activity: 'a',
    start: '2026-02-01', finish: '2026-02-10' };
  const summaryYs = (list) => {
    const c = new GanttChart({ prefix: '', collapsibleGroups: true, enableStats: false });
    c.tasks = list;
    c.deadlines = {};
    c._dataMinD = new Date('2026-05-01T00:00:00');
    c._dataMaxD = new Date('2026-12-31T00:00:00');
    c.fitToData = true;
    c.render(list, 'year');
    return [...document.querySelectorAll('rect[data-toggle-group]')].map(r => Number(r.getAttribute('y')));
  };
  const without = summaryYs(visible);
  const withOld = summaryYs([old, ...visible]);
  assert(
    withOld.join(',') === without.join(','),
    `out-of-window commessa must take no row: ${withOld.join(', ')} vs ${without.join(', ')}`
  );
}

console.log('ok');
