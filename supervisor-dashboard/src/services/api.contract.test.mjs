/**
 * Phase 6E: dashboard API-client contract tests (Django session authentication).
 *
 * Runs on Node's BUILT-IN test runner (node --test) against the real
 * services/api.js module. No new dependency: fetch, document (cookies), URL and
 * localStorage are stubbed here.
 *
 *     npm test        (node --test src/services/api.contract.test.mjs)
 *
 * What these pin down:
 *   - login sends ONLY username + password, in the body, never in a URL
 *   - the dashboard generates NO Authorization: Token header
 *   - every request uses credentials:'include' so the session cookie travels
 *   - unsafe requests carry Django's X-CSRFToken
 *   - the password is never persisted anywhere
 *   - a refused read surfaces as a typed ApiAuthError, not as empty data
 *   - authenticated state restores through /api/auth/me/
 */

import assert from 'node:assert/strict';
import test from 'node:test';

// --- minimal browser environment -------------------------------------------
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

let cookieJar = '';
globalThis.document = {
  get cookie() { return cookieJar; },
  set cookie(v) { cookieJar = v; },
  createElement: () => {
    const el = { href: '', download: '', rel: '', click() { clicked.push({ ...el }); }, remove() {} };
    return el;
  },
  body: { appendChild() {} },
};

let created = [];
let revoked = [];
const clicked = [];
globalThis.URL.createObjectURL = () => { const u = `blob:mock/${created.length}`; created.push(u); return u; };
globalThis.URL.revokeObjectURL = (u) => revoked.push(u);

// --- recording fetch stub ---------------------------------------------------
let calls = [];
let handler = () => ({ status: 200, body: {} });

function installRecordingFetch() {
  globalThis.fetch = async (url, options = {}) => {
    calls.push({
      url,
      method: options.method || 'GET',
      headers: options.headers || {},
      credentials: options.credentials,
      body: options.body,
    });
    const r = handler(url, options);
    return {
      status: r.status,
      ok: r.status >= 200 && r.status < 300,
      headers: { get: (h) => (r.headers || {})[h] ?? null },
      json: async () => r.body,
      blob: async () => r.blob ?? { size: 1234, type: 'application/pdf' },
    };
  };
}
installRecordingFetch();

const { apiService, ApiAuthError, clearApiCache } = await import('./api.js');

const CSRF = 'csrf-token-xyz789';
const PASSWORD = 'supervisor-secret-pw';

function reset({ withCsrf = true } = {}) {
  calls = []; created = []; revoked = []; clicked.length = 0;
  cookieJar = withCsrf ? `csrftoken=${CSRF}` : '';
  store.clear();
  installRecordingFetch();
  clearApiCache();
}

const authHeaderOf = (c) => c.headers.Authorization || c.headers.authorization;
const csrfOf = (c) => c.headers['X-CSRFToken'];

// --- login: username + password only ---------------------------------------

test('login sends only username and password, in the body', async () => {
  reset();
  handler = () => ({ status: 200, body: { username: 'rtgs_supervisor', role: 'SUPERVISOR', is_supervisor: true } });

  const me = await apiService.login('rtgs_supervisor', PASSWORD);
  assert.equal(me.username, 'rtgs_supervisor');

  const call = calls.find((c) => c.url === '/api/auth/login/');
  assert.ok(call, 'login endpoint was not called');
  assert.equal(call.method, 'POST');
  assert.deepEqual(JSON.parse(call.body), { username: 'rtgs_supervisor', password: PASSWORD });
  assert.equal(Object.keys(JSON.parse(call.body)).length, 2, 'login body carries extra fields');
});

test('the password never appears in any URL', async () => {
  reset();
  handler = () => ({ status: 200, body: {} });
  await apiService.login('rtgs_supervisor', PASSWORD);
  await apiService.getBlocks();
  for (const c of calls) {
    assert.ok(!String(c.url).includes(PASSWORD), `password leaked into URL: ${c.url}`);
  }
});

test('the password is never persisted to storage', async () => {
  reset();
  handler = () => ({ status: 200, body: {} });
  await apiService.login('rtgs_supervisor', PASSWORD);
  const dumped = JSON.stringify([...store.entries()]);
  assert.ok(!dumped.includes(PASSWORD), 'password was written to storage');
  assert.equal(store.size, 0, 'the dashboard should persist nothing itself');
});

test('login fetches a CSRF cookie first when none is held', async () => {
  reset({ withCsrf: false });
  handler = (url) => {
    if (url === '/api/auth/csrf/') { cookieJar = `csrftoken=${CSRF}`; return { status: 200, body: {} }; }
    return { status: 200, body: { username: 'u' } };
  };
  await apiService.login('u', 'p');
  assert.equal(calls[0].url, '/api/auth/csrf/', 'CSRF cookie was not obtained before login');
  assert.equal(csrfOf(calls.at(-1)), CSRF, 'login did not send X-CSRFToken');
});

test('invalid credentials surface as a typed ApiAuthError', async () => {
  reset();
  handler = () => ({ status: 401, body: { error: 'Invalid username or password.' } });
  await assert.rejects(() => apiService.login('u', 'bad'), (e) => {
    assert.ok(e instanceof ApiAuthError);
    assert.equal(e.status, 401);
    assert.match(e.message, /invalid username or password/i);
    return true;
  });
});

// --- no bearer token anywhere ----------------------------------------------

test('the dashboard never generates an Authorization: Token header', async () => {
  reset();
  handler = () => ({ status: 200, body: {}, headers: {} });
  await apiService.login('u', 'p');
  await apiService.getBlocks();
  await apiService.getAuditLogs('Block4');
  await apiService.getRevenueSummary();
  await apiService.approveBlock('Block4', { approval_status: 'approved', reason: 'x' });
  await apiService.createAssessment({ block_id: 'Block4', granite_category: 'Others' });
  await apiService.downloadBlockPdf('Block4');

  for (const c of calls) {
    assert.equal(authHeaderOf(c), undefined, `Authorization header sent to ${c.url}`);
  }
});

// --- session cookie + CSRF ---------------------------------------------------

test("every request uses credentials:'include'", async () => {
  reset();
  handler = () => ({ status: 200, body: {}, headers: {} });
  await apiService.getBlocks();
  await apiService.getExecutiveOverview();
  await apiService.whoAmI();
  await apiService.approveBlock('Block4', {});
  await apiService.downloadBlockPdf('Block4');
  for (const c of calls) {
    assert.equal(c.credentials, 'include', `credentials not included for ${c.url}`);
  }
});

test('unsafe requests carry the CSRF token, safe ones need not', async () => {
  reset();
  handler = () => ({ status: 200, body: {} });
  await apiService.approveBlock('Block4', {});
  await apiService.overrideBlock('Block4', {});
  await apiService.createAssessment({ block_id: 'Block4', granite_category: 'Others' });
  await apiService.logout();
  for (const c of calls.filter((x) => x.method === 'POST')) {
    assert.equal(csrfOf(c), CSRF, `no CSRF token on POST ${c.url}`);
  }
});

// --- session restore ---------------------------------------------------------

test('authenticated state restores through /api/auth/me/', async () => {
  reset();
  handler = () => ({ status: 200, body: { username: 'rtgs_supervisor', role: 'SUPERVISOR', is_supervisor: true } });
  const me = await apiService.whoAmI();
  assert.equal(me.username, 'rtgs_supervisor');
  assert.equal(me.is_supervisor, true);
  const call = calls.find((c) => c.url === '/api/auth/me/');
  assert.equal(call.credentials, 'include');
  assert.equal(authHeaderOf(call), undefined);
});

test('no session means whoAmI raises ApiAuthError, not empty data', async () => {
  reset();
  handler = () => ({ status: 401, body: {} });
  await assert.rejects(() => apiService.whoAmI(), (e) => {
    assert.ok(e instanceof ApiAuthError);
    assert.equal(e.status, 401);
    return true;
  });
});

test('logout calls the backend and clears cached data', async () => {
  reset();
  handler = () => ({ status: 200, body: { total_seigniorage: 56.07 } });
  await apiService.getRevenueSummary();
  const before = calls.length;
  await apiService.getRevenueSummary();               // served from cache
  assert.equal(calls.length, before, 'cache should have served the repeat read');

  await apiService.logout();
  assert.ok(calls.some((c) => c.url === '/api/auth/logout/' && c.method === 'POST'));

  await apiService.getRevenueSummary();
  assert.ok(calls.length > before + 1, 'logout did not clear the cache');
});

// --- error model preserved ----------------------------------------------------

test('a refused read raises ApiAuthError rather than returning empty data', async () => {
  reset();
  handler = () => ({ status: 401, body: {} });
  for (const read of [
    () => apiService.getBlocks(),
    () => apiService.getExecutiveOverview(),
    () => apiService.getAuditLogs('Block4'),
    () => apiService.getRevenueSummary(),
  ]) {
    await assert.rejects(read, (e) => {
      assert.ok(e instanceof ApiAuthError, 'expected a typed ApiAuthError');
      assert.equal(e.status, 401);
      return true;
    });
  }
});

test('403 is reported as a permission problem, distinct from 401', async () => {
  reset();
  handler = () => ({ status: 403, body: {} });
  await assert.rejects(() => apiService.getRevenueSummary(), (e) => {
    assert.ok(e instanceof ApiAuthError);
    assert.equal(e.status, 403);
    assert.match(e.message, /permission/i);
    return true;
  });
});

test('a network failure is distinct from an auth failure', async () => {
  reset();
  globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(() => apiService.getRevenueSummary(), (e) => {
    assert.ok(!(e instanceof ApiAuthError));
    assert.match(e.message, /Unable to reach the server/i);
    return true;
  });
});

test('a refused read is not cached as a result', async () => {
  reset();
  handler = () => ({ status: 401, body: {} });
  await assert.rejects(() => apiService.getRevenueSummary());
  handler = () => ({ status: 200, body: { total_seigniorage: 56.07 } });
  const data = await apiService.getRevenueSummary();
  assert.equal(data.total_seigniorage, 56.07);
});

// --- protected data loads after login ----------------------------------------

test('protected dashboard data loads after login', async () => {
  reset();
  handler = (url) => {
    if (url === '/api/auth/login/') return { status: 200, body: { username: 'rtgs_supervisor', is_supervisor: true } };
    return { status: 200, body: [{ block_id: 'Block4', approval_status: 'approved', approved_by: 'rtgs_supervisor',
      measurement: { length_m: 0.3719, breadth_m: 0.3556, height_m: 0.2181 } }] };
  };
  await apiService.login('rtgs_supervisor', PASSWORD);
  const blocks = await apiService.getBlocks();
  assert.equal(blocks[0].block_id, 'Block4');
  assert.equal(blocks[0].approved_by, 'rtgs_supervisor');
  assert.equal(blocks[0].measurement.length_m, 0.3719);
});

// --- PDF over the session -----------------------------------------------------

test('PDF download uses the session, no token, no credentials in the URL', async () => {
  reset();
  handler = () => ({ status: 200, body: {}, headers: { 'Content-Disposition': 'inline; filename="inspection_report_Block4.pdf"' } });

  const filename = await apiService.downloadBlockPdf('Block4');
  const call = calls.at(-1);
  assert.equal(call.url, '/api/blocks/Block4/pdf/');
  assert.equal(call.credentials, 'include');
  assert.equal(authHeaderOf(call), undefined);
  assert.ok(!call.url.includes('?'), 'download URL must carry no query string');
  assert.ok(!call.url.includes(PASSWORD));
  assert.equal(filename, 'inspection_report_Block4.pdf');
});

test('PDF download creates a Blob URL, triggers the save, then revokes it', async () => {
  reset();
  handler = () => ({ status: 200, body: {}, headers: {} });
  await apiService.downloadBlockPdf('Block4');
  assert.equal(created.length, 1);
  assert.equal(clicked.length, 1);
  assert.equal(clicked[0].href, created[0]);
  const mine = created[0];
  await new Promise((r) => setTimeout(r, 1100));
  assert.ok(revoked.includes(mine), 'the Blob URL must be revoked after use');
});

test('PDF download maps each failure to a clear, distinct message', async () => {
  for (const [status, type, pattern] of [
    [401, ApiAuthError, /session expired/i],
    [403, ApiAuthError, /not authorized/i],
    [404, Error, /block not found/i],
    [500, Error, /unable to export pdf/i],
  ]) {
    reset();
    handler = () => ({ status, body: {} });
    await assert.rejects(() => apiService.downloadBlockPdf('Block4'), (e) => {
      assert.ok(e instanceof type, `status ${status}: wrong error type`);
      assert.match(e.message, pattern, `status ${status}: unclear message`);
      return true;
    });
  }
});

test('an unassessed block is NOT an export error any more', async () => {
  // Phase 6F: every registered block exports. The client must not surface
  // "no assessment" as a failure - the backend returns a measurement record.
  reset();
  handler = () => ({
    status: 200, body: {},
    headers: { 'Content-Disposition': 'inline; filename="measurement_record_Block3.pdf"' },
  });
  const filename = await apiService.downloadBlockPdf('Block3');
  assert.equal(filename, 'measurement_record_Block3.pdf');
  assert.equal(clicked.length, 1, 'the download should have been triggered');
});

test('export works for an ARBITRARY block id, with nothing hardcoded', async () => {
  // Guards against a fixed list of known blocks creeping in: a brand-new field
  // submission must be exportable the moment it appears in the registry.
  const ids = [`GR-${Date.now()}`, 'block-with-dashes-42', 'X', '99999999'];
  for (const id of ids) {
    reset();
    handler = () => ({ status: 200, body: {}, headers: {} });
    const filename = await apiService.downloadBlockPdf(id);
    const call = calls.at(-1);
    assert.equal(call.url, `/api/blocks/${id}/pdf/`, `wrong endpoint for ${id}`);
    assert.equal(call.credentials, 'include');
    assert.equal(authHeaderOf(call), undefined, 'no bearer token may be sent');
    assert.equal(filename, `inspection_report_${id}.pdf`, 'fallback filename');
  }
});

test('the client contains no hardcoded block identifiers', async () => {
  const fs = await import('node:fs');
  const source = fs.readFileSync(new URL('./api.js', import.meta.url), 'utf8');
  for (const id of ['block2', 'Block3', 'Block4', 'block5']) {
    assert.ok(!source.includes(id), `api.js hardcodes block id ${id}`);
  }
});

// --- Phase 6G: approval workflow responses ----------------------------------

test('assessment-required refusal gets its own workflow message, not a raw error', async () => {
  reset();
  handler = () => ({
    status: 409,
    body: {
      error: "Block 'X' must have an assessment before it can be approved.",
      code: 'assessment_required_before_approval',
    },
  });
  await assert.rejects(
    () => apiService.approveBlock('X', { approval_status: 'approved', reason: 'r' }),
    (e) => {
      assert.equal(e.code, 'assessment_required_before_approval');
      assert.match(e.message, /assessment required before approval/i);
      assert.ok(!(e instanceof ApiAuthError), 'a workflow precondition is not an auth failure');
      return true;
    });
});

test('already-approved comes back as a normal result, not an error', async () => {
  reset();
  handler = () => ({
    status: 200,
    body: {
      code: 'already_approved',
      detail: "Block 'X' is already approved. No new decision was recorded.",
      block_id: 'X', approval_status: 'approved', approved_by: 'rtgs_supervisor',
    },
  });
  const result = await apiService.approveBlock('X', { approval_status: 'approved', reason: 'r' });
  assert.equal(result.code, 'already_approved');
  assert.equal(result.approved_by, 'rtgs_supervisor');
});

test('approval still sends the session and CSRF, and never an actor field', async () => {
  reset();
  handler = () => ({ status: 200, body: {} });
  await apiService.approveBlock('X', { approval_status: 'approved', reason: 'r' });
  const call = calls.at(-1);
  assert.equal(call.url, '/api/blocks/X/approve/');
  assert.equal(call.credentials, 'include');
  assert.equal(csrfOf(call), CSRF);
  assert.equal(authHeaderOf(call), undefined);
  const body = JSON.parse(call.body);
  assert.ok(!('actor' in body), 'the client must never send an actor identity');
  assert.ok(!('username' in body));
});
