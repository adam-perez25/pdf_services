// The single list of tools. The home page and the side navigation on every
// tool page are built from it; tests/test_navigation.py checks it lists every
// page route.
const TOOL_GROUPS = [
    { id: "organize", title: "Unir y organizar" },
    { id: "convert", title: "Convertir a PDF" },
    { id: "reduce", title: "Reducir y archivar" },
    { id: "secure", title: "Proteger y firmar" },
    { id: "extract", title: "Extraer texto e imágenes" },
    { id: "workflow", title: "Flujo de trabajo" },
];

const TOOLS = [
    { id: "merge", group: "organize", path: "/merge", title: "Unir PDF", summary: "Combina varios documentos en uno." },
    { id: "split", group: "organize", path: "/split", title: "Dividir PDF", summary: "Separa un documento en varios archivos." },
    { id: "pages", group: "organize", path: "/pages", title: "Organizar páginas", summary: "Gira, elimina y reordena páginas con vista previa." },
    { id: "stamp", group: "organize", path: "/stamp", title: "Numerar y marca de agua", summary: "Añade números de página o un texto de marca de agua." },
    { id: "images", group: "convert", path: "/images", title: "Imágenes a PDF", summary: "Convierte varias imágenes en un único PDF." },
    { id: "word", group: "convert", path: "/word", title: "Word a PDF", summary: "Convierte documentos DOC y DOCX en PDF." },
    { id: "excel", group: "convert", path: "/excel", title: "Excel a PDF", summary: "Convierte hojas de cálculo XLS y XLSX en PDF." },
    { id: "powerpoint", group: "convert", path: "/powerpoint", title: "PowerPoint a PDF", summary: "Convierte presentaciones PPT y PPTX en PDF." },
    { id: "compress", group: "reduce", path: "/compress", title: "Comprimir PDF", summary: "Reduce el tamaño, ideal para documentos escaneados." },
    { id: "pdfa", group: "reduce", path: "/pdfa", title: "PDF/A y reparar", summary: "Convierte a PDF/A para archivo o vuelve a guardar un PDF dañado." },
    { id: "protect", group: "secure", path: "/protect", title: "Proteger PDF", summary: "Añade o quita la contraseña de un PDF (AES-256)." },
    { id: "sign", group: "secure", path: "/sign", title: "Firmar PDF", summary: "Dibuja o sube tu firma y colócala en las páginas (firma visual)." },
    { id: "metadata", group: "secure", path: "/metadata", title: "Editar metadatos", summary: "Cambia título y autor, o borra todos los metadatos." },
    { id: "ocr", group: "extract", path: "/ocr", title: "OCR: hacer buscable un escaneo", summary: "Reconoce el texto de los escaneos para poder buscarlo y copiarlo." },
    { id: "extract-text", group: "extract", path: "/extract-text", title: "Extraer texto", summary: "Descarga el texto de un PDF como archivo TXT." },
    { id: "pdf-to-images", group: "extract", path: "/pdf-to-images", title: "PDF a imágenes", summary: "Convierte cada página en una imagen PNG o JPG." },
    { id: "workflow", group: "workflow", path: "/workflow", title: "Flujo de trabajo", summary: "Encadena varias herramientas y descarga solo el resultado." },
];

(() => {
    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    function toolsIn(group) {
        return TOOLS.filter((tool) => tool.group === group.id);
    }

    // Lower case without accents, so "imagenes" finds "Imágenes".
    function searchable(text) {
        return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
    }

    function buildSideNav(nav) {
        const current = window.location.pathname.replace(/\/+$/, "") || "/";
        nav.replaceChildren();

        const bar = element("div", "side-nav-bar");
        const brand = element("a", "brand", "PDF Toolkit");
        brand.href = "/";
        const toggle = element("button", "nav-toggle", "Herramientas");
        toggle.type = "button";
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-controls", "side-nav-panel");
        bar.append(brand, toggle);

        const panel = element("div", "side-nav-panel");
        panel.id = "side-nav-panel";
        const home = element("a", "side-nav-home", "Todas las herramientas");
        home.href = "/";
        panel.append(home);
        for (const group of TOOL_GROUPS) {
            const section = element("div", "side-nav-group");
            section.dataset.group = group.id;
            const heading = element("p", "side-nav-heading", group.title);
            heading.id = `side-nav-${group.id}`;
            const list = element("ul", "side-nav-list");
            list.setAttribute("aria-labelledby", heading.id);
            for (const tool of toolsIn(group)) {
                const item = element("li");
                const link = element("a", "side-nav-link", tool.title);
                link.href = tool.path;
                if (tool.path === current) link.setAttribute("aria-current", "page");
                item.append(link);
                list.append(item);
            }
            section.append(heading, list);
            panel.append(section);
        }
        nav.append(bar, panel);

        function setOpen(open) {
            nav.classList.toggle("is-open", open);
            toggle.setAttribute("aria-expanded", String(open));
        }
        toggle.addEventListener("click", () => setOpen(!nav.classList.contains("is-open")));
        nav.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && nav.classList.contains("is-open")) {
                setOpen(false);
                toggle.focus();
            }
        });
    }

    function buildHome(container, search, empty) {
        const cards = [];
        for (const group of TOOL_GROUPS) {
            const section = element("section", "tool-group");
            section.dataset.group = group.id;
            const heading = element("h2", "tool-group-title", group.title);
            heading.id = `group-${group.id}`;
            section.setAttribute("aria-labelledby", heading.id);
            const list = element("ul", "tool-grid");
            for (const tool of toolsIn(group)) {
                const item = element("li");
                const card = element("a", "tool-card");
                card.href = tool.path;
                card.append(element("strong", "tool-card-title", tool.title), element("span", "tool-card-summary", tool.summary));
                item.append(card);
                list.append(item);
                cards.push({ item, section, text: searchable(`${tool.title} ${tool.summary} ${group.title}`) });
            }
            section.append(heading, list);
            container.append(section);
        }

        function filter() {
            const words = searchable(search.value).split(/\s+/).filter(Boolean);
            const visible = new Set();
            for (const card of cards) {
                const match = words.every((word) => card.text.includes(word));
                card.item.hidden = !match;
                if (match) visible.add(card.section);
            }
            for (const section of container.querySelectorAll(".tool-group")) {
                section.hidden = !visible.has(section);
            }
            empty.hidden = visible.size > 0;
        }
        search.addEventListener("input", filter);
        search.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && search.value) {
                search.value = "";
                filter();
            } else if (event.key === "Enter" && search.value.trim()) {
                const first = cards.find((card) => !card.item.hidden);
                if (first) first.item.querySelector("a").click();
            }
        });
        // "/" jumps to the search box unless the user is typing somewhere.
        document.addEventListener("keydown", (event) => {
            const target = event.target;
            const typing = target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
            if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
                event.preventDefault();
                search.focus();
            }
        });
        filter();
    }

    const nav = document.getElementById("side-nav");
    if (nav) buildSideNav(nav);

    const home = document.getElementById("tool-groups");
    const search = document.getElementById("tool-search");
    const empty = document.getElementById("tool-search-empty");
    if (home && search && empty) {
        // Hidden in the HTML so it only shows when this script runs.
        search.closest("[hidden]")?.removeAttribute("hidden");
        buildHome(home, search, empty);
    }
})();
