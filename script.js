document.addEventListener("DOMContentLoaded", () => {
  const productTable = document.querySelector("#productTable tbody");
  const searchInput = document.getElementById("searchInput");
  const loader = document.getElementById("loader");
  //IMPORTANTE CAMBIAR SI SE SUBE UN NUEVO JSON ↓
  const currentJsonFileName = "ListaPrecio 25-07-24_json_compres.gz";
  //IMPORTANTE CAMBIAR SI SE SUBE UN NUEVO JSON ↑
  const themeToggle = document.getElementById("themeToggle");

  let searchTimeout;
  let products = [];

  const fetchAndDecompressProducts = async () => {
    console.log("Fetching and decompressing products...");
    loader.classList.remove("hidden");
    try {
      const response = await fetch(currentJsonFileName);
      if (!response.ok) {
        throw new Error("Network response was not ok");
      }

      const compressedStream = response.body.pipeThrough(
        new DecompressionStream("gzip")
      );
      const reader = compressedStream.getReader();
      const decoder = new TextDecoder("utf-8");
      let jsonText = "";

      let done, value;
      while ((({ value, done } = await reader.read()), !done)) {
        jsonText += decoder.decode(value, { stream: true });
      }
      jsonText += decoder.decode(); // Decodes any remaining bytes

      products = JSON.parse(jsonText);
      localStorage.removeItem("products");
      localStorage.removeItem("jsonFileName");
      localStorage.setItem("products", JSON.stringify(products));
      localStorage.setItem("jsonFileName", currentJsonFileName);
      displayProducts(products);
    } catch (error) {
      console.error("Error al cargar los productos:", error);
    }
    loader.classList.add("hidden");
  };

  const displayProducts = (products) => {
    productTable.innerHTML = ""; // Limpiar el contenido de la tabla
    if (products.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 6; // Asumiendo que tienes 6 columnas
      cell.textContent = "No se ha encontrado el producto.";
      cell.style.textAlign = "center";
      row.appendChild(cell);
      productTable.appendChild(row);
      return;
    }
    products.forEach((product) => {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td data-label="Producto">${product.producto}</td>
        <td data-label="Detalle">${product.detalle}</td>
        <td data-label="Marca">${product.marca}</td>
        <td data-label="Un/Mts">${product.unidad == "UN" ? "Un" : "Mts"}</td>
        <td data-label="Precio">${product.moneda + " " + product.precio}</td>
        `;
      productTable.appendChild(row);
    });
    loader.classList.add("hidden");
  };

  const filterProducts = (searchTerm, products) => {
    const searchTerms = searchTerm
      .split(" ")
      .map((term) => term.trim())
      .filter((term) => term);
    return products.filter((product) =>
      searchTerms.every(
        (term) =>
          product.producto.toLowerCase().includes(term) ||
          product.detalle.toLowerCase().includes(term) ||
          product.marca.toLowerCase().includes(term)
      )
    );
  };

  const initializeProducts = () => {
    const storedJsonFileName = sessionStorage.getItem("jsonFileName");
    if (storedJsonFileName === currentJsonFileName) {
      const storedProducts = sessionStorage.getItem("products");
      products = JSON.parse(storedProducts);
      displayProducts(products);
      loader.classList.add("hidden");
    } else {
      fetchAndDecompressProducts();
    }
  };

  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      const searchTerm = searchInput.value.toLowerCase().trim();
      if (searchTerm.length > 0) {
        const filteredProducts = filterProducts(searchTerm, products);
        productTable.innerHTML = "";
        displayProducts(filteredProducts);
      } else {
        displayProducts(products);
      }
    }, 400);
  });

  themeToggle.addEventListener("click", () => {
    document.body.classList.toggle("dark-mode");
    if (document.body.classList.contains("dark-mode")) {
      themeToggle.innerHTML = "☀️";
      themeToggle.style.backgroundColor = "#2e2e2e";
    } else {
      themeToggle.innerHTML = "🌙";
      themeToggle.style.backgroundColor = "#fafafa";
    }
  });

  initializeProducts();
  searchInput.focus();
});
