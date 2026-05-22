"""Widget unificado de Validação de Uso do Solo.

Consolida em uma única sub-aba o fluxo completo:
    * Carregamento da camada de uso/cobertura e escolha do mapa de fundo
      (Google Satélite por padrão, Esri, Planet e GEE — SPOT/Landsat 2008).
    * Parâmetros de amostragem simplificados (estratégia + 1 campo
      contextual). Parâmetros avançados via engrenagem.
    * Cálculo + geração dos pontos amostrais em um único clique.
    * Resumo do cálculo (tabela por classe).
    * Rotulagem assistida ponto-a-ponto, sem viés.

Mapa único compartilhado entre todas as etapas.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import amostragem
from ..core.utils import (
    carregar_camada_vetorial,
    garantir_campo,
    listar_campos,
)
from .estilos import botao_style, groupbox_style


XYZ_PERMITIDOS = ("Google Satélite", "Esri Satélite")
PLANET_ITEM_TEXT = "Planet (login necessário)"


class WidgetUsoSolo(QWidget):
    """Sub-aba unificada de validação de uso do solo (amostragem + rotulagem)."""

    # Caches/credenciais persistentes durante a sessão
    _planet_last_email: str = ""
    _planet_last_senha: str = ""
    _gee_spot2008_cache: str = ""
    _gee_landsat2008_cache: str = ""

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window

        # Estado da camada de uso/cobertura
        self.camada_uso_solo: Optional[QgsVectorLayer] = None
        self.resumo_amostragem: Optional[amostragem.ResumoAmostragem] = None

        # Estado da camada de pontos
        self.camada_pontos: Optional[QgsVectorLayer] = None
        self.indice_atual: int = 0
        self.ids_pontos: List[int] = []
        self._rotulagem_iniciada: bool = False

        # Mapa de fundo
        self.camada_xyz: Optional[QgsRasterLayer] = None

        # Parâmetros avançados (acessíveis via engrenagem)
        self._param_confianca: float = 0.95
        self._param_erro: float = 0.05
        self._param_min_classe: int = 5

        self._build_ui()

    # ================================================================== #
    #                              UI                                    #
    # ================================================================== #

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)

        # Cria o canvas ANTES dos blocos da esquerda, pois alguns deles
        # (camadas/mapa de fundo) precisam acessar self.canvas na construção.
        self.canvas = self.mw.criar_canvas_mapa()

        # ----- Painel esquerdo (rolável) -----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setMaximumWidth(440)

        painel = QWidget()
        painel.setMaximumWidth(440)
        pv = QVBoxLayout(painel)
        pv.setContentsMargins(8, 8, 8, 8)
        pv.setSpacing(6)

        pv.addWidget(self._criar_bloco_camadas())
        pv.addWidget(self._criar_bloco_tipo_amostragem())
        pv.addWidget(self._criar_botao_calcular())
        pv.addWidget(self._criar_bloco_resumo())
        pv.addWidget(self._criar_bloco_rotulagem(), 1)
        pv.addWidget(self._criar_botao_matriz())

        scroll.setWidget(painel)
        splitter.addWidget(scroll)

        # Mapa único à direita
        splitter.addWidget(self.canvas)
        splitter.setSizes([440, 900])

        layout.addWidget(splitter)

    def _criar_bloco_camadas(self) -> QGroupBox:
        gb = QGroupBox("📂 Camadas")
        gb.setStyleSheet(groupbox_style())
        v = QVBoxLayout(gb)
        v.setSpacing(6)

        v.addWidget(QLabel("Uso e cobertura:"))
        h = QHBoxLayout()
        self.input_camada_uso = QLineEdit()
        self.input_camada_uso.setPlaceholderText("Base de uso/cobertura (.shp/.gpkg)")
        btn = QPushButton("Procurar")
        btn.setMinimumWidth(80)
        btn.setStyleSheet(botao_style("info"))
        btn.clicked.connect(self._escolher_camada_uso_solo)
        h.addWidget(self.input_camada_uso, 1)
        h.addWidget(btn)
        v.addLayout(h)

        v.addWidget(QLabel("Campo da classe:"))
        self.combo_campo_classe = QComboBox()
        self.combo_campo_classe.setEnabled(False)
        v.addWidget(self.combo_campo_classe)

        v.addWidget(QLabel("Mapa de fundo:"))
        self.combo_xyz = QComboBox()
        idx_google = 0
        for nome, dados in self.mw.config.get("wms_services", {}).items():
            if nome in XYZ_PERMITIDOS:
                self.combo_xyz.addItem(nome, dados.get("url", ""))
                if nome == "Google Satélite":
                    idx_google = self.combo_xyz.count() - 1
        self.combo_xyz.addItem(PLANET_ITEM_TEXT, "planet")
        self.combo_xyz.addItem("SPOT 2008 (Earth Engine)", "gee_spot2008")
        self.combo_xyz.addItem("Landsat 2008 (Earth Engine)", "gee_landsat2008")
        self.combo_xyz.currentIndexChanged.connect(self._on_xyz_change)
        v.addWidget(self.combo_xyz)
        # Aplica Google Satélite como padrão (força carregamento mesmo se idx==0)
        if self.combo_xyz.currentIndex() == idx_google:
            self._on_xyz_change()
        else:
            self.combo_xyz.setCurrentIndex(idx_google)

        return gb

    def _criar_bloco_tipo_amostragem(self) -> QGroupBox:
        gb = QGroupBox("⚙ Tipo Amostragem")
        gb.setStyleSheet(groupbox_style())
        v = QVBoxLayout(gb)
        v.setSpacing(6)

        # Linha "Estratégia:" + botão engrenagem
        h_est = QHBoxLayout()
        h_est.addWidget(QLabel("Estratégia:"))
        self.combo_estrategia = QComboBox()
        self.combo_estrategia.addItem("Estratificada Proporcional", "estratificada_proporcional")
        self.combo_estrategia.addItem("Estratificada Igualitária", "estratificada_igual")
        self.combo_estrategia.addItem("Aleatória Simples", "aleatoria_simples")
        self.combo_estrategia.addItem("Sistemática (grid)", "sistematica")
        h_est.addWidget(self.combo_estrategia, 1)

        btn_config = QPushButton("⚙")
        btn_config.setFixedSize(28, 28)
        btn_config.setToolTip("Configurações avançadas")
        btn_config.clicked.connect(self._abrir_config_avancada)
        h_est.addWidget(btn_config)
        v.addLayout(h_est)

        # Campo dinâmico — muda conforme estratégia
        self.lbl_campo_dinamico = QLabel("Campo de área:")
        v.addWidget(self.lbl_campo_dinamico)

        # Empilhamos os 3 widgets contextuais; apenas 1 fica visível
        self.combo_campo_area = QComboBox()
        self.combo_campo_area.setEnabled(False)
        self.combo_campo_area.addItem("Calcular Área (ha)", "")
        fnt = self.combo_campo_area.font()
        fnt.setBold(True)
        self.combo_campo_area.setItemData(0, fnt, Qt.FontRole)
        v.addWidget(self.combo_campo_area)

        self.spin_n_total_simples = QSpinBox()
        self.spin_n_total_simples.setRange(10, 100000)
        self.spin_n_total_simples.setValue(265)
        v.addWidget(self.spin_n_total_simples)

        self.spin_espacamento = QSpinBox()
        self.spin_espacamento.setRange(50, 100000)
        self.spin_espacamento.setValue(2000)
        self.spin_espacamento.setSuffix(" m")
        v.addWidget(self.spin_espacamento)

        self.combo_estrategia.currentIndexChanged.connect(self._on_estrategia_change)
        self._on_estrategia_change()

        return gb

    def _criar_botao_calcular(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        self.btn_calcular = QPushButton("Calcular pontos")
        self.btn_calcular.setStyleSheet(botao_style("success"))
        self.btn_calcular.clicked.connect(self._calcular_amostra)
        h.addWidget(self.btn_calcular)
        return wrap

    def _criar_bloco_resumo(self) -> QGroupBox:
        gb = QGroupBox("📊 Resumo do cálculo")
        gb.setStyleSheet(groupbox_style())
        v = QVBoxLayout(gb)
        self.txt_resumo = QTextEdit()
        self.txt_resumo.setReadOnly(True)
        self.txt_resumo.setMinimumHeight(80)
        self.txt_resumo.setMaximumHeight(180)
        v.addWidget(self.txt_resumo)
        return gb

    def _criar_bloco_rotulagem(self) -> QGroupBox:
        gb = QGroupBox("📍 Rotulagem")
        gb.setStyleSheet(groupbox_style())
        v = QVBoxLayout(gb)

        self.lbl_id_atual = QLabel("ID: —")
        self.lbl_id_atual.setStyleSheet(
            "font-weight: bold; color: #1a5276; font-size: 13px;"
        )
        v.addWidget(self.lbl_id_atual)

        f = QFormLayout()
        self.spin_zoom = QSpinBox()
        self.spin_zoom.setRange(50, 5000)
        self.spin_zoom.setValue(1000)
        self.spin_zoom.setSuffix(" m")
        self.spin_zoom.valueChanged.connect(lambda _: self._zoom_no_ponto())
        f.addRow("Buffer de zoom:", self.spin_zoom)
        v.addLayout(f)

        v.addWidget(QLabel("<b>Verdade observada:</b>"))
        self.combo_classes = QComboBox()
        self.combo_classes.setStyleSheet(
            "QComboBox { font-size: 13px; padding: 6px; }"
        )
        v.addWidget(self.combo_classes)

        h_nav = QHBoxLayout()
        self.btn_prev = QPushButton("◀ Anterior")
        self.btn_prev.setStyleSheet(botao_style("secondary"))
        self.btn_prev.clicked.connect(self._anterior)
        self.btn_salvar = QPushButton("✓ Salvar e próximo")
        self.btn_salvar.setStyleSheet(botao_style("success"))
        self.btn_salvar.clicked.connect(self._salvar_e_proximo)
        self.btn_next = QPushButton("Próximo ▶")
        self.btn_next.setStyleSheet(botao_style("secondary"))
        self.btn_next.clicked.connect(self._proximo)
        h_nav.addWidget(self.btn_prev)
        h_nav.addWidget(self.btn_salvar)
        h_nav.addWidget(self.btn_next)
        v.addLayout(h_nav)

        self.lbl_progresso = QLabel("Progresso: 0 / 0")
        self.lbl_progresso.setStyleSheet("color: #2c3e50;")
        v.addWidget(self.lbl_progresso)
        return gb

    def _criar_botao_matriz(self) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        self.btn_matriz = QPushButton("📊 Matriz de Confusão")
        self.btn_matriz.setStyleSheet(botao_style("info"))
        self.btn_matriz.clicked.connect(self._abrir_dialog_matriz)
        h.addWidget(self.btn_matriz)
        return wrap

    def _abrir_dialog_matriz(self) -> None:
        if self.camada_pontos is None:
            QMessageBox.warning(
                self, "Sem pontos",
                "Gere os pontos amostrais e rotule alguns antes de "
                "abrir a matriz de confusão."
            )
            return
        from .dialog_matriz_confusao import DialogMatrizConfusao
        dlg = DialogMatrizConfusao(self, self.camada_pontos)
        dlg.exec_()

    # ================================================================== #
    #                  Estratégia / Visibilidade campos                  #
    # ================================================================== #

    def _on_estrategia_change(self) -> None:
        est = self.combo_estrategia.currentData()
        self.combo_campo_area.setVisible(est == "estratificada_proporcional")
        self.spin_n_total_simples.setVisible(est == "aleatoria_simples")
        self.spin_espacamento.setVisible(est == "sistematica")

        if est == "estratificada_proporcional":
            self.lbl_campo_dinamico.setText("Campo de área:")
            self.lbl_campo_dinamico.setVisible(True)
        elif est == "aleatoria_simples":
            self.lbl_campo_dinamico.setText("Nº pontos:")
            self.lbl_campo_dinamico.setVisible(True)
        elif est == "sistematica":
            self.lbl_campo_dinamico.setText("Espaçamento:")
            self.lbl_campo_dinamico.setVisible(True)
        else:
            self.lbl_campo_dinamico.setVisible(False)

    def _abrir_config_avancada(self) -> None:
        """Diálogo para parâmetros estatísticos avançados."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Configurações avançadas")
        dlg.setMinimumWidth(320)
        layout = QVBoxLayout(dlg)

        gb = QGroupBox("Parâmetros estatísticos")
        f = QFormLayout(gb)

        spin_conf = QDoubleSpinBox()
        spin_conf.setRange(0.80, 0.99)
        spin_conf.setSingleStep(0.01)
        spin_conf.setDecimals(2)
        spin_conf.setValue(self._param_confianca)
        f.addRow("Confiança:", spin_conf)

        spin_erro = QDoubleSpinBox()
        spin_erro.setRange(0.001, 0.20)
        spin_erro.setSingleStep(0.005)
        spin_erro.setDecimals(3)
        spin_erro.setValue(self._param_erro)
        f.addRow("Erro (b):", spin_erro)

        spin_min = QSpinBox()
        spin_min.setRange(1, 200)
        spin_min.setValue(self._param_min_classe)
        f.addRow("Mín. por classe:", spin_min)

        layout.addWidget(gb)

        info = QLabel(
            "<small><i>Esses parâmetros se aplicam às estratégias "
            "Estratificada Proporcional e Igualitária.</i></small>"
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#666;")
        layout.addWidget(info)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec_() == QDialog.Accepted:
            self._param_confianca = spin_conf.value()
            self._param_erro = spin_erro.value()
            self._param_min_classe = spin_min.value()

    # ================================================================== #
    #                  Carga da camada de uso/cobertura                  #
    # ================================================================== #

    def _escolher_camada_uso_solo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecione a base de uso/cobertura", "",
            "Vetoriais (*.shp *.gpkg *.geojson *.kml);;Todos (*.*)"
        )
        if not path:
            return
        self.input_camada_uso.setText(path)
        layer = carregar_camada_vetorial(
            path, os.path.splitext(os.path.basename(path))[0]
        )
        if layer is None:
            QMessageBox.warning(self, "Camada inválida", "Não foi possível carregar a camada.")
            return
        QgsProject.instance().addMapLayer(layer)
        self.camada_uso_solo = layer
        self.mw.adicionar_camada_no_canvas(self.canvas, layer)

        campos = listar_campos(layer)
        self.combo_campo_classe.clear()
        self.combo_campo_classe.addItems(campos)
        self.combo_campo_classe.setEnabled(True)

        # Detecta campos numéricos plausíveis para a área
        from qgis.PyQt.QtCore import QVariant
        tipos_numericos = (QVariant.Int, QVariant.Double, QVariant.LongLong)
        self.combo_campo_area.clear()
        self.combo_campo_area.addItem("Calcular Área (ha)", "")
        fnt = self.combo_campo_area.font()
        fnt.setBold(True)
        self.combo_campo_area.setItemData(0, fnt, Qt.FontRole)
        for f in layer.fields():
            if f.type() in tipos_numericos:
                self.combo_campo_area.addItem(f.name(), f.name())
        self.combo_campo_area.setEnabled(True)
        for nome in ("area_calc", "area_ha", "area_m2", "AREA", "area",
                     "Shape_Area", "SHAPE_Area", "AREA_HA"):
            idx = self.combo_campo_area.findData(nome)
            if idx >= 0:
                self.combo_campo_area.setCurrentIndex(idx)
                break

    def _garantir_camada_uso(self) -> bool:
        try:
            invalida = self.camada_uso_solo is None or not self.camada_uso_solo.isValid()
        except RuntimeError:
            invalida = True
        if invalida:
            self.camada_uso_solo = None
            QMessageBox.warning(
                self, "Camada não carregada",
                "Carregue uma camada de uso/cobertura primeiro."
            )
            return False
        if not self.combo_campo_classe.currentText():
            QMessageBox.warning(
                self, "Campo da classe",
                "Selecione o campo que identifica a classe de cobertura."
            )
            return False
        return True

    # ================================================================== #
    #                      Cálculo da amostra                            #
    # ================================================================== #

    def _calcular_amostra(self) -> None:
        if not self._garantir_camada_uso():
            return
        campo = self.combo_campo_classe.currentText()
        campo_area = self.combo_campo_area.currentData() or None
        est = self.combo_estrategia.currentData()

        if est in ("estratificada_proporcional", "estratificada_igual"):
            resumo = amostragem.calcular_tamanho_amostra(
                self.camada_uso_solo,
                campo,
                self._param_confianca,
                self._param_erro,
                self._param_min_classe,
                campo_area=campo_area,
            )
            if (self.combo_campo_area.findData("area_ha") < 0
                    and "area_ha" in [f.name() for f in self.camada_uso_solo.fields()]):
                self.combo_campo_area.addItem("area_ha", "area_ha")
            if est == "estratificada_igual" and resumo.classes:
                n_pc = max(self._param_min_classe,
                           resumo.n_total // max(1, len(resumo.classes)))
                resumo.n_por_classe = {c.valor: n_pc for c in resumo.classes}
                resumo.n_total = sum(resumo.n_por_classe.values())
            self.resumo_amostragem = resumo
            self._mostrar_resumo(resumo)
        elif est == "aleatoria_simples":
            n = self.spin_n_total_simples.value()
            self.resumo_amostragem = amostragem.ResumoAmostragem(
                n_total=n,
                formula_aplicada=f"Aleatória simples: N = {n}",
            )
            self.txt_resumo.setHtml(
                f"<p><b>Estratégia:</b> Aleatória Simples</p>"
                f"<p><b>Tamanho total da amostra:</b> {n} pontos</p>"
            )
        else:
            esp = self.spin_espacamento.value()
            self.resumo_amostragem = amostragem.ResumoAmostragem(
                n_total=0,
                formula_aplicada=f"Sistemática: grid {esp}m × {esp}m",
            )
            self.txt_resumo.setHtml(
                f"<p><b>Estratégia:</b> Sistemática</p>"
                f"<p><b>Grid:</b> {esp} m × {esp} m</p>"
                f"<p>O número total de pontos depende da extensão da camada.</p>"
            )

        self._gerar_pontos()

    def _mostrar_resumo(self, resumo: amostragem.ResumoAmostragem) -> None:
        if not resumo.classes:
            self.txt_resumo.setText("Nenhuma classe encontrada.")
            return

        def _fmt_br(v: float) -> str:
            txt = f"{v:,.2f}"
            return txt.replace(",", "X").replace(".", ",").replace("X", ".")

        html = (
            f"<p style='margin:0 0 4px 0;'><b>Total de pontos: {resumo.n_total}</b></p>"
            "<table border='1' cellpadding='4' cellspacing='0' "
            "style='border-collapse:collapse; width:100%;'>"
            "<tr style='background:#1a5276;color:white;'>"
            "<th>Classe</th><th>Área (ha)</th><th>Proporção</th>"
            "<th>Pontos</th></tr>"
        )
        for c in resumo.classes:
            n = resumo.n_por_classe.get(c.valor, 0)
            area_ha = c.area / 10_000.0
            html += (
                f"<tr><td>{c.nome}</td>"
                f"<td style='text-align:right'>{_fmt_br(area_ha)}</td>"
                f"<td style='text-align:center'>{c.proporcao * 100:.2f}%</td>"
                f"<td style='text-align:center'><b>{n}</b></td></tr>"
            )
        html += "</table>"
        html += (
            "<p style='margin:6px 0 0 0; font-size:9px; color:#666;'>"
            f"Congalton &amp; Green (1999) — {resumo.formula_aplicada}</p>"
        )
        self.txt_resumo.setHtml(html)

    def _gerar_pontos(self) -> None:
        if not self._garantir_camada_uso():
            return
        if self.resumo_amostragem is None:
            return
        campo = self.combo_campo_classe.currentText()
        est = self.combo_estrategia.currentData()

        # Remove camada de pontos anterior
        if self.camada_pontos is not None:
            try:
                layer_id = self.camada_pontos.id()
                camadas = list(self.canvas.layers())
                camadas = [c for c in camadas if c.id() != layer_id]
                self.canvas.setLayers(camadas)
                QgsProject.instance().removeMapLayer(layer_id)
            except (RuntimeError, Exception):
                pass
            self.camada_pontos = None

        try:
            if est in ("estratificada_proporcional", "estratificada_igual"):
                pontos = amostragem.gerar_pontos_estratificados(
                    self.camada_uso_solo,
                    campo,
                    self.resumo_amostragem.n_por_classe,
                )
            elif est == "aleatoria_simples":
                pontos = amostragem.gerar_pontos_aleatorios_simples(
                    self.camada_uso_solo,
                    self.resumo_amostragem.n_total,
                    campo_classe=campo,
                )
            else:
                pontos = amostragem.gerar_pontos_sistematicos(
                    self.camada_uso_solo,
                    float(self.spin_espacamento.value()),
                    campo_classe=campo,
                )
        except Exception as exc:
            QMessageBox.critical(self, "Erro", f"Falha ao gerar pontos:\n{exc}")
            return

        if pontos.featureCount() == 0:
            QMessageBox.warning(self, "Sem pontos", "Nenhum ponto foi gerado.")
            return

        QgsProject.instance().addMapLayer(pontos)
        self.camada_pontos = pontos
        self._aplicar_estilo_pontos_vermelho(pontos)
        self.mw.adicionar_camada_no_canvas(self.canvas, pontos)

        n_gerados = pontos.featureCount()
        if est == "sistematica":
            esp = self.spin_espacamento.value()
            self.txt_resumo.setHtml(
                f"<p><b>Estratégia:</b> Sistemática (grid {esp} m × {esp} m)</p>"
                f"<p><b>Total de pontos gerados:</b> {n_gerados}</p>"
            )
        elif est == "aleatoria_simples":
            self.txt_resumo.setHtml(
                f"<p><b>Estratégia:</b> Aleatória Simples</p>"
                f"<p><b>Total de pontos gerados:</b> {n_gerados}</p>"
            )

        # Reinicia o fluxo de rotulagem para a nova camada de pontos
        self._rotulagem_iniciada = False
        self.ids_pontos = [f.id() for f in pontos.getFeatures()]
        self.indice_atual = 0
        from qgis.PyQt.QtCore import QVariant
        garantir_campo(pontos, "verdade", QVariant.String)
        garantir_campo(pontos, "rotulado", QVariant.Int)
        self._popular_classes_da_camada()
        self._atualizar_display_ponto_sem_zoom()
        self._zoom_em_todos_pontos()

    @staticmethod
    def _aplicar_estilo_pontos_vermelho(camada: QgsVectorLayer) -> None:
        from qgis.core import QgsMarkerSymbol, QgsSingleSymbolRenderer
        symbol = QgsMarkerSymbol.createSimple({
            "name": "circle",
            "color": "red",
            "size": "2.5",
            "outline_color": "darkRed",
            "outline_width": "0.4",
        })
        camada.setRenderer(QgsSingleSymbolRenderer(symbol))
        camada.triggerRepaint()

    # ================================================================== #
    #                          Rotulagem                                 #
    # ================================================================== #

    def set_camada_pontos(self, layer: QgsVectorLayer) -> None:
        """Carrega/atualiza a camada de pontos e abre no primeiro."""
        self.camada_pontos = layer
        self.ids_pontos = [f.id() for f in layer.getFeatures()]
        self.indice_atual = 0
        self._rotulagem_iniciada = False

        from qgis.PyQt.QtCore import QVariant
        garantir_campo(layer, "verdade", QVariant.String)
        garantir_campo(layer, "rotulado", QVariant.Int)

        self._popular_classes_da_camada()
        self._mostrar_ponto_atual()

    def _popular_classes_da_camada(self) -> None:
        if self.camada_pontos is None:
            return
        valores = set()
        nomes = [f.name() for f in self.camada_pontos.fields()]
        if "classificacao" in nomes:
            for feat in self.camada_pontos.getFeatures():
                v = feat["classificacao"]
                if v is not None and str(v).strip() != "":
                    valores.add(str(v))
        self.combo_classes.clear()
        if valores:
            self.combo_classes.addItems(sorted(valores))
            self.combo_classes.setEnabled(True)
        else:
            self.combo_classes.addItem("— sem classes detectadas —")
            self.combo_classes.setEnabled(False)

    def _mostrar_ponto_atual(self) -> None:
        """Atualiza labels + zoom no ponto atual."""
        if not self._atualizar_display_ponto_sem_zoom():
            return
        self._zoom_no_ponto()

    def _atualizar_display_ponto_sem_zoom(self) -> bool:
        """Atualiza apenas labels/seleção, sem mexer no extent do mapa."""
        if not self.ids_pontos or self.camada_pontos is None:
            self.lbl_id_atual.setText("ID: —")
            self.lbl_progresso.setText("Progresso: 0 / 0")
            return False
        self.indice_atual = max(
            0, min(self.indice_atual, len(self.ids_pontos) - 1)
        )
        fid = self.ids_pontos[self.indice_atual]
        feat = self.camada_pontos.getFeature(fid)
        nomes = [f.name() for f in self.camada_pontos.fields()]
        id_visivel = feat["id"] if "id" in nomes else fid
        self.lbl_id_atual.setText(f"ID do ponto: {id_visivel}")

        if "verdade" in nomes:
            v = feat["verdade"]
            if v is not None and str(v).strip() != "":
                idx = self.combo_classes.findText(str(v))
                if idx >= 0:
                    self.combo_classes.setCurrentIndex(idx)

        self.lbl_progresso.setText(
            f"Progresso: {self.indice_atual + 1} / {len(self.ids_pontos)}"
        )
        self.camada_pontos.removeSelection()
        self.camada_pontos.selectByIds([fid])
        return True

    def _zoom_em_todos_pontos(self) -> None:
        """Zoom para enquadrar todos os pontos gerados (com a camada de uso visível)."""
        if self.camada_pontos is None:
            return
        try:
            extent_pts = self.camada_pontos.extent()
        except Exception:
            return
        if extent_pts.isEmpty():
            return
        crs_pts = self.camada_pontos.crs()
        crs_canvas = self.canvas.mapSettings().destinationCrs()
        if not crs_canvas.isValid():
            crs_canvas = crs_pts
            self.canvas.setDestinationCrs(crs_pts)
        if crs_pts != crs_canvas:
            transf = QgsCoordinateTransform(
                crs_pts, crs_canvas, QgsProject.instance()
            )
            extent_pts = transf.transformBoundingBox(extent_pts)
        # Pequena margem (5%) para não cortar pontos da borda
        extent_pts.scale(1.05)
        self.canvas.setExtent(extent_pts)
        self.canvas.refresh()

    def _iniciar_rotulagem_se_preciso(self) -> None:
        """Na primeira ação de navegação, oculta a camada de uso/cobertura."""
        if self._rotulagem_iniciada:
            return
        self._rotulagem_iniciada = True
        if self.camada_uso_solo is not None:
            try:
                camadas = list(self.canvas.layers())
                if self.camada_uso_solo in camadas:
                    camadas.remove(self.camada_uso_solo)
                    self.canvas.setLayers(camadas)
            except (RuntimeError, Exception):
                pass

    def _zoom_no_ponto(self) -> None:
        if self.camada_pontos is None or not self.ids_pontos:
            return
        fid = self.ids_pontos[self.indice_atual]
        feat = self.camada_pontos.getFeature(fid)
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            return
        ponto = geom.asPoint()
        crs_pts = self.camada_pontos.crs()
        crs_canvas = self.canvas.mapSettings().destinationCrs()
        if not crs_canvas.isValid():
            crs_canvas = crs_pts
            self.canvas.setDestinationCrs(crs_pts)

        buffer_m = self.spin_zoom.value()
        crs_metrico = QgsCoordinateReferenceSystem("EPSG:5880")
        transf = QgsCoordinateTransform(
            crs_pts, crs_metrico, QgsProject.instance()
        )
        p_metrico = transf.transform(ponto)
        rect_metrico = QgsRectangle(
            p_metrico.x() - buffer_m,
            p_metrico.y() - buffer_m,
            p_metrico.x() + buffer_m,
            p_metrico.y() + buffer_m,
        )
        transf2 = QgsCoordinateTransform(
            crs_metrico, crs_canvas, QgsProject.instance()
        )
        rect_canvas = transf2.transformBoundingBox(rect_metrico)
        self.canvas.setExtent(rect_canvas)
        self.canvas.refresh()

    def _anterior(self) -> None:
        if self.indice_atual > 0:
            self._iniciar_rotulagem_se_preciso()
            self.indice_atual -= 1
            self._mostrar_ponto_atual()

    def _proximo(self) -> None:
        if self.indice_atual < len(self.ids_pontos) - 1:
            self._iniciar_rotulagem_se_preciso()
            self.indice_atual += 1
            self._mostrar_ponto_atual()

    def _salvar_e_proximo(self) -> None:
        if self.camada_pontos is None or not self.ids_pontos:
            return
        if not self.combo_classes.isEnabled():
            QMessageBox.warning(
                self, "Sem classes",
                "A camada de pontos não tem classes detectadas no campo "
                "'classificacao'. Gere os pontos via 'Calcular pontos'."
            )
            return
        verdade = self.combo_classes.currentText().strip()
        if not verdade:
            QMessageBox.warning(self, "Verdade", "Informe a classe verdadeira.")
            return
        self._iniciar_rotulagem_se_preciso()
        fid = self.ids_pontos[self.indice_atual]
        idx_v = self.camada_pontos.fields().indexFromName("verdade")
        idx_r = self.camada_pontos.fields().indexFromName("rotulado")
        self.camada_pontos.startEditing()
        if idx_v >= 0:
            self.camada_pontos.changeAttributeValue(fid, idx_v, verdade)
        if idx_r >= 0:
            self.camada_pontos.changeAttributeValue(fid, idx_r, 1)
        self.camada_pontos.commitChanges()
        # Próximo sem chamar _proximo (que checa flag de novo)
        if self.indice_atual < len(self.ids_pontos) - 1:
            self.indice_atual += 1
        self._mostrar_ponto_atual()

    # ================================================================== #
    #                       Mapa de fundo (XYZ/Planet/GEE)               #
    # ================================================================== #

    def _on_xyz_change(self) -> None:
        data = self.combo_xyz.currentData()
        if not data:
            return

        if data == "planet":
            self._abrir_planet_dialog()
            return

        if data == "gee_spot2008":
            self._carregar_gee_spot2008()
            return

        if data == "gee_landsat2008":
            self._carregar_gee_landsat2008()
            return

        nome = self.combo_xyz.currentText()
        layer = QgsRasterLayer(data, nome, "wms")
        if not layer.isValid():
            QMessageBox.warning(self, "XYZ", f"Não foi possível carregar {nome}.")
            return
        self._definir_camada_fundo(layer)

    def _definir_camada_fundo(self, layer: QgsRasterLayer) -> None:
        """Substitui a camada de fundo (OSM ou anterior) pela nova."""
        QgsProject.instance().addMapLayer(layer, False)
        camadas = list(self.canvas.layers())
        if self.camada_xyz is not None and self.camada_xyz in camadas:
            camadas.remove(self.camada_xyz)
        osm = getattr(self.canvas, "_osm_layer", None)
        if osm is not None and osm in camadas:
            camadas.remove(osm)
        camadas.append(layer)
        self.canvas.setLayers(camadas)
        self.canvas.refresh()
        self.camada_xyz = layer

    def _voltar_combo_anterior(self) -> None:
        """Volta o combo para a primeira opção válida (Google Satélite)."""
        self.combo_xyz.blockSignals(True)
        for i in range(self.combo_xyz.count()):
            d = self.combo_xyz.itemData(i)
            if d not in ("", "planet", "gee_spot2008", "gee_landsat2008"):
                self.combo_xyz.setCurrentIndex(i)
                break
        self.combo_xyz.blockSignals(False)

    # --- Earth Engine ----------------------------------------------------

    def _carregar_gee_spot2008(self) -> None:
        if WidgetUsoSolo._gee_spot2008_cache:
            self._aplicar_gee_tiles(
                WidgetUsoSolo._gee_spot2008_cache,
                "SPOT 2008 — Brazil Forest Code"
            )
            return
        ok = self._init_earth_engine()
        if not ok:
            return
        try:
            import ee
            from qgis.PyQt.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            img = ee.Image("GOOGLE/BRAZIL_FOREST_2008/V1/VISUAL")
            map_id = img.getMapId({"bands": ["R", "G", "B"], "min": 0, "max": 255})
            tile_url = map_id["tile_fetcher"].url_format
            WidgetUsoSolo._gee_spot2008_cache = tile_url
            self._aplicar_gee_tiles(tile_url, "SPOT 2008 — Brazil Forest Code")
        except Exception as exc:
            QMessageBox.critical(
                self, "Earth Engine",
                f"Erro ao obter tiles da imagem SPOT 2008:\n{exc}"
            )
            self._voltar_combo_anterior()

    def _carregar_gee_landsat2008(self) -> None:
        if WidgetUsoSolo._gee_landsat2008_cache:
            self._aplicar_gee_tiles(
                WidgetUsoSolo._gee_landsat2008_cache,
                "Landsat 5 — 2008 (mediana)"
            )
            return
        ok = self._init_earth_engine()
        if not ok:
            return
        try:
            import ee
            from qgis.PyQt.QtCore import QCoreApplication
            QCoreApplication.processEvents()

            def filtro_nuvens(image):
                qa_mask = image.select("QA_PIXEL").bitwiseAnd(0b11111).eq(0)
                sat_mask = image.select("QA_RADSAT").eq(0)
                optical = image.select("SR_B.").multiply(0.0000275).add(-0.2)
                thermal = image.select("ST_B6").multiply(0.00341802).add(149.0)
                return (image
                        .addBands(optical, None, True)
                        .addBands(thermal, None, True)
                        .updateMask(qa_mask)
                        .updateMask(sat_mask))

            col = (ee.ImageCollection("LANDSAT/LT05/C02/T1_L2")
                   .filterDate("2008-01-01", "2008-07-22")
                   .map(filtro_nuvens)
                   .select("SR_B3", "SR_B2", "SR_B1"))
            mosaic = col.median()
            vis = {"bands": ["SR_B3", "SR_B2", "SR_B1"], "min": 0, "max": 0.3}
            map_id = mosaic.getMapId(vis)
            tile_url = map_id["tile_fetcher"].url_format
            WidgetUsoSolo._gee_landsat2008_cache = tile_url
            self._aplicar_gee_tiles(tile_url, "Landsat 5 — 2008 (mediana)")
        except Exception as exc:
            QMessageBox.critical(
                self, "Earth Engine",
                f"Erro ao obter tiles Landsat 2008:\n{exc}"
            )
            self._voltar_combo_anterior()

    def _init_earth_engine(self) -> bool:
        try:
            import ee
        except ImportError:
            QMessageBox.warning(
                self, "Earth Engine",
                "Biblioteca 'earthengine-api' não encontrada.\n\n"
                "Instale com: pip install earthengine-api"
            )
            self._voltar_combo_anterior()
            return False
        try:
            ee.Initialize()
        except Exception:
            try:
                ee.Authenticate()
                ee.Initialize()
            except Exception as exc:
                QMessageBox.warning(
                    self, "Earth Engine",
                    f"Não foi possível autenticar no Earth Engine.\n\n"
                    f"Execute no console Python do QGIS:\n"
                    f"  import ee\n"
                    f"  ee.Authenticate()\n\n"
                    f"Erro: {exc}"
                )
                self._voltar_combo_anterior()
                return False
        return True

    def _aplicar_gee_tiles(self, tile_url: str, nome: str = "GEE Layer") -> None:
        uri = f"type=xyz&url={tile_url}&zmax=15&zmin=0"
        layer = QgsRasterLayer(uri, nome, "wms")
        if not layer.isValid():
            QMessageBox.warning(self, "GEE", "Camada de tiles inválida.")
            self._voltar_combo_anterior()
            return
        self._definir_camada_fundo(layer)

    # --- Planet ---------------------------------------------------------

    def _abrir_planet_dialog(self) -> None:
        try:
            from ..core.planet_client import planet_client
        except ImportError:
            QMessageBox.warning(
                self, "Planet",
                "Módulo 'requests' não disponível.\n"
                "Instale com: pip install requests"
            )
            self._voltar_combo_anterior()
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Planet Basemaps")
        dlg.setMinimumWidth(440)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        gb_login = QGroupBox("Login Planet")
        fl = QFormLayout(gb_login)
        status_label = QLabel(
            "✓ Conectado" if planet_client.is_logged_in else "Desconectado"
        )
        status_label.setStyleSheet(
            "color: #27ae60; font-weight:bold;" if planet_client.is_logged_in
            else "color: #e74c3c; font-weight:bold;"
        )
        status_label.setAlignment(Qt.AlignCenter)
        fl.addRow(status_label)

        input_email = QLineEdit()
        input_email.setPlaceholderText("seu.email@dominio.com")
        input_email.setText(WidgetUsoSolo._planet_last_email)
        fl.addRow("Email:", input_email)

        input_senha = QLineEdit()
        input_senha.setEchoMode(QLineEdit.Password)
        input_senha.setPlaceholderText("Senha Planet")
        input_senha.setText(WidgetUsoSolo._planet_last_senha)
        fl.addRow("Senha:", input_senha)

        btn_login = QPushButton("Conectar")
        btn_login.setStyleSheet(botao_style("success"))
        fl.addRow(btn_login)
        layout.addWidget(gb_login)

        gb_mosaico = QGroupBox("Mosaicos Disponíveis")
        vm = QVBoxLayout(gb_mosaico)
        btn_listar = QPushButton("Listar mosaicos")
        btn_listar.setStyleSheet(botao_style("info"))
        btn_listar.setEnabled(planet_client.is_logged_in)
        vm.addWidget(btn_listar)

        lista_mosaicos = QListWidget()
        lista_mosaicos.setMinimumHeight(220)
        vm.addWidget(lista_mosaicos)

        btn_usar = QPushButton("Usar mosaico selecionado")
        btn_usar.setStyleSheet(botao_style("success"))
        btn_usar.setEnabled(False)
        vm.addWidget(btn_usar)
        layout.addWidget(gb_mosaico)

        mosaicos_cache: list = []

        def _fazer_login():
            email = input_email.text().strip()
            senha = input_senha.text()
            if not email or not senha:
                QMessageBox.warning(dlg, "Login", "Informe email e senha.")
                return
            btn_login.setText("Conectando…")
            btn_login.setEnabled(False)
            from qgis.PyQt.QtCore import QCoreApplication
            QCoreApplication.processEvents()

            ok, msg = planet_client.login(email, senha)
            btn_login.setText("Conectar")
            btn_login.setEnabled(True)

            if ok:
                WidgetUsoSolo._planet_last_email = email
                WidgetUsoSolo._planet_last_senha = senha
                status_label.setText("✓ Conectado")
                status_label.setStyleSheet("color: #27ae60; font-weight:bold;")
                btn_listar.setEnabled(True)
                self.combo_xyz.setItemText(
                    self.combo_xyz.findData("planet"), "Planet"
                )
                _listar_mosaicos()
            else:
                status_label.setText(f"✗ {msg}")
                status_label.setStyleSheet("color: #e74c3c; font-weight:bold;")

        def _listar_mosaicos():
            btn_listar.setText("Buscando…")
            btn_listar.setEnabled(False)
            from qgis.PyQt.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            mosaicos = planet_client.list_mosaics(
                name_contains="normalized_analytic"
            )
            mosaicos_cache.clear()
            mosaicos_cache.extend(mosaicos)
            lista_mosaicos.clear()
            for m in mosaicos:
                nome_disp = planet_client.get_mosaic_display_name(m)
                lista_mosaicos.addItem(
                    QListWidgetItem(f"{nome_disp}  ({m.get('name', '')})")
                )
            btn_listar.setText("Listar mosaicos")
            btn_listar.setEnabled(True)
            if mosaicos:
                btn_usar.setEnabled(True)
            else:
                QMessageBox.information(dlg, "Planet", "Nenhum mosaico encontrado.")

        def _usar_mosaico():
            idx = lista_mosaicos.currentRow()
            if idx < 0 or idx >= len(mosaicos_cache):
                QMessageBox.warning(dlg, "Planet", "Selecione um mosaico da lista.")
                return
            mosaic = mosaicos_cache[idx]
            tile_url = planet_client.get_tile_url(mosaic.get("name", ""))
            if not tile_url:
                QMessageBox.warning(dlg, "Planet", "Não foi possível obter URL do mosaico.")
                return
            uri = f"type=xyz&url={tile_url}&zmax=19&zmin=0"
            nome_disp = planet_client.get_mosaic_display_name(mosaic)
            layer = QgsRasterLayer(uri, f"Planet — {nome_disp}", "wms")
            if not layer.isValid():
                QMessageBox.warning(dlg, "Planet", "Camada inválida.")
                return
            self._definir_camada_fundo(layer)
            dlg.accept()

        btn_login.clicked.connect(_fazer_login)
        btn_listar.clicked.connect(_listar_mosaicos)
        btn_usar.clicked.connect(_usar_mosaico)

        if planet_client.is_logged_in:
            _listar_mosaicos()

        result = dlg.exec_()
        if result != QDialog.Accepted:
            self._voltar_combo_anterior()
