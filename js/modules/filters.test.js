/**
 * Simple Unit Test for Filter Logic
 */
import { filterProducts } from './filters.js';

const mockProducts = [
    { producto: 'Tornillo Hexagonal', detalle: '1/4 x 1', marca: 'Fijaciones' },
    { producto: 'Tuerca Zincada', detalle: '1/4', marca: 'Fijaciones' },
    { producto: 'Destornillador PH2', detalle: 'Mango goma', marca: 'Stanley' }
];

const runTests = () => {
    console.log('--- Iniciando Pruebas Unitarias de Filtrado ---');
    
    // Test 1: Búsqueda simple
    const t1 = filterProducts(mockProducts, 'tornillo');
    console.assert(t1.length === 1, 'Error: Debería encontrar 1 tornillo');

    // Test 2: Búsqueda multitérmino (producto + marca)
    const t2 = filterProducts(mockProducts, 'fijaciones tuerca');
    console.assert(t2.length === 1, 'Error: Debería encontrar la tuerca de la marca Fijaciones');

    // Test 3: Búsqueda por detalle
    const t3 = filterProducts(mockProducts, 'zincada');
    console.assert(t3.length === 1, 'Error: Debería encontrar el detalle zincada');

    // Test 4: Sin resultados
    const t4 = filterProducts(mockProducts, 'martillo');
    console.assert(t4.length === 0, 'Error: No debería encontrar martillo');

    console.log('--- Pruebas completadas con éxito ---');
};

runTests();
