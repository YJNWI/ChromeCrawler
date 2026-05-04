import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque
import threading


def limpiar_texto(texto):
    return " ".join(texto.split()) if texto else ""


def normalizar_url(url):
    parsed = urlparse(url)

    # Quitar fragmentos tipo #section
    parsed = parsed._replace(fragment="")

    # Quitar slash final
    url = urlunparse(parsed).rstrip("/")

    return url


def obtener_dominio(url):
    dominio = urlparse(url).netloc.lower()

    if dominio.startswith("www."):
        dominio = dominio[4:]

    partes = dominio.split(".")

    compuestos = {"co", "com", "org", "net", "gov", "ac", "edu"}

    if len(partes) >= 3 and partes[-2] in compuestos:
        return ".".join(partes[-3:])

    if len(partes) >= 2:
        return ".".join(partes[-2:])

    return dominio


def es_link_valido(url):
    url_lower = url.lower()
    parsed = urlparse(url)
    ruta = parsed.path.lower()

    if not url_lower.startswith("http"):
        return False

    # Archivos que no queremos rastrear
    extensiones_bloqueadas = [
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
        ".pdf", ".zip", ".rar", ".7z",
        ".mp4", ".mp3", ".avi", ".mov",
        ".css", ".js", ".xml", ".json"
    ]

    if any(url_lower.endswith(ext) for ext in extensiones_bloqueadas):
        return False

    # Ruido típico
    basura = [
        "login", "signup", "sign-up", "signin", "sign-in",
        "register", "auth", "account",
        "share", "sharer", "sharing",
        "utm_", "ref=", "tracking", "campaign",
        "privacy", "terms", "cookies",
        "help", "support", "contact",
        "status", "ads", "advertising",
        "mailto:", "tel:", "javascript:",
        "action=", "oldid=", "printable=", "useparsoid=",
        "returnto=", "bookcmd=",
        "special:", "especial:",
        "archivo:", "file:",
        "categoría:", "category:",
        "ayuda:", "portal:",
        "wmflabs.org", "wsexport", ".php?"
    ]

    if any(x in url_lower for x in basura):
        return False

    # Redes sociales que suelen dar ruido o requieren JS/login
    redes_bloqueadas = [
        "twitter.com", "x.com", "facebook.com",
        "instagram.com", "linkedin.com",
        "tiktok.com", "threads.net"
    ]

    if any(red in url_lower for red in redes_bloqueadas):
        return False

    # Evita raíces pobres tipo /wiki
    if ruta == "/wiki":
        return False

    return True


def puntuacion_link(url):
    url_lower = url.lower()

    score = 0

    buenos = [
        "blog", "news", "article", "post",
        "docs", "documentation", "guide",
        "research", "paper", "arxiv",
        "github.com", "medium.com",
        "techcrunch", "wired", "quantamagazine",
        "developer", "developers",
        "learn", "tutorial"
    ]

    malos = [
        "login", "signup", "share", "status",
        "privacy", "terms", "contact", "help",
        "pricing", "careers", "jobs"
    ]

    for palabra in buenos:
        if palabra in url_lower:
            score += 5

    for palabra in malos:
        if palabra in url_lower:
            score -= 10

    # Preferimos URLs no absurdamente largas
    if len(url) > 180:
        score -= 5

    return score


def ordenar_enlaces(enlaces, url_actual):
    dominio_actual = obtener_dominio(url_actual)

    externos_buenos = []
    externos_normales = []
    internos_buenos = []
    internos_normales = []

    for link in enlaces:
        if not es_link_valido(link):
            continue

        dominio_link = obtener_dominio(link)
        score = puntuacion_link(link)

        if dominio_link != dominio_actual:
            if score > 0:
                externos_buenos.append((score, link))
            else:
                externos_normales.append((score, link))
        else:
            if score > 0:
                internos_buenos.append((score, link))
            else:
                internos_normales.append((score, link))

    externos_buenos.sort(reverse=True)
    internos_buenos.sort(reverse=True)

    ordenados = (
        [x[1] for x in externos_buenos] +
        [x[1] for x in externos_normales] +
        [x[1] for x in internos_buenos] +
        [x[1] for x in internos_normales]
    )

    return ordenados


def obtener_info(url):
    try:
        headers = {
            "User-Agent": "CrawlerEducativoRapido/6.0"
        }

        r = requests.get(url, headers=headers, timeout=6)

        content_type = r.headers.get("Content-Type", "")

        if "text/html" not in content_type:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        titulo = limpiar_texto(soup.title.string if soup.title else "Sin título")

        descripcion = ""
        meta = soup.find("meta", attrs={"name": "description"})

        if meta and meta.get("content"):
            descripcion = limpiar_texto(meta.get("content"))
        else:
            for p in soup.find_all("p"):
                texto = limpiar_texto(p.get_text())
                if len(texto) > 80:
                    descripcion = texto
                    break

        if not descripcion:
            descripcion = "Sin descripción"

        enlaces = []

        for a in soup.find_all("a", href=True):
            link = urljoin(url, a["href"])
            link = normalizar_url(link)

            if es_link_valido(link):
                enlaces.append(link)

        # Quitar duplicados manteniendo orden
        enlaces_unicos = []
        vistos = set()

        for link in enlaces:
            if link not in vistos:
                enlaces_unicos.append(link)
                vistos.add(link)

        return {
            "url": url,
            "dominio": obtener_dominio(url),
            "titulo": titulo,
            "descripcion": descripcion,
            "enlaces": enlaces_unicos
        }

    except Exception as e:
        print(f"Error en {url}: {e}")
        return None


class Crawler:
    def __init__(self, url_inicial, max_paginas, max_por_dominio):
        url_inicial = normalizar_url(url_inicial)

        self.cola = deque([url_inicial])
        self.en_cola = set([url_inicial])
        self.visitadas = set()
        self.resultados = []

        self.max_paginas = max_paginas
        self.max_por_dominio = max_por_dominio

        self.contador_dominios = defaultdict(int)
        self.contador_visitas = 0

        self.lock = threading.Lock()

    def coger_siguiente_url(self):
        with self.lock:
            while self.cola:
                url = self.cola.popleft()
                self.en_cola.discard(url)

                if url in self.visitadas:
                    continue

                dominio = obtener_dominio(url)

                if self.contador_dominios[dominio] >= self.max_por_dominio:
                    continue

                if self.contador_visitas >= self.max_paginas:
                    return None

                self.visitadas.add(url)
                self.contador_dominios[dominio] += 1
                self.contador_visitas += 1

                numero = self.contador_visitas

                print(f"Visitando [{numero}] [{dominio}]: {url}")

                return numero, url

            return None

    def meter_enlaces(self, enlaces, url_actual):
        enlaces_ordenados = ordenar_enlaces(enlaces, url_actual)

        with self.lock:
            for link in enlaces_ordenados:
                if link in self.visitadas:
                    continue

                if link in self.en_cola:
                    continue

                dominio = obtener_dominio(link)

                if self.contador_dominios[dominio] >= self.max_por_dominio:
                    continue

                self.cola.append(link)
                self.en_cola.add(link)

    def guardar_resultado(self, info):
        with self.lock:
            self.resultados.append(info)

    def worker(self):
        while True:
            siguiente = self.coger_siguiente_url()

            if siguiente is None:
                break

            numero, url = siguiente
            info = obtener_info(url)

            if not info:
                continue

            info["numero"] = numero

            self.guardar_resultado(info)
            self.meter_enlaces(info["enlaces"], url)

    def ejecutar(self, max_workers):
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            tareas = []

            for _ in range(max_workers):
                tareas.append(executor.submit(self.worker))

            for tarea in tareas:
                tarea.result()

        self.resultados.sort(key=lambda x: x["numero"])
        return self.resultados


def guardar_markdown(resultados, archivo="informe_links.md"):
    with open(archivo, "w", encoding="utf-8") as f:
        f.write("# Informe de enlaces rastreados\n\n")

        for item in resultados:
            f.write(f"## {item['numero']}. {item['titulo']}\n\n")
            f.write(f"**Dominio:** {item['dominio']}\n\n")
            f.write(f"**URL:** {item['url']}\n\n")
            f.write(f"**Descripción:** {item['descripcion']}\n\n")
            f.write("---\n\n")

    print(f"\nInforme guardado en: {archivo}")


if __name__ == "__main__":
    url = input("Link inicial: ").strip()
    workers = input("Cantidad de hilos rápidos (ej: 20, 40, 60): ").strip()
    limite = input("Cantidad máxima de links mirados (ej: 300): ").strip()
    max_por_dominio = input("Máximo aproximado de visitas por dominio raíz (ej: 3): ").strip()

    try:
        workers = int(workers)
    except:
        workers = 30

    try:
        limite = int(limite)
    except:
        limite = 300

    try:
        max_por_dominio = int(max_por_dominio)
    except:
        max_por_dominio = 3

    crawler = Crawler(
        url_inicial=url,
        max_paginas=limite,
        max_por_dominio=max_por_dominio
    )

    resultados = crawler.ejecutar(max_workers=workers)

    guardar_markdown(resultados)
