/* The detail panel lets the TP60 Custom flag be changed on an existing activity.

   Same approach as avvio_risorse_minime_test.mjs: the page's <script> block is
   extracted from the Django template and run in a jsdom page.
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
const commonSrc = readFileSync(join(root, 'ui', 'static', 'ui', 'common.js'), 'utf8');

const scriptBlocks = [...tpl.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const js = commonSrc + '\n' + scriptBlocks
  .sort((a, b) => b.length - a.length)[0]
  .replace(/\{\{[^}]*\}\}/g, '[]');

const PLAIN_MINS = { teste: 1, mandrini: 1, tastatori: 1, maschere: 1, generatori: 1 };
const TP60_MINS = { teste: 1, mandrini: 2, tastatori: 0, maschere: 1, generatori: 1 };
const NO_TASTATORE = {
  testa: ['T1'], mandrino: ['M1', 'M2'], tastatore: [],
  maschera: ['MA1'], generatore: ['G1'],
};

/* Open the panel on one row, switch to edit mode, optionally tick/untick the
   flag and type an actual start, then hit Salva. */
async function editActivity({ stored, tick, actualStart = '' }) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div id="pian-group-list"></div>
      <div class="pian-activity-row"
           data-source="attivita" data-orm-id="52"
           data-activity-id="25078-04-produzione" data-job-id="25078"
           data-commessa="25078" data-item="04" data-activity="produzione"
           data-start="2026-06-01" data-finish="2026-06-05"
           data-actual-start="" data-actual-finish=""
           data-plant="Valbrembo" data-responsible="Rossi"
           data-resources="" data-resources-typed='${JSON.stringify(NO_TASTATORE)}'
           data-min-requirements='${JSON.stringify(stored ? TP60_MINS : PLAIN_MINS)}'
           data-tp60-custom="${stored ? '1' : ''}"
           data-modello-testa="TP60" data-predecessors="">
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
  // jsdom has no CSS.escape; the save path looks rows up with it.
  win.CSS = { escape: s => String(s).replace(/["\\]/g, '\\$&') };
  let sent = null;
  win.fetch = async (url, opts) => {
    if (String(url).includes('/api/tasks/update/')) {
      sent = JSON.parse(opts.body);
      const flag = 'tp60_custom' in sent ? sent.tp60_custom : stored;
      return {
        ok: true, status: 200,
        json: async () => ({
          ok: true, item: '04', job_id: '25078',
          activity_id: '25078-04-produzione',
          task: { min_requirements: flag ? TP60_MINS : PLAIN_MINS, state_key: 'pianificata' },
        }),
      };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true, predecessors: [] }) };
  };

  const el = win.document.createElement('script');
  el.textContent = js;
  win.document.body.appendChild(el);

  const row = win.document.querySelector('.pian-activity-row');
  win.openDetailModal(row, 'task');
  const viewText = win.document.getElementById('det-body').textContent;
  await win.enterEditMode();
  const box = win.document.querySelector('#det-body input[type="checkbox"][name="tp60Custom"]');
  if (!box) throw new Error('the TP60 Custom checkbox was not rendered in edit mode');
  const initiallyChecked = box.checked;
  if (tick != null) box.checked = tick;
  if (actualStart) {
    win.document.querySelector('.det-input[name="actualStart"]').value = actualStart;
  }
  await win.saveEdit();

  const err = win.document.getElementById('det-error');
  return {
    viewText,
    initiallyChecked,
    sent,
    error: err.style.display === 'none' ? '' : err.textContent,
    rowFlag: row.dataset.tp60Custom,
    rowMins: JSON.parse(row.dataset.minRequirements || '{}'),
    afterText: win.document.getElementById('det-body').textContent,
  };
}

// Off → on: the flag is sent and the row takes the new minimums.
const on = await editActivity({ stored: false, tick: true });
if (!on.viewText.includes('TP60 Custom') || !on.viewText.includes('No')) {
  throw new Error(`view mode must show the flag as «No»: ${on.viewText}`);
}
if (on.initiallyChecked) throw new Error('an unflagged activity must start unticked');
if (!on.sent || on.sent.tp60_custom !== true) {
  throw new Error(`ticking must send tp60_custom=true, got ${JSON.stringify(on.sent)}`);
}
if (on.rowFlag !== '1') throw new Error(`the row must carry the flag, got «${on.rowFlag}»`);
if (on.rowMins.mandrini !== 2 || on.rowMins.tastatori !== 0) {
  throw new Error(`the row must take the TP60 minimums: ${JSON.stringify(on.rowMins)}`);
}
if (on.error) throw new Error(`the save must succeed, got «${on.error}»`);
if (!on.afterText.includes('Sì')) throw new Error('view mode must show «Sì» after saving');

// On → off.
const off = await editActivity({ stored: true, tick: false });
if (!off.initiallyChecked) throw new Error('a flagged activity must start ticked');
if (!off.sent || off.sent.tp60_custom !== false) {
  throw new Error(`unticking must send tp60_custom=false, got ${JSON.stringify(off.sent)}`);
}
if (off.rowFlag !== '') throw new Error(`the row must drop the flag, got «${off.rowFlag}»`);
if (off.rowMins.tastatori !== 1) {
  throw new Error(`the row must take back the plain minimums: ${JSON.stringify(off.rowMins)}`);
}

// Untouched: the flag is not part of the request.
const same = await editActivity({ stored: true, tick: null });
if (!same.sent) throw new Error('an untouched save must still reach the server');
if ('tp60_custom' in same.sent) {
  throw new Error('an untouched flag must not be sent');
}

// Ticking the flag while starting: the stored minimums (tastatore 1) no longer
// apply, so the local guard leaves the decision to the server.
const startTp60 = await editActivity({ stored: false, tick: true, actualStart: '2026-06-02' });
if (!startTp60.sent) {
  throw new Error(`the start with the new flag must reach the server, got «${startTp60.error}»`);
}

console.log('ok', { on: on.rowFlag, off: off.rowFlag, untouched: same.sent.tp60_custom });
