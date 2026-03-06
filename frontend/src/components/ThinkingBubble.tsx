import './ThinkingBubble.css';

export interface ThinkingStep {
  id: string;
  type: 'thinking' | 'tool_start' | 'tool_done';
  message: string;
  toolName?: string;
  durationMs?: number;
  timestamp: number;
}

interface ThinkingBubbleProps {
  steps: ThinkingStep[];
  isActive: boolean;
}

export function ThinkingBubble({ steps, isActive }: ThinkingBubbleProps) {
  if (steps.length === 0 && !isActive) {
    return null;
  }

  return (
    <div className="thinking-bubble">
      {steps.map((step) => {
        let icon = '🧠';
        if (step.type === 'tool_start') icon = '🔧';
        if (step.type === 'tool_done') icon = '✅';

        return (
          <div key={step.id} className="thinking-step fade-in">
            <span className="thinking-icon">{icon}</span>
            <span className="thinking-message">{step.message}</span>
            {step.durationMs !== undefined && (
              <span className="thinking-duration">{(step.durationMs / 1000).toFixed(1)}s</span>
            )}
          </div>
        );
      })}
      
      {isActive && (
        <div className="thinking-step fade-in">
          <span className="thinking-icon">🧠</span>
          <div className="typing-dots">
            <span></span>
            <span></span>
            <span></span>
          </div>
        </div>
      )}
    </div>
  );
}
