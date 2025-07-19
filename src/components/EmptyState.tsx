import React from 'react';
import * as Icons from 'lucide-react';
import { cn } from '../lib/utils';

interface EmptyStateProps {
  title: string;
  description: string;
  icon: string;
  isSpinning?: boolean;
  showBackground?: boolean;
}

const EmptyState: React.FC<EmptyStateProps> = ({ title, description, icon, isSpinning = false, showBackground = true }) => {
  // Dynamically get icon component
  const IconComponent = (Icons as any)[icon] || Icons.HelpCircle;

  return (
    <div className={cn(
      "text-center p-8 h-full flex flex-col items-center justify-center",
      showBackground && "bg-card"
    )}>
      <div className="flex items-center justify-center w-16 h-16 rounded-full bg-muted mb-4">
        <IconComponent className={cn("h-8 w-8 text-muted-foreground", { 'animate-spin': isSpinning })} />
      </div>
      <h3 className="text-lg font-semibold mb-1">{title}</h3>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  );
};

export default EmptyState;