import React, { useState, useEffect, useRef } from 'react';
import { cn } from '../../lib/utils';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  autoResize?: boolean;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, autoResize = false, value, onChange, ...props }, ref) => {
    const [width, setWidth] = useState<number | undefined>();
    const measureRef = useRef<HTMLSpanElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    // Combine refs
    React.useImperativeHandle(ref, () => inputRef.current!);

    useEffect(() => {
      if (autoResize && measureRef.current && inputRef.current) {
        const measureElement = measureRef.current;
        const inputElement = inputRef.current;
        
        // Copy styles from input to measure element
        const computedStyle = window.getComputedStyle(inputElement);
        measureElement.style.font = computedStyle.font;
        measureElement.style.fontSize = computedStyle.fontSize;
        measureElement.style.fontFamily = computedStyle.fontFamily;
        measureElement.style.fontWeight = computedStyle.fontWeight;
        measureElement.style.letterSpacing = computedStyle.letterSpacing;
        measureElement.style.textTransform = computedStyle.textTransform;
        
        // Set text content
        measureElement.textContent = (value as string) || props.placeholder || '';
        
        // Calculate width (add some padding for cursor and border)
        const textWidth = measureElement.offsetWidth;
        const minWidth = 120; // Minimum width
        const padding = 32; // Account for padding and border
        const newWidth = Math.max(minWidth, textWidth + padding);
        
        setWidth(newWidth);
      }
    }, [autoResize, value, props.placeholder]);

    return (
      <div className="relative">
        <input
          ref={inputRef}
          type={type}
          className={cn(
            "flex h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
            autoResize ? "min-w-[120px]" : "w-full",
            className
          )}
          style={autoResize ? { width: width || 120 } : undefined}
          value={value}
          onChange={onChange}
          {...props}
        />
        {autoResize && (
          <span
            ref={measureRef}
            className="absolute invisible whitespace-pre text-sm"
            style={{ left: '-9999px', top: '-9999px' }}
          />
        )}
      </div>
    );
  }
);

Input.displayName = 'Input';