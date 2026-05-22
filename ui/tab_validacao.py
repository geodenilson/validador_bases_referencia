"""Aba — Avaliação da Qualidade.

Subseções:
    * Uso do Solo: amostragem + rotulagem + matriz de confusão (modal).
    * Quadrantes & PEC: avaliação posicional de polígonos.
"""

from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .widget_uso_solo import WidgetUsoSolo
from .widget_quadrantes import WidgetQuadrantes


class TabValidacao(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.subtabs = QTabWidget()

        self.widget_uso_solo = WidgetUsoSolo(self.mw)
        self.idx_uso_solo = self.subtabs.addTab(
            self.widget_uso_solo, "Uso do Solo"
        )

        self.widget_quadrantes = WidgetQuadrantes(self.mw)
        self.idx_quadrantes = self.subtabs.addTab(
            self.widget_quadrantes, "Outras Bases"
        )

        layout.addWidget(self.subtabs)
