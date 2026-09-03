import { CheckCircle2, Download, RotateCcw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getDownloadUrl, type ExtractionResult } from "@/lib/api";

interface CompletionDialogProps {
  open: boolean;
  result: ExtractionResult | null;
  filename: string;
  onClose: () => void;
  onReset: () => void;
}

export function CompletionDialog({
  open,
  result,
  filename,
  onClose,
  onReset,
}: CompletionDialogProps) {
  if (!result) return null;

  const previewRows = (result.preview_data || []).slice(0, 5);
  const columns = previewRows.length
    ? Object.keys(previewRows[0])
    : [];

  const HEADER_MAP: Record<string, string> = {
    SOURCE_FILE: "SOURCE_FILE",
    INV_DATE: "Inv Date",
    INV_NUMBER: "Inv Number",
    BILLING_PERIOD: "Billing Period",
    LASTNAME: "Last Name",
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="p-0 border-none overflow-hidden sm:max-w-3xl max-h-[90vh] flex flex-col rounded-2xl shadow-2xl bg-card transition-colors duration-500">
        <div className="bg-secondary p-8 flex-shrink-0 flex flex-col items-center text-center relative overflow-hidden transition-colors duration-500">
          {/* Subtle glow effect */}
          <div className="absolute top-0 left-1/2 -translate-x-1/2 w-64 h-32 bg-accent/20 blur-[60px] rounded-full pointer-events-none" />

          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full border-2 border-accent/20 bg-accent/10 success-glow relative z-10 transition-all">
            <CheckCircle2 className="h-7 w-7 text-accent" />
          </div>

          <div className="space-y-3 relative z-10 w-full flex flex-col items-center">
            <DialogTitle className="text-2xl font-bold text-white tracking-tight">
              Extraction Complete!
            </DialogTitle>

            <div className="flex items-center gap-2 bg-[#91b99b22] text-[#91b99b] px-4 py-1.5 rounded-full text-sm font-bold border border-[#91b99b44] shadow-lg shadow-black/20">
              <span className="h-2 w-2 rounded-full bg-[#91b99b] animate-pulse" />
              {result.row_count} Rows Extracted
            </div>

            <div className="text-white/50 text-xs font-medium uppercase tracking-widest truncate max-w-[80%] opacity-80" title={filename}>
              {filename}
            </div>
          </div>
        </div>

        <div className="p-6 bg-card flex-1 overflow-auto transition-colors duration-500">
          {/* Preview Table */}
          {previewRows.length > 0 && (
            <div className="rounded-xl border border-border bg-card shadow-sm overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow className="border-border bg-muted/50 hover:bg-muted/50 transition-colors">
                    {columns.map((col) => (
                      <TableHead
                        key={col}
                        className="text-primary font-bold py-4 px-4 text-xs uppercase tracking-wider h-12"
                      >
                        {HEADER_MAP[col] || col}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {previewRows.map((row, i) => (
                    <TableRow key={i} className="border-border hover:bg-muted/20 transition-colors">
                      {columns.map((col) => (
                        <TableCell key={col} className="py-4 px-4 font-medium text-foreground/80">
                          {row[col] ?? "—"}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Actions */}
          <div className="mt-8 flex flex-col sm:flex-row gap-4">
            <Button
              asChild
              className="flex-1 h-12 bg-primary text-white hover:bg-primary/90 font-bold rounded-xl shadow-lg shadow-blue-900/10 transition-all active:scale-[0.98]"
            >
              <a href={getDownloadUrl(result.output_file)} download>
                <Download className="mr-2 h-5 w-5" />
                Download Excel Result
              </a>
            </Button>
            <Button
              variant="outline"
              className="flex-1 h-12 border-border text-foreground/70 hover:bg-muted font-bold rounded-xl transition-all"
              onClick={onReset}
            >
              <RotateCcw className="mr-2 h-5 w-5" />
              Extract Another
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
