"""
📦 Batch Processor - Processamento em Lote de Shipment IDs
===========================================================
Processa múltiplos Shipment IDs de uma vez e gera relatórios completos.
"""

import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
import oracledb
from database.queries import run_shipaudit_query
from troubleshooter.knowledge_manager import KnowledgeBaseManager


class BatchShipmentProcessor:
    """Processador de múltiplos Shipment IDs"""

    def __init__(self, connection_params: Dict):
        """
        Inicializa o processador

        Args:
            connection_params: Parâmetros de conexão Oracle
                {
                    'user': str,
                    'password': str,
                    'host': str,
                    'port': int,
                    'service_name': str
                }
        """
        self.connection_params = connection_params
        self.kb_manager = KnowledgeBaseManager()

    def process_batch(
        self,
        shipment_ids: List[str],
        match_threshold: float = 0.45,
        include_tariff_query: bool = False
    ) -> Dict:
        """
        Processa lote de Shipment IDs

        Args:
            shipment_ids: Lista de Shipment IDs
            match_threshold: Limite de similaridade para matching
            include_tariff_query: Se deve executar Rate Card Lookup Query

        Returns:
            Dicionário com resultados
        """
        results = {
            'summary': {
                'total_requested': len(shipment_ids),
                'total_found': 0,
                'total_with_errors': 0,
                'total_without_errors': 0,
                'total_not_found': 0,
                'processing_time': None,
                'timestamp': datetime.now().isoformat()
            },
            'shipments': [],
            'error_analysis': [],
            'recommendations': []
        }

        start_time = datetime.now()

        # Conecta ao banco
        try:
            connection = self._get_connection()
            cursor = connection.cursor()

            # Processa cada shipment ID
            for shipment_id in shipment_ids:
                shipment_result = self._process_single_shipment(
                    cursor,
                    shipment_id,
                    match_threshold,
                    include_tariff_query
                )

                results['shipments'].append(shipment_result)

                # Atualiza estatísticas
                if shipment_result['found']:
                    results['summary']['total_found'] += 1
                    if shipment_result['has_error']:
                        results['summary']['total_with_errors'] += 1
                    else:
                        results['summary']['total_without_errors'] += 1
                else:
                    results['summary']['total_not_found'] += 1

            cursor.close()
            connection.close()

        except Exception as e:
            results['summary']['error'] = str(e)
            raise

        # Análise agregada de erros
        results['error_analysis'] = self._analyze_errors(results['shipments'])

        # Recomendações gerais
        results['recommendations'] = self._generate_recommendations(results['error_analysis'])

        # Tempo de processamento
        end_time = datetime.now()
        results['summary']['processing_time'] = (end_time - start_time).total_seconds()

        return results

    def _get_connection(self):
        """Cria conexão Oracle"""
        dsn = oracledb.makedsn(
            self.connection_params['host'],
            self.connection_params['port'],
            service_name=self.connection_params['service_name']
        )

        return oracledb.connect(
            user=self.connection_params['user'],
            password=self.connection_params['password'],
            dsn=dsn
        )

    def _process_single_shipment(
        self,
        cursor,
        shipment_id: str,
        match_threshold: float,
        include_tariff_query: bool
    ) -> Dict:
        """Processa um único Shipment ID"""

        result = {
            'shipment_id': shipment_id,
            'found': False,
            'has_error': False,
            'details': {},
            'error_matches': [],
            'tariff_data': None
        }

        # Query para buscar dados do shipment
        query = """
        SELECT 
            SEQ_NO, SHIPMENT_ID, PLAN_ID, REQUEST_ID, 
            RULES_FILE, TOTAL_ROUTE_SEGMENTS, STATUS, ERR_MSG,
            CRTD_DTT, CREATED_BY, UPDT_DTT, UPDATED_BY,
            SOURCE_FILE_NAME, EMAIL_SENT, ORIGIN, DESTINATION,
            LOGISTICS_GROUP, DIVISION_CODE, CHARGE_OVERRIDE, EQUIPMENT_TYPE_CODE,
            RECORD_STATUS, LOAD_ID, LOAD_CREATED, SHIPMENT_CREATED, SUPPORT_GROUP
        FROM ACME_OMS.DEMO_AUDIT
        WHERE SHIPMENT_ID = :shipment_id
        ORDER BY UPDT_DTT DESC
        """

        try:
            cursor.execute(query, {'shipment_id': shipment_id})
            row = cursor.fetchone()

            if row:
                result['found'] = True

                # Mapeia colunas
                columns = [desc[0] for desc in cursor.description]
                details = dict(zip(columns, row))

                result['details'] = details

                # Verifica se tem erro
                err_msg = details.get('ERR_MSG', '')
                if err_msg and str(err_msg).strip():
                    result['has_error'] = True

                    # Faz matching com knowledge base
                    matches = self.kb_manager.smart_match_errors(
                        [err_msg],
                        threshold=match_threshold
                    )

                    if matches:
                        result['error_matches'] = matches[0]['matches']

                # Tariff Query (se solicitado)
                if include_tariff_query and result['has_error']:
                    # Verifica se algum match recomenda Tariff Query
                    needs_tariff = any(
                        m.get('precisa_tariff_query', '').upper() == 'SIM'
                        for m in result['error_matches']
                    )

                    if needs_tariff:
                        result['tariff_data'] = self._execute_tariff_query(
                            cursor,
                            details.get('ORIGIN'),
                            details.get('DESTINATION'),
                            details.get('EQUIPMENT_TYPE_CODE')
                        )

        except Exception as e:
            result['error'] = str(e)

        return result

    def _execute_tariff_query(
        self,
        cursor,
        origin: str,
        destination: str,
        equipment: str
    ) -> Optional[List[Dict]]:
        """Executa Rate Card Lookup Query"""

        # Query básica (pode ser customizada depois)
        query = """
        SELECT DISTINCT 
            T.RATE_CARD_CODE, T.RATE_CARD_ID, T.EFFECTIVE_DATE, T.EXPIRATION_DATE, T.CARRIER_CODE,
            L.ORIGIN_ZONE_CODE, L.ORIGIN_COUNTRY_CODE, L.DESTINATION_ZONE_CODE, L.DESTINATION_COUNTRY_CODE,
            L.SERVICE_CODE, R.CHARGE_CODE, R.EQUIPMENT_TYPE_CODE,
            R.MINIMUM_CHARGE_AMOUNT, RT.BREAK_AMOUNT, RT.RANGE_CODE,
            C.CURRENCY_CODE, R.EFFECTIVE_DATE AS RATE_EFF, R.EXPIRATION_DATE AS RATE_EXP,
            RT.RANGE_END, L.SERVICE_GRADE, L.COMMODITY_CODE, L.BASE_DIVISION_CODE, R.RATE_CODE
        FROM RTG_APP.DEMO_ROUTE_RATE L
        JOIN RTG_APP.DEMO_RATE_CARD T ON T.RATE_CARD_ID = L.RATE_CARD_ID
        JOIN RTG_APP.DEMO_RATE R ON R.SERVICE_CODE = L.SERVICE_CODE 
            AND R.RATE_CODE = L.RATE_CODE 
            AND R.RATE_CARD_ID = L.RATE_CARD_ID
        JOIN RTG_APP.RNG_rate_T RT ON R.RATE_RECORD_ID = RT.RATE_RECORD_ID
        JOIN RTG_APP.DEMO_CURRENCY C ON C.CURRENCY_TYPE = R.CURRENCY_TYPE
        WHERE T.MASTER_RATE_CARD_ID IN ('90001')
        AND ROWNUM <= 100
        """

        try:
            cursor.execute(query)
            rows = cursor.fetchall()

            if rows:
                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]

        except Exception:
            pass

        return None

    def _analyze_errors(self, shipments: List[Dict]) -> List[Dict]:
        """Análise agregada de erros"""

        error_summary = {}

        for shipment in shipments:
            if shipment['has_error'] and shipment['error_matches']:
                best_match = shipment['error_matches'][0]

                categoria = best_match.get('categoria', 'Uncategorized')

                if categoria not in error_summary:
                    error_summary[categoria] = {
                        'categoria': categoria,
                        'count': 0,
                        'shipments': [],
                        'common_solutions': [],
                        'responsible': set()
                    }

                error_summary[categoria]['count'] += 1
                error_summary[categoria]['shipments'].append(shipment['shipment_id'])

                # Coleta soluções
                acao = best_match.get('acao_recomendada', '')
                if acao:
                    error_summary[categoria]['common_solutions'].append(acao)

                # Coleta responsáveis
                resp = best_match.get('responsavel', '')
                if resp:
                    error_summary[categoria]['responsible'].add(resp)

        # Converte para lista
        analysis = []
        for cat_data in error_summary.values():
            # Pega ação mais comum
            if cat_data['common_solutions']:
                from collections import Counter
                most_common = Counter(cat_data['common_solutions']).most_common(1)[0][0]
                cat_data['recommended_action'] = most_common

            cat_data['responsible'] = list(cat_data['responsible'])

            analysis.append(cat_data)

        # Ordena por quantidade
        analysis.sort(key=lambda x: x['count'], reverse=True)

        return analysis

    def _generate_recommendations(self, error_analysis: List[Dict]) -> List[str]:
        """Gera recomendações gerais baseadas na análise"""

        recommendations = []

        if not error_analysis:
            recommendations.append("✅ Nenhum erro encontrado nos shipments analisados.")
            return recommendations

        # Categoria mais problemática
        top_category = error_analysis[0]
        recommendations.append(
            f"🔴 Categoria mais problemática: {top_category['categoria']} "
            f"({top_category['count']} ocorrências)"
        )

        # Ação prioritária
        if 'recommended_action' in top_category:
            recommendations.append(
                f"⚡ Ação prioritária: {top_category['recommended_action']}"
            )

        # Responsáveis a contatar
        all_responsible = set()
        for cat in error_analysis:
            all_responsible.update(cat.get('responsible', []))

        if all_responsible:
            recommendations.append(
                f"👥 Times a contatar: {', '.join(all_responsible)}"
            )

        # Estatísticas gerais
        total_errors = sum(cat['count'] for cat in error_analysis)
        recommendations.append(
            f"📊 Total de erros: {total_errors} em {len(error_analysis)} categorias"
        )

        return recommendations

    def export_to_excel(
        self,
        results: Dict,
        output_path: str = None
    ) -> str:
        """
        Exporta resultados para Excel com múltiplas abas

        Args:
            results: Resultados do processamento
            output_path: Caminho do arquivo de saída

        Returns:
            Caminho do arquivo gerado
        """
        if output_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = f'batch_report_{timestamp}.xlsx'

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:

            # 1. Resumo
            summary_data = []
            for key, value in results['summary'].items():
                summary_data.append({'Métrica': key, 'Valor': value})
            pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)

            # 2. Detalhes dos Shipments
            shipment_details = []
            for ship in results['shipments']:
                row = {
                    'Shipment ID': ship['shipment_id'],
                    'Found': ship['found'],
                    'Has Error': ship['has_error'],
                    'Error Message': ship['details'].get('ERR_MSG', '') if ship['found'] else '',
                    'Origin': ship['details'].get('ORIGIN', '') if ship['found'] else '',
                    'Destination': ship['details'].get('DESTINATION', '') if ship['found'] else '',
                    'Status': ship['details'].get('STATUS', '') if ship['found'] else '',
                    'Best Match Score': ship['error_matches'][0]['score'] if ship['error_matches'] else 0,
                    'Recommended Action': ship['error_matches'][0]['acao_recomendada'] if ship['error_matches'] else '',
                    'Responsible': ship['error_matches'][0]['responsavel'] if ship['error_matches'] else ''
                }
                shipment_details.append(row)

            pd.DataFrame(shipment_details).to_excel(writer, sheet_name='Shipment Details', index=False)

            # 3. Análise de Erros
            if results['error_analysis']:
                error_data = []
                for err in results['error_analysis']:
                    error_data.append({
                        'Categoria': err['categoria'],
                        'Quantidade': err['count'],
                        'Ação Recomendada': err.get('recommended_action', ''),
                        'Responsáveis': ', '.join(err.get('responsible', []))
                    })
                pd.DataFrame(error_data).to_excel(writer, sheet_name='Error Analysis', index=False)

            # 4. Recomendações
            if results['recommendations']:
                rec_df = pd.DataFrame({
                    'Recomendação': results['recommendations']
                })
                rec_df.to_excel(writer, sheet_name='Recommendations', index=False)

            # 5. Matches Detalhados
            all_matches = []
            for ship in results['shipments']:
                if ship['error_matches']:
                    for match in ship['error_matches']:
                        all_matches.append({
                            'Shipment ID': ship['shipment_id'],
                            'Score': match['score'],
                            'Categoria': match['categoria'],
                            'Padrão': match['padrao'],
                            'Significado': match['significado'],
                            'Como Validar': match['como_validar'],
                            'Ação Recomendada': match['acao_recomendada'],
                            'Responsável': match['responsavel'],
                            'Precisa Tariff Query': match['precisa_tariff_query']
                        })

            if all_matches:
                pd.DataFrame(all_matches).to_excel(writer, sheet_name='All Matches', index=False)

        return output_path

