import kenyaMap from '@svg-maps/kenya';

// ===== Dark mode toggle =====
export function setupDarkMode() {
  const themeToggleBtns = document.querySelectorAll('.theme-toggle');
  
  if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    document.documentElement.classList.add('dark');
  } else {
    document.documentElement.classList.remove('dark');
  }

  themeToggleBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      if (document.documentElement.classList.contains('dark')) {
        document.documentElement.classList.remove('dark');
        localStorage.theme = 'light';
      } else {
        document.documentElement.classList.add('dark');
        localStorage.theme = 'dark';
      }
    });
  });
}

// ===== Active nav link =====
export function setupNavigation() {
  const currentPath = window.location.pathname;
  const links = document.querySelectorAll('nav a, header nav a');
  
  links.forEach(link => {
    const href = link.getAttribute('href');
    if (href && currentPath.endsWith(href.replace('./', ''))) {
      link.classList.add('text-primary', 'border-b-2', 'border-primary');
      link.classList.remove('text-on-surface-variant');
    }
  });
}

// ===== API base URL =====
const API_BASE = window.location.hostname === 'localhost' ? 'http://localhost:8000' : '';

// ===== Authentication Logic =====
export function setupAuth() {
    const loginModal = document.getElementById('login-modal');
    
                
                if (res.ok) {
                    const data = await res.json();
                    localStorage.setItem('auth_token', data.access_token);
                    loginModal.classList.add('hidden');
                    window.location.reload(); // reload to fetch data
                } else {
                    loginError.classList.remove('hidden');
                }
            } catch (err) {
                console.error("Login error", err);
                loginError.classList.remove('hidden');
            }
        });
    }
}

// Intercept all fetch requests to add the Authorization header
window._originalFetch = window.fetch;
window.fetch = async function(...args) {
    let [resource, config] = args;
    
    const token = localStorage.getItem('auth_token');
    
    // Only intercept calls to our API
    if (typeof resource === 'string' && resource.startsWith(API_BASE) && !resource.includes('/api/auth/token')) {
        config = config || {};
        config.headers = {
            ...config.headers,
            'Authorization': `Bearer ${token}`
        };
    }
    
    const response = await window._originalFetch(resource, config);
    if (response.status === 401) {
        // Token expired or invalid
        localStorage.removeItem('auth_token');
        if (document.getElementById('login-modal')) {
            document.getElementById('login-modal').classList.remove('hidden');
        } else {
            window.location.href = '/index.html';
        }
    }
    return response;
};

// ===== Formatter =====
function formatKES(num) {
  if (!num || isNaN(num)) return 'KES 0';
  if (num >= 1e12) return 'KES ' + (num / 1e12).toFixed(1) + 'T';
  if (num >= 1e9) return 'KES ' + (num / 1e9).toFixed(1) + 'B';
  if (num >= 1e6) return 'KES ' + (num / 1e6).toFixed(1) + 'M';
  if (num >= 1e3) return 'KES ' + (num / 1e3).toFixed(0) + 'K';
  return 'KES ' + num.toLocaleString();
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    if(isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

// ===== Fetch Stats (index.html) =====
async function fetchStats() {
  try {
    const response = await fetch(`${API_BASE}/api/stats`);
    if (!response.ok) throw new Error('Network response was not ok');
    const data = await response.json();
    
    const elTotalKes = document.getElementById('stat-total-kes');
    if (elTotalKes) elTotalKes.textContent = formatKES(data.total_monitored_spend_kes);

    const elSuppliers = document.getElementById('stat-suppliers');
    if (elSuppliers) elSuppliers.textContent = `${data.total_suppliers_mapped.toLocaleString()} suppliers mapped`;

    const elRedFlags = document.getElementById('stat-red-flags');
    if (elRedFlags) elRedFlags.textContent = data.total_red_flags.toLocaleString();

    const elHighSev = document.getElementById('stat-high-severity');
    if (elHighSev) elHighSev.textContent = `${data.high_severity_flags} High Severity`;

  } catch (error) {
    console.error('Failed to fetch stats:', error);
  }
}

// ===== Red Flags Logic =====
const KENYA_COUNTIES = [
  "Baringo", "Bomet", "Bungoma", "Busia", "Elgeyo-Marakwet", "Embu", "Garissa", "Homa Bay", 
  "Isiolo", "Kajiado", "Kakamega", "Kericho", "Kiambu", "Kilifi", "Kirinyaga", "Kisii", 
  "Kisumu", "Kitui", "Kwale", "Laikipia", "Lamu", "Machakos", "Makueni", "Mandera", 
  "Marsabit", "Meru", "Migori", "Mombasa", "Murang'a", "Nairobi", "Nakuru", "Nandi", 
  "Narok", "Nyamira", "Nyandarua", "Nyeri", "Samburu", "Siaya", "Taita Taveta", "Tana River", 
  "Tharaka-Nithi", "Trans Nzoia", "Turkana", "Uasin Gishu", "Vihiga", "Wajir", "West Pokot"
];

let allRedFlags = [];
let filteredRedFlags = [];
let currentPage = 1;
const itemsPerPage = 50;

function populateJurisdictions() {
  const select = document.getElementById('filter-jurisdiction');
  if (!select) return;

  select.innerHTML = '<option value="">All Regions</option>';

  const activeCounties = new Set();
  allRedFlags.forEach(f => {
    const entity = (f.entity || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    KENYA_COUNTIES.forEach(county => {
      const normalizedCounty = county.toLowerCase().replace(/[^a-z0-9]/g, '');
      if (entity.includes(normalizedCounty)) {
        activeCounties.add(county);
      }
    });
  });

  Array.from(activeCounties).sort().forEach(county => {
    const opt = document.createElement('option');
    opt.value = county;
    opt.textContent = county;
    select.appendChild(opt);
  });
}

async function fetchRedFlags() {
  const tbody = document.getElementById('red-flags-tbody');
  if (!tbody) return;

  try {
    const response = await fetch(`${API_BASE}/api/red-flags?limit=1000`);
    if (!response.ok) throw new Error('Network response was not ok');
    allRedFlags = await response.json();
    filteredRedFlags = [...allRedFlags];
    
    populateJurisdictions();
    
    // Check URL parameters on load
    const urlParams = new URLSearchParams(window.location.search);
    const searchQuery = urlParams.get('search') || '';
    if (searchQuery) {
      const matchedCounty = KENYA_COUNTIES.find(c => c.toLowerCase() === searchQuery.toLowerCase());
      if (matchedCounty) {
        const select = document.getElementById('filter-jurisdiction');
        if (select) select.value = matchedCounty;
      } else {
        const input = document.getElementById('filter-entity');
        if (input) input.value = searchQuery;
      }
      applyRedFlagFilters();
    } else {
      renderRedFlagsPage(1);
    }
  } catch (error) {
    console.error('Failed to fetch red flags:', error);
    tbody.innerHTML = `<tr><td colspan="7" class="py-6 text-center text-extreme-crimson">Error loading red flags from API. Is the backend running?</td></tr>`;
  }
}

function renderRedFlagsPage(page) {
    currentPage = page;
    const tbody = document.getElementById('red-flags-tbody');
    const countSpan = document.getElementById('red-flags-count');
    const paginationInfo = document.getElementById('pagination-info');
    const paginationControls = document.getElementById('pagination-controls');
    
    if (!tbody) return;
    
    if (countSpan) {
      countSpan.innerHTML = `Showing <strong class="text-on-surface">${filteredRedFlags.length}</strong> anomalies matching current filters`;
    }
    
    const totalPages = Math.ceil(filteredRedFlags.length / itemsPerPage) || 1;
    if(currentPage > totalPages) currentPage = totalPages;
    if(currentPage < 1) currentPage = 1;
    
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = Math.min(startIndex + itemsPerPage, filteredRedFlags.length);
    
    if (paginationInfo) {
      paginationInfo.textContent = `Showing ${filteredRedFlags.length > 0 ? startIndex + 1 : 0}-${endIndex} of ${filteredRedFlags.length}`;
    }
    
    // Build pagination buttons
    if (paginationControls) {
        let html = '';
        html += `<button onclick="window.goToPage(${currentPage - 1})" class="w-8 h-8 flex items-center justify-center border border-outline-variant rounded text-on-surface-variant hover:bg-surface-container-low disabled:opacity-50" ${currentPage === 1 ? 'disabled' : ''}><span class="material-symbols-outlined text-[18px]">chevron_left</span></button>`;
        
        for(let i = 1; i <= totalPages; i++) {
            if(i === 1 || i === totalPages || (i >= currentPage - 1 && i <= currentPage + 1)) {
                if(i === currentPage) {
                    html += `<button class="w-8 h-8 flex items-center justify-center border border-primary bg-primary text-on-primary rounded font-data-mono text-[14px]">${i}</button>`;
                } else {
                    html += `<button onclick="window.goToPage(${i})" class="w-8 h-8 flex items-center justify-center border border-outline-variant rounded text-on-surface-variant hover:bg-surface-container-low font-data-mono text-[14px]">${i}</button>`;
                }
            } else if (i === currentPage - 2 || i === currentPage + 2) {
                html += `<span class="w-8 h-8 flex items-center justify-center text-on-surface-variant">...</span>`;
            }
        }
        
        html += `<button onclick="window.goToPage(${currentPage + 1})" class="w-8 h-8 flex items-center justify-center border border-outline-variant rounded text-on-surface-variant hover:bg-surface-container-low disabled:opacity-50" ${currentPage === totalPages ? 'disabled' : ''}><span class="material-symbols-outlined text-[18px]">chevron_right</span></button>`;
        paginationControls.innerHTML = html;
    }
    
    tbody.innerHTML = '';
    
    if (filteredRedFlags.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="py-6 text-center text-on-surface-variant">No red flags found matching criteria.</td></tr>`;
      return;
    }
    
    const pageData = filteredRedFlags.slice(startIndex, endIndex);
    
    pageData.forEach(flag => {
      const flagType = flag.flag_type || '';
      let icon = 'warning';
      let colorClass = 'bg-extreme-crimson text-white';
      let textClass = 'text-extreme-crimson';
      
      if (flagType === 'HIGH_VALUE_NO_COMPETITION') {
        icon = 'gavel';
        colorClass = 'bg-primary text-white';
        textClass = 'text-primary';
      } else if (flagType === 'EXTREME_OUTLIER') {
        icon = 'warning';
      } else if (flagType === 'INSTANT_CONTRACT_START') {
        icon = 'schedule';
        colorClass = 'bg-amber-warning text-white';
        textClass = 'text-amber-warning';
      } else if (flagType === 'NON_COMPETITIVE_OVERUSE') {
        icon = 'hub';
        colorClass = 'bg-dominant-orange text-white';
        textClass = 'text-dominant-orange';
      }

      const awardVal = typeof flag.award_kes === 'number' ? flag.award_kes : parseFloat(flag.award_kes) || 0;

      const tr = document.createElement('tr');
      tr.className = 'hover:bg-surface-container-low transition-colors group cursor-pointer';
      tr.innerHTML = `
        <td class="py-3 px-4 text-center">
            <div class="w-6 h-6 rounded ${colorClass} flex items-center justify-center mx-auto" title="${flagType}">
                <span class="material-symbols-outlined text-[14px]">${icon}</span>
            </div>
        </td>
        <td class="py-3 px-4">
            <div class="text-primary font-bold">${flag.ocid || 'Unknown'}</div>
            <div class="text-[12px] text-on-surface-variant truncate max-w-[200px]">${flagType}</div>
        </td>
        <td class="py-3 px-4 text-on-surface">${flag.entity || '-'}</td>
        <td class="py-3 px-4 text-on-surface font-bold">${flag.supplier || '-'}</td>
        <td class="py-3 px-4 text-right font-bold text-on-surface">${awardVal.toLocaleString(undefined, {maximumFractionDigits: 0})}</td>
        <td class="py-3 px-4 ${textClass} text-[13px]">${flag.description || '-'}</td>
        <td class="py-3 px-4 text-center">
            <a href="${API_BASE}/api/dossier/${encodeURIComponent(flag.supplier)}" target="_blank" class="text-on-surface-variant hover:text-primary transition-colors opacity-0 group-hover:opacity-100 flex items-center justify-center" title="Download Evidence Dossier">
                <span class="material-symbols-outlined text-[20px]">download</span>
            </a>
        </td>
      `;
      tbody.appendChild(tr);
    });
}

// Attach to window so onclick works
window.goToPage = function(page) {
    renderRedFlagsPage(page);
};

// ===== Red Flags Filters =====
function applyRedFlagFilters() {
  const entitySearchInput = document.getElementById('filter-entity');
  const searchStr = entitySearchInput ? entitySearchInput.value.toLowerCase() : '';
  
  const jurisdictionSelect = document.getElementById('filter-jurisdiction');
  const selectedJurisdiction = jurisdictionSelect ? jurisdictionSelect.value : '';

  // Get checked categories
  const categoryCbs = document.querySelectorAll('.flag-category-cb:checked');
  const allowedCategories = Array.from(categoryCbs).map(cb => cb.value);

  filteredRedFlags = allRedFlags.filter(f => {
    // Filter by category
    if (allowedCategories.length > 0) {
      const fType = (f.flag_type || '').toUpperCase();
      if (!allowedCategories.includes(fType)) return false;
    }

    // Filter by jurisdiction (county)
    if (selectedJurisdiction) {
      const entity = (f.entity || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      const normalizedJurisdiction = selectedJurisdiction.toLowerCase().replace(/[^a-z0-9]/g, '');
      if (!entity.includes(normalizedJurisdiction)) return false;
    }

    // Filter by search string
    if (searchStr) {
      const searchable = `${f.entity || ''} ${f.supplier || ''} ${f.ocid || ''}`.toLowerCase();
      if (!searchable.includes(searchStr)) return false;
    }

    return true;
  });

  renderRedFlagsPage(1);
}

function setupRedFlagFilters() {
    const applyBtn = document.getElementById('apply-filters-btn');
    if(applyBtn) {
        applyBtn.addEventListener('click', applyRedFlagFilters);
    }
}


// ===== Fetch Entities (counties.html -> entities.html) =====
async function fetchEntities() {
  const sidebar = document.getElementById('entity-list');
  const detailPanel = document.getElementById('entity-detail');
  if (!sidebar) return;

  try {
    const response = await fetch(`${API_BASE}/api/entities`);
    if (!response.ok) throw new Error('Network response was not ok');
    const entities = await response.json();

    sidebar.innerHTML = '';

    if (entities.length === 0) {
      sidebar.innerHTML = '<div class="p-4 text-on-surface-variant">No entity data found.</div>';
      return;
    }

    entities.forEach((c, idx) => {
      const div = document.createElement('div');
      const isFirst = idx === 0;
      div.className = `p-4 border-b border-outline-variant/30 flex justify-between items-start cursor-pointer transition-colors border-l-4 ${isFirst ? 'bg-surface-container border-l-savannah-green' : 'hover:bg-surface-container-low border-l-transparent'}`;
      div.dataset.entity = c.entity;

      const spendStr = formatKES(c.total_spend).replace('KES ', '');
      div.innerHTML = `
        <div class="overflow-hidden">
          <h3 class="font-body-md ${isFirst ? 'font-bold' : ''} text-on-surface truncate" title="${c.entity}">${c.entity}</h3>
          <p class="font-data-mono text-data-mono text-on-surface-variant mt-1">KES ${spendStr} · ${c.contract_count} contracts</p>
        </div>
        <div class="flex flex-col items-end ml-2 shrink-0">
          <span class="font-data-mono text-[11px] text-on-surface-variant">${c.supplier_count} suppliers</span>
          ${c.flag_count > 0 ? `<span class="px-1.5 py-0.5 bg-extreme-crimson text-white font-label-caps text-[10px] rounded mt-1">${c.flag_count} Flags</span>` : ''}
        </div>
      `;

      div.addEventListener('click', () => {
        // Highlight
        sidebar.querySelectorAll('div[data-entity]').forEach(el => {
          el.classList.remove('bg-surface-container', 'border-l-savannah-green', 'font-bold');
          el.classList.add('hover:bg-surface-container-low', 'border-l-transparent');
        });
        div.classList.add('bg-surface-container', 'border-l-savannah-green');
        div.classList.remove('hover:bg-surface-container-low', 'border-l-transparent');
        div.querySelector('h3').classList.add('font-bold');

        loadEntityDetail(c.entity);
      });

      sidebar.appendChild(div);
    });

    // Add search listener
    const searchInput = document.getElementById('entity-search');
    if(searchInput) {
        searchInput.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase();
            sidebar.querySelectorAll('div[data-entity]').forEach(el => {
                if(el.dataset.entity.toLowerCase().includes(query)) {
                    el.style.display = 'flex';
                } else {
                    el.style.display = 'none';
                }
            });
        });
    }

    // If there is an initial search query, apply it
    const urlParams = new URLSearchParams(window.location.search);
    const initialQuery = urlParams.get('search') || '';
    if (initialQuery && searchInput) {
        searchInput.value = initialQuery;
        let firstMatch = null;
        sidebar.querySelectorAll('div[data-entity]').forEach(el => {
            if(el.dataset.entity.toLowerCase().includes(initialQuery.toLowerCase())) {
                el.style.display = 'flex';
                if (!firstMatch) firstMatch = el.dataset.entity;
            } else {
                el.style.display = 'none';
            }
        });
        if (firstMatch) {
            loadEntityDetail(firstMatch);
            // Highlight the selected one in the sidebar
            sidebar.querySelectorAll('div[data-entity]').forEach(el => {
                if (el.dataset.entity === firstMatch) {
                    el.classList.add('bg-surface-container', 'border-l-savannah-green');
                    el.classList.remove('hover:bg-surface-container-low', 'border-l-transparent');
                    el.querySelector('h3').classList.add('font-bold');
                } else {
                    el.classList.remove('bg-surface-container', 'border-l-savannah-green', 'font-bold');
                    el.classList.add('hover:bg-surface-container-low', 'border-l-transparent');
                }
            });
        } else {
            const detailPanel = document.getElementById('entity-detail');
            if (detailPanel) {
                detailPanel.innerHTML = `<div class="p-6 text-on-surface-variant">No entities match search query "${initialQuery}".</div>`;
            }
        }
    } else if (entities.length > 0) {
        loadEntityDetail(entities[0].entity);
    }

  } catch (error) {
    console.error('Failed to fetch entities:', error);
    sidebar.innerHTML = '<div class="p-4 text-extreme-crimson">Error loading entity data. Is the backend running?</div>';
  }
}

async function loadEntityDetail(entityName) {
  const panel = document.getElementById('entity-detail');
  if (!panel) return;

  panel.innerHTML = '<div class="flex items-center justify-center h-full"><span class="text-on-surface-variant">Loading ' + entityName + '...</span></div>';

  try {
    const response = await fetch(API_BASE + '/api/entities/' + encodeURIComponent(entityName));
    if (!response.ok) throw new Error('Network response was not ok');
    const data = await response.json();

    const summary = data.summary || {};
    const suppliers = data.top_suppliers || [];
    const contracts = data.recent_contracts || [];
    const nlpFindings = data.nlp_findings || [];
    const redFlags = data.red_flags || [];

    // ── Helper: translate flag type to plain English label ───────────────
    function flagLabel(ft) {
      if (ft === 'EXTREME_OUTLIER') return 'Abnormally Large Contract';
      if (ft === 'INSTANT_CONTRACT_START') return 'Suspiciously Fast Execution';
      if (ft === 'Inflated Unit Cost') return 'Price Gouging';
      if (ft === 'DOMINANT_SUPPLIER') return 'Supplier Monopoly';
      if (ft === 'DIRECT_PROCUREMENT_OVERUSE') return 'Direct Procurement Overuse';
      if (ft === 'HIGH_VALUE_NO_COMPETITION') return 'High-Value, No Competition';
      return ft.replace(/_/g, ' ');
    }

    // ── Helper: translate description to plain English ────────────────────
    function plainDescription(desc) {
      // New per-entity format (after main.py fix)
      var m = desc.match(/Award value (KES [\d,]+) exceeds this entity's own 99th-percentile threshold \((KES [\d,]+)\), calculated from (\d+) historical contracts/);
      if (m) {
        return 'Statistical Outlier: This contract (' + m[1] + ') is unusually large — it surpasses the 99th percentile threshold (' + m[2] + ') derived from analysing ' + m[3] + ' real contracts awarded by this specific entity in PPRA records. Only 1% of their own contracts ever reach this value.';
      }
      // Legacy global-percentile format (still in DB from old run)
      var m2 = desc.match(/Award value (KES [\d,]+) exceeds 99th percentile \((KES [\d,]+)\)/);
      if (m2) {
        return 'Statistical Outlier: This contract (' + m2[1] + ') is unusually large compared to the national PPRA contract database. The 99th-percentile cutoff across all entities is ' + m2[2] + ', meaning this award is larger than 99% of all contracts in the dataset.';
      }
      if (desc.includes('Contract started 0 day(s) after award')) {
        return 'Instant Contract Start: Contract executed on the exact same day it was awarded. This is highly unusual and may indicate a pre-arranged deal or a circumvented procurement process.';
      }
      var m3 = desc.match(/Inflated Cost: (.*) unit cost is (KES [\d,.]+) \(Median: (KES [\d,.]+)\)/);
      if (m3) {
        return 'Price Gouging: The supplier charged ' + m3[2] + ' per ' + m3[1] + '. The verified market median for this item is ' + m3[3] + '. Source: benchmarked against comparable PPRA contracts.';
      }
      if (desc.startsWith('Audit (OAG) Pg')) {
        return 'Auditor-General Finding: ' + desc.replace(/Audit \(OAG\) Pg \d+: /, '');
      }
      return desc;
    }

    // ── Build Red Flags HTML ──────────────────────────────────────────────
    function buildRedFlagsHtml() {
      if (redFlags.length === 0) return '';
      var rows = '';
      for (var i = 0; i < redFlags.length; i++) {
        var rf = redFlags[i];
        var sev = (rf.severity || 'UNKNOWN').toUpperCase();
        var sevColor = sev === 'HIGH' ? 'bg-extreme-crimson' : 'bg-dominant-orange';
        var amtHtml = (rf.award_kes > 0) ? '<div class="font-data-mono text-[11px] text-extreme-crimson mt-2 font-bold">' + formatKES(rf.award_kes) + '</div>' : '';
        rows += '<div class="p-3 bg-surface-container-low rounded border border-extreme-crimson/30">'
             + '<div class="flex justify-between items-start mb-1">'
             + '<div class="flex flex-col gap-1">'
             + '<span class="font-label-caps text-[10px] text-extreme-crimson uppercase tracking-wider">' + flagLabel(rf.flag_type) + '</span>'
             + '<span class="text-on-surface-variant font-data-mono text-[10px]">' + (rf.supplier || 'Unknown Supplier') + '</span>'
             + '</div>'
             + '<span class="' + sevColor + ' text-white text-[9px] px-1 py-0.5 rounded">' + sev + '</span>'
             + '</div>'
             + '<div class="font-body-md text-sm text-on-surface mt-2">' + plainDescription(rf.description) + '</div>'
             + amtHtml
             + '</div>';
      }
      return '<div class="mb-8">'
           + '<h4 class="font-label-caps text-on-surface-variant mb-3 flex items-center gap-1 text-extreme-crimson">'
           + '<span class="material-symbols-outlined text-[16px]">flag</span> Active Red Flags (' + redFlags.length + ')'
           + '</h4>'
           + '<div class="space-y-3 max-h-64 overflow-y-auto pr-2">' + rows + '</div>'
           + '</div>';
    }

    // ── Build NLP Findings HTML ───────────────────────────────────────────
    function buildNlpHtml() {
      if (nlpFindings.length === 0) return '';
      var rows = '';
      for (var j = 0; j < nlpFindings.length; j++) {
        var f = nlpFindings[j];
        var amtHtml2 = (f.amount_lost_kes && f.amount_lost_kes > 0)
          ? '<div class="mt-2 text-[12px] font-bold text-extreme-crimson">Amount Lost: KES ' + f.amount_lost_kes.toLocaleString() + '</div>' : '';
        var officersHtml = '';
        if (f.responsible_officers && f.responsible_officers !== '[]') {
          try {
            var officers = JSON.parse(f.responsible_officers);
            if (officers && officers.length > 0) {
              officersHtml = '<div class="mt-1 text-[11px] text-on-surface-variant"><strong>Responsible:</strong> ' + officers.join(', ') + '</div>';
            }
          } catch(e) {}
        }
        var sevColor2 = f.severity === 'HIGH' ? 'bg-extreme-crimson' : (f.severity === 'MEDIUM' ? 'bg-dominant-orange' : 'bg-primary');
        var badge = f.severity ? '<span class="' + sevColor2 + ' text-white text-[9px] px-1 py-0.5 rounded ml-2">' + f.severity + '</span>' : '';
        rows += '<div class="p-3 bg-surface-container-low rounded border border-extreme-crimson/30">'
             + '<div class="flex justify-between items-center mb-1">'
             + '<div><span class="font-label-caps text-[10px] text-extreme-crimson uppercase tracking-wider">' + (f.keyword || '') + '</span>' + badge + '</div>'
             + '<span class="font-data-mono text-[10px] text-on-surface-variant uppercase">' + (f.report_type || '') + ' Pg.' + (f.page_num || '') + '</span>'
             + '</div>'
             + '<div class="font-body-md text-sm text-on-surface italic mt-2">&ldquo;...' + (f.context_snippet || '') + '...&rdquo;</div>'
             + amtHtml2 + officersHtml
             + '</div>';
      }
      return '<div class="mb-8">'
           + '<h4 class="font-label-caps text-on-surface-variant mb-3 flex items-center gap-1 text-extreme-crimson">'
           + '<span class="material-symbols-outlined text-[16px]">warning</span> Auditor-General Findings</h4>'
           + '<div class="space-y-3 max-h-64 overflow-y-auto">' + rows + '</div>'
           + '</div>';
    }

    // ── Build Suppliers HTML ──────────────────────────────────────────────
    function buildSuppliersHtml() {
      var items = suppliers.length === 0 ? '<li class="text-on-surface-variant text-sm">No supplier data</li>' : '';
      for (var k = 0; k < suppliers.length; k++) {
        var s = suppliers[k];
        items += '<li class="flex justify-between items-center py-2 border-b border-outline-variant/30 group">'
               + '<span class="font-body-md text-sm text-on-surface truncate pr-2 flex-1" title="' + (s.supplier_name || '') + '">' + (s.supplier_name || '') + '</span>'
               + '<div class="flex items-center gap-4">'
               + '<span class="font-data-mono text-sm text-on-surface font-bold whitespace-nowrap">' + formatKES(s.total_value) + '</span>'
               + '<a href="' + API_BASE + '/api/dossier/' + encodeURIComponent(s.supplier_name || '') + '" target="_blank" title="Download Evidence Dossier PDF" class="text-on-surface-variant hover:text-primary opacity-0 group-hover:opacity-100 transition-opacity"><span class="material-symbols-outlined text-[18px]">download</span></a>'
               + '</div></li>';
      }
      return '<div class="mb-8">'
           + '<h4 class="font-label-caps text-on-surface-variant mb-3 flex items-center gap-1">'
           + '<span class="material-symbols-outlined text-[16px]">corporate_fare</span> Top Suppliers by Value</h4>'
           + '<ul class="space-y-3">' + items + '</ul></div>';
    }

    // ── Build Contracts HTML ──────────────────────────────────────────────
    function buildContractsHtml() {
      var items = contracts.length === 0 ? '<div class="text-on-surface-variant text-sm">No contract data</div>' : '';
      for (var l = 0; l < contracts.length; l++) {
        var c = contracts[l];
        items += '<div class="p-3 bg-surface-container-low rounded border border-outline-variant/30">'
               + '<div class="font-body-md text-sm text-on-surface font-bold truncate" title="' + (c.tender_title || '') + '">' + (c.tender_title || 'Untitled') + '</div>'
               + '<div class="flex justify-between items-center mt-1">'
               + '<span class="font-data-mono text-[12px] text-on-surface-variant truncate mr-2">' + (c.supplier_name || '-') + '</span>'
               + '<span class="font-data-mono text-[12px] text-on-surface font-bold whitespace-nowrap">' + formatKES(c.award_value) + '</span>'
               + '</div>'
               + '<div class="font-data-mono text-[11px] text-on-surface-variant mt-1">' + formatDate(c.award_date) + '</div>'
               + '</div>';
      }
      return '<div>'
           + '<h4 class="font-label-caps text-on-surface-variant mb-3 flex items-center gap-1">'
           + '<span class="material-symbols-outlined text-[16px]">receipt_long</span> Recent Contracts</h4>'
           + '<div class="space-y-2 max-h-64 overflow-y-auto">' + items + '</div></div>';
    }

    // ── Assemble full panel ───────────────────────────────────────────────
    panel.innerHTML =
      '<div class="p-6 max-w-xl">'
      + '<div class="flex justify-between items-start mb-6"><div>'
      + '<h2 class="font-headline-md text-[20px] text-savannah-green leading-tight">' + entityName + '</h2>'
      + '<p class="font-label-caps text-on-surface-variant mt-1">' + (summary.contract_count || 0) + ' contracts tracked</p>'
      + '</div></div>'
      + '<div class="mb-8"><h4 class="font-label-caps text-on-surface-variant mb-3 flex items-center gap-1">'
      + '<span class="material-symbols-outlined text-[16px]">bar_chart</span> Spending Overview</h4>'
      + '<div class="space-y-3">'
      + '<div class="flex justify-between items-center"><span class="font-body-md text-sm text-on-surface-variant">Total Tracked Spend</span><span class="font-data-mono text-sm text-on-surface font-bold">' + formatKES(summary.total_spend || 0) + '</span></div>'
      + '<div class="flex justify-between items-center"><span class="font-body-md text-sm text-on-surface-variant">Distinct Suppliers</span><span class="font-data-mono text-sm text-on-surface font-bold">' + (summary.supplier_count || 0) + '</span></div>'
      + '</div></div>'
      + buildRedFlagsHtml()
      + buildNlpHtml()
      + buildSuppliersHtml()
      + buildContractsHtml()
      + '</div>';

  } catch (error) {
    console.error('Failed to load entity detail:', error);
    panel.innerHTML = '<div class="p-6 text-extreme-crimson">Error loading ' + entityName + ' detail.</div>';
  }
}

// ===== Fetch Contracts (contract.html) =====
let contractCurrentPage = 1;
const contractPageSize = 50;
let contractCurrentSearch = '';

async function fetchContracts(search = '', page = 1) {
    const contractsList = document.getElementById('contracts-list');
    if (!contractsList) return;
    
    contractCurrentSearch = search;
    contractCurrentPage = page;
    const offset = (page - 1) * contractPageSize;
    
    contractsList.innerHTML = `<div class="p-4 text-center"><span class="material-symbols-outlined animate-spin">sync</span> Loading...</div>`;
    
    try {
        let url = `${API_BASE}/api/contracts?limit=${contractPageSize}&offset=${offset}`;
        if (search) url += `&search=${encodeURIComponent(search)}`;
        
        const response = await fetch(url);
        if (!response.ok) throw new Error('Network error');
        const data = await response.json();
        
        const contractsArr = data.contracts || [];
        const totalCount = data.total || 0;
        
        contractsList.innerHTML = '';
        
        if(contractsArr.length === 0) {
            contractsList.innerHTML = '<div class="p-4 text-on-surface-variant">No contracts found.</div>';
            updateContractPagination(0, 1);
            return;
        }
        
        contractsArr.forEach((c, idx) => {
            const div = document.createElement('div');
            const isFirst = idx === 0;
            div.className = `p-4 border-b border-outline-variant/30 cursor-pointer transition-colors border-l-4 ${isFirst ? 'bg-surface-container border-l-primary' : 'hover:bg-surface-container-low border-l-transparent'}`;
            
            div.innerHTML = `
                <div class="font-label-caps text-[10px] text-on-surface-variant mb-1 truncate">${c.procuring_entity}</div>
                <div class="font-body-md text-sm font-bold text-on-surface line-clamp-2" title="${c.tender_title || ''}">${c.tender_title || 'Untitled'}</div>
                <div class="flex justify-between items-end mt-2">
                    <div class="font-data-mono text-[11px] text-on-surface-variant truncate pr-2">${c.supplier_name || 'No Supplier'}</div>
                    <div class="font-data-mono text-[12px] text-primary font-bold whitespace-nowrap">${formatKES(c.award_value)}</div>
                </div>
            `;
            
            div.addEventListener('click', () => {
                // Highlight
                contractsList.querySelectorAll('div.cursor-pointer').forEach(el => {
                    el.classList.remove('bg-surface-container', 'border-l-primary');
                    el.classList.add('hover:bg-surface-container-low', 'border-l-transparent');
                });
                div.classList.add('bg-surface-container', 'border-l-primary');
                div.classList.remove('hover:bg-surface-container-low', 'border-l-transparent');
                
                renderContractDetail(c);
            });
            
            contractsList.appendChild(div);
        });
        
        if (contractsArr.length > 0) {
            renderContractDetail(contractsArr[0]);
        }
        
        updateContractPagination(totalCount, page);
        
    } catch (e) {
        console.error('Failed to load contracts:', e);
        contractsList.innerHTML = `<div class="p-4 text-extreme-crimson">Failed to load contracts. Is the backend running at ${API_BASE}?</div>`;
    }
}

function updateContractPagination(totalCount, page) {
    const infoEl = document.getElementById('contract-pagination-info');
    const ctrlEl = document.getElementById('contract-pagination-controls');
    
    const totalPages = Math.ceil(totalCount / contractPageSize) || 1;
    const start = totalCount > 0 ? (page - 1) * contractPageSize + 1 : 0;
    const end = Math.min(page * contractPageSize, totalCount);
    
    if (infoEl) {
        infoEl.textContent = `Showing ${start}-${end} of ${totalCount.toLocaleString()} contracts`;
    }
    
    if (ctrlEl) {
        let html = '';
        html += `<button onclick="window.goToContractPage(${page - 1})" class="w-8 h-8 flex items-center justify-center border border-outline-variant rounded text-on-surface-variant hover:bg-surface-container-low disabled:opacity-50" ${page === 1 ? 'disabled' : ''}><span class="material-symbols-outlined text-[18px]">chevron_left</span></button>`;
        
        for (let i = 1; i <= totalPages; i++) {
            if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
                if (i === page) {
                    html += `<button class="w-8 h-8 flex items-center justify-center border border-primary bg-primary text-on-primary rounded font-data-mono text-[14px]">${i}</button>`;
                } else {
                    html += `<button onclick="window.goToContractPage(${i})" class="w-8 h-8 flex items-center justify-center border border-outline-variant rounded text-on-surface-variant hover:bg-surface-container-low font-data-mono text-[14px]">${i}</button>`;
                }
            } else if (i === page - 2 || i === page + 2) {
                html += `<span class="w-8 h-8 flex items-center justify-center text-on-surface-variant">...</span>`;
            }
        }
        
        html += `<button onclick="window.goToContractPage(${page + 1})" class="w-8 h-8 flex items-center justify-center border border-outline-variant rounded text-on-surface-variant hover:bg-surface-container-low disabled:opacity-50" ${page === totalPages ? 'disabled' : ''}><span class="material-symbols-outlined text-[18px]">chevron_right</span></button>`;
        ctrlEl.innerHTML = html;
    }
}

window.goToContractPage = function(page) {
    if (page < 1) return;
    fetchContracts(contractCurrentSearch, page);
};

function renderContractDetail(c) {
    const detailPanel = document.getElementById('contract-detail');
    if(!detailPanel) return;
    
    // Check if there's a red flag for this ocid or supplier (naive check using API would be better, but we can just assume based on award size or something for now, or fetch the red flags array. We'll leave it as a general UI for now)
    
    detailPanel.innerHTML = `
        <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-8">
            <div class="flex items-center gap-2 text-on-surface-variant">
                <span class="material-symbols-outlined text-sm">tag</span>
                <span class="font-data-mono text-sm">${c.ocid || c.tender_id || 'Unknown ID'}</span>
            </div>
            <div class="flex gap-3">
                <button class="flex items-center gap-2 px-3 py-1.5 border border-outline-variant rounded font-label-caps text-label-caps text-on-surface hover:bg-surface-container-low transition-colors">
                    <span class="material-symbols-outlined text-[18px]">bookmark_border</span> Bookmark
                </button>
            </div>
        </div>
        
        <h1 class="font-display-lg text-display-lg text-on-surface mb-6">${c.tender_title || 'Untitled Procurement'}</h1>
        
        <section class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <div class="bg-surface-container-lowest border border-outline-variant p-5 rounded flex flex-col gap-1">
                <span class="font-label-caps text-label-caps text-on-surface-variant">Contract Value</span>
                <span class="font-headline-lg text-headline-lg text-savannah-green">${formatKES(c.award_value)}</span>
            </div>
            <div class="bg-surface-container-lowest border border-outline-variant p-5 rounded flex flex-col gap-1">
                <span class="font-label-caps text-label-caps text-on-surface-variant">Procurement Method</span>
                <span class="font-body-lg text-body-lg text-on-surface font-semibold capitalize">${c.procurement_method || 'Unknown'}</span>
            </div>
            <div class="bg-surface-container-lowest border border-outline-variant p-5 rounded flex flex-col gap-1">
                <span class="font-label-caps text-label-caps text-on-surface-variant">Award Date</span>
                <span class="font-data-mono text-data-mono text-on-surface text-lg">${formatDate(c.award_date)}</span>
            </div>
            <div class="bg-surface-container-lowest border border-outline-variant p-5 rounded flex flex-col gap-1">
                <span class="font-label-caps text-label-caps text-on-surface-variant">Status</span>
                <span class="font-body-lg text-body-lg text-on-surface font-semibold capitalize">${c.tender_status || 'Complete'}</span>
            </div>
        </section>
        
        <section class="grid md:grid-cols-2 gap-gutter mb-8">
            <div class="bg-surface-container-lowest border border-outline-variant p-6 rounded shadow-sm">
                <span class="font-label-caps text-label-caps text-on-surface-variant mb-4 block">Purchasing Entity</span>
                <h2 class="font-headline-md text-headline-md text-on-surface mb-2">${c.procuring_entity}</h2>
                <div class="space-y-3 font-data-mono text-data-mono text-sm border-t border-outline-variant/50 pt-4 mt-6">
                    <div class="flex justify-between">
                        <span class="text-on-surface-variant">Fiscal Year:</span>
                        <span class="text-on-surface">${c.fiscal_year || '-'}</span>
                    </div>
                </div>
            </div>
            <div class="bg-surface-container-lowest border border-outline-variant p-6 rounded shadow-sm">
                <span class="font-label-caps text-label-caps text-on-surface-variant mb-4 block">Winning Supplier</span>
                <h2 class="font-headline-md text-headline-md text-on-surface mb-2">${c.supplier_name || '-'}</h2>
                <div class="space-y-3 font-data-mono text-data-mono text-sm border-t border-outline-variant/50 pt-4 mt-6">
                    <div class="flex justify-between">
                        <span class="text-on-surface-variant">Award Value:</span>
                        <span class="text-primary font-bold">${formatKES(c.award_value)}</span>
                    </div>
                </div>
            </div>
        </section>
    `;
}

function setupContractSearch() {
    const searchInput = document.getElementById('contract-search');
    if(searchInput) {
        let timeout = null;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                fetchContracts(e.target.value);
            }, 300);
        });
    }
}


// ===== Reports page (reports.html) =====
async function fetchReports() {
  const oagContainer = document.getElementById('oag-reports-list');
  const cobContainer = document.getElementById('cob-reports-list');
  const treasuryContainer = document.getElementById('treasury-reports-list');

  if (oagContainer) {
    try {
      const res = await fetch(`${API_BASE}/api/reports/oag`);
      const reports = await res.json();
      oagContainer.innerHTML = reports.map(r => `
        <div class="p-4 border-b border-outline-variant/30 hover:bg-surface-container-low transition-colors bg-white rounded mb-2 shadow-sm">
          <div class="font-body-md text-on-surface font-bold">${r.title}</div>
          <div class="flex justify-between items-center mt-2">
            <span class="font-data-mono text-[12px] text-on-surface-variant">${r.fiscal_year} · ${r.report_type}</span>
            ${r.file_url ? `<a href="${r.file_url}" target="_blank" class="text-primary hover:underline font-data-mono text-[12px] flex items-center gap-1"><span class="material-symbols-outlined text-[14px]">download</span> PDF</a>` : ''}
          </div>
        </div>
      `).join('');
    } catch (e) {
      oagContainer.innerHTML = '<div class="p-4 text-extreme-crimson">Failed to load OAG reports</div>';
    }
  }

  if (cobContainer) {
    try {
      const res = await fetch(`${API_BASE}/api/reports/cob`);
      const reports = await res.json();
      cobContainer.innerHTML = reports.map(r => `
        <div class="p-4 border-b border-outline-variant/30 hover:bg-surface-container-low transition-colors bg-white rounded mb-2 shadow-sm">
          <div class="font-body-md text-on-surface font-bold">${r.title}</div>
          <div class="flex justify-between items-center mt-2">
            <span class="font-data-mono text-[12px] text-on-surface-variant">${r.fiscal_year} · ${r.quarter} · ${r.government_level}</span>
            ${r.file_url ? `<a href="${r.file_url}" target="_blank" class="text-primary hover:underline font-data-mono text-[12px] flex items-center gap-1"><span class="material-symbols-outlined text-[14px]">download</span> PDF</a>` : ''}
          </div>
        </div>
      `).join('');
    } catch (e) {
      cobContainer.innerHTML = '<div class="p-4 text-extreme-crimson">Failed to load CoB reports</div>';
    }
  }

  if (treasuryContainer) {
    try {
      const res = await fetch(`${API_BASE}/api/reports/treasury`);
      const reports = await res.json();
      treasuryContainer.innerHTML = reports.map(r => `
        <div class="p-4 border-b border-outline-variant/30 hover:bg-surface-container-low transition-colors bg-white rounded mb-2 shadow-sm">
          <div class="font-body-md text-on-surface font-bold">${r.title}</div>
          <div class="flex justify-between items-center mt-2">
            <span class="font-data-mono text-[12px] text-on-surface-variant">${r.fiscal_year} · ${r.doc_type}</span>
            ${r.file_url ? `<a href="${r.file_url}" target="_blank" class="text-primary hover:underline font-data-mono text-[12px] flex items-center gap-1"><span class="material-symbols-outlined text-[14px]">download</span> PDF</a>` : ''}
          </div>
        </div>
      `).join('');
    } catch (e) {
      treasuryContainer.innerHTML = '<div class="p-4 text-extreme-crimson">Failed to load Treasury docs</div>';
    }
  }
}


// ===== Global Search =====
function setupGlobalSearch() {
  const searchInput = document.getElementById('global-search-input');
  const searchBtn = document.getElementById('global-search-btn');
  
  if (searchInput && searchBtn) {
    const performSearch = () => {
      const query = searchInput.value.trim();
      if (query) {
        // Check if query is a known county name
        const matchedCounty = KENYA_COUNTIES.find(c => c.toLowerCase() === query.toLowerCase());
        if (matchedCounty) {
          // Redirect to suppliers (red flags) with the county filter
          window.location.href = `./red-flags.html?search=${encodeURIComponent(matchedCounty)}`;
        } else {
          // Otherwise redirect to general contract search
          window.location.href = `./contract.html?search=${encodeURIComponent(query)}`;
        }
      }
    };
    
    searchBtn.addEventListener('click', performSearch);
    searchInput.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        performSearch();
      }
    });
  }
}

// ===== Interactive Kenya County Map =====
const COUNTY_ID_TO_NAME = {};

function renderKenyaMap() {
    const container = document.getElementById('kenya-map-container');
    if (!container || !kenyaMap) return;

    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', kenyaMap.viewBox);
    svg.setAttribute('aria-label', kenyaMap.label || 'Map of Kenya');
    svg.style.width = '100%';
    svg.style.height = '100%';
    svg.style.maxHeight = '480px';

    kenyaMap.locations.forEach(loc => {
        const path = document.createElementNS(svgNS, 'path');
        path.setAttribute('d', loc.path);
        path.setAttribute('id', `county-${loc.id}`);
        path.setAttribute('data-name', loc.name);
        path.style.fill = '#E8E6E3';
        path.style.stroke = '#1A3E35';
        path.style.strokeWidth = '0.5';
        path.style.cursor = 'pointer';
        path.style.transition = 'fill 0.2s ease, transform 0.15s ease';

        COUNTY_ID_TO_NAME[loc.id] = loc.name;

        // Hover
        path.addEventListener('mouseenter', (e) => {
            path.style.fill = '#C59341';
            path.style.filter = 'drop-shadow(0 2px 4px rgba(0,0,0,0.15))';
            showMapTooltip(e, loc);
        });
        path.addEventListener('mousemove', (e) => {
            moveMapTooltip(e);
        });
        path.addEventListener('mouseleave', () => {
            // Revert to data-driven color or default
            path.style.fill = path.dataset.riskColor || '#E8E6E3';
            path.style.filter = 'none';
            // DO NOT hide the tooltip so the user can read it
            // hideMapTooltip();
        });

        // Click — navigate to red flags filtered by county name
        path.addEventListener('click', () => {
            window.location.href = `./red-flags.html?search=${encodeURIComponent(loc.name)}`;
        });

        svg.appendChild(path);
    });

    container.innerHTML = '';
    container.appendChild(svg);
}

let mapTooltipTimeout = null;

function showMapTooltip(e, loc) {
    const tooltip = document.getElementById('map-tooltip');
    if (!tooltip) return;

    if (mapTooltipTimeout) {
        clearTimeout(mapTooltipTimeout);
        mapTooltipTimeout = null;
    }

    const countyEl = document.getElementById('tooltip-county');
    const spendEl = document.getElementById('tooltip-spend');
    const flagsEl = document.getElementById('tooltip-flags');

    if (countyEl) countyEl.textContent = loc.name;
    if (spendEl) spendEl.textContent = loc._spend || '—';
    if (flagsEl) flagsEl.textContent = loc._flags ?? '—';

    tooltip.style.display = 'block';
    requestAnimationFrame(() => { tooltip.style.opacity = '1'; });
    moveMapTooltip(e);
}

function moveMapTooltip(e) {
    const tooltip = document.getElementById('map-tooltip');
    if (!tooltip) return;

    const mapContainer = document.getElementById('kenya-map-container')?.closest('div.relative');
    if (!mapContainer) return;

    const rect = mapContainer.getBoundingClientRect();
    let x = e.clientX - rect.left + 16;
    let y = e.clientY - rect.top - 10;

    // Prevent overflow
    if (x + 230 > rect.width) x = e.clientX - rect.left - 240;
    if (y + 120 > rect.height) y = e.clientY - rect.top - 120;
    if (x < 0) x = 8;
    if (y < 0) y = 8;

    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
}

function hideMapTooltip() {
    const tooltip = document.getElementById('map-tooltip');
    if (tooltip) {
        tooltip.style.opacity = '0';
        if (mapTooltipTimeout) {
            clearTimeout(mapTooltipTimeout);
        }
        mapTooltipTimeout = setTimeout(() => { tooltip.style.display = 'none'; }, 150);
    }
}

function normalizeCountyName(name) {
    return (name || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

function colorizeMap(entities, redFlags) {
    if (!kenyaMap) return;

    // Build entity spend map
    const entitySpendMap = {};
    (entities || []).forEach(e => {
        const norm = normalizeCountyName(e.entity);
        KENYA_COUNTIES.forEach(county => {
            if (norm.includes(normalizeCountyName(county))) {
                if (!entitySpendMap[normalizeCountyName(county)]) {
                    entitySpendMap[normalizeCountyName(county)] = { spend: 0, flags: 0 };
                }
                entitySpendMap[normalizeCountyName(county)].spend += (e.total_spend || 0);
            }
        });
    });

    // Build flag count map
    (redFlags || []).forEach(f => {
        const entityNorm = normalizeCountyName(f.entity);
        KENYA_COUNTIES.forEach(county => {
            const countyNorm = normalizeCountyName(county);
            if (entityNorm.includes(countyNorm)) {
                if (!entitySpendMap[countyNorm]) {
                    entitySpendMap[countyNorm] = { spend: 0, flags: 0 };
                }
                entitySpendMap[countyNorm].flags += 1;
            }
        });
    });

    // Apply colors to SVG paths
    kenyaMap.locations.forEach(loc => {
        const path = document.getElementById(`county-${loc.id}`);
        if (!path) return;

        const countyNorm = normalizeCountyName(loc.name);
        const data = entitySpendMap[countyNorm] || { spend: 0, flags: 0 };

        // Store spend/flag data on the location object for tooltip
        loc._spend = data.spend > 0 ? formatKES(data.spend) : '—';
        loc._flags = data.flags;

        // Determine risk color
        let riskColor = '#C4EBE0'; // safe green
        if (data.flags >= 16) {
            riskColor = '#D44040'; // crimson
        } else if (data.flags >= 6) {
            riskColor = '#FFB366'; // orange
        } else if (data.flags >= 1) {
            riskColor = '#FFDDAF'; // gold
        }

        path.style.fill = riskColor;
        path.dataset.riskColor = riskColor;
    });
}

async function initMapData() {
    if (!document.getElementById('kenya-map-container')) return;
    try {
        const [entitiesRes, flagsRes] = await Promise.all([
            fetch(`${API_BASE}/api/entities`),
            fetch(`${API_BASE}/api/red-flags?limit=1000`)
        ]);
        const entities = await entitiesRes.json();
        const flags = await flagsRes.json();
        colorizeMap(entities, flags);
    } catch (e) {
        console.warn('Could not load map data:', e);
    }
}

// ===== Init =====
document.addEventListener('DOMContentLoaded', () => {
  setupAuth();
  setupDarkMode();
  setupNavigation();
  fetchStats();
  setupGlobalSearch();
  renderKenyaMap();
  initMapData();
  
  fetchRedFlags();
  setupRedFlagFilters();
  
  fetchEntities();
  
  // If we are on contract.html and there is a search param, pre-populate and search
  const urlParams = new URLSearchParams(window.location.search);
  const searchQuery = urlParams.get('search') || '';
  
  if (window.location.pathname.includes('contract.html') && searchQuery) {
    const searchInput = document.getElementById('contract-search');
    if (searchInput) {
      searchInput.value = searchQuery;
    }
    fetchContracts(searchQuery);
  } else {
    fetchContracts();
  }
  setupContractSearch();
  
  fetchReports();
});
