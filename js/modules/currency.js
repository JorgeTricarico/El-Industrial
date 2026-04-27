/**
 * Currency Module - Handles logic for parsing, converting, and formatting prices
 */

export const formatProductPrice = (product, config) => {
    const { showInARS, usDollarPrice } = config;
    
    // Tratamos "U", "DOL", "USD", "U$S" como Dólares.
    const isDolar = ["DOL", "USD", "U$S", "U"].includes(product.moneda?.toUpperCase());
    
    let monedaDisplay = product.moneda;
    
    // Manejo defensivo por si el precio viene como string con comas (ej. "1.000,50" o "1000.50")
    let rawPrice = product.precio;
    if (typeof rawPrice === 'string') {
        // Asumimos que si la API manda decimales, suele usar punto. Si mandara coma:
        rawPrice = rawPrice.replace(',', '.');
    }
    let precioNum = parseFloat(rawPrice) || 0;
    
    let isPrecioConvertido = false;

    if (showInARS && isDolar && usDollarPrice) {
        monedaDisplay = "AR$"; // Claridad en la conversión
        // REDONDEO ARRIBA A 2 DECIMALES
        precioNum = Math.ceil(precioNum * usDollarPrice * 100) / 100;
        isPrecioConvertido = true;
    } else {
        // Restaurar el símbolo clásico "U$S" si es dólar, para evitar que quede una "U" suelta
        monedaDisplay = isDolar ? "U$S" : product.moneda;
    }

    const precioDisplay = precioNum.toLocaleString('es-AR', { 
        minimumFractionDigits: 2, 
        maximumFractionDigits: 2 
    });

    // Calcular el precio alternativo (para el hover y para la etiqueta visible)
    let altPrice = "";
    if (usDollarPrice && usDollarPrice > 0) {
        if (isDolar) {
            if (isPrecioConvertido) {
                // Mostrando AR$, alt es el original en U$S (este no se redondea porque viene de la API)
                altPrice = `U$S ${parseFloat(rawPrice).toLocaleString('es-AR', { minimumFractionDigits: 2 })}`;
            } else {
                // Mostrando U$S, alt es la estimación en AR$ (Redondeamos arriba por seguridad)
                const estimacion = Math.ceil(parseFloat(rawPrice) * usDollarPrice * 100) / 100;
                altPrice = `AR$ ${estimacion.toLocaleString('es-AR', { minimumFractionDigits: 2 })}`;
            }
        } else {
            // El producto está originalmente en Pesos, calculamos el equivalente en USD
            // REDONDEO ARRIBA A 2 DECIMALES:
            const enDolar = Math.ceil((precioNum / usDollarPrice) * 100) / 100;
            altPrice = `U$S ${enDolar.toLocaleString('es-AR', { minimumFractionDigits: 2 })}`;
        }
    }

    return { monedaDisplay, precioDisplay, isPrecioConvertido, altPrice };
};
