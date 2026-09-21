// --- Supervisor authentication (Phase 6E: Django session) --------------------
// The dashboard signs in with a USERNAME and PASSWORD and gets a Django session.
// It no longer asks a human to paste an API token, and it no longer holds a
// bearer credential at all:
//
//   * the session lives in an HttpOnly cookie the browser manages, so no
//     JavaScript here (or any script injected into this page) can read it
//   * the password is used once, in the login request body, and never stored -
//     not in localStorage, not in sessionStorage, not in a URL
//   * unsafe requests carry Django's CSRF token, read from the non-HttpOnly
//     csrftoken cookie, exactly as Django's documented flow intends
//
// Vite proxies /api to the backend, so these are same-origin requests and the
// cookies are handled by the browser. credentials:'include' is stated
// explicitly so the intent survives any future change to how this is served.
//
// Token authentication still exists on the BACKEND for other API clients; the
// dashboard simply no longer uses it.

const _cache = new Map();
const _CACHE_TTL_MS = 60000;

export class ApiAuthError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiAuthError';
    this.status = status;
  }
}

// Django's CSRF cookie is deliberately readable by JS - echoing it back in the
// X-CSRFToken header is what proves the request came from this page.
const readCookie = (name) => {
  try {
    const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : null;
  } catch {
    return null;
  }
};

const csrfHeaders = () => {
  const token = readCookie('csrftoken');
  return token ? { 'X-CSRFToken': token } : {};
};

// One safe call to obtain the CSRF cookie before the first unsafe request.
// A browser that has never contacted this backend has no csrftoken yet, and the
// login POST is itself unsafe - so this has to happen first.
const ensureCsrfCookie = async () => {
  if (readCookie('csrftoken')) return;
  try {
    await fetch('/api/auth/csrf/', { credentials: 'include' });
  } catch { /* surfaced by the caller's own request */ }
};

export const clearApiCache = () => {
  _cache.clear();
};

// Turns a privileged-write response into a clear, typed failure so the UI can
// distinguish "log in", "not permitted" and "the server broke".
const handlePrivilegedResponse = async (res, fallbackMessage) => {
  if (res.status === 401) {
    _cache.clear();
    throw new ApiAuthError('Your session has expired. Please log in again.', 401);
  }
  if (res.status === 403) {
    throw new ApiAuthError(
      'Your account does not have SUPERVISOR permission for this action.', 403);
  }
  if (!res.ok) {
    let detail = fallbackMessage;
    let code = null;
    try {
      const err = await res.json();
      detail = err.error || err.detail || JSON.stringify(err);
      code = err.code || null;
    } catch { /* non-JSON error body */ }
    // Phase 6G: a block cannot be approved before it is assessed. That is a
    // workflow precondition, not a server fault, so it gets its own wording.
    if (code === 'assessment_required_before_approval') {
      const workflow = new Error(
        'Assessment required before approval. Calculate this block\'s seigniorage ' +
        'assessment first, then approve it.');
      workflow.code = code;
      throw workflow;
    }
    const failure = new Error(detail);
    if (code) failure.code = code;
    throw failure;
  }
  return res.json();
};

// Phase 6B: shared handling for authenticated READS. A 401 or 403 must not look
// like "no data" to the UI, so it becomes a typed ApiAuthError that the caller
// can distinguish from an empty result or a server failure.
const handleReadResponse = async (res, url) => {
  if (res.status === 401) {
    _cache.clear();
    throw new ApiAuthError('Supervisor login required to view this data.', 401);
  }
  if (res.status === 403) {
    throw new ApiAuthError(
      'Your account does not have permission to view this data.', 403);
  }
  if (!res.ok) throw new Error(`HTTP error ${res.status} for ${url}`);
  return res.json();
};

// Every read goes through here, so the token is attached in exactly one place.
async function authedFetch(url) {
  let res;
  try {
    res = await fetch(url, { credentials: 'include' });
  } catch {
    // Network/DNS/offline - distinct from an auth failure and from empty data.
    throw new Error(`Unable to reach the server for ${url}`);
  }
  return handleReadResponse(res, url);
}

async function fetchCached(url) {
  const now = Date.now();
  if (_cache.has(url)) {
    const { timestamp, data } = _cache.get(url);
    if (now - timestamp < _CACHE_TTL_MS) {
      return data;
    }
  }
  // Failures are deliberately NOT cached: a 401 while logged out must not be
  // remembered as this endpoint's result once the user logs in.
  const data = await authedFetch(url);
  _cache.set(url, { timestamp: Date.now(), data });
  return data;
}


export const apiService = {
  getBlocks: async () => {
    // Always fetch fresh block registry data directly from Django API.
    // Authenticated since Phase 6B - the register carries measurements and
    // approval state.
    const data = await authedFetch('/api/blocks/');
    // Cache under /api/blocks/ for immediate sync fallback if needed
    _cache.set('/api/blocks/', { timestamp: Date.now(), data });
    return data;
  },

  getBlock: async (blockId) => {
    return authedFetch(`/api/blocks/${blockId}/`);
  },

  approveBlock: async (blockId, payload) => {
    const res = await fetch(`/api/blocks/${blockId}/approve/`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify(payload)
    });
    const data = await handlePrivilegedResponse(res, 'Failed to approve block');
    clearApiCache();
    return data;
  },

  overrideBlock: async (blockId, payload) => {
    const res = await fetch(`/api/blocks/${blockId}/override/`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify(payload)
    });
    const data = await handlePrivilegedResponse(res, 'Failed to override block');
    clearApiCache();
    return data;
  },

  // Phase 6E: username + password in, Django session out.
  //
  // The password appears here once, in the request body, and is never returned,
  // stored or logged. The response carries identity only - no token, no hash.
  login: async (username, password) => {
    await ensureCsrfCookie();
    let res;
    try {
      res = await fetch('/api/auth/login/', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
        body: JSON.stringify({ username, password }),
      });
    } catch {
      throw new Error('Unable to reach the server. Check your connection and try again.');
    }
    if (res.status === 401) {
      throw new ApiAuthError('Invalid username or password.', 401);
    }
    if (res.status === 403) {
      throw new ApiAuthError(
        'Login was rejected for security reasons. Reload the page and try again.', 403);
    }
    if (!res.ok) {
      let detail = `Login failed (${res.status}).`;
      try {
        const err = await res.json();
        detail = err.error || detail;
      } catch { /* non-JSON body */ }
      throw new Error(detail);
    }
    _cache.clear();          // never serve pre-login data to a signed-in user
    return res.json();
  },

  // Ends the Django session server-side. Nothing local to clear but the cache -
  // the session cookie is the browser's to drop.
  logout: async () => {
    try {
      await fetch('/api/auth/logout/', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      });
    } catch { /* signing out locally regardless */ }
    _cache.clear();
  },

  // Restores the session on reload: if the browser still holds a valid session
  // cookie the backend says who it belongs to, otherwise 401 and the UI shows
  // the login form. The dashboard never infers its own role.
  whoAmI: async () => {
    let res;
    try {
      res = await fetch('/api/auth/me/', { credentials: 'include' });
    } catch {
      throw new Error('Unable to reach the server.');
    }
    if (res.status === 401 || res.status === 403) {
      throw new ApiAuthError('Not signed in.', res.status);
    }
    if (!res.ok) throw new Error(`Auth check failed (${res.status}).`);
    return res.json();
  },

  getAssessment: async (blockId) => {
    return fetchCached(`/api/assessments/${blockId}/`).catch(err => {
      if (err.message.includes('404')) return null;
      throw err;
    });
  },

  createAssessment: async (payload) => {
    const res = await fetch('/api/assessments/', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || 'Failed to calculate assessment');
    }
    clearApiCache();
    return res.json();
  },

  getAuditLogs: async (blockId) => {
    return fetchCached(`/api/blocks/${blockId}/audit-logs/`);
  },

  // Phase 6B: authenticated PDF download.
  //
  // The report endpoint now requires a token, which a plain <a href> cannot
  // carry. Fetching the bytes and handing them to the browser as a Blob keeps
  // the credential in the Authorization HEADER - never in the URL, the
  // filename, the DOM or the console. A token in a query string would leak into
  // server access logs, browser history and the Referer header.
  downloadBlockPdf: async (blockId) => {
    let res;
    try {
      res = await fetch(`/api/blocks/${blockId}/pdf/`, { credentials: 'include' });
    } catch {
      throw new Error(
        'Unable to reach the server to download the report. Check your connection and try again.');
    }

    // Every registered block is exportable since Phase 6F, so "no assessment"
    // is no longer an error condition here - an unassessed block simply returns
    // a measurement record. Each remaining failure gets its own message; none
    // fails silently.
    if (res.status === 401) {
      throw new ApiAuthError('Session expired - please log in again.', 401);
    }
    if (res.status === 403) {
      throw new ApiAuthError('Not authorized to export this record.', 403);
    }
    if (res.status === 404) {
      throw new Error(`Block not found: "${blockId}".`);
    }
    if (!res.ok) {
      // Includes any 409 the backend might still raise for a block with no
      // measurement at all - surfaced with the server's own wording when it
      // sends one, rather than guessed at here.
      let detail = `Unable to export PDF (HTTP ${res.status}).`;
      try {
        const err = await res.json();
        if (err && err.error) detail = err.error;
      } catch { /* non-JSON body */ }
      throw new Error(detail);
    }

    const blob = await res.blob();

    // Preserve the server's filename when it supplies one.
    const disposition = res.headers.get('Content-Disposition') || '';
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
    const filename = (match && match[1]) || `inspection_report_${blockId}.pdf`;

    const objectUrl = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = filename;
      link.rel = 'noopener';
      document.body.appendChild(link);
      link.click();
      link.remove();
    } finally {
      // Revoked once the browser has taken the bytes, so the Blob URL does not
      // linger as a readable handle to the report.
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    }
    return filename;
  },

  getExecutiveOverview: async () => {
    return fetchCached('/api/analytics/overview/');
  },

  getOfficersAnalytics: async () => {
    return fetchCached('/api/analytics/officers/');
  },

  getOfficerWeeklyAnalytics: async (officerId) => {
    return fetchCached(`/api/analytics/officers/${officerId}/weekly/`);
  },

  getQuarriesComparison: async () => {
    return fetchCached('/api/analytics/quarries/comparison/');
  },

  getQuarryTrend: async (quarryId) => {
    return fetchCached(`/api/analytics/quarries/${quarryId}/trend/`);
  },

  getRevenueSummary: async () => {
    return fetchCached('/api/analytics/revenue/summary/');
  },

  getRevenueLeakage: async () => {
    return fetchCached('/api/analytics/revenue/leakage/');
  },

  getAuditReadiness: async () => {
    return fetchCached('/api/analytics/audit-readiness/');
  },

  getAlerts: async () => {
    return fetchCached('/api/analytics/alerts/');
  },

  getMapData: async () => {
    return fetchCached('/api/analytics/map-data/');
  }
};
