"""Widget de avaliação de outras bases (não uso-do-solo).

Reorganização baseada em três grupos:
    1. **Camadas** — base a avaliar + mapa de fundo (Google/Esri/Planet).
    2. **Amostragem** — tamanho do quadrante, número de quadrantes e botão.
    3. **Julgamento dos quadrantes** — navegação, medição efêmera de distância
       (clica-arrasta-solta com label flutuante), contadores de erros
       temáticos (omissão/comissão) e botões de aprovação/reprovação.
    4. **Botão "Exibir resultados"** — abre modal com estatísticas brutas
       e permite ao usuário escolher livremente PEC e LQA de referência.
"""

from __future__ import annotations

import os
from typing import List, Optional

from qgis.core import (
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFillSymbol,
    QgsGeometry,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPointXY,
    QgsProject,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core import amostragem
from .dialog_resultados_quadrantes import DialogResultadosQuadrantes
from .estilos import botao_style, groupbox_style
from .helpers_basemap import BasemapManager


class FerramentaMedirArrastando(QgsMapTool):
    """Mede distância em tempo real enquanto o usuário arrasta o mouse.

    Comportamento:
        * Pressiona o botão → marca o início.
        * Arrasta → desenha linha + label flutuante com a distância.
        * Solta → a linha e o label desaparecem.
    """

    def __init__(self, canvas: QgsMapCanvas, callback_solta=None):
        super().__init__(canvas)
        self.canvas = canvas
        self.callback_solta = callback_solta
        self.rubber = QgsRubberBand(canvas, QgsWkbTypes.LineGeometry)
        self.rubber.setColor(QColor(231, 76, 60))
        self.rubber.setWidth(3)

        self.label_flutuante = QLabel(canvas)
        self.label_flutuante.setStyleSheet(
            "QLabel { background: rgba(0,0,0,180); color: white; "
            "padding: 4px 8px; border-radius: 4px; font-weight: bold; }"
        )
        self.label_flutuante.hide()

        self.medindo: bool = False
        self.p_inicial: Optional[QgsPointXY] = None
        self.setCursor(Qt.CrossCursor)

    # ------------------------ eventos do mouse ----------------------------
    def canvasPressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.medindo = True
        ponto_map = self.toMapCoordinates(event.pos())
        self.p_inicial = QgsPointXY(ponto_map.x(), ponto_map.y())
        self.rubber.reset(QgsWkbTypes.LineGeometry)
        self.rubber.addPoint(self.p_inicial, True)

    def canvasMoveEvent(self, event):
        if not self.medindo or self.p_inicial is None:
            return
        ponto_map = self.toMapCoordinates(event.pos())
        p_atual = QgsPointXY(ponto_map.x(), ponto_map.y())
        self.rubber.reset(QgsWkbTypes.LineGeometry)
        self.rubber.addPoint(self.p_inicial, False)
        self.rubber.addPoint(p_atual, True)

        d = self._calc_distancia(self.p_inicial, p_atual)
        self.label_flutuante.setText(f"{d:.2f} m")
        self.label_flutuante.adjustSize()
        pos = event.pos()
        self.label_flutuante.move(pos.x() + 14, pos.y() + 14)
        self.label_flutuante.show()
        self.label_flutuante.raise_()

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.medindo and self.p_inicial is not None:
            ponto_map = self.toMapCoordinates(event.pos())
            p_final = QgsPointXY(ponto_map.x(), ponto_map.y())
            d = self._calc_distancia(self.p_inicial, p_final)
            if self.callback_solta:
                try:
                    self.callback_solta(d)
                except Exception:
                    pass
        self.medindo = False
        self.p_inicial = None
        self.rubber.reset(QgsWkbTypes.LineGeometry)
        self.label_flutuante.hide()

    def _calc_distancia(self, p1: QgsPointXY, p2: QgsPointXY) -> float:
        try:
            d = QgsDistanceArea()
            d.setSourceCrs(
                self.canvas.mapSettings().destinationCrs(),
                QgsProject.instance().transformContext(),
            )
            d.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
            return d.measureLine(p1, p2)
        except Exception:
            return 0.0

    def deactivate(self):
        try:
            self.rubber.reset(QgsWkbTypes.LineGeometry)
            self.label_flutuante.hide()
        except Exception:
            pass
        super().deactivate()


class WidgetQuadrantes(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window

        self.camada_avaliada: Optional[QgsVectorLayer] = None
        self.camada_quadrantes: Optional[QgsVectorLayer] = None
        self.ids_amostrados: List[int] = []
        self.indice_atual: int = 0

        self.tool_medir: Optional[FerramentaMedirArrastando] = None
        self.ultima_distancia: float = 0.0
        self.rubber_quadrante_atual: Optional[QgsRubberBand] = None
        self.basemap: Optional[BasemapManager] = None

        self._build_ui()

    # ------------------------------------------------------------------ #
    #                              UI                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        splitter = QSplitter(Qt.Horizontal)

        scroll_painel = QScrollArea()
        scroll_painel.setWidgetResizable(True)
        scroll_painel.setMaximumWidth(460)
        painel = QWidget()
        scroll_painel.setWidget(painel)
        v = QVBoxLayout(painel)
        v.setSpacing(8)

        # Canvas precisa existir antes do bloco Camadas (combo de fundo o usa)
        self.canvas = self.mw.criar_canvas_mapa()

        v.addWidget(self._criar_bloco_camadas())
        v.addWidget(self._criar_bloco_amostragem())
        v.addWidget(self._criar_bloco_julgamento())

        self.btn_resultados = QPushButton("📊 Exibir resultados")
        self.btn_resultados.setStyleSheet(botao_style("primary"))
        self.btn_resultados.clicked.connect(self._abrir_resultados)
        v.addWidget(self.btn_resultados)

        v.addStretch()

        splitter.addWidget(scroll_painel)
        splitter.addWidget(self.canvas)
        splitter.setSizes([440, 900])
        layout.addWidget(splitter)

    def _wrap(self, lay) -> QWidget:
        w = QWidget()
        w.setLayout(lay)
        return w

    # --------------------------- Camadas --------------------------------
    def _criar_bloco_camadas(self) -> QGroupBox:
        gb = QGroupBox("📂 Camadas")
        gb.setStyleSheet(groupbox_style())
        f = QFormLayout(gb)

        h_cam = QHBoxLayout()
        self.input_camada = QLineEdit()
        self.input_camada.setPlaceholderText(
            "Camada vetorial a avaliar (.shp/.gpkg)"
        )
        btn = QPushButton("Procurar…")
        btn.setMinimumWidth(80)
        btn.clicked.connect(self._escolher_camada_avaliada)
        h_cam.addWidget(self.input_camada)
        h_cam.addWidget(btn)
        f.addRow("Base a avaliar:", self._wrap(h_cam))

        self.basemap = BasemapManager(self, self.canvas, self.mw.config)
        combo = self.basemap.criar_combo()
        f.addRow("Mapa de fundo:", combo)
        return gb

    # ------------------------- Amostragem -------------------------------
    def _criar_bloco_amostragem(self) -> QGroupBox:
        gb = QGroupBox("🎯 Amostragem")
        gb.setStyleSheet(groupbox_style())
        f = QFormLayout(gb)

        self.spin_tamanho = QSpinBox()
        self.spin_tamanho.setRange(50, 100000)
        self.spin_tamanho.setValue(1000)
        self.spin_tamanho.setSuffix(" m")
        f.addRow("Tamanho do quadrante:", self.spin_tamanho)

        self.spin_n_amostras = QSpinBox()
        self.spin_n_amostras.setRange(1, 100000)
        self.spin_n_amostras.setValue(20)
        f.addRow("Quadrantes a sortear:", self.spin_n_amostras)

        self.btn_gerar_quadrantes = QPushButton("Gerar quadrantes")
        self.btn_gerar_quadrantes.setStyleSheet(botao_style("success"))
        self.btn_gerar_quadrantes.clicked.connect(self._gerar_quadrantes)
        f.addRow(self.btn_gerar_quadrantes)

        self.barra_gerar = QProgressBar()
        self.barra_gerar.setRange(0, 100)
        self.barra_gerar.setValue(0)
        self.barra_gerar.setTextVisible(True)
        self.barra_gerar.setVisible(False)
        f.addRow(self.barra_gerar)

        self.lbl_status_gerar = QLabel("")
        self.lbl_status_gerar.setStyleSheet(
            "QLabel { color: #555; font-size: 11px; }"
        )
        self.lbl_status_gerar.setWordWrap(True)
        f.addRow(self.lbl_status_gerar)
        return gb

    # ------------------------- Julgamento -------------------------------
    def _criar_bloco_julgamento(self) -> QGroupBox:
        gb = QGroupBox("🔍 Julgamento dos quadrantes")
        gb.setStyleSheet(groupbox_style())
        v = QVBoxLayout(gb)

        self.lbl_qd_atual = QLabel("Quadrante: — / —")
        self.lbl_qd_atual.setStyleSheet(
            "font-weight: bold; color: #1a5276; font-size: 13px;"
        )
        v.addWidget(self.lbl_qd_atual)

        h_nav = QHBoxLayout()
        btn_prev = QPushButton("◀ Anterior")
        btn_prev.setStyleSheet(botao_style("secondary"))
        btn_prev.clicked.connect(self._anterior)
        btn_next = QPushButton("Próximo ▶")
        btn_next.setStyleSheet(botao_style("secondary"))
        btn_next.clicked.connect(self._proximo)
        h_nav.addWidget(btn_prev)
        h_nav.addWidget(btn_next)
        v.addLayout(h_nav)

        # PEC — medição efêmera
        gb_pec = QGroupBox("📏 PEC — distância vetor ↔ imagem")
        gb_pec.setStyleSheet(groupbox_style())
        v_pec = QVBoxLayout(gb_pec)
        self.btn_medir = QPushButton("📏 Ativar medição (clique e arraste)")
        self.btn_medir.setStyleSheet(botao_style("info"))
        self.btn_medir.setCheckable(True)
        self.btn_medir.toggled.connect(self._toggle_medicao)
        v_pec.addWidget(self.btn_medir)

        self.lbl_ultima_medicao = QLabel(
            "Última medição: — m  (clique-arraste no mapa)"
        )
        self.lbl_ultima_medicao.setStyleSheet(
            "QLabel { color: #444; font-size: 11px; padding: 4px; "
            "background: #f4f6f7; border-radius: 4px; }"
        )
        v_pec.addWidget(self.lbl_ultima_medicao)
        v.addWidget(gb_pec)

        # Erros temáticos (sem observações)
        gb_oc = QGroupBox("📊 Erros temáticos no quadrante")
        gb_oc.setStyleSheet(groupbox_style())
        f_oc = QFormLayout(gb_oc)
        self.spin_omissao = QSpinBox()
        self.spin_omissao.setRange(0, 9999)
        self.spin_omissao.setSuffix(" feições")
        self.spin_omissao.setToolTip(
            "Feições que existem na imagem/realidade mas estão ausentes no mapeamento."
        )
        f_oc.addRow("Erro de OMISSÃO:", self.spin_omissao)

        self.spin_comissao = QSpinBox()
        self.spin_comissao.setRange(0, 9999)
        self.spin_comissao.setSuffix(" feições")
        self.spin_comissao.setToolTip(
            "Feições que estão no mapeamento mas não existem na imagem/realidade."
        )
        f_oc.addRow("Erro de COMISSÃO:", self.spin_comissao)
        v.addWidget(gb_oc)

        # Aprovação
        h_aprov = QHBoxLayout()
        btn_aprovar = QPushButton("✓ Aprovar quadrante")
        btn_aprovar.setStyleSheet(botao_style("success"))
        btn_aprovar.clicked.connect(lambda: self._marcar(True))
        btn_reprovar = QPushButton("✗ Reprovar quadrante")
        btn_reprovar.setStyleSheet(botao_style("danger"))
        btn_reprovar.clicked.connect(lambda: self._marcar(False))
        h_aprov.addWidget(btn_aprovar)
        h_aprov.addWidget(btn_reprovar)
        v.addLayout(h_aprov)
        return gb

    # ================================================================== #
    #                          Camada avaliada                           #
    # ================================================================== #

    def _escolher_camada_avaliada(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecione a camada vetorial a avaliar", "",
            "Vetoriais (*.shp *.gpkg *.geojson *.kml)"
        )
        if not path:
            return
        layer = QgsVectorLayer(
            path, os.path.splitext(os.path.basename(path))[0], "ogr"
        )
        if not layer.isValid():
            QMessageBox.warning(self, "Erro", "Camada inválida.")
            return
        self._aplicar_estilo_amarelo(layer)
        QgsProject.instance().addMapLayer(layer)
        self.input_camada.setText(path)
        self.camada_avaliada = layer
        self.mw.adicionar_camada_no_canvas(self.canvas, layer)

    # ================================================================== #
    #                          Geração                                   #
    # ================================================================== #

    def _gerar_quadrantes(self) -> None:
        if self.camada_avaliada is None or not self.camada_avaliada.isValid():
            QMessageBox.warning(self, "Camada", "Selecione a camada a avaliar.")
            return

        self.btn_gerar_quadrantes.setEnabled(False)
        self.btn_gerar_quadrantes.setText("Gerando…")
        self.barra_gerar.setVisible(True)
        self.barra_gerar.setValue(0)
        self.lbl_status_gerar.setText("Iniciando…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()

        def _progresso(pct: int, msg: str) -> None:
            self.barra_gerar.setValue(int(pct))
            self.lbl_status_gerar.setText(msg)

        try:
            try:
                quadrantes = amostragem.gerar_quadrantes(
                    self.camada_avaliada,
                    tamanho_metros=float(self.spin_tamanho.value()),
                    n_amostras=self.spin_n_amostras.value(),
                    nome_camada_saida="Quadrantes_checagem",
                    progress_callback=_progresso,
                )
            except ValueError as exc:
                QMessageBox.warning(self, "Quadrantes", str(exc))
                return
            except Exception as exc:
                QMessageBox.critical(
                    self, "Erro", f"Falha ao gerar quadrantes:\n{exc}"
                )
                return

            if quadrantes is None or quadrantes.featureCount() == 0:
                QMessageBox.warning(
                    self, "Quadrantes",
                    "Nenhum quadrante foi gerado. Verifique se a camada está "
                    "no CRS correto e se possui feições válidas."
                )
                return

            from ..core.utils import criar_campo
            for nome, tipo in (
                ("erro_omissao", "int"),
                ("erro_comissao", "int"),
            ):
                if nome not in [f.name() for f in quadrantes.fields()]:
                    quadrantes.dataProvider().addAttributes(
                        [criar_campo(nome, tipo)]
                    )
            quadrantes.updateFields()

            self._aplicar_estilo_contorno(quadrantes)
            QgsProject.instance().addMapLayer(quadrantes)
            self.camada_quadrantes = quadrantes

            self.ids_amostrados = [f.id() for f in quadrantes.getFeatures()]
            self.indice_atual = 0
            self.mw.adicionar_camada_no_canvas(self.canvas, quadrantes)
            QMessageBox.information(
                self, "Quadrantes",
                f"Quadrantes sorteados para inspeção: {len(self.ids_amostrados)}"
            )
            self._mostrar_quadrante_atual()
        finally:
            QApplication.restoreOverrideCursor()
            self.btn_gerar_quadrantes.setEnabled(True)
            self.btn_gerar_quadrantes.setText("Gerar quadrantes")

    @staticmethod
    def _aplicar_estilo_contorno(layer: QgsVectorLayer) -> None:
        try:
            symbol = QgsFillSymbol.createSimple({
                "color": "0,0,0,0",
                "outline_color": "231,76,60",
                "outline_width": "0.6",
            })
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            layer.triggerRepaint()
        except Exception:
            pass

    @staticmethod
    def _aplicar_estilo_amarelo(layer: QgsVectorLayer) -> None:
        try:
            geom_type = layer.geometryType()
            if geom_type == QgsWkbTypes.PolygonGeometry:
                symbol = QgsFillSymbol.createSimple({
                    "color": "0,0,0,0",
                    "outline_color": "255,255,0",
                    "outline_width": "0.8",
                })
            elif geom_type == QgsWkbTypes.LineGeometry:
                symbol = QgsLineSymbol.createSimple({
                    "color": "255,255,0",
                    "line_color": "255,255,0",
                    "line_width": "0.8",
                })
            elif geom_type == QgsWkbTypes.PointGeometry:
                symbol = QgsMarkerSymbol.createSimple({
                    "color": "255,255,0",
                    "outline_color": "0,0,0",
                    "outline_width": "0.3",
                    "size": "2.6",
                })
            else:
                return
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            layer.triggerRepaint()
        except Exception:
            pass

    # ================================================================== #
    #                          Julgamento                                #
    # ================================================================== #

    def _mostrar_quadrante_atual(self) -> None:
        if not self.ids_amostrados or self.camada_quadrantes is None:
            self.lbl_qd_atual.setText("Quadrante: — / —")
            return
        self.indice_atual = max(
            0, min(self.indice_atual, len(self.ids_amostrados) - 1)
        )
        fid = self.ids_amostrados[self.indice_atual]
        feat = self.camada_quadrantes.getFeature(fid)
        nomes = [f.name() for f in self.camada_quadrantes.fields()]
        id_visivel = feat["id"] if "id" in nomes else fid
        self.lbl_qd_atual.setText(
            f"Quadrante: {self.indice_atual + 1} / "
            f"{len(self.ids_amostrados)}  (id={id_visivel})"
        )
        self.camada_quadrantes.removeSelection()
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            return
        crs_origem = self.camada_quadrantes.crs()
        crs_canvas = self.canvas.mapSettings().destinationCrs()
        if not crs_canvas.isValid():
            crs_canvas = crs_origem
            self.canvas.setDestinationCrs(crs_origem)
        geom_canvas = QgsGeometry(geom)
        if crs_origem != crs_canvas:
            transf = QgsCoordinateTransform(
                crs_origem, crs_canvas, QgsProject.instance()
            )
            try:
                geom_canvas.transform(transf)
            except Exception:
                pass
        self._destacar_quadrante(geom_canvas)
        rect = geom_canvas.boundingBox()
        rect.scale(1.25)
        self.canvas.setExtent(rect)
        self.canvas.refresh()

        # Carrega valores já registrados
        if "erro_omissao" in nomes:
            self.spin_omissao.setValue(int(feat["erro_omissao"] or 0))
        if "erro_comissao" in nomes:
            self.spin_comissao.setValue(int(feat["erro_comissao"] or 0))
        if "dist_max_m" in nomes:
            d = feat["dist_max_m"]
            if d not in (None, 0, ""):
                try:
                    self.ultima_distancia = float(d)
                    self.lbl_ultima_medicao.setText(
                        f"Última medição registrada: {self.ultima_distancia:.2f} m"
                    )
                except (TypeError, ValueError):
                    self.ultima_distancia = 0.0
            else:
                self.ultima_distancia = 0.0
                self.lbl_ultima_medicao.setText(
                    "Última medição: — m  (clique-arraste no mapa)"
                )

    def _destacar_quadrante(self, geom: QgsGeometry) -> None:
        try:
            if self.rubber_quadrante_atual is None:
                self.rubber_quadrante_atual = QgsRubberBand(
                    self.canvas, QgsWkbTypes.PolygonGeometry
                )
                self.rubber_quadrante_atual.setColor(QColor(0, 200, 255))
                self.rubber_quadrante_atual.setFillColor(QColor(0, 0, 0, 0))
                self.rubber_quadrante_atual.setStrokeColor(QColor(0, 200, 255))
                self.rubber_quadrante_atual.setWidth(3)
            else:
                self.rubber_quadrante_atual.reset(QgsWkbTypes.PolygonGeometry)
            self.rubber_quadrante_atual.setToGeometry(geom, None)
        except Exception:
            pass

    def _anterior(self) -> None:
        if not self.ids_amostrados:
            return
        if self.indice_atual > 0:
            self.indice_atual -= 1
            self._mostrar_quadrante_atual()

    def _proximo(self) -> None:
        if not self.ids_amostrados:
            return
        if self.indice_atual < len(self.ids_amostrados) - 1:
            self.indice_atual += 1
            self._mostrar_quadrante_atual()

    # ---------------------------- medição -------------------------------
    def _toggle_medicao(self, ativo: bool) -> None:
        if ativo:
            self.tool_medir = FerramentaMedirArrastando(
                self.canvas, self._on_distancia_solta
            )
            self.canvas.setMapTool(self.tool_medir)
            self.btn_medir.setText("⏹ Parar medição")
        else:
            pan = getattr(self.canvas, "_pan_tool", None)
            if pan is not None:
                self.canvas.setMapTool(pan)
            else:
                from qgis.gui import QgsMapToolPan
                self.canvas.setMapTool(QgsMapToolPan(self.canvas))
            if self.tool_medir is not None:
                try:
                    self.tool_medir.rubber.reset(QgsWkbTypes.LineGeometry)
                    self.tool_medir.label_flutuante.hide()
                except Exception:
                    pass
                self.tool_medir = None
            self.btn_medir.setText("📏 Ativar medição (clique e arraste)")

    def _on_distancia_solta(self, distancia: float) -> None:
        """Chamado ao soltar o botão: guarda apenas como 'última medição'."""
        self.ultima_distancia = float(distancia)
        self.lbl_ultima_medicao.setText(
            f"Última medição: {distancia:.2f} m"
        )

    # ---------------------- marcação aprovação --------------------------
    def _marcar(self, aprovado: bool) -> None:
        if not self.ids_amostrados or self.camada_quadrantes is None:
            return
        fid = self.ids_amostrados[self.indice_atual]
        idx_apr = self.camada_quadrantes.fields().indexFromName("aprovado")
        idx_dist = self.camada_quadrantes.fields().indexFromName("dist_max_m")
        idx_om = self.camada_quadrantes.fields().indexFromName("erro_omissao")
        idx_com = self.camada_quadrantes.fields().indexFromName("erro_comissao")

        dist_max = float(self.ultima_distancia or 0.0)
        if dist_max == 0.0 and idx_dist >= 0:
            antigo = self.camada_quadrantes.getFeature(fid)["dist_max_m"]
            try:
                dist_max = float(antigo) if antigo is not None else 0.0
            except (TypeError, ValueError):
                dist_max = 0.0

        self.camada_quadrantes.startEditing()
        if idx_apr >= 0:
            self.camada_quadrantes.changeAttributeValue(
                fid, idx_apr, 1 if aprovado else 0
            )
        if idx_dist >= 0:
            self.camada_quadrantes.changeAttributeValue(
                fid, idx_dist, float(dist_max)
            )
        if idx_om >= 0:
            self.camada_quadrantes.changeAttributeValue(
                fid, idx_om, int(self.spin_omissao.value())
            )
        if idx_com >= 0:
            self.camada_quadrantes.changeAttributeValue(
                fid, idx_com, int(self.spin_comissao.value())
            )
        self.camada_quadrantes.commitChanges()
        self._proximo()

    # ================================================================== #
    #                          Resultados                                #
    # ================================================================== #

    def _abrir_resultados(self) -> None:
        if self.camada_quadrantes is None:
            QMessageBox.warning(
                self, "Resultados",
                "Gere os quadrantes e faça pelo menos alguns julgamentos antes."
            )
            return
        dlg = DialogResultadosQuadrantes(self, self.camada_quadrantes)
        dlg.exec_()
