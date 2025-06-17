import React, { useState, useRef, useEffect, useLayoutEffect } from 'react';
import { useForm, useFieldArray } from 'react-hook-form';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Textarea } from '../ui/Textarea';
import { Checkbox } from '../ui/Checkbox';
import { Switch } from '../ui/Switch';
import { Select } from '../ui/Select';
import { Loader2, Plus, Trash2, ChevronUp, ChevronDown, Edit3, Save, X, ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Badge } from '../ui/Badge';
import { cn } from '../../lib/utils';
import { useStore } from '../../store';

// Store scroll position between re-renders caused by key changes.
let lastScrollPosition = 0;

// Add type declaration for window.electronAPI
declare global {
  interface Window {
    electronAPI?: {
      getApiPort: () => Promise<number>;
    };
  }
}

interface FormRendererProps {
  schema: any;
  initialData: any;
  onSubmit: (values: any) => void;
  onCancel?: () => void;
}

const FormRenderer: React.FC<FormRendererProps> = ({ 
  schema,
  initialData,
  onSubmit,
  onCancel,
}) => {
  const { t } = useTranslation();
  const { selectClass, selectInstance } = useStore();
  const formRef = useRef<HTMLFormElement>(null);

  const { register, handleSubmit: reactHookFormSubmit, setValue, watch, formState: { errors, isSubmitting }, reset, control } = useForm({
    defaultValues: initialData,
    mode: 'onSubmit',
    reValidateMode: 'onChange'
  });

  useLayoutEffect(() => {
    const scrollContainer = formRef.current?.closest('main');
    if (scrollContainer) {
      // Restore scroll position on mount
      scrollContainer.scrollTop = lastScrollPosition;
    }

    // Save scroll position on unmount
    return () => {
      if (scrollContainer) {
        lastScrollPosition = scrollContainer.scrollTop;
      }
    };
  }, []);

  // Reset form when initialData changes
  useEffect(() => {
    if (initialData) {
      reset(initialData);
      // 重置 markdown 编辑状态
      setEditingMarkdown({});
      setMarkdownBackups({});
    }
  }, [initialData, reset]);

  // 只记录当前焦点的字段路径
  const [focusedField, setFocusedField] = useState<string | null>(null);
  // 记录正在加载中的 switch
  const [loadingSwitch, setLoadingSwitch] = useState<string | null>(null);
  // 使用单个定时器
  const timerRef = useRef<NodeJS.Timeout>();
  // 记录正在编辑的 markdown 字段
  const [editingMarkdown, setEditingMarkdown] = useState<{[key: string]: boolean}>({});
  // 记录编辑前的值，用于取消编辑
  const [markdownBackups, setMarkdownBackups] = useState<{[key: string]: string}>({});

  // 当提交状态改变时，清除 loading switch
  useEffect(() => {
    if (!isSubmitting) {
      setLoadingSwitch(null);
    }
  }, [isSubmitting]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  // 简单的 Markdown 文本处理函数
  const renderSimpleMarkdown = (text: string) => {
    if (!text) return null;
    
    const lines = text.split('\n');
    const elements: React.ReactNode[] = [];
    
    lines.forEach((line, index) => {
      const trimmedLine = line.trim();
      
      if (trimmedLine.startsWith('# ')) {
        elements.push(
          <h3 key={index} className="text-sm font-medium mt-3 mb-1.5 text-foreground">
            {trimmedLine.slice(2)}
          </h3>
        );
      } else if (trimmedLine.startsWith('## ')) {
        elements.push(
          <h4 key={index} className="text-sm font-medium mt-2.5 mb-1 text-foreground">
            {trimmedLine.slice(3)}
          </h4>
        );
      } else if (trimmedLine.startsWith('### ')) {
        elements.push(
          <h5 key={index} className="text-xs font-medium mt-2 mb-1 text-foreground">
            {trimmedLine.slice(4)}
          </h5>
        );
      } else if (trimmedLine.startsWith('- ')) {
        elements.push(
          <div key={index} className="flex items-start gap-2 ml-3 my-0.5">
            <span className="text-muted-foreground mt-0.5 text-xs">•</span>
            <span className="text-sm">{parseInlineMarkdown(trimmedLine.slice(2))}</span>
          </div>
        );
      } else if (trimmedLine.startsWith('1. ') || /^\d+\. /.test(trimmedLine)) {
        const match = trimmedLine.match(/^(\d+)\. (.+)$/);
        if (match) {
          elements.push(
            <div key={index} className="flex items-start gap-2 ml-3 my-0.5">
              <span className="text-muted-foreground mt-0.5 text-xs">{match[1]}.</span>
              <span className="text-sm">{parseInlineMarkdown(match[2])}</span>
            </div>
          );
        }
      } else if (trimmedLine.startsWith('> ')) {
        elements.push(
          <div key={index} className="border-l-2 border-primary/40 pl-3 py-1 my-1.5 bg-muted/20 italic text-sm">
            {parseInlineMarkdown(trimmedLine.slice(2))}
          </div>
        );
      } else if (trimmedLine === '') {
        elements.push(<div key={index} className="my-1" />);
      } else {
        elements.push(
          <p key={index} className="my-0.5 text-sm leading-relaxed">
            {parseInlineMarkdown(line)}
          </p>
        );
      }
    });
    
    return <div className="space-y-0.5">{elements}</div>;
  };

  // 处理应用内导航
  const handleInternalNavigation = (url: string) => {
    // 解析路径，例如 "/characters" 或 "/characters/someInstance"
    const path = url.startsWith('/') ? url.slice(1) : url;
    const segments = path.split('/').filter(Boolean);
    
    if (segments.length > 0) {
      const className = segments[0];
      const instanceName = segments[1] || null;
      
      selectClass(className);
      if (instanceName) {
        selectInstance(instanceName);
      } else {
        selectInstance(null);
      }
    }
  };

  // 处理行内 Markdown 格式
  const parseInlineMarkdown = (text: string): React.ReactNode => {
    if (!text) return text;
    
    // 先处理链接，但不直接替换为HTML
    const linkRegex = /\[([^\]]+)\]\(([^)]+)\)/g;
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    let match;
    
    while ((match = linkRegex.exec(text)) !== null) {
      const [fullMatch, linkText, url] = match;
      const beforeText = text.slice(lastIndex, match.index);
      
      // 添加链接前的文本
      if (beforeText) {
        parts.push(parseInlineFormats(beforeText));
      }
      
      // 判断是内部链接还是外部链接
      const isInternal = url.startsWith('/') && !url.startsWith('//');
      const isExternal = /^https?:\/\//.test(url);
      
      if (isInternal) {
        // 内部链接
                 parts.push(
           <button
             key={match.index}
             onClick={() => handleInternalNavigation(url)}
             className="text-primary hover:underline cursor-pointer bg-transparent border-none p-0 font-inherit"
           >
             {parseInlineFormats(linkText)}
           </button>
         );
      } else if (isExternal) {
        // 外部链接
        parts.push(
          <a
            key={match.index}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary hover:underline"
          >
            {parseInlineFormats(linkText)}
          </a>
        );
      } else {
        // 其他链接（如 mailto:, tel: 等）
        parts.push(
          <a
            key={match.index}
            href={url}
            className="text-primary hover:underline"
          >
            {parseInlineFormats(linkText)}
          </a>
        );
      }
      
      lastIndex = match.index + fullMatch.length;
    }
    
    // 添加最后剩余的文本
    if (lastIndex < text.length) {
      parts.push(parseInlineFormats(text.slice(lastIndex)));
    }
    
    return parts.length === 1 ? parts[0] : <>{parts}</>;
  };

  // 处理其他行内格式（粗体、斜体、代码）
  const parseInlineFormats = (text: string): React.ReactNode => {
    if (!text) return text;
    
    // 处理粗体 **text**
    let result: React.ReactNode = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    
    // 处理斜体 *text*
    result = String(result).replace(/\*([^*]+)\*/g, '<em>$1</em>');
    
    // 处理行内代码 `code`
    result = String(result).replace(/`([^`]+)`/g, '<code class="bg-muted px-1 py-0.5 rounded text-xs font-mono">$1</code>');
    
    // 如果包含 HTML 标签，使用 dangerouslySetInnerHTML
    if (String(result).includes('<')) {
      return <span dangerouslySetInnerHTML={{ __html: String(result) }} />;
    }
    
    return result;
  };

  // 创建一个处理提交的函数
  const handleFormSubmit = reactHookFormSubmit(
    // 验证通过时的处理函数
    async (data: any) => {
      try {
        await onSubmit(data);
      } catch (error) {
        console.error('Form submission error:', error);
        // 提交出错时，延时2秒后重置表单
        setTimeout(() => {
          reset(initialData);
          // 如果有取消回调，也调用它
          onCancel?.();
        }, 2000);
      }
    },
    // 验证失败时的处理函数
    (errors) => {
      console.error('Form validation errors:', errors);
      // 验证失败时，延时2秒后重置表单
      setTimeout(() => {
        reset(initialData);
        // 如果有取消回调，也调用它
        onCancel?.();
      }, 2000);
    }
  );

  const getExampleDisplay = (example: any) => {
    if (typeof example === 'object' && example.title && example.const !== undefined) {
      return {
        label: example.title,
        value: example.const
      };
    }
    return {
      label: String(example),
      value: example
    };
  };

  const handleFieldChange = (fieldPath: string, value: any) => {
    setValue(fieldPath, value, { 
      shouldDirty: true,
      shouldValidate: true,
      shouldTouch: true
    });

    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }

    timerRef.current = setTimeout(() => {
      if (focusedField !== fieldPath) {
        const formValues = watch();
        handleFormSubmit({ ...formValues, [fieldPath]: value });
      }
    }, 500);
  };

  const handleFieldFocus = (fieldPath: string) => {
    setFocusedField(fieldPath);
  };

  const handleFieldBlur = (fieldPath: string) => {
    setFocusedField(null);
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      delete timerRef.current;
      const formValues = watch();
      handleFormSubmit({ ...formValues, [fieldPath]: watch(fieldPath) });
    }
  };

  if (!schema) {
    return (
      <div className="flex items-center justify-center p-8">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  const isFieldRequired = (fieldName: string) => {
    return schema.required?.includes(fieldName) ?? false;
  };

  const getValidationRules = (field: any, key: string) => {
    const rules: any = {};

    if (isFieldRequired(key)) {
      rules.required = t('validation.required');
    }

    if (field.type === 'string') {
      if (field.minLength !== undefined) {
        rules.minLength = {
          value: field.minLength,
          message: t('validation.minLength', { min: field.minLength })
        };
      }

      if (field.maxLength !== undefined) {
        rules.maxLength = {
          value: field.maxLength,
          message: t('validation.maxLength', { max: field.maxLength })
        };
      }

      if (field.format === 'uri') {
        rules.pattern = {
          value: /^https?:\/\/.+/,
          message: t('validation.uri')
        };
      }

      if (field.format === 'hostname') {
        rules.pattern = {
          value: /^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*$/,
          message: t('validation.hostname')
        };
      }

      if (field.pattern) {
        rules.pattern = {
          value: new RegExp(field.pattern),
          message: field.patternError || t('validation.pattern')
        };
      }
    }

    if (field.type === 'integer' || field.type === 'number') {
      rules.valueAsNumber = true;

      if (field.minimum !== undefined) {
        rules.min = {
          value: field.minimum,
          message: t('validation.min', { min: field.minimum })
        };
      }

      if (field.maximum !== undefined) {
        rules.max = {
          value: field.maximum,
          message: t('validation.max', { max: field.maximum })
        };
      }

      if (field.multipleOf !== undefined) {
        rules.validate = {
          multipleOf: (value: number) => 
            value % field.multipleOf === 0 || 
            t('validation.multipleOf', { n: field.multipleOf })
        };
      }
    }

    if (field.type === 'array') {
      const validates: any = {};

      if (field.minItems !== undefined) {
        validates.minItems = (value: any[]) => 
          (value && value.length >= field.minItems) || 
          t('validation.minItems', { min: field.minItems });
      }

      if (field.maxItems !== undefined) {
        validates.maxItems = (value: any[]) => 
          (!value || value.length <= field.maxItems) || 
          t('validation.maxItems', { max: field.maxItems });
      }

      if (field.uniqueItems === true) {
        validates.uniqueItems = (value: any[]) => {
          if (!value || value.length <= 1) return true;
          const uniqueValues = new Set(
            value.map(item => typeof item === 'object' ? JSON.stringify(item) : item)
          );
          return uniqueValues.size === value.length || t('validation.uniqueItems');
        };
      }

      if (Object.keys(validates).length > 0) {
        rules.validate = validates;
      }
    }

    if (field.enum) {
      rules.validate = {
        ...rules.validate,
        enum: (value: any) => 
          !value || field.enum.includes(value) || 
          t('validation.enum')
      };
    }

    return rules;
  };

  const renderField = (key: string, field: any, path = '') => {
    const fieldPath = path ? `${path}.${key}` : key;
    const value = watch(fieldPath);
    const error = errors[fieldPath];

    // Helper function to get default value for array items
    const getDefaultArrayItemValue = (itemSchema: any) => {
      if (itemSchema.type === 'string') return itemSchema.default || '';
      if (itemSchema.type === 'number' || itemSchema.type === 'integer') return itemSchema.default || 0;
      if (itemSchema.type === 'boolean') return itemSchema.default || false;
      if (itemSchema.type === 'object') {
        const defaultObj: any = {};
        if (itemSchema.properties) {
          Object.keys(itemSchema.properties).forEach(prop => {
            defaultObj[prop] = getDefaultArrayItemValue(itemSchema.properties[prop]);
          });
        }
        return defaultObj;
      }
      return '';
    };

    // Helper function to get options from oneOf
    const getOptionsFromOneOf = (schema: any) => {
      if (!schema.oneOf) return null;
      return schema.oneOf.map((option: any) => ({
        value: option.const,
        label: option.title || option.const
      }));
    };

    // Special handling for name field
    if (key === 'name') {
      return (
        <Input
          type="text"
          id={fieldPath}
          {...register(fieldPath, getValidationRules(field, key))}
          value={value || field.default || ''}
          placeholder={field.placeholder || t('common.pleaseSelect')}
          className={cn(
            error ? 'border-error' : '',
            field.readOnly ? 'bg-muted/50 cursor-default' : ''
          )}
          onChange={(e) => handleFieldChange(fieldPath, e.target.value)}
          onFocus={() => handleFieldFocus(fieldPath)}
          onBlur={() => handleFieldBlur(fieldPath)}
          readOnly={field.readOnly}
        />
      );
    }

    switch (field.type) {
      case 'array':
        return <ArrayField 
          key={fieldPath}
          fieldPath={fieldPath} 
          field={field}
          control={control}
          register={register}
          setValue={setValue}
          watch={watch}
          errors={errors}
          getDefaultArrayItemValue={getDefaultArrayItemValue}
          renderField={renderField}
          isFieldRequired={isFieldRequired}
          t={t}
          handleFormSubmit={handleFormSubmit}
          readOnly={field.readOnly}
          parseInlineMarkdown={parseInlineMarkdown}
        />;

      case 'string':
        if (field.format === 'textarea') {
          return (
            <Textarea
              id={fieldPath}
              {...register(fieldPath, getValidationRules(field, key))}
              value={value || field.default || ''}
              placeholder={field.placeholder || ''}
              className={error ? 'border-error' : ''}
              onChange={(e) => handleFieldChange(fieldPath, e.target.value)}
              onFocus={() => handleFieldFocus(fieldPath)}
              onBlur={() => handleFieldBlur(fieldPath)}
              readOnly={field.readOnly}
            />
          );
        }

        if (field.format === 'markdown') {
          const isEditing = editingMarkdown[fieldPath] || false;
          const currentValue = value || field.default || '';

          const handleStartEdit = () => {
            // 保存当前值作为备份
            setMarkdownBackups(prev => ({ ...prev, [fieldPath]: currentValue }));
            setEditingMarkdown(prev => ({ ...prev, [fieldPath]: true }));
          };

          const handleSaveEdit = () => {
            setEditingMarkdown(prev => ({ ...prev, [fieldPath]: false }));
            // 清除备份
            setMarkdownBackups(prev => {
              const newBackups = { ...prev };
              delete newBackups[fieldPath];
              return newBackups;
            });
            const formValues = watch();
            handleFormSubmit(formValues);
          };

          const handleCancelEdit = () => {
            // 恢复到备份的值
            const backupValue = markdownBackups[fieldPath] || '';
            setValue(fieldPath, backupValue);
            setEditingMarkdown(prev => ({ ...prev, [fieldPath]: false }));
            // 清除备份
            setMarkdownBackups(prev => {
              const newBackups = { ...prev };
              delete newBackups[fieldPath];
              return newBackups;
            });
          };

          if (field.readOnly || !isEditing) {
            return (
              <div className="relative group">
                <div className={cn(
                  "min-h-[60px] p-3 rounded-md border text-sm mb-4",
                  field.readOnly 
                    ? "bg-muted/20 border-muted/40 text-muted-foreground" 
                    : "bg-muted/10 border-muted/30 hover:bg-muted/20 hover:border-muted/50 transition-colors"
                )}>
                  {currentValue ? renderSimpleMarkdown(currentValue) : (
                    <p className="text-muted-foreground italic text-sm">
                      {field.placeholder || '暂无内容'}
                    </p>
                  )}
                </div>
                {!field.readOnly && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleStartEdit}
                    className="absolute top-2 right-2 h-8 w-8 p-0 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <Edit3 className="h-4 w-4" />
                  </Button>
                )}
              </div>
            );
          }

          return (
            <div className="space-y-2">
              <div className="relative">
                <Textarea
                  id={fieldPath}
                  {...register(fieldPath, getValidationRules(field, key))}
                  value={value || field.default || ''}
                  placeholder={field.placeholder || '请输入 Markdown 格式的内容...'}
                  className={cn(
                    error ? 'border-error' : '',
                    'font-mono text-sm pr-20'
                  )}
                  onChange={(e) => handleFieldChange(fieldPath, e.target.value)}
                  onFocus={() => handleFieldFocus(fieldPath)}
                  onBlur={() => handleFieldBlur(fieldPath)}
                  rows={12}
                />
                <div className="absolute top-2 right-2 flex gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleSaveEdit}
                    className="h-8 w-8 p-0 bg-green-50 hover:bg-green-100"
                  >
                    <Save className="h-4 w-4 text-green-600" />
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleCancelEdit}
                    className="h-8 w-8 p-0 bg-red-50 hover:bg-red-100"
                  >
                    <X className="h-4 w-4 text-red-600" />
                  </Button>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                支持 Markdown 格式：**粗体**、*斜体*、# 标题、- 列表、{'> 引用'}、[链接](URL) 等
              </p>
            </div>
          );
        }
        
        const options = getOptionsFromOneOf(field);
        if (options) {
          const val = value ?? field.default;
          return (
            <Select 
              id={fieldPath}
              {...register(fieldPath, getValidationRules(field, key))}
              value={options.some((o: { value: any; }) => o.value === val) ? String(val ?? '') : ''}
              onChange={(e) => handleFieldChange(fieldPath, e.target.value)}
              className={cn(
                error ? 'border-error' : '',
                field.readOnly ? 'bg-muted/50 cursor-default' : ''
              )}
              disabled={field.readOnly}
            >
              <option value="" disabled>{field.placeholder || t('common.pleaseSelect')}</option>
              {options.map((option: { value: string; label: string }) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          );
        }

        if (field.enum) {
          const val = value ?? field.default;
          return (
            <Select 
              id={fieldPath}
              {...register(fieldPath, getValidationRules(field, key))}
              value={field.enum.includes(val) ? String(val ?? '') : ''}
              onChange={(e) => handleFieldChange(fieldPath, e.target.value)}
              className={cn(
                error ? 'border-error' : '',
                field.readOnly ? 'bg-muted/50 cursor-default' : ''
              )}
              disabled={field.readOnly}
            >
              <option value="" disabled>{field.placeholder || t('common.pleaseSelect')}</option>
              {field.enum.map((value: string, index: number) => (
                <option key={value} value={value}>
                  {field.enumNames?.[index] || value}
                </option>
              ))}
            </Select>
          );
        }

        if (field.format === 'password') {
          return (
            <Input
              type="password"
              id={fieldPath}
              {...register(fieldPath, getValidationRules(field, key))}
              value={value || field.default || ''}
              placeholder={field.placeholder || ''}
              className={error ? 'border-error' : ''}
              onChange={(e) => handleFieldChange(fieldPath, e.target.value)}
              onFocus={() => handleFieldFocus(fieldPath)}
              onBlur={() => handleFieldBlur(fieldPath)}
              autoComplete="new-password"
              readOnly={field.readOnly}
            />
          );
        }

        const suggestions = field.suggestions || [];
        const isUriField = field.format === 'uri';
        const currentValue = value || field.default || '';
        const isValidUrl = isUriField && currentValue && /^https?:\/\/.+/.test(currentValue);

        return (
          <div>
            <div className="relative">
            <Input
              type="text"
              id={fieldPath}
              {...register(fieldPath, getValidationRules(field, key))}
                value={currentValue}
              placeholder={field.placeholder || ''}
                className={cn(
                  error ? 'border-error' : '',
                  field.readOnly ? 'bg-muted/50 cursor-default' : '',
                  isUriField && isValidUrl ? 'pr-10' : ''
                )}
              onChange={(e) => handleFieldChange(fieldPath, e.target.value)}
              onFocus={() => handleFieldFocus(fieldPath)}
              onBlur={() => handleFieldBlur(fieldPath)}
                readOnly={field.readOnly}
            />
              {isUriField && isValidUrl && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => window.open(currentValue, '_blank', 'noopener,noreferrer')}
                  className="absolute right-1 top-1/2 -translate-y-1/2 h-8 w-8 p-0 text-muted-foreground hover:text-foreground"
                  title="访问链接"
                >
                  <ExternalLink className="h-4 w-4" />
                </Button>
              )}
            </div>
            {suggestions.length > 0 && !field.readOnly && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {suggestions.map((item: any, index: number) => {
                  const { label, value: suggestionValue } = getExampleDisplay(item);
                  const isSelected = value === suggestionValue;
                  return (
                    <Badge
                      key={index}
                      variant="outline"
                      className={cn(
                        "cursor-pointer transition-all",
                        isSelected 
                          ? "bg-primary/10 text-primary border-primary/40 hover:bg-primary/10 hover:border-primary/60" 
                          : "hover:bg-muted"
                      )}
                      onClick={() => handleFieldChange(fieldPath, suggestionValue)}
                    >
                      {label}
                    </Badge>
                  );
                })}
              </div>
            )}
          </div>
        );
      
      case 'integer':
      case 'number':
        return (
          <Input
            type="number"
            id={fieldPath}
            {...register(fieldPath, getValidationRules(field, key))}
            value={value || field.default || ''}
            placeholder={field.placeholder || ''}
            min={field.minimum}
            max={field.maximum}
            step={field.multipleOf || (field.type === 'integer' ? 1 : 'any')}
            className={cn(
              error ? 'border-error' : '',
              field.readOnly ? 'bg-muted/50 cursor-default' : ''
            )}
            onChange={(e) => handleFieldChange(fieldPath, field.type === 'integer' ? parseInt(e.target.value) : parseFloat(e.target.value))}
            onFocus={() => handleFieldFocus(fieldPath)}
            onBlur={() => handleFieldBlur(fieldPath)}
            readOnly={field.readOnly}
          />
        );
      
      case 'boolean':
        const isLoading = loadingSwitch === fieldPath;
        return (
          <Switch
            id={fieldPath}
            {...register(fieldPath, getValidationRules(field, key))}
            checked={value ?? field.default ?? false}
            onChange={(e) => {
              const newValue = e.target.checked;
              setValue(fieldPath, newValue, {
                shouldDirty: true,
                shouldValidate: true,
                shouldTouch: true
              });
              
              setLoadingSwitch(fieldPath);

              // 立即提交 switch 的变化
              const formValues = watch();
              handleFormSubmit({ ...formValues, [fieldPath]: newValue });
            }}
            disabled={field.readOnly}
            isLoading={isLoading}
          />
        );
      
      case 'object':
        return (
          <div className="space-y-4 border border-border rounded-md p-4">
            {field.properties && Object.entries(field.properties).map(([subKey, subField]: [string, any]) => (
              <div key={subKey} className="space-y-2">
                <label 
                  htmlFor={`${fieldPath}.${subKey}`} 
                  className="block text-sm font-medium cursor-default"
                  onClick={(e) => e.preventDefault()}
                >
                  {subField.title || subKey}
                  {field.required?.includes(subKey) && <span className="text-error ml-1">*</span>}
                </label>
                {subField.description && (
                  <div className="text-xs text-muted-foreground mb-2">
                    {parseInlineMarkdown(subField.description)}
                  </div>
                )}
                {renderField(subKey, subField, fieldPath)}
                {errors[`${fieldPath}.${subKey}`] && (
                  <p className="text-sm text-error">{errors[`${fieldPath}.${subKey}`]?.message?.toString()}</p>
                )}
              </div>
            ))}
          </div>
        );
      
      default:
        return (
          <Input
            type="text"
            id={fieldPath}
            {...register(fieldPath, getValidationRules(field, key))}
            value={value || field.default || ''}
            className={cn(
              error ? 'border-error' : '',
              field.readOnly ? 'bg-muted/50 cursor-default' : ''
            )}
            readOnly={field.readOnly}
          />
        );
    }
  };

  return (
    <form ref={formRef} onSubmit={handleFormSubmit} className="space-y-6">
      {schema && schema.properties && Object.entries(schema.properties).map(([key, field]: [string, any]) => (
        <div key={key} className="space-y-2">
          <label 
            htmlFor={key} 
            className="block text-sm font-medium cursor-default"
            onClick={(e) => e.preventDefault()}
          >
            {field.title || key}
            {isFieldRequired(key) && <span className="text-error ml-1">*</span>}
          </label>
          {field.description && (
            <div className="text-xs text-muted-foreground mb-2">
              {parseInlineMarkdown(field.description)}
            </div>
          )}
          <div className="relative">
            {renderField(key, field)}
            {errors[key] && (
              <p className="text-sm text-error mt-1">{errors[key]?.message?.toString()}</p>
            )}
          </div>
        </div>
      ))}
    </form>
  );
};

// ArrayField component for handling array type fields
interface ArrayFieldProps {
  fieldPath: string;
  field: any;
  control: any;
  register: any;
  setValue: any;
  watch: any;
  errors: any;
  getDefaultArrayItemValue: (itemSchema: any) => any;
  renderField: (key: string, field: any, path?: string) => React.ReactNode;
  isFieldRequired: (fieldName: string) => boolean;
  t: (key: string, options?: any) => string;
  handleFormSubmit: (data: any) => void;
  readOnly?: boolean;
  parseInlineMarkdown: (text: string) => React.ReactNode;
}

const ArrayField: React.FC<ArrayFieldProps> = ({
  fieldPath,
  field,
  control,
  register,
  setValue,
  watch,
  errors,
  getDefaultArrayItemValue,
  renderField,
  isFieldRequired,
  t,
  handleFormSubmit,
  readOnly = false,
  parseInlineMarkdown
}) => {
  const { fields, append, remove, move } = useFieldArray({
    control,
    name: fieldPath,
  });

  const watchedArray = watch(fieldPath);
  const arrayError = errors[fieldPath];
  const [newItemValue, setNewItemValue] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const timerRef = useRef<NodeJS.Timeout>();

  // Helper function to get options from oneOf or enum
  const getOptionsFromOneOf = (schema: any) => {
    if (schema.oneOf) {
      return schema.oneOf.map((option: any) => ({
        value: option.const,
        label: option.title || option.const
      }));
    }
    if (schema.enum) {
      return schema.enum.map((value: string, index: number) => ({
        value: value,
        label: schema.enumNames?.[index] || value
      }));
    }
    return null;
  };

  const handleArrayChange = () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
    }

    timerRef.current = setTimeout(() => {
      if (!isFocused) {
        const currentValue = watch(fieldPath);
        handleFormSubmit({ [fieldPath]: currentValue });
      }
    }, 500);
  };

  const handleRemove = (index: number) => {
    remove(index);
    const currentValue = watch(fieldPath);
    handleFormSubmit({ [fieldPath]: currentValue });
  };

  const handleMove = (from: number, to: number) => {
    move(from, to);
    handleArrayChange();
  };

  const handleFocus = () => {
    setIsFocused(true);
  };

  const handleBlur = () => {
    setIsFocused(false);
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      delete timerRef.current;
      const currentValue = watch(fieldPath);
      handleFormSubmit({ [fieldPath]: currentValue });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addItem();
    }
  };

  const addItem = () => {
    if (!newItemValue.trim()) return;
    
    const defaultValue = field.items.type === 'string' 
      ? newItemValue.trim()
      : getDefaultArrayItemValue(field.items);
    
    if (field.uniqueItems) {
      const isDuplicate = watchedArray.some((item: any) => 
        JSON.stringify(item) === JSON.stringify(defaultValue)
      );
      if (isDuplicate) {
        return;
      }
    }
    
    append(defaultValue);
    setNewItemValue('');
    handleArrayChange();
  };

  // Helper function to get example display value
  const getExampleDisplay = (example: any) => {
    if (typeof example === 'object' && example.title && example.const) {
      return {
        label: example.title,
        value: example.const
      };
    }
    return {
      label: example,
      value: example
    };
  };

  const canAddMore = !field.maxItems || fields.length < field.maxItems;
  const canRemove = !field.minItems || fields.length > field.minItems;

  // For primitive types (string, number, boolean)
  if (field.items.type !== 'object') {
    const options = getOptionsFromOneOf(field.items);
    const suggestions = field.suggestions || [];
    
    return (
      <div className="space-y-2" onFocus={handleFocus} onBlur={handleBlur}>
        <div className="flex flex-wrap gap-2 p-2 border border-input rounded-md bg-background min-h-[42px]">
          {fields.map((item, index) => {
            const value = watchedArray[index];
            const option = options?.find((opt: any) => opt.value === value);
            return (
              <Badge
                key={item.id}
                variant="outline"
                onRemove={canRemove && !readOnly ? () => {
                  handleRemove(index);
                } : undefined}
                className="flex items-center gap-1 bg-primary/10 text-primary border-primary/40 hover:bg-primary/10 hover:border-primary/60 transition-all"
              >
                {option?.label || value}
              </Badge>
            );
          })}
          {canAddMore && !options && !readOnly && (
            <input
              type="text"
              value={newItemValue}
              onChange={(e) => setNewItemValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={field.addPlaceholder}
              className="flex-1 min-w-[120px] bg-transparent outline-none text-sm"
            />
          )}
        </div>

        {/* 显示enum选项或suggestions */}
        {(options || (suggestions.length > 0)) && !readOnly && (
          <div className="flex flex-wrap gap-1.5">
            {[...(options || suggestions)]
              .sort((a: any, b: any) => {
                const aValue = options ? a.value : getExampleDisplay(a).value;
                const bValue = options ? b.value : getExampleDisplay(b).value;
                const aIsSelected = watchedArray?.includes(aValue) ?? false;
                const bIsSelected = watchedArray?.includes(bValue) ?? false;
                return +bIsSelected - +aIsSelected;
              })
              .map((item: any) => {
                const { label, value } = options ? item : getExampleDisplay(item);
                const isSelected = watchedArray?.includes(value) ?? false;
                return (
                  <Badge
                    key={value}
                    variant="outline"
                    className={cn(
                      "cursor-pointer transition-all",
                      isSelected 
                        ? "bg-primary/10 text-primary border-primary/40 hover:bg-primary/10 hover:border-primary/60" 
                        : "hover:bg-muted"
                    )}
                    onClick={() => {
                      if (isSelected) {
                        const index = watchedArray?.indexOf(value) ?? -1;
                        if (index > -1) {
                          handleRemove(index);
                        }
                      } else if (canAddMore) {
                        append(value);
                        const currentValue = watch(fieldPath);
                        handleFormSubmit({ [fieldPath]: currentValue });
                      }
                    }}
                  >
                    {label}
                  </Badge>
                );
              })}
          </div>
        )}
        
        {/* Array info */}
        {(field.minItems !== undefined || field.maxItems !== undefined || (!options && canAddMore && !readOnly)) && (
          <div className="flex justify-between items-center text-xs text-muted-foreground">
            <div>
              {field.minItems !== undefined && `Min: ${field.minItems}`}
              {field.maxItems !== undefined && ` Max: ${field.maxItems}`}
            </div>
            {canAddMore && !options && !readOnly && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={addItem}
                disabled={!newItemValue.trim()}
                className="h-6 px-2"
              >
                <Plus className="h-3 w-3 mr-1" />
                {field.addText || t('array.add')}
              </Button>
            )}
          </div>
        )}

        {/* Array validation errors */}
        {arrayError && (
          <p className="text-sm text-error">{arrayError?.message?.toString()}</p>
        )}
      </div>
    );
  }

  // For object types, keep the existing card-based layout
  return (
    <div className="space-y-3" onFocus={handleFocus} onBlur={handleBlur}>
      {/* Array items */}
      {fields.length > 0 && (
        <div className="space-y-3">
          {fields.map((_, index) => (
            <div key={fields[index].id} className="space-y-3 p-4 border border-border rounded-md bg-background">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium">
                  {field.items.title || `${t('common.item')} ${index + 1}`}
                </h4>
                {!readOnly && (
                <div className="flex items-center gap-2">
                  <div className="flex flex-col gap-1">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => handleMove(index, index - 1)}
                      disabled={index === 0}
                      className="p-1 h-8 w-8"
                    >
                      <ChevronUp className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => handleMove(index, index + 1)}
                      disabled={index === fields.length - 1}
                      className="p-1 h-8 w-8"
                    >
                      <ChevronDown className="h-4 w-4" />
                    </Button>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => handleRemove(index)}
                    disabled={!canRemove}
                    className="p-1 h-8 w-8 text-error hover:text-error"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                )}
              </div>
              
              <div className="space-y-3">
                {field.items.properties && Object.entries(field.items.properties).map(([subKey, subField]: [string, any]) => {
                  const subFieldPath = `${fieldPath}.${index}.${subKey}`;
                  const subError = errors[subFieldPath];
                  
                  return (
                    <div key={subKey} className="space-y-2">
                      <label htmlFor={subFieldPath} className="block text-sm font-medium">
                        {subField.title || subKey}
                        {field.items.required?.includes(subKey) && <span className="text-error ml-1">*</span>}
                      </label>
                      {subField.description && (
                        <div className="text-xs text-muted-foreground mb-2">
                          {parseInlineMarkdown(subField.description)}
                        </div>
                      )}
                      {renderField(subKey, subField, `${fieldPath}.${index}`)}
                      {subError && (
                        <p className="text-sm text-error">{subError?.message?.toString()}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty state */}
      {fields.length === 0 && (
        <div className="text-center py-8 border-2 border-dashed border-border rounded-md">
          <p className="text-muted-foreground text-sm">
            {field.emptyText || t('array.empty', { name: field.title || fieldPath })}
          </p>
        </div>
      )}

      {/* Add button */}
      {!readOnly && (
      <div className="flex justify-between items-center">
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            append(getDefaultArrayItemValue(field.items));
            handleArrayChange();
          }}
          disabled={!canAddMore}
          className="flex items-center gap-2"
        >
          <Plus className="h-4 w-4" />
          {field.addText || t('array.add', { name: field.items.title || t('common.item') })}
        </Button>
        
        {/* Array info */}
        <div className="text-xs text-muted-foreground">
          {fields.length} {t('array.itemCount', { count: fields.length })}
          {field.minItems !== undefined && ` (${t('array.min')}: ${field.minItems})`}
          {field.maxItems !== undefined && ` (${t('array.max')}: ${field.maxItems})`}
        </div>
      </div>
      )}
      
      {/* Read-only array info */}
      {readOnly && (
        <div className="text-xs text-muted-foreground text-center">
          {fields.length} {t('array.itemCount', { count: fields.length })}
        </div>
      )}

      {/* Array validation errors */}
      {arrayError && (
        <p className="text-sm text-error">{arrayError?.message?.toString()}</p>
      )}
    </div>
  );
};

export default FormRenderer;
