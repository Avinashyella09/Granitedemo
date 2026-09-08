import React, { useState, useEffect } from 'react';
import { apiService, clearApiCache } from './services/api';
import { 
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, 
  Tooltip, Legend, ResponsiveContainer, PieChart, Pie, Cell 
} from 'recharts';

export default function App() {
  // Navigation tabs: 'overview', 'registry', 'officers', 'quarries', 'revenue', 'compliance', 'alerts', 'map'
  const [activeTab, setActiveTab] = useState('overview');

  // Core data states
  const [blocks, setBlocks] = useState([]);
  const [selectedBlockId, setSelectedBlockId] = useState(null);
  const [selectedBlock, setSelectedBlock] = useState(null);

  // Analytics states
  const [overviewData, setOverviewData] = useState(null);
  const [officers, setOfficers] = useState([]);
  const [selectedOfficerId, setSelectedOfficerId] = useState(null);
  const [officerTrend, setOfficerTrend] = useState([]);
  
  const [quarries, setQuarries] = useState([]);
  const [selectedQuarryA, setSelectedQuarryA] = useState('');
  const [selectedQuarryB, setSelectedQuarryB] = useState('');
  const [quarryTrendA, setQuarryTrendA] = useState([]);
  const [quarryTrendB, setQuarryTrendB] = useState([]);

  const [revenueData, setRevenueData] = useState(null);
  const [leakageData, setLeakageData] = useState(null);
  const [complianceData, setComplianceData] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [mapData, setMapData] = useState(null);

  // Filter/Search states
  const [search, setSearch] = useState('');
  const [quarryFilter, setQuarryFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [cvStatusFilter, setCvStatusFilter] = useState('all');
  const [dateFilter, setDateFilter] = useState('6weeks');
  const [alertSeverityFilter, setAlertSeverityFilter] = useState('all');

  // Manual override states
  const [showOverride, setShowOverride] = useState(false);
  const [ovLen, setOvLen] = useState('');
  const [ovBrd, setOvBrd] = useState('');
  const [ovHgt, setOvHgt] = useState('');
  const [ovReason, setOvReason] = useState('');
  const [ovActor, setOvActor] = useState('Supervisor-1');

  // Assessment inputs/outputs
  const [assCat, setAssCat] = useState('Premium');
  const [assCls, setAssCls] = useState('Gangsaw Size');
  const [assDen, setAssDen] = useState(2.7);
  const [assWeight, setAssWeight] = useState('N/A');
  const [assRate, setAssRate] = useState('N/A');
  const [assSeig, setAssSeig] = useState('N/A');
  const [assSnapshot, setAssSnapshot] = useState(null);

  // Timeline / Audit logs state
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadAllData();
  }, []);

  const handleManualRefresh = async () => {
    clearApiCache();
    await loadAllData();
  };

  const loadAllData = async () => {
    setLoading(true);
    try {
      // Fire all streams concurrently and set state progressively as each resolves
      const pBlocks = apiService.getBlocks().then(data => { setBlocks(data); return data; }).catch(e => { console.error("Error loading blocks:", e); return []; });
      const pOverview = apiService.getExecutiveOverview().then(data => { setOverviewData(data); return data; }).catch(e => { console.error("Error loading overview:", e); return null; });
      const pOfficers = apiService.getOfficersAnalytics().then(data => { setOfficers(data); return data; }).catch(e => { console.error("Error loading officers:", e); return []; });
      const pQuarries = apiService.getQuarriesComparison().then(data => { setQuarries(data); return data; }).catch(e => { console.error("Error loading quarries:", e); return []; });
      const pRevenue = apiService.getRevenueSummary().then(data => { setRevenueData(data); return data; }).catch(e => { console.error("Error loading revenue:", e); return null; });
      const pLeakage = apiService.getRevenueLeakage().then(data => { setLeakageData(data); return data; }).catch(e => { console.error("Error loading leakage:", e); return null; });
      const pCompliance = apiService.getAuditReadiness().then(data => { setComplianceData(data); return data; }).catch(e => { console.error("Error loading audit readiness:", e); return null; });
      const pAlerts = apiService.getAlerts().then(data => { setAlerts(data); return data; }).catch(e => { console.error("Error loading alerts:", e); return []; });
      const pMap = apiService.getMapData().then(data => { setMapData(data); return data; }).catch(e => { console.error("Error loading map:", e); return null; });

      const [blocksData, overview, officersList, quarriesList] = await Promise.all([
        pBlocks, pOverview, pOfficers, pQuarries, pRevenue, pLeakage, pCompliance, pAlerts, pMap
      ]);

      // Trigger secondary trend fetches in background
      if (officersList && officersList.length > 0) {
        const initialOfficer = officersList[0].officer_id;
        setSelectedOfficerId(initialOfficer);
        apiService.getOfficerWeeklyAnalytics(initialOfficer)
          .then(data => setOfficerTrend(data))
          .catch(e => console.error("Error loading officer weekly trend:", e));
      }

      if (quarriesList && quarriesList.length >= 2) {
        const qA = quarriesList[0].quarry_id;
        const qB = quarriesList[1].quarry_id;
        setSelectedQuarryA(qA);
        setSelectedQuarryB(qB);
        Promise.all([
          apiService.getQuarryTrend(qA),
          apiService.getQuarryTrend(qB)
        ]).then(([trendA, trendB]) => {
          setQuarryTrendA(trendA);
          setQuarryTrendB(trendB);
        }).catch(e => console.error("Error loading quarry comparison trends:", e));
      } else if (quarriesList && quarriesList.length > 0) {
        const qA = quarriesList[0].quarry_id;
        setSelectedQuarryA(qA);
        apiService.getQuarryTrend(qA)
          .then(data => setQuarryTrendA(data))
          .catch(e => console.error("Error loading quarry A trend:", e));
      }

    } catch (e) {
      console.error("Failed to load dashboard data: ", e);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectBlock = async (blockId) => {
    setSelectedBlockId(blockId);
    const block = blocks.find(b => b.block_id === blockId);
    setSelectedBlock(block);
    setShowOverride(false);

    if (block && block.measurement) {
      setOvLen(block.measurement.length_m);
      setOvBrd(block.measurement.breadth_m);
      setOvHgt(block.measurement.height_m);
    }

    await fetchAssessment(blockId);
    await fetchAuditLogs(blockId);
  };

  const fetchAuditLogs = async (blockId) => {
    try {
      const auditData = await apiService.getAuditLogs(blockId);
      setLogs(auditData);
    } catch (e) {
      console.error(e);
    }
  };

  const fetchAssessment = async (blockId) => {
    try {
      const ass = await apiService.getAssessment(blockId);
      if (ass) {
        setAssCat(ass.granite_category);
        setAssCls(ass.gangsaw_classification);
        setAssDen(ass.density_mt_per_m3);
        setAssWeight(ass.weight_mt.toFixed(3) + ' MT');
        setAssRate('INR ' + ass.rate_per_mt.toLocaleString());
        setAssSeig('INR ' + ass.indicative_seigniorage.toLocaleString());
        setAssSnapshot(ass);
      } else {
        setAssWeight('N/A');
        setAssRate('N/A');
        setAssSeig('N/A');
        setAssSnapshot(null);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleCalculateAssessment = async () => {
    if (!selectedBlockId) return;
    try {
      const ass = await apiService.createAssessment({
        block_id: selectedBlockId,
        granite_category: assCat,
        gangsaw_classification: assCls,
        density: parseFloat(assDen)
      });
      setAssWeight(ass.weight_mt.toFixed(3) + ' MT');
      setAssRate('INR ' + ass.rate_per_mt.toLocaleString());
      setAssSeig('INR ' + ass.indicative_seigniorage.toLocaleString());
      setAssSnapshot(ass);
      alert("Seigniorage Assessment saved successfully.");
      await fetchAuditLogs(selectedBlockId);
      await loadAllData();
    } catch (e) {
      alert(e.message);
    }
  };

  const handleApprove = async (statusVal) => {
    let reason = '';
    if (statusVal === 'rejected') {
      reason = prompt("Please provide a reason justification for rejecting this block:");
      if (!reason) {
        alert("Rejection reason is required.");
        return;
      }
    }

    const officerName = prompt("Enter Supervisor Name/ID to sign action log:", ovActor);
    if (!officerName) return;

    try {
      await apiService.approveBlock(selectedBlockId, {
        approval_status: statusVal,
        actor: officerName,
        reason: reason
      });
      alert(`Block marked as ${statusVal.toUpperCase()}`);
      await loadAllData();
      await handleSelectBlock(selectedBlockId);
    } catch (e) {
      alert(e.message);
    }
  };

  const handleOverrideSubmit = async () => {
    const len = parseFloat(ovLen);
    const brd = parseFloat(ovBrd);
    const hgt = parseFloat(ovHgt);

    if (isNaN(len) || isNaN(brd) || isNaN(hgt)) {
      alert("Please enter valid decimal inputs for dimensions.");
      return;
    }
    if (!ovReason.trim()) {
      alert("Reason justification is required for manual override audits.");
      return;
    }

    try {
      await apiService.overrideBlock(selectedBlockId, {
        length_m: len,
        breadth_m: brd,
        height_m: hgt,
        reason: ovReason,
        actor: ovActor
      });
      alert("Manual override applied and saved successfully.");
      setOvReason('');
      setShowOverride(false);
      await loadAllData();
      await handleSelectBlock(selectedBlockId);
    } catch (e) {
      alert(e.message);
    }
  };

  const handleSelectOfficer = async (officerId) => {
    setSelectedOfficerId(officerId);
    try {
      const oTrend = await apiService.getOfficerWeeklyAnalytics(officerId);
      setOfficerTrend(oTrend);
    } catch (e) {
      console.error(e);
    }
  };

  const handleSelectQuarryA = async (quarryId) => {
    setSelectedQuarryA(quarryId);
    try {
      const qTrend = await apiService.getQuarryTrend(quarryId);
      setQuarryTrendA(qTrend);
    } catch (e) {
      console.error(e);
    }
  };

  const handleSelectQuarryB = async (quarryId) => {
    setSelectedQuarryB(quarryId);
    try {
      const qTrend = await apiService.getQuarryTrend(quarryId);
      setQuarryTrendB(qTrend);
    } catch (e) {
      console.error(e);
    }
  };

  // Export utility: simple CSV downloader
  const exportCSV = (type) => {
    let csvContent = "data:text/csv;charset=utf-8,";
    if (type === 'officers') {
      csvContent += "Officer ID,Name,Designation,Total Blocks,Override Rate,Approval Rate,Avg Confidence,Avg Duration(s)\n";
      officers.forEach(o => {
        csvContent += `"${o.officer_id}","${o.name}","${o.designation}",${o.total_blocks},${o.override_rate},${o.approval_rate},${o.avg_confidence},${o.avg_inspection_duration}\n`;
      });
    } else if (type === 'quarries') {
      csvContent += "Quarry Name,District,Lessee,Blocks Count,Total Volume (m3),Total Seigniorage (INR),Override Rate\n";
      quarries.forEach(q => {
        csvContent += `"${q.name}","${q.district}","${q.lessee}",${q.total_blocks},${q.total_volume},${q.total_revenue},${q.override_rate}\n`;
      });
    } else {
      csvContent += "Revenue Metric,Value\n";
      csvContent += `"Total Indicative Seigniorage","INR ${revenueData?.total_seigniorage?.toLocaleString()}"\n`;
      csvContent += `"Total Volume Blocked","${revenueData?.total_volume} m3"\n`;
      csvContent += `"Total Blocks Checked",${revenueData?.block_count}\n`;
      csvContent += `"Gangsaw Billing","INR ${revenueData?.gangsaw_revenue?.toLocaleString()}"\n`;
      csvContent += `"Below Gangsaw Billing","INR ${revenueData?.below_gangsaw_revenue?.toLocaleString()}"\n`;
      csvContent += `"Estimated Recovery Saved","INR ${leakageData?.estimated_revenue_recovered?.toLocaleString()}"\n`;
    }
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `ap_mines_${type}_report.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  // Filter & sort block registrations lists (newest registered blocks first)
  const filteredBlocks = blocks.filter(b => {
    const matchSearch = b.block_id.toLowerCase().includes(search.toLowerCase());
    const matchQuarry = quarryFilter === 'all' || (b.quarry?.id === quarryFilter || b.quarry_id === quarryFilter);
    const matchStatus = statusFilter === 'all' || (b.approval_status || 'pending') === statusFilter;
    const matchCvStatus = cvStatusFilter === 'all' || (b.cv_status || 'pending') === cvStatusFilter;
    return matchSearch && matchQuarry && matchStatus && matchCvStatus;
  }).sort((a, b) => new Date(b.created_at || b.captured_at || 0) - new Date(a.created_at || a.captured_at || 0));

  const COLORS = ['#10b981', '#f59e0b', '#ef4444']; // Green, Amber, Red compliance

  return (
    <div style={{ fontFamily: 'Inter, sans-serif', color: '#1e293b', background: '#f1f5f9', minHeight: '100vh' }}>
      
      {/* portal header branding */}
      <header className="portal-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1rem 2rem', background: '#fff', boxShadow: '0 1px 3px 0 rgba(0,0,0,0.1)' }}>
        <div className="header-brand" style={{ display: 'flex', alignItems: 'center', gap: '1.5rem' }}>
          <div className="gov-seal" style={{ flexShrink: 0 }}>
            <img src="/ap_govt_logo.svg" alt="Andhra Pradesh State Seal Logo" style={{ height: '60px', width: 'auto', display: 'block' }} />
          </div>
          <div className="header-title" style={{ minWidth: '0' }}>
            <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 800, color: '#1e3a8a', lineHeight: '1.2' }}>Smart Granite Block Measurement & Seigniorage Assessment</h1>
            <p style={{ margin: '0.25rem 0 0 0', fontSize: '0.9rem', color: '#64748b', fontWeight: 500 }}>Government of Andhra Pradesh | Department of Mines & Geology</p>
          </div>
        </div>
        
        {/* Navigation panel */}
        <nav style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <button onClick={() => setActiveTab('overview')} className={`nav-btn ${activeTab === 'overview' ? 'active' : ''}`}>Overview</button>
          <button onClick={() => setActiveTab('registry')} className={`nav-btn ${activeTab === 'registry' ? 'active' : ''}`}>Blocks Registry</button>
          <button onClick={() => setActiveTab('officers')} className={`nav-btn ${activeTab === 'officers' ? 'active' : ''}`}>Officers Analytics</button>
          <button onClick={() => setActiveTab('quarries')} className={`nav-btn ${activeTab === 'quarries' ? 'active' : ''}`}>Quarry Analysis</button>
          <button onClick={() => setActiveTab('revenue')} className={`nav-btn ${activeTab === 'revenue' ? 'active' : ''}`}>Revenue Summary</button>
          <button onClick={() => setActiveTab('compliance')} className={`nav-btn ${activeTab === 'compliance' ? 'active' : ''}`}>Compliance Audit</button>
          <button onClick={() => setActiveTab('alerts')} className={`nav-btn ${activeTab === 'alerts' ? 'active' : ''}`}>System Alerts ({overviewData?.critical_alerts_count || 0})</button>
          <button onClick={() => setActiveTab('map')} className={`nav-btn ${activeTab === 'map' ? 'active' : ''}`}>Geospatial Map</button>
        </nav>
      </header>

      {/* Hero Banner Section - ONLY on Overview page */}
      {activeTab === 'overview' && (
        <div style={{ width: '100%', overflow: 'hidden', borderBottom: '2px solid #cbd5e1' }}>
          <img 
            src="/banner_3.png" 
            alt="Andhra Pradesh Geological Department Banner" 
            style={{ width: '100%', height: 'auto', display: 'block', objectFit: 'contain' }} 
          />
        </div>
      )}

      {/* Styled component buttons */}
      <style>{`
        .nav-btn {
          padding: 0.5rem 0.9rem;
          border: 1px solid transparent;
          background: transparent;
          border-radius: 6px;
          cursor: pointer;
          font-weight: 600;
          font-size: 0.85rem;
          color: #64748b;
          transition: all 0.2s ease;
        }
        .nav-btn:hover { background: #f1f5f9; color: #1e3a8a; }
        .nav-btn.active { background: #eff6ff; border-color: #bfdbfe; color: #2563eb; }
        .card { background: #fff; padding: 1.5rem; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; margin-bottom: 1.5rem; }
        .card-title { font-size: 1.1rem; fontWeight: 700; color: #1e3a8a; border-bottom: 1px solid #e2e8f0; padding-bottom: 0.75rem; margin-bottom: 1rem; display: flex; justify-content: space-between; align-items: center; }
        .form-control { width: 100%; padding: 0.5rem; border-radius: 6px; border: 1px solid #cbd5e1; box-sizing: border-box; }
        .btn { padding: 0.5rem 1rem; border-radius: 6px; font-weight: 600; cursor: pointer; border: 1px solid transparent; transition: all 0.2s; }
        .btn-primary { background: #2563eb; color: white; }
        .btn-primary:hover { background: #1d4ed8; }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; }
        .btn-secondary { background: #64748b; color: white; }
        .btn-outline { border-color: #cbd5e1; background: transparent; color: #64748b; }
        .btn-outline:hover { background: #f8fafc; }
        .badge { padding: 0.25rem 0.6rem; border-radius: 12px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; }
        .badge-approved { background: #d1fae5; color: #065f46; }
        .badge-rejected { background: #fee2e2; color: #991b1b; }
        .badge-pending { background: #fef3c7; color: #92400e; }
        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }
        .metric-card { text-align: center; border: 1px solid #e2e8f0; padding: 1rem; border-radius: 8px; }
        .metric-value { font-size: 1.5rem; font-weight: 800; color: #1e3a8a; }
        .metric-label { font-size: 0.75rem; color: #64748b; font-weight: 600; text-transform: uppercase; margin-top: 0.25rem; }
        .media-preview-container { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem; }
        .media-box { border: 1px solid #e2e8f0; border-radius: 8px; height: 200px; display: flex; align-items: center; justify-content: center; overflow: hidden; background: #f8fafc; position: relative; }
        .media-box img { max-width: 100%; max-height: 100%; object-fit: contain; }
        .disclaimer-alert { display: flex; gap: 0.75rem; background: #fffbeb; border: 1px solid #fef3c7; color: #92400e; padding: 0.75rem; border-radius: 6px; font-size: 0.8rem; margin-bottom: 1rem; }
        .disclaimer-icon { font-size: 1.2rem; }
        .timeline { display: flex; flexDirection: column; gap: 1rem; position: relative; }
        .timeline-item { padding-left: 1.5rem; border-left: 2px solid #cbd5e1; position: relative; }
        .timeline-time { font-size: 0.75rem; color: #64748b; font-weight: 600; }
        .timeline-title { font-size: 0.85rem; font-weight: 700; color: #1e3a8a; }
        .timeline-desc { font-size: 0.8rem; color: #475569; }
      `}</style>

      {/* Main content body pages */}
      <main style={{ padding: '2rem' }}>

        {/* 1. EXECUTIVE OVERVIEW TAB */}
        {activeTab === 'overview' && (
          <div>
            {/* Top overview metrics HUD cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1.25rem', marginBottom: '2rem' }}>
              <div className="card" style={{ margin: 0, borderLeft: '4px solid #3b82f6' }}>
                <div className="metric-label">Total Blocks Inspected</div>
                <div className="metric-value" style={{ fontSize: '2rem', marginTop: '0.5rem' }}>
                  {overviewData ? (overviewData.total_blocks_inspected ?? 0).toLocaleString() : '—'}
                </div>
                <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>All quarries combined</div>
              </div>
              <div className="card" style={{ margin: 0, borderLeft: '4px solid #10b981' }}>
                <div className="metric-label">Total Indicative Revenue</div>
                <div className="metric-value" style={{ fontSize: '1.75rem', marginTop: '0.5rem', color: '#10b981' }}>
                  {overviewData ? 'INR ' + (overviewData.total_seigniorage ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—'}
                </div>
                <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>Indicative/Synthetic rates</div>
              </div>
              <div className="card" style={{ margin: 0, borderLeft: '4px solid #f59e0b' }}>
                <div className="metric-label">Manual Override Rate</div>
                <div className="metric-value" style={{ fontSize: '2rem', marginTop: '0.5rem' }}>
                  {overviewData ? (Math.round((overviewData.override_rate ?? 0) * 100)) + '%' : '—'}
                </div>
                <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>Supervisor manual sizing adjustments</div>
              </div>
              <div className="card" style={{ margin: 0, borderLeft: '4px solid #8b5cf6' }}>
                <div className="metric-label">Estimated Leakage Saved</div>
                <div className="metric-value" style={{ fontSize: '1.75rem', marginTop: '0.5rem', color: '#8b5cf6' }}>
                  {overviewData ? 'INR ' + (overviewData.estimated_revenue_recovered ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—'}
                </div>
                <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '0.25rem' }}>Recovered via AI sizing</div>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '2rem' }}>
              <div>
                {/* Weekly Inspection volumes LineChart */}
                <div className="card">
                  <div className="card-title">Weekly Block Inspection & Revenue Sizing Trend (6 Weeks)</div>
                  <div style={{ width: '100%', height: 280 }}>
                    <ResponsiveContainer>
                      <LineChart data={revenueData?.weekly_revenue || overviewData?.weekly_revenue}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="week" />
                        <YAxis yAxisId="left" />
                        <YAxis yAxisId="right" orientation="right" />
                        <Tooltip />
                        <Legend />
                        <Line yAxisId="left" type="monotone" dataKey="volume" name="Inspection Volume (m³)" stroke="#3b82f6" activeDot={{ r: 8 }} />
                        <Line yAxisId="right" type="monotone" dataKey="revenue" name="Indicative Seigniorage (INR)" stroke="#10b981" />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                {/* Quarry volume summaries */}
                <div className="card">
                  <div className="card-title">Quarry Sizing and Volume Contribution</div>
                  <div style={{ width: '100%', height: 280 }}>
                    <ResponsiveContainer>
                      <BarChart data={quarries}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                        <YAxis />
                        <Tooltip />
                        <Legend />
                        <Bar dataKey="total_volume" name="Total Volume (m³)" fill="#3b82f6" />
                        <Bar dataKey="total_revenue" name="Indicative Billing (INR)" fill="#8b5cf6" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>

              {/* Compliance donut charts */}
              <div>
                <div className="card">
                  <div className="card-title">Audit Completeness & Compliance</div>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                    <div style={{ width: '100%', height: 200, position: 'relative' }}>
                      <ResponsiveContainer>
                        <PieChart>
                          <Pie
                            data={[
                              { name: 'Complete (Green)', value: (complianceData?.green_count ?? overviewData?.green_count ?? 1) },
                              { name: 'Minor Missing (Amber)', value: (complianceData?.amber_count ?? overviewData?.amber_count ?? 0) },
                              { name: 'Critical Missing (Red)', value: (complianceData?.red_count ?? overviewData?.red_count ?? 0) }
                            ]}
                            cx="50%"
                            cy="50%"
                            innerRadius={60}
                            outerRadius={80}
                            paddingAngle={5}
                            dataKey="value"
                          >
                            <Cell fill="#10b981" />
                            <Cell fill="#f59e0b" />
                            <Cell fill="#ef4444" />
                          </Pie>
                        </PieChart>
                      </ResponsiveContainer>
                      <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', textAlign: 'center' }}>
                        <span style={{ fontSize: '1.75rem', fontWeight: 800 }}>{complianceData?.completeness_percentage ?? overviewData?.completeness_percentage ?? overviewData?.compliance_completeness_pct ?? 0}%</span>
                        <div style={{ fontSize: '0.65rem', color: '#64748b', textTransform: 'uppercase', fontWeight: 700 }}>Completeness</div>
                      </div>
                    </div>
                    
                    <div style={{ width: '100%', borderTop: '1px solid #e2e8f0', paddingTop: '1rem', fontSize: '0.85rem' }}>
                      <p style={{ display: 'flex', justifyContent: 'space-between', margin: '0.35rem 0' }}>
                        <span>🟢 Fully Documented (Green)</span>
                        <strong>{complianceData?.green_count ?? overviewData?.green_count ?? 0} blocks</strong>
                      </p>
                      <p style={{ display: 'flex', justifyContent: 'space-between', margin: '0.35rem 0' }}>
                        <span>🟡 Minor element missing (Amber)</span>
                        <strong>{complianceData?.amber_count ?? overviewData?.amber_count ?? 0} blocks</strong>
                      </p>
                      <p style={{ display: 'flex', justifyContent: 'space-between', margin: '0.35rem 0' }}>
                        <span>🔴 High alert missing (Red)</span>
                        <strong>{complianceData?.red_count ?? overviewData?.red_count ?? 0} blocks</strong>
                      </p>
                    </div>
                  </div>
                </div>

                {/* Needs Attention alert feed */}
                <div className="card">
                  <div className="card-title">Sizing Items Requiring Attention</div>
                  <div style={{ maxHeight: 290, overflowY: 'auto', fontSize: '0.8rem' }}>
                    {complianceData?.needs_attention?.map((item, idx) => (
                      <div 
                        key={idx} 
                        style={{ borderBottom: '1px solid #f1f5f9', padding: '0.5rem 0', cursor: 'pointer' }}
                        onClick={() => { setActiveTab('registry'); handleSelectBlock(item.block_id); }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 700 }}>
                          <span style={{ color: item.severity === 'CRITICAL' ? '#ef4444' : '#f59e0b' }}>
                            {item.severity === 'CRITICAL' ? '⚠️ CRITICAL' : '⚡ WARNING'}
                          </span>
                          <span>{item.block_id}</span>
                        </div>
                        <div style={{ color: '#475569', marginTop: '0.15rem' }}>{item.reason}</div>
                        <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: '0.15rem' }}>Missing: {item.missing.join(', ')}</div>
                      </div>
                    ))}
                    {complianceData?.needs_attention?.length === 0 && (
                      <p style={{ color: '#94a3b8', textAlign: 'center', padding: '2rem' }}>All blocks passed full audit checks.</p>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* History Reference Banner Section - Exact Replicated Design */}
            <div style={{ 
              display: 'grid', 
              gridTemplateColumns: '1fr 1fr', 
              background: '#F3EFE9', // Warm beige background
              borderRadius: '24px', 
              height: '500px', 
              marginTop: '2.5rem', 
              overflow: 'hidden',
              position: 'relative',
              boxShadow: '0 4px 20px -2px rgba(0,0,0,0.03)',
              border: '1px solid #E6DEC9'
            }}>
              {/* Left Column */}
              <div style={{ padding: '3.5rem', display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
                {/* Single mathematically centered clock diamond SVG */}
                <svg width="80" height="80" viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ marginBottom: '1rem', marginLeft: '-5px' }}>
                  {/* Rotated Diamond Outline */}
                  <rect x="20" y="20" width="40" height="40" rx="4" stroke="#2e2e2e" strokeWidth="1.5" fill="none" transform="rotate(45 40 40)" />
                  
                  {/* Centered Clock Timer & Timeline */}
                  <circle cx="40" cy="34" r="5" stroke="#ea580c" strokeWidth="1.8" />
                  <polyline points="40 31.5 40 34 42 34" stroke="#ea580c" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                  
                  <path d="M28 46h24" stroke="#ea580c" strokeWidth="1.8" strokeLinecap="round" />
                  <circle cx="31" cy="46" r="1.5" fill="#ea580c" />
                  <circle cx="40" cy="46" r="1.5" fill="#ea580c" />
                  <circle cx="49" cy="46" r="1.5" fill="#ea580c" />
                </svg>
                
                <h2 style={{ 
                  fontSize: '2.8rem', 
                  fontWeight: '800', 
                  color: '#ea580c', 
                  margin: '0 0 1.25rem 0', 
                  fontFamily: "'Outfit', 'Inter', sans-serif", 
                  letterSpacing: '0.02em', 
                  textTransform: 'uppercase',
                  lineHeight: '1.1'
                }}>History</h2>
                
                <p style={{ 
                  fontSize: '1rem', 
                  lineHeight: '1.6', 
                  color: '#2e2e2e', 
                  margin: 0, 
                  fontWeight: 500,
                  fontFamily: "'Inter', sans-serif",
                  maxWidth: '440px'
                }}>
                  Andhra Pradesh is strategically located on the Southeast coast of India and is a natural gateway to East & Southeast Asia. Andhra Pradesh has abundant natural resources like Barytes, Limestone, Bauxite and a number of minor minerals.
                </p>
              </div>
              
              {/* Right Column - Diamond Composition */}
              <div style={{ position: 'relative', height: '100%', overflow: 'hidden' }}>
                
                {/* 1. Large Center Diamond */}
                <div style={{ 
                  position: 'absolute',
                  width: '320px', 
                  height: '320px', 
                  transform: 'rotate(45deg)', 
                  overflow: 'hidden', 
                  borderRadius: '36px', 
                  border: '10px solid #F3EFE9', 
                  boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
                  top: '90px',
                  right: '90px',
                  zIndex: 3
                }}>
                  <img 
                    src="/graniteblockimage.jpg" 
                    alt="Andhra Pradesh Quarry Site" 
                    style={{ 
                      width: '140%', 
                      height: '140%', 
                      transform: 'rotate(-45deg) translate(-10%, -5%)', 
                      objectFit: 'cover' 
                    }} 
                  />
                </div>

                {/* 2. Left Medium Diamond */}
                <div style={{ 
                  position: 'absolute',
                  width: '210px', 
                  height: '210px', 
                  transform: 'rotate(45deg)', 
                  overflow: 'hidden', 
                  borderRadius: '28px', 
                  border: '10px solid #F3EFE9', 
                  boxShadow: '0 8px 20px rgba(0,0,0,0.1)',
                  top: '145px',
                  right: '375px',
                  zIndex: 2
                }}>
                  <img 
                    src="/graniteblockimage.jpg" 
                    alt="Andhra Pradesh Quarry Site" 
                    style={{ 
                      width: '200%', 
                      height: '200%', 
                      transform: 'rotate(-45deg) translate(-28%, 20%)', 
                      objectFit: 'cover' 
                    }} 
                  />
                </div>

                {/* 3. Bottom-Right Medium Diamond */}
                <div style={{ 
                  position: 'absolute',
                  width: '240px', 
                  height: '240px', 
                  transform: 'rotate(45deg)', 
                  overflow: 'hidden', 
                  borderRadius: '28px', 
                  border: '10px solid #F3EFE9', 
                  boxShadow: '0 8px 20px rgba(0,0,0,0.1)',
                  top: '290px',
                  right: '-30px',
                  zIndex: 2
                }}>
                  <img 
                    src="/graniteblockimage.jpg" 
                    alt="Andhra Pradesh Quarry Site" 
                    style={{ 
                      width: '180%', 
                      height: '180%', 
                      transform: 'rotate(-45deg) translate(10%, -20%)', 
                      objectFit: 'cover' 
                    }} 
                  />
                </div>

                {/* 4. Top-Right Medium Diamond */}
                <div style={{ 
                  position: 'absolute',
                  width: '240px', 
                  height: '240px', 
                  transform: 'rotate(45deg)', 
                  overflow: 'hidden', 
                  borderRadius: '28px', 
                  border: '10px solid #F3EFE9', 
                  boxShadow: '0 8px 20px rgba(0,0,0,0.1)',
                  top: '-110px',
                  right: '-30px',
                  zIndex: 2
                }}>
                  <img 
                    src="/graniteblockimage.jpg" 
                    alt="Andhra Pradesh Quarry Site" 
                    style={{ 
                      width: '180%', 
                      height: '180%', 
                      transform: 'rotate(-45deg) translate(-10%, 15%)', 
                      objectFit: 'cover' 
                    }} 
                  />
                </div>

              </div>
            </div>

          </div>
        )}

        {/* 2. BLOCKS REGISTRY TAB (Original visual dashboard console) */}
        {activeTab === 'registry' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '2rem' }}>
            
            {/* Sidebar list panel */}
            <div>
              <div className="card">
                <div className="card-title">Filter Inspection Records</div>
                <div className="form-group" style={{ marginBottom: '1rem' }}>
                  <label style={{ fontSize: '0.8rem', fontWeight: 700 }}>Search Block ID</label>
                  <input type="text" className="form-control" placeholder="Search..." value={search} onChange={(e) => setSearch(e.target.value)} />
                </div>
                <div className="form-group" style={{ marginBottom: '1rem' }}>
                  <label style={{ fontSize: '0.8rem', fontWeight: 700 }}>Quarry Filter</label>
                  <select className="form-control" value={quarryFilter} onChange={(e) => setQuarryFilter(e.target.value)}>
                    <option value="all">All Quarries</option>
                    {quarries.map(q => <option key={q.quarry_id} value={q.quarry_id}>{q.name}</option>)}
                  </select>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem' }}>
                  <div className="form-group">
                    <label style={{ fontSize: '0.8rem', fontWeight: 700 }}>Review Status</label>
                    <select className="form-control" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                      <option value="all">All</option>
                      <option value="pending">Pending</option>
                      <option value="approved">Approved</option>
                      <option value="rejected">Rejected</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label style={{ fontSize: '0.8rem', fontWeight: 700 }}>CV Status</label>
                    <select className="form-control" value={cvStatusFilter} onChange={(e) => setCvStatusFilter(e.target.value)}>
                      <option value="all">All</option>
                      <option value="success">Success</option>
                      <option value="failed">Failed</option>
                    </select>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-title">
                  <span>Blocks Registry ({filteredBlocks.length})</span>
                </div>
                <div style={{ maxHeight: '400px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {filteredBlocks.map(b => (
                    <div 
                      key={b.block_id}
                      onClick={() => handleSelectBlock(b.block_id)}
                      style={{ padding: '0.75rem', border: '1px solid #e2e8f0', borderRadius: '8px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: selectedBlockId === b.block_id ? '#eff6ff' : '#fff', borderColor: selectedBlockId === b.block_id ? '#3b82f6' : '#e2e8f0' }}
                    >
                      <div>
                        <strong>{b.block_id}</strong>
                        <div style={{ fontSize: '0.7rem', color: '#64748b' }}>Quarry: {b.quarry?.name || b.quarry_id}</div>
                      </div>
                      <span className={`badge badge-${b.approval_status || 'pending'}`}>{b.approval_status || 'pending'}</span>
                    </div>
                  ))}
                  {filteredBlocks.length === 0 && (
                    <p style={{ textAlign: 'center', color: '#94a3b8', padding: '1rem' }}>No records matches.</p>
                  )}
                </div>
              </div>
            </div>

            {/* Block inspections details panel */}
            <div>
              {selectedBlock ? (
                <div className="card">
                  <div className="card-title">
                    Block Review Details: {selectedBlock.block_id}
                    <span className={`badge badge-${selectedBlock.approval_status || 'pending'}`}>{selectedBlock.approval_status || 'pending'}</span>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem', marginBottom: '1.5rem', fontSize: '0.85rem' }}>
                    <div>
                      <p><strong>Quarry ID:</strong> {selectedBlock.quarry?.name || selectedBlock.quarry_id || 'N/A'}</p>
                      <p><strong>GPS Latitude:</strong> {selectedBlock.gps_latitude || 'N/A'}</p>
                      <p><strong>GPS Longitude:</strong> {selectedBlock.gps_longitude || 'N/A'}</p>
                      <p><strong>Captured Time:</strong> {selectedBlock.captured_at ? new Date(selectedBlock.captured_at).toLocaleString() : 'N/A'}</p>
                      <p><strong>Inspector ID:</strong> {selectedBlock.inspecting_officer_id || 'N/A'}</p>
                    </div>
                    <div>
                      <p><strong>Lighting Condition:</strong> {selectedBlock.lighting_condition || 'Sunny'}</p>
                      <p><strong>Capture Attempts:</strong> {selectedBlock.capture_attempt_count || 1}</p>
                      <p><strong>Device ID:</strong> {selectedBlock.device_id || 'DEV-N/A'}</p>
                      <p><strong>Supervisor Approved By:</strong> {selectedBlock.approved_by || 'Unreviewed'}</p>
                      <p><strong>Approved Time:</strong> {selectedBlock.approved_at ? new Date(selectedBlock.approved_at).toLocaleString() : 'N/A'}</p>
                    </div>
                  </div>

                  <div className="card-title" style={{ fontSize: '1rem' }}>Active Sizing Dimensions</div>
                  {selectedBlock.measurement ? (
                    <>
                      <div className="metrics-grid">
                        <div className="metric-card">
                          <div className="metric-value">{selectedBlock.measurement.length_m.toFixed(2)} m</div>
                          <div className="metric-label">Length</div>
                        </div>
                        <div className="metric-card">
                          <div className="metric-value">{selectedBlock.measurement.breadth_m.toFixed(2)} m</div>
                          <div className="metric-label">Breadth</div>
                        </div>
                        <div className="metric-card">
                          <div className="metric-value">{selectedBlock.measurement.height_m.toFixed(2)} m</div>
                          <div className="metric-label">Height</div>
                        </div>
                        <div className="metric-card" style={{ backgroundColor: '#ecfdf5', borderColor: '#a7f3d0' }}>
                          <div className="metric-value" style={{ color: '#059669' }}>{selectedBlock.measurement.volume_m3.toFixed(4)} m³</div>
                          <div className="metric-label">Volume</div>
                        </div>
                      </div>
                      <p style={{ fontSize: '0.8rem', color: '#64748b', marginBottom: '1rem' }}>
                        CV Sizing confidence margin: <strong>{Math.round(selectedBlock.measurement.confidence * 100)}%</strong>
                      </p>
                    </>
                  ) : (
                    <p style={{ textAlign: 'center', color: '#94a3b8', padding: '1rem' }}>No measurement found.</p>
                  )}

                  {selectedBlock.is_overridden && selectedBlock.original_measurement && (
                    <div style={{ background: '#fef2f2', border: '1px solid #fee2e2', padding: '1rem', borderRadius: '8px', marginBottom: '1rem' }}>
                      <h5 style={{ color: '#991b1b', margin: '0 0 0.5rem 0' }}>Original Computer Vision Estimates (Preserved)</h5>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', fontSize: '0.8rem', textAlign: 'center' }}>
                        <div><strong>Length:</strong> {selectedBlock.original_measurement.length_m}m</div>
                        <div><strong>Breadth:</strong> {selectedBlock.original_measurement.breadth_m}m</div>
                        <div><strong>Height:</strong> {selectedBlock.original_measurement.height_m}m</div>
                        <div><strong>Volume:</strong> {selectedBlock.original_measurement.volume_m3}m³</div>
                      </div>
                      <p style={{ fontSize: '0.75rem', color: '#991b1b', marginTop: '0.5rem' }}><strong>Override justification:</strong> {selectedBlock.override_reason}</p>
                    </div>
                  )}

                  {/* Visual assets overlay */}
                  <div className="media-preview-container">
                    <div className="media-box">
                      <div className="metric-label" style={{ position: 'absolute', top: 5, left: 5, background: 'rgba(0,0,0,0.6)', color: 'white', padding: '0.1rem 0.4rem', borderRadius: 4, zIndex: 5 }}>Raw Photo</div>
                      <img 
                        src={selectedBlock.raw_image_path ? (selectedBlock.raw_image_path.startsWith('/') ? selectedBlock.raw_image_path : `/media/${selectedBlock.raw_image_path}`) : "/Test-images/1.jpeg"} 
                        alt="Original" 
                        onError={(e) => { e.target.onerror = null; e.target.src = "/Test-images/1.jpeg"; }} 
                      />
                    </div>
                    <div className="media-box">
                      <div className="metric-label" style={{ position: 'absolute', top: 5, left: 5, background: 'rgba(30,58,138,0.8)', color: 'white', padding: '0.1rem 0.4rem', borderRadius: 4, zIndex: 5 }}>CV Annotations</div>
                      <img 
                        src={selectedBlock.annotated_image_path ? (selectedBlock.annotated_image_path.startsWith('/') ? selectedBlock.annotated_image_path : `/media/${selectedBlock.annotated_image_path}`) : "/Test-images/2.jpeg"} 
                        alt="Annotated" 
                        onError={(e) => { e.target.onerror = null; e.target.src = "/Test-images/2.jpeg"; }} 
                      />
                    </div>
                  </div>

                  {/* Actions Buttons */}
                  <div style={{ marginTop: '1.5rem', display: 'flex', gap: '0.5rem' }}>
                    <button className="btn btn-success" onClick={() => handleApprove('approved')}>Approve Sizing</button>
                    <button className="btn btn-danger" onClick={() => handleApprove('rejected')}>Reject Block</button>
                    <button className="btn btn-secondary" onClick={() => setShowOverride(!showOverride)}>Override Sizing</button>
                    <a href={`/api/blocks/${selectedBlock.block_id}/pdf/`} target="_blank" className="btn btn-outline">🖨 Export PDF</a>
                  </div>

                  {showOverride && (
                    <div className="card" style={{ marginTop: '1rem', background: '#f8fafc' }}>
                      <div className="card-title" style={{ fontSize: '0.95rem' }}>Manual Sizing Override Input</div>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem', marginBottom: '0.5rem' }}>
                        <div>
                          <label style={{ fontSize: '0.75rem' }}>Length (m)</label>
                          <input type="number" step="0.01" className="form-control" value={ovLen} onChange={(e) => setOvLen(e.target.value)} />
                        </div>
                        <div>
                          <label style={{ fontSize: '0.75rem' }}>Breadth (m)</label>
                          <input type="number" step="0.01" className="form-control" value={ovBrd} onChange={(e) => setOvBrd(e.target.value)} />
                        </div>
                        <div>
                          <label style={{ fontSize: '0.75rem' }}>Height (m)</label>
                          <input type="number" step="0.01" className="form-control" value={ovHgt} onChange={(e) => setOvHgt(e.target.value)} />
                        </div>
                      </div>
                      <div className="form-group" style={{ marginBottom: '0.5rem' }}>
                        <label style={{ fontSize: '0.75rem' }}>Justification Reason</label>
                        <input type="text" className="form-control" placeholder="Describe override necessity..." value={ovReason} onChange={(e) => setOvReason(e.target.value)} />
                      </div>
                      <button className="btn btn-primary" onClick={handleOverrideSubmit} style={{ width: '100%' }}>Save Manual Dimensions Override</button>
                    </div>
                  )}

                  {/* Seigniorage calculations panel inside registry review */}
                  <div className="card" style={{ marginTop: '1.5rem', background: '#eff6ff', borderColor: '#bfdbfe' }}>
                    <div className="card-title" style={{ fontSize: '0.95rem', color: '#1e40af' }}>
                      Automated Seigniorage Billing
                      <button className="btn btn-primary" onClick={handleCalculateAssessment} style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}>Recalculate</button>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginBottom: '0.5rem', fontSize: '0.8rem' }}>
                      <div>
                        <label>Granite Category</label>
                        <select className="form-control" value={assCat} onChange={(e) => setAssCat(e.target.value)}>
                          <option value="Premium">Premium</option>
                          <option value="Standard">Standard</option>
                          <option value="Commercial">Commercial</option>
                        </select>
                      </div>
                      <div>
                        <label>Gangsaw Size Class</label>
                        <select className="form-control" value={assCls} onChange={(e) => setAssCls(e.target.value)}>
                          <option value="Gangsaw Size">Gangsaw Size</option>
                          <option value="Mini Gangsaw Size">Mini Gangsaw Size</option>
                          <option value="Scabos/Other">Scabos/Other</option>
                        </select>
                      </div>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0.5rem', fontSize: '0.8rem' }}>
                      <div>
                        <label>Calculated Weight</label>
                        <input type="text" className="form-control" value={assWeight} readOnly style={{ background: '#cbd5e1' }} />
                      </div>
                      <div>
                        <label>indicative Rate</label>
                        <input type="text" className="form-control" value={assRate} readOnly style={{ background: '#cbd5e1' }} />
                      </div>
                      <div>
                        <label style={{ fontWeight: 700 }}>Seigniorage Fee</label>
                        <input type="text" className="form-control" value={assSeig} readOnly style={{ background: '#cbd5e1', fontWeight: 700 }} />
                      </div>
                    </div>
                  </div>

                  {/* Audits Log timeline */}
                  <div style={{ marginTop: '1.5rem' }}>
                    <h4 className="card-title" style={{ fontSize: '0.95rem' }}>Block Transaction Timelines</h4>
                    <div className="timeline">
                      {logs.map((log, id) => (
                        <div key={id} className="timeline-item">
                          <div className="timeline-time">{log.timestamp}</div>
                          <div className="timeline-title">{log.action.replace('_', ' ').toUpperCase()} (actor: {log.actor})</div>
                          <div className="timeline-desc">{log.details}</div>
                        </div>
                      ))}
                    </div>
                  </div>

                </div>
              ) : (
                <div className="card" style={{ padding: '5rem', textAlign: 'center', color: '#94a3b8' }}>
                  Select an inspection block to audit dimensions.
                </div>
              )}
            </div>

          </div>
        )}

        {/* 3. OFFICERS PERFORMANCE TAB */}
        {activeTab === 'officers' && (
          <div>
            <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '1.25rem', fontWeight: 700, color: '#1e3a8a' }}>Officer Performance & Audit Scorecards</span>
              <button onClick={() => exportCSV('officers')} className="btn btn-outline">📥 Export CSV Performance Sheet</button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: '2rem' }}>
              
              {/* Officers comparison table */}
              <div className="card">
                <div className="card-title">Mines Department Officer Performance registry</div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                    <thead>
                      <tr style={{ background: '#f8fafc', borderBottom: '1px solid #cbd5e1', textAlign: 'left' }}>
                        <th style={{ padding: '0.75rem' }}>Officer</th>
                        <th style={{ padding: '0.75rem' }}>Designation</th>
                        <th style={{ padding: '0.75rem' }}>Blocks Checked</th>
                        <th style={{ padding: '0.75rem' }}>Avg Confidence</th>
                        <th style={{ padding: '0.75rem' }}>Override Rate</th>
                        <th style={{ padding: '0.75rem' }}>Approval Rate</th>
                        <th style={{ padding: '0.75rem' }}>Compliance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {officers.map(o => (
                        <tr 
                          key={o.officer_id}
                          onClick={() => handleSelectOfficer(o.officer_id)}
                          style={{ borderBottom: '1px solid #f1f5f9', cursor: 'pointer', background: selectedOfficerId === o.officer_id ? '#f0f9ff' : 'transparent' }}
                        >
                          <td style={{ padding: '0.75rem', fontWeight: 600 }}>{o.name}</td>
                          <td style={{ padding: '0.75rem', color: '#475569' }}>{o.designation}</td>
                          <td style={{ padding: '0.75rem' }}>{o.total_blocks}</td>
                          <td style={{ padding: '0.75rem' }}>{Math.round(o.avg_confidence * 100)}%</td>
                          <td style={{ padding: '0.75rem', color: o.override_rate > 0.15 ? '#ef4444' : '#1e293b' }}>{Math.round(o.override_rate * 100)}%</td>
                          <td style={{ padding: '0.75rem' }}>{Math.round(o.approval_rate * 100)}%</td>
                          <td style={{ padding: '0.75rem' }}>
                            <span style={{ 
                              background: o.override_rate > 0.2 ? '#fee2e2' : o.override_rate > 0.1 ? '#fef3c7' : '#d1fae5', 
                              color: o.override_rate > 0.2 ? '#b91c1c' : o.override_rate > 0.1 ? '#b45309' : '#047857',
                              padding: '0.15rem 0.4rem', borderRadius: '4px', fontSize: '0.7rem', fontWeight: 700
                            }}>
                              {o.override_rate > 0.2 ? 'RED ALERT' : o.override_rate > 0.1 ? 'AMBER ALERT' : 'OPTIMAL'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Officer details weekly trend charts */}
              <div>
                <div className="card">
                  <div className="card-title">Weekly Inspection & Confidence Trend (6 Weeks)</div>
                  <div style={{ width: '100%', height: 220, marginBottom: '1.5rem' }}>
                    <ResponsiveContainer>
                      <LineChart data={officerTrend}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="week" />
                        <YAxis />
                        <Tooltip />
                        <Legend />
                        <Line type="monotone" dataKey="blocks_inspected" name="Blocks Inspected" stroke="#3b82f6" strokeWidth={2} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                  <div style={{ width: '100%', height: 220 }}>
                    <ResponsiveContainer>
                      <LineChart data={officerTrend}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="week" />
                        <YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                        <Tooltip formatter={(v) => `${Math.round(v * 100)}%`} />
                        <Legend />
                        <Line type="monotone" dataKey="avg_confidence" name="Avg CV Confidence" stroke="#10b981" strokeWidth={2} />
                        <Line type="monotone" dataKey="override_rate" name="Override Rate" stroke="#f59e0b" strokeWidth={2} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>

            </div>
          </div>
        )}

        {/* 4. QUARRY ANALYSIS TAB */}
        {activeTab === 'quarries' && (
          <div>
            <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '1.25rem', fontWeight: 700, color: '#1e3a8a' }}>Quarry Performance Sizing Analysis</span>
              <button onClick={() => exportCSV('quarries')} className="btn btn-outline">📥 Export CSV Sizing Sheet</button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: '2rem' }}>
              
              {/* Quarry list table */}
              <div className="card">
                <div className="card-title">Quarry Comparison Statistics</div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                    <thead>
                      <tr style={{ background: '#f8fafc', borderBottom: '1px solid #cbd5e1', textAlign: 'left' }}>
                        <th style={{ padding: '0.75rem' }}>Quarry</th>
                        <th style={{ padding: '0.75rem' }}>District</th>
                        <th style={{ padding: '0.75rem' }}>Blocks Checked</th>
                        <th style={{ padding: '0.75rem' }}>Total Volume (m³)</th>
                        <th style={{ padding: '0.75rem' }}>Indicative Revenue (INR)</th>
                        <th style={{ padding: '0.75rem' }}>Override Rate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {quarries.map(q => (
                        <tr key={q.quarry_id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                          <td style={{ padding: '0.75rem', fontWeight: 600 }}>{q.name}</td>
                          <td style={{ padding: '0.75rem' }}>{q.district}</td>
                          <td style={{ padding: '0.75rem' }}>{q.total_blocks}</td>
                          <td style={{ padding: '0.75rem' }}>{q.total_volume.toFixed(2)}</td>
                          <td style={{ padding: '0.75rem', color: '#10b981', fontWeight: 600 }}>{q.total_revenue.toLocaleString()}</td>
                          <td style={{ padding: '0.75rem' }}>{Math.round(q.override_rate * 100)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Side-by-side comparison selector widgets */}
              <div>
                <div className="card">
                  <div className="card-title">Side-by-Side Sizing Comparison</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginBottom: '1.5rem' }}>
                    <div>
                      <label style={{ fontSize: '0.75rem', fontWeight: 700 }}>Select Quarry A</label>
                      <select className="form-control" value={selectedQuarryA} onChange={(e) => handleSelectQuarryA(e.target.value)}>
                        {quarries.map(q => <option key={q.quarry_id} value={q.quarry_id}>{q.name}</option>)}
                      </select>
                    </div>
                    <div>
                      <label style={{ fontSize: '0.75rem', fontWeight: 700 }}>Select Quarry B</label>
                      <select className="form-control" value={selectedQuarryB} onChange={(e) => handleSelectQuarryB(e.target.value)}>
                        {quarries.map(q => <option key={q.quarry_id} value={q.quarry_id}>{q.name}</option>)}
                      </select>
                    </div>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', textAlign: 'center' }}>
                    <div style={{ background: '#f8fafc', padding: '0.75rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                      <h4 style={{ margin: 0, fontSize: '0.9rem', color: '#3b82f6' }}>Quarry A</h4>
                      <p style={{ margin: '0.5rem 0 0 0', fontSize: '1.25rem', fontWeight: 800 }}>
                        INR {Number(quarries.find(q => q.quarry_id === selectedQuarryA)?.total_revenue || 0).toLocaleString()}
                      </p>
                      <p style={{ margin: 0, fontSize: '0.75rem', color: '#64748b' }}>
                        Volume: {Number(quarries.find(q => q.quarry_id === selectedQuarryA)?.total_volume || 0).toFixed(2)} m³
                      </p>
                      <p style={{ margin: 0, fontSize: '0.75rem', color: '#64748b' }}>
                        Override: {Math.round(Number(quarries.find(q => q.quarry_id === selectedQuarryA)?.override_rate || 0) * 100)}%
                      </p>
                    </div>
                    <div style={{ background: '#f8fafc', padding: '0.75rem', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
                      <h4 style={{ margin: 0, fontSize: '0.9rem', color: '#8b5cf6' }}>Quarry B</h4>
                      <p style={{ margin: '0.5rem 0 0 0', fontSize: '1.25rem', fontWeight: 800 }}>
                        INR {Number(quarries.find(q => q.quarry_id === selectedQuarryB)?.total_revenue || 0).toLocaleString()}
                      </p>
                      <p style={{ margin: 0, fontSize: '0.75rem', color: '#64748b' }}>
                        Volume: {Number(quarries.find(q => q.quarry_id === selectedQuarryB)?.total_volume || 0).toFixed(2)} m³
                      </p>
                      <p style={{ margin: 0, fontSize: '0.75rem', color: '#64748b' }}>
                        Override: {Math.round(Number(quarries.find(q => q.quarry_id === selectedQuarryB)?.override_rate || 0) * 100)}%
                      </p>
                    </div>
                  </div>
                </div>

                <div className="card">
                  <div className="card-title">Weekly Billing Sizing Revenue Comparison (INR)</div>
                  <div style={{ width: '100%', height: 200 }}>
                    <ResponsiveContainer>
                      <LineChart>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="week" allowDuplicatedCategory={false} />
                        <YAxis />
                        <Tooltip />
                        <Legend />
                        <Line data={quarryTrendA} type="monotone" dataKey="indicative_revenue" name="Quarry A Sizing" stroke="#3b82f6" strokeWidth={2} />
                        <Line data={quarryTrendB} type="monotone" dataKey="indicative_revenue" name="Quarry B Sizing" stroke="#8b5cf6" strokeWidth={2} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>

            </div>
          </div>
        )}

        {/* 5. REVENUE SUMMARY TAB */}
        {activeTab === 'revenue' && (
          <div>
            <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '1.25rem', fontWeight: 700, color: '#1e3a8a' }}>Revenue Summary & AI Sizing Recoveries</span>
              <button onClick={() => exportCSV('revenue')} className="btn btn-outline">📥 Export CSV Billing Summary</button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: '2rem' }}>
              <div>
                {/* Large HUD seigniorage metric */}
                <div className="card" style={{ background: '#ecfdf5', borderColor: '#a7f3d0', textAlign: 'center' }}>
                  <h2 style={{ margin: 0, fontSize: '1rem', color: '#047857', textTransform: 'uppercase', letterSpacing: '0.05em' }}>TOTAL INDICATIVE SEIGNIORAGE</h2>
                  <div style={{ fontSize: '3rem', fontWeight: 900, color: '#065f46', margin: '0.5rem 0' }}>INR {revenueData?.total_seigniorage?.toLocaleString()}</div>
                  <p style={{ margin: 0, color: '#047857', fontSize: '0.85rem' }}>Calculated across {revenueData?.block_count} processed blocks ({revenueData?.total_volume?.toFixed(2)} m³ total volume)</p>
                </div>

                {/* Category revenues breakdown charts */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                  <div className="card">
                    <div className="card-title">Billing Categories Distribution (INR)</div>
                    <div style={{ width: '100%', height: 220 }}>
                      <ResponsiveContainer>
                        <BarChart data={revenueData?.category_revenue}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="category" />
                          <YAxis />
                          <Tooltip />
                          <Bar dataKey="revenue" fill="#3b82f6" />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  <div className="card">
                    <div className="card-title">Gangsaw Size Class billing (INR)</div>
                    <div style={{ width: '100%', height: 220 }}>
                      <ResponsiveContainer>
                        <BarChart data={[
                          { name: 'Gangsaw size', value: revenueData?.gangsaw_revenue },
                          { name: 'Below Gangsaw', value: revenueData?.below_gangsaw_revenue }
                        ]}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="name" />
                          <YAxis />
                          <Tooltip />
                          <Bar dataKey="value" fill="#10b981" />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                </div>
              </div>

              {/* Recovery statistics estimation card */}
              <div>
                <div className="card" style={{ borderLeft: '4px solid #8b5cf6', background: '#faf5ff' }}>
                  <div className="card-title" style={{ color: '#6b21a8' }}>AI Sizing Revenue Recovery Estimate</div>
                  <div style={{ fontSize: '2rem', fontWeight: 900, color: '#6b21a8', marginBottom: '0.5rem' }}>
                    INR {leakageData?.estimated_revenue_recovered?.toLocaleString()}
                  </div>
                  <p style={{ fontSize: '0.85rem', color: '#581c87', lineHeight: 1.5 }}>
                    This estimation shows the recovered geological billing fees saved from under-reported sizing returns. Calculations scale volume discrepancy variance by verified block rates.
                  </p>
                  <div style={{ background: '#f3e8ff', padding: '0.75rem', borderRadius: '6px', fontSize: '0.8rem', color: '#581c87', marginTop: '1rem', border: '1px solid #d8b4fe' }}>
                    <strong>Average block variance detected:</strong> {leakageData?.average_leakage_pct}% discrepancies out of weighbridge estimates.
                  </div>
                </div>
              </div>

            </div>
          </div>
        )}

        {/* 6. COMPLIANCE AUDIT TAB */}
        {activeTab === 'compliance' && (
          <div>
            <div className="card">
              <div className="card-title">Sizing Completeness & SLA Audit readiness</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem', textAlign: 'center', marginBottom: '1.5rem' }}>
                <div style={{ background: '#d1fae5', padding: '1rem', borderRadius: '8px', border: '1px solid #a7f3d0' }}>
                  <h4 style={{ margin: 0, color: '#065f46' }}>Green Checks (Complete)</h4>
                  <p style={{ margin: '0.5rem 0 0 0', fontSize: '1.75rem', fontWeight: 800, color: '#065f46' }}>{complianceData?.green_count}</p>
                </div>
                <div style={{ background: '#fef3c7', padding: '1rem', borderRadius: '8px', border: '1px solid #fde68a' }}>
                  <h4 style={{ margin: 0, color: '#92400e' }}>Amber Checks (Minor Missing)</h4>
                  <p style={{ margin: '0.5rem 0 0 0', fontSize: '1.75rem', fontWeight: 800, color: '#92400e' }}>{complianceData?.amber_count}</p>
                </div>
                <div style={{ background: '#fee2e2', padding: '1rem', borderRadius: '8px', border: '1px solid #fecaca' }}>
                  <h4 style={{ margin: 0, color: '#991b1b' }}>Red Checks (Critical Errors)</h4>
                  <p style={{ margin: '0.5rem 0 0 0', fontSize: '1.75rem', fontWeight: 800, color: '#991b1b' }}>{complianceData?.red_count}</p>
                </div>
              </div>

              <div className="card-title" style={{ fontSize: '1rem' }}>Non-Compliant Blocks List (Needs Attention)</div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                  <thead>
                    <tr style={{ background: '#f8fafc', borderBottom: '1px solid #cbd5e1', textAlign: 'left' }}>
                      <th style={{ padding: '0.75rem' }}>Block ID</th>
                      <th style={{ padding: '0.75rem' }}>Quarry</th>
                      <th style={{ padding: '0.75rem' }}>Non-Compliance Reason</th>
                      <th style={{ padding: '0.75rem' }}>Missing elements</th>
                      <th style={{ padding: '0.75rem' }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {complianceData?.needs_attention?.map((item, idx) => (
                      <tr key={idx} style={{ borderBottom: '1px solid #f1f5f9' }}>
                        <td style={{ padding: '0.75rem', fontWeight: 700 }}>{item.block_id}</td>
                        <td style={{ padding: '0.75rem' }}>{item.quarry}</td>
                        <td style={{ padding: '0.75rem', color: item.severity === 'CRITICAL' ? '#ef4444' : '#f59e0b', fontWeight: 600 }}>{item.reason}</td>
                        <td style={{ padding: '0.75rem', fontStyle: 'italic' }}>{item.missing.join(', ')}</td>
                        <td style={{ padding: '0.75rem' }}>
                          <button onClick={() => { setActiveTab('registry'); handleSelectBlock(item.block_id); }} className="btn btn-outline" style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}>🔧 Resolve</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {/* 7. ALERTS FEED TAB */}
        {activeTab === 'alerts' && (
          <div>
            <div className="card">
              <div className="card-title">
                Sizing alerts feed
                <div>
                  <label style={{ fontSize: '0.8rem', marginRight: '0.5rem' }}>Filter Severity:</label>
                  <select 
                    style={{ padding: '0.35rem 0.5rem', borderRadius: '4px', border: '1px solid #cbd5e1', fontSize: '0.8rem' }}
                    value={alertSeverityFilter}
                    onChange={(e) => setAlertSeverityFilter(e.target.value)}
                  >
                    <option value="all">All Severities</option>
                    <option value="CRITICAL">🔴 CRITICAL</option>
                    <option value="WARNING">🟡 WARNING</option>
                    <option value="INFO">🔵 INFO</option>
                  </select>
                </div>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                {alerts
                  .filter(a => alertSeverityFilter === 'all' || a.severity === alertSeverityFilter)
                  .map((alert, idx) => (
                    <div 
                      key={idx} 
                      style={{ 
                        border: '1px solid',
                        borderColor: alert.severity === 'CRITICAL' ? '#fecaca' : alert.severity === 'WARNING' ? '#fde68a' : '#bfdbfe',
                        background: alert.severity === 'CRITICAL' ? '#fef2f2' : alert.severity === 'WARNING' ? '#fffbeb' : '#eff6ff',
                        padding: '1rem',
                        borderRadius: '8px'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem' }}>
                        <span style={{ 
                          fontWeight: 800, 
                          color: alert.severity === 'CRITICAL' ? '#b91c1c' : alert.severity === 'WARNING' ? '#b45309' : '#1e40af',
                          fontSize: '0.8rem'
                        }}>
                          {alert.severity} - {alert.type}
                        </span>
                        <span style={{ fontSize: '0.75rem', color: '#64748b' }}>{alert.timestamp}</span>
                      </div>
                      <h4 style={{ margin: '0 0 0.25rem 0', color: '#1e293b', fontSize: '0.95rem' }}>{alert.title}</h4>
                      <p style={{ margin: '0 0 0.5rem 0', fontSize: '0.85rem', color: '#475569' }}>{alert.description}</p>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.75rem', color: '#64748b' }}>
                        <span>Target: <strong>{alert.related_entity}</strong></span>
                        <button onClick={() => { setActiveTab('registry'); handleSelectBlock(alert.related_entity.split(':').pop().strip()); }} className="btn btn-outline" style={{ padding: '0.15rem 0.4rem', fontSize: '0.7rem' }}>Inspect Block</button>
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          </div>
        )}

        {/* 8. GEOSPATIAL MAP TAB (Custom visuals to ensure 100% stability) */}
        {activeTab === 'map' && (
          <div className="card">
            <div className="card-title">Geospatial Quarry Sizing Locations (Andhra Pradesh State Clusters)</div>
            <div className="disclaimer-alert">
              <span className="disclaimer-icon">🌐</span>
              <div>
                <strong>Map Coordinate labels:</strong> Visualized locations represent synthetic demo coords inside Prakasam, Nellore, and Chittoor districts bounding box ranges.
              </div>
            </div>

            {/* Visual interactive AP cluster simulation map */}
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '2rem' }}>
              
              {/* Interactive Vector map box */}
              <div style={{ height: '480px', border: '1px solid #cbd5e1', borderRadius: '12px', background: '#e0f2fe', position: 'relative', overflow: 'hidden' }}>
                
                {/* Visual coordinate grid lines */}
                <div style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', opacity: 0.15, pointerEvents: 'none', background: 'radial-gradient(circle, #0284c7 10%, transparent 11%)', backgroundSize: '20px 20px' }} />
                
                {/* Coordinate axis labels */}
                <div style={{ position: 'absolute', bottom: 10, left: 10, background: 'rgba(255,255,255,0.8)', padding: '0.2rem 0.5rem', borderRadius: 4, fontSize: '0.7rem', color: '#64748b' }}>
                  Demo AP Bounding Box: 14.0°N - 16.5°N | 78.5°E - 80.5°E
                </div>

                {/* State outline overlay labels */}
                <div style={{ position: 'absolute', top: '20%', left: '40%', fontSize: '1.5rem', fontWeight: 900, color: '#bae6fd', opacity: 0.8, textTransform: 'uppercase', pointerEvents: 'none' }}>
                  Andhra Pradesh
                </div>

                {/* Render coordinates points */}
                {mapData?.quarries?.map((q, idx) => {
                  // Normalize coordinates to map percentage boundaries
                  const topPct = 100 - ((q.latitude - 14.0) / (16.5 - 14.0) * 100);
                  const leftPct = (q.longitude - 78.5) / (80.5 - 78.5) * 100;

                  return (
                    <div 
                      key={idx}
                      style={{ 
                        position: 'absolute', 
                        top: `${topPct}%`, 
                        left: `${leftPct}%`,
                        transform: 'translate(-50%, -50%)',
                        cursor: 'pointer',
                        zIndex: 10
                      }}
                    >
                      {/* Pulsing marker node */}
                      <div style={{ width: 14, height: 14, background: '#ef4444', borderRadius: '50%', border: '2px solid white', boxShadow: '0 0 6px rgba(0,0,0,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <div style={{ width: 6, height: 6, background: 'white', borderRadius: '50%' }} />
                      </div>
                      
                      {/* Name tags */}
                      <div style={{ position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)', background: 'rgba(15,23,42,0.85)', color: 'white', padding: '0.15rem 0.35rem', borderRadius: 4, fontSize: '0.65rem', whiteSpace: 'nowrap', marginTop: 4 }}>
                        {q.name.split('-').pop()}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Sidebar coordinates list items */}
              <div style={{ maxHeight: 480, overflowY: 'auto' }}>
                <h4 style={{ margin: '0 0 1rem 0', color: '#1e3a8a' }}>Inspected Quarry Details</h4>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                  {mapData?.quarries?.map((q, idx) => (
                    <div key={idx} style={{ background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '8px', padding: '0.75rem' }}>
                      <h5 style={{ margin: '0 0 0.25rem 0', color: '#1e3a8a', fontSize: '0.85rem' }}>{q.name}</h5>
                      <div style={{ fontSize: '0.75rem', color: '#475569' }}>
                        <p style={{ margin: '0.15rem 0' }}>📍 District: <strong>{q.district}</strong></p>
                        <p style={{ margin: '0.15rem 0' }}>🌐 Coords: <strong>{q.latitude.toFixed(4)}°N, {q.longitude.toFixed(4)}°E</strong></p>
                        <p style={{ margin: '0.15rem 0' }}>🧱 Inspected Blocks: <strong>{q.block_count}</strong></p>
                        <p style={{ margin: '0.15rem 0' }}>💰 Indicative Revenue: <strong>INR {q.revenue.toLocaleString()}</strong></p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

            </div>
          </div>
        )}

      </main>
    </div>
  );
}
