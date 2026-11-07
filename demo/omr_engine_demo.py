#!/usr/bin/env python3
"""
SAGGAZ - Optical Mark Recognition (OMR) & Computer Vision Engine
================================================================
Módulo de Visão Computacional para leitura e correção automatizada de cartões-resposta físicos.

Pipeline de Processamento:
1. Leitura de imagem & pré-processamento (escala de cinza, redução de ruído).
2. Decodificação de QR Code com tolerância a rotações arbitrárias (0°, 90°, 180°, 270°).
3. Detecção de marcadores fiduciais nos 4 cantos da folha via binarização de Otsu e contornos.
4. Transformação de perspectiva afim/homografia (desinclinação e normalização geométrica).
5. Extração de matriz de bolhas (OMR) e cálculo de preenchimento (fill ratio).
6. Serialização das respostas extraídas em JSON estruturado de alta fidelidade.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

try:
    import cv2
    import numpy as np
except ImportError:
    print(json.dumps({
        "sucesso": False,
        "erro": "Bibliotecas opencv-python e numpy são necessárias para rodar este motor de visão."
    }, indent=2, ensure_ascii=False))
    sys.exit(1)


class OMRVisionEngine:
    """Motor de visão computacional responsável pelo alinhamento e leitura óptica."""

    def __init__(self, debug_mode: bool = False):
        self.debug_mode = debug_mode
        self.qr_detector = cv2.QRCodeDetector()

    def decodificar_qr_robusto(self, imagem_bgr: np.ndarray) -> tuple[str | None, np.ndarray, int]:
        """Tenta decodificar o QR Code em múltiplos ângulos de orientação."""
        rotacoes = [
            (0, None),
            (90, cv2.ROTATE_90_CLOCKWISE),
            (180, cv2.ROTATE_180),
            (270, cv2.ROTATE_90_COUNTERCLOCKWISE),
        ]

        for angulo, codigo in rotacoes:
            img = imagem_bgr if codigo is None else cv2.rotate(imagem_bgr, codigo)
            h, w = img.shape[:2]
            escalas = [1.0]
            if max(h, w) > 2000:
                escalas.append(1500.0 / max(h, w))

            for escala in escalas:
                redim = img if escala == 1.0 else cv2.resize(img, (int(w * escala), int(h * escala)))
                conteudo, _, _ = self.qr_detector.detectAndDecode(redim)
                if conteudo and len(conteudo.strip()) > 0:
                    return conteudo.strip(), img, angulo

        return None, imagem_bgr, 0

    def detectar_cantos_fiduciais(self, imagem_bgr: np.ndarray) -> np.ndarray | None:
        """Localiza os 4 marcadores quadrados fiduciais e retorna os vértices ordenados."""
        gray = cv2.cvtColor(imagem_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        contornos, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        area_total = h * w
        candidatos = []

        for c in contornos:
            area = cv2.contourArea(c)
            # Marcador fiducial calibrado para ocupar entre 0.05% e 1.5% da folha A4
            if not (0.0005 * area_total <= area <= 0.015 * area_total):
                continue
            peri = cv2.arcLength(c, True)
            aprox = cv2.approxPolyDP(c, 0.04 * peri, True)
            if len(aprox) == 4 and cv2.isContourConvex(aprox):
                x, y, cw, ch = cv2.boundingRect(aprox)
                aspect_ratio = float(cw) / ch
                if 0.75 <= aspect_ratio <= 1.25:
                    m = cv2.moments(aprox)
                    if m["m00"] > 0:
                        candidatos.append((m["m10"] / m["m00"], m["m01"] / m["m00"]))

        if len(candidatos) < 4:
            return None

        # Ordenar os 4 cantos mais extremos: [Top-Left, Top-Right, Bottom-Right, Bottom-Left]
        candidatos = sorted(candidatos, key=lambda p: p[0] + p[1])
        top_left = candidatos[0]
        bottom_right = candidatos[-1]

        restantes = candidatos[1:-1]
        restantes = sorted(restantes, key=lambda p: p[0] - p[1])
        bottom_left = restantes[0]
        top_right = restantes[-1]

        return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)

    def corrigir_perspectiva(self, imagem_bgr: np.ndarray, cantos: np.ndarray, largura: int = 1654, altura: int = 2339) -> np.ndarray:
        """Aplica warpPerspective para desinclinar e normalizar a geometria da página A4."""
        destino = np.array([
            [0, 0],
            [largura - 1, 0],
            [largura - 1, altura - 1],
            [0, altura - 1]
        ], dtype=np.float32)

        matriz = cv2.getPerspectiveTransform(cantos, destino)
        return cv2.warpPerspective(imagem_bgr, matriz, (largura, altura))

    def processar_folha(self, caminho_imagem: str) -> dict[str, Any]:
        """Executa o pipeline completo sobre uma imagem de entrada."""
        imagem = cv2.imread(caminho_imagem)
        if imagem is None:
            return {"sucesso": False, "erro": f"Não foi possível carregar a imagem: {caminho_imagem}"}

        # 1. Decodificar QR Code com compensação de ângulo
        token, img_orientada, angulo = self.decodificar_qr_robusto(imagem)
        if not token:
            return {
                "sucesso": False,
                "erro": "QR Code identificador da avaliação não foi detectado com precisão suficiente."
            }

        # 2. Localizar marcadores fiduciais
        cantos = self.detectar_cantos_fiduciais(img_orientada)
        if cantos is None:
            return {
                "sucesso": False,
                "erro": "Marcadores fiduciais de canto não encontrados para calibragem geométrica."
            }

        # 3. Retificação de perspectiva
        folha_alinhada = self.corrigir_perspectiva(img_orientada, cantos)

        # 4. Retorno estruturado (amostra de integração)
        return {
            "sucesso": True,
            "token_avaliacao": token,
            "angulo_compensado": angulo,
            "geometria_normalizada": {
                "largura_px": folha_alinhada.shape[1],
                "altura_px": folha_alinhada.shape[0]
            },
            "status": "Folha alinhada e pronta para amostragem de densidade das bolhas OMR"
        }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python omr_engine_demo.py <caminho_para_imagem>")
        sys.exit(0)

    motor = OMRVisionEngine(debug_mode=True)
    resultado = motor.processar_folha(sys.argv[1])
    print(json.dumps(resultado, indent=2, ensure_ascii=False))
