import React, { useEffect, useState } from 'react';
import { useStore } from '../store';
import EmptyState from './EmptyState';
import { Button } from './ui/Button';
import { Trash2, Copy, Loader2, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import FormRenderer from './form/FormRenderer';
import { Dialog } from './ui/Dialog';
import { Input } from './ui/Input';
import { useTranslation } from 'react-i18next';
import { cn } from '../lib/utils';

const ConfigEditor: React.FC = () => {
  const { t } = useTranslation();
  const {
    configClasses,
    selectedClass,
    selectedInstance,
    updateConfigValue,
    deleteInstance,
    addInstance,
    selectClass,
    selectInstance,
    loading,
    refreshClasses,
    refreshInstances,
  } = useStore();

  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [showUnsavedDialog, setShowUnsavedDialog] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<{
    className: string;
    instanceName: string | null;
  } | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isCloneDialogOpen, setIsCloneDialogOpen] = useState(false);
  const [cloneNewName, setCloneNewName] = useState('');
  const [cloneError, setCloneError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  if (!selectedClass) {
    return (
      <EmptyState
        title={t('common.welcome')}
        description={t('common.selectConfig')}
        icon="Settings"
      />
    );
  }

  const configDef = configClasses?.[selectedClass];
  
  if (!configDef) {
    return (
      <EmptyState
        title={t('common.loading')}
        description={t('common.loadingDesc')}
        icon="Loader"
        isSpinning
      />
    );
  }

  const instances = configDef.instances || [];
  const isSingleton = configDef.isSingleton;

  const getCurrentData = () => {
    if (isSingleton) {
      return configDef.data;
    }
    if (selectedInstance) {
      return instances.find((inst: any) => inst.name === selectedInstance);
    }
    return null;
  };
  
  const currentData = getCurrentData();

  if (!isSingleton && !selectedInstance) {
    return (
      <div className="bg-card rounded-lg border border-border shadow-sm animate-in">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <div>
            <h2 className="text-xl font-semibold">{configDef.title}</h2>
            <p className="text-sm text-muted-foreground">
              {t('common.instances', { count: instances.length })}
            </p>
          </div>
        </div>
        
        <div className="p-6">
          <EmptyState
            title=""
            description={t('common.selectInstanceDesc')}
            icon="List"
          />
        </div>
      </div>
    );
  }
  
  if (loading.instances && !currentData) {
    return (
      <div className="w-full h-full flex items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }
  
  if (!currentData) {
     return (
      <EmptyState
        title={t('common.notFound')}
        description={t('common.notFoundDesc', { config: selectedInstance })}
        icon="AlertTriangle"
      />
    );
  }

  const handleFormSubmit = async (values: any) => {
    if (!selectedClass) return;
    try {
      setIsSaving(true);
      await updateConfigValue(selectedClass, values, selectedInstance || undefined);
      toast.success(t('common.saveSuccess'));
      setHasUnsavedChanges(false);
    } catch (error) {
      console.error(error);
      toast.error(error instanceof Error ? error.message : t('common.saveFailed'));
      throw error;
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteClick = () => {
    setShowDeleteDialog(true);
  };

  const handleDeleteConfirm = async () => {
    if (!selectedClass || selectedInstance === null) return;
    
    try {
      setIsDeleting(true);
      await deleteInstance(selectedClass, selectedInstance);
      toast.success(t('common.deleteSuccess'));
      setShowDeleteDialog(false);
    } catch (error) {
      console.error(error);
      toast.error(t('common.deleteFailed'));
    } finally {
      setIsDeleting(false);
    }
  };

  const handleCloneClick = () => {
    if (!selectedClass || selectedInstance === null) return;
    setCloneNewName(`${selectedInstance} (copy)`);
    setCloneError(null);
    setIsCloneDialogOpen(true);
  };

  const handleRefreshClick = async () => {
    if (!selectedClass) return;
    
    try {
      setIsRefreshing(true);
      await refreshClasses();
      if (!configDef?.isSingleton) {
        await refreshInstances(selectedClass);
      }
      toast.success(t('common.refreshSuccess'));
    } catch (error) {
      console.error('Refresh failed:', error);
      toast.error(t('common.refreshFailed'));
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleConfirmClone = async () => {
    if (!selectedClass || !cloneNewName.trim()) {
      setCloneError(t('clone.nameRequired'));
      return;
    }

    const trimmedName = cloneNewName.trim();

    if (instances.some((inst: any) => inst.name === trimmedName)) {
      setCloneError(t('common.nameExists'));
      return;
    }
    
    try {
      const currentInstanceData = getCurrentData();
      if (!currentInstanceData) {
        throw new Error("Source instance data not found for cloning.");
      }
      const newInstanceData = { ...currentInstanceData, name: trimmedName };
      
      await addInstance(selectedClass, newInstanceData);
      
      toast.success(t('clone.success', { name: trimmedName }));
      
      setIsCloneDialogOpen(false);
      setCloneNewName('');
      setCloneError(null);
      
    } catch (error) {
      console.error(error);
      const errorMessage = error instanceof Error ? error.message : t('clone.failed');
      toast.error(errorMessage);
      setCloneError(errorMessage);
    }
  };

  const handleNavigationAttempt = (newClassName: string, newInstanceName: string | null) => {
    if (hasUnsavedChanges) {
      setPendingNavigation({ className: newClassName, instanceName: newInstanceName });
      setShowUnsavedDialog(true);
      return false;
    }
    return true;
  };

  const handleUnsavedDialogAction = async (action: 'save' | 'discard' | 'cancel') => {
    setShowUnsavedDialog(false);

    if (!pendingNavigation) return;

    if (action === 'save') {
      try {
        await handleFormSubmit(getCurrentData());
        navigateToNewSelection();
      } catch (error) {
        return;
      }
    } else if (action === 'discard') {
      setHasUnsavedChanges(false);
      navigateToNewSelection();
    }

    setPendingNavigation(null);
  };

  const navigateToNewSelection = () => {
    if (!pendingNavigation) return;
    selectClass(pendingNavigation.className);
    selectInstance(pendingNavigation.instanceName);
  };

  return (
    <>
      <div className="bg-card rounded-lg border border-border shadow-sm animate-in">
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <div>
            <h2 className="text-xl font-semibold">{configDef.title}</h2>
            {!configDef.isSingleton && selectedInstance && (
              <p className="text-sm text-muted-foreground">
                {selectedInstance || t('common.unnamedInstance')}
              </p>
            )}
          </div>
          
          <div className="flex items-center gap-2">
            {/* 刷新按钮 - 对所有配置都显示 */}
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefreshClick}
              disabled={isRefreshing}
              className="flex items-center gap-2 opacity-80 hover:opacity-100"
            >
              <RefreshCw className={cn("h-4 w-4", isRefreshing && "animate-spin")} />
              <span>{isRefreshing ? t('common.refreshing') : t('common.refresh')}</span>
            </Button>
            
            {/* 克隆和删除按钮 - 仅对非singleton且有选中实例的配置显示 */}
            {!configDef.isSingleton && selectedInstance && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleCloneClick}
                  className="flex items-center gap-2 opacity-80 hover:opacity-100"
                >
                  <Copy className="h-4 w-4" />
                  <span>{t('common.clone')}</span>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleDeleteClick}
                  disabled={isDeleting}
                  className="flex items-center gap-2 text-destructive hover:text-destructive-foreground hover:bg-destructive opacity-80 hover:opacity-100"
                >
                  <Trash2 className="h-4 w-4" />
                  <span>{isDeleting ? t('common.deleting') : t('common.delete')}</span>
                </Button>
              </>
            )}
          </div>
        </div>
        
        <div className="p-6">
          <FormRenderer
            key={`${selectedClass}-${selectedInstance}`}
            schema={configDef.schema}
            initialData={currentData}
            onSubmit={handleFormSubmit}
          />
        </div>
      </div>
      <Dialog
        isOpen={showUnsavedDialog}
        onClose={() => handleUnsavedDialogAction('cancel')}
        title={t('common.unsavedChanges')}
        description={t('common.unsavedChangesDesc')}
      >
        <div className="flex justify-end gap-3 mt-6">
          <Button
            variant="outline"
            onClick={() => handleUnsavedDialogAction('cancel')}
          >
            {t('common.cancel')}
          </Button>
          <Button
            variant="destructive"
            onClick={() => handleUnsavedDialogAction('discard')}
          >
            {t('common.discard')}
          </Button>
          <Button onClick={() => handleUnsavedDialogAction('save')}>
            {t('common.save')}
          </Button>
        </div>
      </Dialog>
      <Dialog
        isOpen={showDeleteDialog}
        onClose={() => setShowDeleteDialog(false)}
        title={t('common.deleteTitle')}
        description={t('common.deleteDesc')}
      >
        <div className="flex justify-end gap-3 mt-6">
          <Button
            variant="outline"
            onClick={() => setShowDeleteDialog(false)}
          >
            {t('common.cancel')}
          </Button>
          <Button
            variant="destructive"
            onClick={handleDeleteConfirm}
            disabled={isDeleting}
          >
            {isDeleting ? t('common.deleting') : t('common.delete')}
          </Button>
        </div>
      </Dialog>
      <Dialog
        isOpen={isCloneDialogOpen}
        onClose={() => setIsCloneDialogOpen(false)}
        title={t('clone.title')}
      >
        <div className="space-y-4 mt-4">
          <p className="text-sm text-muted-foreground">
            {t('clone.description')}
          </p>
          <Input
            value={cloneNewName}
            onChange={(e) => setCloneNewName(e.target.value)}
            placeholder={t('clone.newNamePlaceholder')}
            onKeyDown={(e) => e.key === 'Enter' && handleConfirmClone()}
          />
          {cloneError && <p className="text-sm text-destructive">{cloneError}</p>}
        </div>
        <div className="flex justify-end gap-3 mt-6">
          <Button variant="outline" onClick={() => setIsCloneDialogOpen(false)}>
            {t('common.cancel')}
          </Button>
          <Button onClick={handleConfirmClone}>
            {t('common.confirm')}
          </Button>
        </div>
      </Dialog>
    </>
  );
};

export default ConfigEditor;