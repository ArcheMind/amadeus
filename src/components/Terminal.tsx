import React, { useState, useEffect, useRef } from 'react';
import AnsiToHtml from 'ansi-to-html';
import './Terminal.css';

const converter = new AnsiToHtml({
  fg: '#d4d4d4',
  bg: '#1e1e1e',
  newline: false,
  escapeXML: true,
});

interface TerminalProps {
  isVisible: boolean;
}

const Terminal: React.FC<TerminalProps> = ({ isVisible }) => {
  const [logs, setLogs] = useState<string[]>([]);
  const logsEndRef = useRef<null | HTMLDivElement>(null);
  const [isInitialLoad, setIsInitialLoad] = useState(true);

  useEffect(() => {
    if (window.api) {
      // 获取历史日志
      window.api.getHistoricalLogs().then((historicalLogs: string) => {
        if (historicalLogs) {
          setLogs((prevLogs) => [...prevLogs, historicalLogs]);
        }
      });
      
      // 监听实时日志
      const cleanup = window.api.receive('backend-log', (log: string) => {
        if (isInitialLoad) {
          setIsInitialLoad(false);
        }
        setLogs((prevLogs) => [...prevLogs, log]);
      });

      return cleanup;
    }
  }, []);

  useEffect(() => {
    // 初始加载时，立即滚动到底部；后续更新则平滑滚动
    logsEndRef.current?.scrollIntoView({ behavior: isInitialLoad ? 'auto' : 'smooth' });
  }, [logs, isInitialLoad]);

  return (
    <div className={`terminal-modal ${isVisible ? 'visible' : ''}`}>
      <div className="terminal-container">
        <pre className="logs">
          {logs.map((log, index) => (
            <span key={index} dangerouslySetInnerHTML={{ __html: converter.toHtml(log) }} />
          ))}
          <div ref={logsEndRef} />
        </pre>
      </div>
    </div>
  );
};

export default Terminal; 