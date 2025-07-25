import React from 'react';
import './TerminalToggleButton.css';

interface TerminalToggleButtonProps {
  onClick: () => void;
}

const TerminalToggleButton: React.FC<TerminalToggleButtonProps> = ({ onClick }) => {
  return (
    <button onClick={onClick} className="terminal-toggle-button" title="Toggle Terminal">
      <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 17l6-6-6-6"/><path d="M12 19h8"/></svg>
    </button>
  );
};

export default TerminalToggleButton; 