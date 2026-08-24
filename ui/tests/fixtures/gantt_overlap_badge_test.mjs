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
    commessa: 'C101',
    job_id: 'C101',
    activity_id: 'C101-1-Saldatura',
    item: 1,
    activity: 'Saldatura',
    start: '2026-08-10',
    finish: '2026-08-19',
    actual_start: '2026-08-17',
    warning_count: 0,
    resource_overlaps: [
      {
        role: 'late',
        peer_commessa: 'C205',
        peer_item: 2,
        peer_activity: 'Montaggio',
        overlap_start: '2026-08-20',
        overlap_end: '2026-08-26',
      },
    ],
  },
];

const chart = new GanttChart({ prefix: '', enableStats: false, simplified: false });
chart.view = 'year';
chart.tasks = tasks;
chart.deadlines = {};
chart.render(tasks, 'year');

if (document.querySelector('.gantt-overlap-hatch')) {
  throw new Error('overlap hatch rectangle should not be drawn');
}

const texts = [...document.querySelectorAll('#bars-svg text')].map((t) => t.textContent);
const badge = texts.find((t) => t && t.includes('Sovrapposizione risorse'));
if (!badge) {
  throw new Error('missing overlap badge, texts=' + JSON.stringify(texts));
}
if (badge !== 'Sovrapposizione risorse C205 - 2 - Montaggio') {
  throw new Error('unexpected overlap badge text: ' + badge);
}

console.log('ok', badge);
