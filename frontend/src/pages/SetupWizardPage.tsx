import React from "react";
import { SetupWizard } from "../features/stores/SetupWizard";

export interface SetupWizardPageProps {
  onComplete?: (storeId: string) => void;
  onCancel?: () => void;
}

export const SetupWizardPage: React.FC<SetupWizardPageProps> = ({ onComplete, onCancel }) => {
  return (
    <div className="p-6 bg-[var(--ds-background-default)] min-h-full">
      <SetupWizard onComplete={onComplete} onCancel={onCancel} />
    </div>
  );
};
