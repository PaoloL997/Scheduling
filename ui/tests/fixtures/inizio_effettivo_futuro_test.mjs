/* The detail panel refuses an «inizio effettivo» dated after today.

   The panel script lives inside the Django template, so the test extracts the
   page's <script> block, neutralises the `{{ ... }}` context values and runs it
   in a jsdom page — same approach as avvio_risorse_minime_test.mjs.
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

/* A complete kit, so only the future-date rule can block the start. */
const FULL_KIT = {
  testa: ['T1'], mandrino: ['M1'], tastatore: ['TA1'],
  maschera: ['MA1'], generatore: ['G1'],
};
const MIN_REQUIREMENTS = {
  teste: 1, mandrini: 1, tastatori: 1, maschere: 1, generatori: 1,
};

function isoShift(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const TODAY = isoShift(0);

/* Build the page, open the detail modal on one activity row, switch to edit
   mode, type an actual start and hit Salva. */
async function startActivity({ actualStart, storedActualStart = '' }) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div id="pian-group-list"></div>
      <div class="pian-activity-row"
           data-source="attivita" data-orm-id="41"
           data-activity-id="25078-03-produzione" data-job-id="25078"
           data-commessa="25078" data-item="03" data-activity="produzione"
           data-start="${isoShift(-10)}" data-finish="${isoShift(-5)}"
           data-actual-start="${storedActualStart}" data-actual-finish=""
           data-plant="Valbrembo" data-responsible="Rossi"
           data-resources="" data-resources-typed='${JSON.stringify(FULL_KIT)}'
           data-min-requirements='${JSON.stringify(MIN_REQUIREMENTS)}'
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
  const max = input.getAttribute('max') || '';
  input.value = actualStart;
  await win.saveEdit();

  const err = win.document.getElementById('det-error');
  return {
    win,
    max,
    saved: calls.some(u => u.includes('/api/tasks/update/')),
    error: err.style.display === 'none' ? '' : err.textContent,
  };
}

// Tomorrow: the start never reaches the server.
const domani = await startActivity({ actualStart: isoShift(1) });
if (domani.saved) {
  throw new Error('a start dated tomorrow was sent to the server');
}
if (!domani.error.includes('non può essere una data futura')) {
  throw new Error(`the block must be explained to the user, got «${domani.error}»`);
}
if (!domani.error.includes(isoShift(1)) || !domani.error.includes(TODAY)) {
  throw new Error(`the message must name both dates: ${domani.error}`);
}
if (domani.max !== TODAY) {
  throw new Error(`the date picker must stop at today, got max=«${domani.max}»`);
}

// A date well beyond today is refused the same way.
const lontano = await startActivity({ actualStart: isoShift(45) });
if (lontano.saved) {
  throw new Error('a far future start was sent to the server');
}

// Today is the latest accepted date.
const oggi = await startActivity({ actualStart: TODAY });
if (!oggi.saved) {
  throw new Error(`today must be accepted, got «${oggi.error}»`);
}

// A past date is what the operator normally types after the fact.
const passato = await startActivity({ actualStart: isoShift(-3) });
if (!passato.saved) {
  throw new Error(`a past start must be accepted, got «${passato.error}»`);
}

// Clearing the field is not a future date.
const vuoto = await startActivity({ actualStart: '', storedActualStart: isoShift(-3) });
if (!vuoto.saved) {
  throw new Error(`clearing the start must be accepted, got «${vuoto.error}»`);
}

// A future value already stored is echoed back untouched by the panel: the
// unrelated edit must still go through, exactly as on the server.
const eco = await startActivity({
  actualStart: isoShift(7),
  storedActualStart: isoShift(7),
});
if (!eco.saved) {
  throw new Error(`echoing a stored future start must not block, got «${eco.error}»`);
}

// The comparison itself, with an explicit reference day.
const cmp = oggi.win;
const cases = [
  ['2026-06-11', '2026-06-10', true],
  ['2026-06-10', '2026-06-10', false],
  ['2026-05-01', '2026-06-10', false],
  ['2027-01-01', '2026-12-31', true],
  ['', '2026-06-10', false],
];
for (const [iso, today, expected] of cases) {
  if (cmp._isFutureActualStart(iso, today) !== expected) {
    throw new Error(`_isFutureActualStart('${iso}', '${today}') must be ${expected}`);
  }
}

console.log('ok', {
  blocked: [domani.saved, lontano.saved],
  allowed: [oggi.saved, passato.saved, vuoto.saved, eco.saved],
});
