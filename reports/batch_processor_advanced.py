"""
📦 Batch Processor - Processamento em Lote de Shipments
========================================================
Sistema de processamento paralelo de múltiplos shipment IDs com geração de relatórios.
"""

import pandas as pd
from typing import List, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time
from pathlib import Path


class BatchProcessor:
    """Processador em lote de shipments"""

    def __init__(self, max_workers: int = 5):
        """
        Args:
            max_workers: Número máximo de threads paralelas
        """
        self.max_workers = max_workers
        self.results = []
        self.errors = []

    def process_shipments(self,
                         shipment_ids: List[str],
                         query_fn: Callable,
                         troubleshoot_fn: Callable = None,
                         progress_callback: Callable = None) -> Dict:
        """
        Processa lista de shipment IDs em paralelo

        Args:
            shipment_ids: Lista de IDs para processar
            query_fn: Função para executar query (deve aceitar shipment_id)
            troubleshoot_fn: Função opcional para troubleshooting
            progress_callback: Callback para atualizar progresso

        Returns:
            Dicionário com resultados agregados
        """
        start_time = time.time()

        self.results = []
        self.errors = []

        total = len(shipment_ids)
        processed = 0

        # Processa em paralelo
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submete todas as tarefas
            future_to_id = {
                executor.submit(self._process_single, ship_id, query_fn, troubleshoot_fn): ship_id
                for ship_id in shipment_ids
            }

            # Coleta resultados conforme completam
            for future in as_completed(future_to_id):
                ship_id = future_to_id[future]
                processed += 1

                try:
                    result = future.result()
                    if result['success']:
                        self.results.append(result)
                    else:
                        self.errors.append(result)

                except Exception as exc:
                    self.errors.append({
                        'shipment_id': ship_id,
                        'success': False,
                        'error': str(exc)
                    })

                # Callback de progresso
                if progress_callback:
                    progress_callback(processed, total, ship_id)

        elapsed_time = time.time() - start_time

        return {
            'total_processed': total,
            'successful': len(self.results),
            'failed': len(self.errors),
            'elapsed_time': elapsed_time,
            'results': self.results,
            'errors': self.errors
        }

    def _process_single(self,
                       shipment_id: str,
                       query_fn: Callable,
                       troubleshoot_fn: Callable = None) -> Dict:
        """
        Processa um único shipment

        Args:
            shipment_id: ID do shipment
            query_fn: Função de query
            troubleshoot_fn: Função de troubleshooting

        Returns:
            Dicionário com resultado
        """
        try:
            # Executa query
            df = query_fn(shipment_id=shipment_id)

            if df.empty:
                return {
                    'shipment_id': shipment_id,
                    'success': True,
                    'data': None,
                    'error_analysis': None,
                    'message': 'No data found'
                }

            # Análise de erro (se disponível)
            error_analysis = None
            if troubleshoot_fn and 'ERR_MSG' in df.columns:
                error_analysis = troubleshoot_fn(df['ERR_MSG'])

            return {
                'shipment_id': shipment_id,
                'success': True,
                'data': df.to_dict('records'),
                'error_analysis': error_analysis,
                'record_count': len(df),
                'has_errors': df['ERR_MSG'].notna().any() if 'ERR_MSG' in df.columns else False
            }

        except Exception as e:
            return {
                'shipment_id': shipment_id,
                'success': False,
                'error': str(e)
            }

    def generate_summary_report(self) -> pd.DataFrame:
        """
        Gera relatório resumido dos resultados

        Returns:
            DataFrame com resumo
        """
        summary_data = []

        for result in self.results:
            ship_id = result['shipment_id']
            record_count = result.get('record_count', 0)
            has_errors = result.get('has_errors', False)

            # Extrai informações de erro
            error_count = 0
            error_categories = []
            recommended_actions = []

            if result.get('error_analysis'):
                for error_item in result['error_analysis']:
                    error_count += 1
                    if error_item.get('category'):
                        error_categories.append(error_item['category'])

                    if error_item.get('matches') and len(error_item['matches']) > 0:
                        action = error_item['matches'][0].get('Ação recomendada', '')
                        if action:
                            recommended_actions.append(action)

            summary_data.append({
                'Shipment ID': ship_id,
                'Record Count': record_count,
                'Has Errors': 'Yes' if has_errors else 'No',
                'Error Count': error_count,
                'Error Categories': ', '.join(set(error_categories)) if error_categories else '-',
                'Top Recommended Action': recommended_actions[0] if recommended_actions else '-',
                'Status': 'Processed'
            })

        # Adiciona erros
        for error in self.errors:
            summary_data.append({
                'Shipment ID': error['shipment_id'],
                'Record Count': 0,
                'Has Errors': 'N/A',
                'Error Count': 0,
                'Error Categories': '-',
                'Top Recommended Action': '-',
                'Status': f"Failed: {error.get('error', 'Unknown error')}"
            })

        return pd.DataFrame(summary_data)

    def generate_detailed_report(self) -> Dict[str, pd.DataFrame]:
        """
        Gera relatório detalhado com múltiplas abas

        Returns:
            Dicionário de DataFrames (nome_aba -> DataFrame)
        """
        sheets = {}

        # 1. Summary
        sheets['Summary'] = self.generate_summary_report()

        # 2. All Records (dados brutos)
        all_records = []
        for result in self.results:
            if result.get('data'):
                for record in result['data']:
                    record['_source_shipment'] = result['shipment_id']
                    all_records.append(record)

        if all_records:
            sheets['All Records'] = pd.DataFrame(all_records)

        # 3. Error Analysis
        error_analysis_data = []
        for result in self.results:
            if result.get('error_analysis'):
                for error_item in result['error_analysis']:
                    err_msg = error_item.get('err_msg', '')
                    category = error_item.get('category', '')

                    matches = error_item.get('matches', [])
                    if matches:
                        top_match = matches[0]
                        error_analysis_data.append({
                            'Shipment ID': result['shipment_id'],
                            'Error Message': err_msg,
                            'Category': category,
                            'Meaning': top_match.get('Significado provável', ''),
                            'How to Validate': top_match.get('Como validar', ''),
                            'Recommended Action': top_match.get('Ação recomendada', ''),
                            'Responsible': top_match.get('Responsável sugerido', ''),
                            'Match Score': top_match.get('_match_score', 0),
                            'Needs Tariff': 'Yes' if error_item.get('needs_tariff') else 'No'
                        })

        if error_analysis_data:
            sheets['Error Analysis'] = pd.DataFrame(error_analysis_data)

        # 4. Category Breakdown
        if error_analysis_data:
            df_errors = pd.DataFrame(error_analysis_data)
            category_counts = df_errors['Category'].value_counts().reset_index()
            category_counts.columns = ['Category', 'Count']
            sheets['Category Breakdown'] = category_counts

        # 5. Failed Shipments
        if self.errors:
            sheets['Failed Shipments'] = pd.DataFrame(self.errors)

        return sheets

    def export_to_excel(self, output_path: str, detailed: bool = True):
        """
        Exporta resultados para Excel

        Args:
            output_path: Caminho do arquivo de saída
            detailed: Se True, inclui todas as abas detalhadas
        """
        if detailed:
            sheets = self.generate_detailed_report()
        else:
            sheets = {'Summary': self.generate_summary_report()}

        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            for sheet_name, df in sheets.items():
                df.to_excel(writer, sheet_name=sheet_name, index=False)

    def get_statistics(self) -> Dict:
        """
        Retorna estatísticas do processamento

        Returns:
            Dicionário com estatísticas
        """
        total_errors = 0
        error_categories = []
        shipments_with_errors = 0

        for result in self.results:
            if result.get('has_errors'):
                shipments_with_errors += 1

            if result.get('error_analysis'):
                total_errors += len(result['error_analysis'])
                for error_item in result['error_analysis']:
                    if error_item.get('category'):
                        error_categories.append(error_item['category'])

        return {
            'total_shipments': len(self.results) + len(self.errors),
            'successful_queries': len(self.results),
            'failed_queries': len(self.errors),
            'shipments_with_errors': shipments_with_errors,
            'total_errors_found': total_errors,
            'unique_error_categories': len(set(error_categories)),
            'category_distribution': pd.Series(error_categories).value_counts().to_dict() if error_categories else {}
        }


def parse_shipment_list(input_text: str) -> List[str]:
    """
    Parse texto com shipment IDs (comma/newline separated)

    Args:
        input_text: Texto com IDs

    Returns:
        Lista de IDs limpos
    """
    import re

    # Remove espaços extras e quebras de linha
    input_text = input_text.strip()

    # Split por vírgula, newline, tab, etc
    ids = re.split(r'[,\s\n\t;]+', input_text)

    # Remove vazios e limpa
    ids = [id.strip() for id in ids if id.strip()]

    return ids

