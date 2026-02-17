import { Download, FileText, Merge, Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { DocumentFile } from "@/types/extractor";
import { useState } from "react";
import { toast } from "sonner";

interface MergeJsonButtonProps {
  documents: DocumentFile[];
  onSummaryGenerated?: (summary: string) => void;
}

const MergeJsonButton = ({ documents, onSummaryGenerated }: MergeJsonButtonProps) => {
  const [isSummarizing, setIsSummarizing] = useState(false);
  const completedDocs = documents.filter((d) => d.stage === "complete" && d.result);

  if (completedDocs.length < 2) return null;

  const getAllClaims = () => {
    return completedDocs.flatMap((d) => d.result?.claims || []);
  };

  const handleDownloadJson = () => {
    const claims = getAllClaims();
    const blob = new Blob([JSON.stringify(claims, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = globalThis.document.createElement("a");
    a.href = url;
    a.download = `merged_claims_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadCsv = () => {
    const claims = getAllClaims();
    if (claims.length === 0) return;

    // Get all unique keys for headers
    const headers = Array.from(new Set(claims.flatMap(c => Object.keys(c))));

    const csvRows = [
      headers.join(','),
      ...claims.map(row =>
        headers.map(header => {
          const val = row[header];
          const escaped = ('' + (val ?? '')).replace(/"/g, '""');
          return `"${escaped}"`;
        }).join(',')
      )
    ];

    const blob = new Blob([csvRows.join('\n')], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = globalThis.document.createElement("a");
    a.href = url;
    a.download = `merged_claims_${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleGenerateSummary = async () => {
    const claims = getAllClaims();
    if (claims.length === 0) return;

    setIsSummarizing(true);
    try {
      const response = await fetch("/api/claim-summary", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ claims }),
      });

      const data = await response.json();
      if (data.success && data.summary) {
        if (onSummaryGenerated) {
          onSummaryGenerated(data.summary);
          toast.success("AI Summary generated successfully!");
        }
      } else {
        toast.error("Failed to generate summary: " + (data.error || "Unknown error"));
      }
    } catch (error) {
      console.error("Error generating summary:", error);
      toast.error("Error connecting to summary service");
    } finally {
      setIsSummarizing(false);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <Button
          onClick={handleDownloadJson}
          className="bg-stage-done hover:bg-stage-done/90 text-primary-foreground flex-1"
        >
          <Merge className="w-4 h-4 mr-2" />
          Merge JSON
          <Download className="w-4 h-4 ml-2" />
        </Button>
        <Button
          variant="outline"
          onClick={handleDownloadCsv}
          className="border-stage-done text-stage-done hover:bg-stage-done/5 flex-1"
        >
          <Download className="w-4 h-4 mr-2" />
          Merge CSV
        </Button>
      </div>

      <div className="flex gap-2">
        <Button
          variant="secondary"
          onClick={handleGenerateSummary}
          disabled={isSummarizing}
          className="flex-[2] bg-primary/10 hover:bg-primary/20 text-primary border border-primary/20"
        >
          {isSummarizing ? (
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
          ) : (
            <FileText className="w-4 h-4 mr-2" />
          )}
          {isSummarizing ? "Analyzing..." : "Summary of JSON"}
        </Button>
        <Button
          variant="outline"
          onClick={() => window.location.reload()}
          className="flex-1 border-muted-foreground/20 text-muted-foreground hover:bg-muted/50"
          title="Clear all and start over"
        >
          <RotateCcw className="w-4 h-4 mr-2" />
          Reset
        </Button>
      </div>
    </div>
  );
};

export default MergeJsonButton;