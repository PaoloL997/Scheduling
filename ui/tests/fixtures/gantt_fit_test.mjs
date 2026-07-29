import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const ganttSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'gantt.js'), 'utf8');

function makeDom(prefix) {
  return new JSDOM(`<!DOCTYPE html><html><body>
  <div id="${prefix}-panel">
    <div id="${prefix}-bars-scroll" style="width:640px;height:320px;overflow:auto">
      <svg id="${prefix}-bars-svg"></svg>
    </div>
    <div id="${prefix}-axis-scroll"><svg id="${prefix}-axis-svg"></svg></div>
    <button class="tbtn" data-view="month"></button>
    <button class="tbtn active" data-view="year"></button>
  </div>
</body></html>`, { pretendToBeVisual: true });
}

function runBasicFitTest() {
  const dom = makeDom('gantt-before');
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
      commessa: 'TG01',
      job_id: 'j1',
      activity_id: 'a1',
      item: '01',
      activity: 'prep',
      start: '2026-06-01',
      finish: '2026-06-08',
    },
    {
      commessa: 'TG01',
      job_id: 'j1',
      activity_id: 'a2',
      item: '02',
      activity: 'work',
      start: '2026-06-09',
      finish: '2026-06-15',
    },
  ];

  const fit = GanttChart.computeFitViewport(tasks);
  if (!fit) throw new Error('computeFitViewport returned null');

  const spanDays = Math.ceil((fit.maxD - fit.minD) / 86400000);
  // Months window centred on a ~2-week span → about 3 months.
  if (spanDays < 60 || spanDays > 120) {
    throw new Error(`expected ~3-month centred window, got ${spanDays} days`);
  }
  if (fit.view !== 'month') {
    throw new Error(`expected month view, got ${fit.view}`);
  }
  // Activities (Jun 1–15) should sit near the middle of the window.
  const midAct = new Date('2026-06-08T00:00:00').getTime();
  const midWin = fit.minD.getTime() + (fit.maxD.getTime() - fit.minD.getTime()) / 2;
  const driftDays = Math.abs(midAct - midWin) / 86400000;
  if (driftDays > 20) {
    throw new Error(`window not centred on activities: drift ${driftDays.toFixed(1)} days`);
  }

  const chart = new GanttChart({ prefix: 'gantt-before', simplified: true, fitToData: false });
  chart.applyFitViewport(fit);
  chart.tasks = tasks;
  chart.deadlines = {};
  chart.renderWhenReady();

  const svg = document.getElementById('gantt-before-bars-svg');
  const rects = svg.querySelectorAll('rect[data-task]');
  if (rects.length < 2) {
    throw new Error(`expected at least 2 task bars, got ${rects.length}`);
  }

  const axisSvg = document.getElementById('gantt-before-axis-svg');
  const axisLabels = axisSvg.querySelectorAll('text');
  if (axisLabels.length > 12) {
    throw new Error(`too many axis labels: ${axisLabels.length}`);
  }
  if (axisLabels.length < 2) {
    throw new Error(`expected axis labels, got ${axisLabels.length}`);
  }

  return { spanDays, bars: rects.length, labels: axisLabels.length };
}

function runCompareModeTest() {
  const dom = makeDom('gantt-compare');
  global.window = dom.window;
  global.document = dom.window.document;
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  global.requestAnimationFrame = (cb) => { cb(); return 1; };

  eval(ganttSrc);
  const GanttChart = dom.window.GanttChart;

  const tasksBefore = [
    {
      commessa: 'TG01',
      job_id: 'j1',
      activity_id: 'a1',
      item: '01',
      activity: 'prep',
      start: '2026-06-01',
      finish: '2026-06-08',
    },
  ];
  const tasksAfter = [
    {
      commessa: 'TG01',
      job_id: 'j1',
      activity_id: 'a1',
      item: '01',
      activity: 'prep',
      start: '2026-06-03',
      finish: '2026-06-10',
    },
  ];

  const fit = GanttChart.computeFitViewport(tasksBefore, tasksAfter);
  if (!fit) throw new Error('compare computeFitViewport returned null');

  const chart = new GanttChart({
    prefix: 'gantt-compare',
    simplified: true,
    fitToData: false,
    compareMode: true,
  });
  chart.applyFitViewport(fit);
  chart.tasks = tasksBefore;
  chart.compareTasks = tasksAfter;
  chart.highlightChanges = { a1: true };
  chart.deadlines = {};
  chart.renderWhenReady();

  const svg = document.getElementById('gantt-compare-bars-svg');
  const currentBars = svg.querySelectorAll('rect.gantt-bar--current');
  const proposedBars = svg.querySelectorAll('rect.gantt-bar--proposed');
  if (currentBars.length < 1) {
    throw new Error(`expected current bar, got ${currentBars.length}`);
  }
  if (proposedBars.length < 1) {
    throw new Error(`expected proposed bar, got ${proposedBars.length}`);
  }

  const proposedStroke = proposedBars[0].getAttribute('stroke-dasharray');
  if (!proposedStroke) {
    throw new Error('expected dashed stroke on proposed bar');
  }

  return { currentBars: currentBars.length, proposedBars: proposedBars.length };
}

const basic = runBasicFitTest();
const compare = runCompareModeTest();
console.log('ok', { basic, compare });
