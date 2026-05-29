/**
 * FundRadar — Frontend Logic
 * 三层架构交互、实时刷新、ECharts图表
 */

// ── State ──
let watchlist = JSON.parse(localStorage.getItem("fundradar_watchlist") || "[]");
let currentFundCode = null;
let currentNavDays = 365;
let navChartInstance = null;
let klineChartInstance = null;
let autoRefreshTimer = null;
let newsRefreshTimer = null;
let sectorRefreshTimer = null;

// ── API helper with timeout ──
function apiFetch(url, timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { signal: controller.signal }).finally(() => clearTimeout(timer));
}

// ── Init ──
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  loadMarketIndex();
  loadSectors();
  renderWatchlist();
  initAutoRefresh();
  initNewsAutoRefresh();
  initNavRangeSelector();
});

// ── Tabs ──
function initTabs() {
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      const panelId = tab.dataset.tab;
      document.getElementById(panelId).classList.add("active");

      if (panelId === "tab-news") {
        loadAIAnalysis();
        loadNews();
        initNewsAutoRefresh();
      } else {
        clearNewsAutoRefresh();
      }

      // 延迟resize图表
      setTimeout(() => {
        if (navChartInstance) navChartInstance.resize();
        if (klineChartInstance) klineChartInstance.resize();
      }, 200);
    });
  });
}

// ── Auto Refresh ──
function initAutoRefresh() {
  if (autoRefreshTimer) clearInterval(autoRefreshTimer);
  autoRefreshTimer = setInterval(() => {
    loadMarketIndex();
    loadSectors();
    updateTimeDisplay();
  }, 120000); // 2分钟刷新，避免API限流
}

function initNewsAutoRefresh() {
  if (newsRefreshTimer) clearInterval(newsRefreshTimer);
  newsRefreshTimer = setInterval(() => {
    if (document.getElementById("tab-news").classList.contains("active")) {
      loadNews();
    }
  }, 60000); // 60秒刷新新闻
}

function clearNewsAutoRefresh() {
  if (newsRefreshTimer) { clearInterval(newsRefreshTimer); newsRefreshTimer = null; }
}

function updateTimeDisplay() {
  const now = new Date();
  document.getElementById("updateTime").textContent =
    now.toLocaleString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ── Market Index ──
async function loadMarketIndex() {
  try {
    const resp = await apiFetch("/api/market/index", 15000);
    const data = await resp.json();
    if (data.success) {
      renderMarketIndex(data.index);
      renderMarketTicker(data.index);
    }
    updateTimeDisplay();
  } catch (e) {
    console.error("Load market index error:", e);
    if (e.name === "AbortError") {
      document.getElementById("marketIndex").innerHTML =
        '<div class="ticker-item"><span class="name">数据加载超时</span></div>';
    }
  }
}

function renderMarketIndex(index) {
  const container = document.getElementById("marketIndex");
  if (!index || Object.keys(index).length === 0) {
    container.innerHTML = '<div class="ticker-item"><span class="name">数据暂不可用，请刷新</span></div>';
    return;
  }
  let html = "";
  for (const [name, info] of Object.entries(index)) {
    const cls = info.change_pct >= 0 ? "up" : "down";
    const sign = info.change_pct >= 0 ? "+" : "";
    html += `<div class="ticker-item">
      <span class="name">${name}</span>
      <span class="price">${info.price.toFixed(0)}</span>
      <span class="change ${cls}">${sign}${info.change_pct.toFixed(2)}%</span>
    </div>`;
  }
  container.innerHTML = html;
}

function renderMarketTicker(index) {
  const container = document.getElementById("marketTicker");
  if (!index || Object.keys(index).length === 0) return;
  let html = "";
  for (const [name, info] of Object.entries(index)) {
    const cls = info.change_pct >= 0 ? "up" : "down";
    const sign = info.change_pct >= 0 ? "+" : "";
    html += `<div class="ticker-item">
      <span class="name">${name}</span>
      <span class="price">${info.price.toFixed(0)}</span>
      <span class="change ${cls}">${sign}${info.change_pct.toFixed(2)}%</span>
    </div>`;
  }
  container.innerHTML = html;
}

// ── Sector Boards ──
let sectorDataAll = [];
let sectorExpanded = false;
let lastSectorSearch = "";
const SECTOR_PREVIEW_COUNT = 10;

// Concept alias map: common search terms → standard concept names
const CONCEPT_ALIASES = {
  "ai": "人工智能", "gpt": "ChatGPT概念", "vr": "虚拟现实", "ar": "增强现实",
  "新能源车": "新能源车概念", "芯片": "半导体", "国产芯片": "半导体",
  "芯片概念": "半导体概念", "半导体芯片": "半导体", "储能": "储能概念",
  "光伏": "光伏概念", "锂电池": "锂电池", "风电": "风能", "机器人": "机器人概念",
  "军工": "军工", "医药": "生物医药", "银行": "银行", "地产": "房地产开发",
  "煤炭": "煤炭开采", "电力": "电力行业", "白酒": "白酒", "券商": "券商概念",
  "5g": "5G概念", "6g": "6G概念", "自动驾驶": "无人驾驶",
  "量子": "量子通信", "量子计算": "量子通信", "卫星": "北斗导航",
  "航天": "航天航空", "船舶": "船舶制造", "数据": "数据要素",
  "大数据": "大数据", "算力": "算力概念", "云计算": "云计算",
  "光刻": "光刻机", "工业母机": "工业母机", "信创": "信创",
  "鸿蒙": "鸿蒙概念", "华为": "华为概念", "小米": "小米概念",
  "特斯拉": "特斯拉", "消费": "消费电子", "汽车": "汽车整车",
  "低空": "低空经济", "人形": "人形机器人", "aigc": "AIGC概念",
};

async function loadSectors() {
  const container = document.getElementById("sectorGrid");
  const firstLoad = !sectorDataAll.length;
  if (firstLoad) {
    container.innerHTML = '<div class="loading"><div class="spinner"></div><div>加载行业板块...</div></div>';
  }

  try {
    const resp = await apiFetch("/api/sectors?type=all", 15000);
    const data = await resp.json();
    if (data.success) {
      sectorDataAll = data.sectors || [];
      applySectorFilter();
      document.getElementById("sectorUpdateTime").textContent =
        `更新 ${data.update_time || ""}`;
      document.getElementById("sectorSubtitle").textContent =
        `共${sectorDataAll.length}个基金板块（传统+科技），点击可搜索对应基金`;
    }
  } catch (e) {
    if (!sectorDataAll.length) {
      container.innerHTML = '<div class="empty-state"><div>加载失败，请稍后重试</div></div>';
    }
    console.error("Load sectors error:", e);
  }
}

function filterSectors() {
  applySectorFilter();
}

function applySectorFilter() {
  const keyword = (document.getElementById("sectorSearch") || {}).value || "";
  let filtered = sectorDataAll;
  // 仅在搜索关键词变化时自动展开，不覆盖手动展开/收起
  if (keyword.trim() !== lastSectorSearch) {
    lastSectorSearch = keyword.trim();
    if (keyword.trim()) sectorExpanded = true;
  }
  if (keyword.trim()) {
    const kw = keyword.trim().toLowerCase();
    // 检查别名映射
    const mapped = CONCEPT_ALIASES[kw] || kw;
    filtered = sectorDataAll.filter(s => {
      const name = s.name.toLowerCase();
      return name.includes(kw) || name.includes(mapped.toLowerCase());
    });
    // 有数据的排前面
    filtered.sort((a, b) => {
      if (a.has_data !== false && b.has_data === false) return -1;
      if (a.has_data === false && b.has_data !== false) return 1;
      if (a.has_data === false && b.has_data === false) return a.name.localeCompare(b.name);
      // 有数据的按基金数量降序
      const cntA = a.fund_count || a.stock_count || 0;
      const cntB = b.fund_count || b.stock_count || 0;
      if (cntB !== cntA) return cntB - cntA;
      return a.name.localeCompare(b.name);
    });
  }
  renderSectors(filtered);
}

function toggleSectorCollapse() {
  sectorExpanded = !sectorExpanded;
  applySectorFilter();
}

function renderSectors(sectors) {
  const container = document.getElementById("sectorGrid");
  const btn = document.getElementById("sectorCollapseBtn");
  const searchKw = (document.getElementById("sectorSearch") || {}).value || "";

  if (!sectors || sectors.length === 0) {
    container.innerHTML = '<div class="empty-state"><div>暂无匹配板块</div></div>';
    if (btn) btn.style.display = "none";
    return;
  }

  const total = sectors.length;
  const showAll = sectorExpanded || searchKw || total <= SECTOR_PREVIEW_COUNT;
  const display = showAll ? sectors : sectors.slice(0, SECTOR_PREVIEW_COUNT);

  let html = "";
  display.forEach(s => {
    const count = s.fund_count || s.stock_count || 0;
    const hasFunds = count > 0;
    const groupLabel = s.group || "";
    const groupCls = groupLabel === "科技板块" ? "group-tech" : "group-trad";

    if (!hasFunds) {
      html += `<div class="sector-card sector-no-data" style="cursor:default;">
        <div class="sector-header">
          <div>
            <div class="sector-name">${s.name}</div>
            <div class="sector-meta"><span class="sector-group-tag ${groupCls}">${groupLabel}</span> · 暂无基金数据</div>
          </div>
        </div>
      </div>`;
    } else {
      const chg = s.change_pct || 0;
      const hasQuote = s.has_quote === true;
      const chgSign = chg >= 0 ? "+" : "";
      const chgCls = chg > 0 ? "up" : (chg < 0 ? "down" : "flat");
      html += `<div class="sector-card fund-sector-card" onclick="searchFundBySector('${s.name.replace(/'/g, "\\'")}', ${count})">
        <div class="sector-header">
          <div>
            <div class="sector-name">${s.name}<span style="font-size:12px;color:var(--ios-text-secondary);margin-left:4px;">${count}只</span></div>
            <div class="sector-meta"><span class="sector-group-tag ${groupCls}">${groupLabel}</span></div>
          </div>
          <div class="sector-right">
            ${hasQuote ? `<div class="sector-change ${chgCls}"><div class="sector-change-val">${chgSign}${chg.toFixed(2)}%</div></div>` : `<div class="sector-change flat"><div class="sector-change-val">--</div></div>`}
          </div>
        </div>
      </div>`;
    }
  });
  container.innerHTML = html;

  // 折叠按钮
  if (btn) {
    if (!searchKw && total > SECTOR_PREVIEW_COUNT) {
      btn.style.display = "";
      const hidden = total - SECTOR_PREVIEW_COUNT;
      btn.textContent = sectorExpanded ? "收起" : `展开全部（还有${hidden}个板块）`;
    } else if (searchKw && total > 0) {
      btn.style.display = "";
      btn.textContent = `搜索结果：${total}个板块`;
      btn.style.pointerEvents = "none";
      btn.style.color = "var(--ios-text-secondary)";
      btn.style.borderColor = "var(--ios-gray4)";
    } else {
      btn.style.display = "none";
    }
    if (!searchKw && btn.style.pointerEvents === "none") {
      btn.style.pointerEvents = "";
      btn.style.color = "";
      btn.style.borderColor = "";
    }
  }
}

async function searchFundBySector(name, expectedCount) {
  document.getElementById("fundSearch").value = name;
  const container = document.getElementById("searchResults");
  container.innerHTML = '<div class="loading"><div class="spinner"></div><div>加载中...</div></div>';

  try {
    const resp = await apiFetch(`/api/sector/${encodeURIComponent(name)}/funds`, 15000);
    const data = await resp.json();
    if (data.success && data.funds.length > 0) {
      let html = `<div style="font-size:12px;color:var(--ios-text-secondary);margin-bottom:8px;">${name}板块：共 ${data.count} 只基金</div>`;
      data.funds.forEach(f => {
        const code = String(f.code || "").zfill(6);
        const isStarred = watchlist.includes(code);
        html += `<div class="fund-item" onclick="viewFundDetail('${code}')">
          <div class="fund-info">
            <div class="fund-name">${f.name || code}</div>
            <div class="fund-code">${code} · ${f.fund_type || ""}</div>
          </div>
          <div class="actions">
            <button class="btn-star${isStarred ? ' starred' : ''}"
                    data-code="${code}"
                    onclick="event.stopPropagation();toggleWatchlist('${code}','${(f.name || "").replace(/'/g, "\\'")}')">
              ${isStarred ? '★' : '☆'}
            </button>
          </div>
        </div>`;
      });
      container.innerHTML = html;
    } else {
      container.innerHTML = `<div class="empty-state"><div>${name}板块暂无匹配基金</div></div>`;
    }
  } catch (e) {
    container.innerHTML = '<div class="empty-state"><div>加载失败</div></div>';
  }

  // 滚动到搜索区域
  document.getElementById("fundSearch").scrollIntoView({ behavior: "smooth" });
}

// ── Fund Search ──
async function searchFunds() {
  const keyword = document.getElementById("fundSearch").value.trim();
  if (keyword.length < 2) { alert("请输入至少2位代码或关键词"); return; }

  const container = document.getElementById("searchResults");
  container.innerHTML = '<div class="loading"><div class="spinner"></div></div>';

  try {
    const resp = await apiFetch(`/api/funds/search?q=${encodeURIComponent(keyword)}`, 15000);
    const data = await resp.json();
    if (data.success && data.funds.length > 0) {
      let html = `<div style="font-size:12px;color:var(--ios-text-secondary);margin-bottom:8px;">找到 ${data.count} 只基金</div>`;
      data.funds.forEach(f => {
        const code = String(f.code || "").zfill(6);
        const isStarred = watchlist.includes(code);
        html += `<div class="fund-item" onclick="viewFundDetail('${code}')">
          <div class="fund-info">
            <div class="fund-name">${f.name || code}</div>
            <div class="fund-code">${code} · ${f.fund_type || ""} · ${f.company || ""}</div>
          </div>
          <div class="actions">
            <button class="btn-star${isStarred ? ' starred' : ''}"
                    data-code="${code}"
                    onclick="event.stopPropagation();toggleWatchlist('${code}','${(f.name || "").replace(/'/g, "\\'")}')">
              ${isStarred ? '★' : '☆'}
            </button>
          </div>
        </div>`;
      });
      container.innerHTML = html;
    } else {
      container.innerHTML = '<div class="empty-state"><div>未找到匹配的基金</div></div>';
    }
  } catch (e) {
    container.innerHTML = '<div class="empty-state"><div>搜索出错</div></div>';
  }
}

// 回车键搜索
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && document.activeElement === document.getElementById("fundSearch")) {
    searchFunds();
  }
  if (e.key === "Enter" && document.activeElement === document.getElementById("fundCodeInput")) {
    loadFundDetail();
  }
});

// ── Watchlist ──
function toggleWatchlist(code, name) {
  const idx = watchlist.indexOf(code);
  if (idx >= 0) {
    watchlist.splice(idx, 1);
  } else {
    if (watchlist.length >= 20) { alert("自选基金最多20只"); return; }
    watchlist.push(code);
  }
  localStorage.setItem("fundradar_watchlist", JSON.stringify(watchlist));
  renderWatchlist();
  // 定点更新星标按钮，不重新加载整个列表
  updateStarButtons(code);
}

function updateStarButtons(code) {
  const isStarred = watchlist.includes(code);
  const buttons = document.querySelectorAll(`[data-code="${code}"]`);
  buttons.forEach(btn => {
    if (isStarred) {
      btn.classList.add("starred");
      btn.innerHTML = "★";
      btn.title = "取消自选";
    } else {
      btn.classList.remove("starred");
      btn.innerHTML = "☆";
      btn.title = "加入自选";
    }
  });
}

function renderWatchlist() {
  const container = document.getElementById("watchlistItems");
  document.getElementById("watchlistCount").textContent = `${watchlist.length}只`;

  if (watchlist.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="icon">☆</div><div>暂无自选基金，点击基金右侧星标添加</div></div>';
    return;
  }

  container.innerHTML = '<div class="loading"><div class="spinner"></div><div>加载自选基金...</div></div>';

  // 获取自选基金基本信息
  const codes = watchlist.join(",");
  return apiFetch(`/api/watchlist/analyze?codes=${encodeURIComponent(codes)}`, 20000)
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        let html = "";
        data.results.forEach(r => {
          const phaseClassMap = {
            "建仓期": "accumulation", "拉升期": "markup",
            "派发期": "distribution", "震荡期": "shakeout",
            "震荡观察期": "shakeout",
          };
          const phaseClass = phaseClassMap[r.phase] || "shakeout";
          html += `<div class="fund-item" onclick="viewFundDetail('${r.code}')">
            <div class="fund-info">
              <div class="fund-name">${r.name}</div>
              <div class="fund-code">${r.code} · ${r.fund_type || ""}</div>
              <div style="margin-top:4px;">
                <span class="phase-badge ${phaseClass}">${r.badge.icon} ${r.phase}</span>
                <span style="font-size:11px;color:var(--ios-text-secondary);margin-left:6px;">置信度: ${r.confidence}%</span>
              </div>
            </div>
            <div class="actions">
              <button class="btn-star starred"
                      data-code="${r.code}"
                      onclick="event.stopPropagation();toggleWatchlist('${r.code}','${(r.name || '').replace(/'/g, "\\'")}')">★</button>
            </div>
          </div>`;
        });
        container.innerHTML = html;
      } else {
        container.innerHTML = '<div class="empty-state"><div>加载失败，请重试</div></div>';
      }
    })
    .catch(() => {
      container.innerHTML = '<div class="empty-state"><div>加载失败</div></div>';
    });
}

async function analyzeWatchlist() {
  if (watchlist.length === 0) { alert("请先添加自选基金"); return; }
  const btn = document.getElementById("analyzeWatchlistBtn");
  btn.textContent = "分析中...";
  btn.classList.add("refreshing");
  await renderWatchlist();
  btn.textContent = "分析阶段";
  btn.classList.remove("refreshing");
}

// ── Fund Detail (Tab 2) ──
function viewFundDetail(code) {
  currentFundCode = code;
  document.getElementById("fundCodeInput").value = code;

  // 切换到分析tab
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  document.querySelector('[data-tab="tab-analysis"]').classList.add("active");
  document.getElementById("tab-analysis").classList.add("active");

  loadFundDetail();
}

async function loadFundDetail() {
  const code = currentFundCode || document.getElementById("fundCodeInput").value.trim();
  if (!code) { alert("请输入基金代码"); return; }
  currentFundCode = code;

  document.getElementById("analysisPlaceholder").style.display = "none";
  document.getElementById("analysisContent").style.display = "block";
  document.getElementById("backBtn").style.display = "";

  // Loading states
  document.getElementById("fundPhaseCard").innerHTML =
    '<div class="loading"><div class="spinner"></div><div>加载基金数据...</div></div>';
  document.getElementById("holdingsGrid").innerHTML =
    '<div class="loading"><div class="spinner"></div><div>加载重仓股数据...</div></div>';

  try {
    const resp = await apiFetch(`/api/fund/${code}/detail?days=${currentNavDays}`, 60000);
    const data = await resp.json();
    if (data.success) {
      renderFundPhase(data);
      renderNavChart(data.nav_data, currentNavDays, data.chart_max_drawdown);
      renderHoldings(data.holdings);
      document.getElementById("holdingsUpdate").textContent =
        `更新于 ${data.update_time || ""}`;
      // 异步加载AI持仓分析
      loadHoldingsAIAnalysis(code);
    } else {
      document.getElementById("fundPhaseCard").innerHTML =
        `<div class="empty-state"><div>${data.error || "未找到该基金"}</div></div>`;
      document.getElementById("holdingsGrid").innerHTML = "";
    }
  } catch (e) {
    const msg = e.name === "AbortError" ? "请求超时，请稍后重试" : "网络错误，请检查后端服务";
    document.getElementById("fundPhaseCard").innerHTML =
      `<div class="empty-state"><div>${msg}</div></div>`;
    console.error("Load fund detail error:", e);
  }
}

function closeFundDetail() {
  currentFundCode = null;
  currentNavDays = 365;
  document.getElementById("fundCodeInput").value = "";
  document.getElementById("analysisPlaceholder").style.display = "";
  document.getElementById("analysisContent").style.display = "none";
  document.getElementById("backBtn").style.display = "none";
  document.getElementById("fundPhaseCard").innerHTML = "";
  document.getElementById("holdingsGrid").innerHTML = "";
  // 隐藏AI持仓诊断
  document.getElementById("holdingsAICard").style.display = "none";
  document.getElementById("holdingsAIContent").innerHTML =
    '<div class="loading"><div class="spinner"></div><div>AI 正在分析重仓股数据...</div></div>';
  // 重置范围选择器
  document.querySelectorAll("#navRangeSelector .filter-pill").forEach(p => {
    p.classList.remove("active");
    if (parseInt(p.dataset.days) === 365) p.classList.add("active");
  });
  document.getElementById("navChartTitle").textContent = "净值走势（近1年）";
  // 关闭K线弹窗
  closeKline();
  // 销毁图表
  if (navChartInstance) { navChartInstance.dispose(); navChartInstance = null; }
  document.getElementById("navChart").innerHTML = "";
}

function renderFundPhase(data) {
  const info = data.fund_info || {};
  const phase = data.phase || {};
  const badge = data.badge || {};

  const phaseClassMap = {
    "建仓期": "accumulation", "拉升期": "markup",
    "派发期": "distribution", "震荡期": "shakeout",
    "震荡观察期": "shakeout",
  };
  const phaseClass = phaseClassMap[phase.phase] || "shakeout";

  let reasonsHtml = "";
  if (phase.reasons && phase.reasons.length > 0) {
    reasonsHtml = '<ul class="reasons">' + phase.reasons.map(r => `<li>${r}</li>`).join("") + '</ul>';
  }

  const ind = phase.indicators || {};

  document.getElementById("fundPhaseCard").innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;">
      <div>
        <div style="font-size:20px;font-weight:700;">${info.name || currentFundCode}</div>
        <div style="font-size:13px;color:var(--ios-text-secondary);margin-top:2px;">
          ${info.code || currentFundCode} · ${info.fund_type || ""} · ${info.manager || ""}
        </div>
        <div style="font-size:12px;color:var(--ios-text-secondary);">${info.company || ""} · 规模: ${info.scale || "-"}</div>
      </div>
      <div style="text-align:right;">
        <span class="phase-badge ${phaseClass}" style="font-size:15px;padding:8px 16px;">${badge.icon || ""} ${phase.phase || "分析中"}</span>
        <div style="font-size:12px;color:var(--ios-text-secondary);margin-top:4px;">置信度: ${phase.confidence || 0}%</div>
      </div>
    </div>
    <div class="phase-detail">
      ${reasonsHtml}
      <div style="font-size:13px;color:var(--ios-text-secondary);margin-top:8px;padding-top:8px;border-top:0.5px solid var(--ios-separator);">
        ${data.phase_description || ""}
      </div>
      <div style="display:flex;gap:16px;margin-top:8px;font-size:12px;color:var(--ios-text-secondary);flex-wrap:wrap;">
        ${ind.ret_20d != null ? `<span>近20日: ${ind.ret_20d > 0 ? '+' : ''}${ind.ret_20d}%</span>` : ""}
        ${ind.ret_60d != null ? `<span>近60日: ${ind.ret_60d > 0 ? '+' : ''}${ind.ret_60d}%</span>` : ""}
        ${ind.vol_annualized != null ? `<span>年化波动: ${ind.vol_annualized}%</span>` : ""}
        ${ind.max_drawdown_60d != null ? `<span>最大回撤(60日): ${ind.max_drawdown_60d}%</span>` : ""}
        ${formatDrawdownSpan(data.chart_max_drawdown)}
        ${ind.ma_trend ? `<span>均线: ${ind.ma_trend}</span>` : ""}
      </div>
    </div>
  `;
}

function renderNavChart(navData, days, chartMaxDD) {
  const container = document.getElementById("navChart");
  if (!navData || navData.length === 0) {
    container.innerHTML = '<div class="empty-state"><div>暂无净值数据</div></div>';
    return;
  }

  if (navChartInstance) navChartInstance.dispose();

  // 更新标题
  document.getElementById("navChartTitle").textContent = "净值走势（" + getRangeLabel(days) + "）";

  const dates = navData.map(d => d.date);
  const navs = navData.map(d => d.nav);
  const firstNav = navs[0];  // 区间起点净值，用于计算涨跌幅

  const option = {
    grid: { left: 55, right: 20, top: 20, bottom: 30 },
    xAxis: {
      type: "category", data: dates,
      axisLine: { lineStyle: { color: "#E5E5EA" } },
      axisLabel: { color: "#8E8E93", fontSize: 10,
        formatter: v => v.slice(5) },
    },
    yAxis: {
      type: "value", scale: true,
      axisLine: { show: false },
      axisLabel: { color: "#8E8E93", fontSize: 10 },
      splitLine: { lineStyle: { color: "#F2F2F7" } },
    },
    series: [{
      type: "line", data: navs,
      smooth: true, symbol: "none",
      lineStyle: { color: "#007AFF", width: 2 },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: "rgba(0,122,255,0.2)" },
        { offset: 1, color: "rgba(0,122,255,0.02)" }
      ])},
    }],
    tooltip: {
      trigger: "axis",
      formatter: function(params) {
        const p = params[0];
        const val = p.value;
        const change = firstNav ? ((val - firstNav) / firstNav * 100) : 0;
        const sign = change >= 0 ? "+" : "";
        return `${p.axisValue}<br/>净值: <b>${val.toFixed(4)}</b><br/>区间涨跌: <b style="color:${change>=0?'#FF3B30':'#34C759'}">${sign}${change.toFixed(2)}%</b>`;
      },
    },
  };

  navChartInstance = echarts.init(container);
  navChartInstance.setOption(option);
}

function getRangeLabel(days) {
  const labels = { 30: "近1月", 90: "近3月", 180: "近6月", 365: "近1年", 1095: "近3年" };
  return labels[days] || ("近" + days + "日");
}

function formatDrawdownSpan(dd) {
  if (!dd || dd.value == null || isNaN(dd.value) || dd.value >= 0) return "";
  return `<span title="${dd.peak_date || "?"} → ${dd.trough_date || "?"}">区间最大回撤: ${dd.value}%</span>`;
}

function initNavRangeSelector() {
  const selector = document.getElementById("navRangeSelector");
  if (!selector) return;
  selector.addEventListener("click", function(e) {
    const pill = e.target.closest(".filter-pill");
    if (!pill) return;
    const days = parseInt(pill.dataset.days);
    if (!days || days === currentNavDays) return;
    switchNavRange(days);
  });
}

function switchNavRange(days) {
  if (currentNavDays === days) return;
  currentNavDays = days;

  // 更新active样式
  document.querySelectorAll("#navRangeSelector .filter-pill").forEach(p => p.classList.remove("active"));
  const activePill = document.querySelector(`#navRangeSelector [data-days="${days}"]`);
  if (activePill) activePill.classList.add("active");

  // 更新标题
  document.getElementById("navChartTitle").textContent = "净值走势（" + getRangeLabel(days) + "）";

  // 显示加载中
  const container = document.getElementById("navChart");
  if (navChartInstance) { navChartInstance.dispose(); navChartInstance = null; }
  container.innerHTML = '<div class="loading"><div class="spinner"></div><div>正在加载...</div></div>';

  // 重新获取数据
  if (currentFundCode) loadFundDetail();
}

function renderHoldings(holdings) {
  const container = document.getElementById("holdingsGrid");
  if (!holdings || holdings.length === 0) {
    container.innerHTML = '<div class="empty-state"><div>暂未获取到重仓股数据</div></div>';
    return;
  }

  let html = "";
  holdings.forEach(h => {
    const rt = h.realtime || {};
    const changePct = rt.change_pct || 0;
    const changeCls = changePct > 0 ? "up" : (changePct < 0 ? "down" : "flat");
    const changeSign = changePct >= 0 ? "+" : "";

    // 状态标签颜色
    const state = h.state || "";
    let stateTagCls = "default";
    if (state.includes("破位") || state.includes("下跌")) stateTagCls = "danger";
    else if (state.includes("多头") || state.includes("上涨") || state.includes("拉升")) stateTagCls = "success";
    else if (state.includes("缩量") || state.includes("横盘") || state.includes("低位")) stateTagCls = "warning";
    else if (state.includes("背离")) stateTagCls = "info";

    // 指标列表
    let indicatorsHtml = "";
    if (h.indicators && h.indicators.length > 0) {
      indicatorsHtml = '<ul class="indicator-list">' +
        h.indicators.slice(0, 4).map(i => `<li>· ${i}</li>`).join("") +
        '</ul>';
    }

    // 支撑阻力位
    let srHtml = "";
    if (h.support_resistance) {
      const sr = h.support_resistance;
      if (sr.support && sr.support.length > 0) {
        srHtml += `<div style="font-size:11px;color:var(--ios-green);margin-top:4px;">支撑: ${sr.support.join(", ")}</div>`;
      }
      if (sr.resistance && sr.resistance.length > 0) {
        srHtml += `<div style="font-size:11px;color:var(--ios-red);margin-top:2px;">阻力: ${sr.resistance.join(", ")}</div>`;
      }
    }

    // 头肩形态
    let hsHtml = "";
    if (h.head_shoulders && (h.head_shoulders.head_shoulders_top || h.head_shoulders.head_shoulders_bottom)) {
      hsHtml = `<div style="font-size:11px;margin-top:4px;color:var(--ios-orange);font-weight:500;">
        ${h.head_shoulders.head_shoulders_top ? '⚠ 头肩顶' : ''}
        ${h.head_shoulders.head_shoulders_bottom ? '↑ 头肩底' : ''}
        ${h.head_shoulders.confidence ? `(${h.head_shoulders.confidence}%)` : ''}
      </div>`;
    }

    // 金叉/死叉
    let crossHtml = "";
    if (h.golden_cross && h.golden_cross.length > 0) {
      const latest = h.golden_cross[h.golden_cross.length - 1];
      crossHtml += `<div style="font-size:11px;color:var(--ios-red);margin-top:2px;">金叉 ${latest.date}</div>`;
    }
    if (h.death_cross && h.death_cross.length > 0) {
      const latest = h.death_cross[h.death_cross.length - 1];
      crossHtml += `<div style="font-size:11px;color:var(--ios-green);margin-top:2px;">死叉 ${latest.date}</div>`;
    }

    html += `<div class="holding-card" onclick="viewKline('${h.code}','${h.name}')">
      <div class="holding-header">
        <div>
          <div class="stock-name">${h.name}</div>
          <div class="stock-code">${h.code}</div>
        </div>
        <div class="weight">${h.weight}%</div>
      </div>
      <div class="price-row">
        <div class="price">${rt.price ? rt.price.toFixed(2) : "--"}</div>
        <div class="change ${changeCls}">${changeSign}${changePct.toFixed(2)}%</div>
      </div>
      ${state ? `<span class="state-tag ${stateTagCls}">${state}</span>` : ""}
      ${crossHtml}
      ${hsHtml}
      ${srHtml}
      ${indicatorsHtml}
    </div>`;
  });

  container.innerHTML = html;
}

// ── K-line Chart ──
async function viewKline(code, name) {
  const card = document.getElementById("klineCard");
  card.style.display = "block";
  document.getElementById("klineTitle").textContent = `${name} (${code}) — K线图`;

  const container = document.getElementById("klineChart");
  container.innerHTML = '<div class="loading"><div class="spinner"></div><div>加载K线数据...</div></div>';

  card.scrollIntoView({ behavior: "smooth" });

  try {
    const resp = await apiFetch(`/api/stock/${code}/kline?days=120`, 20000);
    const data = await resp.json();
    if (data.success) {
      renderKlineChart(code, name, data);
      renderKlineAnalysis(data);
    } else {
      container.innerHTML = `<div class="empty-state"><div>${data.error || "加载失败"}</div></div>`;
      document.getElementById("klineAnalysis").innerHTML = "";
    }
  } catch (e) {
    container.innerHTML = '<div class="empty-state"><div>网络错误</div></div>';
    document.getElementById("klineAnalysis").innerHTML = "";
  }
}

function renderKlineChart(code, name, data) {
  const container = document.getElementById("klineChart");
  if (klineChartInstance) klineChartInstance.dispose();

  const dates = data.dates || [];
  const klineData = data.kline || [];
  const indicators = data.indicators || {};

  // ECharts candlestick: [open, close, low, high]
  const ohlc = klineData.map(d => [d.open, d.close, d.low, d.high]);
  const volumes = klineData.map(d => d.volume);

  klineChartInstance = echarts.init(container);

  const option = {
    animation: false,
    grid: [
      { left: "8%", right: "8%", top: "5%", height: "55%" },
      { left: "8%", right: "8%", top: "68%", height: "12%" },
      { left: "8%", right: "8%", top: "83%", height: "15%" },
    ],
    xAxis: [
      {
        type: "category", data: dates, gridIndex: 0,
        axisLabel: { show: false }, axisLine: { lineStyle: { color: "#E5E5EA" } },
      },
      {
        type: "category", data: dates, gridIndex: 1,
        axisLabel: { show: false }, axisLine: { lineStyle: { color: "#E5E5EA" } },
      },
      {
        type: "category", data: dates, gridIndex: 2,
        axisLabel: {
          color: "#8E8E93", fontSize: 10,
          formatter: v => v.slice(5),
          interval: Math.floor(dates.length / 5) || 1,
        },
        axisLine: { lineStyle: { color: "#E5E5EA" } },
      },
    ],
    yAxis: [
      {
        scale: true, gridIndex: 0, splitNumber: 4,
        axisLabel: { color: "#8E8E93", fontSize: 10 },
        splitLine: { lineStyle: { color: "#F2F2F7" } },
      },
      {
        scale: true, gridIndex: 1, splitNumber: 2,
        axisLabel: { color: "#8E8E93", fontSize: 10 },
        splitLine: { lineStyle: { color: "#F2F2F7" } },
      },
      {
        scale: true, gridIndex: 2, splitNumber: 2,
        axisLabel: { color: "#8E8E93", fontSize: 10 },
        splitLine: { lineStyle: { color: "#F2F2F7" } },
      },
    ],
    series: [
      // K线
      {
        type: "candlestick", name: "K线",
        data: ohlc,
        xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: {
          color: "#FF3B30", color0: "#34C759",
          borderColor: "#FF3B30", borderColor0: "#34C759",
        },
      },
      // 均线
      ...["MA5", "MA10", "MA20", "MA60"].map((ma, idx) => ({
        type: "line", name: ma,
        data: indicators[ma] || [],
        xAxisIndex: 0, yAxisIndex: 0,
        symbol: "none", smooth: true,
        lineStyle: { width: 1.5, color: ["#FF9500", "#5AC8FA", "#AF52DE", "#8E8E93"][idx] },
      })),
      // 成交量
      {
        type: "bar", name: "成交量",
        data: volumes,
        xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: {
          color: (p) => {
            const d = klineData[p.dataIndex];
            return d && d.open > d.close ? "#FF3B30" : "#34C759";
          },
        },
      },
      // MACD
      {
        type: "bar", name: "MACD",
        data: indicators["MACD"] || [],
        xAxisIndex: 2, yAxisIndex: 2,
        itemStyle: {
          color: (p) => {
            const v = (indicators["MACD"] || [])[p.dataIndex];
            return v >= 0 ? "#FF3B30" : "#34C759";
          },
        },
      },
      {
        type: "line", name: "DIF",
        data: indicators["DIF"] || [],
        xAxisIndex: 2, yAxisIndex: 2,
        symbol: "none", smooth: true,
        lineStyle: { width: 1, color: "#007AFF" },
      },
      {
        type: "line", name: "DEA",
        data: indicators["DEA"] || [],
        xAxisIndex: 2, yAxisIndex: 2,
        symbol: "none", smooth: true,
        lineStyle: { width: 1, color: "#FF9500" },
      },
    ],
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      formatter: (params) => {
        const k = params.find(p => p.seriesName === "K线");
        if (!k) return "";
        const d = klineData[k.dataIndex];
        if (!d) return "";
        return `${k.axisValue}<br/>
          开: ${d.open.toFixed(2)} 高: ${d.high.toFixed(2)}<br/>
          低: ${d.low.toFixed(2)} 收: ${d.close.toFixed(2)}<br/>
          量: ${(d.volume / 10000).toFixed(0)}万手`;
      },
    },
  };

  klineChartInstance.setOption(option);
}

function renderKlineAnalysis(data) {
  const container = document.getElementById("klineAnalysis");
  const a = data.analysis;
  if (!a) { container.innerHTML = ""; return; }

  const latest = data.kline && data.kline.length > 0 ? data.kline[data.kline.length - 1] : null;
  const latestClose = latest ? latest.close.toFixed(2) : "?";

  let html = '<div style="border-top:0.5px solid var(--ios-separator);padding-top:16px;margin-top:8px;">';

  // ── 1. 综合判断 ──
  html += `<div style="margin-bottom:16px;">
    <div style="font-size:16px;font-weight:600;margin-bottom:8px;">智能分析报告</div>
    <div style="font-size:13px;color:var(--ios-text-secondary);line-height:1.8;">`;

  const state = a.state || "";
  if (state.includes("破位")) {
    html += `<span style="color:var(--ios-red);font-weight:600;">当前状态：${state}</span><br>
      价格已跌破关键支撑位，卖方力量占优。`;
    if (state.includes("放量")) {
      html += "伴随成交量放大，说明有大量筹码被抛售，下跌动能较强，不宜急于抄底。";
    } else if (state.includes("缩量")) {
      html += "但成交量并未放大，可能是主力洗盘行为——通过打压价格逼迫散户离场。若后续放量回升，可视为洗盘结束信号。";
    }
  } else if (state.includes("横盘")) {
    html += `<span style="color:var(--ios-orange);font-weight:600;">当前状态：${state}</span><br>
      价格在一定区间内反复波动，多空双方力量均衡，方向不明。`;
    if (state.includes("低位")) {
      html += "当前处于相对低位，可能是主力在底部收集筹码（吸筹阶段），关注何时放量突破盘整区间上沿。";
    } else {
      html += "建议等待价格突破盘整区间后再做判断——突破上沿看多，跌破下沿看空。";
    }
  } else if (state.includes("多头")) {
    html += `<span style="color:var(--ios-green);font-weight:600;">当前状态：${state}</span><br>
      均线呈多头排列（短期均线在长期均线之上），买方力量主导，处于上升趋势中。`;
  } else if (state.includes("上涨")) {
    html += `<span style="color:var(--ios-green);font-weight:600;">当前状态：${state}</span><br>
      价格持续走高，市场情绪偏乐观。关注成交量是否配合——价升量增是健康上涨信号。`;
  } else {
    html += `<span style="font-weight:600;">当前状态：${state || "震荡整理"}</span><br>市场处于多空博弈阶段，趋势尚不明确。`;
  }
  html += '</div></div>';

  // ── 2. 关键指标解读 ──
  html += '<div style="margin-bottom:16px;"><div style="font-size:14px;font-weight:600;margin-bottom:8px;">关键指标解读</div>';

  const ind = data.indicators || {};
  const lastIdx = (data.kline || []).length - 1;

  // RSI
  if (ind.RSI && ind.RSI[lastIdx] != null) {
    const rsi = ind.RSI[lastIdx];
    let rsiDesc, rsiColor;
    if (rsi > 80) { rsiDesc = "严重超买，回调风险较大"; rsiColor = "var(--ios-red)"; }
    else if (rsi > 70) { rsiDesc = "超买区域，短期可能回调"; rsiColor = "var(--ios-orange)"; }
    else if (rsi < 20) { rsiDesc = "严重超卖，反弹概率较高"; rsiColor = "var(--ios-green)"; }
    else if (rsi < 30) { rsiDesc = "超卖区域，短期可能反弹"; rsiColor = "var(--ios-green)"; }
    else { rsiDesc = "中性区域，无极端信号"; rsiColor = "var(--ios-text-secondary)"; }
    html += `<div style="font-size:13px;margin:6px 0;display:flex;gap:8px;">
      <span style="color:var(--ios-text-secondary);min-width:60px;">RSI ${rsi.toFixed(1)}</span>
      <span style="color:${rsiColor};">${rsiDesc}</span></div>`;
  }

  // MACD
  if (ind.DIF && ind.DEA && ind.MACD && ind.DIF[lastIdx] != null) {
    const dif = ind.DIF[lastIdx], dea = ind.DEA[lastIdx], macd = ind.MACD[lastIdx];
    let macdDesc;
    if (dif > dea && macd > 0) macdDesc = "DIF在DEA上方，MACD红柱，多头主导";
    else if (dif > dea && macd <= 0) macdDesc = "DIF上穿DEA（金叉雏形），关注能否持续";
    else if (dif < dea && macd < 0) macdDesc = "DIF在DEA下方，MACD绿柱，空头主导";
    else macdDesc = "DIF下穿DEA（死叉雏形），短期偏弱";
    html += `<div style="font-size:13px;margin:6px 0;display:flex;gap:8px;">
      <span style="color:var(--ios-text-secondary);min-width:60px;">MACD</span>
      <span>${macdDesc}</span></div>`;
  }

  // KDJ
  if (ind.K && ind.D && ind.J && ind.K[lastIdx] != null) {
    const k = ind.K[lastIdx], d = ind.D[lastIdx], j = ind.J[lastIdx];
    let kdjDesc;
    if (j > 100) kdjDesc = `J值${j.toFixed(0)}，严重超买，短期谨防回调`;
    else if (j < 0) kdjDesc = `J值${j.toFixed(0)}，严重超卖，短期或有反弹`;
    else if (k > 80 && d > 80) kdjDesc = "K/D均在80以上，超买区间";
    else if (k < 20 && d < 20) kdjDesc = "K/D均在20以下，超卖区间";
    else if (k > d) kdjDesc = "K线在D线上方，短期偏多";
    else kdjDesc = "K线在D线下方，短期偏空";
    html += `<div style="font-size:13px;margin:6px 0;display:flex;gap:8px;">
      <span style="color:var(--ios-text-secondary);min-width:60px;">KDJ</span>
      <span>${kdjDesc}</span></div>`;
  }

  // 均线
  if (ind.MA20 && ind.MA20[lastIdx] != null && ind.MA60 && ind.MA60[lastIdx] != null) {
    const ma20 = ind.MA20[lastIdx], ma60 = ind.MA60[lastIdx];
    let maDesc, maColor;
    const price = latest ? latest.close : 0;
    if (ind.MA5 && ind.MA5[lastIdx] != null && ind.MA10 && ind.MA10[lastIdx] != null) {
      const ma5 = ind.MA5[lastIdx], ma10 = ind.MA10[lastIdx];
      if (ma5 > ma10 && ma10 > ma20 && ma20 > ma60) {
        maDesc = "多头排列（MA5>MA10>MA20>MA60），上升趋势良好"; maColor = "var(--ios-green)";
      } else if (ma5 < ma10 && ma10 < ma20 && ma20 < ma60) {
        maDesc = "空头排列（MA5<MA10<MA20<MA60），下跌趋势明显"; maColor = "var(--ios-red)";
      } else if (price > ma60) {
        maDesc = `价格在MA60(${ma60.toFixed(2)})上方，中期趋势偏多`; maColor = "var(--ios-green)";
      } else {
        maDesc = `价格在MA60(${ma60.toFixed(2)})下方，中期趋势偏空`; maColor = "var(--ios-red)";
      }
    } else {
      maDesc = `MA20=${ma20.toFixed(2)}, MA60=${ma60.toFixed(2)}`;
      maColor = "var(--ios-text-secondary)";
    }
    html += `<div style="font-size:13px;margin:6px 0;display:flex;gap:8px;">
      <span style="color:var(--ios-text-secondary);min-width:60px;">均线</span>
      <span style="color:${maColor};">${maDesc}</span></div>`;
  }

  // BOLL
  if (ind.BOLL_UP && ind.BOLL_MID && ind.BOLL_DN && ind.BOLL_UP[lastIdx] != null) {
    const bUp = ind.BOLL_UP[lastIdx], bMid = ind.BOLL_MID[lastIdx], bDn = ind.BOLL_DN[lastIdx];
    let bollDesc;
    const price = latest ? latest.close : 0;
    if (price >= bUp * 0.99) bollDesc = "价格触及布林上轨，短期有回调压力；（布林带口诀：碰顶易回落）";
    else if (price <= bDn * 1.01) bollDesc = "价格触及布林下轨，短期有反弹需求；（布林带口诀：触底易反弹）";
    else bollDesc = "价格在布林带中轨附近运行，波动正常";
    html += `<div style="font-size:13px;margin:6px 0;display:flex;gap:8px;">
      <span style="color:var(--ios-text-secondary);min-width:60px;">布林带</span>
      <span>${bollDesc}</span></div>`;
  }

  html += '</div>';

  // ── 3. 支撑与阻力 ──
  if ((a.support && a.support.length > 0) || (a.resistance && a.resistance.length > 0)) {
    html += '<div style="margin-bottom:16px;"><div style="font-size:14px;font-weight:600;margin-bottom:6px;">关键价位</div>';
    html += '<div style="font-size:13px;color:var(--ios-text-secondary);line-height:1.8;">';
    html += `当前价：<b style="color:var(--ios-text);">${latestClose}</b><br>`;
    if (a.support && a.support.length > 0) {
      html += `支撑位：<span style="color:var(--ios-green);font-weight:500;">${a.support.map(s => s.toFixed(2)).join(" / ")}</span>（跌破则看下一档）<br>`;
    }
    if (a.resistance && a.resistance.length > 0) {
      html += `阻力位：<span style="color:var(--ios-red);font-weight:500;">${a.resistance.map(r => r.toFixed(2)).join(" / ")}</span>（突破则打开上行空间）`;
    }
    html += '</div></div>';
  }

  // ── 4. 形态与背离 ──
  if (a.head_shoulders || a.divergence) {
    html += '<div style="margin-bottom:16px;"><div style="font-size:14px;font-weight:600;margin-bottom:6px;">形态与信号</div>';
    html += '<div style="font-size:13px;color:var(--ios-text-secondary);line-height:1.8;">';

    if (a.head_shoulders) {
      const hs = a.head_shoulders;
      if (hs.head_shoulders_top) {
        html += `<span style="color:var(--ios-red);font-weight:500;">头肩顶形态</span> — 经典看跌反转信号。左肩、头部、右肩依次形成，"头"为最高点。若跌破颈线，下跌空间约为头部到颈线的垂直距离。建议关注颈线位能否守住。<br>`;
      }
      if (hs.head_shoulders_bottom) {
        html += `<span style="color:var(--ios-green);font-weight:500;">头肩底形态</span> — 经典看涨反转信号。"头"为最低点，突破颈线后上涨空间约为头部到颈线的距离。若放量突破颈线，可视为趋势反转确认。<br>`;
      }
      if (hs.description && !hs.head_shoulders_top && !hs.head_shoulders_bottom) {
        html += `${hs.description}<br>`;
      }
    }

    if (a.divergence) {
      const div = a.divergence;
      if (div.top_divergence) {
        html += `<span style="color:var(--ios-red);font-weight:500;">顶背离</span> — 价格创新高但MACD的DIF未创新高，说明上涨动能衰竭，是潜在见顶信号。持仓者可考虑减仓，不宜追高。<br>`;
      }
      if (div.bottom_divergence) {
        html += `<span style="color:var(--ios-green);font-weight:500;">底背离</span> — 价格创新低但MACD的DIF未创新低，说明下跌动能减弱，是潜在见底信号。可关注右侧确认（放量阳线）后择机入场。<br>`;
      }
    }
    html += '</div></div>';
  }

  // ── 5. 风险提示 ──
  html += `<div style="background:var(--ios-gray6);border-radius:10px;padding:12px;font-size:12px;color:var(--ios-text-secondary);line-height:1.6;">
    <span style="font-weight:600;color:var(--ios-text);">风险提示</span><br>
    以上分析基于技术指标（MA/MACD/RSI/KDJ/BOLL）的客观计算结果，仅反映历史趋势和统计规律，不构成投资建议。技术分析存在局限性——突发消息、政策变化、资金面异动等均可能导致走势与指标信号偏离。请结合基本面和自身风险承受能力综合判断。
  </div>`;

  html += '</div>';
  container.innerHTML = html;
}

function closeKline() {
  document.getElementById("klineCard").style.display = "none";
  if (klineChartInstance) klineChartInstance.dispose();
  klineChartInstance = null;
  document.getElementById("klineAnalysis").innerHTML = "";
}

// ── AI Analysis (Tab 3) ──
let aiAnalysisLoading = false;

// ── Holdings AI Analysis (Tab 2) ──
let holdingsAILoading = false;

async function loadHoldingsAIAnalysis(code, force = false) {
  if (!code || holdingsAILoading) return;
  holdingsAILoading = true;

  const card = document.getElementById("holdingsAICard");
  const container = document.getElementById("holdingsAIContent");
  const btn = document.getElementById("holdingsAIRefreshBtn");

  card.style.display = "";
  if (force || !container.querySelector(".ai-h2")) {
    container.innerHTML = '<div class="loading"><div class="spinner"></div><div>AI 正在分析重仓股数据...</div><div style="font-size:12px;color:var(--ios-text-secondary);margin-top:4px;">综合分析技术指标、趋势形态，推理中...</div></div>';
  }
  if (btn) { btn.textContent = "分析中..."; btn.classList.add("refreshing"); }

  try {
    const url = force
      ? `/api/fund/${code}/holdings-ai?force=1`
      : `/api/fund/${code}/holdings-ai`;
    const resp = await apiFetch(url, 130000);
    const data = await resp.json();

    if (data.success) {
      renderHoldingsAIAnalysis(data);
    } else {
      container.innerHTML = `<div class="empty-state">
        <div class="icon">🤖</div>
        <div>${data.error || "AI分析暂时不可用"}</div>
        <div style="font-size:12px;margin-top:4px;">请稍后重试</div>
      </div>`;
    }
  } catch (e) {
    const msg = e.name === "AbortError" ? "AI分析请求超时（>2分钟），请稍后重试" : "网络错误，请检查后端服务";
    container.innerHTML = `<div class="empty-state">
      <div class="icon">⚠️</div><div>${msg}</div>
    </div>`;
    console.error("Holdings AI error:", e);
  }

  holdingsAILoading = false;
  if (btn) { btn.textContent = "刷新分析"; btn.classList.remove("refreshing"); }
}

function renderHoldingsAIAnalysis(data) {
  const container = document.getElementById("holdingsAIContent");
  let html = convertMarkdownToHTML(data.analysis);

  html += `<div class="ai-meta">
    <span>模型: ${data.model || "DeepSeek"}</span>
    <span>·</span>
    <span>分析时间: ${data.update_time || ""}</span>
    <span>·</span>
    <span>${data.fund_name || ""} · ${data.stock_count || 0}只重仓股</span>
  </div>`;

  container.innerHTML = html;
}

async function loadAIAnalysis(force = false) {
  if (aiAnalysisLoading) return;
  aiAnalysisLoading = true;

  const container = document.getElementById("aiAnalysisContent");
  const btn = document.getElementById("aiRefreshBtn");

  // Only show loading on first load or force refresh
  if (force || !container.querySelector(".ai-h2")) {
    container.innerHTML = '<div class="loading"><div class="spinner"></div><div>AI 正在深度分析今日市场数据...</div><div style="font-size:12px;color:var(--ios-text-secondary);margin-top:4px;">综合新闻、板块、指数，推理中...</div></div>';
  }
  if (btn) { btn.textContent = "分析中..."; btn.classList.add("refreshing"); }

  try {
    const url = force ? "/api/ai/analysis?force=1" : "/api/ai/analysis";
    const resp = await apiFetch(url, 130000);
    const data = await resp.json();

    if (data.success) {
      renderAIAnalysis(data);
    } else {
      container.innerHTML = `<div class="empty-state">
        <div class="icon">🤖</div>
        <div>${data.error || "AI分析暂时不可用"}</div>
        <div style="font-size:12px;margin-top:4px;">请稍后重试</div>
      </div>`;
    }
  } catch (e) {
    const msg = e.name === "AbortError" ? "AI分析请求超时（>2分钟），请稍后重试" : "网络错误，请检查后端服务";
    container.innerHTML = `<div class="empty-state">
      <div class="icon">⚠️</div><div>${msg}</div>
    </div>`;
    console.error("AI analysis error:", e);
  }

  aiAnalysisLoading = false;
  if (btn) { btn.textContent = "刷新分析"; btn.classList.remove("refreshing"); }
}

function renderAIAnalysis(data) {
  const container = document.getElementById("aiAnalysisContent");

  // 简单的 Markdown 渲染（处理标题、列表、加粗、分隔线）
  let html = convertMarkdownToHTML(data.analysis);

  // 底部元信息
  html += `<div class="ai-meta">
    <span>模型: ${data.model || "DeepSeek"}</span>
    <span>·</span>
    <span>分析时间: ${data.update_time || ""}</span>
    <span>·</span>
    <span>数据: ${data.data_summary?.market_indices || 0}个指数 · ${data.data_summary?.sectors_analyzed || 0}个板块 · ${data.data_summary?.news_analyzed || 0}条新闻</span>
  </div>`;

  container.innerHTML = html;
}

function convertMarkdownToHTML(md) {
  if (!md) return "";
  let html = md;

  // 标题
  html = html.replace(/^#### (.+)$/gm, '<h4 class="ai-h4">$1</h4>');
  html = html.replace(/^### (.+)$/gm, '<h3 class="ai-h3">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 class="ai-h2">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h2 class="ai-h2">$1</h2>');

  // 分隔线
  html = html.replace(/^---$/gm, '<hr class="ai-hr">');

  // 加粗
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  // 无序列表
  html = html.replace(/^\- (.+)$/gm, '<li class="ai-li">$1</li>');
  html = html.replace(/^\* (.+)$/gm, '<li class="ai-li">$1</li>');

  // 有序列表
  html = html.replace(/^\d+\.\s(.+)$/gm, '<li class="ai-li">$1</li>');

  // 将连续的 <li> 包裹在 <ul> 中
  html = html.replace(/(<li class="ai-li">[\s\S]*?<\/li>)/g, (match) => {
    // 如果已被 ul 包裹则跳过
    return '<ul class="ai-ul">' + match + '</ul>';
  });
  // 合并相邻的 ul
  html = html.replace(/<\/ul>\n<ul class="ai-ul">/g, '\n');

  // 段落：将未处理的文本行包裹为 <p>
  const lines = html.split('\n');
  const result = [];
  for (let line of lines) {
    const trimmed = line.trim();
    if (!trimmed) { result.push(''); continue; }
    // 跳过已处理的标签
    if (trimmed.startsWith('<h') || trimmed.startsWith('<ul') || trimmed.startsWith('</ul')
        || trimmed.startsWith('<li') || trimmed.startsWith('<hr') || trimmed.startsWith('<div')) {
      result.push(line);
    } else {
      result.push('<p class="ai-p">' + trimmed + '</p>');
    }
  }
  return result.join('\n');
}

// ── News (Tab 3) ──
let newsTag = "";
let newsRegion = "all";

function initNewsRegionFilters() {
  const regions = [
    {id: "all", name: "全部资讯"},
    {id: "domestic", name: "国内资讯"},
    {id: "international", name: "国际资讯"},
  ];
  const container = document.getElementById("newsRegionFilters");
  if (!container) return;
  container.innerHTML = regions.map(r =>
    `<span class="filter-pill${r.id === newsRegion ? " active" : ""}"
           onclick="switchNewsRegion('${r.id}')">${r.name}</span>`
  ).join("");
}

function initNewsFilters() {
  const tags = ["全部", "官方政策", "国内财经", "国际财经", "美联储", "海外机构", "产业政策", "市场动态", "科技", "能源", "大宗商品", "国际贸易", "综合资讯"];
  const container = document.getElementById("newsFilters");
  if (!container) return;
  container.innerHTML = tags.map(t =>
    `<span class="filter-pill${t === newsTag || (t === "全部" && !newsTag) ? " active" : ""}"
           onclick="switchNewsTag('${t === "全部" ? "" : t}')">${t}</span>`
  ).join("");
}

function switchNewsRegion(region) {
  newsRegion = region;
  initNewsRegionFilters();
  loadNews();
}

function switchNewsTag(tag) {
  newsTag = tag;
  initNewsFilters();
  loadNews();
}

async function loadNews() {
  initNewsRegionFilters();
  initNewsFilters();

  const btn = document.getElementById("newsRefreshBtn");
  if (btn) { btn.textContent = "刷新中..."; btn.classList.add("refreshing"); }

  let url = "/api/news";
  const params = [];
  if (newsTag) params.push(`tag=${encodeURIComponent(newsTag)}`);
  if (newsRegion !== "all") params.push(`region=${encodeURIComponent(newsRegion)}`);
  if (params.length > 0) url += "?" + params.join("&");
  const newsContainer = document.getElementById("newsList");

  newsContainer.innerHTML = '<div class="loading"><div class="spinner"></div><div>获取新闻中...</div></div>';

  try {
    const newsResp = await apiFetch(url, 15000);
    const newsData = await newsResp.json();

    if (newsData.success) {
      renderNews(newsData.news);
      document.getElementById("newsUpdateTime").textContent = `更新 ${newsData.update_time || ""}`;
    }
  } catch (e) {
    newsContainer.innerHTML = '<div class="empty-state"><div>新闻加载失败</div></div>';
  }

  if (btn) { btn.textContent = "刷新"; btn.classList.remove("refreshing"); }
}

function renderNews(newsList) {
  const container = document.getElementById("newsList");
  if (!newsList || newsList.length === 0) {
    container.innerHTML = '<div class="empty-state"><div>暂无新闻数据</div><div style="font-size:12px;margin-top:4px;">API可能暂时不可用，请稍后刷新</div></div>';
    return;
  }

  container.innerHTML = newsList.map(n => {
    const starsHtml = renderStars(n.stars);
    const sectorsHtml = (n.sectors || []).slice(0, 4).map(s => `<span class="news-sector-tag">${s}</span>`).join("");
    const tagsHtml = (n.tags || []).slice(0, 5).map(t => `<span class="news-tag news-tag-${t}">${t}</span>`).join("");
    // Unix时间戳 → HH:MM
    let timeStr = "";
    if (n.time) {
      const ts = parseInt(n.time);
      const d = !isNaN(ts) ? new Date(ts * 1000) : null;
      if (d) timeStr = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    }
    return `<div class="news-item">
      <div class="news-header">
        <div class="news-title">${n.title}</div>
        <div class="news-stars">${starsHtml}</div>
      </div>
      <div class="news-meta">
        <span class="news-source">${n.source}</span>
        <span class="region-tag region-${n.region || 'domestic'}">${n.region === 'international' ? '国际' : '国内'}</span>
      </div>
      ${n.summary ? `<div style="font-size:13px;color:var(--ios-text-secondary);margin-top:4px;">${n.summary}</div>` : ""}
      <div class="news-bottom-row">
        <div class="news-tags">${tagsHtml}</div>
        ${timeStr ? `<span class="news-time">${timeStr}</span>` : ""}
      </div>
      ${sectorsHtml ? `<div class="news-sectors">${sectorsHtml}</div>` : ""}
    </div>`;
  }).join("");
}

function renderStars(count) {
  let html = "";
  for (let i = 0; i < 5; i++) {
    html += `<span class="star${i < count ? '' : ' empty'}">★</span>`;
  }
  return html;
}

// ── Refresh All ──
async function refreshAll() {
  const btn = document.getElementById("refreshBtn");
  btn.textContent = "刷新中...";
  btn.classList.add("refreshing");

  try {
    await apiFetch("/api/refresh", 10000);
  } catch (e) {}

  await Promise.all([
    loadMarketIndex(),
    loadSectors(),
  ]);

  if (currentFundCode) {
    loadFundDetail();
  }

  btn.textContent = "刷新";
  btn.classList.remove("refreshing");
}

// ── String polyfill ──
String.prototype.zfill = function(width) {
  let s = this;
  while (s.length < width) s = "0" + s;
  return s;
};
