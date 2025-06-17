import { create } from 'zustand';
// 关键数据路径设计：
// 1. 表单配置
// 2. 实例
//
//
//
interface ConfigStore {
  // Configuration state
  configClasses: Record<string, ConfigClass> | null;
  selectedClass: string | null;
  selectedInstance: string | null;
  expandedItems: string[];
  
  // Loading states
  loading: {
    classes: boolean;
    schema: boolean;
    instances: boolean;
    update: boolean;
  };
  
  // Actions
  initialize: () => Promise<void>;
  fetchSchema: (className: string, instanceName?: string | null) => Promise<void>;
  refreshClasses: () => Promise<void>;
  refreshInstances: (className: string) => Promise<void>;
  selectClass: (className: string) => Promise<void>;
  selectInstance: (instanceName: string | null) => Promise<void>;
  updateConfigValue: (className: string, value: any, instanceName?: string) => Promise<void>;
  addInstance: (className: string, instanceData: any) => Promise<void>;
  deleteInstance: (className: string, instanceName: string) => Promise<void>;
  cloneInstance: (className: string, instanceName: string) => Promise<void>;
  toggleExpanded: (className: string) => Promise<void>;
}

interface ConfigClass {
  title: string;
  schema: any;
  isSingleton: boolean;
  icon: string;
  instances?: any[];
  name: string;
  data?: any; // For singleton data
}

// Add error types
export class ValidationError extends Error {
  constructor(message: string, public field?: string) {
    super(message);
    this.name = 'ValidationError';
  }
}

export class ConfigError extends Error {
  constructor(message: string, public code: string) {
    super(message);
    this.name = 'ConfigError';
  }
}

// Add error codes
export const ErrorCodes = {
  VALIDATION: {
    REQUIRED: 'validation.required',
    MIN_LENGTH: 'validation.minLength',
    MAX_LENGTH: 'validation.maxLength',
    MIN: 'validation.min',
    MAX: 'validation.max',
    PATTERN: 'validation.pattern',
    URI: 'validation.uri',
    HOSTNAME: 'validation.hostname',
    ENUM: 'validation.enum',
    MULTIPLE_OF: 'validation.multipleOf',
    MIN_ITEMS: 'validation.minItems',
    MAX_ITEMS: 'validation.maxItems',
    UNIQUE_ITEMS: 'validation.uniqueItems',
    TYPE: 'validation.type'
  },
  CONFIG: {
    NOT_FOUND: 'config.notFound',
    UPDATE_FAILED: 'config.updateFailed',
    ADD_FAILED: 'config.addFailed',
    DELETE_FAILED: 'config.deleteFailed',
    CLONE_FAILED: 'config.cloneFailed'
  }
} as const;

// Add error messages
const errorMessages: Record<string, string> = {
  [ErrorCodes.VALIDATION.REQUIRED]: 'This field is required',
  [ErrorCodes.VALIDATION.MIN_LENGTH]: 'Must be at least {{min}} characters',
  [ErrorCodes.VALIDATION.MAX_LENGTH]: 'Must be at most {{max}} characters',
  [ErrorCodes.VALIDATION.MIN]: 'Must be at least {{min}}',
  [ErrorCodes.VALIDATION.MAX]: 'Must be at most {{max}}',
  [ErrorCodes.VALIDATION.PATTERN]: 'Invalid format',
  [ErrorCodes.VALIDATION.URI]: 'Must be a valid URL',
  [ErrorCodes.VALIDATION.HOSTNAME]: 'Must be a valid hostname',
  [ErrorCodes.VALIDATION.ENUM]: '请选择一个有效的选项',
  [ErrorCodes.VALIDATION.MULTIPLE_OF]: 'Must be a multiple of {{n}}',
  [ErrorCodes.VALIDATION.MIN_ITEMS]: 'Array must have at least {{min}} items',
  [ErrorCodes.VALIDATION.MAX_ITEMS]: 'Array must have at most {{max}} items',
  [ErrorCodes.VALIDATION.UNIQUE_ITEMS]: 'Array items must be unique',
  [ErrorCodes.VALIDATION.TYPE]: 'Expected {{type}}',
  [ErrorCodes.CONFIG.NOT_FOUND]: 'Configuration not found',
  [ErrorCodes.CONFIG.UPDATE_FAILED]: 'Failed to update configuration',
  [ErrorCodes.CONFIG.ADD_FAILED]: 'Failed to add instance',
  [ErrorCodes.CONFIG.DELETE_FAILED]: 'Failed to delete instance',
  [ErrorCodes.CONFIG.CLONE_FAILED]: 'Failed to clone instance'
};

// Add error helper functions
function createValidationError(code: string, field?: string, params?: Record<string, any>): ValidationError {
  let message = errorMessages[code];
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      message = message.replace(`{{${key}}}`, String(value));
    });
  }
  return new ValidationError(message, field);
}

function createConfigError(code: string): ConfigError {
  return new ConfigError(errorMessages[code], code);
}

export const useStore = create<ConfigStore>((set, get) => ({
  // State
  configClasses: null,
  selectedClass: null,
  selectedInstance: null,
  expandedItems: [],
  loading: {
    classes: false,
    schema: false,
    instances: false,
    update: false
  },
  
  // Actions
  initialize: async () => {
    let attempts = 0;
    const maxAttempts = 1800;
    const retryDelay = 3000; // 3 seconds

    const attempt = async () => {
      try {
        if (attempts === 0) {
          console.log("[Store] Starting configuration initialization...");
          set(state => ({ 
            loading: { ...state.loading, classes: true } 
          }));
        }
        
        console.log(`[Store] Attempt ${attempts + 1}: Getting API base URL...`);
        const baseUrl = await getApiBaseUrl();
        console.log(`[Store] API base URL: ${baseUrl}`);
        
        console.log(`[Store] Fetching configuration classes from: ${baseUrl}/config/class`);
        const response = await fetch(`${baseUrl}/config/class`);
        
        console.log(`[Store] Response status: ${response.status} ${response.statusText}`);
        
        if (!response.ok) {
          const errorText = await response.text().catch(() => 'Unable to read response');
          console.error(`[Store] HTTP Error: ${response.status} ${response.statusText}`);
          console.error(`[Store] Response body: ${errorText}`);
          throw new Error(`Failed to fetch configuration classes: ${response.status} ${response.statusText}`);
        }

        console.log("[Store] Parsing response JSON...");
        const classes = await response.json();
        console.log(`[Store] Received ${Array.isArray(classes) ? classes.length : 0} configuration classes:`, classes);
        
        const classesMap = classes.reduce((acc: Record<string, ConfigClass>, cls: ConfigClass) => {
          // Preserve existing instance data if classes are re-fetched
          const existingClass = get().configClasses?.[cls.name];
          if (existingClass) {
            cls.instances = existingClass.instances;
            cls.data = existingClass.data;
            cls.schema = existingClass.schema;
          }
          acc[cls.name] = cls;
          return acc;
        }, {});
        
        console.log("[Store] Configuration initialization completed successfully");
        set({ 
          configClasses: classesMap,
          loading: {
            classes: false,
            schema: false,
            instances: false,
            update: false
          }
        });
      } catch (error) {
        attempts++;
        console.error(`[Store] Attempt ${attempts} failed to initialize config:`, error);
        
        if (error instanceof TypeError && error.message.includes('fetch')) {
          console.error("[Store] Network error - server may not be ready yet");
        }
        
        if (attempts < maxAttempts) {
          console.log(`[Store] Retrying in ${retryDelay / 1000} seconds... (${attempts}/${maxAttempts})`);
          setTimeout(attempt, retryDelay);
        } else {
          console.error('[Store] Max retry attempts reached. Giving up.');
          set(state => ({ 
            loading: { ...state.loading, classes: false } 
          }));
          throw error;
        }
      }
    };
    await attempt();
  },
  
  fetchSchema: async (className, instanceName = null) => {
    set(state => ({ loading: { ...state.loading, schema: true } }));
    try {
      const baseUrl = await getApiBaseUrl();
      const instanceQuery = instanceName ? `?instance=${instanceName}` : '';
      const url = `${baseUrl}/config/class/${className}/schema${instanceQuery}`;
      
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Failed to fetch schema for ${className}`);
      }
      const schema = await response.json();

      set(state => ({
        configClasses: state.configClasses ? {
          ...state.configClasses,
          [className]: {
            ...state.configClasses[className],
            schema: schema
          }
        } : null
      }));
    } catch (error) {
      console.error('Failed to fetch schema:', error);
      // We don't re-throw here to avoid breaking the UI flow
    } finally {
      set(state => ({ loading: { ...state.loading, schema: false } }));
    }
  },
  
  refreshClasses: async () => {
    try {
      set(state => ({ 
        loading: { ...state.loading, classes: true } 
      }));
      
      const baseUrl = await getApiBaseUrl();
      const response = await fetch(`${baseUrl}/config/class`);
      
      if (!response.ok) {
        throw new Error('Failed to fetch configuration classes');
      }

      const classes = await response.json();
      console.log('Fetched classes:', classes);
      
      const classesMap = classes.reduce((acc: Record<string, ConfigClass>, cls: ConfigClass) => {
        // Preserve existing instance data if classes are re-fetched
        const existingClass = get().configClasses?.[cls.name];
        if (existingClass) {
          cls.instances = existingClass.instances;
          cls.data = existingClass.data;
          cls.schema = existingClass.schema;
        }
        acc[cls.name] = cls;
        return acc;
      }, {});
      
      set({ 
        configClasses: classesMap,
        loading: {
          ...get().loading,
          classes: false
        }
      });
    } catch (error) {
      console.error('Failed to refresh classes:', error);
      set(state => ({ 
        loading: { ...state.loading, classes: false } 
      }));
      throw error;
    }
  },
  
  refreshInstances: async (className: string) => {
    try {
      set(state => ({ 
        loading: { ...state.loading, instances: true } 
      }));
      
      const baseUrl = await getApiBaseUrl();
      const response = await fetch(`${baseUrl}/config/class/${className}/instances`);
      
      if (!response.ok) {
        throw new Error('Failed to fetch instances');
      }

      const instances = await response.json();
      
      set(state => ({
        configClasses: state.configClasses ? {
          ...state.configClasses,
          [className]: {
            ...state.configClasses[className],
            instances
          }
        } : null,
        loading: {
          ...state.loading,
          instances: false
        }
      }));
    } catch (error) {
      console.error('Failed to refresh instances:', error);
      set(state => ({ 
        loading: { ...state.loading, instances: false } 
      }));
      throw error;
    }
  },
  
  selectClass: async (className) => {
    const { configClasses } = get();
    if (!configClasses) return;

    const configClass = configClasses[className];
    if (!configClass) return;

    set(state => ({
      selectedClass: className,
      selectedInstance: null,
      loading: { ...state.loading, instances: true, schema: true }
    }));
    
    try {
      const baseUrl = await getApiBaseUrl();
      
      if (configClass.isSingleton) {
        // Fetch schema first
        await get().fetchSchema(className);

        // Fetch singleton data
        const response = await fetch(`${baseUrl}/config/class/${className}/singleton`);
        if (!response.ok) throw new Error('Failed to fetch singleton data');
        const data = await response.json();
        
        set(state => ({
          configClasses: state.configClasses ? {
            ...state.configClasses,
            [className]: {
              ...state.configClasses[className],
              data: data
            }
          } : null,
        }));
      } else {
        // Fetch instances list
        await get().refreshInstances(className);
      }
    } catch (error) {
      console.error(`Failed to select or fetch data for class ${className}:`, error);
    } finally {
      set(state => ({
        loading: { ...state.loading, instances: false, schema: false }
      }));
    }
  },
  
  selectInstance: async (instanceName) => {
    set(state => ({
      selectedInstance: instanceName
    }));

    if (instanceName) {
      const { selectedClass } = get();
      if (selectedClass) {
        await get().fetchSchema(selectedClass, instanceName);
      }
    }
  },
  
  updateConfigValue: async (className, value, instanceName) => {
    set(state => ({ 
      loading: { ...state.loading, update: true } 
    }));

    const { configClasses } = get();
    const configClass = configClasses?.[className];

    try {
      const baseUrl = await getApiBaseUrl();
      const url = instanceName
        ? `${baseUrl}/config/class/${className}/instances/${instanceName}`
        : `${baseUrl}/config/class/${className}`;

      const response = await fetch(url, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(value),
      });

      if (!response.ok) {
        throw createConfigError(ErrorCodes.CONFIG.UPDATE_FAILED);
      }
      
      // After successful update, refetch data
      // 1. Refetch schema
      await get().fetchSchema(className, instanceName);

      // 2. Refetch instance data (or list)
      if (configClass?.isSingleton) {
        const dataResponse = await fetch(`${baseUrl}/config/class/${className}/singleton`);
        if (!dataResponse.ok) throw new Error('Failed to refetch singleton data');
        const data = await dataResponse.json();
        
        set(state => ({
          configClasses: state.configClasses ? {
            ...state.configClasses,
            [className]: {
              ...state.configClasses![className],
              data: data
            }
          } : null,
        }));
      } else {
        await get().refreshInstances(className);
      }

      // 3. Finally refresh class list metadata
      await get().refreshClasses();

    } catch (error) {
      console.error('Failed to update config value:', error);
      throw error;
    } finally {
      set(state => ({ 
        loading: { ...state.loading, update: false } 
      }));
    }
  },
  
  addInstance: async (className, instanceData) => {
    try {
      const baseUrl = await getApiBaseUrl();
      const response = await fetch(`${baseUrl}/config/class/${className}/instances`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(instanceData),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => null);
        const detail = errorData?.detail || 'Failed to add instances';
        throw new Error(detail);
      }
      
      // Get instance name from response data
      const responseData = await response.json();
      const instanceName = responseData?.name || instanceData.name;
      
      set({
        selectedClass: className,
        selectedInstance: instanceName
      });
      
      // Refresh instances list to update UI
      await get().refreshInstances(className);
    } catch (error) {
      console.error('Failed to add instance:', error);
      set(state => ({ 
        loading: { ...state.loading, update: false } 
      }));
      throw error;
    }
  },
  
  deleteInstance: async (className, instanceName) => {
    try {
      const baseUrl = await getApiBaseUrl();
      const response = await fetch(`${baseUrl}/config/class/${className}/instances/${instanceName}`, {
        method: 'DELETE',
      });

      if (!response.ok) {
        throw new Error('Failed to delete instance');
      }
      
      set(state => ({
        selectedInstance: null,
        loading: {
          ...state.loading,
          update: false
        }
      }));
      
      await get().refreshInstances(className);
    } catch (error) {
      console.error('Failed to delete instance:', error);
      set(state => ({ 
        loading: { ...state.loading, update: false } 
      }));
      throw error;
    }
  },

  cloneInstance: async (className, instanceName) => {
    try {
      const baseUrl = await getApiBaseUrl();
      const response = await fetch(`${baseUrl}/config/class/${className}/instances/${instanceName}/clone`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ clone: true }),
      });

      if (!response.ok) {
        throw new Error('Failed to clone instance');
      }
      
      const location = response.headers.get('Location');
      if (!location) {
        throw new Error('No location header in response');
      }
      
      const newInstanceName = decodeURIComponent(location.split('/').pop() || '');
      
      set({
        selectedClass: className,
        selectedInstance: newInstanceName
      });
      
      await get().refreshInstances(className);
    } catch (error) {
      console.error('Failed to clone instance:', error);
      set(state => ({ 
        loading: { ...state.loading, update: false } 
      }));
      throw error;
    }
  },

  toggleExpanded: async (className) => {
    const { expandedItems } = get();
    if (expandedItems.includes(className)) {
      set(state => ({
        expandedItems: state.expandedItems.filter(item => item !== className)
      }));
    } else {
      await get().refreshInstances(className);
      set(state => ({
        expandedItems: [...state.expandedItems, className]
      }));
    }
  }
}));

// Helper function to get API base URL, aware of Electron/browser environments
async function getApiBaseUrl() {
  console.log("[Store] Getting API base URL...");
  
  // @ts-ignore
  if (window.api) {
    console.log("[Store] Running in Electron environment");
    try {
      // @ts-ignore
      const port = await window.api.getApiPort();
      console.log("[Store] Retrieved port from Electron:", port);
      if (port) {
        const url = `http://localhost:${port}`;
        console.log("[Store] Using Electron API URL:", url);
        return url;
      }
    } catch (error) {
      console.error('[Store] Failed to get API port from Electron, falling back to dev port.', error);
    }
  } else {
    console.log("[Store] Running in browser environment");
  }
  
  // Fallback for browser development or if Electron IPC fails
  const fallbackUrl = `http://localhost:38178`;
  console.log("[Store] Using fallback API URL:", fallbackUrl);
  return fallbackUrl;
}
