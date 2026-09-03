// Configure your FastAPI backend URL here
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://10.10.8.180:8005";

export interface ExtractionResult {
  filename: string;
  row_count: number;
  output_file: string;
  preview_data: Record<string, string>[];
}

export async function extractInvoice(file: File): Promise<ExtractionResult> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_BASE_URL}/api/extract`, {
    method: "POST",
    body: formData,
  });

  if (res.status === 429) {
    throw new Error("OpenAI API quota exhausted. Please check your billing/usage limits.");
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Extraction failed" }));
    throw new Error(err.detail || "Extraction failed");
  }

  return res.json();
}

export function getDownloadUrl(filename: string): string {
  return `${API_BASE_URL}/api/download/${encodeURIComponent(filename)}`;
}
