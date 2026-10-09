/* Helpers shared by every page. Loaded by base.html before the page scripts,
 * so templates use them as globals and define nothing of their own. */

/** HTML-escape a value; null and undefined become the empty string. */
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** HTML-escape a value; null and undefined become an em dash. */
function escOrDash(v) {
  return v == null ? '—' : esc(v);
}

/** Coloured plant chip. Colours come from window.STAB_COLORS, set by the page. */
function stabBadge(name, extraClass) {
  if (!name) return '—';
  const c = (window.STAB_COLORS || {})[name];
  const style = c ? ` style="--stab-c:${c}"` : '';
  const cls = extraClass ? ` ${extraClass}` : '';
  return `<span class="stab-badge${cls}"${style}>${esc(name)}</span>`;
}

/** CSRF token for fetch(): a rendered form field first, else the cookie. */
function getCsrfToken() {
  const input = document.querySelector('input[name=csrfmiddlewaretoken]');
  if (input && input.value) return input.value;
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

/** Whole progress percentage, as the server's progress_percent: halves
 *  round up and 100 is reached only once every working day is done. */
function progressPercent(done, total) {
  if (!total) return 0;
  const pct = Math.round(done / total * 100);
  return done < total ? Math.min(pct, 99) : pct;
}

/** Parse YYYY-MM-DD (longer ISO strings are cut to the day) to a local
 *  noon Date, so day arithmetic never crosses a DST boundary. Null when
 *  unreadable. */
function parseIsoDay(raw) {
  const s = String(raw || '').trim().slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return null;
  const d = new Date(`${s}T12:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}
