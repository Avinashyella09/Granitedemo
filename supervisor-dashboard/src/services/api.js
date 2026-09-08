const _cache = new Map();
const _CACHE_TTL_MS = 60000;

async function fetchCached(url) {
  const now = Date.now();
  if (_cache.has(url)) {
    const { timestamp, data } = _cache.get(url);
    if (now - timestamp < _CACHE_TTL_MS) {
      return data;
    }
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP error ${res.status} for ${url}`);
  const data = await res.json();
  _cache.set(url, { timestamp: Date.now(), data });
  return data;
}

export const clearApiCache = () => {
  _cache.clear();
};

export const apiService = {
  getBlocks: async () => {
    // Always fetch fresh block registry data directly from Django API
    const res = await fetch('/api/blocks/');
    if (!res.ok) throw new Error(`HTTP error ${res.status} for /api/blocks/`);
    const data = await res.json();
    // Cache under /api/blocks/ for immediate sync fallback if needed
    _cache.set('/api/blocks/', { timestamp: Date.now(), data });
    return data;
  },

  getBlock: async (blockId) => {
    const res = await fetch(`/api/blocks/${blockId}/`);
    if (!res.ok) throw new Error(`HTTP error ${res.status} for /api/blocks/${blockId}/`);
    return res.json();
  },

  approveBlock: async (blockId, payload) => {
    const res = await fetch(`/api/blocks/${blockId}/approve/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || 'Failed to approve block');
    }
    clearApiCache();
    return res.json();
  },

  overrideBlock: async (blockId, payload) => {
    const res = await fetch(`/api/blocks/${blockId}/override/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || 'Failed to override block');
    }
    clearApiCache();
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
      headers: { 'Content-Type': 'application/json' },
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
