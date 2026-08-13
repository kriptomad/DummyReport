![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)

# DummyReport

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)

DummyReport é uma plataforma multi-app em Streamlit para troubleshooting de banco de dados Oracle, geração de relatórios assistida por IA e operações internas de suporte. Foi originalmente construída como uma ferramenta interna de suporte e é publicada aqui como uma demo de portfólio sanitizada, com dados totalmente fictícios.

Referências específicas da empresa, infraestrutura real, credenciais e documentos de negócio foram removidos e substituídos por dados fictícios de demonstração. Todas as funcionalidades abaixo funcionam com o conjunto de dados mock incluído — não é necessária nenhuma conexão real com banco de dados para explorar o app. Veja a seção Aviso no final para detalhes sobre o que foi sanitizado.

## O que faz

O DummyReport simula um portal de suporte/operações para um time que investiga falhas em transações de um sistema de logística apoiado em Oracle. Está dividido em três apps Streamlit que cooperam entre si e compartilham a mesma camada de autenticação/sessão:

| App | Porta | Finalidade |
|---|---|---|
| `portal_app.py` | 8500 | Portal central de login — direciona o usuário autenticado para qual dos dois apps abaixo ele tem acesso, além de um dashboard de administração unificado |
| `app.py` | 8501 | Console principal de troubleshooting: geração de relatórios, triagem de erros, construção de consultas com IA, base de conhecimento e revisão de correções autônomas |
| `psld_app.py` | 8502 | Um segundo workspace, com identidade visual própria, para um time interno diferente, reaproveitando o mesmo motor com sua própria base de conhecimento, fila de tickets e pipeline de aprendizado |

Os dois apps rodam sobre o mesmo motor de troubleshooting/IA (`troubleshooter/`, `ai/`, `database/`), então melhorias em correspondência de erros, aprendizado ou geração de consultas beneficiam ambos.

## Principais funcionalidades

Autenticação e administração
- Autenticação por usuário/senha com hash PBKDF2, par de chaves RSA por usuário para mensagens internas criptografadas de ponta a ponta, e um mecanismo de custódia de chave para que um reset de senha feito por admin nunca destrua o histórico de mensagens do usuário
- Autocadastro com fila de aprovação por admin, flags de papel (admin, suporte, conta de aplicação/negócio, revisor de peças) e controle de acesso por tela
- Armazenamento de sessão compartilhado entre processos (baseado em cookie), permitindo que um único login funcione nos três apps
- Log de auditoria cobrindo logins, resets de senha, ações administrativas e falhas de integração/filas, com categorização e nível de severidade

Troubleshooting e relatórios
- Construtor de consultas em linguagem natural: converte perguntas em português/inglês em SQL validado e somente leitura
- Correspondência de erros por fuzzy matching/TF-IDF contra uma base de conhecimento de problemas e soluções já conhecidos, tolerante a erros quase idênticos que só mudam um ID, data ou local
- Modo de troubleshooting em lote: cole vários IDs de shipment/pedido e receba um relatório consolidado
- Explorador de schema, glossário de SQL e um construtor visual guiado de consultas para quem não conhece SQL
- Ingestão de base de conhecimento via Excel/CSV/PDF/DOCX, com padronização automática de colunas e backups versionados

Correção autônoma (resolução assistida por IA)
- Um pipeline de aprendizado contínuo que estuda erros históricos e suas correções, aprende a reconhecer padrões de falha (não apenas strings exatas) e propõe correções para novos erros semelhantes
- As correções propostas ficam em uma fila de aprovação pendente — nada é aplicado automaticamente, um usuário de suporte/admin precisa revisar e aprovar cada uma
- Um centro de controle de IA no painel administrativo para disparar e acompanhar execuções de treinamento, com retorno visual de progresso

Mensageria interna e presença
- Mensagens criptografadas de ponta a ponta entre usuários (esquema híbrido RSA + AES-GCM), mantendo o corpo das mensagens ilegível em repouso
- Indicadores de presença online/última vez visto, links diretos estilo Teams e avisos em broadcast enviados por admins

## Tecnologias utilizadas

| Camada | Stack |
|---|---|
| UI / framework do app | Streamlit, multi-página, temas customizados (claro/escuro, paletas seguras para daltonismo) |
| Banco de dados | Oracle via `oracledb` — execução de consultas somente leitura, introspecção de schema, perfis de conexão |
| Manipulação de dados | pandas, openpyxl, xlrd (formato .xls legado), python-docx, pypdf, mammoth (visualizador DOCX para HTML) |
| Machine learning | scikit-learn (TF-IDF, clustering) e rapidfuzz para correspondência aproximada de strings; estado treinado persistido com joblib |
| Integrações de IA / LLM | OpenAI, Anthropic, Google Gemini, GitHub Copilot SDK — provedores plugáveis para texto-para-SQL e chat |
| Autenticação e criptografia | hashlib/secrets da stdlib para hash PBKDF2 de senha, `cryptography` para criptografia de mensagens RSA-2048 + AES-256-GCM e custódia de chave via Fernet |
| Automação (experimental) | playwright (experimento de captura de sessão de navegador), msal (prova de conceito de SSO via Azure AD/Entra ID) |
| Relatórios | plotly para gráficos interativos, exportadores customizados em `reports/` |
| Persistência | arquivos JSON simples com file locking (filelock) — não é necessário um servidor de banco externo para rodar o app em si; o Oracle é apenas a fonte de dados inspecionada |

## Configuração

```bash
pip install -r requirements.txt
streamlit run app.py            # console principal de troubleshooting (porta 8501)
streamlit run psld_app.py        # segundo workspace (porta 8502)
streamlit run portal_app.py      # portal de login unificado (porta 8500)
```

Não é necessária nenhuma conexão real com banco de dados para logar, navegar pela base de conhecimento ou explorar o painel administrativo — os arquivos `data/*.json` incluídos fornecem um conjunto de dados mock pequeno e funcional. Conectar a uma instância Oracle real é opcional e configurado pela própria interface.

### Logins de demonstração

| CWS / usuário | Senha | Papel |
|---|---|---|
| `demo_admin` | `DemoPass123!` | Administrador |
| `demo_support` | `DemoPass123!` | Suporte (pode aprovar correções autônomas) |
| `demo_user` | `DemoPass123!` | Usuário comum |

## Estrutura do projeto

```
app.py                 # Console principal de troubleshooting
portal_app.py          # Portal de login unificado / roteador
psld_app.py            # Segundo workspace independente
auth/                  # Login, sessões, papéis, mensagens criptografadas, log de auditoria
ai/                     # Catálogo de schema, texto-para-SQL, análise estilo Copilot
troubleshooter/        # Motor de correspondência, aprendizado contínuo, correção autônoma, KB
database/              # Conexão Oracle, execução de consultas, introspecção de schema
ui/                     # Componentes de abas do Streamlit (admin, KB, construtor de consultas, etc.)
i18n/                   # Textos de tradução PT/EN
config/                 # Configurações do app e padrões de conexão com o banco
integrations/          # Experimentos de SSO/captura de sessão do ServiceNow
reports/                # Processamento em lote e exportação
data/                   # Dados mock/demo em JSON (seguro para inspecionar/resetar)
```

## Aviso

Esta é uma demo de portfólio sanitizada e anonimizada, derivada de uma ferramenta interna privada. Não tem qualquer vínculo com nenhum empregador específico. Tudo neste repositório — contas de usuário, entradas da base de conhecimento, histórico de shipments/erros, hostnames e dados de exemplo — é fictício e gerado apenas para fins de demonstração. Nomes reais de empresas, logotipos, credenciais, hostnames internos, nomes de funcionários e documentos de negócio proprietários do projeto original foram removidos e substituídos por equivalentes fictícios antes da publicação.
