import * as api from './modules/api.js';
import * as ui from './modules/ui.js';
import { filterProducts } from './modules/filters.js';

const state = {
    products: [],
    fileName: '',
    usDollarPrice: null,
    showInARS: false,
    searchTimeout: null
};

const init = async () => {
    ui.setupTheme();
    ui.toggleLoader(true);

    try {
        // Parallel fetching of independent data
        const [branding, dollarData, fileName] = await Promise.all([
            api.loadBranding(),
            api.fetchDollarPrice().catch(() => null),
            api.fetchLatestJsonFileName()
        ]);

        if (branding) {
            document.title = `${branding.siteName} - Catálogo`;
            if (ui.elements.brandName) ui.elements.brandName.textContent = branding.siteName;
        }

        if (dollarData) {
            state.usDollarPrice = dollarData.venta;
            ui.updateDollarUI(dollarData);
        } else {
            // Si falla el dólar, igual mostramos el botón (usará un fallback o cargará luego)
            ui.elements.currencyToggle.style.display = "flex";
        }

        state.fileName = fileName;
        ui.updateDateUI(fileName);

        // Load products (from cache or API)
        const storedName = localStorage.getItem('jsonFileName');
        const storedData = localStorage.getItem('products');

        if (storedName === fileName && storedData) {
            state.products = JSON.parse(storedData);
        } else {
            state.products = await api.fetchAndDecompressProducts(fileName);
            localStorage.setItem('jsonFileName', fileName);
            localStorage.setItem('products', JSON.stringify(state.products));
        }

        refreshUI();
    } catch (error) {
        console.error("Initialization failed:", error);
        ui.elements.productTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color: var(--error);">Error al cargar la base de datos.</td></tr>`;
    } finally {
        ui.toggleLoader(false);
        ui.elements.searchInput.focus();
    }
};

const refreshUI = () => {
    const term = ui.elements.searchInput.value.trim();
    const filtered = filterProducts(state.products, term);
    ui.renderProducts(filtered, {
        showInARS: state.showInARS,
        usDollarPrice: state.usDollarPrice
    });
};

// Event Listeners
ui.elements.searchInput.addEventListener('input', () => {
    clearTimeout(state.searchTimeout);
    state.searchTimeout = setTimeout(refreshUI, 300);
});

ui.elements.currencyToggle.addEventListener('click', () => {
    state.showInARS = !state.showInARS;
    ui.elements.currencyToggle.classList.toggle('active', state.showInARS);
    ui.elements.currencyToggle.innerHTML = state.showInARS 
        ? '<i data-lucide="refresh-cw" style="width: 14px; height: 14px;"></i><span>U$S</span>' 
        : '<i data-lucide="refresh-cw" style="width: 14px; height: 14px;"></i><span>AR$</span>';
    lucide.createIcons();
    refreshUI();
});

document.addEventListener('DOMContentLoaded', init);
