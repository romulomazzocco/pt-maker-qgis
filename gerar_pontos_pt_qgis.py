from math import atan2, ceil, cos, degrees, floor, radians, sin, sqrt

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterNumber,
    QgsWkbTypes,
)


class GerarPontosPT(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    ESPACAMENTO = "ESPACAMENTO"
    DIST_X = "DIST_X"
    DIST_Y = "DIST_Y"
    MODO_ANGULO = "MODO_ANGULO"
    ANGULO_MANUAL = "ANGULO_MANUAL"
    TOLERANCIA_ORTO = "TOLERANCIA_ORTO"
    MARGEM_BORDA = "MARGEM_BORDA"
    OUTPUT = "OUTPUT"
    AMOSTRAS_OFFSET = 8
    REFINOS_OFFSET = 2
    TOL_ANGULO_EQUIVALENTE = 1.0

    def name(self):
        return "gerar_pontos_pt_alinhados"

    def displayName(self):
        return "Gerar pontos PT alinhados em poligono"

    def group(self):
        return "PT Maker"

    def groupId(self):
        return "pt_maker"

    def shortHelpString(self):
        return (
            "Gera uma camada de pontos dentro de um poligono, alinhada ao angulo "
            "de um lado do contorno. O modo automatico prioriza o maior lado que "
            "forma um canto proximo de 90 graus com outro segmento e ajusta o "
            "deslocamento da grade para ocupar melhor a area.\n\n"
            "Campos criados: nome (PT-001...), utm_e, utm_n, epsg_utm, angulo, "
            "dist_x e dist_y.\n\n"
            "Use a margem interna para afastar os pontos do limite do poligono.\n\n"
            "A camada de saida e criada em UTM SIRGAS 2000, escolhida pelo "
            "centroide do primeiro poligono. O script foi pensado para uso no Brasil."
        )

    def createInstance(self):
        return GerarPontosPT()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                "Camada de poligono",
                [QgsProcessing.TypeVectorPolygon],
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.ESPACAMENTO,
                "Espacamento padrao",
                options=["25 x 25", "50 x 50", "Personalizado"],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DIST_X,
                "Distancia X (metros) quando Personalizado",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=25.0,
                minValue=0.001,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DIST_Y,
                "Distancia Y (metros) quando Personalizado",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=25.0,
                minValue=0.001,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.MODO_ANGULO,
                "Modo de alinhamento",
                options=[
                    "Automatico (maior lado quase ortogonal)",
                    "Maior lado do poligono",
                    "Angulo manual",
                ],
                defaultValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.ANGULO_MANUAL,
                "Angulo manual (graus)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TOLERANCIA_ORTO,
                "Tolerancia para canto perto de 90 graus",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=20.0,
                minValue=0.0,
                maxValue=89.999,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MARGEM_BORDA,
                "Margem interna da borda (metros)",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=2.0,
                minValue=0.0,
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUTPUT,
                "Pontos PT",
            )
        )

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException("Nao foi possivel ler a camada de poligono.")

        espacamento = self.parameterAsEnum(parameters, self.ESPACAMENTO, context)
        dist_x, dist_y = self._resolver_espacamento(parameters, context, espacamento)

        modo_angulo = self.parameterAsEnum(parameters, self.MODO_ANGULO, context)
        angulo_manual = self.parameterAsDouble(parameters, self.ANGULO_MANUAL, context)
        tolerancia = self.parameterAsDouble(parameters, self.TOLERANCIA_ORTO, context)
        margem_borda = self.parameterAsDouble(parameters, self.MARGEM_BORDA, context)

        primeiro = next(source.getFeatures(), None)
        if primeiro is None:
            raise QgsProcessingException("A camada de entrada nao possui feicoes.")

        utm_crs, epsg_utm = self._definir_utm_sirgas(
            primeiro.geometry(),
            source.sourceCrs(),
            context,
        )

        to_utm = QgsCoordinateTransform(
            source.sourceCrs(),
            utm_crs,
            context.transformContext(),
        )

        fields = QgsFields()
        fields.append(QgsField("id", QVariant.Int))
        fields.append(QgsField("nome", QVariant.String, len=20))
        fields.append(QgsField("utm_e", QVariant.Double, len=20, prec=3))
        fields.append(QgsField("utm_n", QVariant.Double, len=20, prec=3))
        fields.append(QgsField("epsg_utm", QVariant.Int))
        fields.append(QgsField("angulo", QVariant.Double, len=20, prec=6))
        fields.append(QgsField("dist_x", QVariant.Double, len=20, prec=3))
        fields.append(QgsField("dist_y", QVariant.Double, len=20, prec=3))
        fields.append(QgsField("origem_id", QVariant.String, len=32))

        sink, dest_id = self.parameterAsSink(
            parameters,
            self.OUTPUT,
            context,
            fields,
            QgsWkbTypes.Point,
            utm_crs,
        )
        if sink is None:
            raise QgsProcessingException("Nao foi possivel criar a camada de saida.")

        total = source.featureCount()
        contador = 1

        for indice, feature in enumerate(source.getFeatures(), start=1):
            if feedback.isCanceled():
                break

            geom = feature.geometry()
            if geom is None or geom.isEmpty():
                continue

            geom_utm = QgsGeometry(geom)
            try:
                geom_utm.transform(to_utm)
            except Exception as exc:
                raise QgsProcessingException(
                    "Falha ao reprojetar a feicao para UTM SIRGAS 2000: {}".format(exc)
                )

            geom_area = self._aplicar_margem_interna(geom_utm, margem_borda)

            if modo_angulo == 0:
                solucao = self._escolher_solucao_automatica(
                    geom_utm,
                    geom_area,
                    dist_x,
                    dist_y,
                    tolerancia,
                )
            elif modo_angulo == 1:
                lado = self._escolher_maior_lado(geom_utm)
                solucao = self._otimizar_grade_no_poligono(
                    geom_area,
                    lado["p0"],
                    lado["angle"],
                    dist_x,
                    dist_y,
                )
            else:
                lado = self._escolher_maior_lado(geom_utm)
                solucao = self._otimizar_grade_no_poligono(
                    geom_area,
                    lado["p0"],
                    angulo_manual,
                    dist_x,
                    dist_y,
                )

            angulo = solucao["angle"]
            pontos = solucao["points"]

            angulo_saida = round((angulo + 360.0) % 360.0, 6)

            feedback.pushInfo(
                "Feicao {}: EPSG:{} | angulo {:.4f} graus | offset {:.3f} x {:.3f} | {} pontos".format(
                    feature.id(),
                    epsg_utm,
                    angulo_saida,
                    solucao["offset_x"],
                    solucao["offset_y"],
                    len(pontos),
                )
            )

            for point in pontos:
                nova = QgsFeature(fields)
                nova.setGeometry(QgsGeometry.fromPointXY(point))
                nova["id"] = contador
                nova["nome"] = "PT-{:03d}".format(contador)
                nova["utm_e"] = round(point.x(), 3)
                nova["utm_n"] = round(point.y(), 3)
                nova["epsg_utm"] = epsg_utm
                nova["angulo"] = angulo_saida
                nova["dist_x"] = round(dist_x, 3)
                nova["dist_y"] = round(dist_y, 3)
                nova["origem_id"] = str(feature.id())
                sink.addFeature(nova, QgsFeatureSink.FastInsert)
                contador += 1

            if total and total > 0:
                feedback.setProgress(int((indice / total) * 100))

        return {self.OUTPUT: dest_id}

    def _resolver_espacamento(self, parameters, context, espacamento):
        if espacamento == 0:
            return 25.0, 25.0
        if espacamento == 1:
            return 50.0, 50.0

        dist_x = self.parameterAsDouble(parameters, self.DIST_X, context)
        dist_y = self.parameterAsDouble(parameters, self.DIST_Y, context)
        if dist_x <= 0 or dist_y <= 0:
            raise QgsProcessingException("As distancias X e Y devem ser maiores que zero.")
        return dist_x, dist_y

    def _definir_utm_sirgas(self, geometry, source_crs, context):
        if geometry is None or geometry.isEmpty():
            raise QgsProcessingException("Nao foi possivel calcular o centroide do poligono.")

        sirgas_geo = QgsCoordinateReferenceSystem("EPSG:4674")
        centroide = geometry.centroid()
        try:
            to_geo = QgsCoordinateTransform(
                source_crs,
                sirgas_geo,
                context.transformContext(),
            )
            centroide.transform(to_geo)
        except Exception as exc:
            raise QgsProcessingException(
                "Falha ao converter centroide para SIRGAS 2000 geografico: {}".format(exc)
            )

        ponto = centroide.asPoint()
        lon = ponto.x()
        lat = ponto.y()
        zona = int(floor((lon + 180.0) / 6.0) + 1)

        if zona < 18 or zona > 25:
            raise QgsProcessingException(
                "A zona UTM calculada ({}) esta fora do intervalo esperado para o Brasil.".format(
                    zona
                )
            )

        epsg = 31959 + zona if lat < 0 else 31954 + zona
        utm_crs = QgsCoordinateReferenceSystem("EPSG:{}".format(epsg))
        if not utm_crs.isValid():
            raise QgsProcessingException("Nao foi possivel criar o CRS EPSG:{}.".format(epsg))

        return utm_crs, epsg

    def _escolher_lado_automatico(self, geometry, tolerancia):
        segmentos = self._listar_segmentos(geometry)
        candidatos = [seg for seg in segmentos if seg["deviation"] <= tolerancia]
        if candidatos:
            return max(candidatos, key=lambda seg: seg["length"])
        return max(segmentos, key=lambda seg: seg["length"])

    def _escolher_maior_lado(self, geometry):
        return max(self._listar_segmentos(geometry), key=lambda seg: seg["length"])

    def _escolher_solucao_automatica(self, geometry_base, geometry_area, dist_x, dist_y, tolerancia):
        segmentos = self._listar_segmentos(geometry_base)
        candidatos = [seg for seg in segmentos if seg["deviation"] <= tolerancia]
        if not candidatos:
            candidatos = segmentos

        segmento_preferido = max(candidatos, key=lambda seg: seg["length"])
        melhor = self._otimizar_grade_no_poligono(
            geometry_area,
            segmento_preferido["p0"],
            segmento_preferido["angle"],
            dist_x,
            dist_y,
        )
        melhor["segmento"] = segmento_preferido

        candidatos = self._deduplicar_segmentos_por_angulo(candidatos)[:12]

        for segmento in candidatos:
            if (
                self._diferenca_angular_180(segmento["angle"], segmento_preferido["angle"])
                <= self.TOL_ANGULO_EQUIVALENTE
            ):
                continue

            solucao = self._otimizar_grade_no_poligono(
                geometry_area,
                segmento["p0"],
                segmento["angle"],
                dist_x,
                dist_y,
            )
            solucao["segmento"] = segmento

            if self._alternativa_merece_troca(
                solucao,
                segmento,
                melhor,
                melhor["segmento"],
                dist_x,
                dist_y,
            ):
                melhor = solucao

        return melhor

    def _deduplicar_segmentos_por_angulo(self, segmentos):
        escolhidos = []
        ordenados = sorted(
            segmentos,
            key=lambda seg: (seg["length"], -seg["deviation"]),
            reverse=True,
        )
        for segmento in ordenados:
            angulo = self._normalizar_angulo_180(segmento["angle"])
            repetido = False
            for existente in escolhidos:
                ang_existente = self._normalizar_angulo_180(existente["angle"])
                if (
                    self._diferenca_angular_180(angulo, ang_existente)
                    <= self.TOL_ANGULO_EQUIVALENTE
                ):
                    repetido = True
                    break

            if not repetido:
                escolhidos.append(segmento)

        return escolhidos

    def _listar_segmentos(self, geometry):
        anel = self._extrair_anel_externo(geometry)
        if len(anel) < 3:
            raise QgsProcessingException(
                "O poligono precisa ter pelo menos 3 vertices para gerar os pontos."
            )

        if anel[0] == anel[-1]:
            anel = anel[:-1]

        quantidade = len(anel)
        if quantidade < 3:
            raise QgsProcessingException(
                "O poligono precisa ter pelo menos 3 vertices para gerar os pontos."
            )

        segmentos = []
        for i in range(quantidade):
            p0 = anel[i]
            p1 = anel[(i + 1) % quantidade]
            anterior = anel[(i - 1) % quantidade]
            proximo = anel[(i + 2) % quantidade]

            dx = p1.x() - p0.x()
            dy = p1.y() - p0.y()
            comprimento = sqrt((dx * dx) + (dy * dy))
            if comprimento == 0:
                continue

            angulo = degrees(atan2(dy, dx))
            angulo_ant = degrees(atan2(p0.y() - anterior.y(), p0.x() - anterior.x()))
            angulo_prox = degrees(atan2(proximo.y() - p1.y(), proximo.x() - p1.x()))

            dev_ant = abs(90.0 - self._diferenca_orientacao(angulo, angulo_ant))
            dev_prox = abs(90.0 - self._diferenca_orientacao(angulo, angulo_prox))

            segmentos.append(
                {
                    "p0": QgsPointXY(p0),
                    "p1": QgsPointXY(p1),
                    "length": comprimento,
                    "angle": angulo,
                    "deviation": min(dev_ant, dev_prox),
                }
            )

        if not segmentos:
            raise QgsProcessingException("Nao foi possivel extrair lados validos do poligono.")

        return segmentos

    def _extrair_anel_externo(self, geometry):
        if geometry.isMultipart():
            multi = geometry.asMultiPolygon()
            if not multi:
                raise QgsProcessingException("Geometria multiparte invalida.")

            melhor_anel = None
            maior_area = -1.0
            for poligono in multi:
                if not poligono:
                    continue
                anel = poligono[0]
                area = abs(QgsGeometry.fromPolygonXY([anel]).area())
                if area > maior_area:
                    maior_area = area
                    melhor_anel = anel

            if melhor_anel is None:
                raise QgsProcessingException("Nao foi possivel identificar o anel externo.")
            return melhor_anel

        poligono = geometry.asPolygon()
        if not poligono:
            raise QgsProcessingException("Geometria de poligono invalida.")
        return poligono[0]

    def _diferenca_orientacao(self, a, b):
        diferenca = abs((a - b) % 180.0)
        if diferenca > 90.0:
            diferenca = 180.0 - diferenca
        return diferenca

    def _normalizar_angulo_180(self, angulo):
        valor = angulo % 180.0
        if valor < 0:
            valor += 180.0
        return valor

    def _diferenca_angular_180(self, a, b):
        diferenca = abs((a - b) % 180.0)
        if diferenca > 90.0:
            diferenca = 180.0 - diferenca
        return diferenca

    def _alternativa_merece_troca(
        self,
        nova_solucao,
        novo_segmento,
        atual_solucao,
        atual_segmento,
        dist_x,
        dist_y,
    ):
        ganho_pontos = len(nova_solucao["points"]) - len(atual_solucao["points"])
        ganho_margem = atual_solucao["max_margin"] - nova_solucao["max_margin"]
        ganho_soma = atual_solucao["margin_sum"] - nova_solucao["margin_sum"]
        referencia = min(dist_x, dist_y)

        if ganho_pontos >= 2:
            return True

        if ganho_pontos >= 1 and ganho_margem >= (0.10 * referencia):
            return True

        if ganho_pontos == 0 and ganho_margem >= (0.35 * referencia) and ganho_soma > 0:
            return True

        if (
            ganho_pontos == 0
            and abs(ganho_margem) < (0.05 * referencia)
            and abs(ganho_soma) < (0.10 * referencia)
            and novo_segmento["length"] > atual_segmento["length"]
            and novo_segmento["deviation"] <= atual_segmento["deviation"]
        ):
            return True

        return False

    def _aplicar_margem_interna(self, geometry, margem_borda):
        if margem_borda <= 0:
            return QgsGeometry(geometry)

        geometry_interna = geometry.buffer(-margem_borda, 8)
        if geometry_interna is None or geometry_interna.isEmpty():
            raise QgsProcessingException(
                "A margem interna de {:.3f} m eliminou totalmente a area util do poligono.".format(
                    margem_borda
                )
            )

        return geometry_interna

    def _otimizar_grade_no_poligono(self, geometry, origem, angulo, dist_x, dist_y):
        geom_rot = QgsGeometry(geometry)
        # QgsGeometry.rotate usa sentido horario; isso equivale a rotacionar
        # a geometria por -angulo no sistema trigonometrico padrao.
        geom_rot.rotate(angulo, origem)
        bbox = geom_rot.boundingBox()

        melhor = None
        melhor_chave = None

        for ix in range(self.AMOSTRAS_OFFSET):
            offset_x = (dist_x * ix) / self.AMOSTRAS_OFFSET
            for iy in range(self.AMOSTRAS_OFFSET):
                offset_y = (dist_y * iy) / self.AMOSTRAS_OFFSET
                avaliacao = self._avaliar_offset(
                    geom_rot,
                    bbox,
                    origem,
                    angulo,
                    dist_x,
                    dist_y,
                    offset_x,
                    offset_y,
                )
                chave = self._chave_avaliacao_offset(avaliacao)
                if melhor is None or chave > melhor_chave:
                    melhor = avaliacao
                    melhor_chave = chave

        passo_x = dist_x / self.AMOSTRAS_OFFSET
        passo_y = dist_y / self.AMOSTRAS_OFFSET

        for _ in range(self.REFINOS_OFFSET):
            passo_x /= 4.0
            passo_y /= 4.0

            for dx_mul in range(-2, 3):
                for dy_mul in range(-2, 3):
                    offset_x = (melhor["offset_x"] + (dx_mul * passo_x)) % dist_x
                    offset_y = (melhor["offset_y"] + (dy_mul * passo_y)) % dist_y
                    avaliacao = self._avaliar_offset(
                        geom_rot,
                        bbox,
                        origem,
                        angulo,
                        dist_x,
                        dist_y,
                        offset_x,
                        offset_y,
                    )
                    chave = self._chave_avaliacao_offset(avaliacao)
                    if chave > melhor_chave:
                        melhor = avaliacao
                        melhor_chave = chave

        return melhor

    def _chave_avaliacao_offset(self, avaliacao):
        return (
            len(avaliacao["points"]),
            -avaliacao["max_margin"],
            -avaliacao["margin_sum"],
            avaliacao["spread"],
        )

    def _avaliar_offset(
        self,
        geom_rot,
        bbox,
        origem,
        angulo,
        dist_x,
        dist_y,
        offset_x,
        offset_y,
    ):
        i_min = int(floor((bbox.xMinimum() - origem.x() - offset_x) / dist_x)) - 1
        i_max = int(ceil((bbox.xMaximum() - origem.x() - offset_x) / dist_x)) + 1
        j_min = int(floor((bbox.yMinimum() - origem.y() - offset_y) / dist_y)) - 1
        j_max = int(ceil((bbox.yMaximum() - origem.y() - offset_y) / dist_y)) + 1

        pontos_rot = []

        for j in range(j_max, j_min - 1, -1):
            for i in range(i_min, i_max + 1):
                px = origem.x() + offset_x + (i * dist_x)
                py = origem.y() + offset_y + (j * dist_y)
                ponto_rot = QgsPointXY(px, py)
                if not geom_rot.contains(QgsGeometry.fromPointXY(ponto_rot)):
                    continue

                pontos_rot.append(ponto_rot)

        if pontos_rot:
            xs = [p.x() for p in pontos_rot]
            ys = [p.y() for p in pontos_rot]
            esquerda = min(xs) - bbox.xMinimum()
            direita = bbox.xMaximum() - max(xs)
            inferior = min(ys) - bbox.yMinimum()
            superior = bbox.yMaximum() - max(ys)
            max_margin = max(esquerda, direita, inferior, superior)
            margin_sum = esquerda + direita + inferior + superior
            spread = (max(xs) - min(xs)) + (max(ys) - min(ys))
        else:
            max_margin = float("inf")
            margin_sum = float("inf")
            spread = 0.0

        pontos = [self._rotacionar_ponto(ponto, origem, angulo) for ponto in pontos_rot]

        return {
            "angle": angulo,
            "offset_x": round(offset_x, 6),
            "offset_y": round(offset_y, 6),
            "points": pontos,
            "max_margin": max_margin,
            "margin_sum": margin_sum,
            "spread": spread,
        }

    def _rotacionar_ponto(self, ponto, origem, angulo):
        rad = radians(angulo)
        dx = ponto.x() - origem.x()
        dy = ponto.y() - origem.y()

        x = origem.x() + (dx * cos(rad)) - (dy * sin(rad))
        y = origem.y() + (dx * sin(rad)) + (dy * cos(rad))
        return QgsPointXY(x, y)
