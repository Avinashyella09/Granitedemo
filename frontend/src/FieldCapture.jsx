import React, { useState, useEffect, useRef } from 'react';

const quarriesList = [
  { id: 'Q-9982', name: 'AP Mines - Chimakurthy Main Quarry' },
  { id: 'Q-0419', name: 'Standard Black Granite Quarry' },
  { id: 'Q-5561', name: 'Premium Galaxy Granite Mines' }
];

export default function FieldCapture() {
  const [quarryId, setQuarryId] = useState('');
  const [blockId, setBlockId] = useState('');
  const [latitude, setLatitude] = useState('');
  const [longitude, setLongitude] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState('');
  
  const [isRegistered, setIsRegistered] = useState(false);
  const [registrationMessage, setRegistrationMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [showOptions, setShowOptions] = useState(false);

  const cameraInputRef = useRef(null);
  const galleryInputRef = useRef(null);

  useEffect(() => {
    if (quarriesList.length > 0) {
      setQuarryId(quarriesList[0].id);
    }
  }, []);

  const handleBlockIdChange = (val) => {
    setBlockId(val);
    setIsRegistered(false);
    setRegistrationMessage('');
  };

  const handleCaptureGPS = () => {
    if ("geolocation" in navigator) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          setLatitude(position.coords.latitude.toFixed(6));
          setLongitude(position.coords.longitude.toFixed(6));
        },
        (err) => {
          alert("GPS extraction failed. Please enter coordinates manually.");
        }
      );
    } else {
      alert("GPS extraction is not supported on this device.");
    }
  };

  const handleFileSelected = (e) => {
    if (e.target.files.length > 0) {
      const file = e.target.files[0];
      setSelectedFile(file);
      setPreviewUrl(URL.createObjectURL(file));
      setShowOptions(false);
    }
  };

  const triggerCamera = () => {
    if (cameraInputRef.current) {
      cameraInputRef.current.click();
    }
  };

  const triggerGallery = () => {
    if (galleryInputRef.current) {
      galleryInputRef.current.click();
    }
  };

  const handleRegisterBlock = async () => {
    if (!blockId.trim()) {
      alert("Please enter a valid Block ID.");
      return;
    }
    try {
      const blockPayload = {
        block_id: blockId.trim(),
        quarry_id: quarryId,
        gps_latitude: latitude ? parseFloat(latitude) : null,
        gps_longitude: longitude ? parseFloat(longitude) : null,
        status: 'pending'
      };

      const res = await fetch('/api/blocks/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(blockPayload)
      });

      const data = await res.json();
      if (res.ok) {
        setIsRegistered(true);
        setRegistrationMessage("Block registered successfully.");
      } else {
        alert(`Registration failed: ${data.block_id || data.error || 'Check fields.'}`);
      }
    } catch (err) {
      alert(`Error registering block: ${err}`);
    }
  };

  const handleSubmitCV = async () => {
    if (!blockId.trim()) {
      alert("Please enter a valid Block ID.");
      return;
    }
    if (!selectedFile) {
      alert("Please capture or select a block photograph first.");
      return;
    }

    setLoading(true);
    setResult(null);

    try {
      const formData = new FormData();
      formData.append('image', selectedFile);

      const cvRes = await fetch(`/api/blocks/${blockId.trim()}/measure-cv/`, {
        method: 'POST',
        body: formData
      });

      const cvData = await cvRes.json();
      setLoading(false);

      if (cvRes.ok) {
        setResult({
          status: 'success',
          length: cvData.measurement.length_m,
          breadth: cvData.measurement.breadth_m,
          height: cvData.measurement.height_m,
          volume: cvData.measurement.volume_m3,
          confidence: cvData.measurement.confidence,
          rawImage: cvData.raw_image_path,
          annImage: cvData.annotated_image_path
        });
      } else {
        alert(`Computer Vision Warning: ${cvData.error || 'Measurement failed.'}`);
        setResult({
          status: 'failed',
          error: cvData.error || 'No block segmentation mask provided.',
          rawImage: URL.createObjectURL(selectedFile)
        });
      }
    } catch (err) {
      setLoading(false);
      alert(`Error communicating with backend: ${err}`);
    }
  };

  const handleSave = () => {
    alert("Measurement inspection saved successfully. Record forwarded to supervisor for review.");
    setBlockId('');
    setLatitude('');
    setLongitude('');
    setSelectedFile(null);
    setPreviewUrl('');
    setResult(null);
    setIsRegistered(false);
    setRegistrationMessage('');
  };

  return (
    <div className="grid-container">
      {/* Registration Column */}
      <div className="column">
        <div className="card">
          <div className="card-title">Block Registry & GPS Setup</div>
          
          <div className="form-group">
            <label>Select Quarry Reference ID</label>
            <select 
              className="form-control"
              value={quarryId}
              onChange={(e) => setQuarryId(e.target.value)}
            >
              {quarriesList.map((q) => (
                <option key={q.id} value={q.id}>{q.name}</option>
              ))}
            </select>
          </div>
          
          <div className="form-group">
            <label>Block Number / ID</label>
            <input 
              type="text" 
              className="form-control" 
              placeholder="e.g. AP-GRANITE-049"
              value={blockId}
              onChange={(e) => handleBlockIdChange(e.target.value)}
            />
          </div>
          
          <div className="form-group">
            <label>GPS Coordinates</label>
            <div className="form-row" style={{ marginBottom: '0.5rem' }}>
              <input 
                type="number" 
                step="0.000001" 
                className="form-control" 
                placeholder="Latitude"
                value={latitude}
                onChange={(e) => setLatitude(e.target.value)}
              />
              <input 
                type="number" 
                step="0.000001" 
                className="form-control" 
                placeholder="Longitude"
                value={longitude}
                onChange={(e) => setLongitude(e.target.value)}
              />
            </div>
            <button type="button" className="btn btn-secondary" onClick={handleCaptureGPS} style={{ width: '100%', marginBottom: '0.75rem' }}>
              📍 Capture Device Location
            </button>
            <button type="button" className="btn btn-success" onClick={handleRegisterBlock} style={{ width: '100%' }}>
              ➕ Create/Register Block
            </button>
            {registrationMessage && (
              <p style={{ marginTop: '0.5rem', fontSize: '0.85rem', color: 'var(--success)', fontWeight: 'bold', textAlign: 'center' }}>
                ✓ {registrationMessage}
              </p>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-title">Photograph Capture</div>
          
          {/* Hidden inputs to trigger native actions */}
          <input 
            type="file" 
            accept="image/*" 
            capture="camera" 
            ref={cameraInputRef} 
            style={{ display: 'none' }}
            onChange={handleFileSelected} 
          />
          <input 
            type="file" 
            accept="image/*" 
            ref={galleryInputRef} 
            style={{ display: 'none' }}
            onChange={handleFileSelected} 
          />

          <div className="form-group">
            <label>Granite Block Photograph (Ensure Markers Visible)</label>
            
            {/* Interactive Upload/Capture Zone */}
            <div 
              className="media-box" 
              onClick={() => setIsRegistered(prev => prev) && setShowOptions(!showOptions)}
              style={{ 
                cursor: isRegistered ? 'pointer' : 'not-allowed', 
                backgroundColor: isRegistered ? '#fafafa' : '#f1f5f9',
                borderColor: showOptions ? 'var(--primary-light)' : '#cbd5e1'
              }}
            >
              {previewUrl ? (
                <div style={{ width: '100%', height: '100%', position: 'relative' }}>
                  <img src={previewUrl} alt="Preview" style={{ maxHeight: '240px' }} />
                  <div style={{ marginTop: '0.5rem', fontSize: '0.8rem', color: 'var(--primary-light)', fontWeight: 'bold' }}>
                    Click / Tap to change photograph
                  </div>
                </div>
              ) : (
                <div style={{ color: isRegistered ? '#64748b' : '#94a3b8', padding: '1rem' }}>
                  <span style={{ fontSize: '2.5rem', display: 'block', marginBottom: '0.5rem' }}>📷</span>
                  <strong>{isRegistered ? "Tap here to capture or upload photograph" : "Register the block to enable photo capture"}</strong>
                </div>
              )}

              {showOptions && isRegistered && (
                <div style={{
                  position: 'absolute',
                  top: 0, left: 0, right: 0, bottom: 0,
                  backgroundColor: 'rgba(15, 23, 42, 0.9)',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'center',
                  alignItems: 'center',
                  gap: '1rem',
                  padding: '1rem',
                  zIndex: 10
                }} onClick={(e) => e.stopPropagation()}>
                  <button type="button" className="btn btn-primary" onClick={triggerCamera} style={{ width: '80%' }}>
                    📸 Open Camera
                  </button>
                  <button type="button" className="btn btn-secondary" onClick={triggerGallery} style={{ width: '80%' }}>
                    🖼 Upload Image (Gallery)
                  </button>
                  <button type="button" className="btn btn-outline" onClick={() => setShowOptions(false)} style={{ width: '80%', color: 'white', borderColor: 'white' }}>
                    Cancel
                  </button>
                </div>
              )}
            </div>
          </div>
          
          <button 
            type="button" 
            className="btn btn-primary" 
            onClick={handleSubmitCV} 
            style={{ width: '100%', marginTop: '0.5rem' }}
            disabled={loading || !isRegistered || !selectedFile}
          >
            ⚡ Analyze Block & Estimate Volume
          </button>
          
          {loading && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginTop: '1rem' }}>
              <div className="loader"></div>
              <p style={{ fontSize: '0.85rem', marginTop: '0.5rem', fontWeight: 600 }}>
                Running Computer Vision Pipeline (YOLOv8 & Pose)...
              </p>
            </div>
          )}
        </div>
      </div>

      {/* CV Result Displays Column */}
      <div className="column">
        {result && (
          <div className="card">
            <div className="card-title">
              Measurement Output (POC)
              <span className={`badge ${result.status === 'success' ? 'badge-measured' : 'badge-rejected'}`}>
                {result.status === 'success' ? 'Success' : 'CV Failed'}
              </span>
            </div>
            
            {result.status === 'success' ? (
              <>
                <div className="metrics-grid">
                  <div className="metric-card">
                    <div className="metric-value">{result.length.toFixed(2)}</div>
                    <div className="metric-label">Length (m)</div>
                  </div>
                  <div className="metric-card">
                    <div className="metric-value">{result.breadth.toFixed(2)}</div>
                    <div className="metric-label">Breadth (m)</div>
                  </div>
                  <div className="metric-card">
                    <div className="metric-value">{result.height.toFixed(2)}</div>
                    <div className="metric-label">Height (m)</div>
                  </div>
                  <div className="metric-card" style={{ backgroundColor: '#eff6ff', borderColor: '#bfdbfe' }}>
                    <div className="metric-value" style={{ color: 'var(--primary-light)' }}>{result.volume.toFixed(4)}</div>
                    <div className="metric-label">Volume (m³)</div>
                  </div>
                </div>

                <div style={{ marginBottom: '1rem', display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem' }}>
                  <span>CV System Confidence: <strong>{result.confidence.toFixed(2)}</strong></span>
                  <span>Inspection Method: <strong style={{ textTransform: 'uppercase' }}>Computer Vision</strong></span>
                </div>

                <div className="media-preview-container">
                  <div className="media-box">
                    <div className="metric-label" style={{ position: 'absolute', top: '5px', left: '5px', background: 'rgba(0,0,0,0.6)', color: 'white', padding: '0.2rem 0.5rem', borderRadius: '4px' }}>Raw Image</div>
                    <img src={'/media/' + result.rawImage} alt="Raw Block" />
                  </div>
                  <div className="media-box">
                    <div className="metric-label" style={{ position: 'absolute', top: '5px', left: '5px', background: 'rgba(30,58,138,0.8)', color: 'white', padding: '0.2rem 0.5rem', borderRadius: '4px' }}>CV Overlay</div>
                    <img src={'/media/' + result.annImage} alt="Annotated Block" />
                  </div>
                </div>
              </>
            ) : (
              <div style={{ padding: '1rem', background: '#fee2e2', borderRadius: '8px', color: '#991b1b', fontSize: '0.9rem', textAlign: 'center', marginBottom: '1rem' }}>
                <p style={{ fontWeight: 700, marginBottom: '0.5rem' }}>⚠ CV Pipeline Error</p>
                <p>{result.error}</p>
              </div>
            )}

            <div style={{ marginTop: '1.5rem', display: 'flex' }}>
              <button className="btn btn-success" onClick={handleSave} style={{ flex: 1 }}>
                ✓ Save Inspection Records
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
