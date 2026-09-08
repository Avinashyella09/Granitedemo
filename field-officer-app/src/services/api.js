let apiBaseUrl = 'http://192.168.1.161:8000'; // Active LAN IP for physical device testing

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
    const res = await fetch(`${apiBaseUrl}/api/blocks/`);
    if (!res.ok) {
      throw new Error(`Failed to fetch blocks list: ${res.status}`);
    }
    return res.json();
  },

  createBlock: async (payload) => {
    const res = await fetch(`${apiBaseUrl}/api/blocks/`, {
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
      const res = await fetch(endpoint, {
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

    const res = await fetch(`${apiBaseUrl}/api/blocks/${blockId}/measure-cv/`, {
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
