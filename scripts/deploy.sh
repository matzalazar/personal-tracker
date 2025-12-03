#!/usr/bin/env bash
set -euo pipefail

echo "Iniciando despliegue FHS de Personal-Tracker"

# 1. Detección de rutas
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REPO_ROOT=$( dirname "$SCRIPT_DIR" )

# 2. Variables de Destino (FHS)
DEST_CODE="/opt/personal-track"
DEST_VENV="$DEST_CODE/.venv"
DEST_ETC="/etc/personal-track"
DEST_VAR="/var/lib/personal-track"
DEST_LOG="/var/log/personal-track"
SERVICE_USER="track"

echo "Fuente: $REPO_ROOT"
echo "Usuario del Servicio: $SERVICE_USER"
echo "Destinos:"
echo "  -> Código: $DEST_CODE"
echo "  -> Config: $DEST_ETC"
echo "  -> Datos:  $DEST_VAR"
echo "  -> Logs:   $DEST_LOG"
echo

# 3. Instalación de Dependencias del Sistema
echo "[1/9] Instalando dependencias del sistema (Chromium/WebDriver)..."
sudo apt update || echo "Advertencia: Falló apt update. Continuando con la instalación..."
sudo apt install -y chromium chromium-driver || \
sudo apt install -y chromium chromium-chromedriver || \
sudo apt install -y chromium-browser chromium-chromedriver || \
echo "Advertencia: No se pudo instalar Chromium ni su driver."

# 4. Creación/Actualización de Usuario y Shell
echo "[2/9] Creando o actualizando usuario de sistema '$SERVICE_USER'..."
if ! id -u "$SERVICE_USER" &>/dev/null; then
    sudo useradd --system --home-dir "$DEST_VAR" --shell /bin/bash "$SERVICE_USER"
    echo "Usuario '$SERVICE_USER' creado con shell /bin/bash."
else
    echo "Usuario '$SERVICE_USER' ya existe. Asegurando shell /bin/bash..."
    sudo usermod --shell /bin/bash "$SERVICE_USER"
    echo "Shell del usuario '$SERVICE_USER' configurado a /bin/bash."
fi

# 5. Creación de Directorios FHS
echo "[3/9] Creando directorios FHS..."
sudo mkdir -p "$DEST_CODE" "$DEST_ETC" "$DEST_VAR/data" "$DEST_LOG"
sudo mkdir -p "$DEST_VAR/chromium_data_coursera"
sudo mkdir -p "$DEST_VAR/chromium_data_upso"
sudo mkdir -p "$DEST_VAR/chromium_data_linkedin"

# 6. Asignación de Permisos de Carpeta
echo "[4/9] Asignando permisos de directorios..."
sudo chown "$SERVICE_USER":"$SERVICE_USER" "$DEST_CODE"
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$DEST_VAR" "$DEST_LOG"
sudo chown root:"$SERVICE_USER" "$DEST_ETC"
sudo chmod 0750 "$DEST_CODE" "$DEST_VAR" "$DEST_VAR/data" "$DEST_LOG" "$DEST_VAR/chromium_data_coursera" "$DEST_VAR/chromium_data_upso" "$DEST_VAR/chromium_data_linkedin"
sudo chmod 0750 "$DEST_ETC"

# 7. Despliegue de Código (rsync)
echo "[5/9] Copiando código fuente con rsync (El código es la fuente de verdad)..."
sudo rsync -a --delete --exclude ".git" --exclude ".venv" "$REPO_ROOT/" "$DEST_CODE/"
sudo chown -R "$SERVICE_USER":"$SERVICE_USER" "$DEST_CODE"
sudo chmod -R 0750 "$DEST_CODE"
echo "    -> Código copiado. No se aplicarán parches."

# 8. Limpieza de Caché de Python
echo "[6/9] Limpiando caché de Python (__pycache__)..."
sudo find "$DEST_CODE" -type d -name "__pycache__" -exec rm -rf {} +
echo "    -> Caché de Python eliminada."

# 9. Despliegue de Venv
echo "[7/9] Sincronizando entorno virtual (venv)..."
sudo chown "$SERVICE_USER":"$SERVICE_USER" "$DEST_CODE"

if [ ! -d "$DEST_VENV" ]; then
    echo "    -> Creando venv en $DEST_VENV (como usuario '$SERVICE_USER')..."
    sudo -u "$SERVICE_USER" python3 -m venv "$DEST_VENV"
fi
echo "    -> Instalando/actualizando dependencias (como usuario '$SERVICE_USER')..."
sudo -u "$SERVICE_USER" "$DEST_VENV/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$DEST_VENV/bin/pip" install -r "$DEST_CODE/requirements.txt"

# 10. Despliegue de Configuración y Servicios
echo "[8/9] Copiando archivos de Configuración y Systemd..."

# 11. Copia archivos de configuración (YAML, .envs, cookies) si existen
copy_config() {
    local src="$1"
    local dst="$2"
    if [ -f "$src" ]; then
        sudo install -D -m 0640 -o root -g "$SERVICE_USER" "$src" "$dst"
        echo "    -> Copiado $(basename "$src")"
    else
        echo "    -> Saltando $(basename "$src") (no existe en el repo)"
    fi
}

copy_config "$REPO_ROOT/config/settings.yaml" "$DEST_ETC/settings.yaml"
copy_config "$REPO_ROOT/config/.env.dev" "$DEST_ETC/.env.dev"
copy_config "$REPO_ROOT/config/.env.prod" "$DEST_ETC/.env.prod"
copy_config "$REPO_ROOT/config/.env.example" "$DEST_ETC/.env.example"
find "$REPO_ROOT/config" -name "*.json" -exec sudo install -D -m 0640 -o root -g "$SERVICE_USER" {} "$DEST_ETC/" \;
echo "    -> Archivos de configuración copiados (los ausentes se ignoraron)."

# 12. Maneja el archivo de SECRETOS (.env)
ENV_FILE="$DEST_ETC/.env"
ENV_EXAMPLE="$DEST_ETC/.env.example"
if [ ! -f "$ENV_FILE" ]; then
    echo "    -> [Primera vez] Creando archivo de secretos $ENV_FILE."
    sudo cp "$ENV_EXAMPLE" "$ENV_FILE"
    sudo chown root:"$SERVICE_USER" "$ENV_FILE"
    sudo chmod 0640 "$ENV_FILE"
    echo "    -> ¡ACCIÓN REQUERIDA! Edita $ENV_FILE con tus secretos."
else
    echo "    -> Archivo de secretos $ENV_FILE ya existe, no se sobrescribe."
fi

# 13. Copia de servicios Systemd
sudo install -D -m 0644 -o root -g root \
    "$REPO_ROOT/scripts/systemd/personal-track.service" \
    "/etc/systemd/system/personal-track.service"
sudo install -D -m 0644 -o root -g root \
    "$REPO_ROOT/scripts/systemd/personal-track.timer" \
    "/etc/systemd/system/personal-track.timer"

# 14. Recarga de Systemd
echo "[9/9] Recargando daemon-reload de systemd..."
sudo systemctl daemon-reload

echo
echo "¡Despliegue completado!"
echo "---------------------------------------------------------------"
echo "PRÓXIMOS PASOS:"
echo "1. Edita el archivo de secretos (si es la primera vez):"
echo "   sudo nano $ENV_FILE"
echo "2. Habilita e inicia el timer:"
echo "   sudo systemctl enable --now personal-track.timer"
echo "3. Prueba la ejecución manual con el entorno de 'prod':"
echo "   sudo -u track $DEST_VENV/bin/python $DEST_CODE/main.py --env prod -s all"
echo "---------------------------------------------------------------"
