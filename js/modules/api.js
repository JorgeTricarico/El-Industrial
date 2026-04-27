/**
 * API Module - Handles data fetching and decompression
 */

export const fetchLatestJsonFileName = async () => {
    try {
        const response = await fetch("/latest-json-filename.json");
        if (!response.ok) throw new Error("JSON naming file not found");
        const data = await response.json();
        return data.filename.trim();
    } catch (error) {
        console.warn("Falling back to .txt for filename");
        const response = await fetch("/latest-json-filename.txt");
        if (response.ok) {
            const text = await response.text();
            return text.trim();
        }
        throw error;
    }
};

export const fetchDollarPrice = async () => {
    const response = await fetch("https://dolarapi.com/v1/ambito/dolares/oficial");
    if (!response.ok) throw new Error("Dollar API failed");
    return await response.json();
};

export const fetchAndDecompressProducts = async (fileName) => {
    const response = await fetch(fileName);
    if (!response.ok) throw new Error("Network response was not ok");

    const contentEncoding = response.headers.get("Content-Encoding");

    if (fileName.endsWith(".gz") && contentEncoding !== "gzip") {
        const arrayBuffer = await response.arrayBuffer();
        const uint8Array = new Uint8Array(arrayBuffer);
        
        // Magic numbers GZIP (0x1f 0x8b)
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
            return JSON.parse(jsonText);
        } else {
            return JSON.parse(new TextDecoder("utf-8").decode(uint8Array));
        }
    }
    
    return await response.json();
};

export const loadBranding = async () => {
    try {
        const response = await fetch("config/branding.json");
        if (response.ok) return await response.json();
    } catch (e) {
        return null;
    }
};
