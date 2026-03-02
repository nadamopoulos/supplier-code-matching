// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  csv1: { headers: [], rows: [] },
  csv2: { headers: [], rows: [] },
  results: [],
};

const LLM_BATCH_SIZE = 20;

// ---------------------------------------------------------------------------
// CSV loading
// ---------------------------------------------------------------------------
document.getElementById('csv1-input').addEventListener('change', (e) => {
  loadCSV(e.target.files[0], 'csv1');
});
document.getElementById('csv2-input').addEventListener('change', (e) => {
  loadCSV(e.target.files[0], 'csv2');
});

function loadCSV(file, which) {
  if (!file) return;
  Papa.parse(file, {
    header: true,
    skipEmptyLines: true,
    complete(result) {
      const headers = result.meta.fields || [];
      const rows = result.data || [];

      if (which === 'csv1') {
        state.csv1 = { headers, rows };
        document.getElementById('csv1-info').textContent =
          `${file.name}  (${rows.length.toLocaleString()} rows)`;
        document.getElementById('csv1-info').classList.add('loaded');
        populateSelect('csv1-id-col', headers);
        populateSelect('csv1-name-col', headers);
        autoSelect('csv1-id-col', headers, ['id', 'uid', 'unique', 'invoice', 'key', 'identifier']);
        autoSelect('csv1-name-col', headers, ['supplier', 'vendor', 'name', 'company']);
      } else {
        state.csv2 = { headers, rows };
        document.getElementById('csv2-info').textContent =
          `${file.name}  (${rows.length.toLocaleString()} rows)`;
        document.getElementById('csv2-info').classList.add('loaded');
        populateSelect('csv2-name-col', headers);
        populateSelect('csv2-code-col', headers);
        autoSelect('csv2-name-col', headers, ['supplier', 'vendor', 'name', 'company']);
        autoSelect('csv2-code-col', headers, ['code', 'id', 'number', 'key', 'identifier']);
      }
    },
    error(err) {
      alert('Failed to parse CSV: ' + err.message);
    },
  });
}

function populateSelect(id, headers) {
  const sel = document.getElementById(id);
  sel.innerHTML = '';
  headers.forEach((h) => {
    const opt = document.createElement('option');
    opt.value = h;
    opt.textContent = h;
    sel.appendChild(opt);
  });
  sel.disabled = false;
}

function autoSelect(id, headers, keywords) {
  const sel = document.getElementById(id);
  for (const kw of keywords) {
    for (let i = 0; i < headers.length; i++) {
      if (headers[i].toLowerCase().includes(kw)) {
        sel.selectedIndex = i;
        return;
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------
function validate() {
  if (!state.csv1.rows.length) { alert('Please load a source CSV.'); return false; }
  if (!state.csv2.rows.length) { alert('Please load a lookup CSV.'); return false; }
  if (!document.getElementById('csv1-id-col').value) { alert('Select the Unique ID column.'); return false; }
  if (!document.getElementById('csv1-name-col').value) { alert('Select the Supplier Name column for CSV1.'); return false; }
  if (!document.getElementById('csv2-name-col').value) { alert('Select the Supplier Name column for CSV2.'); return false; }
  if (!document.getElementById('csv2-code-col').value) { alert('Select the Supplier Code column for CSV2.'); return false; }
  return true;
}

// ---------------------------------------------------------------------------
// Progress helpers
// ---------------------------------------------------------------------------
function showProgress(pct, text) {
  const card = document.getElementById('progress-card');
  card.style.display = '';
  document.getElementById('progress-fill').style.width = pct + '%';
  document.getElementById('progress-status').textContent = text;
}

// ---------------------------------------------------------------------------
// Main matching flow
// ---------------------------------------------------------------------------
async function runMatching() {
  if (!validate()) return;

  const btn = document.getElementById('run-btn');
  btn.disabled = true;
  btn.textContent = 'Running...';

  // Clear previous results
  document.getElementById('results-card').style.display = 'none';
  document.getElementById('results-body').innerHTML = '';
  document.getElementById('summary').innerHTML = '';
  state.results = [];

  const idCol = document.getElementById('csv1-id-col').value;
  const nameColSrc = document.getElementById('csv1-name-col').value;
  const nameColLookup = document.getElementById('csv2-name-col').value;
  const codeCol = document.getElementById('csv2-code-col').value;
  const apiKey = document.getElementById('api-key').value.trim();

  // Build request data
  const sourceRecords = state.csv1.rows
    .filter((r) => r[idCol] && r[nameColSrc])
    .map((r) => ({ unique_id: r[idCol].trim(), supplier_name: r[nameColSrc].trim() }));

  const lookupEntries = state.csv2.rows
    .filter((r) => r[nameColLookup] && r[codeCol])
    .map((r) => ({ supplier_name: r[nameColLookup].trim(), supplier_code: r[codeCol].trim() }));

  if (!sourceRecords.length) { alert('No valid source records found.'); btn.disabled = false; btn.textContent = 'Run Matching'; return; }
  if (!lookupEntries.length) { alert('No valid lookup entries found.'); btn.disabled = false; btn.textContent = 'Run Matching'; return; }

  try {
    // Phase 1: Exact matching
    showProgress(10, `Phase 1: Exact matching (${sourceRecords.length.toLocaleString()} records)...`);

    const exactRes = await fetch('/api/match-exact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_records: sourceRecords, lookup_entries: lookupEntries }),
    });

    if (!exactRes.ok) {
      const err = await exactRes.text();
      throw new Error('Phase 1 failed: ' + err);
    }

    const exactData = await exactRes.json();
    const allResults = [...exactData.matched];
    let unmatched = exactData.unmatched;

    showProgress(30, `Phase 1 complete: ${exactData.stats.exact_matches} exact matches, ${exactData.stats.unmatched} unmatched`);

    // Phase 2: LLM matching (if API key provided and unmatched exist)
    if (apiKey && unmatched.length > 0) {
      const batches = [];
      for (let i = 0; i < unmatched.length; i += LLM_BATCH_SIZE) {
        batches.push(unmatched.slice(i, i + LLM_BATCH_SIZE));
      }

      for (let i = 0; i < batches.length; i++) {
        const pct = 30 + ((i + 1) / batches.length) * 65;
        showProgress(pct, `Phase 2: LLM batch ${i + 1}/${batches.length}...`);

        try {
          const llmRes = await fetch('/api/match-llm-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              api_key: apiKey,
              unmatched_records: batches[i],
              lookup_entries: lookupEntries,
            }),
          });

          if (!llmRes.ok) {
            const err = await llmRes.text();
            console.error(`Batch ${i + 1} failed:`, err);
            // Mark as no_match
            batches[i].forEach((r) => {
              allResults.push({
                unique_id: r.unique_id,
                supplier_name: r.supplier_name,
                matched_supplier_name: null,
                supplier_code: null,
                match_method: 'no_match',
                confidence: 0.0,
              });
            });
            continue;
          }

          const llmData = await llmRes.json();
          allResults.push(...llmData.results);
        } catch (err) {
          console.error(`Batch ${i + 1} error:`, err);
          batches[i].forEach((r) => {
            allResults.push({
              unique_id: r.unique_id,
              supplier_name: r.supplier_name,
              matched_supplier_name: null,
              supplier_code: null,
              match_method: 'no_match',
              confidence: 0.0,
            });
          });
        }
      }
    } else if (unmatched.length > 0) {
      // No API key — mark all unmatched as no_match
      unmatched.forEach((r) => {
        allResults.push({
          unique_id: r.unique_id,
          supplier_name: r.supplier_name,
          matched_supplier_name: null,
          supplier_code: null,
          match_method: 'no_match',
          confidence: 0.0,
        });
      });
    }

    showProgress(100, 'Done!');

    // Sort results to match original CSV1 order
    const orderMap = {};
    sourceRecords.forEach((r, i) => { orderMap[r.unique_id] = i; });
    allResults.sort((a, b) => (orderMap[a.unique_id] ?? 0) - (orderMap[b.unique_id] ?? 0));

    state.results = allResults;
    renderResults(allResults);
  } catch (err) {
    alert('Error: ' + err.message);
    console.error(err);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Matching';
  }
}

// ---------------------------------------------------------------------------
// Render results
// ---------------------------------------------------------------------------
function renderResults(results) {
  const card = document.getElementById('results-card');
  card.style.display = '';

  const total = results.length;
  const exactCount = results.filter((r) => r.match_method === 'exact').length;
  const llmCount = results.filter((r) => r.match_method === 'llm').length;
  const noneCount = results.filter((r) => r.match_method === 'no_match').length;
  const llmAvg = llmCount > 0
    ? (results.filter((r) => r.match_method === 'llm').reduce((s, r) => s + r.confidence, 0) / llmCount).toFixed(2)
    : '0.00';

  const summaryEl = document.getElementById('summary');
  summaryEl.innerHTML = `
    <span class="stat stat-total">Total: ${total.toLocaleString()}</span>
    <span class="stat stat-exact">Exact: ${exactCount.toLocaleString()} (${((exactCount / total) * 100).toFixed(1)}%)</span>
    <span class="stat stat-llm">LLM: ${llmCount.toLocaleString()} (${((llmCount / total) * 100).toFixed(1)}%) avg: ${llmAvg}</span>
    <span class="stat stat-none">No match: ${noneCount.toLocaleString()} (${((noneCount / total) * 100).toFixed(1)}%)</span>
  `;

  const tbody = document.getElementById('results-body');
  tbody.innerHTML = '';

  for (const r of results) {
    const tr = document.createElement('tr');
    tr.className = 'row-' + r.match_method;
    tr.innerHTML = `
      <td title="${esc(r.unique_id)}">${esc(r.unique_id)}</td>
      <td title="${esc(r.supplier_name)}">${esc(r.supplier_name)}</td>
      <td title="${esc(r.matched_supplier_name || '')}">${esc(r.matched_supplier_name || '')}</td>
      <td>${esc(r.supplier_code || '')}</td>
      <td>${r.match_method}</td>
      <td>${r.confidence.toFixed(2)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// ---------------------------------------------------------------------------
// CSV download
// ---------------------------------------------------------------------------
function downloadCSV() {
  if (!state.results.length) return;

  const headers = ['Unique ID', 'Supplier Name', 'Matched Supplier Name', 'Supplier Code', 'Match Method', 'Confidence'];
  let csv = headers.join(',') + '\n';

  for (const r of state.results) {
    csv += [
      csvEscape(r.unique_id),
      csvEscape(r.supplier_name),
      csvEscape(r.matched_supplier_name || ''),
      csvEscape(r.supplier_code || ''),
      r.match_method,
      r.confidence.toFixed(2),
    ].join(',') + '\n';
  }

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'matched_output.csv';
  a.click();
  URL.revokeObjectURL(url);
}

function csvEscape(val) {
  if (val == null) return '';
  const str = String(val);
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return '"' + str.replace(/"/g, '""') + '"';
  }
  return str;
}
