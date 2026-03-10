import { useState } from 'react';
import SessionPanel from './components/SessionPanel';
import InspirationPanel from './components/InspirationPanel';
import ResultPanel from './components/ResultPanel';
import ExportPanel from './components/ExportPanel';
import ModelPanel from './components/ModelPanel';
import ToastContainer from './components/ToastContainer';
import { Settings } from 'lucide-react';

function App() {
  const [showModels, setShowModels] = useState(false);

  return (
    <>
      <ToastContainer />

      <div className="layout-left panel panel-glass">
        <SessionPanel />
        <div style={{ padding: '16px', borderTop: '1px solid var(--border-color)' }}>
           <button className="btn btn-secondary" style={{ width: '100%' }} onClick={() => setShowModels(true)}>
             <Settings size={16} /> 模型设置
           </button>
        </div>
      </div>

      <div className="layout-middle">
        <div className="panel panel-glass" style={{ flex: 1 }}>
          <InspirationPanel />
        </div>
      </div>

      <div className="layout-right">
        <div className="panel panel-glass right-panel-result">
          <ResultPanel />
        </div>
        <div className="panel panel-glass right-panel-export">
          <ExportPanel />
        </div>
      </div>

      {showModels && (
        <ModelPanel onClose={() => setShowModels(false)} />
      )}
    </>
  );
}

export default App;
