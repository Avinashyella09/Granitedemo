import React, { useState } from 'react';
import FieldCapture from './FieldCapture';
import SupervisorDashboard from './SupervisorDashboard';

function App() {
  const [activeTab, setActiveTab] = useState('field-capture');

  return (
    <div>
      {/* Top Gov Banner */}
      <header className="portal-header">
        <div className="header-brand">
          <div className="gov-seal">AP</div>
          <div className="header-title">
            <h1>Granite Block Sizing & Seigniorage Portal</h1>
            <p>Govt. of Andhra Pradesh • Dept. of Mines & Geology (POC)</p>
          </div>
        </div>
        <div>
          <span style={{ fontSize: '0.85rem', background: 'rgba(255,255,255,0.15)', padding: '0.4rem 0.8rem', borderRadius: '50px', fontWeight: '600' }}>
            Secure Officer Session
          </span>
        </div>
      </header>

      {/* Navigation Tabs */}
      <nav className="tabs-nav">
        <button 
          className={`tab-btn ${activeTab === 'field-capture' ? 'active' : ''}`}
          onClick={() => setActiveTab('field-capture')}
        >
          📷 Field Capture Interface
        </button>
        <button 
          className={`tab-btn ${activeTab === 'supervisor-dashboard' ? 'active' : ''}`}
          onClick={() => setActiveTab('supervisor-dashboard')}
        >
          📊 Supervisor Dashboard
        </button>
      </nav>

      {/* Main Content */}
      <main className="main-content">
        {activeTab === 'field-capture' ? (
          <FieldCapture />
        ) : (
          <SupervisorDashboard />
        )}
      </main>
    </div>
  );
}

export default App;
