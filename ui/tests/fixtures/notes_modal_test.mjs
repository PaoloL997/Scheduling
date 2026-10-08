/* Renders the pianificazione notes modal with a stubbed problems API.

   The modal script lives inside the Django template, so the test extracts the
   page's <script> block, neutralises the `{{ ... }}` context values and runs it
   in a jsdom page.
*/
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const tpl = readFileSync(
  join(root, 'ui', 'templates', 'ui', 'pianificazione.html'),
  'utf8'
);

// Helpers every page shares (esc, stabBadge, getCsrfToken, parseIsoDay) are
// served by base.html from common.js: load them before the page script.
const commonSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'common.js'), 'utf8');

const scriptBlocks = [...tpl.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const js = commonSrc + '\n' + scriptBlocks
  .sort((a, b) => b.length - a.length)[0]
  // Django context values: an empty list satisfies both the `.length` and the
  // lookup-table uses in the page init code.
  .replace(/\{\{[^}]*\}\}/g, '[]');

const PROPOSAL = {
  status: 'proposed',
  blocked_pk: 39,
  meets_deadline: true,
  moved_count: 1,
  blocked: {
    start: '2026-09-08',
    finish: '2026-09-17',
    resource_labels: ['Generatore WO408'],
  },
};

function problem(id, tipo, risolto, proposta) {
  return {
    id,
    tipo,
    risorsa_tipo: 'generatore',
    risorsa_uid: 'WO408',
    stabilimento_origine: 'Valbrembo',
    stabilimento_attivita: 'Valbrembo',
    bloccata_fino: null,
    riferimento_attivita: '',
    descrizione: `nota ${id}`,
    proposta_json: proposta || null,
    risolto,
    note_risoluzione: '',
    data_risoluzione: null,
    created_at: `2026-09-0${id}T08:00:00`,
  };
}

async function renderModal(problemi, proposta) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div id="det-modal-overlay"></div>
      <tr data-orm-id="39" data-activity-id="25078-02-SNA-produzione"></tr>
      <div id="notes-modal-overlay">
        <div id="notes-modal-title"></div>
        <div id="notes-modal-subtitle"></div>
        <div id="notes-modal-body"></div>
      </div>
    </body></html>`,
    { pretendToBeVisual: true, url: 'http://localhost/', runScripts: 'dangerously' }
  );
  const win = dom.window;
  win.ResizeObserver = class { observe() {} disconnect() {} };
  win.alert = () => {};
  const calls = [];
  win.fetch = async (url) => {
    calls.push(String(url));
    const body = String(url).includes('/riprogramma/proposta/')
      ? { ok: true, proposta: proposta === undefined ? PROPOSAL : proposta }
      : { ok: true, problemi };
    return { ok: true, json: async () => body };
  };
  // The compare Gantt is exercised by the gantt_*.mjs fixtures.
  win.GanttChart = undefined;

  const el = win.document.createElement('script');
  el.textContent = js;
  win.document.body.appendChild(el);
  if (typeof win.openNotesModal !== 'function') {
    throw new Error('openNotesModal is not defined: script block not loaded');
  }
  await win.openNotesModal(39);
  const doc = win.document;
  return {
    calls,
    pending: doc.querySelectorAll('.reschedule-pending').length,
    proposals: doc.querySelectorAll('.reschedule-proposal').length,
    dates: [...doc.querySelectorAll('.reschedule-dates')].map(e => e.textContent),
    types: [...doc.querySelectorAll('.note-item__type')].map(e => e.textContent),
    states: [...doc.querySelectorAll('.note-item__state')].map(e => e.textContent),
  };
}

// 25078-02-SNA: nothing overlaps, so the only open row is `ritardo` — the
// proposal must still be shown.
const ritardoOnly = await renderModal([
  problem(1, 'acquisto', true, null),
  problem(2, 'spostamento', false, null),
  problem(3, 'ritardo', false, null),
]);
if (!ritardoOnly.calls.some(u => u.includes('/riprogramma/proposta/'))) {
  throw new Error('the modal must solve the proposal when it opens');
}
if (ritardoOnly.pending !== 0) {
  throw new Error('the pending placeholder must be replaced by the result');
}
if (ritardoOnly.proposals !== 1) {
  throw new Error(`expected the proposal on a ritardo-only activity, got ${ritardoOnly.proposals}`);
}
if (!ritardoOnly.dates[0].includes('17/09/2026')) {
  throw new Error(`proposal dates not rendered: ${ritardoOnly.dates}`);
}
if (ritardoOnly.types.includes('ritardo')) {
  throw new Error('the ritardo note shows its raw type key instead of a label');
}

// 25078-02-SNC: the same payload is stored on every open reschedulable row, so
// the card must be drawn once — two of them would duplicate the compare Gantt.
const bothRows = await renderModal([
  problem(1, 'sovrapposizione', false, null),
  problem(2, 'ritardo', false, null),
]);
if (bothRows.proposals !== 1) {
  throw new Error(`expected exactly one proposal card, got ${bothRows.proposals}`);
}

// A rejected proposal stays hidden, and so does a solver that found nothing:
// the placeholder must go away in both cases.
for (const status of ['rejected', 'no_proposal', 'no_problem']) {
  const none = await renderModal(
    [problem(1, 'ritardo', false, null)],
    { status }
  );
  if (none.proposals !== 0 || none.pending !== 0) {
    throw new Error(`status ${status} must leave no card behind`);
  }
}

// Open notes first, resolved ones after; creation order kept inside each group.
const ordered = await renderModal([
  problem(1, 'acquisto', true, null),
  problem(2, 'spostamento', false, null),
  problem(3, 'acquisto', true, null),
  problem(4, 'ritardo', false, null),
]);
const expected = ['Aperta', 'Aperta', 'Risolta', 'Risolta'];
if (JSON.stringify(ordered.states) !== JSON.stringify(expected)) {
  throw new Error(`open notes must come first, got ${ordered.states.join(',')}`);
}
if (ordered.types[0] !== 'Spostamento richiesto' || ordered.types[1] !== 'Attività in ritardo') {
  throw new Error(`creation order lost inside the open group: ${ordered.types.join(',')}`);
}

console.log('ok', {
  ritardoOnly: ritardoOnly.proposals,
  bothRows: bothRows.proposals,
  order: ordered.states,
});
