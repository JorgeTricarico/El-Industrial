import { test, expect } from '@playwright/test';

test.describe('El Industrial - Validación Pro Frontend', () => {
    
    const URL = 'http://127.0.0.1:8080';

    test('Mobile: Auditoría Visual de Tarjetas', async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto(URL, { waitUntil: 'networkidle' });

        // Esperar carga de productos
        await page.waitForSelector('#productTable tbody tr', { timeout: 15000 });

        // Captura 1: Estado Original
        await page.screenshot({ path: 'tests/screenshots/audit-mobile-original.png' });

        // Activar conversión
        const currencyBtn = page.locator('#currencyToggle');
        await currencyBtn.click();
        await page.waitForTimeout(500);

        // Captura 2: Estado Convertido (Verde)
        await page.screenshot({ path: 'tests/screenshots/audit-mobile-converted.png' });
        
        // Copiar para compatibilidad con nombres anteriores si es necesario
        // Pero ahora miraremos estas nuevas
    });

    test('Desktop: Hover Tooltip', async ({ page }) => {
        await page.setViewportSize({ width: 1280, height: 720 });
        await page.goto(URL, { waitUntil: 'networkidle' });

        const firstPrice = page.locator('#productTable tbody tr:first-child td[data-label="Precio"]');
        await firstPrice.hover();
        await page.waitForTimeout(500);

        await page.screenshot({ path: 'tests/screenshots/audit-desktop-hover.png' });
    });
});
