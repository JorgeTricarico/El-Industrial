/**
 * UI Module - DOM operations and rendering
 */

import { formatProductPrice } from './currency.js';

export const elements = {
    productTableBody: document.querySelector("#productTable tbody"),
    searchInput: document.getElementById("searchInput"),
    loader: document.getElementById("loader"),
    themeToggle: document.getElementById("themeToggle"),
    dollarPrice: document.getElementById("dollarPrice"),
    dollarDate: document.getElementById("dollarDatee"),
    currencyToggle: document.getElementById("currencyToggle"),
    fechaLista: document.getElementById("fechaLista"),
    brandName: document.getElementById("brandName")
};

let allProducts = [];
let currentConfig = {};
let itemsPerPage = 40;
let currentIndex = 0;

export const toggleLoader = (show) => {
    elements.loader.classList.toggle("hidden", !show);
};

const createRow = (product, config) => {
    const { monedaDisplay, precioDisplay, isPrecioConvertido, altPrice } = formatProductPrice(product, config);
    const unidadLabel = ["UN", "Un"].includes(product.unidad) ? "x Unidad" : "x Metro";
    const convertClass = isPrecioConvertido ? 'price-converted' : '';
    
    const tr = document.createElement('tr');
    tr.innerHTML = `
        <td class="col-producto">
            <span class="product-code"><span class="code-label">Cód:</span> ${product.producto}</span>
        </td>
        <td class="col-detalle">${product.detalle}</td>
        <td class="col-marca">
            <span class="brand-badge">${product.marca}</span>
        </td>
        <td class="col-um">
           <div class="meta-info">
             <span class="meta-label">U/M:</span>
             <span class="meta-value">${unidadLabel}</span>
           </div>
        </td>
        <td class="col-precio ${convertClass}" tabindex="0">
           <div class="price-section">
              <div class="cell-value">
                  <span class="currency-symbol">${monedaDisplay}</span> ${precioDisplay}
                  <span class="info-icon">ⓘ</span>
              </div>
              <div class="price-tooltip">${altPrice}</div>
           </div>
        </td>
    `;
    return tr;
};

export const renderProducts = (products, config, append = false) => {
    if (!append) {
        elements.productTableBody.innerHTML = '';
        allProducts = products;
        currentConfig = config;
        currentIndex = 0;
        window.scrollTo(0, 0);
    }

    if (allProducts.length === 0) {
        elements.productTableBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 3rem;">No se encontraron productos.</td></tr>';
        return;
    }

    const fragment = document.createDocumentFragment();
    const limit = Math.min(currentIndex + itemsPerPage, allProducts.length);

    for (let i = currentIndex; i < limit; i++) {
        fragment.appendChild(createRow(allProducts[i], currentConfig));
    }

    elements.productTableBody.appendChild(fragment);
    currentIndex = limit;

    if (currentIndex < allProducts.length) {
        initObserver();
    }
};

let observer;
const initObserver = () => {
    if (observer) observer.disconnect();
    const lastRow = elements.productTableBody.lastElementChild;
    if (!lastRow) return;
    observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting) {
            observer.disconnect();
            renderProducts(allProducts, currentConfig, true);
        }
    }, { rootMargin: '400px' });
    observer.observe(lastRow);
};

export const updateDollarUI = (data) => {
    if (!data) return;
    if (elements.dollarPrice) elements.dollarPrice.textContent = `$${data.venta}`;
    if (elements.dollarDate) {
        const fecha = new Date(data.fechaActualizacion);
        elements.dollarDate.textContent = `(${fecha.toLocaleDateString('es-AR')})`;
    }
    if (elements.currencyToggle) elements.currencyToggle.style.display = "flex";
};

export const updateDateUI = (fileName) => {
    const datePattern = /(\d{2}-\d{2}-\d{2,4})/;
    const match = fileName.match(datePattern);
    if (match) {
        const parts = match[0].split("-");
        const [y, m, d] = parts;
        const fullYear = y.length === 2 ? "20" + y : y;
        elements.fechaLista.textContent = `Lista vigente al ${d}/${m}/${fullYear}`;
    }
};

export const setupTheme = () => {
    const isDark = localStorage.getItem('theme') === 'dark';
    if (isDark) {
        document.body.classList.add('dark-mode');
        updateThemeIcon(true);
    }
    elements.themeToggle.addEventListener('click', () => {
        document.documentElement.classList.add('no-transitions');
        const isNowDark = document.body.classList.toggle('dark-mode');
        localStorage.setItem('theme', isNowDark ? 'dark' : 'light');
        updateThemeIcon(isNowDark);
        setTimeout(() => {
            document.documentElement.classList.remove('no-transitions');
        }, 100);
    });
};

const updateThemeIcon = (isDark) => {
    elements.themeToggle.innerHTML = isDark 
        ? '<i data-lucide="sun" style="width: 22px; height: 22px;"></i>' 
        : '<i data-lucide="moon" style="width: 22px; height: 22px;"></i>';
    if (window.lucide) lucide.createIcons();
};
