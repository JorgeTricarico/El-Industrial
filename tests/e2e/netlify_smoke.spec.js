/**
 * E2E smoke tests contra los Netlify sites de PRODUCCION.
 *
 * Cuando esta suite falla, los compradores estan viendo algo roto. Por eso se
 * corre en GH Actions cada noche con alerta Telegram inmediata si falla.
 *
 * Que valida (lo que un test unitario o el post_deploy_check.py NO cubren):
 *   - El navegador realmente puede cargar el sitio sin errores de JS.
 *   - El JS aplica el branding desde config/branding.json en runtime (title
 *     y brandName cambian de "Cargando…" al nombre real).
 *   - La tabla de productos se llena con al menos N rows visibles.
 *   - El primer producto tiene un precio numerico parseable.
 *
 * Se ejecuta contra cada tenant active/testing leyendo de tenants/_registry.yml.
 */
import { test, expect } from '@playwright/test';
import fs from 'fs';
import path from 'path';
import yaml from 'js-yaml';

// Lee la lista de tenants y filtra los que estan en estado active/testing
// con URL Netlify valida.
function loadTenants() {
    const registryPath = path.join(process.cwd(), 'tenants', '_registry.yml');
    if (!fs.existsSync(registryPath)) return [];
    const data = yaml.load(fs.readFileSync(registryPath, 'utf8')) || {};
    return (data.tenants || []).filter(t =>
        ['active', 'testing'].includes(t.state) &&
        (t.netlify_url || '').startsWith('http')
    );
}

const tenants = loadTenants();

for (const tenant of tenants) {
    test.describe(`tenant: ${tenant.slug}`, () => {

        test(`${tenant.slug}: carga sin errores JS y aplica branding`, async ({ page }) => {
            const jsErrors = [];
            page.on('pageerror', err => jsErrors.push(err.message));

            const consoleErrors = [];
            page.on('console', msg => {
                if (msg.type() === 'error') consoleErrors.push(msg.text());
            });

            await page.goto(tenant.netlify_url, { waitUntil: 'networkidle', timeout: 30000 });

            // El JS DEBE haber reemplazado el placeholder "Cargando..." con el siteName
            // de config/branding.json. Si quedo "Cargando…", el branding no cargo.
            const brandName = await page.locator('#brandName').textContent();
            expect(brandName, `brandName quedo en placeholder en ${tenant.slug}`).not.toBe('Cargando…');
            expect(brandName, `brandName vacio en ${tenant.slug}`).toBeTruthy();

            // El title del browser tambien debe haberse actualizado
            const title = await page.title();
            expect(title, `title quedo como 'Cargando catalogo…' en ${tenant.slug}`).not.toBe('Cargando catalogo…');

            // Sin errores fatales de JS
            expect(jsErrors, `Errores JS en ${tenant.slug}: ${jsErrors.join('; ')}`).toEqual([]);
        });

        test(`${tenant.slug}: tabla de productos se llena`, async ({ page }) => {
            await page.goto(tenant.netlify_url, { waitUntil: 'networkidle', timeout: 30000 });

            // Esperar a que se rendericen rows. El loader debe esconderse.
            await page.waitForSelector('#productTable tbody tr', { timeout: 20000 });

            const rowCount = await page.locator('#productTable tbody tr').count();
            expect(rowCount, `${tenant.slug}: tabla vacia (0 productos). El catalogo no cargo.`).toBeGreaterThan(10);
        });

        test(`${tenant.slug}: primer producto tiene precio numerico`, async ({ page }) => {
            await page.goto(tenant.netlify_url, { waitUntil: 'networkidle', timeout: 30000 });
            await page.waitForSelector('#productTable tbody tr', { timeout: 20000 });

            // El primer row debe tener un precio que parsee a numero positivo
            const firstPriceText = await page.locator('#productTable tbody tr').first()
                .locator('td.col-precio').textContent();
            expect(firstPriceText, `${tenant.slug}: precio del primer producto vacio`).toBeTruthy();

            // Extraer el numero (puede tener $ o U$S al inicio y . como separador)
            const priceMatch = firstPriceText.replace(/[^\d.]/g, '');
            const priceNum = parseFloat(priceMatch);
            expect(priceNum, `${tenant.slug}: precio no parseable: '${firstPriceText}'`).toBeGreaterThan(0);
        });

        test(`${tenant.slug}: pointer publico apunta a archivo de hoy o ayer`, async ({ page, request }) => {
            // Tenants en state=testing usan supplier stub (sin fetch real). Su
            // data es la del root mirror que solo se actualiza si update_products
            // corre en root — cosa que ya NO pasa post-M1. Por eso saltamos el
            // chequeo de freshness para testing tenants. El chequeo de "carga y
            // contenido" SI corre arriba.
            test.skip(tenant.state === 'testing',
                `tenant '${tenant.slug}' es testing/stub, data no se actualiza por design`);

            const res = await request.get(`${tenant.netlify_url}/latest-json-filename.txt`);
            expect(res.ok(), `${tenant.slug}: latest-json-filename.txt no responde`).toBeTruthy();
            const txt = (await res.text()).trim();
            const m = txt.match(/(\d{2})-(\d{2})-(\d{2})/);
            expect(m, `${tenant.slug}: no se pudo parsear fecha de ${txt}`).not.toBeNull();
            const [_, yy, mm, dd] = m;
            const fileDate = new Date(`20${yy}-${mm}-${dd}T00:00:00Z`);
            const ageHours = (Date.now() - fileDate.getTime()) / 3600000;

            // Threshold weekend-aware: el cron es Lun-Sab AR. Domingo no
            // actualiza, asi que en Lun-mañana la data puede tener hasta ~50h
            // legitimamente. Toleramos:
            //   - Domingo: 60h
            //   - Lunes antes de las 20 AR: 60h
            //   - resto: 36h
            // Usamos hora AR (UTC-3) para weekday/hora.
            const nowAR = new Date(Date.now() - 3 * 3600 * 1000);
            const arWeekday = nowAR.getUTCDay(); // 0=Dom..6=Sab
            const arHour = nowAR.getUTCHours();
            let threshold = 36;
            if (arWeekday === 0) threshold = 60; // Domingo todo el dia
            else if (arWeekday === 1 && arHour < 20) threshold = 60; // Lun antes del cron 20:00
            expect(
                ageHours,
                `${tenant.slug}: data servida es de hace ${ageHours.toFixed(0)}h (>${threshold}h, el cron no esta deployando)`
            ).toBeLessThan(threshold);
        });
    });
}
