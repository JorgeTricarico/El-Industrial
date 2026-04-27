/**
 * Filters Module - Logic for searching and filtering products
 */

export const filterProducts = (products, searchTerm) => {
    if (!searchTerm) return products;
    
    const terms = searchTerm.toLowerCase().split(/\s+/).filter(Boolean);
    return products.filter((product) =>
        terms.every(
            (term) =>
                product.producto.toLowerCase().includes(term) ||
                product.detalle.toLowerCase().includes(term) ||
                product.marca.toLowerCase().includes(term)
        )
    );
};
