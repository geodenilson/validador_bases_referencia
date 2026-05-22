"""Janela principal do plugin Validador de Bases de Referência."""

from __future__ import annotations

import json
import os

from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer
from qgis.gui import QgsMapCanvas, QgsMapToolPan, QgsMapToolZoom
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .estilos import (
    HEADER_STYLE,
    FOOTER_STYLE,
    botao_style,
    groupbox_style,
    tab_style,
)
from .tab_validacao import TabValidacao
from .tab_adequacao import TabAdequacao


class MainWindow(QMainWindow):
    """Janela independente do plugin com cabeçalho, abas e rodapé."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.plugin_dir = os.path.dirname(os.path.dirname(__file__))
        self.config = self._carregar_config()

        self._mapas_criados = []

        self._configurar_janela()
        self._criar_ui()

    # ------------------------------------------------------------------ #
    #                          Inicialização                             #
    # ------------------------------------------------------------------ #

    def _carregar_config(self) -> dict:
        path = os.path.join(self.plugin_dir, "config", "config.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _icon_path(self) -> str:
        for nome in ("logo.svg", "logo.png"):
            p = os.path.join(self.plugin_dir, "icones", nome)
            if os.path.exists(p):
                return p
        return ""

    def _configurar_janela(self) -> None:
        self.setWindowTitle("Validador de Bases de Referência")
        # Tamanho mínimo enxuto para permitir uso em telas menores; o
        # tamanho inicial fica maior para acomodar o mapa quando aberto.
        self.setMinimumSize(720, 480)
        self.resize(1280, 820)
        icone = self._icon_path()
        if icone:
            self.setWindowIcon(QIcon(icone))
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )

    # ------------------------------------------------------------------ #
    #                              Layout                                #
    # ------------------------------------------------------------------ #

    def _criar_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._criar_cabecalho())

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(tab_style())

        self.tab_validacao = TabValidacao(self)
        self.tab_adequacao = TabAdequacao(self)

        self.tabs.addTab(self.tab_validacao, "✔ Avaliação da Qualidade")
        self.tabs.addTab(self.tab_adequacao, "📐 Adequação de Bases")

        layout.addWidget(self.tabs, 1)
        layout.addWidget(self._criar_rodape())

    def _criar_cabecalho(self) -> QFrame:
        header = QFrame()
        header.setStyleSheet(HEADER_STYLE)
        header.setFixedHeight(72)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 8, 20, 8)

        icone = self._icon_path()
        if icone:
            logo = QLabel()
            pix = QPixmap(icone).scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo.setPixmap(pix)
            layout.addWidget(logo)

        bloco_titulo = QVBoxLayout()
        bloco_titulo.setSpacing(2)
        titulo = QLabel("VALIDADOR DE BASES DE REFERÊNCIA")
        titulo.setStyleSheet(
            "color: white; font-size: 20px; font-weight: bold; background: transparent;"
        )
        sub = QLabel("Validação de qualidade  •  Adequação de bases  •  Geração de APP hídrica")
        sub.setStyleSheet("color: #b0c4d6; font-size: 11px; background: transparent;")
        bloco_titulo.addWidget(titulo)
        bloco_titulo.addWidget(sub)
        layout.addLayout(bloco_titulo)

        layout.addStretch()

        btn_ajuda = QPushButton("Ajuda")
        btn_ajuda.setStyleSheet(botao_style("secondary"))
        btn_ajuda.clicked.connect(self._mostrar_ajuda)
        layout.addWidget(btn_ajuda)

        return header

    def _criar_rodape(self) -> QFrame:
        footer = QFrame()
        footer.setStyleSheet(FOOTER_STYLE)
        footer.setFixedHeight(26)

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(15, 4, 15, 4)

        versao = QLabel(f"v{self.config.get('version', '1.0.0')}")
        versao.setStyleSheet("color: #7f8c8d; font-size: 10px; background: transparent;")
        layout.addWidget(versao)

        layout.addStretch()

        copyright_lbl = QLabel("© 2026 — Validador de Bases de Referência")
        copyright_lbl.setStyleSheet("color: #7f8c8d; font-size: 10px; background: transparent;")
        layout.addWidget(copyright_lbl)
        return footer

    # ------------------------------------------------------------------ #
    #                          Helpers (mapa)                            #
    # ------------------------------------------------------------------ #

    def criar_canvas_mapa(self) -> QgsMapCanvas:
        """Cria um QgsMapCanvas pronto para uso (com pan/zoom).

        O canvas inicia com OpenStreetMap de fundo centralizado no Brasil.
        """
        canvas = QgsMapCanvas()
        canvas.setCanvasColor(QColor(245, 245, 245))
        canvas.enableAntiAliasing(True)
        pan = QgsMapToolPan(canvas)
        canvas.setMapTool(pan)
        canvas._pan_tool = pan
        canvas._zoom_in = QgsMapToolZoom(canvas, False)
        canvas._zoom_out = QgsMapToolZoom(canvas, True)
        self._mapas_criados.append(canvas)
        self._aplicar_fundo_osm(canvas)
        return canvas

    def _aplicar_fundo_osm(self, canvas: QgsMapCanvas) -> None:
        """Carrega OpenStreetMap de fundo e centraliza no Brasil."""
        from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

        osm_cfg = self.config.get("wms_services", {}).get("OpenStreetMap", {})
        uri = osm_cfg.get("url", "")
        if not uri:
            uri = (
                "type=xyz&url=https://tile.openstreetmap.org/"
                "{z}/{x}/{y}.png&zmax=19&zmin=0"
            )
        osm = QgsRasterLayer(uri, "OpenStreetMap", "wms")
        if osm.isValid():
            QgsProject.instance().addMapLayer(osm, False)
            canvas.setLayers([osm])
            canvas._osm_layer = osm
            crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
            canvas.setDestinationCrs(crs_4326)
            brasil_extent = QgsRectangle(-74.0, -34.0, -34.0, 6.0)
            canvas.setExtent(brasil_extent)
            canvas.refresh()

    def adicionar_camada_no_canvas(self, canvas: QgsMapCanvas, camada) -> None:
        """Adiciona/insere uma camada no canvas e ajusta extensão.

        A camada de fundo OSM (se existir) é mantida no final da lista
        para ficar sob todas as outras.
        """
        if camada is None:
            return
        camadas = list(canvas.layers())
        osm = getattr(canvas, "_osm_layer", None)
        if camada not in camadas:
            if osm is not None and osm in camadas:
                camadas.remove(osm)
                camadas.insert(0, camada)
                camadas.append(osm)
            else:
                camadas.insert(0, camada)
        canvas.setLayers(camadas)
        canvas.setDestinationCrs(camada.crs())
        canvas.setExtent(camada.extent())
        canvas.refresh()

    def carregar_xyz(self, uri: str, nome: str) -> QgsRasterLayer:
        layer = QgsRasterLayer(uri, nome, "wms")
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer, False)
            return layer
        return None

    # ------------------------------------------------------------------ #
    #                              Eventos                               #
    # ------------------------------------------------------------------ #

    def _mostrar_ajuda(self) -> None:
        QMessageBox.information(
            self,
            "Ajuda - Validador de Bases de Referência",
            (
                "<h3>Como usar</h3>"
                "<ol>"
                "<li><b>Avaliação da Qualidade</b>: gere pontos amostrais a "
                "partir de uma camada de uso/cobertura, rotule cada ponto "
                "com a verdade observada, calcule a matriz de confusão e o "
                "Kappa, e gere o parecer (incluindo módulo de quadrantes "
                "1×1 km para PEC).</li>"
                "<li><b>Adequação de Bases</b>: organize suas bases conforme "
                "o padrão escolhido (novos padrões serão adicionados no "
                "futuro). Inclui o assistente "
                "<i>Categorizar Hidrografia e Gerar APP</i>, que produz a APP "
                "conforme a Lei 12.651/2012.</li>"
                "</ol>"
            ),
        )

    def closeEvent(self, event):
        # Garante que canvases pendurados sejam liberados.
        try:
            for c in self._mapas_criados:
                try:
                    c.setLayers([])
                except Exception:
                    pass
        finally:
            super().closeEvent(event)
