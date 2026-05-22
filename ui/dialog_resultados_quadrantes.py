"""Diálogo modal com os resultados de avaliação dos quadrantes.

Mostra as estatísticas brutas (sem parecer de aprovação) e permite ao
usuário configurar PEC desejado (classe + escala) e LQA máximo apenas
para visualizar como o resultado se compararia.

Permite exportar tudo em CSV.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from qgis.core import QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .estilos import botao_style, groupbox_style


PEC_TABELA = {
    "1:25.000":  {"A": 12.5, "B": 20.0, "C": 25.0},
    "1:50.000":  {"A": 25.0, "B": 40.0, "C": 50.0},
    "1:100.000": {"A": 50.0, "B": 80.0, "C": 100.0},
    "1:250.000": {"A": 125.0, "B": 200.0, "C": 250.0},
}


class DialogResultadosQuadrantes(QDialog):

    def __init__(self, parent, camada_quadrantes: QgsVectorLayer):
        super().__init__(parent)
        self.setWindowTitle("Resultados da Avaliação — Outras Bases")
        self.setMinimumSize(780, 600)

        self.camada = camada_quadrantes
        self.dados = self._coletar_dados()

        self._build_ui()
        self._atualizar()

    # ------------------------------------------------------------------ #
    #                          Coleta de dados                           #
    # ------------------------------------------------------------------ #

    def _coletar_dados(self) -> List[Dict]:
        registros = []
        nomes = [f.name() for f in self.camada.fields()]
        for feat in self.camada.getFeatures():
            apr = feat["aprovado"] if "aprovado" in nomes else None
            try:
                dist = float(feat["dist_max_m"]) if "dist_max_m" in nomes and feat["dist_max_m"] is not None else 0.0
            except (TypeError, ValueError):
                dist = 0.0
            try:
                om = int(feat["erro_omissao"]) if "erro_omissao" in nomes and feat["erro_omissao"] is not None else 0
            except (TypeError, ValueError):
                om = 0
            try:
                com = int(feat["erro_comissao"]) if "erro_comissao" in nomes and feat["erro_comissao"] is not None else 0
            except (TypeError, ValueError):
                com = 0
            id_q = feat["id"] if "id" in nomes else feat.id()
            registros.append({
                "id": id_q,
                "aprovado": apr,
                "dist": dist,
                "omissao": om,
                "comissao": com,
            })
        return registros

    # ------------------------------------------------------------------ #
    #                              UI                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # --- Bloco de critérios (apenas referência, não aprovação automática) ---
        gb_crit = QGroupBox("Critérios para referência (PEC e LQA)")
        gb_crit.setStyleSheet(groupbox_style())
        f = QFormLayout(gb_crit)

        self.combo_escala = QComboBox()
        for escala in PEC_TABELA.keys():
            self.combo_escala.addItem(escala)
        self.combo_escala.currentIndexChanged.connect(self._atualizar)
        f.addRow("Escala do produto:", self.combo_escala)

        self.combo_classe_pec = QComboBox()
        self.combo_classe_pec.addItems(["A", "B", "C"])
        self.combo_classe_pec.setCurrentIndex(1)
        self.combo_classe_pec.currentIndexChanged.connect(self._atualizar)
        f.addRow("Classe PEC:", self.combo_classe_pec)

        self.spin_lqa = QDoubleSpinBox()
        self.spin_lqa.setRange(0, 1.0)
        self.spin_lqa.setSingleStep(0.01)
        self.spin_lqa.setValue(0.10)
        self.spin_lqa.setDecimals(3)
        self.spin_lqa.setSuffix(" (fração de quadrantes)")
        self.spin_lqa.valueChanged.connect(self._atualizar)
        f.addRow("LQA máximo:", self.spin_lqa)

        self.spin_max_oc = QSpinBox()
        self.spin_max_oc.setRange(0, 9999)
        self.spin_max_oc.setValue(0)
        self.spin_max_oc.setSuffix(" feições")
        self.spin_max_oc.valueChanged.connect(self._atualizar)
        f.addRow("Máx. (omissão + comissão) por quadrante:", self.spin_max_oc)

        layout.addWidget(gb_crit)

        # --- Estatísticas resumo ---
        gb_res = QGroupBox("Resumo")
        gb_res.setStyleSheet(groupbox_style())
        vr = QVBoxLayout(gb_res)
        self.lbl_resumo = QLabel()
        self.lbl_resumo.setStyleSheet(
            "QLabel { font-size: 12px; padding: 6px; }"
        )
        self.lbl_resumo.setWordWrap(True)
        vr.addWidget(self.lbl_resumo)
        layout.addWidget(gb_res)

        # --- Tabela detalhada por quadrante ---
        gb_tab = QGroupBox("Detalhamento por quadrante")
        gb_tab.setStyleSheet(groupbox_style())
        vt = QVBoxLayout(gb_tab)
        self.tbl = QTableWidget()
        self.tbl.setStyleSheet(
            """
            QTableWidget { gridline-color: #b0b6bb; font-size: 11px; }
            QHeaderView::section { background: #1a5276; color: white;
                font-weight: bold; padding: 4px; }
            """
        )
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setSelectionMode(QTableWidget.NoSelection)
        self.tbl.setFocusPolicy(Qt.NoFocus)
        vt.addWidget(self.tbl)
        layout.addWidget(gb_tab, 1)

        # --- Botões ---
        h = QHBoxLayout()
        btn_csv = QPushButton("💾 Salvar CSV")
        btn_csv.setStyleSheet(botao_style("info"))
        btn_csv.clicked.connect(self._salvar_csv)
        btn_fechar = QPushButton("Fechar")
        btn_fechar.setStyleSheet(botao_style("secondary"))
        btn_fechar.clicked.connect(self.accept)
        h.addWidget(btn_csv)
        h.addStretch()
        h.addWidget(btn_fechar)
        layout.addLayout(h)

    # ------------------------------------------------------------------ #
    #                            Cálculo                                  #
    # ------------------------------------------------------------------ #

    def _atualizar(self) -> None:
        escala = self.combo_escala.currentText()
        classe = self.combo_classe_pec.currentText()
        em_max = PEC_TABELA[escala][classe]
        lqa_max = self.spin_lqa.value()
        max_oc = int(self.spin_max_oc.value())

        total = len(self.dados)
        avaliados = sum(1 for d in self.dados if d["aprovado"] in (0, 1))
        soma_dist = sum(d["dist"] for d in self.dados if d["aprovado"] in (0, 1))
        media_dist = soma_dist / max(1, avaliados)
        dist_max_global = max((d["dist"] for d in self.dados if d["aprovado"] in (0, 1)), default=0.0)
        total_om = sum(d["omissao"] for d in self.dados if d["aprovado"] in (0, 1))
        total_com = sum(d["comissao"] for d in self.dados if d["aprovado"] in (0, 1))

        # Contagens de "fora do critério" — apenas informativas
        fora_pec = sum(
            1 for d in self.dados
            if d["aprovado"] in (0, 1) and d["dist"] > em_max
        )
        fora_oc = sum(
            1 for d in self.dados
            if d["aprovado"] in (0, 1) and (d["omissao"] + d["comissao"]) > max_oc
        )
        reprovados_julg = sum(1 for d in self.dados if d["aprovado"] == 0)

        # LQA observado: quantos reprovados (qualquer critério) / avaliados
        ids_fora = set()
        for d in self.dados:
            if d["aprovado"] not in (0, 1):
                continue
            if d["aprovado"] == 0 or d["dist"] > em_max or (d["omissao"] + d["comissao"]) > max_oc:
                ids_fora.add(d["id"])
        lqa_obs = len(ids_fora) / max(1, avaliados)

        cor_lqa = "#27ae60" if lqa_obs <= lqa_max else "#c0392b"
        cor_pec = "#27ae60" if fora_pec == 0 else "#c0392b"

        self.lbl_resumo.setText(
            f"<b>Total de quadrantes:</b> {total} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Avaliados:</b> {avaliados} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Reprovados (julgamento manual):</b> {reprovados_julg}<br>"
            f"<b>Distância média:</b> {media_dist:.2f} m &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Distância máxima:</b> {dist_max_global:.2f} m &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>EM máx. p/ PEC {classe} ({escala}):</b> {em_max:.2f} m<br>"
            f"<b>Σ Omissão:</b> {total_om} &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"<b>Σ Comissão:</b> {total_com}<br>"
            f"<b style='color:{cor_pec};'>Quadrantes com distância &gt; EM máx.: {fora_pec}</b><br>"
            f"<b>Quadrantes com (omissão+comissão) &gt; {max_oc}: {fora_oc}</b><br>"
            f"<b style='color:{cor_lqa};'>LQA observado: {lqa_obs * 100:.2f}% "
            f"(limite: {lqa_max * 100:.2f}%)</b>"
        )

        # Tabela detalhada
        self.tbl.clear()
        cabs = ["ID", "Julgamento", "Distância (m)", "PEC?", "Omissão", "Comissão", "Σ O+C", "Dentro Σ?"]
        self.tbl.setColumnCount(len(cabs))
        self.tbl.setHorizontalHeaderLabels(cabs)
        self.tbl.setRowCount(len(self.dados))

        def _it(t, color=None, bold=False) -> QTableWidgetItem:
            it = QTableWidgetItem(str(t))
            it.setTextAlignment(Qt.AlignCenter)
            f = QFont()
            f.setBold(bold)
            it.setFont(f)
            if color:
                it.setForeground(QColor(color))
            it.setFlags(Qt.ItemIsEnabled)
            return it

        for i, d in enumerate(self.dados):
            self.tbl.setItem(i, 0, _it(d["id"]))
            apr = d["aprovado"]
            if apr == 1:
                self.tbl.setItem(i, 1, _it("✓ Aprovado", color="#27ae60", bold=True))
            elif apr == 0:
                self.tbl.setItem(i, 1, _it("✗ Reprovado", color="#c0392b", bold=True))
            else:
                self.tbl.setItem(i, 1, _it("— não avaliado —", color="#888"))
            self.tbl.setItem(i, 2, _it(f"{d['dist']:.2f}"))
            dentro_pec = d["dist"] <= em_max
            self.tbl.setItem(i, 3, _it(
                "✓" if dentro_pec else "✗",
                color="#27ae60" if dentro_pec else "#c0392b",
                bold=True
            ))
            self.tbl.setItem(i, 4, _it(d["omissao"]))
            self.tbl.setItem(i, 5, _it(d["comissao"]))
            soma = d["omissao"] + d["comissao"]
            self.tbl.setItem(i, 6, _it(soma, bold=True))
            dentro_oc = soma <= max_oc
            self.tbl.setItem(i, 7, _it(
                "✓" if dentro_oc else "✗",
                color="#27ae60" if dentro_oc else "#c0392b",
                bold=True
            ))

        self.tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    # ------------------------------------------------------------------ #
    #                          Exportação                                #
    # ------------------------------------------------------------------ #

    def _salvar_csv(self) -> None:
        import csv
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvar resultados como CSV", "resultados_quadrantes.csv",
            "CSV (*.csv)"
        )
        if not path:
            return
        escala = self.combo_escala.currentText()
        classe = self.combo_classe_pec.currentText()
        em_max = PEC_TABELA[escala][classe]
        max_oc = int(self.spin_max_oc.value())
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["RESULTADOS DA AVALIAÇÃO DE QUADRANTES"])
                w.writerow(["Escala", escala])
                w.writerow(["Classe PEC", classe])
                w.writerow(["EM máx. (m)", f"{em_max:.2f}"])
                w.writerow(["LQA máx.", f"{self.spin_lqa.value():.3f}"])
                w.writerow(["Máx. (O+C) por quadrante", max_oc])
                w.writerow([])
                w.writerow([
                    "ID", "Julgamento", "Distância_m",
                    "Dentro_PEC", "Omissão", "Comissão",
                    "Soma_OC", "Dentro_OC"
                ])
                for d in self.dados:
                    apr_txt = (
                        "Aprovado" if d["aprovado"] == 1
                        else ("Reprovado" if d["aprovado"] == 0 else "Não avaliado")
                    )
                    soma = d["omissao"] + d["comissao"]
                    w.writerow([
                        d["id"],
                        apr_txt,
                        f"{d['dist']:.2f}",
                        "sim" if d["dist"] <= em_max else "não",
                        d["omissao"],
                        d["comissao"],
                        soma,
                        "sim" if soma <= max_oc else "não",
                    ])
            QMessageBox.information(self, "Exportado", f"Arquivo salvo em:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))
