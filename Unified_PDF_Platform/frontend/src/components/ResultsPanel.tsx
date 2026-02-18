import { Download, FileJson, BarChart3, Building2, RotateCcw, HardHat, FileText, Table as TableIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { DocumentFile } from "@/types/extractor";
import JsonViewer from "./JsonViewer";
import TableView from "./TableView";

interface ResultsPanelProps {
  document: DocumentFile | null;
  onReprocess?: (id: string) => void;
}

const ResultsPanel = ({ document, onReprocess }: ResultsPanelProps) => {
  // ... (previous guard clauses remain same)
  if (!document) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-muted-foreground gap-3">
        <FileJson className="w-12 h-12 opacity-30" />
        <p className="text-sm">Select a document to view results</p>
      </div>
    );
  }

  if (document.stage === "error") {
    // ... (retry button logic)
    return (
      <div className="p-6 bg-destructive/5 rounded-lg border border-destructive/20 flex flex-col gap-4">
        <div>
          <p className="text-sm text-destructive font-medium">Processing Error</p>
          <p className="text-xs text-muted-foreground mt-1">{document.error}</p>
        </div>
        {onReprocess && (
          <Button
            variant="outline"
            size="sm"
            className="w-fit text-destructive border-destructive/20 hover:bg-destructive/10"
            onClick={() => onReprocess(document.id)}
          >
            <RotateCcw className="w-4 h-4 mr-2" />
            Retry Extraction
          </Button>
        )}
      </div>
    );
  }

  if (!document.result) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-muted-foreground gap-3">
        <div className="stage-pulse">
          <FileJson className="w-12 h-12 opacity-40" />
        </div>
        <p className="text-sm">Processing in progress...</p>
        <p className="text-xs">{document.stageMessage}</p>
      </div>
    );
  }

  const { result, metadata } = document;
  const docType = metadata?.documentType;
  const isWorkComp = docType === "WORK_COMPENSATION";
  const isInvoice = docType === "INVOICE";
  const wcMeta = metadata?.work_comp_metadata;

  // Normalized data for table view
  const tableData = metadata?.documentType === "INSURANCE"
    ? (Array.isArray(result?.claims) ? result.claims : [])
    : (Array.isArray(result) ? result : []);

  const handleDownloadJson = () => {
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = globalThis.document.createElement("a");
    a.href = url;
    a.download = `${document.name.replace(".pdf", "")}_extracted.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadExcel = async () => {
    if (!document.excelPath) {
      console.error("No Excel file path available");
      return;
    }

    try {
      const response = await fetch(`/api/download/${document.excelPath}`);
      if (!response.ok) throw new Error("Download failed");

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = globalThis.document.createElement("a");
      a.href = url;
      a.download = document.excelPath;
      a.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      console.error("Excel download error:", error);
    }
  };

  return (
    <div className="space-y-4 animate-slide-up">
      {/* Summary cards */}
      {isWorkComp ? (
        /* Work Comp: only Form Type + Confidence */
        <div className="grid grid-cols-2 gap-3">
          <div className="p-3 rounded-lg bg-muted/50 border border-border">
            <div className="flex items-center gap-2 mb-1">
              <HardHat className="w-3.5 h-3.5 text-primary" />
              <span className="text-[11px] text-muted-foreground font-medium">Form Type</span>
            </div>
            <p className="text-sm font-semibold text-foreground truncate">
              {wcMeta?.form_type || metadata?.insurer || "N/A"}
            </p>
          </div>
          <div className="p-3 rounded-lg bg-muted/50 border border-border">
            <div className="flex items-center gap-2 mb-1">
              <FileText className="w-3.5 h-3.5 text-primary" />
              <span className="text-[11px] text-muted-foreground font-medium">Confidence</span>
            </div>
            <p className="text-sm font-semibold text-foreground">
              {metadata?.confidence ? `${metadata.confidence}%` : "N/A"}
            </p>
          </div>
        </div>
      ) : (
        /* Insurance / Invoice: original 3-card layout */
        <div className="grid grid-cols-3 gap-3">
          <div className="p-3 rounded-lg bg-muted/50 border border-border">
            <div className="flex items-center gap-2 mb-1">
              <Building2 className="w-3.5 h-3.5 text-primary" />
              <span className="text-[11px] text-muted-foreground font-medium">Insurer</span>
            </div>
            <p className="text-sm font-semibold text-foreground truncate">{metadata?.insurer || "N/A"}</p>
          </div>
          <div className="p-3 rounded-lg bg-muted/50 border border-border">
            <div className="flex items-center gap-2 mb-1">
              <BarChart3 className="w-3.5 h-3.5 text-primary" />
              <span className="text-[11px] text-muted-foreground font-medium">
                {isInvoice ? "Total Value" : "Claims Found"}
              </span>
            </div>
            <p className="text-sm font-semibold text-foreground">
              {isInvoice
                ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(metadata?.total_value || 0)
                : (metadata?.claims_count || result?.claims?.length || 0)
              }
            </p>
          </div>
          <div className="p-3 rounded-lg bg-muted/50 border border-border">
            <div className="flex items-center gap-2 mb-1">
              <FileText className="w-3.5 h-3.5 text-primary" />
              <span className="text-[11px] text-muted-foreground font-medium">Confidence</span>
            </div>
            <p className="text-sm font-semibold text-foreground">
              {metadata?.confidence ? `${metadata.confidence}%` : "N/A"}
            </p>
          </div>
        </div>
      )}


      {/* WC States badge row for Work Comp */}
      {isWorkComp && wcMeta?.wc_states && wcMeta.wc_states.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <span className="text-[11px] text-muted-foreground font-medium self-center">States:</span>
          {wcMeta.wc_states.map((state) => (
            <span
              key={state}
              className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-primary/10 text-primary border border-primary/20"
            >
              {state}
            </span>
          ))}
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-2">
        <Button size="sm" onClick={handleDownloadJson}>
          <FileJson className="w-4 h-4 mr-2" />
          Download JSON
        </Button>
        <Button size="sm" variant="outline" onClick={handleDownloadExcel} disabled={!document.excelPath}>
          <Download className="w-4 h-4 mr-2" />
          Download Excel
        </Button>
        {onReprocess && (
          <Button size="sm" variant="ghost" className="text-muted-foreground hover:text-primary" onClick={() => onReprocess(document.id)}>
            <RotateCcw className="w-3.5 h-3.5 mr-2" />
            Reprocess
          </Button>
        )}
      </div>

      {/* View Switcher */}
      <Tabs defaultValue="table" className="w-full">
        <TabsList className="grid w-[400px] grid-cols-2 mb-2">
          <TabsTrigger value="table" className="text-xs flex items-center gap-2 font-semibold tracking-wide">
            <TableIcon className="w-3.5 h-3.5" />
            TABLE VIEW
          </TabsTrigger>
          <TabsTrigger value="json" className="text-xs flex items-center gap-2 font-semibold tracking-wide">
            <FileJson className="w-3.5 h-3.5" />
            JSON VIEW
          </TabsTrigger>
        </TabsList>
        <TabsContent value="table" className="mt-0">
          <TableView data={tableData} title="Extracted Data Grid" maxHeight="450px" />
        </TabsContent>
        <TabsContent value="json" className="mt-0">
          <JsonViewer data={result} title="Raw Extraction Data" maxHeight="450px" />
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default ResultsPanel;
