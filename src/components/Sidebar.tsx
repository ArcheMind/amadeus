import React, { useState, useEffect, useRef } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useStore } from '../store';
import { cn } from '../lib/utils';
import * as Icons from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Input } from './ui/Input';

interface SidebarProps {
  isOpen: boolean;
  onToggle: () => void;
}

type AddItemState = 'IDLE' | 'INPUT' | 'VALIDATING' | 'ERROR';

const Sidebar: React.FC<SidebarProps> = ({ isOpen, onToggle }) => {
  const { 
    configClasses,
    selectedClass,
    selectedInstance,
    expandedItems,
    selectClass,
    selectInstance,
    addInstance,
    toggleExpanded
  } = useStore();

  const { t } = useTranslation();
  const [addItemState, setAddItemState] = useState<AddItemState>('IDLE');
  const [newItemName, setNewItemName] = useState('');
  const [activePath, setActivePath] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (addItemState === 'INPUT' && inputRef.current) {
      inputRef.current.focus();
    }
  }, [addItemState]);

  // Auto-select first config class when loaded
  useEffect(() => {
    if (configClasses && !selectedClass && Object.keys(configClasses).length > 0) {
      const firstClassName = Object.keys(configClasses)[0];
      selectClass(firstClassName);
    }
  }, [configClasses, selectedClass, selectClass]);

  const handleSelectConfig = async (className: string, isSingleton: boolean) => {
    selectClass(className);
    if (!isSingleton) {
      await toggleExpanded(className);
    }
  };

  const handleAddInstance = async (className: string) => {
    try {
      setAddItemState('VALIDATING');
      await addInstance(className, { name: newItemName });
      await new Promise(resolve => setTimeout(resolve, 100));
      if (!expandedItems.includes(className)) {
        await toggleExpanded(className);
      }
      setAddItemState('IDLE');
      setNewItemName('');
      setActivePath(null);
    } catch (error) {
      console.error('Failed to add instance:', error);
      setAddItemState('ERROR');
    }
  };

  const handleInputKeyDown = async (e: React.KeyboardEvent<HTMLInputElement>, className: string) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const configClass = configClasses?.[className];
      if (!configClass) return;
      
      await handleAddInstance(className);
    } else if (e.key === 'Escape') {
      setAddItemState('IDLE');
      setNewItemName('');
      setActivePath(null);
    }
  };

  const handleInputBlur = () => {
    setAddItemState('IDLE');
    setNewItemName('');
    setActivePath(null);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setNewItemName(e.target.value);
    if (addItemState === 'ERROR') {
      setAddItemState('INPUT');
    }
  };

  const isInputOrErrorState = (state: AddItemState): state is 'INPUT' | 'ERROR' =>
    state === 'INPUT' || state === 'ERROR';

  const getIconComponent = (iconName: string) => {
    const IconComponent = (Icons as any)[iconName] || Icons.Settings;
    return <IconComponent className="h-5 w-5" />;
  };

  if (!configClasses) {
    return (
      <aside className={cn(
        "h-full bg-card/12 backdrop-blur-2xl border-r border-white/8 transition-all duration-300 flex-shrink-0 overflow-hidden shadow-xl shadow-primary/5",
        isOpen ? "w-64" : "w-0"
      )}>
        <div className="flex items-center justify-center h-full">
          <p className="text-muted-foreground">{t('common.loading')}</p>
        </div>
      </aside>
    );
  }

  return (
    <aside
      className={cn(
        "h-full bg-card border-r border-border transition-all duration-300 flex-shrink-0 overflow-hidden",
        isOpen ? "w-64" : "w-0"
      )}
    >
      <div className="h-full flex flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto scrollbar-thin py-4">
          {Object.entries(configClasses).length > 0 ? (
            <ul className="space-y-1 px-3">
              {Object.entries(configClasses).map(([className, config]) => (
                <li key={className}>
                  <div
                    className={cn(
                      "flex items-center justify-between px-3 py-2 rounded-md cursor-pointer transition-colors group",
                      selectedClass === className && !selectedInstance
                        ? "bg-primary/10 text-primary"
                        : "hover:bg-muted"
                    )}
                    onClick={() => handleSelectConfig(className, config.isSingleton)}
                  >
                    <div className="flex items-center gap-2">
                      {getIconComponent(config.icon)}
                      <span className="truncate">{config.title}</span>
                    </div>
                    {!config.isSingleton && (
                      <button 
                        className="p-1 rounded-sm hover:bg-muted-foreground/10"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleExpanded(className);
                        }}
                      >
                        {expandedItems.includes(className) ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </button>
                    )}
                  </div>
                  
                  {!config.isSingleton && expandedItems.includes(className) && (
                    <ul className="ml-8 mt-1 space-y-1">
                      {(config.instances || []).map((instanceData: any) => (
                        <li key={instanceData.name}>
                          <div
                            className={cn(
                              "flex items-center justify-between px-3 py-2 rounded-md cursor-pointer transition-colors group",
                              selectedClass === className && selectedInstance === instanceData.name
                                ? "bg-primary/10 text-primary"
                                : "hover:bg-muted"
                            )}
                          >
                            <span 
                              className="truncate flex-1"
                              onClick={() => {
                                selectClass(className);
                                selectInstance(instanceData.name);
                              }}
                            >
                              {instanceData.name}
                            </span>
                          </div>
                        </li>
                      ))}
                      <li>
                        {isInputOrErrorState(addItemState) && activePath === className ? (
                          <div className="px-3 py-2">
                            <Input
                              ref={inputRef}
                              value={newItemName}
                              onChange={handleInputChange}
                              onKeyDown={(e) => handleInputKeyDown(e, className)}
                              onBlur={handleInputBlur}
                              placeholder={t('common.pleaseSelect')}
                              className={cn(
                                "h-8",
                                addItemState === 'ERROR' && "border-destructive focus-visible:ring-destructive"
                              )}
                            />
                            {addItemState === 'ERROR' && activePath === className && (
                              <p className="text-xs text-destructive mt-1">
                                {t('common.nameExists')}
                              </p>
                            )}
                          </div>
                        ) : (
                          <div
                            className={cn(
                              "flex items-center px-3 py-2 rounded-md cursor-pointer transition-colors text-muted-foreground hover:bg-muted hover:text-foreground"
                            )}
                            onClick={() => {
                              setAddItemState('INPUT');
                              setActivePath(className);
                              setNewItemName('');
                            }}
                          >
                            <span className="truncate">{t('common.add')}</span>
                          </div>
                        )}
                      </li>
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <div className="px-6 py-8 text-center text-muted-foreground">
              <p>{t('common.noConfig')}</p>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;
