"""Geração de planos de amostragem para validação de qualidade.

Estratégias suportadas:
    * estratificada_proporcional: Pontos por classe proporcionais à área.
    * estratificada_igual:       Mesmo nº de pontos por classe.
    * aleatoria_simples:         N pontos aleatórios em toda a camada.
    * sistematica:               Grid regular de pontos.

Para cobertura/uso do solo o cálculo do tamanho da amostra usa a
distribuição multinomial (Congalton & Green, 1999):

    N = B * pi * (1 - pi) / b²

Onde:
    B  = quantil da distribuição qui-quadrado (gl=1, alpha=a/k)
    pi = proporção da classe com maior área
    k  = número de classes
    b  = erro admissível
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSpatialIndex,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QCoreApplication

from .utils import criar_campo


# Tabela qui-quadrado (gl=1) – valores aproximados utilizados na
# metodologia. Para níveis de confiança fora desta tabela
# utilizamos a aproximação 7.47677 (recomendado para 95%).
QUI_QUADRADO_GL1 = {
    0.90: 6.6349,
    0.95: 7.47677,
    0.99: 10.8276,
}


@dataclass
class ClasseAmostragem:
    """Representa uma classe e sua proporção na camada."""

    valor: object
    nome: str
    area: float
    proporcao: float = 0.0


@dataclass
class ResumoAmostragem:
    """Resumo do cálculo do tamanho da amostra."""

    n_total: int
    n_por_classe: Dict[object, int] = field(default_factory=dict)
    classes: List[ClasseAmostragem] = field(default_factory=list)
    formula_aplicada: str = ""
    parametros: Dict[str, float] = field(default_factory=dict)


def _qui_quadrado(nivel_confianca: float) -> float:
    """Retorna o valor qui-quadrado (gl=1) para um nível de confiança."""
    if nivel_confianca in QUI_QUADRADO_GL1:
        return QUI_QUADRADO_GL1[nivel_confianca]
    chaves = sorted(QUI_QUADRADO_GL1.keys())
    mais_proximo = min(chaves, key=lambda x: abs(x - nivel_confianca))
    return QUI_QUADRADO_GL1[mais_proximo]


def _calcular_proporcoes(
    camada: QgsVectorLayer,
    campo_classe: str,
    campo_area: Optional[str] = None,
    gravar_area: bool = False,
) -> List[ClasseAmostragem]:
    """Calcula a área e a proporção de cada classe na camada.

    Quando ``campo_area`` é informado e existe, usa o valor desse campo
    como área de cada feição (muito mais rápido). Caso contrário, calcula
    a área via QgsDistanceArea (geodésica/elipsoide do projeto).

    Se ``gravar_area`` for True e o cálculo for geodésico, grava o valor
    (em hectares) no campo ``area_ha`` da camada.
    """
    usar_campo = (
        campo_area
        and campo_area in [f.name() for f in camada.fields()]
    )

    distancia = None
    if not usar_campo:
        distancia = QgsDistanceArea()
        distancia.setSourceCrs(camada.crs(), QgsProject.instance().transformContext())
        distancia.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")

    areas: Dict[object, float] = {}
    areas_por_feat: Dict[int, float] = {}
    calculou_geom = False

    for feat in camada.getFeatures():
        valor = feat[campo_classe]
        if valor is None:
            continue
        if usar_campo:
            try:
                area = float(feat[campo_area]) if feat[campo_area] is not None else 0.0
            except (TypeError, ValueError):
                area = 0.0
        else:
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            try:
                area = distancia.measureArea(geom)
            except Exception:
                area = geom.area()
            calculou_geom = True
            if gravar_area:
                areas_por_feat[feat.id()] = area
        areas[valor] = areas.get(valor, 0.0) + max(area, 0.0)

    total = sum(areas.values())

    if total <= 0 and usar_campo:
        distancia = QgsDistanceArea()
        distancia.setSourceCrs(camada.crs(), QgsProject.instance().transformContext())
        distancia.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
        areas.clear()
        calculou_geom = True
        for feat in camada.getFeatures():
            valor = feat[campo_classe]
            if valor is None:
                continue
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            try:
                area = distancia.measureArea(geom)
            except Exception:
                area = geom.area()
            if gravar_area:
                areas_por_feat[feat.id()] = area
            areas[valor] = areas.get(valor, 0.0) + max(area, 0.0)
        total = sum(areas.values())

    if gravar_area and calculou_geom and areas_por_feat:
        _gravar_area_ha(camada, areas_por_feat)

    classes = []
    for valor, area in sorted(areas.items(), key=lambda kv: -kv[1]):
        proporcao = area / total if total > 0 else 0
        classes.append(
            ClasseAmostragem(
                valor=valor,
                nome=str(valor),
                area=area,
                proporcao=proporcao,
            )
        )
    return classes


def _gravar_area_ha(camada: QgsVectorLayer, areas_m2: Dict[int, float]) -> None:
    """Grava a área em hectares no campo ``area_ha`` da camada."""
    nome = "area_ha"
    if nome not in [f.name() for f in camada.fields()]:
        camada.dataProvider().addAttributes([criar_campo(nome, "double")])
        camada.updateFields()
    idx = camada.fields().indexFromName(nome)
    if idx < 0:
        return
    camada.startEditing()
    for fid, area_m2 in areas_m2.items():
        camada.changeAttributeValue(fid, idx, round(area_m2 / 10_000.0, 4))
    camada.commitChanges()


def calcular_tamanho_amostra(
    camada: QgsVectorLayer,
    campo_classe: str,
    nivel_confianca: float = 0.95,
    erro_admissivel: float = 0.05,
    minimo_por_classe: int = 5,
    campo_area: Optional[str] = None,
) -> ResumoAmostragem:
    """Calcula o tamanho total e por classe usando a fórmula multinomial.

    Aplica também um piso (minimo_por_classe) para evitar que classes
    raras fiquem sem pontos suficientes para a matriz de confusão.

    ``campo_area`` (opcional) acelera o cálculo quando a camada já tem
    um campo numérico com a área das feições (em qualquer unidade —
    apenas a proporção importa).
    """
    classes = _calcular_proporcoes(camada, campo_classe, campo_area=campo_area,
                                    gravar_area=True)
    if not classes:
        return ResumoAmostragem(n_total=0)

    k = len(classes)
    pi_max = max(c.proporcao for c in classes) or 0.99
    b = erro_admissivel
    B = _qui_quadrado(nivel_confianca)

    n_total = math.ceil(B * pi_max * (1 - pi_max) / (b ** 2)) if b > 0 else 0
    n_total = max(n_total, k * minimo_por_classe)

    n_por_classe: Dict[object, int] = {}
    for c in classes:
        n_classe = max(int(round(n_total * c.proporcao)), minimo_por_classe)
        n_por_classe[c.valor] = n_classe

    n_total_final = sum(n_por_classe.values())

    formula = (
        f"N = B · pᵢ · (1 - pᵢ) / b²  =  "
        f"{B:.4f} × {pi_max:.4f} × (1 - {pi_max:.4f}) / ({b}²) "
        f"≈ {n_total}"
    )

    return ResumoAmostragem(
        n_total=n_total_final,
        n_por_classe=n_por_classe,
        classes=classes,
        formula_aplicada=formula,
        parametros={
            "B": B,
            "pi_max": pi_max,
            "b": b,
            "k": k,
            "nivel_confianca": nivel_confianca,
        },
    )


def _ponto_aleatorio_em_geometria(geom: QgsGeometry) -> Optional[QgsPointXY]:
    """Sorteia um ponto aleatório dentro do bounding box que pertença à geometria."""
    bbox = geom.boundingBox()
    for _ in range(200):
        x = random.uniform(bbox.xMinimum(), bbox.xMaximum())
        y = random.uniform(bbox.yMinimum(), bbox.yMaximum())
        ponto = QgsPointXY(x, y)
        if geom.contains(QgsGeometry.fromPointXY(ponto)):
            return ponto
    return None


def _criar_camada_pontos(
    crs: QgsCoordinateReferenceSystem,
    nome: str = "Pontos amostrais",
    extra_fields: Optional[List[QgsField]] = None,
) -> QgsVectorLayer:
    """Cria uma camada de pontos em memória padronizada."""
    fields = QgsFields()
    fields.append(criar_campo("id", "int"))
    fields.append(criar_campo("classificacao", "string"))
    fields.append(criar_campo("verdade", "string"))
    fields.append(criar_campo("rotulado", "int"))
    fields.append(criar_campo("observacao", "string"))
    if extra_fields:
        for f in extra_fields:
            fields.append(f)

    uri = f"Point?crs={crs.authid()}"
    layer = QgsVectorLayer(uri, nome, "memory")
    pr = layer.dataProvider()
    pr.addAttributes(list(fields))
    layer.updateFields()
    return layer


def gerar_pontos_estratificados(
    camada: QgsVectorLayer,
    campo_classe: str,
    n_por_classe: Dict[object, int],
    nome_camada_saida: str = "Pontos amostrais",
) -> QgsVectorLayer:
    """Gera pontos aleatórios estratificados por classe.

    Para cada classe, sorteia o número de pontos definido em
    ``n_por_classe`` dentro das feições daquela classe.
    """
    layer_pts = _criar_camada_pontos(camada.crs(), nome_camada_saida)
    pr = layer_pts.dataProvider()

    feicoes_por_classe: Dict[object, List[QgsGeometry]] = {}
    for feat in camada.getFeatures():
        valor = feat[campo_classe]
        if valor is None:
            continue
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        feicoes_por_classe.setdefault(valor, []).append(QgsGeometry(geom))

    next_id = 1
    novas_feicoes: List[QgsFeature] = []
    for valor, n in n_por_classe.items():
        geometrias = feicoes_por_classe.get(valor, [])
        if not geometrias or n <= 0:
            continue
        for _ in range(n):
            geom = random.choice(geometrias)
            ponto = _ponto_aleatorio_em_geometria(geom)
            if ponto is None:
                continue
            f = QgsFeature(layer_pts.fields())
            f.setGeometry(QgsGeometry.fromPointXY(ponto))
            f.setAttribute("id", next_id)
            f.setAttribute("classificacao", str(valor))
            f.setAttribute("verdade", None)
            f.setAttribute("rotulado", 0)
            f.setAttribute("observacao", "")
            novas_feicoes.append(f)
            next_id += 1

    pr.addFeatures(novas_feicoes)
    layer_pts.updateExtents()
    return layer_pts


def gerar_pontos_aleatorios_simples(
    camada: QgsVectorLayer,
    n_total: int,
    campo_classe: Optional[str] = None,
    nome_camada_saida: str = "Pontos amostrais",
) -> QgsVectorLayer:
    """Gera N pontos aleatórios simples sobre a área total da camada."""
    layer_pts = _criar_camada_pontos(camada.crs(), nome_camada_saida)
    pr = layer_pts.dataProvider()

    feicoes = [
        (f, QgsGeometry(f.geometry()))
        for f in camada.getFeatures()
        if f.geometry() is not None and not f.geometry().isEmpty()
    ]
    if not feicoes:
        return layer_pts

    novas: List[QgsFeature] = []
    next_id = 1
    tentativas = 0
    while len(novas) < n_total and tentativas < n_total * 50:
        feat, geom = random.choice(feicoes)
        ponto = _ponto_aleatorio_em_geometria(geom)
        tentativas += 1
        if ponto is None:
            continue
        f = QgsFeature(layer_pts.fields())
        f.setGeometry(QgsGeometry.fromPointXY(ponto))
        f.setAttribute("id", next_id)
        if campo_classe and campo_classe in [fl.name() for fl in camada.fields()]:
            f.setAttribute("classificacao", str(feat[campo_classe]))
        f.setAttribute("rotulado", 0)
        novas.append(f)
        next_id += 1

    pr.addFeatures(novas)
    layer_pts.updateExtents()
    return layer_pts


def gerar_pontos_sistematicos(
    camada: QgsVectorLayer,
    espacamento_metros: float,
    campo_classe: Optional[str] = None,
    nome_camada_saida: str = "Pontos sistemáticos",
) -> QgsVectorLayer:
    """Gera grid sistemático de pontos sobre o bounding box da camada."""
    layer_pts = _criar_camada_pontos(camada.crs(), nome_camada_saida)
    pr = layer_pts.dataProvider()

    crs_origem = camada.crs()
    crs_metrico = crs_origem
    if crs_origem.isGeographic():
        crs_metrico = QgsCoordinateReferenceSystem("EPSG:5880")
    transform_in = QgsCoordinateTransform(crs_origem, crs_metrico, QgsProject.instance())
    transform_out = QgsCoordinateTransform(crs_metrico, crs_origem, QgsProject.instance())

    bbox_geom = QgsGeometry.fromRect(QgsRectangle(camada.extent()))
    bbox_metrico = QgsGeometry(bbox_geom)
    bbox_metrico.transform(transform_in)
    bb = bbox_metrico.boundingBox()

    nx = int((bb.xMaximum() - bb.xMinimum()) / espacamento_metros) + 1
    ny = int((bb.yMaximum() - bb.yMinimum()) / espacamento_metros) + 1
    if nx * ny > _LIMITE_CELULAS_GRID:
        raise ValueError(
            f"Grid resultaria em {nx * ny:,} pontos. "
            f"Aumente o espaçamento ou reduza a extensão."
        )

    next_id = 1
    novas: List[QgsFeature] = []
    y = bb.yMinimum()
    while y <= bb.yMaximum():
        x = bb.xMinimum()
        while x <= bb.xMaximum():
            pt = QgsGeometry.fromPointXY(QgsPointXY(x, y))
            pt.transform(transform_out)
            f = QgsFeature(layer_pts.fields())
            f.setGeometry(pt)
            f.setAttribute("id", next_id)
            f.setAttribute("rotulado", 0)
            novas.append(f)
            next_id += 1
            x += espacamento_metros
        y += espacamento_metros

    pr.addFeatures(novas)
    layer_pts.updateExtents()
    return layer_pts


_LIMITE_CELULAS_GRID = 5_000_000  # proteção contra extensões absurdas


def gerar_quadrantes(
    camada: QgsVectorLayer,
    tamanho_metros: float = 1000.0,
    n_amostras: Optional[int] = None,
    nome_camada_saida: str = "Quadrantes de checagem",
    apenas_sorteados: bool = True,
    sorteio_apenas_intersecta: bool = True,
    progress_callback=None,
) -> QgsVectorLayer:
    """Gera quadrantes (default 1×1 km) sobre a extensão da camada.

    Versão otimizada usando ``QgsSpatialIndex`` e *reservoir sampling*:

        * Constrói um índice espacial das feições da camada (rápido).
        * Itera pela grid no CRS métrico mas SEM materializar todas as
          células — usa amostragem de reservatório para manter no máximo
          ``n_amostras`` candidatas em memória.
        * Para cada célula candidata, verifica via índice se há alguma
          feição cuja BBox intersecta o quadrante (extremamente rápido).
        * Retorna **somente os quadrantes sorteados** — evita poluir o
          canvas com milhares de células.
        * A camada de saída tem campos: ``id``, ``amostrado``, ``aprovado``,
          ``dist_max_m``, ``erro_omissao``, ``erro_comissao``, ``observacao``.

    Args:
        camada: Camada vetorial a checar.
        tamanho_metros: Lado da célula em metros.
        n_amostras: Quantidade de quadrantes a sortear (None = todos os
            que intersectam a camada).
        progress_callback: Função opcional ``f(pct: int, msg: str)`` chamada
            periodicamente durante a iteração para atualizar a UI.

    Para gerar o grid completo (uso menos comum), passe
    ``apenas_sorteados=False`` e ``n_amostras=None``.
    """

    def _progresso(pct: int, msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass
        # processa eventos da UI para evitar congelamento
        try:
            QCoreApplication.processEvents()
        except Exception:
            pass

    crs_origem = camada.crs()
    crs_metrico = crs_origem
    if crs_origem.isGeographic():
        crs_metrico = QgsCoordinateReferenceSystem("EPSG:5880")
    transform_in = QgsCoordinateTransform(
        crs_origem, crs_metrico, QgsProject.instance()
    )
    transform_out = QgsCoordinateTransform(
        crs_metrico, crs_origem, QgsProject.instance()
    )

    extent = camada.extent()
    if extent.isEmpty():
        raise ValueError(
            "A camada selecionada não possui extensão válida (vazia)."
        )

    # Bounding box no CRS métrico (transformBoundingBox densifica os cantos).
    bb = transform_in.transformBoundingBox(extent)

    largura_m = bb.width()
    altura_m = bb.height()
    if tamanho_metros <= 0:
        raise ValueError("Tamanho do quadrante deve ser > 0.")
    n_cols = max(1, int(math.ceil(largura_m / tamanho_metros)))
    n_rows = max(1, int(math.ceil(altura_m / tamanho_metros)))
    total_celulas = n_cols * n_rows

    if total_celulas > _LIMITE_CELULAS_GRID:
        raise ValueError(
            f"O grid resultante teria ~{total_celulas:,} células ({n_cols}×{n_rows}). "
            f"Aumente o tamanho do quadrante (atual: {tamanho_metros:.0f} m) "
            f"ou recorte a camada antes de gerar quadrantes."
        )

    _progresso(0, f"Indexando {camada.featureCount()} feições da camada…")

    # Índice espacial das feições da camada (no CRS de origem, que é o
    # mesmo dos retângulos transformados depois).
    indice = QgsSpatialIndex()
    contagem_indice = 0
    for f in camada.getFeatures():
        geom = f.geometry()
        if geom is None or geom.isEmpty():
            continue
        try:
            indice.insertFeature(f)
            contagem_indice += 1
        except Exception:
            continue
        if contagem_indice % 5000 == 0:
            _progresso(
                5,
                f"Indexando feições… ({contagem_indice})",
            )

    if contagem_indice == 0 and sorteio_apenas_intersecta:
        raise ValueError(
            "Nenhuma feição válida encontrada na camada para amostrar."
        )

    fields = QgsFields()
    fields.append(criar_campo("id", "int"))
    fields.append(criar_campo("amostrado", "int"))
    fields.append(criar_campo("aprovado", "int"))
    fields.append(criar_campo("dist_max_m", "double"))
    fields.append(criar_campo("erro_omissao", "int"))
    fields.append(criar_campo("erro_comissao", "int"))
    fields.append(criar_campo("observacao", "string"))

    uri = f"Polygon?crs={crs_origem.authid()}"
    layer = QgsVectorLayer(uri, nome_camada_saida, "memory")
    pr = layer.dataProvider()
    pr.addAttributes(list(fields))
    layer.updateFields()

    # Reservoir sampling: mantemos no máximo `n_amostras` candidatas
    # (ou TODOS quando n_amostras=None / quando guardamos tudo).
    usar_reservoir = (
        apenas_sorteados
        and n_amostras is not None
        and n_amostras > 0
    )
    coletados: List[QgsFeature] = []  # candidatos (todos ou reservoir)
    visitadas_validas = 0  # quadrantes que intersectam a camada
    visitadas = 0  # quadrantes inspecionados (com ou sem interseção)

    next_id_provisorio = 1
    x_min = bb.xMinimum()
    y_min = bb.yMinimum()

    _progresso(
        10,
        f"Iterando grid {n_cols}×{n_rows} ({total_celulas:,} células)…",
    )

    for ir in range(n_rows):
        y0 = y_min + ir * tamanho_metros
        y1 = y0 + tamanho_metros
        for ic in range(n_cols):
            visitadas += 1
            x0 = x_min + ic * tamanho_metros
            x1 = x0 + tamanho_metros

            rect_metrico = QgsRectangle(x0, y0, x1, y1)
            geom_metrico = QgsGeometry.fromRect(rect_metrico)
            geom_origem = QgsGeometry(geom_metrico)
            try:
                geom_origem.transform(transform_out)
            except Exception:
                continue

            if sorteio_apenas_intersecta:
                bbox_origem = geom_origem.boundingBox()
                ids_candidatos = indice.intersects(bbox_origem)
                if not ids_candidatos:
                    continue

            visitadas_validas += 1

            f = QgsFeature(layer.fields())
            f.setGeometry(geom_origem)
            f.setAttribute("id", next_id_provisorio)
            f.setAttribute("amostrado", 0)
            f.setAttribute("aprovado", -1)
            f.setAttribute("erro_omissao", 0)
            f.setAttribute("erro_comissao", 0)
            next_id_provisorio += 1

            if usar_reservoir:
                if len(coletados) < n_amostras:
                    coletados.append(f)
                else:
                    j = random.randint(0, visitadas_validas - 1)
                    if j < n_amostras:
                        coletados[j] = f
            else:
                coletados.append(f)

        if (ir % max(1, n_rows // 50)) == 0:
            pct = 10 + int(80 * (ir + 1) / n_rows)
            _progresso(
                pct,
                f"Linha {ir + 1}/{n_rows} • intersectaram: {visitadas_validas:,}",
            )

    _progresso(95, "Finalizando camada…")

    if visitadas_validas == 0:
        # Sem interseção: pode ser que nenhuma célula bate com a camada
        # (ex.: bbox transformada não cobre as feições por causa de CRS).
        # Fallback: gera grid puro.
        coletados = []

    # Re-numera ids contíguos e marca todos como amostrados (são os finais).
    feats_finais: List[QgsFeature] = []
    for novo_id, feat in enumerate(coletados, start=1):
        feat.setAttribute("id", novo_id)
        feat.setAttribute("amostrado", 1)
        feats_finais.append(feat)

    if not apenas_sorteados:
        # Nesse caso o usuário pediu o grid inteiro → marcamos todos
        # como amostrados (ou sorteados aleatoriamente se n_amostras dado).
        if n_amostras and n_amostras < len(feats_finais):
            sorteados_set = set(random.sample(range(len(feats_finais)), n_amostras))
            for i, fe in enumerate(feats_finais):
                fe.setAttribute("amostrado", 1 if i in sorteados_set else 0)

    pr.addFeatures(feats_finais)
    layer.updateExtents()
    _progresso(100, f"{len(feats_finais)} quadrante(s) gerado(s).")
    return layer
