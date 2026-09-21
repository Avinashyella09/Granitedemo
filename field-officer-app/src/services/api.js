import { NativeModules } from 'react-native';
// TurboModule-backed accessor for the Metro URL. NativeModules.SourceCode is
// NOT populated under React Native 0.81's new architecture - verified on
// device, where it fell through to the localhost fallback. getDevServer()
// reads NativeSourceCode.getConstants().scriptURL, which is.
import getDevServer from 'react-native/Libraries/Core/Devtools/getDevServer';

// --- API host resolution -----------------------------------------------------
//
// THE API HOST IS DERIVED, NOT HARDCODED.
//
// A hardcoded LAN IP has now gone stale three times on this network
// (192.168.1.158 -> 192.168.1.184 -> 192.168.0.82). Each time, the app kept
// calling a dead address and hung until React Native's 60-second default
// timeout, surfacing as "Network request timed out" at ANALYZE BLOCK - long
// after the officer finished measuring.
//
// The Metro dev server always runs on the same machine as the Django API, and
// React Native already knows its address: NativeModules.SourceCode.scriptURL is
// the URL the JS bundle was loaded from, e.g.
//     http://192.168.0.82:8081/index.bundle?platform=ios&dev=true
// Taking the HOST from it and forcing the API PORT keeps the API host correct
// automatically whenever the network reassigns the Mac's address.
//
// The two URLs stay strictly separate, which is the point: only the host is
// shared (it is the same machine). The bundle port is Metro's; the API port is
// always API_PORT below. The API URL is never used as a bundle URL, and the
// bundle URL is never used for API calls.
const API_PORT = 8000;

const hostFromUrl = (url) => {
  if (typeof url !== 'string') return null;
  const host = url.split('://')[1]?.split('/')[0]?.split(':')[0];
  return host && host !== 'localhost' && host !== '127.0.0.1' ? host : null;
};

const deriveApiBaseUrl = () => {
  // 1. The TurboModule path (RN 0.81 new architecture).
  try {
    const dev = getDevServer();
    if (dev?.bundleLoadedFromServer) {
      const host = hostFromUrl(dev.url);
      if (host) return `http://${host}:${API_PORT}`;
    }
  } catch { /* fall through */ }

  // 2. Legacy bridge path, for completeness.
  try {
    const host = hostFromUrl(NativeModules?.SourceCode?.scriptURL);
    if (host) return `http://${host}:${API_PORT}`;
  } catch { /* fall through */ }

  // 3. Release builds have no dev server; a deployment sets the host explicitly
  //    via the login screen's server field.
  return `http://127.0.0.1:${API_PORT}`;
};

let apiBaseUrl = deriveApiBaseUrl();

// Logged once at startup so a host problem is visible in Metro immediately,
// rather than only surfacing as a timeout at ANALYZE BLOCK.
console.log(`[API] base URL resolved to ${apiBaseUrl}`);

// How long to wait before giving up on a request. React Native's default is 60
// seconds, which is far too long to leave an officer staring at a spinner when
// the host is simply unreachable - the successful round trip measured on this
// project is ~1.1 s including a 2 MB photo.
const REQUEST_TIMEOUT_MS = 20000;

const withTimeout = async (url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err && (err.name === 'AbortError' || /abort/i.test(err.message || ''))) {
      const host = apiBaseUrl.replace('http://', '');
      throw new Error(
        `The server at ${host} did not respond within ${timeoutMs / 1000} seconds.\n\n` +
        `Check that Django is running and that the phone and Mac are on the same Wi-Fi. ` +
        `The current server address can be changed on the login screen.`
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
};

export const setApiBaseUrl = (url) => {
  if (url.endsWith('/')) {
    apiBaseUrl = url.slice(0, -1);
  } else {
    apiBaseUrl = url;
  }
};

export const getApiBaseUrl = () => apiBaseUrl;

export const apiService = {
  getQuarries: async () => {
    // Quarries data (lookup mockup or fallbacks)
    return [
      { id: 'Q-9982', name: 'AP Mines - Chimakurthy Main Quarry' },
      { id: 'Q-0419', name: 'Standard Black Granite Quarry' },
      { id: 'Q-5561', name: 'Premium Galaxy Granite Mines' }
    ];
  },

  getBlocks: async () => {
    const res = await withTimeout(`${apiBaseUrl}/api/blocks/`);
    if (!res.ok) {
      throw new Error(`Failed to fetch blocks list: ${res.status}`);
    }
    return res.json();
  },

  createBlock: async (payload) => {
    const res = await withTimeout(`${apiBaseUrl}/api/blocks/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.block_id || data.error || 'Failed to register block');
    }
    return data;
  },

  /**
   * Submit a complete AR-based New Block Inspection in a single multipart POST.
   * Creates / updates the block, saves the captured image, and stores AR measurements.
   */
  submitARInspection: async (params) => {
    const {
      block_id, quarry_id, officer_id,
      gps_latitude, gps_longitude,
      length_m, breadth_m, height_m, volume_m3,
      imageUri
    } = params;

    const formData = new FormData();

    // Image file
    const uriParts = (imageUri || '').split('/');
    const fileName  = uriParts[uriParts.length - 1] || 'photo.jpg';
    const fileType  = fileName.toLowerCase().endsWith('.png') ? 'image/png' : 'image/jpeg';
    formData.append('image', { uri: imageUri, name: fileName, type: fileType });

    // Block identification
    formData.append('block_id',  block_id);
    formData.append('quarry_id', quarry_id || '');
    formData.append('officer_id', officer_id || '');

    // GPS coordinates (optional)
    if (gps_latitude  != null) formData.append('gps_latitude',  String(gps_latitude));
    if (gps_longitude != null) formData.append('gps_longitude', String(gps_longitude));

    // AR dimensions
    formData.append('length_m',  String(length_m));
    formData.append('breadth_m', String(breadth_m));
    formData.append('height_m',  String(height_m));
    formData.append('volume_m3', String(volume_m3));
    if (params.ar_points) {
      formData.append('ar_points', JSON.stringify(params.ar_points));
    }

    const endpoint = `${apiBaseUrl}/api/blocks/ar-measure/`;
    const t0 = Date.now();
    console.log(`[PERF] FormData constructed: ${t0 - (params._tStart || t0)} ms`);
    console.log(`[PERF] Upload started: ${new Date().toISOString()}`);

    try {
      const res = await withTimeout(endpoint, {
        method: 'POST',
        body: formData,
      });
      const tUpload = Date.now();
      console.log(`[PERF] Upload & Server roundtrip completed: ${tUpload - t0} ms`);

      const text = await res.text();
      let data = {};
      try {
        data = JSON.parse(text);
      } catch (jsonErr) {
        console.error(`[API] Server returned non-JSON response (${res.status}):`, text);
        throw new Error(`Server response error (${res.status}). Check server logs.`);
      }

      if (!res.ok) {
        console.error(`[API] Error ${res.status}:`, data);
        throw new Error(data.error || `Server error (${res.status}).`);
      }
      console.log(`[API] AR Inspection submitted successfully:`, data);
      return data;
    } catch (err) {
      console.error(`[API] Network error calling ${endpoint}:`, err.message || err);
      if (err.message && err.message.toLowerCase().includes('network request failed')) {
        throw new Error(`Unable to connect to Django server. Check that Django is running on ${apiBaseUrl.replace('http://', '')} and that phone and PC are on the same Wi-Fi.`);
      }
      throw err;
    }
  },

  measureBlockCV: async (blockId, imageUri) => {
    const formData = new FormData();
    
    // Resolve file details for upload
    const uriParts = imageUri.split('/');
    const fileName = uriParts[uriParts.length - 1];
    const fileType = fileName.endsWith('.png') ? 'image/png' : 'image/jpeg';
    
    formData.append('image', {
      uri: imageUri,
      name: fileName,
      type: fileType,
    });

    const res = await withTimeout(`${apiBaseUrl}/api/blocks/${blockId}/measure-cv/`, {
      method: 'POST',
      body: formData,
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || 'Computer Vision measurement failed.');
    }
    return data;
  }
};
