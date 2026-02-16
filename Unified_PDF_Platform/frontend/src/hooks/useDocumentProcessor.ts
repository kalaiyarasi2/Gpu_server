import { useState, useCallback, useRef } from "react";
import type { DocumentFile, ProcessingStage, ExtractionResult } from "@/types/extractor";

const STAGE_FLOW: { stage: ProcessingStage; message: string; duration: [number, number] }[] = [
  { stage: "classification", message: "🧠 Classifying document type...", duration: [1000, 1500] },
  { stage: "classification", message: "✅ Document classified", duration: [300, 500] },
  { stage: "rotation_check", message: "Checking for rotation...", duration: [800, 1500] },
  { stage: "rotation_check", message: "✅ PDF already correctly oriented", duration: [500, 800] },
  { stage: "text_extraction", message: "Extracting text from pages...", duration: [1500, 3000] },
  { stage: "text_extraction", message: "✓ Combined text saved", duration: [400, 700] },
  { stage: "schema_extraction", message: "Analyzing document format...", duration: [1000, 2000] },
  { stage: "policy_detection", message: "Detecting policy boundaries...", duration: [1200, 2500] },
  { stage: "policy_detection", message: "Using AI to detect claim number patterns...", duration: [1500, 3000] },
  { stage: "claim_extraction", message: "Extracting claims using adaptive prompt...", duration: [2000, 4000] },
  { stage: "validation", message: "Validating extraction...", duration: [800, 1500] },
  { stage: "validation", message: "✓ Extraction is COMPLETE", duration: [500, 800] },
];

function randomBetween(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function generateMockResult(fileName: string): ExtractionResult {
  const claimsCount = randomBetween(5, 20);
  const claims = Array.from({ length: claimsCount }, (_, i) => ({
    claim_number: `${randomBetween(30, 49)}${randomBetween(2000, 2200)}${randomBetween(10000, 99999)}0001`,
    claimant_name: `Claimant ${i + 1}`,
    date_of_loss: `${randomBetween(1, 12).toString().padStart(2, "0")}/${randomBetween(1, 28).toString().padStart(2, "0")}/${randomBetween(2020, 2024)}`,
    status: ["Open", "Closed", "Pending"][randomBetween(0, 2)],
    reserve_amount: randomBetween(1000, 50000),
    paid_amount: randomBetween(0, 30000),
  }));

  return {
    insurer: ["Service American Indemnity Company", "Atlas Insurance Group", "National General Insurance"][randomBetween(0, 2)],
    format: "complex_multi_row",
    confidence: randomBetween(88, 99),
    claims_count: claimsCount,
    claims,
  };
}

export function useDocumentProcessor() {
  const [documents, setDocuments] = useState<DocumentFile[]>([]);
  const [activeDocId, setActiveDocId] = useState<string | null>(null);
  const processingQueue = useRef<{ id: string; file: File }[]>([]);
  const isProcessing = useRef(false);

  const updateDoc = useCallback((id: string, updates: Partial<DocumentFile>) => {
    setDocuments((prev) =>
      prev.map((d) => (d.id === id ? { ...d, ...updates } : d))
    );
  }, []);

  const processDocument = useCallback(
    async (id: string, file: File) => {
      updateDoc(id, { stage: "classification", stageMessage: "Starting processing...", startedAt: Date.now() });

      let isSimulationRunning = true;

      // Start simulated progress
      const runSimulation = async () => {
        for (const step of STAGE_FLOW) {
          if (!isSimulationRunning) break;

          updateDoc(id, {
            stage: step.stage,
            stageMessage: step.message
          });

          const delay = randomBetween(step.duration[0], step.duration[1]);
          await new Promise(resolve => setTimeout(resolve, delay));
        }
      };

      const simulationPromise = runSimulation();

      try {
        const formData = new FormData();
        formData.append("file", file);

        const response = await fetch("/api/extract", {
          method: "POST",
          body: formData,
        });

        // Stop simulation regardless of success/fail
        isSimulationRunning = false;

        if (!response.ok) {
          throw new Error(`Server error: ${response.statusText}`);
        }

        const json = await response.json();

        // Handle unified router response format
        const documentType = json.type || "UNKNOWN";
        const jsonPath = json.json || json.output_json;

        // Fetch the JSON file from the backend
        let schema: any = null;
        if (jsonPath) {
          try {
            const schemaResponse = await fetch(`/api/download/${jsonPath}`);
            schema = await schemaResponse.json();
          } catch (e) {
            console.error("Failed to fetch schema:", e);
          }
        }

        // Calculate metadata based on document type
        let totalValue = 0;
        let claimsCount = 0;

        if (documentType === "INSURANCE") {
          // Insurance format: { claims: [...] }
          const claims = schema?.claims || [];
          claimsCount = claims.length;
        } else if (documentType === "INVOICE") {
          // Invoice format: [...] (flat array of records)
          const records = Array.isArray(schema) ? schema : [];

          // Calculate sum of CURRENT_PREMIUM across all records
          totalValue = records.reduce((sum: number, rec: any) => {
            const val = parseFloat(String(rec.CURRENT_PREMIUM || 0).replace(/[^0-9.-]+/g, ""));
            return sum + (isNaN(val) ? 0 : val);
          }, 0);
        }

        const metadata = {
          insurer: documentType === "INSURANCE" ? "Insurance Document" : "Invoice Document",
          format: documentType.toLowerCase(),
          confidence: 95,
          claims_count: claimsCount,
          total_value: totalValue,
          documentType: documentType as any
        };

        updateDoc(id, {
          stage: "complete",
          stageMessage: "✓ Extraction complete",
          result: schema,
          metadata,
          excelPath: json.output_file,
          jsonPath: json.output_json,
          completedAt: Date.now(),
        });
      } catch (error) {
        isSimulationRunning = false;
        console.error("Processing error:", error);
        updateDoc(id, {
          stage: "error",
          error: error instanceof Error ? error.message : "Processing failed",
          stageMessage: "Error",
        });
      }
    },
    [updateDoc]
  );

  const processQueue = useCallback(async () => {
    if (isProcessing.current) return;
    isProcessing.current = true;

    while (processingQueue.current.length > 0) {
      const item = processingQueue.current.shift()!;
      setActiveDocId(item.id);
      try {
        await processDocument(item.id, item.file);
      } catch {
        updateDoc(item.id, { stage: "error", error: "Processing failed", stageMessage: "Error" });
      }
    }

    isProcessing.current = false;
  }, [processDocument, updateDoc]);

  const addFiles = useCallback(
    (files: File[]) => {
      const newDocs: DocumentFile[] = files.map((file) => ({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        file,
        name: file.name,
        size: file.size,
        stage: "queued" as ProcessingStage,
        stageMessage: "Waiting in queue...",
        progress: 0,
        result: null,
        error: null,
        startedAt: null,
        completedAt: null,
      }));

      setDocuments((prev) => [...prev, ...newDocs]);

      if (!activeDocId && newDocs.length > 0) {
        setActiveDocId(newDocs[0].id);
      }

      newDocs.forEach((d) => processingQueue.current.push({ id: d.id, file: d.file }));
      processQueue();
    },
    [activeDocId, processQueue]
  );

  const reprocessDocument = useCallback((id: string) => {
    setDocuments((prev) => {
      const doc = prev.find((d) => d.id === id);
      if (!doc) return prev;

      // Add to queue logic after state update
      setTimeout(() => {
        processingQueue.current.push({ id: doc.id, file: doc.file });
        processQueue();
      }, 0);

      return prev.map((d) =>
        d.id === id
          ? {
            ...d,
            stage: "queued" as ProcessingStage,
            stageMessage: "Reprocessing...",
            result: null,
            error: null,
            progress: 0,
            startedAt: null,
            completedAt: null
          }
          : d
      );
    });
  }, [processQueue]);

  const selectedDoc = documents.find((d) => d.id === activeDocId) || null;

  return {
    documents,
    activeDocId,
    selectedDoc,
    addFiles,
    setActiveDocId,
    reprocessDocument,
  };
}
