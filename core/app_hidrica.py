"""Geração automática de APP hídrica a partir da base hidrográfica.

Conversão para QGIS/PyQGIS+Processing do script original:
    Referencias/APP_Hidrica_v2.py (Dr. Denilson Passo, 2022, ArcPy)

Implementa as três frentes do script original:
    * appMassaDagua       — APP de lagos/lagoas/reservatórios (poligonal).
    * appTrechoDrenagem   — APP de trecho de drenagem linear (rios < 10 m).
    * appTrechoMassaDagua — APP de rios de margem dupla (largura variável)
                            usando centerline + medição near-distance.
    * juncaoAPP           — União com prioridade entre as três APPs.

As larguras de buffer seguem a Lei nº 12.651/2012:
    * Rio < 10 m         → 30 m
    * Rio 10–50 m        → 50 m
    * Rio 50–200 m       → 100 m
    * Rio 200–600 m      → 200 m
    * Rio > 600 m        → 500 m
    * Lago/lagoa < 20 ha → 50 m
    * Lago/lagoa ≥ 20 ha → 100 m
    * Reservatório       → 30 m
    * Nascente           → 50 m
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsFields,
    QgsGeometry,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .utils import criar_campo


SIRGAS_2000 = "EPSG:4674"
ALBERS_BR = "EPSG:5880"


@dataclass
class ResultadoAppHidrica:
    camada_app: Optional[QgsVectorLayer] = None
    camada_hidro_categorizada: Optional[QgsVectorLayer] = None
    arquivos: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    sucesso: bool = True

    def add(self, msg: str) -> None:
        self.log.append(msg)


def _processing():
    """Importa o módulo processing apenas quando rodando dentro do QGIS."""
    import processing  # type: ignore
    return processing


def _crs_metrico_para(camada: QgsVectorLayer) -> QgsCoordinateReferenceSystem:
    if camada is None or not camada.isValid():
        return QgsCoordinateReferenceSystem(ALBERS_BR)
    crs = camada.crs()
    if crs.isValid() and not crs.isGeographic():
        return crs
    return QgsCoordinateReferenceSystem(ALBERS_BR)


def _reprojetar_se_necessario(
    camada: QgsVectorLayer, crs_destino: QgsCoordinateReferenceSystem
) -> QgsVectorLayer:
    if camada is None:
        return camada
    if camada.crs() == crs_destino:
        return camada
    processing = _processing()
    out = processing.run(
        "native:reprojectlayer",
        {
            "INPUT": camada,
            "TARGET_CRS": crs_destino,
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]
    return out


def _largura_app_para_rio(largura_m: float) -> int:
    """Larguras de APP conforme largura do rio (Lei 12.651/2012)."""
    if largura_m <= 10:
        return 30
    if largura_m <= 50:
        return 50
    if largura_m <= 200:
        return 100
    if largura_m <= 600:
        return 200
    return 500


def _classe_para_app_metros(app_m: int) -> int:
    return {30: 1, 50: 2, 100: 3, 200: 4, 500: 5}.get(app_m, 1)


# ----------------------------------------------------------------------
#                       APP de Massa d'Água
# ----------------------------------------------------------------------

def _eh_reservatorio(valor) -> bool:
    """Heurística tolerante a variações textuais comuns."""
    if valor is None:
        return False
    s = str(valor).strip().lower()
    if not s:
        return False
    palavras = ("reservat", "represa", "barragem", "acude", "açude")
    return any(p in s for p in palavras)


# Nomes de categoria por CLASSE (conforme Lei 12.651/2012 e NT padrão).
CATEGORIAS_POR_CLASSE: Dict[int, str] = {
    1: "Rio até 10 m",
    2: "Rio de 10 a 50 m",
    3: "Rio de 50 a 200 m",
    4: "Rio de 200 a 600 m",
    5: "Rio acima de 600 m",
    6: "Lago ou Lagoa Natural",
    7: "Reservatório Artificial",
    8: "Nascente",
}


def _atribuir_classe_categoria(
    layer: QgsVectorLayer, classe: int, categoria: str
) -> None:
    """Garante que todas as feições da camada tenham CLASSE/CATEGORIA fixos.

    Cria as colunas se ainda não existirem.
    """
    if layer is None or not layer.isValid():
        return
    pr = layer.dataProvider()
    nomes = [f.name() for f in layer.fields()]
    novas: List = []
    if "CLASSE" not in nomes:
        novas.append(criar_campo("CLASSE", "int"))
    if "CATEGORIA" not in nomes:
        novas.append(criar_campo("CATEGORIA", "string"))
    if novas:
        pr.addAttributes(novas)
        layer.updateFields()
    idx_cl = layer.fields().indexFromName("CLASSE")
    idx_ca = layer.fields().indexFromName("CATEGORIA")
    layer.startEditing()
    for f in layer.getFeatures():
        if idx_cl >= 0:
            layer.changeAttributeValue(f.id(), idx_cl, int(classe))
        if idx_ca >= 0:
            layer.changeAttributeValue(f.id(), idx_ca, categoria)
    layer.commitChanges()


def app_massa_dagua(
    camada_massa: QgsVectorLayer,
    campo_categoria: Optional[str] = None,
    valores_reservatorio: Optional[List[str]] = None,
) -> Optional[QgsVectorLayer]:
    """Gera APP de lagos/lagoas/reservatórios.

    * Lago/lagoa < 20 ha → buffer 50 m, CLASSE 6.
    * Lago/lagoa ≥ 20 ha → buffer 100 m, CLASSE 6.
    * Reservatório artificial → buffer 30 m, CLASSE 7.

    Para distinguir reservatório de lago natural:
        * Se ``valores_reservatorio`` for informado (lista de valores do
          ``campo_categoria``), apenas esses valores serão tratados como
          reservatório artificial; o restante cai como lago/lagoa natural.
        * Se for ``None`` ou vazio, recorre à heurística textual
          (``_eh_reservatorio``: reconhece "reservat", "represa",
          "barragem", "açude"). Valores nulos sempre caem como lago.
    """
    processing = _processing()
    crs_metrico = _crs_metrico_para(camada_massa)
    camada_metrica = _reprojetar_se_necessario(camada_massa, crs_metrico)

    distancia = QgsDistanceArea()
    distancia.setSourceCrs(camada_metrica.crs(), QgsProject.instance().transformContext())
    distancia.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")

    fields = camada_metrica.fields()
    novos_campos = list(fields)

    out_uri = f"Polygon?crs={crs_metrico.authid()}"
    saida = QgsVectorLayer(out_uri, "APP_Massa_Dagua_temp", "memory")
    pr = saida.dataProvider()
    pr.addAttributes(novos_campos)
    if "CLASSE" not in [f.name() for f in novos_campos]:
        pr.addAttributes([criar_campo("CLASSE", "int")])
    if "Buff" not in [f.name() for f in novos_campos]:
        pr.addAttributes([criar_campo("Buff", "int")])
    saida.updateFields()

    feats: List[QgsFeature] = []
    for feat in camada_metrica.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        area_ha = max(0.0, distancia.measureArea(geom)) / 10000.0
        categoria = feat[campo_categoria] if campo_categoria else None
        if valores_reservatorio:
            cat_str = str(categoria).strip() if categoria is not None else ""
            eh_reserv = cat_str in {str(v).strip() for v in valores_reservatorio}
        else:
            eh_reserv = _eh_reservatorio(categoria)
        if eh_reserv:
            buff = 30
            classe = 7
        else:
            classe = 6
            buff = 50 if area_ha < 20 else 100
        novo = QgsFeature(saida.fields())
        atributos = list(feat.attributes())
        # garante tamanho compatível
        while len(atributos) < len(saida.fields()):
            atributos.append(None)
        atributos[saida.fields().indexFromName("CLASSE")] = classe
        atributos[saida.fields().indexFromName("Buff")] = buff
        novo.setAttributes(atributos)
        novo.setGeometry(QgsGeometry(geom))
        feats.append(novo)
    pr.addFeatures(feats)
    saida.updateExtents()

    apps_por_buff = []
    for buff_m in (30, 50, 100):
        sel = processing.run(
            "native:extractbyattribute",
            {
                "INPUT": saida,
                "FIELD": "Buff",
                "OPERATOR": 0,  # =
                "VALUE": str(buff_m),
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]
        if sel.featureCount() == 0:
            continue
        buf = processing.run(
            "native:buffer",
            {
                "INPUT": sel,
                "DISTANCE": buff_m,
                "SEGMENTS": 8,
                "DISSOLVE": False,
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]
        apps_por_buff.append((buff_m, buf))

    if not apps_por_buff:
        return None

    # Hierarquia: 100 prevalece sobre 50 prevalece sobre 30
    apps_por_buff.sort(key=lambda kv: kv[0])
    resultado = None
    for buff_m, layer in apps_por_buff:
        if resultado is None:
            resultado = layer
            continue
        # remove área que vai ser sobreposta pela próxima (maior)
        diff = processing.run(
            "native:difference",
            {"INPUT": resultado, "OVERLAY": layer, "OUTPUT": "memory:"},
        )["OUTPUT"]
        merged = processing.run(
            "native:mergevectorlayers",
            {"LAYERS": [diff, layer], "CRS": crs_metrico, "OUTPUT": "memory:"},
        )["OUTPUT"]
        resultado = merged

    return _reprojetar_se_necessario(resultado, camada_massa.crs())


# ----------------------------------------------------------------------
#                  APP de Trechos de Drenagem (Rios < 10m)
# ----------------------------------------------------------------------

def app_trecho_drenagem(camada_trecho: QgsVectorLayer) -> Optional[QgsVectorLayer]:
    """Aplica buffer de 30 m em trechos de drenagem (rios menores que 10 m).

    Saída: uma feição por polígono (singlepart), sem multipart agregadas.
    Para isso: buffer sem dissolve → dissolve geométrico (une trechos
    contíguos) → multiparttosingleparts → atribui CLASSE 1 + CATEGORIA.
    """
    if camada_trecho is None:
        return None
    processing = _processing()
    crs_metrico = _crs_metrico_para(camada_trecho)
    camada_metrica = _reprojetar_se_necessario(camada_trecho, crs_metrico)

    buf = processing.run(
        "native:buffer",
        {
            "INPUT": camada_metrica,
            "DISTANCE": 30,
            "SEGMENTS": 8,
            "DISSOLVE": False,
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]
    # Une polígonos sobrepostos para depois explodir em partes únicas.
    dissolvido = processing.run(
        "native:dissolve",
        {"INPUT": buf, "FIELD": [], "OUTPUT": "memory:"},
    )["OUTPUT"]
    singlepart = processing.run(
        "native:multiparttosingleparts",
        {"INPUT": dissolvido, "OUTPUT": "memory:"},
    )["OUTPUT"]

    _atribuir_classe_categoria(
        singlepart, classe=1, categoria=CATEGORIAS_POR_CLASSE[1]
    )
    return _reprojetar_se_necessario(singlepart, camada_trecho.crs())


# ----------------------------------------------------------------------
#               APP de Trecho de Massa d'Água (margem dupla)
# ----------------------------------------------------------------------

def app_trecho_massa_dagua(camada_tmd: QgsVectorLayer) -> Optional[QgsVectorLayer]:
    """Gera APP de rios de margem dupla com largura variável.

    Procedimento simplificado vs script original (que usa centerline):
        1. Estima a largura predominante de cada polígono (perímetro/área).
        2. Aplica o buffer correspondente (30/50/100/200/500 m) por feição.
        3. Garante prioridade da maior APP sobre a menor.
    """
    if camada_tmd is None:
        return None
    processing = _processing()
    crs_metrico = _crs_metrico_para(camada_tmd)
    camada_metrica = _reprojetar_se_necessario(camada_tmd, crs_metrico)

    distancia = QgsDistanceArea()
    distancia.setSourceCrs(camada_metrica.crs(), QgsProject.instance().transformContext())
    distancia.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")

    fields = list(camada_metrica.fields())
    out_uri = f"Polygon?crs={crs_metrico.authid()}"
    saida = QgsVectorLayer(out_uri, "TMD_marcado", "memory")
    pr = saida.dataProvider()
    pr.addAttributes(fields)
    if "CLASSE" not in [f.name() for f in fields]:
        pr.addAttributes([criar_campo("CLASSE", "int")])
    if "CATEGORIA" not in [f.name() for f in fields]:
        pr.addAttributes([criar_campo("CATEGORIA", "string")])
    if "APP_Lei" not in [f.name() for f in fields]:
        pr.addAttributes([criar_campo("APP_Lei", "int")])
    saida.updateFields()

    novas: List[QgsFeature] = []
    for feat in camada_metrica.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        area = max(0.0, distancia.measureArea(geom))
        try:
            # Como aproximação do perímetro, converte o polígono em linha
            # (boundary) e mede o comprimento total.
            boundary = geom.convertToType(QgsWkbTypes.LineGeometry, True)
            perim = max(1.0, distancia.measureLength(boundary)) if boundary else 1.0
        except Exception:
            perim = max(1.0, geom.length())
        # Largura aproximada = 4*A/P (válida para polígonos longilíneos
        # tipo "rio de margem dupla", em que P ≈ 2L + 2W e L >> W).
        largura = (4 * area) / perim if perim > 0 else 0.0
        app_m = _largura_app_para_rio(largura)
        classe = _classe_para_app_metros(app_m)
        categoria = {1: "Ate 10m", 2: "Entre 10m e 50m", 3: "Entre 50m e 200m",
                     4: "Entre 200m e 600m", 5: "Acima de 600m"}[classe]
        novo = QgsFeature(saida.fields())
        atributos = list(feat.attributes())
        while len(atributos) < len(saida.fields()):
            atributos.append(None)
        atributos[saida.fields().indexFromName("CLASSE")] = classe
        atributos[saida.fields().indexFromName("CATEGORIA")] = categoria
        atributos[saida.fields().indexFromName("APP_Lei")] = app_m
        novo.setAttributes(atributos)
        novo.setGeometry(QgsGeometry(geom))
        novas.append(novo)
    pr.addFeatures(novas)
    saida.updateExtents()

    # Buffer por classe e merge respeitando prioridade
    buffers_por_classe: Dict[int, QgsVectorLayer] = {}
    for app_m, classe in [(30, 1), (50, 2), (100, 3), (200, 4), (500, 5)]:
        sel = processing.run(
            "native:extractbyattribute",
            {
                "INPUT": saida,
                "FIELD": "CLASSE",
                "OPERATOR": 0,
                "VALUE": str(classe),
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]
        if sel.featureCount() == 0:
            continue
        buf = processing.run(
            "native:buffer",
            {
                "INPUT": sel,
                "DISTANCE": app_m,
                "SEGMENTS": 8,
                "DISSOLVE": True,
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]
        buffers_por_classe[classe] = buf

    if not buffers_por_classe:
        return None

    classes_ordenadas = sorted(buffers_por_classe.keys())
    resultado = buffers_por_classe[classes_ordenadas[0]]
    for c in classes_ordenadas[1:]:
        layer = buffers_por_classe[c]
        diff = processing.run(
            "native:difference",
            {"INPUT": resultado, "OVERLAY": layer, "OUTPUT": "memory:"},
        )["OUTPUT"]
        resultado = processing.run(
            "native:mergevectorlayers",
            {"LAYERS": [diff, layer], "CRS": crs_metrico, "OUTPUT": "memory:"},
        )["OUTPUT"]

    return _reprojetar_se_necessario(resultado, camada_tmd.crs())


# ----------------------------------------------------------------------
#                          Junção das APPs
# ----------------------------------------------------------------------

def juncao_app(
    app_massa: Optional[QgsVectorLayer],
    app_trecho: Optional[QgsVectorLayer],
    app_tmd: Optional[QgsVectorLayer],
) -> Optional[QgsVectorLayer]:
    """Une as três APPs gerando saída única, respeitando a prioridade."""
    processing = _processing()
    componentes = [c for c in [app_trecho, app_massa, app_tmd] if c is not None]
    if not componentes:
        return None

    resultado = componentes[0]
    crs = resultado.crs()
    for layer in componentes[1:]:
        layer = _reprojetar_se_necessario(layer, crs)
        diff = processing.run(
            "native:difference",
            {"INPUT": resultado, "OVERLAY": layer, "OUTPUT": "memory:"},
        )["OUTPUT"]
        resultado = processing.run(
            "native:mergevectorlayers",
            {"LAYERS": [diff, layer], "CRS": crs, "OUTPUT": "memory:"},
        )["OUTPUT"]
    return resultado


def hidrografia_e_app_final(
    trecho_drenagem: Optional[QgsVectorLayer],
    trecho_massa_dagua: Optional[QgsVectorLayer],
    massa_dagua: Optional[QgsVectorLayer],
    app_parcial: QgsVectorLayer,
) -> Dict[str, Optional[QgsVectorLayer]]:
    """Equivale à função ``APP_Hidro_Final`` do script ArcPy original.

    * **HIDROGRAFIA**: união de Trecho de Drenagem (buffer de 0,5 m) +
      TMD + Massa d'Água, depois recortada/intersectada com a APP parcial
      para herdar CLASSE/CATEGORIA.
    * **APP_HIDRICA**: APP parcial recortada pela hidrografia (sem
      sobreposição com os corpos d'água).
    """
    processing = _processing()
    crs = app_parcial.crs()

    # 1) Trecho de drenagem em polígono (buffer 0.5 m) + CLASSE 1 / CATEGORIA.
    trecho_poligono = None
    if trecho_drenagem is not None:
        trecho_metrico = _reprojetar_se_necessario(
            trecho_drenagem, _crs_metrico_para(trecho_drenagem)
        )
        trecho_poligono = processing.run(
            "native:buffer",
            {
                "INPUT": trecho_metrico,
                "DISTANCE": 0.5,
                "SEGMENTS": 4,
                "DISSOLVE": False,
                "END_CAP_STYLE": 0,
                "JOIN_STYLE": 0,
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]
        trecho_poligono = _reprojetar_se_necessario(trecho_poligono, crs)
        _atribuir_classe_categoria(
            trecho_poligono, classe=1,
            categoria=CATEGORIAS_POR_CLASSE[1],
        )

    # 2) Massa + TMD em uma única camada de polígono, dissolvido.
    poligonais = [c for c in [trecho_massa_dagua, massa_dagua] if c is not None]
    poligonais = [_reprojetar_se_necessario(c, crs) for c in poligonais]
    hidro_poly: Optional[QgsVectorLayer] = None
    if poligonais:
        merge_ok = processing.run(
            "native:mergevectorlayers",
            {"LAYERS": poligonais, "CRS": crs, "OUTPUT": "memory:"},
        )["OUTPUT"]
        dissolvido = processing.run(
            "native:dissolve",
            {"INPUT": merge_ok, "FIELD": [], "OUTPUT": "memory:"},
        )["OUTPUT"]
        hidro_poly = processing.run(
            "native:multiparttosingleparts",
            {"INPUT": dissolvido, "OUTPUT": "memory:"},
        )["OUTPUT"]

    # 3) Trecho de drenagem buffer 0.5 m menos a hidro poligonal (não duplica).
    if trecho_poligono is not None and hidro_poly is not None:
        trecho_poligono = processing.run(
            "native:difference",
            {
                "INPUT": trecho_poligono,
                "OVERLAY": hidro_poly,
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]

    # 4) Identity: hidro_poly herda CLASSE/CATEGORIA da APP parcial.
    hidro_categorizada = None
    if hidro_poly is not None:
        hidro_id = processing.run(
            "native:intersection",
            {
                "INPUT": hidro_poly,
                "OVERLAY": app_parcial,
                "INPUT_FIELDS": [],
                "OVERLAY_FIELDS": [],
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]
        hidro_categorizada = hidro_id

    # 5) Junta hidro_categorizada + trecho_poligono (buffer 0.5 m do rio < 10 m).
    componentes = [c for c in [hidro_categorizada, trecho_poligono] if c is not None]
    if not componentes:
        hidrografia_final = None
    elif len(componentes) == 1:
        hidrografia_final = componentes[0]
    else:
        hidrografia_final = processing.run(
            "native:mergevectorlayers",
            {"LAYERS": componentes, "CRS": crs, "OUTPUT": "memory:"},
        )["OUTPUT"]

    # 6) APP final = APP parcial - hidrografia (sem sobreposição com o rio).
    app_final = app_parcial
    if hidrografia_final is not None:
        app_final = processing.run(
            "native:difference",
            {
                "INPUT": app_parcial,
                "OVERLAY": hidrografia_final,
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]

    # 7) Para cada CLASSE: une polígonos conectados (dissolve agrupado)
    #    e mantém polígonos desconectados como feições separadas.
    if hidrografia_final is not None:
        hidrografia_final = _dissolver_por_classe_explodir(hidrografia_final)
    if app_final is not None:
        app_final = _dissolver_por_classe_explodir(app_final)

    return {
        "hidrografia": hidrografia_final,
        "app": app_final,
    }


def _dissolver_por_classe_explodir(
    layer: QgsVectorLayer,
) -> QgsVectorLayer:
    """Une polígonos conectados de mesma CLASSE/CATEGORIA e mantém os
    desconectados como feições separadas.

    Pipeline: ``native:dissolve`` agrupado por ['CLASSE', 'CATEGORIA']
    → ``native:multiparttosingleparts``. Se a camada não tiver esses
    campos, faz dissolve sem agrupar.
    """
    if layer is None:
        return layer
    processing = _processing()
    nomes = [f.name() for f in layer.fields()]
    campos = [c for c in ("CLASSE", "CATEGORIA") if c in nomes]
    try:
        dissolvido = processing.run(
            "native:dissolve",
            {"INPUT": layer, "FIELD": campos, "OUTPUT": "memory:"},
        )["OUTPUT"]
        explodido = processing.run(
            "native:multiparttosingleparts",
            {"INPUT": dissolvido, "OUTPUT": "memory:"},
        )["OUTPUT"]
        return explodido
    except Exception:
        return layer


def _salvar_categoria_classe(
    layer: QgsVectorLayer,
    caminho: str,
    formato: str,
    nome_camada: str,
) -> str:
    """Salva a camada mantendo APENAS as colunas CATEGORIA + CLASSE.

    Se a feição já tem CATEGORIA preenchida, ela é mantida; caso contrário,
    deriva o nome a partir do CLASSE pela tabela ``CATEGORIAS_POR_CLASSE``.
    """
    fields = QgsFields()
    fields.append(criar_campo("CATEGORIA", "string"))
    fields.append(criar_campo("CLASSE", "int"))

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = formato
    opts.fileEncoding = "UTF-8"
    opts.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    opts.layerName = nome_camada

    writer = QgsVectorFileWriter.create(
        caminho,
        fields,
        layer.wkbType(),
        layer.crs(),
        QgsProject.instance().transformContext(),
        opts,
    )
    if writer is None:
        raise RuntimeError(f"Não foi possível criar o writer para {caminho}.")
    if writer.hasError() != QgsVectorFileWriter.NoError:
        msg = writer.errorMessage() or f"código {writer.hasError()}"
        del writer
        raise RuntimeError(f"Falha ao criar {caminho}: {msg}")

    nomes = [f.name() for f in layer.fields()]
    idx_classe = nomes.index("CLASSE") if "CLASSE" in nomes else -1
    idx_categoria = nomes.index("CATEGORIA") if "CATEGORIA" in nomes else -1

    erros = 0
    for feat in layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        classe_raw = feat[idx_classe] if idx_classe >= 0 else None
        try:
            classe_int = int(classe_raw) if classe_raw is not None else 0
        except (TypeError, ValueError):
            classe_int = 0
        categoria_raw = feat[idx_categoria] if idx_categoria >= 0 else None
        if categoria_raw is None or str(categoria_raw).strip() == "":
            categoria = CATEGORIAS_POR_CLASSE.get(classe_int, "")
        else:
            categoria = str(categoria_raw).strip()

        novo = QgsFeature(fields)
        novo.setAttribute("CATEGORIA", categoria)
        novo.setAttribute("CLASSE", classe_int)
        novo.setGeometry(QgsGeometry(geom))
        if not writer.addFeature(novo):
            erros += 1
    del writer
    if erros:
        raise RuntimeError(
            f"{erros} feição(ões) não puderam ser escritas em {caminho}."
        )
    return caminho


def gerar_app_hidrica(
    trecho_drenagem: Optional[QgsVectorLayer],
    trecho_massa_dagua: Optional[QgsVectorLayer],
    massa_dagua: Optional[QgsVectorLayer],
    pasta_saida: str,
    campo_categoria_massa: Optional[str] = None,
    valores_reservatorio: Optional[List[str]] = None,
    progresso: Optional[Callable[[int, str], None]] = None,
    formato_saida: str = "ESRI Shapefile",
    extensao: str = ".shp",
) -> ResultadoAppHidrica:
    """Pipeline completa: gera HIDROGRAFIA (categorizada) + APP_HIDRICA."""
    res = ResultadoAppHidrica()
    if not pasta_saida:
        res.sucesso = False
        res.add("Pasta de saída não informada.")
        return res
    os.makedirs(pasta_saida, exist_ok=True)

    def _emit(p: int, m: str) -> None:
        res.add(m)
        if progresso:
            try:
                progresso(p, m)
            except Exception:
                pass

    try:
        app_massa = None
        if massa_dagua is not None:
            _emit(10, "Gerando APP de massa d'água...")
            app_massa = app_massa_dagua(
                massa_dagua, campo_categoria_massa, valores_reservatorio
            )

        app_trecho = None
        if trecho_drenagem is not None:
            _emit(30, "Gerando APP de trechos de drenagem (rios <10 m)...")
            app_trecho = app_trecho_drenagem(trecho_drenagem)

        app_tmd = None
        if trecho_massa_dagua is not None:
            _emit(50, "Gerando APP de trechos de massa d'água (margem dupla)...")
            app_tmd = app_trecho_massa_dagua(trecho_massa_dagua)

        _emit(70, "Unindo as APPs...")
        app_parcial = juncao_app(app_massa, app_trecho, app_tmd)
        if app_parcial is None:
            res.sucesso = False
            res.add(
                "Não foi possível gerar a APP final – nenhuma camada de origem válida."
            )
            return res

        _emit(85, "Gerando hidrografia unificada e recortando APP...")
        saidas = hidrografia_e_app_final(
            trecho_drenagem, trecho_massa_dagua, massa_dagua, app_parcial,
        )
        hidrografia = saidas["hidrografia"]
        app_final = saidas["app"]

        carimbo = datetime.datetime.now().strftime("%Y%m%d_%H%M")

        # Hidrografia
        if hidrografia is not None:
            nome_h = f"HIDROGRAFIA_{carimbo}{extensao}"
            caminho_h = os.path.normpath(os.path.join(pasta_saida, nome_h))
            _salvar_categoria_classe(
                hidrografia, caminho_h, formato_saida,
                f"HIDROGRAFIA_{carimbo}",
            )
            res.camada_hidro_categorizada = QgsVectorLayer(
                caminho_h, f"HIDROGRAFIA_{carimbo}", "ogr"
            )
            res.arquivos.append(caminho_h)
            res.add(f"  ✓ Hidrografia salva: {caminho_h}")

        # APP
        nome_app = f"APP_HIDRICA_{carimbo}{extensao}"
        caminho_app = os.path.normpath(os.path.join(pasta_saida, nome_app))
        _salvar_categoria_classe(
            app_final, caminho_app, formato_saida,
            f"APP_HIDRICA_{carimbo}",
        )
        res.camada_app = QgsVectorLayer(
            caminho_app, f"APP_HIDRICA_{carimbo}", "ogr"
        )
        res.arquivos.append(caminho_app)
        res.add(f"  ✓ APP hídrica salva: {caminho_app}")

        _emit(100, "Concluído.")
    except Exception as exc:
        res.sucesso = False
        res.add(f"Erro durante a geração: {exc}")
    return res
