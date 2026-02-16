import { useCallback, useState } from "react";
import { Upload, FileText, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

interface FileUploadZoneProps {
  onExtract: (file: File) => void;
  disabled?: boolean;
}

export function FileUploadZone({ onExtract, disabled }: FileUploadZoneProps) {
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const handleFile = useCallback((f: File) => {
    if (f.type !== "application/pdf") {
      toast.error("Only PDF files are supported");
      return;
    }
    setFile(f);
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
    },
    [handleFile]
  );

  const onFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files?.[0]) handleFile(e.target.files[0]);
    },
    [handleFile]
  );

  return (
    <div className="flex flex-col items-center gap-8 w-full max-w-xl mx-auto">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={`relative w-full rounded-2xl border-2 transition-all duration-300 cursor-pointer bg-card p-2 shadow-sm ${dragOver
          ? "border-primary scale-[1.01]"
          : "border-border shadow-primary/5"
          }`}
        onClick={() => document.getElementById("pdf-input")?.click()}
      >
        <div className="border-2 border-dashed border-border rounded-xl py-12 px-6 flex flex-col items-center gap-4 hover:bg-muted/30 transition-colors">
          <input
            id="pdf-input"
            type="file"
            accept=".pdf,application/pdf"
            className="hidden"
            onChange={onFileInput}
            disabled={disabled}
          />

          {file ? (
            <div className="flex flex-col items-center gap-3 animate-in zoom-in-95 duration-300">
              <div className="relative">
                <FileText className="h-16 w-16 text-primary" />
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setFile(null);
                  }}
                  className="absolute -top-1 -right-1 rounded-full bg-destructive p-1.5 text-white hover:scale-110 transition-transform shadow-md"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
              <p className="text-foreground font-semibold text-lg">{file.name}</p>
              <p className="text-muted-foreground text-sm">
                {(file.size / 1024 / 1024).toFixed(2)} MB
              </p>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-4">
              <Upload className="h-20 w-20 text-muted-foreground/40 stroke-[1.5]" />
              <div className="text-center">
                <p className="text-foreground font-semibold text-xl mb-1">
                  Drag & drop your PDF here
                </p>
                <p className="text-muted-foreground">
                  or click to browse files
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      <Button
        size="lg"
        disabled={!file || disabled}
        onClick={() => file && onExtract(file)}
        className="w-full h-14 bg-primary text-white hover:bg-primary/90 font-bold text-lg rounded-xl transition-all active:scale-[0.98] shadow-lg shadow-blue-900/10"
      >
        Extract Invoice Data
      </Button>
    </div>
  );
}
