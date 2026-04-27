document.addEventListener("DOMContentLoaded", async () => {
  const productTable = document.querySelector("#productTable tbody");
  const searchInput = document.getElementById("searchInput");
  const loader = document.getElementById("loader");
  const themeToggle = document.getElementById("themeToggle");
  const dollarPriceElement = document.getElementById("dollarPrice");
  const fechaListaElement = document.getElementById("fechaLista");

  let currentJsonFileName = "";
  let products = [];
  let searchTimeout;
  let usDollarPrice = null; // Para guardar la cotización
  let showInARS = false;    // Estado del toggle de moneda

  // Función para obtener el nombre del archivo JSON desde latest-json-filename.json en la raíz
  const getLatestJsonFileName = async () => {
    try {
      console.log("Obteniendo nombre del archivo JSON desde /latest-json-filename.json (raíz del sitio)...");
      const response = await fetch("/latest-json-filename.json"); // Fetch desde la raíz del sitio web
      if (!response.ok) {
        throw new Error("No se pudo obtener el nombre del archivo JSON desde /latest-json-filename.json");
      }
      const data = await response.json(); 
      const latestFile = data.filename; // Esperamos un objeto { "filename": "..." }
      console.log("Nombre del archivo JSON obtenido:", latestFile);
      return latestFile.trim();
    } catch (error) {
      console.error("Error al obtener el nombre del JSON:", error);
      // Fallback por si sigue siendo un archivo de texto plano
      try {
          const response = await fetch("/latest-json-filename.txt");
          if (response.ok) {
              const text = await response.text();
              return text.trim();
          }
      } catch (e) {
          console.error("Fallback a .txt también falló");
      }
      throw error;
    }
  };

  const fetchDollarPrice = async () => {
    try {
      const response = await fetch("https://dolarapi.com/v1/ambito/dolares/oficial");
      const data = await response.json();
      console.log("Precio del dólar obtenido:", data);
      usDollarPrice = data.venta;
      dollarPriceElement.textContent = `$${usDollarPrice}`;
      
      const fecha = new Date(data.fechaActualizacion);
      const opciones = { day: '2-digit', month: '2-digit', year: 'numeric' };
      const dollarDateElement = document.getElementById("dollarDatee");
      if (dollarDateElement) {
        dollarDateElement.textContent = `(${fecha.toLocaleDateString('es-AR', opciones)})`;
      }
      
      const currencyToggle = document.getElementById("currencyToggle");
      if (currencyToggle) {
        currencyToggle.style.display = "inline-block";
      }
    } catch (error) {
      console.error("Error al obtener el precio del dólar:", error);
      dollarPriceElement.textContent = "N/A";
    }
  };

  const fetchAndDecompressProducts = async () => {
    console.log("Cargando productos desde:", currentJsonFileName);
    loader.classList.remove("hidden");
    try {
      const response = await fetch(currentJsonFileName);
      if (!response.ok) throw new Error("Error en la respuesta de red");

      let productsData;
      const contentType = response.headers.get("Content-Type");
      const contentEncoding = response.headers.get("Content-Encoding");

      if (currentJsonFileName.endsWith(".gz") && contentEncoding !== "gzip") {
        // El archivo dice ser .gz pero no viene con encoding gzip (posible texto plano)
        // O necesitamos descomprimirlo manualmente si el navegador no lo hizo
        try {
            const arrayBuffer = await response.arrayBuffer();
            const uint8Array = new Uint8Array(arrayBuffer);
            
            // Verificar "magic numbers" de GZIP (0x1f 0x8b)
            if (uint8Array[0] === 0x1f && uint8Array[1] === 0x8b) {
                const stream = new ReadableStream({
                    start(controller) {
                        controller.enqueue(uint8Array);
                        controller.close();
                    }
                });
                const decompressedStream = stream.pipeThrough(new DecompressionStream("gzip"));
                const reader = decompressedStream.getReader();
                const decoder = new TextDecoder("utf-8");
                let jsonText = "";
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    jsonText += decoder.decode(value, { stream: true });
                }
                jsonText += decoder.decode();
                productsData = JSON.parse(jsonText);
            } else {
                // No es GZIP real, intentar leer como texto
                const jsonText = new TextDecoder("utf-8").decode(uint8Array);
                productsData = JSON.parse(jsonText);
            }
        } catch (e) {
            console.error("Fallo la descompresión manual, intentando fallback:", e);
            productsData = await response.json();
        }
      } else {
        // Carga directa de JSON plano o ya descomprimido por el navegador
        productsData = await response.json();
      }

      products = productsData;
      console.log("Productos cargados:", products.length);
      localStorage.setItem("products", JSON.stringify(products));
      localStorage.setItem("jsonFileName", currentJsonFileName);
      displayProducts(products);
    } catch (error) {
      console.error("Error al cargar los productos:", error);
      productTable.innerHTML = `<tr><td colspan="5" style="text-align:center; color: red;">Error al cargar la base de datos. Por favor reintente.</td></tr>`;
    }
    loader.classList.add("hidden");
  };

  const displayProducts = (productsToDisplay) => {
    if (productsToDisplay.length === 0) {
      productTable.innerHTML = `<tr><td colspan="5" style="text-align:center;">No se ha encontrado el producto.</td></tr>`;
      return;
    }

    let rowsHtml = "";
    productsToDisplay.forEach((product) => {
      let monedaDisplay = product.moneda;
      let precioDisplay = product.precio;
      let isPrecioConvertido = false;

      const isDolar = (product.moneda === "DOL" || product.moneda === "USD" || product.moneda === "U$S");
      
      if (showInARS && isDolar && usDollarPrice !== null) {
          monedaDisplay = "$";
          const precioConvertido = parseFloat(product.precio) * usDollarPrice;
          precioDisplay = precioConvertido.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          isPrecioConvertido = true;
      } else {
          monedaDisplay = isDolar ? "U$S" : product.moneda;
      }
      
      const formatUnidad = product.unidad === "UN" || product.unidad === "Un" ? "Un" : "Mts";
      const styleConvertido = isPrecioConvertido ? 'title="Precio aproximado según cotización de hoy" style="font-weight: bold;"' : '';
      const markIndicator = isPrecioConvertido ? ' <span style="opacity:0.6; font-size:0.8em">*</span>' : '';

      rowsHtml += `
        <tr>
          <td data-label="Producto">${product.producto}</td>
          <td data-label="Detalle">${product.detalle}</td>
          <td data-label="Marca">${product.marca}</td>
          <td data-label="Un/Mts">${formatUnidad}</td>
          <td data-label="Precio" ${styleConvertido}>
              ${monedaDisplay} ${precioDisplay}${markIndicator}
          </td>
        </tr>
      `;
    });
    
    productTable.innerHTML = rowsHtml;
  };

  const filterProducts = (searchTerm) => {
    const searchTerms = searchTerm.toLowerCase().split(/\s+/).filter(Boolean);
    return products.filter((product) =>
      searchTerms.every(
        (term) =>
          product.producto.toLowerCase().includes(term) ||
          product.detalle.toLowerCase().includes(term) ||
          product.marca.toLowerCase().includes(term)
      )
    );
  };

  // Inicialización: se comprueba si en localStorage ya se cargaron productos con el mismo archivo
  const initializeProducts = async () => {
    try {
      currentJsonFileName = await getLatestJsonFileName();
    } catch (error) {
      console.error("No se pudo obtener el nombre del archivo JSON.");
      loader.classList.add("hidden");
      return;
    }
    if (!currentJsonFileName) {
      console.error("No hay archivo JSON disponible para cargar los productos.");
      loader.classList.add("hidden");
      return;
    }
    const storedJsonFileName = localStorage.getItem("jsonFileName");
    const storedProducts = localStorage.getItem("products");
    if (storedJsonFileName === currentJsonFileName && storedProducts) {
      products = JSON.parse(storedProducts);
      console.log("Usando productos cacheados para el archivo:", currentJsonFileName);
      displayProducts(products);
    } else {
      await fetchAndDecompressProducts();
    }
    displayDate(); // Mostrar la fecha extraída del nombre del archivo
  };

  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      const searchTerm = searchInput.value.trim();
      const filteredProducts = searchTerm ? filterProducts(searchTerm) : products;
      displayProducts(filteredProducts);
    }, 400);
  });

  themeToggle.addEventListener("click", () => {
    document.body.classList.toggle("dark-mode");
    themeToggle.innerHTML = document.body.classList.contains("dark-mode") ? "☀️" : "🌙";
    themeToggle.style.backgroundColor = document.body.classList.contains("dark-mode") ? "#2e2e2e" : "#fafafa";
  });

  const currencyToggle = document.getElementById("currencyToggle");
  if (currencyToggle) {
    currencyToggle.addEventListener("click", () => {
      showInARS = !showInARS;
      currencyToggle.textContent = showInARS ? "🔄 U$S" : "🔄 AR$";
      const searchTerm = searchInput.value.trim();
      const filteredProducts = searchTerm ? filterProducts(searchTerm) : products;
      displayProducts(filteredProducts);
    });
  }

  // Función para extraer y mostrar la fecha del archivo (basada en su nombre)
  const extractDateFromFileName = (fileName) => {
    const datePattern = /(\d{2}-\d{2}-\d{2,4})/;
    const match = fileName.match(datePattern);
    return match ? match[0] : null;
  };

  const displayDate = () => {
    const fechaExtraida = extractDateFromFileName(currentJsonFileName);
    if (fechaExtraida) {
      // Reformat de yy-mm-dd (del nombre de archivo) a dd/mm/yyyy
      const parts = fechaExtraida.split("-");
      let fechaFormateada = fechaExtraida;
      if (parts.length === 3) {
        const [y, m, d] = parts;
        const fullYear = y.length === 2 ? "20" + y : y;
        fechaFormateada = `${d}/${m}/${fullYear}`;
      }
      
      fechaListaElement.textContent = `Según Lista ${fechaFormateada}`;
      fechaListaElement.style.fontSize = "small";
      fechaListaElement.style.textAlign = "center";
      console.log("Fecha formateada para la lista:", fechaFormateada);
    } else {
      console.log("No se pudo extraer fecha del nombre del archivo:", currentJsonFileName);
    }
  };

  // Función para cargar la marca (White-Label)
  const loadBranding = async () => {
    try {
      const response = await fetch("config/branding.json");
      if (response.ok) {
        const brand = await response.json();
        document.title = brand.siteName + " - Lista de Precios";
        if (document.getElementById("brandName")) {
            document.getElementById("brandName").textContent = brand.siteName;
        }
        // Aplicar colores si se desea (opcional)
        // document.documentElement.style.setProperty('--primary-color', brand.primaryColor);
        console.log("Marca cargada:", brand.siteName);
      }
    } catch (e) {
      console.log("No se encontró branding personalizado, usando defaults.");
    }
  };

  // Inicializamos la aplicación
  await loadBranding();
  await initializeProducts();
  fetchDollarPrice();
  searchInput.focus();
});