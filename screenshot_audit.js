import { chromium } from 'playwright';

(async () => {
    const browser = await chromium.launch();
    const context = await browser.newContext({
        viewport: { width: 1280, height: 720 },
        deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    
    console.log("Cargando sitio...");
    await page.goto('http://127.0.0.1:8081', { waitUntil: 'networkidle' });
    
    // ACTIVAR MODO OSCURO
    await page.evaluate(() => { localStorage.setItem('theme', 'dark'); document.body.classList.add('dark-mode'); });
    await page.evaluate(() => localStorage.clear());
    await page.reload({ waitUntil: 'networkidle' });
    
    // Esperar a que los productos carguen realmente
    console.log("Esperando productos...");
    try {
        await page.waitForSelector('#productTable tbody tr', { timeout: 30000 });
        console.log("Productos cargados.");
        // Hacer scroll para ver el efecto de las tarjetas
        await page.evaluate(() => window.scrollTo(0, 500));
        await page.waitForTimeout(2000);
    } catch (e) {
        console.log("No se detectaron productos, sacando foto del error.");
    }
    
    await page.screenshot({ path: 'audit_final.png' });
    console.log("Captura guardada en audit_final.png");
    
    await browser.close();
})();
