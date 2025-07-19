import React, { useEffect, useState } from 'react';
import Sidebar from './components/Sidebar';
import ConfigEditor from './components/ConfigEditor';
import Navbar from './components/Navbar';
import Orb from './components/Orb';
import { useStore } from './store';
import { useTranslation } from 'react-i18next';
import { useTheme } from './context/ThemeContext';
import EmptyState from './components/EmptyState';
import Terminal from './components/Terminal';
import TerminalToggleButton from './components/TerminalToggleButton';

const App: React.FC = () => {
  const { t } = useTranslation();
  const { theme } = useTheme();
  const { selectedClass, initialize, loading } = useStore();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [isTerminalVisible, setTerminalVisible] = useState(false);

  // Orb controls state (for development)
  const [orbHue, setOrbHue] = useState(32); // 使用强调色橙色
  const [orbOpacity, setOrbOpacity] = useState(theme === 'dark' ? 0.35 : 0.6);
  const [orbHoverIntensity, setOrbHoverIntensity] = useState(1.2);
  const [orbRotateOnHover, setOrbRotateOnHover] = useState(true);
  const isDarkMode = theme === 'dark';

  // Update orb colors when theme changes
  useEffect(() => {
    setOrbHue(32); // 统一使用强调色橙色
    setOrbOpacity(theme === 'dark' ? 0.35 : 0.6); // 大幅提升清晰度
  }, [theme]);

  const loadingOrbOpacity = orbOpacity * 1.5;

  useEffect(() => {
    const init = async () => {
      console.log("[App] Starting initialization...");
      
      // If not in Electron, initialize right away.
      if (!window.api) {
        console.log("[App] Running in browser, initializing directly.");
        try {
          await initialize();
          console.log("[App] Browser initialization completed");
        } catch (error) {
          console.error("[App] Browser initialization failed:", error);
        }
        return;
      }
      
      console.log("[App] Running in Electron, waiting for api-ready event...");
      
      // Check if we can already get the port (in case the event was fired before we listened)
      try {
        const port = await window.api.getApiPort();
        if (port) {
          console.log("[App] Port already available:", port);
          try {
            await initialize();
            console.log("[App] Direct initialization completed");
            return;
          } catch (error) {
            console.error("[App] Direct initialization failed:", error);
          }
        }
      } catch (error) {
        console.log("[App] Port not yet available, will wait for api-ready event");
      }
      
      // Set up the api-ready event listener
      const handleApiReady = async () => {
        console.log("[App] api-ready event received, starting initialization...");
        try {
          await initialize();
          console.log("[App] Event-based initialization completed");
        } catch (error) {
          console.error("[App] Event-based initialization failed:", error);
        }
      };
      
      // If in Electron, wait for the 'api-ready' event from the preload script
      window.addEventListener('api-ready', handleApiReady, { once: true });
      
      // Set a timeout as fallback in case the event never fires
      const timeoutId = setTimeout(async () => {
        console.log("[App] Timeout reached, attempting fallback initialization...");
        window.removeEventListener('api-ready', handleApiReady);
        try {
          await initialize();
          console.log("[App] Fallback initialization completed");
        } catch (error) {
          console.error("[App] Fallback initialization failed:", error);
        }
      }, 10000); // 10 second timeout
      
      // Clean up timeout if event fires
      const originalHandler = handleApiReady;
      const wrappedHandler = async () => {
        clearTimeout(timeoutId);
        await originalHandler();
      };
      
      window.removeEventListener('api-ready', handleApiReady);
      window.addEventListener('api-ready', wrappedHandler, { once: true });
    };
    
    init();

    return () => {
      // Cleanup listener if component unmounts before event fires
      window.removeEventListener('api-ready', initialize);
    }
  }, [initialize]);

  if (loading.classes) {
    return (
      <div className="flex h-screen items-center justify-center bg-background relative">
        {/* Orb background layer */}
        <div className="fixed inset-0 w-full h-full pointer-events-none">
          <Orb hue={orbHue} hoverIntensity={orbHoverIntensity * 0.6} rotateOnHover={orbRotateOnHover} forceHoverState={false} isDarkMode={isDarkMode} />
        </div>
        <div className="text-center relative z-10">
          <h2 className="text-xl font-semibold mb-2">{t('common.loading')}</h2>
          <p className="text-muted-foreground">{t('common.loadingDesc')}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background relative">
      {/* Orb background layer */}
      <div className="fixed inset-0 w-full h-full z-0">
        <Orb hue={orbHue} hoverIntensity={orbHoverIntensity} rotateOnHover={orbRotateOnHover} forceHoverState={false} isDarkMode={isDarkMode} />
      </div>
      
      <div className="relative z-10">
        <Sidebar isOpen={sidebarOpen} onToggle={() => setSidebarOpen(!sidebarOpen)} />
      </div>
      
      <div className="flex flex-col flex-1 overflow-hidden relative z-10">
        <Navbar onMenuClick={() => setSidebarOpen(!sidebarOpen)} />
        
        <main className="flex-1 overflow-auto p-4 md:p-6">
          {selectedClass ? (
            <ConfigEditor />
          ) : (
            <EmptyState
              title={t('common.welcome')}
              description={t('common.selectConfig')}
              icon="Settings"
            />
          )}
        </main>
      </div>
      <Terminal isVisible={isTerminalVisible} />
      <TerminalToggleButton onClick={() => setTerminalVisible(!isTerminalVisible)} />
    </div>
  );
};

export default App;