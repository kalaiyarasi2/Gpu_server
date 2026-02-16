import { useState, useCallback, useEffect } from "react";
import { FileUploadZone } from "@/components/FileUploadZone";
import { ProcessingSteps } from "@/components/ProcessingSteps";
import { CompletionDialog } from "@/components/CompletionDialog";
import { Sidebar, type HistoryItem } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { extractInvoice, type ExtractionResult } from "@/lib/api";
import { toast } from "sonner";

type AppState = "upload" | "processing" | "done";

const Index = () => {
  const [state, setState] = useState<AppState>("upload");
  const [step, setStep] = useState(-1);
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [filename, setFilename] = useState("");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false); // Hidden by default as requested

  // Load history from localStorage on mount
  useEffect(() => {
    const saved = localStorage.getItem("extraction-history");
    if (saved) {
      try {
        setHistory(JSON.parse(saved));
      } catch (e) {
        console.error("Failed to parse history", e);
      }
    }

    // Check if user previously had sidebar open
    const savedSidebar = localStorage.getItem("sidebar-open");
    if (savedSidebar !== null) {
      setSidebarOpen(savedSidebar === "true");
    }
  }, []);

  // Save history to localStorage whenever it changes
  useEffect(() => {
    localStorage.setItem("extraction-history", JSON.stringify(history));
  }, [history]);

  // Save sidebar state
  useEffect(() => {
    localStorage.setItem("sidebar-open", sidebarOpen.toString());
  }, [sidebarOpen]);

  const addToHistory = useCallback((name: string, isSuccess: boolean) => {
    const newItem: HistoryItem = {
      id: `Batch #${(history.length + 1).toString().padStart(3, '0')}`,
      date: new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      filename: name,
      status: isSuccess ? "success" : "error"
    };
    setHistory(prev => [newItem, ...prev].slice(0, 10)); // Keep last 10
  }, [history.length]);

  const simulateSteps = useCallback(
    async (file: File) => {
      setState("processing");
      setFilename(file.name);

      setStep(0);
      await new Promise((r) => setTimeout(r, 800));

      setStep(1);

      try {
        const promise = extractInvoice(file);
        await new Promise((r) => setTimeout(r, 1200));

        setStep(2);
        const data = await promise;
        await new Promise((r) => setTimeout(r, 800));

        setStep(3);
        await new Promise((r) => setTimeout(r, 600));

        setResult(data);
        setState("done");
        addToHistory(file.name, true);
      } catch (err: any) {
        toast.error(err.message || "Something went wrong");
        addToHistory(file.name, false);
        reset();
      }
    },
    [addToHistory]
  );

  const reset = useCallback(() => {
    setState("upload");
    setStep(-1);
    setResult(null);
    setFilename("");
  }, []);

  return (
    <div className="flex h-screen w-full bg-background overflow-hidden selection:bg-primary/20 transition-colors duration-500">
      <Sidebar
        history={history}
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(false)}
      />

      <div className="flex-1 flex flex-col h-full bg-background relative overflow-hidden transition-all duration-300">
        <TopBar
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen(true)}
        />

        <main className="flex-1 flex flex-col items-center justify-center p-8 relative z-10 overflow-auto">
          {/* Removed -mt-20 to prevent header overlap, using flex-col centering instead */}
          <div className="max-w-4xl w-full flex flex-col items-center text-center animate-in fade-in slide-in-from-bottom-4 duration-700">
            <h1 className="text-5xl font-extrabold tracking-tight text-foreground mb-4 dark:text-white">
              PDF Invoice Extractor
            </h1>
            <p className="text-muted-foreground text-lg mb-12 max-w-lg">
              Upload a PDF invoice and extract structured data instantly using our AI-powered engine.
            </p>

            <div className="w-full max-w-xl transition-all duration-500">
              {state === "upload" && (
                <div className="animate-in fade-in zoom-in-95 duration-500">
                  <FileUploadZone onExtract={simulateSteps} />
                </div>
              )}

              {state === "processing" && (
                <div className="animate-in fade-in zoom-in-95 duration-500 w-full flex justify-center">
                  <div className="w-full max-w-md">
                    <ProcessingSteps currentStep={step} />
                  </div>
                </div>
              )}
            </div>
          </div>
        </main>

        <div className="absolute bottom-0 right-0 w-64 h-64 bg-primary/5 blur-[100px] rounded-full pointer-events-none" />
      </div>

      <CompletionDialog
        open={state === "done"}
        result={result}
        filename={filename}
        onClose={reset}
        onReset={reset}
      />
    </div>
  );
};

export default Index;
