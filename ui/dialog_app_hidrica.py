"""Diálogo modal: Categorizar Hidrografia e Gerar APP Hídrica.

Substitui a antiga aba dedicada — agora é uma operação a mais do
padrão "Análise Dinamizada do CAR" disponível direto pelo grupo
"Categorizar Hidrografia e Gerar APP" na aba Adequação de Bases.
"""

from __future__ import annotations

import os
from typing import Optional

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..core import app_hidrica
from ..core.utils import carregar_camada_vetorial, listar_campos
from .estilos import botao_style, groupbox_style


class DialogAppHidrica(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Categorizar Hidrografia e Gerar APP")
        self.setMinimumSize(720, 560)
        self._build_ui()

    # ------------------------------------------------------------------ #
    #                              UI                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        gb_in = QGroupBox("📥 Camadas de hidrografia (qualquer combinação)")
        gb_in.setStyleSheet(groupbox_style())
        f = QFormLayout(gb_in)
        f.setHorizontalSpacing(10)
        f.setVerticalSpacing(8)

        self.input_trecho = self._criar_input(
            "Trecho de drenagem (rios <10 m, LINHA)"
        )
        self.input_tmd = self._criar_input(
            "Trecho de massa d'água (rios margem dupla, POLÍGONO)"
        )
        self.input_massa = self._criar_input(
            "Massa d'água (lagos/lagoas/reservatórios, POLÍGONO)"
        )
        f.addRow("Trecho de drenagem:", self.input_trecho)
        f.addRow("Trecho de massa d'água:", self.input_tmd)
        f.addRow("Massa d'água:", self.input_massa)

        self.combo_campo_cat = QComboBox()
        self.combo_campo_cat.setEnabled(False)
        f.addRow("Campo categoria (massa):", self.combo_campo_cat)
        self.input_massa.line_edit.textChanged.connect(self._on_massa_changed)
        self.combo_campo_cat.currentIndexChanged.connect(self._on_campo_cat_change)

        layout.addWidget(gb_in)

        # --- Reservatórios artificiais (seleção de valores do campo) ---
        gb_res = QGroupBox("🏞 Reservatório Artificial × Lago/Lagoa Natural")
        gb_res.setStyleSheet(groupbox_style())
        vr = QVBoxLayout(gb_res)
        lbl_res = QLabel(
            "Selecione (Ctrl/Shift) os valores do campo categoria que "
            "representam <b>Reservatório Artificial</b> (CLASSE 7, APP 30 m). "
            "Os demais serão tratados como <b>Lago/Lagoa Natural</b> "
            "(CLASSE 6, APP 50/100 m por área)."
        )
        lbl_res.setWordWrap(True)
        lbl_res.setStyleSheet("color: #555; font-size: 11px;")
        vr.addWidget(lbl_res)
        self.lst_reservatorio = QListWidget()
        self.lst_reservatorio.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )
        self.lst_reservatorio.setMinimumHeight(56)
        self.lst_reservatorio.setMaximumHeight(70)
        self.lst_reservatorio.setEnabled(False)
        vr.addWidget(self.lst_reservatorio)
        layout.addWidget(gb_res)

        gb_out = QGroupBox("📂 Saída")
        gb_out.setStyleSheet(groupbox_style())
        f2 = QFormLayout(gb_out)
        h = QHBoxLayout()
        self.input_pasta = QLineEdit()
        self.input_pasta.setPlaceholderText("Pasta de saída para a APP final")
        btn = QPushButton("Procurar…")
        btn.setMinimumWidth(80)
        btn.clicked.connect(self._escolher_pasta)
        h.addWidget(self.input_pasta)
        h.addWidget(btn)
        w_h = self._wrap(h)
        f2.addRow("Pasta:", w_h)

        self.combo_formato = QComboBox()
        self.combo_formato.addItem(
            "ESRI Shapefile (.shp)", ("ESRI Shapefile", ".shp")
        )
        self.combo_formato.addItem("GeoPackage (.gpkg)", ("GPKG", ".gpkg"))
        f2.addRow("Formato:", self.combo_formato)
        layout.addWidget(gb_out)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        gb_log = QGroupBox("📜 Log")
        gb_log.setStyleSheet(groupbox_style())
        vl = QVBoxLayout(gb_log)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(140)
        self.txt_log.setStyleSheet(
            "QTextEdit { background-color: #fefefe; "
            "font-family: Consolas, monospace; font-size: 11px; }"
        )
        vl.addWidget(self.txt_log)
        layout.addWidget(gb_log, 1)

        h_btn = QHBoxLayout()
        self.btn_gerar = QPushButton("🚀 Gerar APP Hídrica")
        self.btn_gerar.setStyleSheet(botao_style("success"))
        self.btn_gerar.clicked.connect(self._executar)
        btn_fechar = QPushButton("Fechar")
        btn_fechar.setStyleSheet(botao_style("secondary"))
        btn_fechar.clicked.connect(self.accept)
        h_btn.addWidget(self.btn_gerar)
        h_btn.addStretch()
        h_btn.addWidget(btn_fechar)
        layout.addLayout(h_btn)

    def _criar_input(self, placeholder: str):
        from qgis.PyQt.QtWidgets import QWidget
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        btn = QPushButton("Procurar…")
        btn.setMinimumWidth(80)
        btn.clicked.connect(lambda: self._escolher_arquivo(le))
        h.addWidget(le)
        h.addWidget(btn)
        w.line_edit = le
        return w

    def _wrap(self, lay):
        from qgis.PyQt.QtWidgets import QWidget
        w = QWidget()
        w.setLayout(lay)
        return w

    # ------------------------------------------------------------------ #
    #                          Handlers UI                                #
    # ------------------------------------------------------------------ #

    def _escolher_arquivo(self, line_edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Camada vetorial", "",
            "Vetoriais (*.shp *.gpkg *.geojson)"
        )
        if path:
            line_edit.setText(path)

    def _escolher_pasta(self) -> None:
        pasta = QFileDialog.getExistingDirectory(self, "Pasta de saída")
        if pasta:
            self.input_pasta.setText(pasta)

    def _on_massa_changed(self) -> None:
        path = self.input_massa.line_edit.text().strip()
        if not path:
            self.combo_campo_cat.clear()
            self.combo_campo_cat.setEnabled(False)
            self.lst_reservatorio.clear()
            self.lst_reservatorio.setEnabled(False)
            return
        layer = carregar_camada_vetorial(path)
        if layer is None:
            self.combo_campo_cat.clear()
            self.combo_campo_cat.setEnabled(False)
            self.lst_reservatorio.clear()
            self.lst_reservatorio.setEnabled(False)
            return
        self._massa_layer_cache = layer
        self.combo_campo_cat.clear()
        self.combo_campo_cat.addItem("— sem categoria —", "")
        self.combo_campo_cat.addItems(listar_campos(layer))
        self.combo_campo_cat.setEnabled(True)
        for nome in ("CATEGORIA", "categoria", "tipo", "TIPO"):
            idx = self.combo_campo_cat.findText(nome)
            if idx >= 0:
                self.combo_campo_cat.setCurrentIndex(idx)
                break

    def _on_campo_cat_change(self) -> None:
        """Popula a lista de valores únicos do campo categoria."""
        self.lst_reservatorio.clear()
        layer = getattr(self, "_massa_layer_cache", None)
        campo = self.combo_campo_cat.currentText().strip()
        if (
            layer is None
            or not campo
            or campo.startswith("—")
            or self.combo_campo_cat.currentData() == ""
        ):
            self.lst_reservatorio.setEnabled(False)
            return
        try:
            valores = sorted({
                str(f[campo]) for f in layer.getFeatures()
                if f[campo] is not None
            })
        except Exception:
            valores = []
        try:
            from ..core.app_hidrica import _eh_reservatorio
        except Exception:
            _eh_reservatorio = lambda _v: False
        for v in valores:
            it = QListWidgetItem(v)
            self.lst_reservatorio.addItem(it)
            try:
                if _eh_reservatorio(v):
                    it.setSelected(True)
            except Exception:
                pass
        self.lst_reservatorio.setEnabled(bool(valores))

    # ------------------------------------------------------------------ #
    #                          Execução                                  #
    # ------------------------------------------------------------------ #

    def _executar(self) -> None:
        trecho_path = self.input_trecho.line_edit.text().strip()
        tmd_path = self.input_tmd.line_edit.text().strip()
        massa_path = self.input_massa.line_edit.text().strip()
        pasta = self.input_pasta.text().strip()

        if not pasta:
            QMessageBox.warning(self, "Pasta", "Informe a pasta de saída.")
            return
        if not (trecho_path or tmd_path or massa_path):
            QMessageBox.warning(
                self, "Camadas",
                "Informe pelo menos uma das três camadas de hidrografia."
            )
            return

        campo_cat = (self.combo_campo_cat.currentText().strip() or None)
        if campo_cat and campo_cat.startswith("—"):
            campo_cat = None
        valores_reserv = [
            it.text() for it in self.lst_reservatorio.selectedItems()
        ]
        params = {
            "trecho": carregar_camada_vetorial(trecho_path) if trecho_path else None,
            "tmd": carregar_camada_vetorial(tmd_path) if tmd_path else None,
            "massa": carregar_camada_vetorial(massa_path) if massa_path else None,
            "pasta": pasta,
            "campo_cat": campo_cat,
            "valores_reservatorio": valores_reserv or None,
        }
        formato, ext = self.combo_formato.currentData()

        self.btn_gerar.setEnabled(False)
        self.progress.setValue(0)
        self.txt_log.clear()
        self._log("<b>Iniciando geração da APP hídrica…</b>")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = app_hidrica.gerar_app_hidrica(
                trecho_drenagem=params["trecho"],
                trecho_massa_dagua=params["tmd"],
                massa_dagua=params["massa"],
                pasta_saida=pasta,
                campo_categoria_massa=params["campo_cat"],
                valores_reservatorio=params["valores_reservatorio"],
                progresso=self._progresso_cb,
                formato_saida=formato,
                extensao=ext,
            )
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self._log(f"<span style='color:#c0392b'>Erro: {exc}</span>")
            self.btn_gerar.setEnabled(True)
            QMessageBox.critical(self, "Erro", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self._on_terminado(res)

    def _progresso_cb(self, percentual: int, mensagem: str) -> None:
        self.progress.setValue(int(percentual))
        self._log(mensagem)
        QApplication.processEvents()

    def _on_terminado(self, res) -> None:
        self.btn_gerar.setEnabled(True)
        if isinstance(res, Exception):
            self._log(f"<span style='color:#c0392b'>Erro: {res}</span>")
            QMessageBox.critical(self, "Erro", str(res))
            return
        for msg in res.log:
            self._log(msg)
        if res.sucesso and (res.camada_app is not None or res.camada_hidro_categorizada is not None):
            if res.camada_hidro_categorizada is not None:
                QgsProject.instance().addMapLayer(res.camada_hidro_categorizada)
            if res.camada_app is not None:
                QgsProject.instance().addMapLayer(res.camada_app)
            arquivos = "\n• ".join(res.arquivos) if res.arquivos else "—"
            QMessageBox.information(
                self, "Concluído",
                f"Geração concluída com sucesso!\n\nArquivos:\n• {arquivos}"
            )
        else:
            QMessageBox.warning(
                self, "Atenção",
                "A geração terminou com problemas. Verifique o log."
            )

    def _log(self, msg: str) -> None:
        self.txt_log.append(msg)
