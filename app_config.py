"""
Editá los valores _DEFAULT_* (o usá .env) — un solo lugar para plantilla, carpeta, Gemini.
"""
import os

# --- Presentación plantilla y Drive (pegá aquí tus URLs) ---
_DEFAULT_PRESENTATION_URL = "https://docs.google.com/presentation/d/1Q1PtD0eAKaNlWA6fDev4naT1bzNsxZbQRsdbnTGA2D8/edit"
_DEFAULT_DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/1KLSmUUSGL0QyGC_hGywib-IXeog2Dj4m"
_DEFAULT_NEW_NAME = "Presentación generada desde texto"

# --- Gemini: modelo y ritmo (RPM más alto = menos espera entre llamadas; Flash free ~15/min) ---
_DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
_DEFAULT_GEMINI_RPM = "15"

DEFAULT_PRESENTATION_URL = os.getenv("DEFAULT_PRESENTATION_URL", _DEFAULT_PRESENTATION_URL)
DEFAULT_DRIVE_FOLDER_URL = os.getenv("DEFAULT_DRIVE_FOLDER_URL", _DEFAULT_DRIVE_FOLDER_URL)
DEFAULT_NEW_NAME = os.getenv("DEFAULT_NEW_PRESENTATION_NAME", _DEFAULT_NEW_NAME)
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", _DEFAULT_GEMINI_MODEL)
DEFAULT_GEMINI_RPM = os.getenv("GEMINI_RPM_LIMIT", _DEFAULT_GEMINI_RPM)
