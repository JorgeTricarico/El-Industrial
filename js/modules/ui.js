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

export const toggleLoader = (show) => {
    elements.loader.classList.toggle("hidden", !show);
};

export const renderProducts = (products, config) => {
    
    if (products.length === 0) {
        elements.productTableBody.innerHTML = `
            <tr>
                <td colspan="5" class="empty-state" style="text-align: center; display: block; width: 100%;">
                    No se han encontrado productos.
                </td>
            </tr>`;
        return;
    }

    const rowsHtml = products.map((product) => {
        
        const { monedaDisplay, precioDisplay, isPrecioConvertido, altPrice } = formatProductPrice(product, config);
        
        // Unidades más descriptivas para el catálogo
        const unidadLabel = ["UN", "Un"].includes(product.unidad) ? "x Unidad" : "x Metro";
        const convertClass = isPrecioConvertido ? 'price-converted' : '';
        const mark = isPrecioConvertido ? '<span class="price-asterisk">*</span>' : '';

        return `
            <tr>
                <td data-label="Producto" class="col-producto">
                    <span class="product-code"><span class="code-label">Cód:</span> ${product.producto}</span>
                </td>
                <td data-label="Detalle" class="col-detalle">
                    ${product.detalle}
                </td>
                <td data-label="Marca" class="col-marca">
                    <span class="brand-badge"><span class="brand-label">Marca:</span> ${product.marca}</span>
                </td>
                <td data-label="U/M" class="col-um">
                   <div class="meta-info">
                     <span class="meta-label">U/M:</span>
                     <span class="meta-value">${unidadLabel}</span>
                   </div>
                </td>
                <td data-label="Precio" class="col-precio ${convertClass}" tabindex="0">
                   <div class="price-section">
                      <div class="cell-value">
                          <span class="currency-symbol">${monedaDisplay}</span> ${precioDisplay}${mark}
                      </div>
                      ${altPrice ? `<div class="price-tooltip">${altPrice}</div>` : ''}
                   </div>
                </td>
            </tr>
        `;
    }).join('');
    
    elements.productTableBody.innerHTML = rowsHtml;
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
        // Desactivar transiciones para evitar lag
        document.documentElement.classList.add('no-transitions');
        
        const isNowDark = document.body.classList.toggle('dark-mode');
        localStorage.setItem('theme', isNowDark ? 'dark' : 'light');
        
        updateThemeIcon(isNowDark);
        
        // Re-activar transiciones después de un breve momento
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
