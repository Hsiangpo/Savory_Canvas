import { useAppStore } from '../store';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

export default function ToastContainer() {
  const { toasts, removeToast } = useAppStore();

  if (toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`toast toast-${t.type}`}>
           {t.type === 'success' && <CheckCircle2 size={18} className="toast-icon" />}
           {t.type === 'error' && <AlertCircle size={18} className="toast-icon error" />}
           {t.type === 'info' && <Info size={18} className="toast-icon info" />}
           <div className="toast-message">{t.message}</div>
           <button className="btn-icon" onClick={() => removeToast(t.id)}>
             <X size={16} />
           </button>
        </div>
      ))}
    </div>
  );
}
