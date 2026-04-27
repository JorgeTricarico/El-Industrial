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
    // 1. UI Crítica (Inmediata y no bloqueante)
    ui.setupTheme();
    lucide.createIcons();
    ui.toggleLoader(true);

    // 2. Carga de Datos (con manejo de errores individual)
    try {
        const branding = await api.loadBranding().catch(() => null);
        if (branding) {
            document.title = `${branding.siteName} - Catálogo`;
            if (ui.elements.brandName) ui.elements.brandName.textContent = branding.siteName;
        }

        const dollarData = await api.fetchDollarPrice().catch(err => {
            console.warn("Dollar API failed, but continuing...", err);
            return null;
        });

        if (dollarData) {
            state.usDollarPrice = dollarData.venta;
            ui.updateDollarUI(dollarData);
        } else {
            ui.elements.currencyToggle.style.display = "flex";
        }

        const fileName = await api.fetchLatestJsonFileName();
        state.fileName = fileName;
        ui.updateDateUI(fileName);

        // Load products
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
        console.error("Critical initialization failed:", error);
        ui.elements.productTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color: var(--error);">Error al cargar la base de datos. Verifique su conexión.</td></tr>`;
    } finally {
        ui.toggleLoader(false);
        if (ui.elements.searchInput) ui.elements.searchInput.focus();
        lucide.createIcons(); // Re-sincronizar iconos por si acaso
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
        ? '<i data-lucide="refresh-cw" style="width: 14px; height: 14px;"></i><span>Conv. U$S</span>' 
        : '<i data-lucide="refresh-cw" style="width: 14px; height: 14px;"></i><span>Conv. AR$</span>';
    lucide.createIcons();
    refreshUI();
});

document.addEventListener('DOMContentLoaded', init);
