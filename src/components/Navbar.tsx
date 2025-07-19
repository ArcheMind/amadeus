import React, { useState } from 'react';
import { Menu, Moon, Sun, Languages, Download, Upload } from 'lucide-react';
import { useTheme } from '../context/ThemeContext';
import { useTranslation } from 'react-i18next';
import { useStore } from '../store';
import { ExportDialog } from './ui/ExportDialog';
import { ImportDialog } from './ui/ImportDialog';

interface NavbarProps {
  onMenuClick: () => void;
}

const Navbar: React.FC<NavbarProps> = ({ onMenuClick }) => {
  const { theme, toggleTheme } = useTheme();
  const { i18n } = useTranslation();
  const { configData } = useStore();
  const [showExportDialog, setShowExportDialog] = useState(false);
  const [showImportDialog, setShowImportDialog] = useState(false);

  const toggleLanguage = () => {
    const nextLang = i18n.language.startsWith('zh') ? 'en' : 'zh';
    i18n.changeLanguage(nextLang);
  };

  return (
    <>
      <header className="border-b border-white/8 bg-card/12 backdrop-blur-2xl shadow-sm shadow-primary/5">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center">
            <button
              onClick={onMenuClick}
              className="mr-4 p-2 rounded-md hover:bg-muted transition-colors"
              aria-label="Toggle menu"
            >
              <Menu className="h-5 w-5" />
            </button>
          </div>

          <div className="flex items-center space-x-2">
            <button
              onClick={() => setShowImportDialog(true)}
              className="p-2 rounded-md hover:bg-muted transition-colors flex items-center gap-2"
              aria-label="Import configuration"
            >
              <Upload className="h-5 w-5" />
            </button>
            <button
              onClick={() => setShowExportDialog(true)}
              className="p-2 rounded-md hover:bg-muted transition-colors flex items-center gap-2"
              aria-label="Export configuration"
            >
              <Download className="h-5 w-5" />
            </button>
            <button
              onClick={toggleLanguage}
              className="p-2 rounded-md hover:bg-muted transition-colors flex items-center gap-2"
              aria-label="Toggle language"
            >
              <Languages className="h-5 w-5" />
              <span className="text-sm font-medium">
                {i18n.language.startsWith('zh') ? 'EN' : '中文'}
              </span>
            </button>
            <button
              onClick={toggleTheme}
              className="p-2 rounded-md hover:bg-muted transition-colors"
              aria-label="Toggle theme"
            >
              {theme === 'dark' ? (
                <Sun className="h-5 w-5" />
              ) : (
                <Moon className="h-5 w-5" />
              )}
            </button>
          </div>
        </div>
      </header>

      <ExportDialog
        isOpen={showExportDialog}
        onClose={() => setShowExportDialog(false)}
        data={configData}
      />

      <ImportDialog
        isOpen={showImportDialog}
        onClose={() => setShowImportDialog(false)}
      />
    </>
  );
};

export default Navbar;