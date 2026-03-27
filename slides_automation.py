# slides_automation.py — Integración con Google Slides API: leer plantillas, copiar presentación, rellenar marcadores (#) y eliminar identificadores ($).

import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GoogleSlidesAutomation:
    """
    Cliente para Google Slides API y Drive.
    Usa credenciales de cuenta de servicio (credentials.json).
    """
    # Inicializa el cliente con la ruta al JSON de credenciales de la cuenta de servicio de Google.
    def __init__(self, credentials_path: str) -> None:
        self.credentials_path = credentials_path
        self.service: Any = self._initialize_service()

    # Inicializa la conexión con Google Slides API y Drive usando el archivo de credenciales.
    def _initialize_service(self) -> Any:
        SCOPES = [
            "https://www.googleapis.com/auth/presentations",
            "https://www.googleapis.com/auth/drive",
        ]
        self._credentials = service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=SCOPES
        )
        self._drive_service: Any = cast(Any, build("drive", "v3", credentials=self._credentials))
        service = cast(Any, build("slides", "v1", credentials=self._credentials))
        logger.info("✓ Conexión exitosa con Google Slides API")
        return service

    # -------------------------------------------------------------------------
    # Extracción de IDs y marcadores
    # -------------------------------------------------------------------------

    @staticmethod
    # Extrae el ID de la presentación desde una URL de Google Slides.
    def _extract_presentation_id(url: str) -> str:
        match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
        if match:
            return match.group(1)
        raise ValueError(f"No se pudo extraer ID de presentación de: {url}")

    @staticmethod
    # Extrae el ID de carpeta desde una URL de Drive o devuelve el valor si ya es un ID.
    def _extract_folder_id(folder_url_or_id: str) -> str:
        if not folder_url_or_id:
            return ""
        m = re.search(r"/folders/([a-zA-Z0-9_-]+)", folder_url_or_id)
        if m:
            return m.group(1)
        m2 = re.search(r"id=([a-zA-Z0-9_-]+)", folder_url_or_id)
        if m2:
            return m2.group(1)
        if re.match(r"^[a-zA-Z0-9_-]+$", folder_url_or_id):
            return folder_url_or_id
        return ""

    @staticmethod
    # Extrae de un elemento de slide todos los marcadores que coinciden con el patrón $nombre o #nombre según el valor de marker.
    def _extract_markers_from_element(element: Dict, marker: str) -> Set[str]:
        markers: Set[str] = set()
        if "shape" in element and "text" in element["shape"]:
            for paragraph in element["shape"]["text"].get("textElements", []):
                text = paragraph.get("textRun", {}).get("content", "")
                markers.update(re.findall(rf"\{marker}\w+", text))
        if "table" in element:
            for row in element["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    for paragraph in cell.get("text", {}).get("textElements", []):
                        text = paragraph.get("textRun", {}).get("content", "")
                        markers.update(re.findall(rf"\{marker}\w+", text))
        return markers

    @staticmethod
    # Recorre todos los elementos de una slide y devuelve el conjunto de marcadores encontrados (por defecto # para placeholders).
    def _find_all_components_in_slide(slide: Dict, marker: str = "#") -> Set[str]:
        components: Set[str] = set()
        for element in slide.get("pageElements", []):
            components.update(
                GoogleSlidesAutomation._extract_markers_from_element(element, marker)
            )
        return components

    # -------------------------------------------------------------------------
    # Lectura de la presentación
    # -------------------------------------------------------------------------

    # Obtiene la lista de todas las slides de la presentación con su índice, objectId e identificadores $ encontrados en cada una.
    def get_presentation_slides(self, presentation_url: str) -> List[Dict]:
        presentation_id = self._extract_presentation_id(presentation_url)
        presentation = self.service.presentations().get(
            presentationId=presentation_id
        ).execute()
        slides = presentation.get("slides", [])
        result = []
        for i, slide in enumerate(slides):
            identifiers = list(self._find_all_components_in_slide(slide, "$"))
            result.append({
                "index": i,
                "objectId": slide["objectId"],
                "identifiers": identifiers,
                "pageElements": len(slide.get("pageElements", [])),
            })
        return result

    # -------------------------------------------------------------------------
    # Copia y reordenamiento
    # -------------------------------------------------------------------------

    # Copia la presentación completa a la carpeta de Drive indicada. Devuelve el ID de la nueva presentación.
    def copy_presentation_to_folder(
        self,
        presentation_url: str,
        folder_url_or_id: str,
        new_name: str = None,
    ) -> str:
        src_id = self._extract_presentation_id(presentation_url)
        src_file = self._drive_service.files().get(
            fileId=src_id, fields="name", supportsAllDrives=True
        ).execute()
        base_name = src_file.get("name", "Presentation")
        target_name = new_name or f"Copy of {base_name}"
        folder_id = self._extract_folder_id(folder_url_or_id)
        copy_body = {"name": target_name}
        if folder_id:
            copy_body["parents"] = [folder_id]
        try:
            new_file = self._drive_service.files().copy(
                fileId=src_id, body=copy_body, supportsAllDrives=True
            ).execute()
        except HttpError as e:
            logger.warning("Advertencia al copiar con parents: %s", getattr(e, "content", str(e)))
            if "parents" in copy_body:
                copy_body.pop("parents", None)
                new_file = self._drive_service.files().copy(
                    fileId=src_id, body=copy_body, supportsAllDrives=True
                ).execute()
            else:
                raise
        new_id = new_file.get("id")
        logger.info("✓ Copia creada en Drive: %s", new_id)
        return new_id

    # Crea una copia de la presentación en la carpeta indicada y, si se pasa  slide_sequence, reordena las slides para que queden en ese orden 
    # (duplicando las que hagan falta y eliminando las originales sobrantes).  slide_counts se ignora si slide_sequence no es None.
    def copy_presentation_advanced(
        self,
        presentation_url: str,
        folder_url_or_id: str,
        new_name: str = None,
        slide_sequence: List[int] = None,
    ) -> str:
        new_id = self.copy_presentation_to_folder(
            presentation_url, folder_url_or_id, new_name
        )
        if slide_sequence is not None:
            self._reorder_slides_by_sequence(new_id, slide_sequence)
        return new_id

    # Reconstruye la presentación: duplica las slides según la secuencia de índices, elimina las originales y reordena las nuevas. Así se obtiene solo las slides deseadas en el orden indicado.
    def _reorder_slides_by_sequence(self, presentation_id: str, sequence: List[int]) -> None:
        presentation = self.service.presentations().get(
            presentationId=presentation_id
        ).execute()
        original_slides = presentation.get("slides", [])
        if not original_slides:
            return
        original_count = len(original_slides)
        requests = []
        requested_new_ids = []
        for slide_index in sequence:
            if 0 <= slide_index < original_count:
                source_id = original_slides[slide_index]["objectId"]
                new_slide_id = f"gen_slide_{uuid.uuid4().hex}"
                requests.append({
                    "duplicateObject": {
                        "objectId": source_id,
                        "objectIds": {source_id: new_slide_id},
                    }
                })
                requested_new_ids.append(new_slide_id)
            else:
                logger.warning("Índice de slide %s fuera de rango, se omite", slide_index)
        if requests:
            self.service.presentations().batchUpdate(
                presentationId=presentation_id, body={"requests": requests}
            ).execute()
            logger.info("✓ Duplicadas %s slides según la secuencia", len(requested_new_ids))
        delete_requests = [
            {"deleteObject": {"objectId": slide["objectId"]}}
            for slide in original_slides
        ]
        if delete_requests:
            self.service.presentations().batchUpdate(
                presentationId=presentation_id, body={"requests": delete_requests}
            ).execute()
            logger.info("✓ Eliminadas slides originales")
        reorder_requests = [
            {
                "updateSlidesPosition": {
                    "slideObjectIds": [slide_id],
                    "insertionIndex": desired_index,
                }
            }
            for desired_index, slide_id in enumerate(requested_new_ids)
        ]
        if reorder_requests:
            self.service.presentations().batchUpdate(
                presentationId=presentation_id, body={"requests": reorder_requests}
            ).execute()
            logger.info("✓ Reordenadas las slides")

    # -------------------------------------------------------------------------
    # Rellenar placeholders (#) y limpiar identificadores ($)
    # -------------------------------------------------------------------------

    # Normaliza las claves del diccionario de reemplazos (sin #, lower) y detecta valores semánticos para title/description como fallback.
    def _normalize_replacements(
        self, replacements: Dict[str, str]
    ) -> Tuple[Dict[str, str], Dict[str, Optional[str]]]:
        normalized: Dict[str, str] = {}
        semantic: Dict[str, Optional[str]] = {"title": None, "description": None}
        for key, value in replacements.items():
            if value is None:
                continue
            base = (key[1:] if key.startswith("#") else key).lower()
            normalized[base] = value
            if any(tag in base for tag in ["title", "titulo", "heading", "main"]):
                semantic["title"] = semantic["title"] or value
            if any(tag in base for tag in ["description", "descripcion", "body", "texto"]):
                semantic["description"] = semantic["description"] or value
        return normalized, semantic

    # Genera las solicitudes replaceAllText para reemplazar cada marcador #  por el valor correspondiente del JSON (o el semántico si no hay clave exacta).
    def _build_component_requests(
        self,
        slide_id: str,
        target_components: Set[str],
        normalized_replacements: Dict[str, str],
        semantic: Dict[str, Any],
    ) -> Tuple[List[Dict], List[str]]:
        requests: List[Dict] = []
        applied: List[str] = []
        for marker in target_components:
            base = marker.lstrip("#").lower()
            value = None
            if base in normalized_replacements:
                value = normalized_replacements[base]
            elif any(t in base for t in ["title", "titulo", "heading", "main"]):
                value = semantic.get("title")
            elif any(t in base for t in ["description", "descripcion", "body", "texto"]):
                value = semantic.get("description")
            else:
                value = semantic.get("description") or semantic.get("title")
            if value is not None:
                requests.append({
                    "replaceAllText": {
                        "containsText": {"text": marker, "matchCase": False},
                        "replaceText": value,
                        "pageObjectIds": [slide_id],
                    }
                })
                applied.append(marker)
        return requests, applied

    # Genera solicitudes para eliminar los identificadores $ de la slide una vez usados (dejar la presentación limpia para el usuario).
    def _build_identifier_cleanup_requests(
        self, slide_id: str, identifiers: Set[str]
    ) -> List[Dict]:
        return [
            {
                "replaceAllText": {
                    "containsText": {"text": ident, "matchCase": False},
                    "replaceText": "",
                    "pageObjectIds": [slide_id],
                }
            }
            for ident in identifiers
        ]

    # Rellena los placeholders (#) de la slide indicada por índice con los valores del diccionario replacements. Si remove_identifiers es True,  también borra los identificadores $ de esa slide.  
    def replace_components_in_slide_by_index(
        self,
        presentation_url: str,
        slide_index: int,
        replacements: Dict[str, str],
        remove_identifiers: bool = True,
    ) -> Dict[str, Any]:
        presentation_id = self._extract_presentation_id(presentation_url)
        presentation = self.service.presentations().get(
            presentationId=presentation_id
        ).execute()
        slides = presentation.get("slides", [])
        if slide_index < 0 or slide_index >= len(slides):
            raise ValueError(
                f"slide_index {slide_index} fuera de rango (0-{len(slides) - 1})."
            )
        slide = slides[slide_index]
        slide_id = slide["objectId"]
        target_components = {m.lower() for m in self._find_all_components_in_slide(slide, "#")}
        target_identifiers = self._find_all_components_in_slide(slide, "$")
        normalized_replacements, semantic = self._normalize_replacements(replacements)
        replacement_requests, applied = self._build_component_requests(
            slide_id, target_components, normalized_replacements, semantic
        )
        if not replacement_requests:
            if not target_components:
                # Sin marcadores # — si hay $ para limpiar, solo hacemos eso
                if remove_identifiers and target_identifiers:
                    cleanup = self._build_identifier_cleanup_requests(slide_id, target_identifiers)
                    self.service.presentations().batchUpdate(
                        presentationId=presentation_id, body={"requests": cleanup}
                    ).execute()
                    logger.info("✓ Eliminados $ en slide %s (sin marcadores #)", slide_index)
                return {
                    "presentation_id": presentation_id,
                    "slide_index": slide_index,
                    "replaced": [],
                }
            raise ValueError(
                f"En la slide {slide_index} hay marcadores # pero no hubo coincidencia con el JSON."
            )
        requests: List[Dict] = list(replacement_requests)
        if remove_identifiers and target_identifiers:
            requests.extend(
                self._build_identifier_cleanup_requests(slide_id, target_identifiers)
            )
        self.service.presentations().batchUpdate(
            presentationId=presentation_id, body={"requests": requests}
        ).execute()
        logger.info(
            "✓ Reemplazados %s componentes en slide %s%s",
            len(replacement_requests),
            slide_index,
            " y eliminados $" if remove_identifiers and target_identifiers else "",
        )
        return {
            "presentation_id": presentation_id,
            "slide_index": slide_index,
            "replaced": applied,
        }
