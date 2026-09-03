const API_BASE = "http://localhost:8004";

const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const summaryBody = document.getElementById("summary-body");
const depositsTbody = document.querySelector("#deposits-table tbody");
const debitsTbody = document.querySelector("#debits-table tbody");

form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const files = fileInput.files;
  if (files.length === 0) {
    statusEl.textContent = "Please select one or more PDF files.";
    return;
  }

  statusEl.textContent = "Uploading and extracting…";
  form.querySelector("button").disabled = true;
  resultsEl.hidden = true;
  resultsEl.innerHTML = ""; // Clear previous results

  try {
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append("files", files[i]);
    }

    const resp = await fetch(`${API_BASE}/extract`, {
      method: "POST",
      body: formData,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.error || `Request failed with status ${resp.status}`);
    }

    const results = await resp.json();

    // Process each result
    results.forEach((payload, index) => {
      if (payload.status === "error") {
        const errorDiv = document.createElement("div");
        errorDiv.className = "error-result";
        errorDiv.innerHTML = `<h3>${payload.filename}</h3><p class="error">Error: ${payload.error}</p>`;
        resultsEl.appendChild(errorDiv);
        return;
      }

      const data = payload.data || {};
      const meta = data.metadata || payload.metadata || {};
      const claims = data.checks_and_other_debits || [];
      const deposits = data.deposits_and_credits || [];

      const fileResultDiv = document.createElement("div");
      fileResultDiv.className = "file-result";
      fileResultDiv.innerHTML = `
            <h2>Results for: ${payload.filename}</h2>
            <div class="summary">
                <h3>Summary</h3>
                <div class="summary-grid">
                    <div>
                        <div class="summary-label">Source file</div>
                        <div class="summary-value">${meta.source_file || payload.filename}</div>
                    </div>
                    <div>
                        <div class="summary-label">Period</div>
                        <div class="summary-value">${(meta.period_start || "–")} → ${(meta.period_end || "–")}</div>
                    </div>
                    <div>
                        <div class="summary-label">Deposits</div>
                        <div class="summary-value">${deposits.length}</div>
                    </div>
                    <div>
                        <div class="summary-label">Checks/debits</div>
                        <div class="summary-value">${claims.length}</div>
                    </div>
                </div>
            </div>

            <div class="tables">
                <section class="table-card">
                    <h3>Deposits and credits</h3>
                    <table>
                        <thead>
                            <tr><th>Date</th><th>Amount</th><th>Description</th></tr>
                        </thead>
                        <tbody>
                            ${deposits.map(row => `
                                <tr>
                                    <td>${row.date || ""}</td>
                                    <td>${row.amount != null ? row.amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ""}</td>
                                    <td>${row.description || ""}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </section>

                <section class="table-card">
                    <h3>Checks and other debits</h3>
                    <table>
                        <thead>
                            <tr><th>Check #</th><th>Amount</th><th>Date</th></tr>
                        </thead>
                        <tbody>
                            ${claims.map(row => `
                                <tr>
                                    <td>${row.check_no || ""}</td>
                                    <td>${row.amount != null ? row.amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : ""}</td>
                                    <td>${row.date || ""}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </section>
            </div>
            <hr class="file-divider">
        `;
      resultsEl.appendChild(fileResultDiv);
    });

    resultsEl.hidden = false;
    statusEl.textContent = `Processing complete for ${files.length} file(s).`;
  } catch (err) {
    console.error(err);
    statusEl.textContent = `Error: ${err.message || err}`;
  } finally {
    form.querySelector("button").disabled = false;
  }
});

