/* The detail panel refuses to start an activity without its minimum kit.

   The panel script lives inside the Django template, so the test extracts the
   page's <script> block, neutralises the `{{ ... }}` context values and runs it
   in a jsdom page — same approach as notes_modal_test.mjs.
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
  .replace(/\{\{[^}]*\}\}/g, '[]');

const FULL_KIT = {
  testa: ['T1'], mandrino: ['M1'], tastatore: ['TA1'],
  maschera: ['MA1'], generatore: ['G1'],
};
const MIN_REQUIREMENTS = {
  teste: 1, mandrini: 1, tastatori: 1, maschere: 1, generatori: 1,
};

/* Build the page, open the detail modal on one activity row, switch to edit
   mode, type an actual start and hit Salva. */
async function startActivity({ resources, minRequirements, actualStart }) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div id="pian-group-list"></div>
      <div class="pian-activity-row"
           data-source="attivita" data-orm-id="39"
           data-activity-id="25078-02-produzione" data-job-id="25078"
           data-commessa="25078" data-item="02" data-activity="produzione"
           data-start="2026-06-01" data-finish="2026-06-05"
           data-actual-start="" data-actual-finish=""
           data-plant="Valbrembo" data-responsible="Rossi"
           data-resources="" data-resources-typed='${JSON.stringify(resources)}'
           data-min-requirements='${JSON.stringify(minRequirements)}'
           data-modello-testa="HT200" data-predecessors="">
      </div>
      <div id="det-modal-overlay">
        <h2 id="det-title"></h2>
        <div id="det-body" class="det-grid"></div>
        <div id="det-error" class="det-error" style="display:none;"></div>
        <div id="det-view-btns"></div>
        <div id="det-edit-btns" style="display:none;">
          <button id="det-save-btn">Salva</button>
        </div>
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
    return { ok: true, status: 200, json: async () => ({ ok: true, predecessors: [] }) };
  };

  const el = win.document.createElement('script');
  el.textContent = js;
  win.document.body.appendChild(el);
  if (typeof win.saveEdit !== 'function') {
    throw new Error('saveEdit is not defined: script block not loaded');
  }

  const row = win.document.querySelector('.pian-activity-row');
  win.openDetailModal(row, 'task');
  await win.enterEditMode();
  const input = win.document.querySelector('.det-input[name="actualStart"]');
  if (!input) throw new Error('the «Inizio effett.» input was not rendered');
  input.value = actualStart;
  await win.saveEdit();

  const err = win.document.getElementById('det-error');
  return {
    saved: calls.some(u => u.includes('/api/tasks/update/')),
    error: err.style.display === 'none' ? '' : err.textContent,
    hint: (win.document.querySelector('.s6-shortfall-hint') || {}).textContent || '',
    missing: win._missingMinimumResources(JSON.stringify(minRequirements), {
      teste: resources.testa || [], mandrini: resources.mandrino || [],
      tastatori: resources.tastatore || [], maschere: resources.maschera || [],
      generatori: resources.generatore || [],
    }),
  };
}

// No resources at all: the start never reaches the server.
const empty = await startActivity({
  resources: {},
  minRequirements: MIN_REQUIREMENTS,
  actualStart: '2026-06-02',
});
if (empty.saved) {
  throw new Error('the start was sent to the server without the minimum kit');
}
if (!empty.error.includes('Impossibile avviare')) {
  throw new Error(`the block must be explained to the user, got «${empty.error}»`);
}
if (!empty.error.includes('Testa 0/1') || !empty.error.includes('Generatore 0/1')) {
  throw new Error(`the message must name what is missing: ${empty.error}`);
}
if (!empty.hint.includes('Avvio bloccato')) {
  throw new Error('the «Inizio effett.» field must warn before the user types');
}
if (empty.missing.length !== 5) {
  throw new Error(`expected five shortfalls, got ${empty.missing.length}`);
}

// One generatore short of the configured minimum: still blocked.
const noGenerator = await startActivity({
  resources: { ...FULL_KIT, generatore: [] },
  minRequirements: MIN_REQUIREMENTS,
  actualStart: '2026-06-02',
});
if (noGenerator.saved) {
  throw new Error('the start was sent without a generatore');
}
if (!noGenerator.error.includes('Generatore 0/1')) {
  throw new Error(`only the missing generatore must be reported: ${noGenerator.error}`);
}

// Two mandrini required by the testa configuration, one booked.
const shortMandrini = await startActivity({
  resources: FULL_KIT,
  minRequirements: { ...MIN_REQUIREMENTS, mandrini: 2 },
  actualStart: '2026-06-02',
});
if (shortMandrini.saved) {
  throw new Error('the start was sent below the configured mandrini minimum');
}
if (!shortMandrini.error.includes('Mandrino 1/2')) {
  throw new Error(`the shortfall count must be shown: ${shortMandrini.error}`);
}

// Complete kit: the request goes through.
const complete = await startActivity({
  resources: FULL_KIT,
  minRequirements: MIN_REQUIREMENTS,
  actualStart: '2026-06-02',
});
if (!complete.saved) {
  throw new Error(`an equipped activity must be startable, got «${complete.error}»`);
}
if (complete.hint) {
  throw new Error('no warning must be shown when the kit is complete');
}

// Editing something else on an under-equipped activity is not blocked.
const otherEdit = await startActivity({
  resources: {},
  minRequirements: MIN_REQUIREMENTS,
  actualStart: '',
});
if (!otherEdit.saved) {
  throw new Error('only the start is guarded, other edits must go through');
}

console.log('ok', {
  blocked: [empty.saved, noGenerator.saved, shortMandrini.saved],
  allowed: [complete.saved, otherEdit.saved],
});
