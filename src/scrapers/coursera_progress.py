#!/usr/bin/env python3
# src/scrapers/coursera_progress.py

"""
Scraper para obtener el progreso de los cursos en Coursera usando Selenium.

Este script maneja el inicio de sesión (incluyendo cookies y resolución de puzzles),
navega a la página "My Learning" y extrae los cursos en progreso
junto con su porcentaje de finalización.
"""

from __future__ import annotations

# Importaciones de la Biblioteca Estándar
import json
import logging
import os
import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Importaciones de Terceros (Selenium)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# NOTA: webdriver_manager se importa condicionalmente dentro de _make_driver
# como un fallback, para no hacerlo un requisito estricto si no se usa.

# Importaciones Locales
from src.base_scraper import BaseScraper


@dataclass
class CourseProgress:
    """Representa el progreso de un único curso en Coursera."""
    title: str
    percent: Optional[int]
    course_url: Optional[str]


class CourseraProgressScraper(BaseScraper):
    """
    Scraper para extraer el progreso de cursos en Coursera.
    
    Maneja el login, la persistencia de sesión con cookies y el parsing
    de la página "My Learning".
    """

    def __init__(self, config_dir: Path, env_name: str = "dev"):
        """
        Inicializa el scraper de Coursera.

        Args:
            config_dir: Ruta al directorio de configuración.
            env_name: Entorno de ejecución (ej. "dev", "prod").
        """
        super().__init__(config_dir, "coursera", env_name=env_name)

        self.env_name = env_name
        
        # Configuración específica de Coursera
        self.email = self.config.get("coursera.email")
        self.password = self.config.get("coursera.password")
        self.puzzle_max_wait = self.config.get_int("coursera.puzzle_max_wait", 300)
        
        if not self.email or not self.password:
            raise ValueError("Faltan las variables COURSERA_EMAIL o COURSERA_PASSWORD")
            
        self.driver: Optional[webdriver.Chrome] = None
        
        # URLs y configuraciones
        self.login_url = "https://www.coursera.org/?authMode=login&redirectTo=%2Fmy-learning"
        self.timeout = self.config.get_int("coursera.timeout", 25)
        
        # Localizadores (CSS y XPath)
        # Se usan múltiples selectores para resiliencia ante cambios en la UI.
        self.locators = {
            "email": [
                (By.CSS_SELECTOR, 'input[type="email"]'),
                (By.CSS_SELECTOR, 'input[name="email"]'),
                (By.XPATH, '//input[@autocomplete="username"]'),
            ],
            "email_continue": [
                (By.CSS_SELECTOR, 'form button[type="submit"]'),
                (By.XPATH, '//form//button[contains(.,"Continue") or contains(.,"Siguiente") or contains(.,"Next")]'),
            ],
            "password": [
                (By.CSS_SELECTOR, 'input[type="password"]'),
                (By.CSS_SELECTOR, 'input[name="password"]'),
                (By.XPATH, '//input[@autocomplete="current-password" or @type="password"]'),
            ],
            "submit": [
                (By.CSS_SELECTOR, 'form button[type="submit"]'),
                (By.XPATH, '//form//button[contains(.,"Log in") or contains(.,"Acceder") or contains(.,"Iniciar sesión") or contains(.,"Sign in")]'),
            ],
            "email_tab": [
                (By.XPATH, '//button[contains(.,"Email") or contains(.,"Correo")]'),
                (By.XPATH, '//a[contains(.,"Email") or contains(.,"Correo")]'),
            ],
            "cookie_accept": [
                (By.XPATH, '//button[contains(.,"Accept") and contains(.,"cookies")]'),
                (By.XPATH, '//button[contains(.,"Aceptar") and contains(.,"cookies")]'),
            ],
        }

    def _is_arm_architecture(self) -> bool:
        """Determina si estamos ejecutando en una arquitectura ARM (como Raspberry Pi)."""
        arch = platform.machine().lower()
        return any(a in arch for a in ["arm", "aarch64", "armv"])

    def _make_driver(self) -> None:
        """Inicializa el driver de Selenium con lógica multi-arquitectura."""
        
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        
        # Argumentos de Seguridad/Headless Agregados
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1280,1600")

        # Bloque de perfil de usuario para 'prod'
        if self.env_name == "prod":
            # Forzar a Chromium a usar directorios escribibles por el usuario 'track'.
            data_dir = "/var/lib/personal-track/chromium_data_coursera"
            options.add_argument(f"--user-data-dir={data_dir}/user-data")
            options.add_argument(f"--disk-cache-dir={data_dir}/cache")
            options.add_argument(f"--crash-dumps-dir={data_dir}/crash-dumps")

        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-features=UtilityProcessSandbox") # Necesario a veces en RPi
        options.add_argument("--disable-features=TranslateService") 
        options.add_argument("--disable-dbus") # CLAVE: Inhabilita la conexión a D-Bus
        
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Encontrar el binario de Chromium en rutas conocidas
        chromium_binaries = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/lib/chromium/chromium",
            "/usr/lib/chromium-browser/chromium-browser",
        ]
        
        found_binary = None
        for path in chromium_binaries:
            if os.path.exists(path):
                found_binary = path
                break
        
        if not found_binary and self.env_name == "prod":
             # Si estamos en prod y no encontramos ninguno, fallar rápido
             raise FileNotFoundError(f"No se encontró el binario de Chromium en ninguna ruta: {chromium_binaries}")
        elif found_binary:
             options.binary_location = found_binary
             self.logger.info(f"Usando binario de Chromium: {found_binary}")
        # Si no se encontró y estamos en 'dev', dejamos que Selenium Manager (PC) resuelva
        
        is_arm = self._is_arm_architecture()
        
        # 1. Intento RPi/ARM: Usar driver pre-instalado
        if is_arm:
            # Rutas conocidas en Debian/RPi
            arm_driver_paths = [
                "/usr/bin/chromedriver",
                "/usr/lib/chromium-browser/chromedriver",
                "/usr/lib/chromium/chromedriver",
            ]
            
            for path in arm_driver_paths:
                if os.path.exists(path):
                    self.logger.info(f"Usando chromedriver del sistema ARM: {path}")
                    try:
                        # Usamos executable_path ya que no confiamos en el PATH en servicios
                        service = Service(executable_path=path) 
                        self.driver = webdriver.Chrome(service=service, options=options)
                        self.driver.set_page_load_timeout(self.timeout)
                        return
                    except Exception as e:
                        self.logger.warning(f"Driver ARM encontrado pero falló al iniciar: {e}")
                        continue
            
            # Si el driver ARM no funciona, saltamos la descarga de WDM (x86-64)
            raise RuntimeError("Driver ARM no encontrado/funcional. La descarga automática está deshabilitada en esta arquitectura (Exec format error).")

        # 2. Intento PC (Selenium Manager)
        try:
            # Esto funciona en PC x86-64 y usa el driver descargado por Selenium Manager
            self.logger.info("Intentando iniciar driver con Selenium Manager (PC x86-64).")
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(self.timeout)
            return
        except Exception as e1:
            self.logger.warning(f"Selenium Manager falló: {e1}")

        # 3. Intento PC (WebDriver Manager - Fallback)
        try:
            # Si Selenium Manager falla en PC, intentamos con WDM
            self.logger.info("Intentando descarga con WebDriver Manager (solo para PC).")
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.set_page_load_timeout(self.timeout)
            return
        except Exception as e3:
            raise RuntimeError(f"No se pudo inicializar Chrome (PC Error): {e3}")

    def _cookies_path(self) -> Path:
        """Devuelve la ruta estandarizada para el archivo de cookies."""
        env_path = self.config.get("coursera.cookies_file")
        if env_path:
            return Path(env_path).expanduser().resolve()
        return self.config_dir / "coursera_cookies.json"

    def _refresh_and_save_cookies(self, note: str = "") -> None:
        """Refresca la página actual y guarda las cookies en el archivo JSON."""
        if not self.driver:
            return
        try:
            if "coursera.org" not in (self.driver.current_url or ""):
                self.driver.get("https://www.coursera.org/")
                time.sleep(0.8)
            else:
                self.driver.refresh()
                time.sleep(0.8)
            
            cookies = self.driver.get_cookies()
            path = self._cookies_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            
            self.logger.info(f"Cookies guardadas ({len(cookies)} items){' - ' + note if note else ''}")
        except Exception as e:
            self.logger.warning(f"No se pudieron guardar cookies ({note}): {e}")

    def _load_cookies(self) -> bool:
        """Carga las cookies desde el archivo JSON y las aplica al driver."""
        if not self.driver:
            return False
            
        path = self._cookies_path()
        if not path.exists():
            return False
            
        try:
            with open(path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            
            # Se necesita visitar el dominio antes de añadir cookies
            self.driver.get("https://www.coursera.org/")
            
            for c in cookies:
                # 'sameSite' y 'expiry' a veces causan problemas al cargar
                c.pop("sameSite", None)
                c.pop("expiry", None)
                try:
                    self.driver.add_cookie(c)
                except Exception:
                    # Ignorar cookies individuales que fallen
                    continue
                    
            self.driver.refresh()
            time.sleep(1.2)
            self.logger.info("Cookies restauradas exitosamente")
            return True
        except Exception as e:
            self.logger.warning(f"Error al cargar cookies: {e}")
            return False

    def _find_first(self, locators: List[Tuple[str, str]], timeout: Optional[int] = None) -> WebElement:
        """
        Encuentra el primer elemento que coincida con una lista de localizadores.

        Args:
            locators: Lista de tuplas (By, selector_value).
            timeout: Tiempo máximo de espera (usa self.timeout si es None).

        Returns:
            El primer WebElement encontrado.
        
        Raises:
            TimeoutError: Si ningún localizador encuentra un elemento.
        """
        timeout = timeout or self.timeout
        for by, value in locators:
            try:
                return WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
            except Exception:
                continue
        raise TimeoutError(f"No se encontró elemento con {locators=}")

    def _click_first_visible(self, locators: List[Tuple[str, str]], timeout: Optional[int] = None) -> None:
        """
        Hace click en el primer elemento clickeable de una lista de localizadores.

        Args:
            locators: Lista de tuplas (By, selector_value).
            timeout: Tiempo máximo de espera (usa self.timeout si es None).

        Raises:
            TimeoutError: Si ningún elemento es clickeable.
        """
        timeout = timeout or self.timeout
        for by, value in locators:
            try:
                el = WebDriverWait(self.driver, timeout).until(
                    EC.element_to_be_clickable((by, value))
                )
                el.click()
                return
            except Exception:
                continue
        raise TimeoutError(f"No se pudo hacer click en el elemento deseado: {locators=}")

    def _has_text(self, text: str, pattern: str) -> bool:
        """Verifica si el texto coincide con un patrón regex (case-insensitive)."""
        try:
            return re.search(pattern, text, re.I) is not None
        except Exception:
            return False

    def _ensure_in_progress_tab(self) -> None:
        """Asegura que la pestaña 'In Progress' (o 'En curso') esté seleccionada."""
        try:
            # Lista de XPaths para encontrar la pestaña "En curso"
            for xp in [
                '//button[normalize-space()="In Progress"]',
                '//button[contains(.,"In Progress")]',
                '//button[normalize-space()="En curso"]',
                '//button[contains(.,"En curso")]',
                '//a[normalize-space()="In Progress"]',
                '//a[normalize-space()="En curso"]',
            ]:
                try:
                    btn = WebDriverWait(self.driver, 2).until(EC.element_to_be_clickable((By.XPATH, xp)))
                    # Verificar si ya está seleccionada
                    aria_current = (btn.get_attribute("aria-current") or "").lower()
                    if aria_current in ("page", "true"):
                        return # Ya está seleccionada
                    
                    # Si no, hacer click
                    btn.click()
                    time.sleep(0.4)
                    return
                except Exception:
                    continue
        except Exception:
            # Si falla, no es crítico, pero se logueará si no se encuentran cursos.
            pass

    def _extract_percent_text(self, text: str) -> Optional[int]:
        """Extrae un porcentaje (ej. '85% complete') de una cadena de texto."""
        # Busca "XX% complete" o "XX% completado"
        m = re.search(r'(\d{1,3})\s?%\s*(?:complete|completado|completados?)', text, re.I)
        if m:
            v = max(0, min(100, int(m.group(1))))
            return v
        
        # Fallback: busca solo "XX%"
        m2 = re.search(r'(\d{1,3})\s?%', text)
        if m2:
            v = max(0, min(100, int(m2.group(1))))
            return v
        return None

    def _extract_percent_from_container(self, container: WebElement) -> Optional[int]:
        """Extrae el porcentaje de un contenedor de curso (progressbar, style, o texto)."""
        # 1. Intentar por 'role="progressbar"' (aria-valuenow)
        try:
            pb = container.find_element(By.CSS_SELECTOR, '[role="progressbar"]')
            now = pb.get_attribute("aria-valuenow")
            if now is not None:
                return max(0, min(100, int(float(now))))
        except Exception:
            pass
        
        # 2. Intentar por estilo (ej. style="width: 80%;")
        try:
            bar = container.find_element(By.XPATH, './/*[contains(@style,"width") and contains(@style,"%")]')
            style = bar.get_attribute("style") or ""
            m = re.search(r'width\s*:\s*(\d{1,3})\s?%', style, re.I)
            if m:
                return max(0, min(100, int(m.group(1))))
        except Exception:
            pass
            
        # 3. Intentar por texto dentro del contenedor
        try:
            return self._extract_percent_text(container.text or "")
        except Exception:
            return None

    def _find_course_rows(self, card: WebElement) -> List[WebElement]:
        """Encuentra elementos que parecen ser filas de cursos dentro de una tarjeta."""
        rows = card.find_elements(
            By.XPATH,
            # Selector genérico para listas o divs que contienen enlaces/títulos
            './/li[.//a or .//h3 or .//span] | '
            './/div[contains(@class,"row") or contains(@class,"ListItem") or .//button[contains(.,"Resume")]]'
        )
        return rows

    def _safe_text_first(self, scope: WebElement, locators: List[Tuple[str, str]]) -> Optional[str]:
        """Obtiene texto (aria-label o text) del primer elemento encontrado."""
        for by, value in locators:
            try:
                el = scope.find_element(by, value)
                # Priorizar aria-label, luego .text
                txt = (el.get_attribute("aria-label") or el.text or "").strip()
                if txt:
                    return txt
            except Exception:
                continue
        return None

    def _attr_first(self, scope: WebElement, attr: str, locators: List[Tuple[str, str]]) -> Optional[str]:
        """Obtiene un atributo del primer elemento encontrado."""
        for by, value in locators:
            try:
                el = scope.find_element(by, value)
                v = el.get_attribute(attr)
                if v:
                    return v
            except Exception:
                continue
        return None

    def _get_login_form(self) -> Optional[WebElement]:
        """Encuentra el formulario de login, ya sea en un modal o en la página."""
        # 1. Buscar en un diálogo/modal
        try:
            dialog = WebDriverWait(self.driver, 2).until(
                EC.presence_of_element_located((By.XPATH, '//div[@role="dialog"]//form'))
            )
            return dialog
        except Exception:
            pass
            
        # 2. Buscar formulario principal en la página
        try:
            form = WebDriverWait(self.driver, 2).until(
                EC.presence_of_element_located((By.XPATH, '//form[.//input[@type="email" or @name="email"]]'))
            )
            return form
        except Exception:
            pass
        return None

    def _is_logged_in(self) -> bool:
        """Verifica si el usuario parece estar logueado (busca avatar o links de perfil)."""
        try:
            # Busca varios indicadores de sesión activa en el header
            self.driver.find_element(
                By.XPATH,
                '//header//img[contains(@alt,"avatar") or contains(@src,"avatar") or contains(@src,"profile")] | '
                '//header//button[contains(@aria-label,"Account") or contains(@aria-label,"Cuenta")] | '
                '//header//a[@href="/my-learning"] | '
                '//header//a[contains(@href,"/profile")] | '
                '//header//a[contains(@href,"/user/")]'
            )
            return True
        except Exception:
            return False

    def _challenge_present(self) -> bool:
        """Detecta la presencia de un captcha, puzzle o desafío hCaptcha/Cloudflare."""
        try:
            # 1. Buscar iframes de desafío
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            for fr in iframes:
                title = (fr.get_attribute("title") or "").lower()
                src = (fr.get_attribute("src") or "").lower()
                if any(k in title for k in ("captcha", "challenge")) or \
                   any(k in src for k in ("captcha", "hcaptcha", "cf-chl")):
                    return True
            
            # 2. Buscar elementos genéricos de desafío
            self.driver.find_element(By.XPATH, '//*[contains(@class,"captcha") or contains(@id,"captcha") or contains(@class,"challenge")]')
            return True
        except Exception:
            return False

    def _my_learning_looks_loaded(self) -> bool:
        """Verifica si la página 'My Learning' parece estar completamente cargada."""
        d = self.driver
        url_ok = "/my-learning" in (d.current_url or "").lower()
        if not url_ok:
            return False
            
        # Comprobar indicadores de carga
        try:
            el = d.find_element(By.CSS_SELECTOR, '.isCurrent > span:nth-child(1) .cds-button-label')
            tab_ok = el.text.strip().lower() in ("my learning", "mi aprendizaje")
        except Exception:
            tab_ok = False
            
        try:
            h1 = d.find_element(By.XPATH, '//h1[normalize-space()="My Learning" or normalize-space()="Mi aprendizaje"]')
            h1_ok = h1.is_displayed()
        except Exception:
            h1_ok = False
            
        try:
            cards_ok = len(d.find_elements(By.XPATH, '//a[contains(@href,"/learn/") or contains(@href,"/courses/")]')) > 0
        except Exception:
            cards_ok = False
            
        # Si la pestaña, el H1 o las tarjetas están presentes, asumimos que cargó.
        return tab_ok or h1_ok or cards_ok

    def _await_puzzle_resolution(self) -> bool:
        """Espera a que el usuario resuelva un puzzle/captcha manualmente."""
        d = self.driver
        t0 = time.time()
        last_notice = -1

        if not self._challenge_present():
            self._refresh_and_save_cookies("puzzle-gone-immediate")
            return True

        self.logger.info(f"¡PUZZLE DETECTADO! Resuélvelo manualmente en la ventana del navegador.")
        self.logger.info(f"Esperando {self.puzzle_max_wait} segundos...")

        while time.time() - t0 < self.puzzle_max_wait:
            url = d.current_url or ""
            
            # 1. Éxito si vemos señal de login
            if self._is_logged_in() or "/profile" in url or "/user/" in url:
                self._refresh_and_save_cookies("login-signals-after-puzzle")
                return True
                
            # 2. Éxito si el desafío desaparece
            if not self._challenge_present():
                time.sleep(0.5) # Espera breve a que la página reaccione
                self._refresh_and_save_cookies("puzzle-gone")
                return True
                
            # Loguear progreso
            elapsed = int(time.time() - t0)
            if elapsed // 10 != last_notice // 10: # Notificar cada 10s
                self.logger.info(f"Esperando resolución del puzzle… {elapsed}s / {self.puzzle_max_wait}s")
                last_notice = elapsed
                
            time.sleep(2)
            
        self.logger.error("Timeout esperando la resolución del puzzle.")
        return False

    def _login(self) -> None:
        """Realiza el proceso completo de login en Coursera."""
        d = self.driver
        d.get(self.login_url)
        time.sleep(0.6)
        
        # 1. Comprobar si ya estamos logueados
        if self._is_logged_in() or "/my-learning" in (d.current_url or "").lower():
            self._refresh_and_save_cookies("already-in")
            return

        # 2. Aceptar cookies si aparece el banner
        try:
            self._click_first_visible(self.locators["cookie_accept"], timeout=2)
            time.sleep(0.2)
        except Exception:
            pass # No es crítico si no está

        # 3. Encontrar el formulario de login
        form = self._get_login_form()
        if form is None and (self._is_logged_in() or "/my-learning" in (d.current_url or "").lower()):
            self._refresh_and_save_cookies("no-form-but-logged-in")
            return
            
        # 4. Si no hay formulario, intentar clickear el botón "Log In" del header
        if form is None:
            try:
                btn_login = WebDriverWait(d, 4).until(EC.element_to_be_clickable((
                    By.XPATH,
                    '//header//a[normalize-space()="Log In" or normalize-space()="Sign in" or normalize-space()="Iniciar sesión"] | '
                    '//header//button[normalize-space()="Log In" or normalize-space()="Sign in" or normalize-space()="Iniciar sesión"]'
                )))
                btn_login.click()
                time.sleep(0.4)
                form = self._get_login_form() # Reintentar buscar el formulario
            except Exception:
                form = None # Seguir sin formulario

        if form is None:
            if self._is_logged_in() or "/my-learning" in (d.current_url or "").lower():
                self._refresh_and_save_cookies("logged-in-without-form")
                return
            raise RuntimeError("No se encontró el formulario de login de Coursera.")

        # 5. Asegurar que estamos en la pestaña "Email"
        try:
            within_email_tab = form.find_element(By.XPATH, './/button[contains(.,"Email") or contains(.,"Correo")]')
            within_email_tab.click()
            time.sleep(0.1)
        except Exception:
            pass # Asumir que ya está

        # 6. Ingresar Email
        email_field = self._find_first(self.locators["email"], timeout=6)
        email_field.clear()
        email_field.send_keys(self.email)
        email_field.send_keys(Keys.ENTER)

        # 7. Manejar puzzle pre-password
        if self._challenge_present():
            self.logger.info("Puzzle detectado antes del password.")
            if not self._await_puzzle_resolution():
                raise RuntimeError("Timeout esperando la resolución del puzzle (previo a password).")
            if self._is_logged_in() or "/my-learning" in (d.current_url or "").lower():
                self._refresh_and_save_cookies("after-prepass-puzzle")
                return

        # 8. Ingresar Password
        try:
            pass_field = self._find_first(self.locators["password"], timeout=10)
        except Exception:
            # A veces el ENTER en email no muestra el password si la UI es lenta
            if not self._is_logged_in():
                raise RuntimeError("No apareció el campo de contraseña tras ingresar el email.")
            else:
                pass_field = None # Ya estamos logueados

        if pass_field:
            pass_field.clear()
            pass_field.send_keys(self.password)
            pass_field.send_keys(Keys.ENTER)

        # 9. Manejar puzzle post-password
        if self._challenge_present():
            self.logger.info("Puzzle detectado tras enviar password.")
            if not self._await_puzzle_resolution():
                raise RuntimeError("Timeout esperando la resolución del puzzle (post-password).")

        # 10. Esperar confirmación de login
        try:
            WebDriverWait(d, self.timeout).until(lambda drv: self._is_logged_in() or "/my-learning" in (drv.current_url or "").lower())
        except Exception:
            if not self._is_logged_in():
                raise TimeoutError(f"Login fallido. URL actual: {d.current_url}")

        self.logger.info("Login exitoso.")
        self._refresh_and_save_cookies("post-login-final")

    def _ensure_session(self) -> None:
        """Asegura que haya una sesión activa, usando cookies o logueándose."""
        d = self.driver
        d.get("https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS")
        
        # 1. Intentar cargar con cookies
        try:
            WebDriverWait(d, 10).until(lambda drv: "/my-learning" in (drv.current_url or "").lower())
            # Forzar renderizado con scroll
            for _ in range(3):
                d.execute_script("window.scrollTo(0, document.body.scrollHeight/3);")
                time.sleep(0.25)
                d.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.25)
                if self._my_learning_looks_loaded():
                    return # Éxito con cookies
            
            WebDriverWait(d, 4).until(lambda drv: self._my_learning_looks_loaded())
            return # Éxito con cookies
        except Exception:
            pass
            
        # 2. Si falla, intentar login completo
        self.logger.info("/my-learning no cargó con cookies. Intentando login completo…")
        self._login()

    def _go_to_learning(self) -> None:
        """Navega a la página 'My Learning' y espera a que cargue."""
        d = self.driver

        d.get("https://www.coursera.org/my-learning?myLearningTab=IN_PROGRESS")

        # 1. Esperar a que la URL sea correcta y la página cargue
        try:
            WebDriverWait(d, 12).until(lambda drv: "/my-learning" in (drv.current_url or "").lower())
            for _ in range(2):
                self._ensure_in_progress_tab()
                d.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                time.sleep(0.3)
                d.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.3)
                if self._my_learning_looks_loaded():
                    return
            WebDriverWait(d, 6).until(lambda drv: self._my_learning_looks_loaded())
            return
        except Exception:
            pass # Intentar navegación manual

        # 2. Fallback: Intentar clickear el enlace "My Learning"
        self.logger.warning("No se pudo cargar /my-learning directamente, intentando click manual.")
        for xp in [
            '//a[@href="/my-learning"]',
            ('//a[span[normalize-space()="My Learning"]] | //a[normalize-space()="My Learning"] | '
             '//a[span[normalize-space()="Mi aprendizaje"]] | //a[normalize-space()="Mi aprendizaje"] | '
             '//button[normalize-space()="My Learning"] | //button[normalize-space()="Mi aprendizaje"]')
        ]:
            try:
                el = WebDriverWait(d, 6).until(EC.element_to_be_clickable((By.XPATH, xp)))
                el.click()
                WebDriverWait(d, 10).until(lambda drv: "/my-learning" in (drv.current_url or "").lower())
                self._ensure_in_progress_tab()
                # Esperar carga post-click
                for _ in range(2):
                    d.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                    time.sleep(0.3)
                    d.execute_script("window.scrollTo(0, 0);")
                    time.sleep(0.3)
                    if self._my_learning_looks_loaded():
                        return
                WebDriverWait(d, 6).until(lambda drv: self._my_learning_looks_loaded())
                return
            except Exception:
                continue

        # 3. Fallo total
        debug_path = self.outdir / "debug_last_my_learning.html"
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(d.page_source)
        except Exception as e:
            self.logger.error(f"No se pudo guardar el dump HTML: {e}")
            
        raise RuntimeError(
            f"No cargó My Learning (URL actual: {d.current_url}). Se guardó dump en {debug_path}"
        )

    def _parse_courses(self) -> List[CourseProgress]:
        """Parsea la página 'My Learning' para extraer los cursos en progreso."""
        d = self.driver
        scope = d.find_element(By.TAG_NAME, 'main') # Buscar solo en el contenido principal

        self._ensure_in_progress_tab() # Asegurar pestaña correcta
        time.sleep(0.2)

        results: List[CourseProgress] = []
        seen: set[Tuple[str, str]] = set() # (title, href)

        # 1. Estrategia: Buscar tarjetas de Especialización (que contienen cursos)
        cards = scope.find_elements(
            By.XPATH,
            './/article | .//section | '
            './/li[contains(@class,"card") or contains(@class,"Card")] | '
            './/div[contains(@class,"card") or contains(@class,"Card") or contains(@class,"Grid")]'
        )

        def _percent_from(el: WebElement) -> Optional[int]:
            p = self._extract_percent_from_container(el)
            if p is not None:
                return p
            txt = (el.text or "").strip()
            return self._extract_percent_text(txt)

        for card in cards:
            try:
                text = card.text or ""
                # Buscar tarjetas que sean Especializaciones (ej. "Curso 1 de 5")
                is_spec = self._has_text(text, r'\bCourse\s+\d+\s+of\s+\d+\b')
                if not is_spec:
                    continue

                spec_percent = _percent_from(card) # Progreso general de la especialización
                rows = self._find_course_rows(card) # Cursos dentro de la especialización
                
                for row in rows:
                    try:
                        title = self._safe_text_first(row, [(By.XPATH, './/h2|.//h3|.//h4|.//a|.//span')])
                        href = self._attr_first(row, 'href', [(By.CSS_SELECTOR, 'a[href*="/learn/"], a[href*="/courses/"]')])
                        
                        percent = _percent_from(row) # Progreso específico del curso
                        if percent is None and self._has_text(row.text, r'\bNot\s+started\b|\bNo\s+iniciado\b'):
                            percent = 0
                        if percent is None:
                            percent = spec_percent # Usar progreso de la spec como fallback
                            
                        if title and href:
                            key = (title, href)
                            if key not in seen:
                                seen.add(key)
                                results.append(CourseProgress(title=title, percent=percent, course_url=href))
                    except Exception:
                        continue # Ignorar fila individual
            except Exception:
                continue # Ignorar tarjeta individual

        # 2. Estrategia: Buscar todos los enlaces de cursos (fallback)
        anchors = scope.find_elements(By.CSS_SELECTOR, 'a[href*="/learn/"], a[href*="/courses/"]')

        def _nearest_card(a: WebElement) -> WebElement:
            # Encuentra el contenedor "padre" más cercano que contenga la info
            for xp in [
                './ancestor::article[1]',
                './ancestor::li[1]',
                './ancestor::section[1]',
                './ancestor::div[contains(@class,"card") or contains(@class,"Card") or contains(@class,"Row") or contains(@class,"Grid")][1]'
            ]:
                try:
                    return a.find_element(By.XPATH, xp)
                except Exception:
                    continue
            return a # Devolver el mismo anchor como último recurso

        for a in anchors:
            try:
                href = a.get_attribute("href")
                title = (a.get_attribute("aria-label") or a.text or "").strip()
                card = _nearest_card(a) # Contenedor del enlace

                percent = _percent_from(card)
                
                if percent is None:
                    # Filtro: si no tiene %, al menos debe decir "Resume"
                    txt = (card.text or "").lower()
                    if ("resume" not in txt) and ("continuar" not in txt) and ("% " not in txt and "%" not in txt):
                        continue # Probablemente un curso no iniciado o completado

                if not title:
                    # Intentar buscar un título mejor en el contenedor
                    title = self._safe_text_first(card, [(By.XPATH, './/h2|.//h3|.//h4|.//span')]) or title

                if not href or not title:
                    continue # Dato inútil

                key = (title, href)
                if key in seen:
                    continue # Ya lo procesamos en la estrategia 1
                    
                seen.add(key)
                results.append(CourseProgress(title=title, percent=percent, course_url=href))
            except Exception:
                continue # Ignorar anchor individual

        # 3. Logueo de depuración si no se encuentra nada
        if not results:
            try:
                debug_path = self.outdir / "debug_last_my_learning.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(d.page_source)
                self.logger.warning(f"No se hallaron cursos en progreso. Se guardó dump en: {debug_path}")
                try:
                    total_anchors = len(anchors)
                    self.logger.warning(f"Total anchors '/learn|/courses' encontrados: {total_anchors}")
                except Exception:
                    pass
            except Exception as e:
                self.logger.error(f"No se pudo guardar el dump HTML de depuración: {e}")

        return results

    def fetch_data(self) -> List[CourseProgress]:
        """
        Método principal para ejecutar el scraper.
        
        Inicializa el driver, asegura la sesión (login/cookies) y
        parsea los cursos en progreso.
        
        Returns:
            Lista de objetos CourseProgress.
        """
        self._make_driver()
        if not self.driver:
            raise RuntimeError("El driver de Selenium no se inicializó correctamente.")
            
        try:
            cookies_ok = self._load_cookies()
            if cookies_ok:
                self.logger.info("Cookies cargadas. Probando acceso directo a My Learning…")
            else:
                self.logger.info("Sin cookies previas. Se procederá con el login.")

            # Asegurar sesión (usa cookies o hace login)
            self._ensure_session()
            
            # Navegar a la página de cursos
            self._go_to_learning()
            
            # Extraer los datos
            data = self._parse_courses()
            
            self.logger.info(f"Scraping finalizado. {len(data)} cursos en progreso encontrados.")
            return data
        except Exception as e:
            self.logger.error(f"Falló el scraping de Coursera: {e}", exc_info=True)
            # Guardar dump en caso de error inesperado
            try:
                debug_path = self.outdir / f"debug_error_{int(time.time())}.html"
                with open(debug_path, "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                self.logger.info(f"Se guardó dump del error en {debug_path}")
            except Exception as de:
                self.logger.error(f"No se pudo guardar el dump del error: {de}")
            return [] # Devolver lista vacía en caso de error
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None