#!/usr/bin/env python3
# src/scrapers/linkedin_profile.py

from __future__ import annotations
import json
import time
import logging
import os
import platform
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# BeautifulSoup
from bs4 import BeautifulSoup

# Local Imports
from src.base_scraper import BaseScraper

@dataclass
class LinkedInProfileData:
    """Estructura de datos para el perfil."""
    about: str
    experience: List[Dict[str, str]]
    education: List[Dict[str, str]]
    certifications: List[Dict[str, str]]

class LinkedInProfileScraper(BaseScraper):
    def __init__(self, config_dir: Path, env_name: str = "dev"):
        super().__init__(config_dir, "linkedin", env_name=env_name)
        
        self.env_name = env_name
        
        # Credenciales y Config
        self.email = self.config.get("linkedin.email") or self.config.get("LINKEDIN_EMAIL")
        self.password = self.config.get("linkedin.password") or self.config.get("LINKEDIN_PASSWORD")
        self.profile_url = self.config.get("linkedin.profile_url") or self.config.get("LINKEDIN_PROFILE_URL")
        
        # Determinar URL base
        if not self.profile_url:
            self.profile_url = "https://www.linkedin.com/in/me/" 
        elif "/in/" not in self.profile_url:
            self.profile_url = f"https://www.linkedin.com/in/{self.profile_url.strip('/')}/"

        self.profile_url = self.profile_url.rstrip("/")

        if not self.email or not self.password:
            raise ValueError("Faltan credenciales de LinkedIn (EMAIL/PASSWORD).")
            
        self.driver: Optional[webdriver.Chrome] = None
        self.login_url = "https://www.linkedin.com/login"
        self.timeout = self.config.get_int("linkedin.timeout", 60)
        self.wait_timeout = self.config.get_int("linkedin.wait_timeout", 20)

    def _is_arm_architecture(self) -> bool:
        """Detecta si estamos en Raspberry Pi / ARM."""
        arch = platform.machine().lower()
        return any(a in arch for a in ["arm", "aarch64", "armv"])

    def _make_driver(self):
        """
        Inicializa el driver.
        """
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        
        options.page_load_strategy = 'normal'
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1366,768")
        
        # Rutas de datos persistentes (prod)
        if self.env_name == "prod":
            data_dir = "/var/lib/personal-track/chromium_data_linkedin"
            options.add_argument(f"--user-data-dir={data_dir}/user-data")
            options.add_argument(f"--disk-cache-dir={data_dir}/cache")
        
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # 1. Buscar binario de Chromium (navegador)
        chromium_binaries = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/lib/chromium/chromium",
        ]
        for path in chromium_binaries:
            if os.path.exists(path):
                options.binary_location = path
                self.logger.info(f"Usando binario Chromium: {path}")
                break

        # 2. Buscar binario de Chromedriver (driver)
        is_arm = self._is_arm_architecture()
        if is_arm:
            arm_driver_paths = [
                "/usr/bin/chromedriver",
                "/usr/lib/chromium-browser/chromedriver",
                "/usr/lib/chromium/chromedriver",
            ]
            for path in arm_driver_paths:
                if os.path.exists(path):
                    self.logger.info(f"Usando driver ARM detectado: {path}")
                    try:
                        service = Service(executable_path=path)
                        self.driver = webdriver.Chrome(service=service, options=options)
                        self.driver.set_page_load_timeout(self.timeout)
                        return
                    except Exception as e:
                        self.logger.warning(f"Fallo al iniciar driver ARM {path}: {e}")
                        continue

        # 3. Fallback estándar (PC / Mac)
        try:
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(self.timeout)
        except Exception as e:
            # Último intento manual para Arch en PC si Selenium Manager falla
            self.logger.warning(f"Selenium Manager falló: {e}. Intentando fallback manual...")
            try:
                service = Service("/usr/bin/chromedriver")
                self.driver = webdriver.Chrome(service=service, options=options)
            except Exception as final_e:
                raise RuntimeError(f"No se pudo iniciar el driver: {final_e}")

    def _login(self):
        """Login original del script que funcionaba."""
        d = self.driver
        d.get(self.login_url)

        # Check rápido de sesión
        try:
            WebDriverWait(d, 5).until(
                EC.presence_of_element_located((By.ID, "username"))
            )
        except:
            pass

        if "feed" in d.current_url or "nav-item" in d.page_source:
            self.logger.info("Sesión detectada, saltando login.")
            return

        try:
            # Manejo de 'Welcome Back'
            try:
                other_account = d.find_element(By.CLASS_NAME, "signin-other-account")
                other_account.click()
                time.sleep(1)
            except:
                pass

            user_field = d.find_element(By.ID, "username")
            user_field.clear()
            user_field.send_keys(self.email)
            d.find_element(By.ID, "password").send_keys(self.password)
            d.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
            
            WebDriverWait(d, 30).until(lambda x: "feed" in x.current_url or "challenge" in x.current_url)
            
            if "challenge" in d.current_url:
                raise RuntimeError("LinkedIn Challenge detectado. Requiere intervención manual.")
                
            self.logger.info("Login realizado con éxito.")
            
        except Exception as e:
            if "feed" in d.current_url:
                return 
            raise RuntimeError(f"Fallo en login: {e}")

    def _get_soup(self, url: str, wait_selector: Optional[Tuple[str, str]] = None, wait_time: Optional[int] = None) -> BeautifulSoup:
        """Navegación y espera visual con tolerancia a timeouts."""
        self.logger.info(f"Navegando a: {url}")
        wait_selector = wait_selector or (By.CSS_SELECTOR, ".pvs-list, #profile-content, .artdeco-card, footer")
        wait_time = wait_time or self.wait_timeout

        try:
            self.driver.get(url)
        except TimeoutException:
            self.logger.warning(f"Timeout de carga en {url}, usando HTML parcial...")
            try:
                self.driver.execute_script("window.stop();")
            except Exception:
                pass
        
        try:
            # Espera genérica a que cargue algo de contenido
            WebDriverWait(self.driver, wait_time).until(
                EC.presence_of_element_located(wait_selector)
            )
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
            time.sleep(2) 
            self.driver.execute_script("window.scrollTo(0, 0);")
        except TimeoutException:
            self.logger.warning(f"Timeout esperando carga visual de {url}, parseando lo que haya...")

        return BeautifulSoup(self.driver.page_source, "html.parser")

    def _extract_about_from_soup(self, soup: BeautifulSoup) -> str:
        """Extrae About desde un soup ya cargado."""
        about_anchor = soup.find("div", {"id": "about"})
        if not about_anchor:
            return ""

        section = about_anchor.find_parent("section")
        if not section:
            return ""

        text_container = section.find(
            lambda tag: tag.name in ["div", "span"]
            and tag.get("class")
            and any("inline-show-more-text" in c for c in tag.get("class"))
        )

        if text_container:
            hidden_span = text_container.find("span", class_="visually-hidden")
            if hidden_span:
                return hidden_span.get_text(" ", strip=True)
            
            visible_span = text_container.find("span", {"aria-hidden": "true"})
            if visible_span:
                return visible_span.get_text(" ", strip=True)
            
            return text_container.get_text(" ", strip=True)

        return ""

    def _parse_about(self) -> str:
        """Extrae el texto de la sección 'Acerca de' con fallback."""
        try:
            soup = self._get_soup(self.profile_url, wait_time=self.config.get_int("linkedin.about_wait", self.wait_timeout + 10))
            about_text = self._extract_about_from_soup(soup)
            if about_text:
                return about_text

            # Fallback a página más ligera de detalles/about
            about_details_url = f"{self.profile_url}/details/about/"
            soup = self._get_soup(
                about_details_url,
                wait_selector=(By.CSS_SELECTOR, ".pvs-list, .artdeco-card"),
                wait_time=self.config.get_int("linkedin.about_wait", self.wait_timeout + 10),
            )
            return self._extract_about_from_soup(soup)
        except Exception as e:
            self.logger.warning(f"No se pudo extraer About: {e}")
            return ""

    def _parse_list_page(self, endpoint: str) -> List[Dict[str, str]]: 
        """
        Extrae ítems usando lógica posicional sobre elementos de accesibilidad.
        Estrategia: Si ya tenemos Título, Subtítulo y Meta en los índices 0, 1 y 2,
        todo lo que sobre (índices 3+) es parte del cuerpo (Descripción, Ubicación o Skills).
        """
        full_url = f"{self.profile_url}/details/{endpoint}/"
        soup = self._get_soup(full_url)
        
        items = []
        
        main_list = soup.find("div", class_="pvs-list__container")
        if not main_list:
            self.logger.warning(f"No se encontró lista PVS en {endpoint}")
            return []

        for li in main_list.find_all("li", class_="pvs-list__paged-list-item"):
            try:
                item_data = {}
                
                # Buscamos TODOS los spans ocultos, que contienen la estructura semántica real
                hidden_spans = li.find_all("span", class_="visually-hidden")
                
                # 0: Título (Rol)
                if len(hidden_spans) >= 1:
                    item_data['title'] = hidden_spans[0].get_text(strip=True)
                
                # 1: Subtítulo (Empresa / Institución)
                if len(hidden_spans) >= 2:
                    raw_subtitle = hidden_spans[1].get_text(strip=True)
                    # Limpieza común: a veces trae " · Jornada completa", lo quitamos si queremos solo la empresa
                    item_data['subtitle'] = raw_subtitle.split("·")[0].strip() 
                
                # 2: Meta (Fechas / Duración)
                if len(hidden_spans) >= 3:
                    item_data['meta'] = hidden_spans[2].get_text(strip=True)

                # Si hay más de 3 elementos, son la descripción, la ubicación o las aptitudes.
                # Los unimos todos con saltos de línea para no perder nada.
                description_parts = []
                if len(hidden_spans) > 3:
                    for span in hidden_spans[3:]:
                        text = span.get_text(" ", strip=True)
                        # Filtros básicos para evitar ruido del sistema
                        if text and "ver más" not in text.lower():
                            description_parts.append(text)
                
                # Fallback: Si por alguna razón no hay hidden spans extras, buscamos la clase visual
                # Esto ayuda si LinkedIn decide no poner la descripción en hidden (raro, pero posible)
                if not description_parts:
                    outer_desc = li.find("div", class_="display-flex align-items-center")
                    # A veces la descripción está en un div hermano directo con la clase inline-show-more-text
                    inline_text = li.select_one(".inline-show-more-text")
                    if inline_text:
                         description_parts.append(inline_text.get_text(" ", strip=True))

                item_data['description'] = "\n".join(description_parts).strip()

                if item_data.get('title'):
                    items.append(item_data)
                    
            except Exception as e:
                self.logger.warning(f"Error parseando item en {endpoint}: {e}")
                continue

        self.logger.info(f"Extraídos {len(items)} elementos de {endpoint}")
        return items

    def fetch_data(self) -> LinkedInProfileData:
        self._make_driver()
        if not self.driver:
            raise RuntimeError("No se pudo iniciar el driver.")

        try:
            self._login()
            
            about_text = self._parse_about()
            experience_data = self._parse_list_page("experience")
            education_data = self._parse_list_page("education")
            certifications_data = self._parse_list_page("certifications")

            return LinkedInProfileData(
                about=about_text,
                experience=experience_data,
                education=education_data,
                certifications=certifications_data
            )

        except Exception as e:
            self.logger.error(f"Error fatal en scraping: {e}", exc_info=True)
            return None
            
        finally:
            if self.driver:
                self.driver.quit()

    def save_data(self, data: LinkedInProfileData) -> Optional[Path]:
        if not data:
            return None
        return super().save_data([asdict(data)])
