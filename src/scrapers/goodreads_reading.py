#!/usr/bin/env python3
# src/scrapers/goodreads_reading.py

"""
Scraper para obtener el progreso de lectura (libros "currently-reading")
desde el sitio web de Goodreads.

Utiliza scraping (BeautifulSoup) de la página de la estantería del usuario
y de la página de perfil público para obtener los porcentajes de
lectura.
"""

from __future__ import annotations

# Importaciones de la Biblioteca Estándar
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Importaciones de Terceros
import requests
from bs4 import BeautifulSoup, Tag

# Importaciones Locales
from src.base_scraper import BaseScraper


@dataclass
class BookProgress:
    """Representa un libro en progreso de lectura."""
    title: str
    author: Optional[str]
    percent: Optional[int]
    pages_read: Optional[int]
    pages_total: Optional[int]
    book_url: Optional[str]
    shelf: str = "currently-reading"


class GoodreadsReadingScraper(BaseScraper):
    """
    Scraper para progreso de lectura en Goodreads.
    
    Obtiene los libros de la estantería 'currently-reading' de un usuario
    parseando el HTML de la web pública.
    """
    
    def __init__(self, config_dir: Path, env_name: str = "dev"):
        """
        Inicializa el scraper de Goodreads.

        Args:
            config_dir: Ruta al directorio de configuración.
            env_name: Entorno de ejecución (ej. "dev", "prod").

        Raises:
            ValueError: Si falta GOODREADS_PROFILE_URL o GOODREADS_USERNAME.
        """
        super().__init__(config_dir, "goodreads", env_name=env_name)
        
        # Configuración específica de Goodreads
        self.profile_url = self.config.get("goodreads.profile_url")
        self.username = self.config.get("goodreads.username")
        self.shelf = self.config.get("goodreads.shelf", "currently-reading")
        self.per_page = self.config.get_int("goodreads.per_page", 100)
        
        if not self.profile_url and not self.username:
            raise ValueError("Configura GOODREADS_PROFILE_URL o GOODREADS_USERNAME")
            
        # Resolver URL de perfil si solo se dio el username
        if not self.profile_url and self.username:
            self.profile_url = f"https://www.goodreads.com/{self.username}"

        # Configuración de requests
        self.base_url = "https://www.goodreads.com"
        self.timeout = self.config.get_int("goodreads.timeout", 25)
        
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-ES,es;q=0.9,en-US;q=0.8,en;q=0.7",
        })

    def _extract_int(self, s: str) -> Optional[int]:
        """Extrae un entero de una cadena, manejando fallos."""
        try:
            return int(s)
        except (ValueError, TypeError):
            return None

    def _extract_percent_any(self, text: str) -> Optional[int]:
        """Captura '10%', '10 %', '35% done', etc. de una cadena."""
        if not text:
            return None
        m = re.search(r'(\d{1,3})\s?%', text)
        if m:
            v = int(m.group(1))
            return max(0, min(100, v)) # Clamp between 0 and 100
        return None

    def _extract_pages_progress(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        """
        Extrae (páginas leídas, páginas totales) de una cadena.
        Soporta "X of Y pages", "X de Y páginas", "X / Y".
        """
        if not text:
            return None, None

        # "X of Y pages"
        m = re.search(r'(\d{1,5})\s+of\s+(\d{1,5})\s+pages', text, re.I)
        if m:
            return self._extract_int(m.group(1)), self._extract_int(m.group(2))

        # "X de Y páginas"
        m = re.search(r'(\d{1,5})\s+de\s+(\d{1,5})\s+p(?:á|a)ginas', text, re.I)
        if m:
            return self._extract_int(m.group(1)), self._extract_int(m.group(2))

        # "p. X / Y"
        m = re.search(r'(?:\bp\.?\s*)?(\d{1,5})\s*/\s*(\d{1,5})\b', text, re.I)
        if m:
            return self._extract_int(m.group(1)), self._extract_int(m.group(2))

        return None, None

    def _canonical_book_url(self, url: Optional[str]) -> Optional[str]:
        """Normaliza URLs de libros (quita query params/fragments, añade base_url)."""
        if not url:
            return url
        if url.startswith("/"):
            url = f"{self.base_url}{url}"
        url = re.split(r"[?#]", url)[0] # Quitar parámetros
        return url

    def _extract_style_percent(self, node: Tag) -> Optional[int]:
        """
        Busca un porcentaje en estilos inline (ej. style="width: 42%").
        Usado para las barras de progreso.
        """
        # 1) Buscar elementos específicos de progreso
        elems = node.select('.graphBar, .progressGraph, .progress, .meter, [class*="graph"], [class*="progress"], [class*="meter"]')
        
        # 2) Búsqueda más amplia si la anterior no devolvió nada
        if not elems:
            elems = node.select('[style*="width"]')

        best: Optional[int] = None
        for el in elems:
            style = (el.get("style") or "")
            m = re.search(r'width\s*:\s*(\d{1,3})\s*%', style, re.I)
            if m:
                v = max(0, min(100, int(m.group(1))))
                # Preferimos el valor más alto (suele ser la barra "llena")
                if best is None or v > best:
                    best = v
        return best

    def _norm_title(self, t: Optional[str]) -> Optional[str]:
        """Normaliza un título de libro (lowercase, strip, espacios)."""
        if not t:
            return None
        return re.sub(r'\s+', ' ', t).strip().lower()

    def _resolve_user_id(self) -> str:
        """
        Obtiene el user_id numérico desde la URL pública del perfil.
        
        Goodreads usa un ID numérico para las estanterías (ej. /review/list/USER_ID)
        que es diferente del username (ej. /mi_usuario).
        
        Returns:
            El ID numérico del usuario.

        Raises:
            RuntimeError: Si no se puede encontrar el ID en la página de perfil.
        """
        self.logger.info(f"Resolviendo User ID desde {self.profile_url}")
        resp = self.session.get(self.profile_url, timeout=self.timeout, allow_redirects=True)
        final_url = resp.url

        # 1. Buscar en la URL final
        m = re.search(r'/user/show/(\d+)', final_url)
        if m:
            return m.group(1)

        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 2. Buscar en todos los enlaces de la página
        for a in soup.find_all("a", href=True):
            hm = re.search(r'/user/show/(\d+)', a["href"])
            if hm:
                return hm.group(1)

        # 3. Buscar en metadatos OpenGraph
        og = soup.find("meta", property="og:url")
        if og and og.get("content"):
            hm = re.search(r'/user/show/(\d+)', og["content"])
            if hm:
                return hm.group(1)

        raise RuntimeError("No se pudo resolver el user_id de Goodreads a partir del perfil.")

    def _parse_table_layout(self, soup: BeautifulSoup) -> List[BookProgress]:
        """Parsea el layout de estantería clásico (formato <table>)."""
        results: List[BookProgress] = []
        table = soup.find("table", id="books") or soup.find("table", class_=re.compile(r"\btableList\b"))
        if not table:
            return results

        rows = table.find_all("tr")
        for tr in rows:
            try:
                # Título y URL
                title = None
                book_url = None
                tcell = tr.find("td", class_=re.compile(r"\bfield\s*title\b")) or tr.find("td", class_=re.compile(r"\btitle\b"))
                if tcell:
                    a = tcell.find("a", href=True)
                    if a:
                        title = a.get_text(strip=True)
                        book_url = self._canonical_book_url(a["href"])

                # Autor
                author = None
                acell = tr.find("td", class_=re.compile(r"\bfield\s*author\b")) or tr.find("td", class_=re.compile(r"\bauthor\b"))
                if acell:
                    a = acell.find("a")
                    author = (a.get_text(strip=True) if a else acell.get_text(strip=True)) or None

                # Progreso
                percent = None
                pages_read = None
                pages_total = None

                pc_cell = tr.find("td", class_=re.compile(r"\bprogress\b"))
                
                # 1) Estilo width: XX% (barra de progreso)
                percent = self._extract_style_percent(pc_cell or tr) or percent

                # 2) Texto de la fila
                row_full = tr.get_text(" ", strip=True)
                percent = percent or self._extract_percent_any(row_full)
                rpages, tpages = self._extract_pages_progress(row_full)
                
                if rpages is not None or tpages is not None:
                    pages_read, pages_total = rpages, tpages
                    # Calcular % si no existe y tenemos páginas
                    if percent is None and rpages is not None and tpages and tpages > 0:
                        try:
                            percent = int(round((rpages / tpages) * 100))
                        except Exception:
                            pass # Evitar división por cero si tpages es 0

                if not title and not book_url:
                    continue # Fila inútil 

                results.append(BookProgress(
                    title=title or "(sin título)",
                    author=author,
                    percent=percent,
                    pages_read=pages_read,
                    pages_total=pages_total,
                    book_url=book_url,
                    shelf="currently-reading",
                ))
            except Exception:
                continue # Ignorar fila rota

        return results

    def _parse_cards_layout(self, soup: BeautifulSoup) -> List[BookProgress]:
        """Parsea el layout de estantería moderno (formato de tarjetas/divs)."""
        results: List[BookProgress] = []
        # Selectores genéricos para varios layouts de "tarjetas"
        cards = soup.select('div.bookalike.review, div.elementList, li.bookListItem, div.listWithDividers__item')
        
        for card in cards:
            try:
                a = card.find("a", href=True)
                title = a.get_text(strip=True) if a else None
                book_url = self._canonical_book_url(a["href"]) if a else None

                author = None
                auth = card.find("a", class_=re.compile("author", re.I)) or card.find("span", class_=re.compile("author", re.I))
                if auth:
                    author = auth.get_text(strip=True)

                # 1) Estilo width: XX%
                percent = self._extract_style_percent(card)

                # 2) Texto libre
                txt = card.get_text(" ", strip=True)
                percent = percent or self._extract_percent_any(txt)
                rpages, tpages = self._extract_pages_progress(txt)
                pages_read = rpages
                pages_total = tpages
                
                if percent is None and rpages is not None and tpages and tpages > 0:
                    try:
                        percent = int(round((rpages / tpages) * 100))
                    except Exception:
                        pass

                if not (title or book_url):
                    continue

                results.append(BookProgress(
                    title=title or "(sin título)",
                    author=author,
                    percent=percent,
                    pages_read=pages_read,
                    pages_total=pages_total,
                    book_url=book_url,
                    shelf="currently-reading",
                ))
            except Exception:
                continue

        return results

    def _parse_print_layout(self, html_text: str) -> List[BookProgress]:
        """Fallback: Parsea la vista de impresión (print=true)."""
        results: List[BookProgress] = []
        sp = BeautifulSoup(html_text, "html.parser")
        
        for tr in sp.select("tr"):
            try:
                a = tr.find("a", href=True)
                title = a.get_text(strip=True) if a else None
                book_url = self._canonical_book_url(a["href"]) if a else None

                author = None
                row_txt = tr.get_text(" ", strip=True)
                # El autor suele estar después de 'by'
                by_m = re.search(r'\bby\s+(.+)$', row_txt, re.I)
                if by_m:
                    author = re.split(r'\s{2,}|\s\(|\s-\s', by_m.group(1))[0].strip()

                percent = self._extract_style_percent(tr) or self._extract_percent_any(row_txt)
                rpages, tpages = self._extract_pages_progress(row_txt)
                pages_read = rpages
                pages_total = tpages
                
                if percent is None and rpages is not None and tpages and tpages > 0:
                    try:
                        percent = int(round((rpages / tpages) * 100))
                    except Exception:
                        pass

                if title or book_url:
                    results.append(BookProgress(
                        title=title or "(sin título)",
                        author=author,
                        percent=percent,
                        pages_read=pages_read,
                        pages_total=pages_total,
                        book_url=book_url,
                        shelf="currently-reading",
                    ))
            except Exception:
                continue

        return results

    def _augment_from_profile_widget(self) -> dict[str, int]:
        """
        Lee el perfil público y extrae porcentajes desde el widget 
        'currently reading' para rellenar datos faltantes.
        
        Returns:
            Un diccionario mapeando (URL o título normalizado) -> (porcentaje).
        """
        mapping: dict[str, int] = {}
        single_pct: list[int] = [] # Para casos donde solo hay un % sin libro claro

        try:
            r = self.session.get(self.profile_url, timeout=self.timeout)
            if r.status_code != 200:
                return mapping

            soup = BeautifulSoup(r.text, "html.parser")
            # Buscar el widget específico, o usar todo el body como fallback
            root = soup.find(id="currentlyReadingReviews") or soup

            # Estrategia 1: Barras de progreso (style="width: X%")
            bars = root.select("div.graphBar, .progressGraph .graphBar, [style*='width']")
            for bar in bars:
                style = (bar.get("style") or "")
                m = re.search(r"width\s*:\s*(\d{1,3})\s*%", style, re.I)
                if not m:
                    continue
                pct = max(0, min(100, int(m.group(1))))

                # Intentar encontrar el link del libro en el mismo bloque
                blk: Optional[Tag] = bar
                for _ in range(4): # Subir 4 niveles
                    if blk and blk.parent:
                        blk = blk.parent
                    else:
                        break

                a = None
                if blk:
                    a = blk.find("a", href=re.compile(r"/book/|/work/"))
                if not a:
                    # Fallback: buscar el primer link de libro en el widget
                    a = root.find("a", href=re.compile(r"/book/|/work/"))

                title = a.get_text(strip=True) if a else None
                url = self._canonical_book_url(a["href"]) if a and a.has_attr("href") else None

                if url:
                    mapping[url] = pct
                if title:
                    mapping[self._norm_title(title)] = pct
                if not url and not title:
                    single_pct.append(pct)

            # Estrategia 2: Texto tipo "(42%)"
            for a in root.select("a, span"):
                txt = a.get_text(" ", strip=True)
                m = re.search(r"\((\d{1,3})%\)", txt)
                if m:
                    pct = max(0, min(100, int(m.group(1))))
                    
                    # Buscar el link asociado
                    blk: Optional[Tag] = a
                    for _ in range(3): # Subir 3 niveles
                        if blk and blk.parent:
                            blk = blk.parent
                            
                    link = None
                    if blk:
                        link = blk.find("a", href=re.compile(r"/book/|/work/"))
                        
                    if link:
                        url = self._canonical_book_url(link.get("href"))
                        if url:
                            mapping[url] = pct
                        t = link.get_text(strip=True)
                        if t:
                            mapping[self._norm_title(t)] = pct
                    else:
                        single_pct.append(pct)

            # Si solo encontramos un % pero no pudimos asociarlo,
            # lo guardamos por si solo hay un libro en lectura.
            if not mapping and single_pct:
                mapping["__single_percent__"] = max(single_pct)

        except Exception as e:
            self.logger.warning(f"No se pudo aumentar data desde el widget de perfil: {e}")

        return mapping

    def _fetch_currently_reading(self, user_id: str) -> List[BookProgress]:
        """Descarga y parsea la shelf 'currently-reading'."""
        shelf_url = f"{self.base_url}/review/list/{user_id}?shelf=currently-reading&per_page={self.per_page}"
        self.logger.info(f"Accediendo a la estantería: {shelf_url}")
        r = self.session.get(shelf_url, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"Goodreads devolvió {r.status_code} para {shelf_url}")

        soup = BeautifulSoup(r.text, "html.parser")

        # Estrategia A) Layout de tabla
        results = self._parse_table_layout(soup)
        if results:
            self.logger.info(f"Parseando con layout de TABLA.")

        # Estrategia B) Layout de tarjetas
        if not results:
            results = self._parse_cards_layout(soup)
            if results:
                self.logger.info(f"Parseando con layout de TARJETAS.")

        # Estrategia C) Layout de impresión (fallback)
        if not results:
            self.logger.info(f"Layout normal fallido. Intentando layout de IMPRESIÓN.")
            shelf_print = f"{self.base_url}/review/list/{user_id}?shelf=currently-reading&per_page={self.per_page}&print=true"
            try:
                rp = self.session.get(shelf_print, timeout=self.timeout)
                if rp.status_code == 200:
                    results = self._parse_print_layout(rp.text)
            except Exception as e:
                self.logger.warning(f"Fallback de impresión falló: {e}")


        # Estrategia D) Aumentar datos faltantes
        # Si algunos libros no tienen %, intentar sacarlos del widget del perfil.
        need_pct = any(b.percent is None for b in results)
        if results and need_pct:
            self.logger.info("Faltan porcentajes, intentando aumentar desde el widget del perfil...")
            pct_map = self._augment_from_profile_widget()
            if pct_map:
                single = pct_map.get("__single_percent__")

                for b in results:
                    if b.percent is not None:
                        continue
                    
                    # 1. Match por URL exacta
                    if b.book_url and b.book_url in pct_map:
                        b.percent = pct_map[b.book_url]
                        continue
                        
                    # 2. Match por Título normalizado
                    tnorm = self._norm_title(b.title)
                    if tnorm and tnorm in pct_map:
                        b.percent = pct_map[tnorm]
                        continue
                        
                    # 3. Match único (si solo hay 1 libro y 1 % suelto)
                    if single is not None and len(results) == 1:
                        b.percent = single

        # Dump de debug si sigue vacío
        if not results:
            debug_path = self.outdir / "debug_last_shelf.html"
            try:
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                self.logger.warning(f"No se encontraron libros en 'currently-reading'. Dump: {debug_path}")
            except Exception as e:
                self.logger.error(f"No se pudo guardar el dump de debug: {e}")


        return results

    def fetch_data(self) -> List[BookProgress]:
        """
        Método principal para ejecutar el scraper.
        
        1. Resuelve el ID de usuario.
        2. Obtiene y parsea la estantería 'currently-reading'.
        3. Intenta aumentar datos faltantes desde el widget del perfil.
        
        Returns:
            Una lista de objetos BookProgress.
        """
        try:
            user_id = self._resolve_user_id()
            self.logger.info(f"User ID resuelto: {user_id}")
            
            data = self._fetch_currently_reading(user_id)
            self.logger.info(f"Encontrados {len(data)} libros en lectura")
            return data
        except Exception as e:
            self.logger.error(f"Falló el scraping de Goodreads: {e}", exc_info=True)
            return []