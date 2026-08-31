"""Non-destructive guidance for building useful search profiles."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from auction_watch.core.models import SearchProfile


class GuidanceCriteria(BaseModel):
    """Accept incomplete draft criteria before SearchProfile validation."""

    model_config = ConfigDict(extra="ignore")

    keywords_any: tuple[str, ...] = ()
    keywords_all: tuple[str, ...] = ()
    exact_phrases: tuple[str, ...] = ()
    boost_keywords: dict[str, int] = Field(default_factory=dict)


def search_guide() -> dict[str, object]:
    """Return the user-facing search guide as a stable API document."""

    return {
        "title": "Cómo buscar mejor",
        "intro": (
            "Empezá amplio, ejecutá una búsqueda y ajustá de a poco. "
            "Auction Watch nunca cambia tus criterios automáticamente."
        ),
        "fields": [
            {
                "id": "keywords_any",
                "title": "Cualquiera de estos términos",
                "description": "Amplía resultados: alcanza con que aparezca uno.",
                "example": "mesa de pool, billar, pool",
            },
            {
                "id": "keywords_all",
                "title": "Debe incluir todos",
                "description": (
                    "Restringe mucho: cada resultado debe contener todos los términos."
                ),
                "example": "mesa, ping pong",
                "warning": (
                    "No mezcles conceptos sin relación. “autor, tapa dura” no sirve para "
                    "buscar consolas."
                ),
            },
            {
                "id": "exact_phrases",
                "title": "Frases exactas",
                "description": "Úsalas para nombres, ediciones o modelos concretos.",
                "example": "game boy color, the dark side of the moon",
            },
            {
                "id": "exclude_keywords",
                "title": "Exclusiones",
                "description": "Quitan falsos positivos sin cerrar la búsqueda principal.",
                "example": "réplica, funda, lámina, roto",
            },
            {
                "id": "filters",
                "title": "Categorías, precio, urgencia y frecuencia",
                "description": (
                    "Las categorías y el precio máximo reducen ruido. Si necesitás un "
                    "precio mínimo, anotá esa limitación al revisar: hoy el editor sólo "
                    "aplica máximo. Para más urgencia, usá más horarios y notificaciones; "
                    "para búsquedas tranquilas, menos frecuencia."
                ),
                "example": "máximo 8.000 UYU, horarios 09:00 y 18:00",
            },
        ],
        "sources": [
            {
                "id": "bavastro",
                "name": "Bavastro",
                "coverage": "Remates y lotes publicados en su catálogo público.",
            },
            {
                "id": "castells",
                "name": "Castells",
                "coverage": (
                    "Remates descubiertos en su sitio y lotes de su API pública. "
                    "Puede quedar parcial si una página o grupo no responde."
                ),
            },
            {
                "id": "prado",
                "name": "Prado",
                "coverage": "Productos públicos identificados como remates.",
            },
            {
                "id": "remotes",
                "name": "Remotes",
                "coverage": "Remates y lotes visibles en su feed público.",
            },
            {
                "id": "todoremates",
                "name": "TodoRemates",
                "coverage": "Categorías y publicaciones del catálogo público.",
            },
        ],
        "source_advice": (
            "Dejá varias fuentes activas al comenzar: cada rematador publica inventario "
            "distinto y una fuente parcial no oculta los resultados sanos de las demás."
        ),
        "no_results": (
            "“Sin hallazgos” no demuestra que no existan remates. Los criterios pueden "
            "ser demasiado cerrados, no haber inventario relevante o una fuente puede "
            "haber quedado parcial. Revisá cobertura antes de concluir."
        ),
        "statuses": [
            {
                "status": "complete",
                "meaning": "La fuente verificó toda la cobertura prevista.",
                "action": "Podés interpretar cero hallazgos dentro de esos criterios.",
            },
            {
                "status": "partial",
                "meaning": "Hay resultados válidos, pero falta una parte verificable.",
                "action": "Conservá el snapshot y revisá la causa antes de ajustar términos.",
            },
            {
                "status": "failed",
                "meaning": "La fuente no entregó cobertura publicable en esa corrida.",
                "action": "No interpretes cero como ausencia y mantené otras fuentes activas.",
            },
        ],
        "recipes": [
            {
                "name": "Consolas",
                "keywords_any": "consola, playstation, nintendo, sega, xbox",
                "exact_phrases": "game boy, family game",
                "exclude": "funda, lámina, libro",
            },
            {
                "name": "Discos de música",
                "keywords_any": "vinilo, disco, LP",
                "exact_phrases": "nombre del artista o álbum",
                "exclude": "decorativo, reloj",
            },
            {
                "name": "Libros",
                "keywords_any": "libro, novela, colección",
                "exact_phrases": "autor o título concreto",
                "exclude": "revista, fotocopia",
            },
            {
                "name": "Mesa de pool",
                "keywords_any": "mesa de pool, billar, pool",
                "exact_phrases": "mesa de billar",
                "exclude": "miniatura, juguete",
            },
            {
                "name": "Mesa de ping pong",
                "keywords_any": "ping pong, tenis de mesa",
                "keywords_all": "mesa, ping pong",
                "exclude": "paleta, pelota, red",
            },
        ],
        "flow": [
            "Crear un perfil.",
            "Elegir varias fuentes.",
            "Comenzar con pocos términos en “Cualquiera”.",
            "Ejecutar una corrida.",
            "Ajustar exclusiones, categorías y precio.",
            "Activar frecuencia y notificaciones cuando el resultado sea útil.",
        ],
    }


def profile_warnings(
    profile: SearchProfile | GuidanceCriteria,
) -> tuple[dict[str, str], ...]:
    """Suggest improvements without changing or rejecting the user's profile."""

    warnings: list[dict[str, str]] = []
    positive_boosts = tuple(
        term for term, weight in profile.boost_keywords.items() if weight > 0
    )
    has_positive = any(
        (
            profile.keywords_any,
            profile.keywords_all,
            profile.exact_phrases,
            positive_boosts,
        )
    )
    if not has_positive:
        warnings.append(
            {
                "code": "no_positive_terms",
                "field": "keywords_any",
                "message": (
                    "No hay términos positivos. Agregá al menos uno en “Cualquiera”, "
                    "“Debe incluir todos” o “Frases exactas”."
                ),
            }
        )
    if len(profile.keywords_all) >= 4:
        warnings.append(
            {
                "code": "too_many_required_terms",
                "field": "keywords_all",
                "message": (
                    "“Debe incluir todos” tiene muchos términos y probablemente cierre "
                    "demasiado la búsqueda."
                ),
            }
        )
    if len(profile.keywords_all) >= 3 and not profile.keywords_any:
        warnings.append(
            {
                "code": "move_required_terms_to_any",
                "field": "keywords_all",
                "message": (
                    "Parece una lista separada por comas. Considerá mover parte de esos "
                    "términos a “Cualquiera”; no cambiaremos nada automáticamente."
                ),
            }
        )
    return tuple(warnings)


__all__ = ["GuidanceCriteria", "profile_warnings", "search_guide"]
