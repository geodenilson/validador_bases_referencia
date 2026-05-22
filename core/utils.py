"""Funções utilitárias compartilhadas pelo plugin."""

from __future__ import annotations

import os
from typing import Iterable, List, Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsField,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant


# ---------------------------------------------------------------- #
#  Compatibilidade de tipos de campo (QVariant vs. QMetaType)      #
# ---------------------------------------------------------------- #
# Em QGIS 3.38+ o construtor `QgsField(name, QVariant.X)` foi        #
# depreciado em favor de `QgsField(name, QMetaType.Type.X)`.         #
# Em versões anteriores o `QgsField` AINDA não aceita QMetaType.     #
# Por isso detectamos o overload válido por TESTE em runtime.        #
_TIPOS_NOVO: Optional[dict] = None  # se preenchido, usa QMetaType
_TIPOS_ANTIGO = {
    "int": QVariant.Int,
    "double": QVariant.Double,
    "string": QVariant.String,
    "bool": QVariant.Bool,
}

try:
    from qgis.PyQt.QtCore import QMetaType  # type: ignore[attr-defined]

    _candidato_novo = {
        "int": QMetaType.Type.Int,
        "double": QMetaType.Type.Double,
        "string": QMetaType.Type.QString,
        "bool": QMetaType.Type.Bool,
    }
    # Testa se o QgsField aceita o overload com QMetaType.Type.
    try:
        _ = QgsField("__test__", _candidato_novo["int"])
        _TIPOS_NOVO = _candidato_novo
    except (TypeError, ValueError):
        _TIPOS_NOVO = None
except (ImportError, AttributeError):
    _TIPOS_NOVO = None


def criar_campo(nome: str, tipo: str, comprimento: int = 0, precisao: int = 0) -> QgsField:
    """Cria um ``QgsField`` no formato suportado pelo QGIS atual.

    ``tipo`` aceita os apelidos: ``"int"``, ``"double"``, ``"string"``,
    ``"bool"``. A detecção do overload (`QMetaType.Type` vs `QVariant.X`)
    é feita uma única vez no carregamento do módulo.
    """
    apelidos = _TIPOS_NOVO if _TIPOS_NOVO is not None else _TIPOS_ANTIGO
    t = apelidos.get(tipo.lower())
    if t is None:
        raise ValueError(f"Tipo de campo desconhecido: {tipo!r}")
    try:
        if comprimento or precisao:
            return QgsField(nome, t, "", comprimento, precisao)
        return QgsField(nome, t)
    except TypeError:
        # Fallback definitivo: força a forma antiga (QVariant) caso o
        # teste inicial tenha falhado de outra forma estranha.
        t_antigo = _TIPOS_ANTIGO[tipo.lower()]
        if comprimento or precisao:
            return QgsField(nome, t_antigo, "", comprimento, precisao)
        return QgsField(nome, t_antigo)


# CRS padrão para bases geográficas de referência no Brasil.
SIRGAS_2000_CRS_AUTHID = "EPSG:4674"
# CRS métrico (Albers SAD-69 / IBGE) – usado para cálculos em metros no Brasil
# quando a camada de origem não está em projeção métrica.
ALBERS_BRASIL_CRS_AUTHID = "EPSG:5880"


def carregar_camada_vetorial(caminho: str, nome: Optional[str] = None) -> Optional[QgsVectorLayer]:
    """Carrega uma camada vetorial OGR a partir de um caminho local.

    Retorna None se a camada não for válida.
    """
    if not caminho or not os.path.exists(caminho):
        return None
    nome = nome or os.path.splitext(os.path.basename(caminho))[0]
    layer = QgsVectorLayer(caminho, nome, "ogr")
    if not layer.isValid():
        return None
    return layer


def carregar_xyz_layer(uri: str, nome: str) -> Optional[QgsRasterLayer]:
    """Carrega uma camada raster XYZ/WMS a partir de URI já formatada."""
    layer = QgsRasterLayer(uri, nome, "wms")
    if not layer.isValid():
        return None
    return layer


def listar_campos(camada: QgsVectorLayer) -> List[str]:
    """Retorna a lista de nomes dos campos da camada (vazia se inválida)."""
    if camada is None or not camada.isValid():
        return []
    return [f.name() for f in camada.fields()]


def garantir_campo(camada: QgsVectorLayer, nome: str, tipo) -> bool:
    """Garante que um campo exista na camada. Cria se não existir.

    Retorna True se o campo já existia ou foi criado com sucesso.
    """
    from qgis.core import QgsField

    if camada is None or not camada.isValid():
        return False
    if nome in [f.name() for f in camada.fields()]:
        return True
    provider = camada.dataProvider()
    ok = provider.addAttributes([QgsField(nome, tipo)])
    camada.updateFields()
    return bool(ok)


def crs_metrico_para(camada: QgsVectorLayer) -> QgsCoordinateReferenceSystem:
    """Retorna um CRS métrico adequado para cálculos em metros.

    Se a camada já está em CRS projetado em metros, retorna o próprio CRS;
    caso contrário, retorna o CRS Albers do Brasil (EPSG:5880).
    """
    crs = camada.crs() if camada is not None else QgsCoordinateReferenceSystem()
    if crs.isValid() and not crs.isGeographic():
        return crs
    return QgsCoordinateReferenceSystem(ALBERS_BRASIL_CRS_AUTHID)


def transform_geom(geom, crs_origem: QgsCoordinateReferenceSystem,
                   crs_destino: QgsCoordinateReferenceSystem):
    """Reprojeta uma geometria entre dois CRSs e devolve a nova geometria."""
    if crs_origem == crs_destino:
        return geom
    transform = QgsCoordinateTransform(crs_origem, crs_destino, QgsProject.instance())
    nova = type(geom)(geom)
    nova.transform(transform)
    return nova


def garantir_pasta(caminho: str) -> str:
    """Garante a existência de uma pasta e retorna seu caminho."""
    os.makedirs(caminho, exist_ok=True)
    return caminho


def somente_classes_unicas(camada: QgsVectorLayer, campo: str) -> List:
    """Lista valores únicos de um campo (descarta nulos)."""
    if camada is None or not camada.isValid() or campo not in listar_campos(camada):
        return []
    valores = set()
    for feat in camada.getFeatures():
        v = feat[campo]
        if v is None:
            continue
        valores.add(v)
    return sorted(valores, key=lambda x: str(x))


def humanize_bool(valor: bool) -> str:
    return "Sim" if valor else "Não"


def formatar_porcentagem(valor: Optional[float], casas: int = 2) -> str:
    if valor is None:
        return "—"
    return f"{valor * 100:.{casas}f}%"


def soma(valores: Iterable[float]) -> float:
    total = 0.0
    for v in valores:
        if v is None:
            continue
        total += float(v)
    return total
