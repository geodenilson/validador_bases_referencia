"""Aba — Adequação de Bases.

Estrutura única (sem sub-abas):
    * Grupo "Camadas" — base a adequar + mapa de fundo.
    * Grupo "Padrão de Adequação" — único padrão: "Análise Dinamizada do CAR".
      O tipo de base define o conjunto de ações:
        - Uso do Solo → 4 shapefiles (ANTROPIZADO, CONSOLIDADO,
          VEGETACAO_ATUAL, VEGETACAO_2008) por agrupamento de valores.
        - Fitofisionomia → 3 shapefiles (AML_FLORESTA, AML_CERRADO,
          AML_CAMPO) por agrupamento de valores.
        - APP_ESPECIAL / RELEVO / SERVIDAO / USO_RESTRITO → 1 shapefile
          unificado com coluna CLASSE, atribuindo valores do campo origem
          a cada classe do padrão.
    * Grupo "Geração de APP Hídrica" — atalho para a aba dedicada.

A hidrografia / APP hídrica ficam fora deste grupo: são tratadas na
aba "Geração de APP Hídrica".
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
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
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core import adequacao_bases
from ..core.utils import carregar_camada_vetorial, listar_campos
from .estilos import botao_style, groupbox_style
from .helpers_basemap import BasemapManager


# Estrutura de cada "tipo" de base que esta aba sabe adequar.
TIPOS_BASE: Dict[str, dict] = {
    "uso_solo": {
        "rotulo": "Uso do Solo",
        "modo": "individuais",
        "nomes_destino": [
            "ANTROPIZADO",
            "CONSOLIDADO",
            "VEGETACAO_ATUAL",
            "VEGETACAO_2008",
        ],
    },
    "fitofisionomia": {
        "rotulo": "Fitofisionomia",
        "modo": "individuais",
        "nomes_destino": [
            "AML_FLORESTA",
            "AML_CERRADO",
            "AML_CAMPO",
        ],
    },
    "app_especial": {
        "rotulo": "APP_ESPECIAL",
        "modo": "unificada",
        "nome_arquivo": "APP_ESPECIAL",
        "estrutura": adequacao_bases.ESTRUTURA_APP_ESPECIAL,
    },
    "relevo": {
        "rotulo": "RELEVO",
        "modo": "unificada",
        "nome_arquivo": "RELEVO",
        "estrutura": adequacao_bases.ESTRUTURA_RELEVO,
    },
    "servidao": {
        "rotulo": "SERVIDAO",
        "modo": "unificada",
        "nome_arquivo": "SERVIDAO",
        "estrutura": adequacao_bases.ESTRUTURA_SERVIDAO,
    },
    "uso_restrito": {
        "rotulo": "USO_RESTRITO",
        "modo": "unificada",
        "nome_arquivo": "USO_RESTRITO",
        "estrutura": adequacao_bases.ESTRUTURA_USO_RESTRITO,
    },
}


class TabAdequacao(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self.camada_atual: Optional[QgsVectorLayer] = None
        self.basemap: Optional[BasemapManager] = None
        # Widgets construídos por tipo (chave = id do tipo).
        self._paginas: Dict[str, QWidget] = {}
        # Para cada página guarda o handle do campo escolhido + listas.
        self._estado_paginas: Dict[str, dict] = {}
        self._build_ui()

    # ================================================================== #
    #                              UI                                    #
    # ================================================================== #

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Horizontal)

        # Canvas precisa existir antes dos blocos da esquerda (alguns
        # acessam self.canvas durante a construção).
        self.canvas = self.mw.criar_canvas_mapa()

        # ---------- Painel esquerdo (rolável e responsivo) ----------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setMinimumWidth(440)
        scroll.setMaximumWidth(460)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        painel = QWidget()
        painel.setMaximumWidth(460)
        scroll.setWidget(painel)
        v = QVBoxLayout(painel)
        v.setSpacing(8)
        v.setContentsMargins(6, 6, 6, 6)

        v.addWidget(self._criar_bloco_camadas())
        v.addWidget(self._criar_bloco_padrao())
        v.addWidget(self._criar_bloco_app_hidrica())

        # Log de execução
        gb_log = QGroupBox("📝 Log")
        gb_log.setStyleSheet(groupbox_style())
        vl = QVBoxLayout(gb_log)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(90)
        self.txt_log.setStyleSheet(
            "QTextEdit { background-color: #fefefe; "
            "font-family: Consolas, monospace; font-size: 11px; }"
        )
        vl.addWidget(self.txt_log)
        v.addWidget(gb_log)

        v.addStretch()

        splitter.addWidget(scroll)
        splitter.addWidget(self.canvas)
        splitter.setCollapsible(0, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 900])

        layout.addWidget(splitter)

    def _wrap(self, lay) -> QWidget:
        w = QWidget()
        w.setLayout(lay)
        return w

    # ---------------------------- Grupo 1 --------------------------------
    def _criar_bloco_camadas(self) -> QGroupBox:
        gb = QGroupBox("📂 Camadas")
        gb.setStyleSheet(groupbox_style())
        f = QFormLayout(gb)

        h = QHBoxLayout()
        self.input_camada = QLineEdit()
        self.input_camada.setPlaceholderText(
            "Camada vetorial a adequar (.shp/.gpkg/.geojson)"
        )
        btn = QPushButton("Procurar…")
        btn.setMinimumWidth(80)
        btn.clicked.connect(self._escolher_camada)
        h.addWidget(self.input_camada)
        h.addWidget(btn)
        f.addRow("Base a adequar:", self._wrap(h))

        self.basemap = BasemapManager(self, self.canvas, self.mw.config)
        f.addRow("Mapa de fundo:", self.basemap.criar_combo())
        return gb

    # ---------------------------- Grupo 2 --------------------------------
    def _criar_bloco_padrao(self) -> QGroupBox:
        gb = QGroupBox("⚙ Padrão de Adequação")
        gb.setStyleSheet(groupbox_style())
        v = QVBoxLayout(gb)

        f = QFormLayout()
        self.combo_padrao = QComboBox()
        for pid, nome in adequacao_bases.listar_padroes():
            self.combo_padrao.addItem(nome, pid)
        self.combo_padrao.setCurrentIndex(0)
        f.addRow("Padrão:", self.combo_padrao)

        h_pasta = QHBoxLayout()
        self.input_pasta = QLineEdit()
        self.input_pasta.setPlaceholderText(
            "Pasta onde os arquivos adequados serão salvos"
        )
        btn_pasta = QPushButton("Procurar…")
        btn_pasta.setMinimumWidth(80)
        btn_pasta.clicked.connect(self._escolher_pasta)
        h_pasta.addWidget(self.input_pasta)
        h_pasta.addWidget(btn_pasta)
        f.addRow("Pasta de saída:", self._wrap(h_pasta))

        self.combo_formato = QComboBox()
        self.combo_formato.addItem(
            "ESRI Shapefile (.shp)", ("ESRI Shapefile", ".shp")
        )
        self.combo_formato.addItem(
            "GeoPackage (.gpkg)", ("GPKG", ".gpkg")
        )
        f.addRow("Formato:", self.combo_formato)

        self.combo_tipo_base = QComboBox()
        for key, info in TIPOS_BASE.items():
            self.combo_tipo_base.addItem(info["rotulo"], key)
        self.combo_tipo_base.currentIndexChanged.connect(self._on_tipo_change)
        f.addRow("Tipo de base:", self.combo_tipo_base)

        # Quebra de polígonos por nº de vértices (≡ Dice do ArcGIS Pro)
        h_dice = QHBoxLayout()
        self.chk_dice = QCheckBox("Quebrar polígonos com mais de")
        self.chk_dice.setToolTip(
            "Equivalente à ferramenta Dice do ArcGIS Pro — usa "
            "native:subdivide para dividir polígonos complexos."
        )
        self.spin_dice = QSpinBox()
        self.spin_dice.setRange(50, 100000)
        self.spin_dice.setValue(500)
        self.spin_dice.setSuffix(" vértices")
        self.spin_dice.setEnabled(False)
        self.chk_dice.toggled.connect(self.spin_dice.setEnabled)
        h_dice.addWidget(self.chk_dice)
        h_dice.addWidget(self.spin_dice, 1)
        f.addRow(self._wrap(h_dice))

        v.addLayout(f)

        # Stack para mostrar opções específicas conforme o tipo
        self.stack_tipos = QStackedWidget()
        for key in TIPOS_BASE.keys():
            page = self._criar_pagina_tipo(key)
            self._paginas[key] = page
            self.stack_tipos.addWidget(page)
        v.addWidget(self.stack_tipos)

        return gb

    # ---------------------------- Grupo 3 --------------------------------
    def _criar_bloco_app_hidrica(self) -> QGroupBox:
        gb = QGroupBox("💧 Categorizar Hidrografia e Gerar APP")
        gb.setStyleSheet(groupbox_style())
        v = QVBoxLayout(gb)

        lbl = QLabel(
            "Atribui CLASSE 1–8 à hidrografia conforme a Lei 12.651/2012 "
            "e gera a APP correspondente em um único passo."
        )
        lbl.setWordWrap(True)
        lbl.setStyleSheet("QLabel { color: #444; padding: 4px; font-size: 11px; }")
        v.addWidget(lbl)

        btn = QPushButton("Abrir assistente…")
        btn.setStyleSheet(botao_style("primary"))
        btn.clicked.connect(self._abrir_dialog_app_hidrica)
        v.addWidget(btn)
        return gb

    def _abrir_dialog_app_hidrica(self) -> None:
        from .dialog_app_hidrica import DialogAppHidrica
        dlg = DialogAppHidrica(self)
        dlg.exec_()

    # ================================================================== #
    #                       Páginas do stack                             #
    # ================================================================== #

    def _criar_pagina_tipo(self, key: str) -> QWidget:
        info = TIPOS_BASE[key]
        if info["modo"] == "individuais":
            return self._criar_pagina_individuais(key, info["nomes_destino"])
        return self._criar_pagina_unificada(
            key, info["nome_arquivo"], info["estrutura"]
        )

    def _criar_pagina_individuais(
        self, key: str, nomes_destino: List[str]
    ) -> QWidget:
        return self._criar_pagina_generica(
            key=key,
            modo="individuais",
            entradas=[(nome, nome) for nome in nomes_destino],
            label_combo_categoria="Categoria a configurar:",
            label_campo="Campo da classe:",
            label_lista="Valores que pertencem à categoria:",
            botao_acao=("Separar e salvar", lambda: self._executar_individuais(key)),
        )

    def _criar_pagina_unificada(
        self, key: str, nome_arquivo: str, estrutura: list
    ) -> QWidget:
        entradas = [
            (int(c), f"CLASSE {c} — {desc}") for c, desc in estrutura
        ]
        return self._criar_pagina_generica(
            key=key,
            modo="unificada",
            entradas=entradas,
            label_combo_categoria="CLASSE a configurar:",
            label_campo="Campo da classe (origem):",
            label_lista="Valores do campo que pertencem à CLASSE:",
            botao_acao=("Gerar base unificada", lambda: self._executar_unificada(key)),
            nome_arquivo=nome_arquivo,
            estrutura=estrutura,
        )

    def _criar_pagina_generica(
        self,
        key: str,
        modo: str,
        entradas,
        label_combo_categoria: str,
        label_campo: str,
        label_lista: str,
        botao_acao,
        nome_arquivo: Optional[str] = None,
        estrutura: Optional[list] = None,
    ) -> QWidget:
        """Página enxuta: campo → combo de categoria → 1 lista contextual.

        ``entradas`` é uma lista de tuplas (chave_interna, rótulo_exibido).
        A ``chave_interna`` é ``str`` (nome do arquivo, no modo individuais)
        ou ``int`` (CLASSE, no modo unificada).
        """
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(8)

        f = QFormLayout()
        combo_campo = QComboBox()
        combo_campo.setEnabled(False)
        f.addRow(label_campo, combo_campo)

        btn_detect = QPushButton("Detectar valores únicos")
        btn_detect.setStyleSheet(botao_style("info"))
        btn_detect.clicked.connect(lambda: self._popular_valores_unicos(key))
        f.addRow(btn_detect)

        combo_categoria = QComboBox()
        combo_categoria.setEnabled(False)
        for chave, rotulo in entradas:
            combo_categoria.addItem(rotulo, chave)
        f.addRow(label_combo_categoria, combo_categoria)
        v.addLayout(f)

        gb_lst = QGroupBox(label_lista)
        gb_lst.setStyleSheet(groupbox_style())
        vg = QVBoxLayout(gb_lst)
        vg.setContentsMargins(6, 14, 6, 6)

        hint = QLabel(
            "Use Ctrl/Shift para selecionar múltiplos valores. "
            "Trocar a categoria preserva as seleções já feitas, permitindo "
            "salvar todas de uma vez no final da seleção."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555; font-size: 11px;")
        vg.addWidget(hint)

        lst = QListWidget()
        lst.setSelectionMode(QAbstractItemView.ExtendedSelection)
        lst.setMinimumHeight(60)
        lst.setMaximumHeight(95)
        lst.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        vg.addWidget(lst)
        v.addWidget(gb_lst)

        btn_text, btn_handler = botao_acao
        btn = QPushButton(btn_text)
        btn.setStyleSheet(botao_style("success"))
        btn.clicked.connect(btn_handler)
        v.addWidget(btn)
        v.addStretch()

        # Estado da página
        selecoes_iniciais = {chave: [] for chave, _ in entradas}
        estado = {
            "modo": modo,
            "combo_campo": combo_campo,
            "combo_categoria": combo_categoria,
            "lst": lst,
            "selecoes": selecoes_iniciais,
            "valores_unicos": [],
            "entradas": entradas,
        }
        if modo == "individuais":
            estado["nomes_destino"] = [c for c, _ in entradas]
        else:
            estado["nome_arquivo"] = nome_arquivo
            estado["estrutura"] = estrutura
        self._estado_paginas[key] = estado

        combo_categoria.currentIndexChanged.connect(
            lambda _i, k=key: self._on_categoria_change(k)
        )
        lst.itemSelectionChanged.connect(
            lambda k=key: self._on_selecao_change(k)
        )
        return w

    # --------------- estado dinâmico das páginas ----------------------
    def _on_categoria_change(self, key: str) -> None:
        estado = self._estado_paginas.get(key)
        if estado is None:
            return
        if not estado["valores_unicos"]:
            return
        # Re-aplica a seleção persistida para a categoria atual.
        chave_atual = estado["combo_categoria"].currentData()
        if chave_atual is None:
            return
        lst: QListWidget = estado["lst"]
        salvos = set(estado["selecoes"].get(chave_atual, []))
        lst.blockSignals(True)
        for i in range(lst.count()):
            it = lst.item(i)
            it.setSelected(it.text() in salvos)
        lst.blockSignals(False)

    def _on_selecao_change(self, key: str) -> None:
        estado = self._estado_paginas.get(key)
        if estado is None:
            return
        chave_atual = estado["combo_categoria"].currentData()
        if chave_atual is None:
            return
        lst: QListWidget = estado["lst"]
        estado["selecoes"][chave_atual] = [
            it.text() for it in lst.selectedItems()
        ]

    # ================================================================== #
    #                          Handlers gerais                           #
    # ================================================================== #

    def _on_tipo_change(self) -> None:
        key = self.combo_tipo_base.currentData()
        if key is None:
            return
        idx = list(TIPOS_BASE.keys()).index(key)
        self.stack_tipos.setCurrentIndex(idx)
        # Reaplica o campo da camada (se já houver) ao novo combo
        if self.camada_atual is not None:
            self._popular_campo_no_estado(key)

    def _popular_campo_no_estado(self, key: str) -> None:
        estado = self._estado_paginas.get(key)
        if estado is None or self.camada_atual is None:
            return
        combo = estado["combo_campo"]
        combo.clear()
        combo.addItems(listar_campos(self.camada_atual))
        combo.setEnabled(True)
        # Heurística: marca 'classe' se existir
        for nome in ("classe", "CLASSE", "Classe", "fitofisionomia", "tipo"):
            idx = combo.findText(nome)
            if idx >= 0:
                combo.setCurrentIndex(idx)
                break

    def _escolher_camada(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Camada a adequar", "",
            "Vetoriais (*.shp *.gpkg *.geojson *.kml)"
        )
        if not path:
            return
        layer = carregar_camada_vetorial(path)
        if layer is None or not layer.isValid():
            QMessageBox.warning(self, "Erro", "Camada inválida.")
            return
        # Remove a base anterior do mapa/projeto antes de carregar a nova.
        self._remover_camada_anterior()
        QgsProject.instance().addMapLayer(layer)
        self.input_camada.setText(path)
        self.camada_atual = layer
        self.mw.adicionar_camada_no_canvas(self.canvas, layer)
        # Atualiza todos os combos de campo de todas as páginas
        for key in TIPOS_BASE.keys():
            self._popular_campo_no_estado(key)
        # Limpa estado de listas/seleções
        for key, estado in self._estado_paginas.items():
            estado["lst"].clear()
            estado["valores_unicos"] = []
            estado["combo_categoria"].setEnabled(False)
            estado["selecoes"] = {
                chave: [] for chave, _ in estado["entradas"]
            }

    def _remover_camada_anterior(self) -> None:
        """Remove a base previamente carregada do canvas e do projeto."""
        anterior = self.camada_atual
        if anterior is None:
            return
        try:
            camadas = list(self.canvas.layers())
            camadas = [c for c in camadas if c.id() != anterior.id()]
            self.canvas.setLayers(camadas)
        except RuntimeError:
            pass
        try:
            QgsProject.instance().removeMapLayer(anterior.id())
        except Exception:
            pass
        self.camada_atual = None

    def _escolher_pasta(self) -> None:
        pasta = QFileDialog.getExistingDirectory(self, "Pasta de saída")
        if pasta:
            self.input_pasta.setText(pasta)

    def _popular_valores_unicos(self, key: str) -> None:
        estado = self._estado_paginas.get(key)
        if estado is None:
            return
        if self.camada_atual is None or not self.camada_atual.isValid():
            QMessageBox.warning(self, "Camada", "Carregue a camada primeiro.")
            return
        combo = estado["combo_campo"]
        campo = combo.currentText().strip()
        if not campo:
            QMessageBox.warning(self, "Campo", "Selecione o campo da classe.")
            return
        try:
            valores = sorted({
                str(f[campo]) for f in self.camada_atual.getFeatures()
                if f[campo] is not None
            })
        except Exception as exc:
            QMessageBox.warning(self, "Campo", f"Erro ao ler valores: {exc}")
            return

        estado["valores_unicos"] = valores
        # Reseta seleções pois o campo mudou.
        estado["selecoes"] = {chave: [] for chave, _ in estado["entradas"]}
        lst: QListWidget = estado["lst"]
        lst.blockSignals(True)
        lst.clear()
        for v in valores:
            lst.addItem(QListWidgetItem(v))
        lst.blockSignals(False)

        estado["combo_categoria"].setEnabled(True)
        # Força refresco para a categoria atual
        self._on_categoria_change(key)

        self._log(
            f"<b>{TIPOS_BASE[key]['rotulo']}</b> — {len(valores)} valores "
            f"únicos detectados em '{campo}'."
        )

    # ================================================================== #
    #                          Execução                                  #
    # ================================================================== #

    def _validar_pasta_e_formato(self):
        pasta = self.input_pasta.text().strip()
        if not pasta:
            QMessageBox.warning(self, "Pasta", "Informe a pasta de saída.")
            return None, None, None
        formato, ext = self.combo_formato.currentData()
        return pasta, formato, ext

    def _max_vertices_selecionado(self):
        """Retorna o valor de máx. vértices se o checkbox estiver marcado."""
        if self.chk_dice.isChecked():
            return int(self.spin_dice.value())
        return None

    def _executar_individuais(self, key: str) -> None:
        if self.camada_atual is None:
            QMessageBox.warning(self, "Camada", "Carregue a camada primeiro.")
            return
        estado = self._estado_paginas[key]
        campo = estado["combo_campo"].currentText().strip()
        if not campo:
            QMessageBox.warning(self, "Campo", "Selecione o campo da classe.")
            return
        pasta, formato, ext = self._validar_pasta_e_formato()
        if pasta is None:
            return

        valores_por_destino: Dict[str, List[str]] = {
            nome: vals for nome, vals in estado["selecoes"].items() if vals
        }

        if not valores_por_destino:
            QMessageBox.warning(
                self, "Seleção",
                "Selecione ao menos um valor para alguma categoria."
            )
            return

        max_v = self._max_vertices_selecionado()
        sufixo = (
            f" (subdividindo ≤ {max_v} vértices)" if max_v else ""
        )
        self._log(
            f"<b>Separando {TIPOS_BASE[key]['rotulo']} → {pasta}</b>{sufixo}"
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = adequacao_bases.separar_em_individuais_multi(
                self.camada_atual, campo, valores_por_destino,
                pasta, formato, ext, max_vertices=max_v,
            )
        finally:
            QApplication.restoreOverrideCursor()
        for msg in res.log:
            self._log(msg)
        self._mostrar_resultado_final(res)

    def _executar_unificada(self, key: str) -> None:
        if self.camada_atual is None:
            QMessageBox.warning(self, "Camada", "Carregue a camada primeiro.")
            return
        estado = self._estado_paginas[key]
        campo = estado["combo_campo"].currentText().strip()
        if not campo:
            QMessageBox.warning(self, "Campo", "Selecione o campo da classe.")
            return
        pasta, formato, ext = self._validar_pasta_e_formato()
        if pasta is None:
            return

        valores_por_classe: Dict[int, List[str]] = {
            int(c): vals for c, vals in estado["selecoes"].items() if vals
        }

        if not valores_por_classe:
            QMessageBox.warning(
                self, "Seleção",
                "Selecione ao menos um valor para alguma CLASSE."
            )
            return

        nome_arquivo = estado["nome_arquivo"]
        max_v = self._max_vertices_selecionado()
        sufixo = (
            f" (subdividindo ≤ {max_v} vértices)" if max_v else ""
        )
        self._log(
            f"<b>Gerando base unificada {nome_arquivo} → {pasta}</b>{sufixo}"
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = adequacao_bases.gerar_unificada_com_classe(
                self.camada_atual, campo, valores_por_classe,
                nome_arquivo, pasta, formato, ext,
                max_vertices=max_v,
            )
        finally:
            QApplication.restoreOverrideCursor()
        for msg in res.log:
            self._log(msg)
        self._mostrar_resultado_final(res)

    def _mostrar_resultado_final(self, res) -> None:
        if res.sucesso and res.arquivos_gerados:
            msg = "Arquivos gerados:\n• " + "\n• ".join(res.arquivos_gerados)
            QMessageBox.information(self, "Concluído", msg)
        else:
            detalhes = "\n".join(res.log[-6:]) if res.log else ""
            QMessageBox.warning(
                self, "Falha",
                "Nenhum arquivo gerado. Veja o log para detalhes:\n\n"
                + detalhes
            )

    # ================================================================== #
    #                              Log                                   #
    # ================================================================== #

    def _log(self, msg: str) -> None:
        self.txt_log.append(msg)
