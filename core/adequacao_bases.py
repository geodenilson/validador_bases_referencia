"""Adequação de bases ao padrão escolhido.

Funcionalidades cobertas:
    * Bases individuais de uso/cobertura: separação em shapefiles individuais
      com nomes padronizados (AML_FLORESTA, VEGETACAO_2008 etc).
    * Base de hidrografia unificada com coluna ``CLASSE`` (1 a 8).
    * Bases unificadas (APP_HIDRICA, APP_ESPECIAL, RELEVO, SERVIDAO,
      USO_RESTRITO) com a coluna ``CLASSE``.
    * Validação de geometria (RepairGeometry) e reprojeção para
      SIRGAS 2000 (EPSG:4674).
    * Subdivisão de polígonos com mais de 500 vértices.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
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


def _nome_tipo_geom(wkb_type) -> str:
    """Retorna o nome do tipo de geometria sem Z/M para URIs de memória.

    Ex.: PolygonZM → "Polygon"; MultiPolygonZ → "MultiPolygon".
    Usar ``flatType`` é seguro; tentar fazer split por "M" quebra a
    palavra "Multi" (bug clássico).
    """
    try:
        flat = QgsWkbTypes.flatType(wkb_type)
        return QgsWkbTypes.displayString(flat) or "Polygon"
    except Exception:
        return "Polygon"

# Estruturas das bases unificadas.
ESTRUTURA_HIDROGRAFIA = [
    (1, "Rio até 10 m"),
    (2, "Rio de 10 a 50 m"),
    (3, "Rio de 50 a 200 m"),
    (4, "Rio de 200 a 600 m"),
    (5, "Rio acima de 600 m"),
    (6, "Lago natural"),
    (7, "Reservatório artificial"),
    (8, "Nascente"),
]
ESTRUTURA_APP_HIDRICA = [(c, n.replace("Rio", "APP de rio").replace("Lago", "APP de lago").replace("Reservatório", "APP de reservatório").replace("Nascente", "APP de nascente"))
                        for c, n in ESTRUTURA_HIDROGRAFIA]
ESTRUTURA_APP_ESPECIAL = [
    (1, "Reservatório de energia até 24/08/2001"),
    (2, "Restinga"),
    (3, "Vereda"),
    (4, "Banhado"),
    (5, "Manguezal"),
    (6, "APP de reservatório de energia até 24/08/2001"),
    (7, "APP de restinga"),
    (8, "APP de vereda"),
    (9, "APP de banhado"),
    (10, "APP de manguezal"),
]
ESTRUTURA_RELEVO = [
    (1, "Altitude superior a 1.800 m"),
    (2, "Borda de chapada"),
    (3, "Declividade maior que 45°"),
    (4, "Topo de morro"),
    (5, "APP de altitude superior a 1.800 m"),
    (6, "APP de borda de chapada"),
    (7, "APP de declividade maior que 45°"),
    (8, "APP de topo de morro"),
]
ESTRUTURA_SERVIDAO = [
    (1, "Infraestrutura pública"),
    (2, "Utilidade pública"),
    (3, "Reservatório de energia até 24/08/2001"),
    (4, "Entorno de reservatório de abastecimento/geração de energia"),
]
ESTRUTURA_USO_RESTRITO = [
    (1, "Declividade entre 25° e 45°"),
    (2, "Área pantaneira"),
]

NOMES_BASES_INDIVIDUAIS = [
    "AML_FLORESTA",
    "AML_CERRADO",
    "AML_CAMPO",
    "VEGETACAO_2008",
    "VEGETACAO_ATUAL",
    "ANTROPIZADO",
    "CONSOLIDADO",
]


@dataclass
class ResultadoAdequacao:
    arquivos_gerados: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    sucesso: bool = True

    def add(self, msg: str) -> None:
        self.log.append(msg)


def _reprojetar_para_sirgas(camada: QgsVectorLayer) -> QgsVectorLayer:
    """Retorna uma cópia em memória da camada reprojetada para SIRGAS 2000."""
    crs_destino = QgsCoordinateReferenceSystem(SIRGAS_2000)
    if camada.crs() == crs_destino:
        return camada
    transform = QgsCoordinateTransform(camada.crs(), crs_destino, QgsProject.instance())

    geom_type = _nome_tipo_geom(camada.wkbType())
    uri = f"{geom_type}?crs={SIRGAS_2000}"
    nova = QgsVectorLayer(uri, camada.name(), "memory")
    pr = nova.dataProvider()
    pr.addAttributes(camada.fields())
    nova.updateFields()
    feats: List[QgsFeature] = []
    for feat in camada.getFeatures():
        novo = QgsFeature(nova.fields())
        novo.setAttributes(feat.attributes())
        geom = QgsGeometry(feat.geometry())
        if not geom.isEmpty():
            geom.transform(transform)
        novo.setGeometry(geom)
        feats.append(novo)
    pr.addFeatures(feats)
    nova.updateExtents()
    return nova


def _reparar_geometrias(camada: QgsVectorLayer) -> QgsVectorLayer:
    """Repara geometrias inválidas usando o algoritmo nativo do QGIS."""
    try:
        import processing  # type: ignore
    except Exception:
        return camada
    try:
        # Repete duas vezes conforme prática recomendada.
        params = {"INPUT": camada, "METHOD": 1, "OUTPUT": "memory:"}
        out1 = processing.run("native:fixgeometries", params)["OUTPUT"]
        params2 = {"INPUT": out1, "METHOD": 1, "OUTPUT": "memory:"}
        out2 = processing.run("native:fixgeometries", params2)["OUTPUT"]
        return out2
    except Exception:
        return camada


def _exportar(camada: QgsVectorLayer, caminho: str, formato: str = "ESRI Shapefile") -> str:
    """Salva a camada para disco no formato indicado."""
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = formato
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

    err = QgsVectorFileWriter.writeAsVectorFormatV2(
        camada,
        caminho,
        QgsProject.instance().transformContext(),
        options,
    )
    # writeAsVectorFormatV2 retorna (errCode, errMessage) — extraímos os dois.
    err_code = err[0] if isinstance(err, tuple) else err
    err_msg = err[1] if isinstance(err, tuple) and len(err) > 1 else ""
    if err_code != QgsVectorFileWriter.NoError:
        raise RuntimeError(
            f"Falha ao exportar {caminho}: código {err_code} {err_msg}".strip()
        )
    return caminho


def separar_uso_solo_em_individuais(
    camada: QgsVectorLayer,
    campo_classe: str,
    mapeamento: Dict[str, str],
    pasta_saida: str,
    formato: str = "ESRI Shapefile",
    extensao: str = ".shp",
) -> ResultadoAdequacao:
    """Separa uma camada de uso/cobertura em shapefiles individuais.

    ``mapeamento`` é um dicionário {valor_atual_no_campo: NOME_BASE_FINAL}
    onde NOME_BASE_FINAL é um dos nomes padronizados (ex.: VEGETACAO_2008).
    """
    # Converte para o formato multi-valores e delega.
    valores_por_destino: Dict[str, List[str]] = {}
    for valor, nome in mapeamento.items():
        if not nome:
            continue
        valores_por_destino.setdefault(nome, []).append(str(valor))
    return separar_em_individuais_multi(
        camada, campo_classe, valores_por_destino,
        pasta_saida, formato, extensao,
    )


def separar_em_individuais_multi(
    camada: QgsVectorLayer,
    campo_classe: str,
    valores_por_destino: Dict[str, List[str]],
    pasta_saida: str,
    formato: str = "ESRI Shapefile",
    extensao: str = ".shp",
    max_vertices: Optional[int] = None,
) -> ResultadoAdequacao:
    """Separa uma camada em shapefiles individuais, agrupando múltiplos
    valores em cada arquivo de saída.

    ``valores_por_destino`` = {NOME_DESTINO: [valor1, valor2, …]}.
    ``max_vertices`` (opcional): se informado, aplica ``native:subdivide``
    (equivalente ao Dice do ArcGIS Pro) em cada arquivo gerado, quebrando
    polígonos com mais de N vértices.

    Mantém o CRS original (sem reprojeção) e não repara geometrias —
    é apenas um filtro + export rápido.
    """
    res = ResultadoAdequacao()
    if camada is None or not camada.isValid():
        res.sucesso = False
        res.add("Camada inválida.")
        return res
    try:
        os.makedirs(pasta_saida, exist_ok=True)
    except Exception as exc:
        res.sucesso = False
        res.add(f"Erro ao criar pasta de saída: {exc}")
        return res

    if campo_classe not in [f.name() for f in camada.fields()]:
        res.sucesso = False
        res.add(f"Campo '{campo_classe}' não existe na camada.")
        return res

    # Pré-indexa feições por valor (1 passada na camada).
    indice: Dict[str, List["QgsFeature"]] = {}
    for feat in camada.getFeatures():
        valor = feat[campo_classe]
        if valor is None:
            continue
        indice.setdefault(str(valor), []).append(feat)

    gerou_algo = False
    for nome_base, valores in valores_por_destino.items():
        if not nome_base or not valores:
            continue
        feats_alvo: List["QgsFeature"] = []
        for v in valores:
            feats_alvo.extend(indice.get(str(v), []))

        if not feats_alvo:
            res.add(
                f"  • {nome_base}: nenhuma feição para "
                f"{sorted(set(str(v) for v in valores))}."
            )
            continue

        caminho = os.path.join(pasta_saida, f"{nome_base}{extensao}")
        try:
            n_escritas = _exportar_selecao(
                camada, feats_alvo, caminho, formato, nome_base,
                max_vertices=max_vertices,
            )
            res.arquivos_gerados.append(caminho)
            res.add(
                f"  ✓ {nome_base}: {n_escritas} feições → {caminho}"
            )
            gerou_algo = True
        except Exception as exc:
            res.sucesso = False
            res.add(f"  ✗ {nome_base}: erro ao exportar – {exc}")

    if not res.arquivos_gerados:
        res.sucesso = False

    return res


def _exportar_selecao(
    camada_origem: QgsVectorLayer,
    feats: List["QgsFeature"],
    caminho: str,
    formato: str,
    nome_camada: str,
    max_vertices: Optional[int] = None,
) -> int:
    """Escreve as feições recebidas em um arquivo vetorial.

    Se ``max_vertices`` for informado e > 0, aplica ``native:subdivide``
    (equivalente ao Dice do ArcGIS) na camada antes de gravar.

    Retorna o número de feições escritas.
    """
    caminho = os.path.normpath(caminho)
    pasta = os.path.dirname(caminho)
    if pasta:
        os.makedirs(pasta, exist_ok=True)

    # Cria camada em memória com as feições escolhidas (necessário para
    # aplicar subdivide e/ou padronizar a saída).
    geom_type = _nome_tipo_geom(camada_origem.wkbType())
    crs_str = camada_origem.crs().authid() or SIRGAS_2000
    uri = f"{geom_type}?crs={crs_str}"
    mem = QgsVectorLayer(uri, nome_camada, "memory")
    pr = mem.dataProvider()
    pr.addAttributes(camada_origem.fields())
    mem.updateFields()
    novos = []
    for feat in feats:
        novo = QgsFeature(mem.fields())
        novo.setAttributes(feat.attributes())
        geom = feat.geometry()
        if geom is not None and not geom.isEmpty():
            novo.setGeometry(QgsGeometry(geom))
        novos.append(novo)
    if novos:
        pr.addFeatures(novos)
    mem.updateExtents()

    # Subdivide se solicitado.
    if max_vertices and max_vertices > 0:
        sub = subdividir_poligonos_complexos(mem, int(max_vertices))
        if sub is not None and sub.isValid():
            mem = sub

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = formato
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    options.layerName = nome_camada

    writer = QgsVectorFileWriter.create(
        caminho,
        mem.fields(),
        mem.wkbType(),
        mem.crs(),
        QgsProject.instance().transformContext(),
        options,
    )
    if writer is None:
        raise RuntimeError(f"Não foi possível criar o writer para {caminho}.")
    if writer.hasError() != QgsVectorFileWriter.NoError:
        err = writer.errorMessage() or f"código {writer.hasError()}"
        del writer
        raise RuntimeError(f"Falha ao criar {caminho}: {err}")

    escritas = 0
    erros = 0
    for feat in mem.getFeatures():
        if writer.addFeature(feat):
            escritas += 1
        else:
            erros += 1
    del writer
    if erros:
        raise RuntimeError(
            f"{erros} feição(ões) não puderam ser escritas em {caminho}."
        )
    return escritas


def gerar_unificada_com_classe(
    camada: QgsVectorLayer,
    campo_classe_origem: str,
    valores_por_classe: Dict[int, List[str]],
    nome_saida: str,
    pasta_saida: str,
    formato: str = "ESRI Shapefile",
    extensao: str = ".shp",
    max_vertices: Optional[int] = None,
) -> ResultadoAdequacao:
    """Gera uma base unificada com coluna CLASSE a partir de uma única
    camada de entrada.

    Para cada CLASSE numérica do padrão, ``valores_por_classe`` indica
    quais valores do ``campo_classe_origem`` pertencem a essa classe.
    O resultado é UM único shapefile (com coluna CLASSE).

    Mantém o CRS original (sem reprojeção) e não repara geometrias.
    """
    res = ResultadoAdequacao()
    if camada is None or not camada.isValid():
        res.sucesso = False
        res.add("Camada inválida.")
        return res
    try:
        os.makedirs(pasta_saida, exist_ok=True)
    except Exception as exc:
        res.sucesso = False
        res.add(f"Erro ao criar pasta de saída: {exc}")
        return res

    if campo_classe_origem not in [f.name() for f in camada.fields()]:
        res.sucesso = False
        res.add(f"Campo '{campo_classe_origem}' não existe na camada.")
        return res

    # Saída: APENAS a coluna CLASSE.
    fields = QgsFields()
    fields.append(criar_campo("CLASSE", "int"))

    valor_para_classe: Dict[str, int] = {}
    for classe, valores in valores_por_classe.items():
        for v in valores:
            valor_para_classe[str(v)] = int(classe)

    caminho = os.path.normpath(
        os.path.join(pasta_saida, f"{nome_saida}{extensao}")
    )

    # Constrói camada em memória primeiro (necessário para opcionalmente
    # aplicar subdivide antes do salvamento).
    geom_type = _nome_tipo_geom(camada.wkbType())
    crs_str = camada.crs().authid() or SIRGAS_2000
    mem_uri = f"{geom_type}?crs={crs_str}"
    mem = QgsVectorLayer(mem_uri, nome_saida, "memory")
    pr_mem = mem.dataProvider()
    pr_mem.addAttributes(fields)
    mem.updateFields()

    novos: List[QgsFeature] = []
    contagem: Dict[int, int] = {}
    for feat in camada.getFeatures():
        valor = feat[campo_classe_origem]
        if valor is None:
            continue
        classe = valor_para_classe.get(str(valor))
        if classe is None:
            continue
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        novo = QgsFeature(mem.fields())
        novo.setAttribute("CLASSE", int(classe))
        novo.setGeometry(QgsGeometry(geom))
        novos.append(novo)
        contagem[classe] = contagem.get(classe, 0) + 1
    if novos:
        pr_mem.addFeatures(novos)
    mem.updateExtents()

    if not novos:
        res.sucesso = False
        res.add("Nenhuma feição correspondente aos valores selecionados.")
        return res

    # Subdivide opcional.
    if max_vertices and max_vertices > 0:
        sub = subdividir_poligonos_complexos(mem, int(max_vertices))
        if sub is not None and sub.isValid():
            mem = sub

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = formato
    options.fileEncoding = "UTF-8"
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    options.layerName = nome_saida

    writer = QgsVectorFileWriter.create(
        caminho,
        mem.fields(),
        mem.wkbType(),
        mem.crs(),
        QgsProject.instance().transformContext(),
        options,
    )
    if writer is None:
        res.sucesso = False
        res.add(f"Não foi possível criar o writer para {caminho}.")
        return res
    if writer.hasError() != QgsVectorFileWriter.NoError:
        msg = writer.errorMessage() or f"código {writer.hasError()}"
        del writer
        res.sucesso = False
        res.add(f"Falha ao criar {caminho}: {msg}")
        return res

    total = 0
    erros = 0
    for feat in mem.getFeatures():
        if writer.addFeature(feat):
            total += 1
        else:
            erros += 1
    del writer  # finaliza/fecha o arquivo

    for c, n in sorted(contagem.items()):
        res.add(f"  • CLASSE {c}: {n} feições antes de subdividir.")
    if max_vertices and max_vertices > 0:
        res.add(
            f"  ↳ após subdividir (≤ {int(max_vertices)} vértices): "
            f"{total} feições."
        )
    if erros:
        res.add(f"  ⚠ {erros} feição(ões) não puderam ser escritas.")

    res.arquivos_gerados.append(caminho)
    res.add(
        f"  ✓ Base unificada {nome_saida} ({total} feições) → {caminho}"
    )
    return res


def gerar_base_unificada(
    camadas_origem: List[Tuple[QgsVectorLayer, int]],
    nome_saida: str,
    pasta_saida: str,
    formato: str = "ESRI Shapefile",
    extensao: str = ".shp",
    geom_type_padrao: str = "Polygon",
) -> ResultadoAdequacao:
    """Funde várias camadas em uma única base unificada com coluna ``CLASSE``.

    Cada item de ``camadas_origem`` é uma tupla (camada, valor_classe).
    Reprojetadas para SIRGAS 2000 e geometrias reparadas.
    """
    res = ResultadoAdequacao()
    os.makedirs(pasta_saida, exist_ok=True)

    if not camadas_origem:
        res.sucesso = False
        res.add("Nenhuma camada de origem informada.")
        return res

    fields = QgsFields()
    fields.append(criar_campo("CLASSE", "int"))

    uri = f"{geom_type_padrao}?crs={SIRGAS_2000}"
    unificada = QgsVectorLayer(uri, nome_saida, "memory")
    pr = unificada.dataProvider()
    pr.addAttributes(fields)
    unificada.updateFields()

    total = 0
    for camada, classe in camadas_origem:
        if camada is None or not camada.isValid():
            res.add(f"  • Camada inválida ignorada (classe={classe}).")
            continue
        camada = _reprojetar_para_sirgas(camada)
        camada = _reparar_geometrias(camada)
        feats = []
        for feat in camada.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            novo = QgsFeature(unificada.fields())
            novo.setAttribute("CLASSE", int(classe))
            novo.setGeometry(QgsGeometry(geom))
            feats.append(novo)
        pr.addFeatures(feats)
        total += len(feats)
        res.add(f"  ✓ Classe {classe}: {len(feats)} feições adicionadas.")

    unificada.updateExtents()
    caminho = os.path.join(pasta_saida, f"{nome_saida}{extensao}")
    try:
        _exportar(unificada, caminho, formato)
        res.arquivos_gerados.append(caminho)
        res.add(f"Base unificada {nome_saida} salva em {caminho} (total: {total}).")
    except Exception as exc:
        res.sucesso = False
        res.add(f"Erro ao exportar base unificada: {exc}")
    return res


def subdividir_poligonos_complexos(
    camada: QgsVectorLayer,
    max_vertices: int = 500,
) -> Optional[QgsVectorLayer]:
    """Subdivide polígonos com mais de ``max_vertices`` vértices.

    Usa o algoritmo nativo ``native:subdivide`` do QGIS.
    """
    try:
        import processing  # type: ignore
    except Exception:
        return None
    try:
        params = {"INPUT": camada, "MAX_NODES": max_vertices, "OUTPUT": "memory:"}
        out = processing.run("native:subdivide", params)["OUTPUT"]
        return out
    except Exception:
        return None


def remover_sobreposicao(
    camada_base: QgsVectorLayer,
    camada_prevalente: QgsVectorLayer,
) -> Optional[QgsVectorLayer]:
    """Aplica diferença geométrica (Erase) usando uma base prevalente.

    Para a hierarquia: SERVIDAO > HIDROGRAFIA > APP_HIDRICA > RELEVO/USO/APP_ESPECIAL > Uso do solo.
    """
    try:
        import processing  # type: ignore
    except Exception:
        return None
    try:
        params = {
            "INPUT": camada_base,
            "OVERLAY": camada_prevalente,
            "OUTPUT": "memory:",
        }
        out = processing.run("native:difference", params)["OUTPUT"]
        return out
    except Exception:
        return None


def converter_linhas_e_pontos_para_poligonos(
    camada_origem: QgsVectorLayer,
    classe: int,
    buffer_metros: float = 0.5,
    nome_saida: str = "poligonos",
) -> Optional[QgsVectorLayer]:
    """Converte rios menores que 10 m (linha) e nascentes (ponto) em polígonos.

    Aplica buffer pequeno (0.5 m por padrão) e atribui a coluna CLASSE,
    conforme práticas de conversão para bases poligonais.
    """
    try:
        import processing  # type: ignore
    except Exception:
        return None
    try:
        params = {
            "INPUT": camada_origem,
            "DISTANCE": buffer_metros,
            "SEGMENTS": 5,
            "END_CAP_STYLE": 0,
            "JOIN_STYLE": 0,
            "MITER_LIMIT": 2,
            "DISSOLVE": False,
            "OUTPUT": "memory:",
        }
        out = processing.run("native:buffer", params)["OUTPUT"]
        # Adiciona coluna CLASSE
        pr = out.dataProvider()
        if "CLASSE" not in [f.name() for f in out.fields()]:
            pr.addAttributes([criar_campo("CLASSE", "int")])
            out.updateFields()
        idx = out.fields().indexFromName("CLASSE")
        out.startEditing()
        for feat in out.getFeatures():
            out.changeAttributeValue(feat.id(), idx, int(classe))
        out.commitChanges()
        out.setName(nome_saida)
        return out
    except Exception:
        return None


# ----------------------------------------------------------------------
#                       PADRÃO Análise Dinamizada (CAR)
# ----------------------------------------------------------------------

PADROES_ADEQUACAO = {
    "ad_car": {
        "nome": "Base para Análise Dinamizada do CAR",
        "crs": SIRGAS_2000,
        "uso_solo_individuais": NOMES_BASES_INDIVIDUAIS,
        "bases_unificadas": {
            "HIDROGRAFIA": ESTRUTURA_HIDROGRAFIA,
            "APP_HIDRICA": ESTRUTURA_APP_HIDRICA,
            "APP_ESPECIAL": ESTRUTURA_APP_ESPECIAL,
            "RELEVO": ESTRUTURA_RELEVO,
            "SERVIDAO": ESTRUTURA_SERVIDAO,
            "USO_RESTRITO": ESTRUTURA_USO_RESTRITO,
        },
    }
}


def listar_padroes() -> List[Tuple[str, str]]:
    """Lista (id, nome) dos padrões de adequação disponíveis."""
    return [(pid, p["nome"]) for pid, p in PADROES_ADEQUACAO.items()]


def estrutura_padrao(padrao_id: str) -> Optional[Dict[str, object]]:
    return PADROES_ADEQUACAO.get(padrao_id)
