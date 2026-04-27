/**
 * Unit Test for Currency Conversion Logic
 */
import { formatProductPrice } from './currency.js';

const runTests = () => {
    console.log('--- Iniciando Pruebas Unitarias de Conversión de Moneda ---');
    
    const usDollarPrice = 1000; // Dólar simulado a $1000 ARS

    // Test 1: Producto en Pesos (No debe convertir aunque showInARS sea true)
    const p1 = { moneda: '$', precio: '150.50' };
    const res1 = formatProductPrice(p1, { showInARS: true, usDollarPrice });
    console.assert(res1.monedaDisplay === '$', `Error T1 Moneda: ${res1.monedaDisplay}`);
    console.assert(res1.isPrecioConvertido === false, 'Error T1 Flag');
    console.assert(res1.precioDisplay === '150,50', `Error T1 Valor: ${res1.precioDisplay}`);

    // Test 2: Producto en Dólares "U" (API Data) (Debe mostrar U$S si no hay conversión)
    const p2 = { moneda: 'U', precio: '10.00' };
    const res2 = formatProductPrice(p2, { showInARS: false, usDollarPrice });
    console.assert(res2.monedaDisplay === 'U$S', `Error T2 Moneda: ${res2.monedaDisplay}`);
    console.assert(res2.isPrecioConvertido === false, 'Error T2 Flag');
    console.assert(res2.precioDisplay === '10,00', `Error T2 Valor: ${res2.precioDisplay}`);

    // Test 3: Producto en Dólares "U" con Conversión Activa
    const res3 = formatProductPrice(p2, { showInARS: true, usDollarPrice });
    console.assert(res3.monedaDisplay === 'AR$', `Error T3 Moneda: ${res3.monedaDisplay}`);
    console.assert(res3.isPrecioConvertido === true, 'Error T3 Flag');
    // 10.00 * 1000 = 10000 -> formateado en es-AR: "10.000,00"
    console.assert(res3.precioDisplay === '10.000,00', `Error T3 Valor: ${res3.precioDisplay}`);

    // Test 4: Producto en "USD" con texto como precio
    const p4 = { moneda: 'USD', precio: '1234.56' };
    const res4 = formatProductPrice(p4, { showInARS: true, usDollarPrice });
    console.assert(res4.isPrecioConvertido === true, 'Error T4 Flag');
    // 1234.56 * 1000 = 1234560 -> "1.234.560,00"
    console.assert(res4.precioDisplay === '1.234.560,00', `Error T4 Valor: ${res4.precioDisplay}`);

    console.log('--- ✅ Todas las pruebas de moneda completadas con éxito ---');
};

runTests();
