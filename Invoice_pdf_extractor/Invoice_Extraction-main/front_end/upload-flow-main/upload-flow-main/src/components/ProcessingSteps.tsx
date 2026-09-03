import { Upload, FileSearch, Table2, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";

export type StepStatus = "idle" | "active" | "done";

export interface Step {
  label: string;
  icon: React.ElementType;
  status: StepStatus;
}

const defaultSteps: Step[] = [
  { label: "Uploading", icon: Upload, status: "idle" },
  { label: "Analyzing PDF", icon: FileSearch, status: "idle" },
  { label: "Extracting Data", icon: Table2, status: "idle" },
  { label: "Complete", icon: CheckCircle2, status: "idle" },
];

interface ProcessingStepsProps {
  currentStep: number; // 0-3, -1 = not started
}

export function ProcessingSteps({ currentStep }: ProcessingStepsProps) {
  const steps = defaultSteps.map((s, i) => ({
    ...s,
    status: (i < currentStep ? "done" : i === currentStep ? "active" : "idle") as StepStatus,
  }));

  return (
    <div className="w-full max-w-xl mx-auto glass-card rounded-xl p-8">
      <div className="flex flex-col gap-6">
        {steps.map((step, i) => {
          const Icon = step.icon;
          const isDone = step.status === "done";
          const isActive = step.status === "active";

          return (
            <div key={step.label} className="flex items-center gap-4">
              {/* Indicator */}
              <div
                className={cn(
                  "flex items-center justify-center h-12 w-12 rounded-full border-2 transition-all duration-500",
                  isDone && "border-[hsl(var(--success))] bg-[hsl(var(--success)/0.15)]",
                  isActive && "border-primary bg-primary/10 step-active",
                  !isDone && !isActive && "border-border bg-muted/30"
                )}
              >
                <Icon
                  className={cn(
                    "h-5 w-5 transition-all duration-500",
                    isDone && "text-[hsl(var(--success))]",
                    isActive && "text-primary float-up",
                    !isDone && !isActive && "text-muted-foreground"
                  )}
                />
              </div>

              {/* Label */}
              <span
                className={cn(
                  "text-base font-medium transition-colors duration-300",
                  isDone && "text-[hsl(var(--success))]",
                  isActive && "text-primary neon-text",
                  !isDone && !isActive && "text-muted-foreground"
                )}
              >
                {step.label}
                {isActive && (
                  <span className="ml-2 inline-block animate-pulse">…</span>
                )}
                {isDone && " ✓"}
              </span>

              {/* Connector line */}
              {i < steps.length - 1 && (
                <div className="flex-1 h-px bg-border ml-2" />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
