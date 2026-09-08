import React, { useState, useEffect } from 'react';

export default function SupervisorDashboard() {
  const [blocks, setBlocks] = useState([]);
  const [search, setSearch] = useState('');
  const [selectedBlockId, setSelectedBlockId] = useState(null);
  const [selectedBlock, setSelectedBlock] = useState(null);
  
  // Manual override states
  const [showOverride, setShowOverride] = useState(false);
  const [ovLen, setOvLen] = useState('');
  const [ovBrd, setOvBrd] = useState('');
  const [ovHgt, setOvHgt] = useState('');
  const [ovReason, setOvReason] = useState('');
  const [ovActor, setOvActor] = useState('Mining Officer');

  // Assessment inputs/outputs
  const [assCat, setAssCat] = useState('Premium');
  const [assCls, setAssCls] = useState('Gangsaw Size');
  const [assDen, setAssDen] = useState(2.7);
  const [assWeight, setAssWeight] = useState('N/A');
  const [assRate, setAssRate] = useState('N/A');
  const [assSeig, setAssSeig] = useState('N/A');

  // Audit Logs history
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    loadBlocks();
  }, []);

  const loadBlocks = async () => {
    try {
      const res = await fetch('/api/blocks/');
      if (res.ok) {
        const data = await res.json();
        setBlocks(data);
        if (selectedBlockId) {
          const updated = data.find(b => b.block_id === selectedBlockId);
          if (updated) setSelectedBlock(updated);
        }
      }
    } catch (err) {
      console.error("Failed to load blocks", err);
    }
  };

  const handleSelectBlock = async (blockId) => {
    setSelectedBlockId(blockId);
    const block = blocks.find(b => b.block_id === blockId);
    setSelectedBlock(block);
    setShowOverride(false);
    
    // Set overrides default inputs
    if (block && block.measurement) {
      setOvLen(block.measurement.length_m);
      setOvBrd(block.measurement.breadth_m);
      setOvHgt(block.measurement.height_m);
    }

    // Load assessments & logs
    await fetchAssessment(blockId);
    await fetchAuditLogs(blockId);
  };

  const fetchAuditLogs = async (blockId) => {
    try {
      const res = await fetch(`/api/blocks/${blockId}/audit-logs/`);
      if (res.ok) {
        const data = await res.json();
        setLogs(data);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const fetchAssessment = async (blockId) => {
    try {
      const res = await fetch(`/api/assessments/${blockId}/`);
      if (res.ok) {
        const ass = await res.json();
        setAssCat(ass.granite_category);
        setAssCls(ass.gangsaw_classification);
        setAssDen(ass.density_mt_per_m3);
        setAssWeight(ass.weight_mt.toFixed(3) + ' MT');
        setAssRate('INR ' + ass.rate_per_mt.toLocaleString());
        setAssSeig('INR ' + ass.indicative_seigniorage.toLocaleString());
      } else {
        setAssWeight('N/A');
        setAssRate('N/A');
        setAssSeig('N/A');
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleCalculateAssessment = async () => {
    if (!selectedBlockId) return;

    try {
      const res = await fetch('/api/assessments/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          block_id: selectedBlockId,
          granite_category: assCat,
          gangsaw_classification: assCls,
          density: parseFloat(assDen)
        })
      });

      if (res.ok) {
        const ass = await res.json();
        setAssWeight(ass.weight_mt.toFixed(3) + ' MT');
        setAssRate('INR ' + ass.rate_per_mt.toLocaleString());
        setAssSeig('INR ' + ass.indicative_seigniorage.toLocaleString());
        alert("Assessment saved successfully.");
        await fetchAuditLogs(selectedBlockId);
      } else {
        const errData = await res.json();
        alert(`Assessment error: ${errData.error || 'Failed calculation.'}`);
      }
    } catch (err) {
      alert(`Error calculating: ${err}`);
    }
  };

  const handleApprove = async (statusVal) => {
    const officer = prompt("Enter Supervisor Name/ID to authorize:", "Supervisor Officer");
    if (!officer) return;

    try {
      const res = await fetch(`/api/blocks/${selectedBlockId}/approve/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          approval_status: statusVal,
          actor: officer
        })
      });

      if (res.ok) {
        alert(`Block updated: ${statusVal.toUpperCase()}`);
        await loadBlocks();
        await handleSelectBlock(selectedBlockId);
      } else {
        const err = await res.json();
        alert(`Approval error: ${err.error}`);
      }
    } catch (err) {
      alert(`Error: ${err}`);
    }
  };

  const handleOverrideSubmit = async () => {
    const len = parseFloat(ovLen);
    const brd = parseFloat(ovBrd);
    const hgt = parseFloat(ovHgt);

    if (isNaN(len) || isNaN(brd) || isNaN(hgt)) {
      alert("Please enter valid numbers for dimensions.");
      return;
    }
    if (!ovReason.trim()) {
      alert("Please provide a reason justification for manual override.");
      return;
    }

    try {
      const res = await fetch(`/api/blocks/${selectedBlockId}/override/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          length_m: len,
          breadth_m: brd,
          height_m: hgt,
          reason: ovReason,
          actor: ovActor
        })
      });

      if (res.ok) {
        alert("Manual override submitted and logged.");
        setOvReason('');
        setShowOverride(false);
        await loadBlocks();
        await handleSelectBlock(selectedBlockId);
      } else {
        const err = await res.json();
        alert(`Override failed: ${err.error}`);
      }
    } catch (err) {
      alert(`Error submitting override: ${err}`);
    }
  };

  const filteredBlocks = blocks.filter(b => 
    b.block_id.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="grid-container">
      {/* Sidebar List */}
      <div className="column">
        <div className="card">
          <div className="card-title">Inspection Records</div>
          <div className="form-group">
            <input 
              type="text" 
              className="form-control" 
              placeholder="Search by Block ID..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="block-list">
            {filteredBlocks.length === 0 ? (
              <div style={{ padding: '1.5rem', textAlign: 'center', color: '#94a3b8' }}>No records found.</div>
            ) : (
              filteredBlocks.map(b => (
                <div 
                  key={b.block_id}
                  className={`block-item ${selectedBlockId === b.block_id ? 'active' : ''}`}
                  onClick={() => handleSelectBlock(b.block_id)}
                >
                  <div>
                    <strong>{b.block_id}</strong>
                    <div style={{ fontSize: '0.75rem', color: '#64748b' }}>{b.quarry_id || 'No Quarry'}</div>
                  </div>
                  <div>
                    <span className={`badge badge-${b.approval_status || 'pending'}`}>
                      {b.approval_status || 'pending'}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Details Display Panel */}
      <div className="column">
        {selectedBlock ? (
          <div className="card">
            <div className="card-title">
              Block Details: <span style={{ color: 'var(--primary)' }}>{selectedBlock.block_id}</span>
              <span className={`badge badge-${selectedBlock.approval_status || 'pending'}`}>
                {selectedBlock.approval_status || 'pending'}
              </span>
            </div>

            {/* Info grid */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
              <div>
                <p><strong>Quarry ID:</strong> {selectedBlock.quarry_id || 'N/A'}</p>
                <p><strong>GPS Latitude:</strong> {selectedBlock.gps_latitude || 'N/A'}</p>
                <p><strong>GPS Longitude:</strong> {selectedBlock.gps_longitude || 'N/A'}</p>
                <p><strong>Capture Time:</strong> {selectedBlock.captured_at ? new Date(selectedBlock.captured_at).toLocaleString() : 'N/A'}</p>
              </div>
              <div>
                <p><strong>Approval Status:</strong> {selectedBlock.approval_status || 'Pending Review'}</p>
                <p><strong>Action Officer:</strong> {selectedBlock.approved_by || 'N/A'}</p>
                <p><strong>Action Time:</strong> {selectedBlock.approved_at ? new Date(selectedBlock.approved_at).toLocaleString() : 'N/A'}</p>
                <p><strong>CV Status:</strong> {selectedBlock.cv_status ? selectedBlock.cv_status.toUpperCase() : 'PENDING'}</p>
              </div>
            </div>

            <div className="card-title" style={{ fontSize: '1rem', borderBottom: '1px solid var(--card-border)', paddingBottom: '0.25rem' }}>
              Active Geometric Measurements
            </div>
            
            {selectedBlock.measurement ? (
              <div className="metrics-grid">
                <div className="metric-card">
                  <div className="metric-value">{selectedBlock.measurement.length_m.toFixed(2)}</div>
                  <div className="metric-label">Length (m)</div>
                </div>
                <div className="metric-card">
                  <div className="metric-value">{selectedBlock.measurement.breadth_m.toFixed(2)}</div>
                  <div className="metric-label">Breadth (m)</div>
                </div>
                <div className="metric-card">
                  <div className="metric-value">{selectedBlock.measurement.height_m.toFixed(2)}</div>
                  <div className="metric-label">Height (m)</div>
                </div>
                <div className="metric-card" style={{ backgroundColor: '#f0fdf4', borderColor: '#bbf7d0' }}>
                  <div className="metric-value" style={{ color: 'var(--success)' }}>
                    {selectedBlock.measurement.volume_m3.toFixed(4)}
                  </div>
                  <div className="metric-label">Volume (m³)</div>
                </div>
              </div>
            ) : (
              <div style={{ textAlign: 'center', color: '#94a3b8', padding: '1rem', border: '1px dashed #cbd5e1', borderRadius: '8px', marginBottom: '1.5rem' }}>
                No active measurement data recorded.
              </div>
            )}

            {/* Original CV snapshot */}
            {selectedBlock.is_overridden && selectedBlock.original_measurement && (
              <div style={{ backgroundColor: '#fee2e2', border: '1px solid #fecaca', borderRadius: '8px', padding: '1rem', marginBottom: '1.5rem' }}>
                <h4 style={{ color: '#b91c1c', fontSize: '0.9rem', marginBottom: '0.5rem', fontWeight: 700 }}>
                  ⚠ Original CV Measurements Snapshot
                </h4>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', textAlign: 'center', fontSize: '0.85rem' }}>
                  <div><strong>L:</strong> {selectedBlock.original_measurement.length_m.toFixed(2)}m</div>
                  <div><strong>B:</strong> {selectedBlock.original_measurement.breadth_m.toFixed(2)}m</div>
                  <div><strong>H:</strong> {selectedBlock.original_measurement.height_m.toFixed(2)}m</div>
                  <div><strong>Vol:</strong> {selectedBlock.original_measurement.volume_m3.toFixed(4)}m³</div>
                </div>
                <p style={{ marginTop: '0.5rem', fontSize: '0.8rem', color: '#7f1d1d' }}>
                  <strong>Override Reason:</strong> {selectedBlock.override_reason}
                </p>
              </div>
            )}

            {/* Visual Previews */}
            {(selectedBlock.raw_image_path || selectedBlock.annotated_image_path) && (
              <div className="media-preview-container">
                <div className="media-box">
                  <div className="metric-label" style={{ position: 'absolute', top: '5px', left: '5px', background: 'rgba(0,0,0,0.6)', color: 'white', padding: '0.2rem 0.5rem', borderRadius: '4px' }}>Raw Photo</div>
                  {selectedBlock.raw_image_path ? <img src={'/media/' + selectedBlock.raw_image_path} alt="Raw" /> : 'No photo uploaded'}
                </div>
                <div className="media-box">
                  <div className="metric-label" style={{ position: 'absolute', top: '5px', left: '5px', background: 'rgba(30,58,138,0.8)', color: 'white', padding: '0.2rem 0.5rem', borderRadius: '4px' }}>CV Analysis</div>
                  {selectedBlock.annotated_image_path ? <img src={'/media/' + selectedBlock.annotated_image_path} alt="CV" /> : 'No CV output processed'}
                </div>
              </div>
            )}

            {/* Action buttons */}
            <div style={{ marginTop: '1.5rem', display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
              <button className="btn btn-success" onClick={() => handleApprove('approved')}>Approve Measurement</button>
              <button className="btn btn-danger" onClick={() => handleApprove('rejected')}>Reject Block</button>
              <button className="btn btn-secondary" onClick={() => setShowOverride(!showOverride)}>✏ Manual Override</button>
              <a href={`/api/blocks/${selectedBlock.block_id}/pdf/`} target="_blank" className="btn btn-outline">
                🖨 Download PDF Report
              </a>
            </div>

            {/* Manual Override Form */}
            {showOverride && (
              <div className="card" style={{ marginTop: '1.5rem', backgroundColor: '#f8fafc', borderColor: '#cbd5e1' }}>
                <div className="card-title" style={{ fontSize: '1rem' }}>Manual Override Inputs</div>
                <div className="form-row">
                  <div className="form-group">
                    <label>Length (m)</label>
                    <input type="number" step="0.01" className="form-control" value={ovLen} onChange={(e) => setOvLen(e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label>Breadth (m)</label>
                    <input type="number" step="0.01" className="form-control" value={ovBrd} onChange={(e) => setOvBrd(e.target.value)} />
                  </div>
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label>Height (m)</label>
                    <input type="number" step="0.01" className="form-control" value={ovHgt} onChange={(e) => setOvHgt(e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label>Officer Name</label>
                    <input type="text" className="form-control" value={ovActor} onChange={(e) => setOvActor(e.target.value)} />
                  </div>
                </div>
                <div className="form-group">
                  <label>Reason for Manual Override</label>
                  <input 
                    type="text" 
                    className="form-control" 
                    placeholder="e.g. Marker corner occluded by wet mud"
                    value={ovReason}
                    onChange={(e) => setOvReason(e.target.value)}
                  />
                </div>
                <button className="btn btn-primary" onClick={handleOverrideSubmit} style={{ width: '100%' }}>
                  Submit Override Dimensions
                </button>
              </div>
            )}

            {/* Assessment Panel */}
            {selectedBlock.measurement && (
              <div className="card" style={{ marginTop: '1.5rem', borderColor: '#bfdbfe', backgroundColor: '#eff6ff' }}>
                <div className="card-title" style={{ fontSize: '1rem', borderColor: '#bfdbfe' }}>
                  Seigniorage Assessment (POC Rates)
                  <button className="btn btn-primary" onClick={handleCalculateAssessment} style={{ padding: '0.3rem 0.75rem', fontSize: '0.8rem' }}>
                    ↺ Calculate Assessment
                  </button>
                </div>
                
                <div className="disclaimer-alert">
                  <span class="disclaimer-icon">⚠</span>
                  <div>
                    <strong>POC Indicative Billing Alert:</strong> Rates and calculations shown below are based on the experimental POC tariff scheduler. They do not represent official billing values.
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>Granite Category</label>
                    <select className="form-control" value={assCat} onChange={(e) => setAssCat(e.target.value)}>
                      <option value="Premium">Premium</option>
                      <option value="Standard">Standard</option>
                      <option value="Commercial">Commercial</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Gangsaw Classification</label>
                    <select className="form-control" value={assCls} onChange={(e) => setAssCls(e.target.value)}>
                      <option value="Gangsaw Size">Gangsaw Size</option>
                      <option value="Mini Gangsaw Size">Mini Gangsaw Size</option>
                    </select>
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>Density (MT/m³)</label>
                    <input type="number" step="0.1" className="form-control" value={assDen} onChange={(e) => setAssDen(e.target.value)} />
                  </div>
                  <div className="form-group">
                    <label>Calculated Metric Weight</label>
                    <input type="text" className="form-control" value={assWeight} readOnly style={{ backgroundColor: '#e2e8f0' }} />
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>POC Tariff Rate (per MT)</label>
                    <input type="text" className="form-control" value={assRate} readOnly style={{ backgroundColor: '#e2e8f0' }} />
                  </div>
                  <div className="form-group">
                    <label style={{ color: 'var(--primary)', fontWeight: 700 }}>Indicative Seigniorage Fee</label>
                    <input type="text" className="form-control" value={assSeig} readOnly style={{ backgroundColor: '#e2e8f0', fontWeight: 700, color: 'var(--primary)' }} />
                  </div>
                </div>
              </div>
            )}

            {/* Audit history timeline */}
            <div className="card" style={{ marginTop: '1.5rem' }}>
              <div className="card-title" style={{ fontSize: '1rem' }}>Action & Supervision History</div>
              <div className="timeline">
                {logs.length === 0 ? (
                  <div style={{ fontSize: '0.85rem', color: '#94a3b8' }}>No history logs registered.</div>
                ) : (
                  logs.map((log, idx) => (
                    <div key={idx} className="timeline-item">
                      <div className="timeline-time">{log.timestamp}</div>
                      <div className="timeline-title">{log.action.replace('_', ' ').toUpperCase()} (by {log.actor})</div>
                      <div className="timeline-desc">{log.details}</div>
                    </div>
                  ))
                )}
              </div>
            </div>

          </div>
        ) : (
          <div className="card" style={{ textAlign: 'center', color: '#94a3b8', padding: '3rem' }}>
            Select an inspection record from the list to view dimensions, photos, audit history, and PDF reporting.
          </div>
        )}
      </div>
    </div>
  );
}
