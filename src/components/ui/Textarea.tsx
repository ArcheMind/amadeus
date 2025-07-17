import React from 'react';
import TextareaAutosize, { TextareaAutosizeProps } from 'react-textarea-autosize';
import { cn } from '../../lib/utils';

export interface TextareaProps extends TextareaAutosizeProps {
  /**
   * 是否禁用自动调整高度
   */
  disableAutosize?: boolean;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, minRows = 3, maxRows = 10, disableAutosize = false, ...props }, ref) => {
    const baseClassName = cn(
      "flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 resize-none",
      className
    );

    if (disableAutosize) {
      return (
        <textarea
          className={baseClassName}
          ref={ref}
          {...props}
        />
      );
    }

    return (
      <TextareaAutosize
        className={baseClassName}
        minRows={minRows}
        maxRows={maxRows}
        ref={ref}
        {...props}
      />
    );
  }
);

Textarea.displayName = 'Textarea';