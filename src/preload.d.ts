export {};

declare global {
  interface Window {
    api: {
      getApiPort: () => Promise<number>;
      getHistoricalLogs: () => Promise<string>;
      receive: (channel: string, func: (...args: any[]) => void) => () => void;
    };
  }
} 