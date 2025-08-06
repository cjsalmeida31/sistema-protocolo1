import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, date
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.units import inch
from io import BytesIO
import tempfile
import hashlib
import os
import plotly.express as px
import plotly.graph_objects as go
import json

# Configuração da página
st.set_page_config(
    page_title="Sistema de Protocolo de Documentos",
    page_icon="📋",
    layout="wide"
)

# Classe para gerenciar logs do sistema
class LogManager:
    def __init__(self, db_manager):
        self.db = db_manager
    
    def registrar_log(self, usuario_id, acao, tabela_afetada, registro_id=None, 
                     detalhes=None, status='sucesso', ip_address=None, user_agent=None):
        """Registra uma ação no log do sistema"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        # Converter detalhes para JSON se for um dicionário
        if isinstance(detalhes, dict):
            detalhes = json.dumps(detalhes, ensure_ascii=False)
        
        cursor.execute('''
            INSERT INTO logs_usuario (usuario_id, acao, tabela_afetada, registro_id,
                                    detalhes, ip_address, user_agent, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (usuario_id, acao, tabela_afetada, registro_id, detalhes, 
              ip_address, user_agent, status))
        conn.commit()
        conn.close()
    
    def buscar_logs(self, filtro_usuario=None, filtro_tabela=None, filtro_acao=None,
                   data_inicio=None, data_fim=None, limite=100):
        """Busca logs do sistema com filtros"""
        conn = self.db.get_connection()
        
        query = '''
            SELECT l.*, u.nome as usuario_nome
            FROM logs_usuario l
            LEFT JOIN usuarios u ON l.usuario_id = u.id
            WHERE 1=1
        '''
        params = []
        
        if filtro_usuario:
            query += " AND l.usuario_id = ?"
            params.append(filtro_usuario)
        
        if filtro_tabela:
            query += " AND l.tabela_afetada = ?"
            params.append(filtro_tabela)
        
        if filtro_acao:
            query += " AND l.acao = ?"
            params.append(filtro_acao)
        
        if data_inicio:
            query += " AND DATE(l.timestamp) >= ?"
            params.append(data_inicio)
        
        if data_fim:
            query += " AND DATE(l.timestamp) <= ?"
            params.append(data_fim)
        
        query += f" ORDER BY l.timestamp DESC LIMIT {limite}"
        
        df = pd.read_sql_query(query, conn, params=params)
        conn.close()
        return df
    
    def estatisticas_logs(self):
        """Retorna estatísticas dos logs"""
        conn = self.db.get_connection()
        
        # Total de ações por tipo
        df_acoes = pd.read_sql_query('''
            SELECT acao, COUNT(*) as total
            FROM logs_usuario
            GROUP BY acao
            ORDER BY total DESC
        ''', conn)
        
        # Ações por usuário
        df_usuarios = pd.read_sql_query('''
            SELECT u.nome, COUNT(*) as total_acoes
            FROM logs_usuario l
            LEFT JOIN usuarios u ON l.usuario_id = u.id
            GROUP BY l.usuario_id, u.nome
            ORDER BY total_acoes DESC
        ''', conn)
        
        # Ações por dia (últimos 30 dias)
        df_diario = pd.read_sql_query('''
            SELECT DATE(timestamp) as data, COUNT(*) as total
            FROM logs_usuario
            WHERE timestamp >= datetime('now', '-30 days')
            GROUP BY DATE(timestamp)
            ORDER BY data
        ''', conn)
        
        conn.close()
        return df_acoes, df_usuarios, df_diario

# Classe para gerenciar o banco de dados
class DatabaseManager:
    def __init__(self, db_name="protocolo_documentos.db"):
        self.db_name = db_name
        self.init_database()
    
    def get_connection(self):
        return sqlite3.connect(self.db_name)
    
    def init_database(self):
        """Cria as tabelas se não existirem"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Tabela de Usuários
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT UNIQUE NOT NULL,
                senha TEXT NOT NULL,
                nome TEXT NOT NULL,
                email TEXT,
                nivel_acesso TEXT DEFAULT 'usuario',
                ativo BOOLEAN DEFAULT 1,
                data_cadastro DATETIME DEFAULT CURRENT_TIMESTAMP,
                ultimo_login DATETIME
            )
        ''')
        
        # Tabela de Solicitantes
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS solicitantes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                email TEXT,
                telefone TEXT,
                departamento TEXT,
                data_cadastro DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Tabela de Protocolos
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS protocolos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero_protocolo TEXT UNIQUE NOT NULL,
                titulo TEXT NOT NULL,
                descricao TEXT,
                tipo_documento TEXT,
                status TEXT DEFAULT 'Pendente',
                data_protocolo DATE NOT NULL,
                data_prazo DATE,
                solicitante_id INTEGER,
                observacoes TEXT,
                criado_por INTEGER,
                data_criacao DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (solicitante_id) REFERENCES solicitantes(id),
                FOREIGN KEY (criado_por) REFERENCES usuarios(id)
            )
        ''')
        
        # Tabela de Logs do Sistema
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs_usuario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                acao TEXT NOT NULL,
                tabela_afetada TEXT NOT NULL,
                registro_id INTEGER,
                detalhes TEXT,
                ip_address TEXT,
                user_agent TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'sucesso',
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
            )
        ''')
        
        # Criar usuário administrador padrão se não existir
        cursor.execute("SELECT COUNT(*) FROM usuarios WHERE nivel_acesso = 'admin'")
        if cursor.fetchone()[0] == 0:
            senha_hash = self.hash_senha("admin123")
            cursor.execute('''
                INSERT INTO usuarios (usuario, senha, nome, nivel_acesso)
                VALUES (?, ?, ?, ?)
            ''', ("admin", senha_hash, "Administrador", "admin"))
        
        conn.commit()
        conn.close()
    
    def hash_senha(self, senha):
        """Cria hash da senha"""
        return hashlib.sha256(senha.encode()).hexdigest()
    
    def gerar_numero_protocolo(self):
        """Gera um número de protocolo único"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM protocolos")
        count = cursor.fetchone()[0]
        conn.close()
        
        ano = datetime.now().year
        return f"PROT-{ano}-{count + 1:04d}"

# Classe para autenticação
class AuthManager:
    def __init__(self, db_manager, log_manager):
        self.db = db_manager
        self.log = log_manager
    
    def login(self, usuario, senha):
        """Autentica usuário"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        senha_hash = self.db.hash_senha(senha)
        
        cursor.execute('''
            SELECT id, usuario, nome, nivel_acesso, ativo 
            FROM usuarios 
            WHERE usuario = ? AND senha = ? AND ativo = 1
        ''', (usuario, senha_hash))
        
        user = cursor.fetchone()
        
        if user:
            # Atualizar último login
            cursor.execute('''
                UPDATE usuarios SET ultimo_login = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (user[0],))
            conn.commit()
            
            # Registrar log de login
            self.log.registrar_log(
                usuario_id=user[0],
                acao='LOGIN',
                tabela_afetada='usuarios',
                registro_id=user[0],
                detalhes=f"Login realizado com sucesso para usuário: {user[1]}"
            )
        else:
            # Registrar tentativa de login falhada
            self.log.registrar_log(
                usuario_id=None,
                acao='LOGIN_FALHOU',
                tabela_afetada='usuarios',
                detalhes=f"Tentativa de login falhada para usuário: {usuario}",
                status='erro'
            )
        
        conn.close()
        return user
    
    def criar_usuario(self, usuario, senha, nome, email, nivel_acesso, criado_por_id):
        """Cria novo usuário"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        senha_hash = self.db.hash_senha(senha)
        
        try:
            cursor.execute('''
                INSERT INTO usuarios (usuario, senha, nome, email, nivel_acesso)
                VALUES (?, ?, ?, ?, ?)
            ''', (usuario, senha_hash, nome, email, nivel_acesso))
            
            novo_usuario_id = cursor.lastrowid
            conn.commit()
            
            # Registrar log de criação
            self.log.registrar_log(
                usuario_id=criado_por_id,
                acao='CRIAR',
                tabela_afetada='usuarios',
                registro_id=novo_usuario_id,
                detalhes={
                    'usuario_criado': usuario,
                    'nome': nome,
                    'email': email,
                    'nivel_acesso': nivel_acesso
                }
            )
            
            conn.close()
            return True
        except sqlite3.IntegrityError:
            # Registrar log de erro
            self.log.registrar_log(
                usuario_id=criado_por_id,
                acao='CRIAR_USUARIO_ERRO',
                tabela_afetada='usuarios',
                detalhes=f"Erro ao criar usuário: {usuario} - Usuário já existe",
                status='erro'
            )
            conn.close()
            return False
    
    def listar_usuarios(self):
        """Lista todos os usuários"""
        conn = self.db.get_connection()
        df = pd.read_sql_query('''
            SELECT id, usuario, nome, email, nivel_acesso, ativo, 
                   data_cadastro, ultimo_login
            FROM usuarios ORDER BY nome
        ''', conn)
        conn.close()
        return df
    
    def atualizar_usuario(self, id, nome, email, nivel_acesso, ativo, atualizado_por_id):
        """Atualiza dados do usuário"""
        # Buscar dados antigos para o log
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM usuarios WHERE id=?', (id,))
        dados_antigos = cursor.fetchone()
        
        cursor.execute('''
            UPDATE usuarios 
            SET nome=?, email=?, nivel_acesso=?, ativo=?
            WHERE id=?
        ''', (nome, email, nivel_acesso, ativo, id))
        conn.commit()
        
        # Registrar log de atualização
        self.log.registrar_log(
            usuario_id=atualizado_por_id,
            acao='ATUALIZAR',
            tabela_afetada='usuarios',
            registro_id=id,
            detalhes={
                'campos_alterados': {
                    'nome': {'antes': dados_antigos[3], 'depois': nome},
                    'email': {'antes': dados_antigos[4], 'depois': email},
                    'nivel_acesso': {'antes': dados_antigos[5], 'depois': nivel_acesso},
                    'ativo': {'antes': dados_antigos[6], 'depois': ativo}
                }
            }
        )
        
        conn.close()
        return True
    
    def alterar_senha(self, id, nova_senha, alterado_por_id):
        """Altera senha do usuário"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        senha_hash = self.db.hash_senha(nova_senha)
        cursor.execute('UPDATE usuarios SET senha=? WHERE id=?', (senha_hash, id))
        conn.commit()
        
        # Registrar log de alteração de senha
        self.log.registrar_log(
            usuario_id=alterado_por_id,
            acao='ALTERAR_SENHA',
            tabela_afetada='usuarios',
            registro_id=id,
            detalhes=f"Senha alterada para usuário ID: {id}"
        )
        
        conn.close()
        return True
    
    def deletar_usuario(self, id, deletado_por_id):
        """Deleta usuário"""
        # Buscar dados do usuário antes de deletar
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT usuario, nome FROM usuarios WHERE id=?', (id,))
        dados_usuario = cursor.fetchone()
        
        cursor.execute("DELETE FROM usuarios WHERE id=?", (id,))
        conn.commit()
        
        # Registrar log de exclusão
        self.log.registrar_log(
            usuario_id=deletado_por_id,
            acao='DELETAR',
            tabela_afetada='usuarios',
            registro_id=id,
            detalhes={
                'usuario_deletado': dados_usuario[0],
                'nome_deletado': dados_usuario[1]
            }
        )
        
        conn.close()
        return True

# CRUD para Solicitantes
class SolicitantesCRUD:
    def __init__(self, db_manager, log_manager):
        self.db = db_manager
        self.log = log_manager
    
    def criar(self, nome, email, telefone, departamento, criado_por_id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO solicitantes (nome, email, telefone, departamento)
            VALUES (?, ?, ?, ?)
        ''', (nome, email, telefone, departamento))
        
        solicitante_id = cursor.lastrowid
        conn.commit()
        
        # Registrar log de criação
        self.log.registrar_log(
            usuario_id=criado_por_id,
            acao='CRIAR',
            tabela_afetada='solicitantes',
            registro_id=solicitante_id,
            detalhes={
                'nome': nome,
                'email': email,
                'telefone': telefone,
                'departamento': departamento
            }
        )
        
        conn.close()
        return True
    
    def listar(self):
        conn = self.db.get_connection()
        df = pd.read_sql_query("SELECT * FROM solicitantes ORDER BY nome", conn)
        conn.close()
        return df
    
    def atualizar(self, id, nome, email, telefone, departamento, atualizado_por_id):
        # Buscar dados antigos
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM solicitantes WHERE id=?', (id,))
        dados_antigos = cursor.fetchone()
        
        cursor.execute('''
            UPDATE solicitantes 
            SET nome=?, email=?, telefone=?, departamento=?
            WHERE id=?
        ''', (nome, email, telefone, departamento, id))
        conn.commit()
        
        # Registrar log de atualização
        self.log.registrar_log(
            usuario_id=atualizado_por_id,
            acao='ATUALIZAR',
            tabela_afetada='solicitantes',
            registro_id=id,
            detalhes={
                'campos_alterados': {
                    'nome': {'antes': dados_antigos[1], 'depois': nome},
                    'email': {'antes': dados_antigos[2], 'depois': email},
                    'telefone': {'antes': dados_antigos[3], 'depois': telefone},
                    'departamento': {'antes': dados_antigos[4], 'depois': departamento}
                }
            }
        )
        
        conn.close()
        return True
    
    def deletar(self, id, deletado_por_id):
        # Buscar dados antes de deletar
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT nome FROM solicitantes WHERE id=?', (id,))
        nome_solicitante = cursor.fetchone()[0]
        
        cursor.execute("DELETE FROM solicitantes WHERE id=?", (id,))
        conn.commit()
        
        # Registrar log de exclusão
        self.log.registrar_log(
            usuario_id=deletado_por_id,
            acao='DELETAR',
            tabela_afetada='solicitantes',
            registro_id=id,
            detalhes=f"Solicitante deletado: {nome_solicitante}"
        )
        
        conn.close()
        return True
    
    def buscar_por_id(self, id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM solicitantes WHERE id=?", (id,))
        result = cursor.fetchone()
        conn.close()
        return result

# CRUD para Protocolos
class ProtocolosCRUD:
    def __init__(self, db_manager, log_manager):
        self.db = db_manager
        self.log = log_manager
    
    def criar(self, titulo, descricao, tipo_documento, data_protocolo, data_prazo, 
              solicitante_id, observacoes, criado_por):
        conn = self.db.get_connection()
        cursor = conn.cursor()

        # Gerar o número do protocolo automaticamente
        numero_protocolo = self.db.gerar_numero_protocolo()
        
        cursor.execute('''
            INSERT INTO protocolos (numero_protocolo, titulo, descricao, tipo_documento,
                                  data_protocolo, data_prazo, solicitante_id, 
                                  observacoes, criado_por)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (numero_protocolo, titulo, descricao, tipo_documento, data_protocolo, 
              data_prazo, solicitante_id, observacoes, criado_por))
        
        protocolo_id = cursor.lastrowid
        conn.commit()
        
        # Registrar log de criação
        self.log.registrar_log(
            usuario_id=criado_por,
            acao='CRIAR',
            tabela_afetada='protocolos',
            registro_id=protocolo_id,
            detalhes={
                'numero_protocolo': numero_protocolo,
                'titulo': titulo,
                'tipo_documento': tipo_documento,
                'solicitante_id': solicitante_id
            }
        )
        
        conn.close()
        return numero_protocolo
    
    def listar(self, usuario_id=None, nivel_acesso="admin"):
        conn = self.db.get_connection()
        
        if nivel_acesso == "admin":
            query = '''
                SELECT p.*, s.nome as solicitante_nome,
                       u.nome as criado_por_nome
                FROM protocolos p
                LEFT JOIN solicitantes s ON p.solicitante_id = s.id
                LEFT JOIN usuarios u ON p.criado_por = u.id
                ORDER BY p.numero_protocolo DESC
            '''
            df = pd.read_sql_query(query, conn)
        else:
            # Usuários comuns só veem seus próprios protocolos
            query = '''
                SELECT p.*, s.nome as solicitante_nome,
                       u.nome as criado_por_nome
                FROM protocolos p
                LEFT JOIN solicitantes s ON p.solicitante_id = s.id
                LEFT JOIN usuarios u ON p.criado_por = u.id
                WHERE p.criado_por = ?
                ORDER BY p.numero_protocolo DESC
            '''
            df = pd.read_sql_query(query, conn, params=[usuario_id])
        
        conn.close()
        return df
    
    def atualizar(self, id, titulo, descricao, tipo_documento, status, data_protocolo, 
                  data_prazo, solicitante_id, observacoes, atualizado_por_id):
        # Buscar dados antigos
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM protocolos WHERE id=?', (id,))
        dados_antigos = cursor.fetchone()
        
        cursor.execute('''
            UPDATE protocolos 
            SET titulo=?, descricao=?, tipo_documento=?, status=?, data_protocolo=?,
                data_prazo=?, solicitante_id=?, observacoes=?
            WHERE id=?
        ''', (titulo, descricao, tipo_documento, status, data_protocolo, data_prazo,
              solicitante_id, observacoes, id))
        conn.commit()
        
        # Registrar log de atualização
        self.log.registrar_log(
            usuario_id=atualizado_por_id,
            acao='ATUALIZAR',
            tabela_afetada='protocolos',
            registro_id=id,
            detalhes={
                'numero_protocolo': dados_antigos[1],
                'campos_alterados': {
                    'titulo': {'antes': dados_antigos[2], 'depois': titulo},
                    'status': {'antes': dados_antigos[5], 'depois': status},
                    'tipo_documento': {'antes': dados_antigos[4], 'depois': tipo_documento}
                }
            }
        )
        
        conn.close()
        return True
    
    def deletar(self, id, deletado_por_id):
        # Buscar dados antes de deletar
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT numero_protocolo, titulo FROM protocolos WHERE id=?', (id,))
        dados_protocolo = cursor.fetchone()
        
        cursor.execute("DELETE FROM protocolos WHERE id=?", (id,))
        conn.commit()
        
        # Registrar log de exclusão
        self.log.registrar_log(
            usuario_id=deletado_por_id,
            acao='DELETAR',
            tabela_afetada='protocolos',
            registro_id=id,
            detalhes={
                'numero_protocolo': dados_protocolo[0],
                'titulo': dados_protocolo[1]
            }
        )
        
        conn.close()
        return True
    
    def buscar_por_id(self, id):
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM protocolos WHERE id=?", (id,))
        result = cursor.fetchone()
        conn.close()
        return result

# Função para verificar se o usuário tem permissão
def verificar_permissao(nivel_requerido):
    """Verifica se o usuário logado tem o nível de acesso necessário"""
    if 'user_data' not in st.session_state:
        return False
    
    user_level = st.session_state.user_data[3]  # nivel_acesso
    
    if nivel_requerido == "admin":
        return user_level == "admin"
    elif nivel_requerido == "usuario":
        return user_level in ["admin", "usuario"]
    
    return False

# Função de logout
def logout():
    """Faz logout do usuário"""
    if 'user_data' in st.session_state:
        # Registrar log de logout
        log_manager.registrar_log(
            usuario_id=st.session_state.user_data[0],
            acao='LOGOUT',
            tabela_afetada='usuarios',
            registro_id=st.session_state.user_data[0],
            detalhes=f"Logout realizado para usuário: {st.session_state.user_data[1]}"
        )
    
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

# Página para visualizar logs
def pagina_logs():
    """Página para visualização e análise de logs"""
    st.title("📊 Logs do Sistema")
    
    # Verificar permissão de admin
    if not verificar_permissao("admin"):
        st.error("Acesso negado. Apenas administradores podem visualizar os logs.")
        return
    
    tabs = st.tabs(["📋 Lista de Logs", "📈 Estatísticas", "🔍 Filtros Avançados"])
    
    with tabs[0]:
        st.subheader("Logs Recentes")
        
        # Buscar logs recentes
        logs_df = log_manager.buscar_logs(limite=50)
        
        if not logs_df.empty:
            # Configurar cores por status
            def colorir_status(status):
                if status == 'sucesso':
                    return 'background-color: #d4edda'
                elif status == 'erro':
                    return 'background-color: #f8d7da'
                else:
                    return ''
            
            # Mostrar tabela
            st.dataframe(
                logs_df[['timestamp', 'usuario_nome', 'acao', 'tabela_afetada', 'status', 'detalhes']],
                use_container_width=True
            )
        else:
            st.info("Nenhum log encontrado.")
    
    with tabs[1]:
        st.subheader("Estatísticas de Atividade")
        
        df_acoes, df_usuarios, df_diario = log_manager.estatisticas_logs()
        
        col1, col2 = st.columns(2)
        
        with col1:
            if not df_acoes.empty:
                fig_acoes = px.pie(df_acoes, values='total', names='acao', 
                                 title='Distribuição de Ações')
                st.plotly_chart(fig_acoes, use_container_width=True)
        
        with col2:
            if not df_usuarios.empty:
                fig_usuarios = px.bar(df_usuarios.head(10), x='nome', y='total_acoes',
                                    title='Top 10 Usuários Mais Ativos')
                st.plotly_chart(fig_usuarios, use_container_width=True)
        
        # Gráfico de atividade diária
        if not df_diario.empty:
            fig_diario = px.line(df_diario, x='data', y='total',
                               title='Atividade Diária (Últimos 30 dias)')
            st.plotly_chart(fig_diario, use_container_width=True)
    
    with tabs[2]:
        st.subheader("Filtros Avançados")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Filtro por usuário
            usuarios_df = auth_manager.listar_usuarios()
            usuario_opcoes = [{"label": "Todos", "value": None}] + \
                           [{"label": row['nome'], "value": row['id']} for _, row in usuarios_df.iterrows()]
            
            filtro_usuario = st.selectbox(
                "Filtrar por Usuário:",
                options=[opt["value"] for opt in usuario_opcoes],
                format_func=lambda x: next((opt["label"] for opt in usuario_opcoes if opt["value"] == x), "Todos")
            )
        
        with col2:
            # Filtro por tabela
            filtro_tabela = st.selectbox(
                "Filtrar por Tabela:",
                ["Todas", "usuarios", "protocolos", "solicitantes"]
            )
            if filtro_tabela == "Todas":
                filtro_tabela = None
        
        with col3:
            # Filtro por ação
            filtro_acao = st.selectbox(
                "Filtrar por Ação:",
                ["Todas", "CRIAR", "ATUALIZAR", "DELETAR", "LOGIN", "LOGOUT"]
            )
            if filtro_acao == "Todas":
                filtro_acao = None
        
        col4, col5 = st.columns(2)
        with col4:
            data_inicio = st.date_input("Data Início:")
        with col5:
            data_fim = st.date_input("Data Fim:")
        
        if st.button("🔍 Aplicar Filtros"):
            logs_filtrados = log_manager.buscar_logs(
                filtro_usuario=filtro_usuario,
                filtro_tabela=filtro_tabela,
                filtro_acao=filtro_acao,
                data_inicio=data_inicio,
                data_fim=data_fim,
                limite=200
            )
            
            if not logs_filtrados.empty:
                st.dataframe(
                    logs_filtrados[['timestamp', 'usuario_nome', 'acao', 'tabela_afetada', 'status', 'detalhes']],
                    use_container_width=True
                )
                
                # Opção para download
                csv = logs_filtrados.to_csv(index=False)
                st.download_button(
                    label="📥 Download CSV",
                    data=csv,
                    file_name=f"logs_sistema_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv"
                )
            else:
                st.info("Nenhum log encontrado com os filtros aplicados.")

# Inicialização
db_manager = DatabaseManager()
log_manager = LogManager(db_manager)
auth_manager = AuthManager(db_manager, log_manager)
solicitantes_crud = SolicitantesCRUD(db_manager, log_manager)
protocolos_crud = ProtocolosCRUD(db_manager, log_manager)

# Página de Login
def pagina_login():
    st.title("🔐 Sistema de Protocolo - Login")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("### Acesso ao Sistema")
        
        with st.form("login_form"):
            usuario = st.text_input("Usuário")
            senha = st.text_input("Senha", type="password")
            submit = st.form_submit_button("Entrar")
            
            if submit:
                user_data = auth_manager.login(usuario, senha)
                if user_data:
                    st.session_state.authenticated = True
                    st.session_state.user_data = user_data
                    st.success(f"Bem-vindo, {user_data[2]}!")
                    st.rerun()
                else:
                    st.error("Usuário ou senha inválidos!")
        
        st.markdown("---")
        st.info("**Dúvidas:** GCMADM | **admgcm@itapeva.sp.gov.br**")

# Dashboard
def dashboard():
    st.title("📊 Dashboard")
    
    # Métricas gerais
    col1, col2, col3, col4 = st.columns(4)
    
    # Buscar dados para métricas
    protocolos_df = protocolos_crud.listar(
    #    st.session_state.user_data[0], 
    #    st.session_state.user_data[3]
    )
    solicitantes_df = solicitantes_crud.listar()
    
    with col1:
        st.metric("Total de Protocolos", len(protocolos_df))
    
    with col2:
        pendentes = len(protocolos_df[protocolos_df['status'] == 'Pendente'])
        st.metric("Protocolos Pendentes", pendentes)
    
    with col3:
        st.metric("Total de Solicitantes", len(solicitantes_df))
    
    with col4:
        # Protocolos vencidos (data_prazo < hoje)
        if not protocolos_df.empty:
            protocolos_df['data_prazo'] = pd.to_datetime(protocolos_df['data_prazo'])
            vencidos = len(protocolos_df[
                (protocolos_df['data_prazo'] < datetime.now()) & 
                (protocolos_df['status'] == 'Pendente')
            ])
            st.metric("Protocolos Vencidos", vencidos)
        else:
            st.metric("Protocolos Vencidos", 0)
    
    # Gráficos
    if not protocolos_df.empty:
        col1, col2 = st.columns(2)
        
        with col1:
            # Gráfico de status
            status_counts = protocolos_df['status'].value_counts()
            fig_status = px.pie(values=status_counts.values, names=status_counts.index,
                              title="Distribuição por Status")
            st.plotly_chart(fig_status, use_container_width=True)
        
        with col2:
            # Gráfico de tipos de documento
            tipo_counts = protocolos_df['tipo_documento'].value_counts()
            fig_tipo = px.bar(x=tipo_counts.index, y=tipo_counts.values,
                            title="Protocolos por Tipo de Documento")
            st.plotly_chart(fig_tipo, use_container_width=True)
    
    # Logs recentes (apenas para admin)
    if verificar_permissao("admin"):
        st.subheader("📋 Atividades Recentes")
        logs_recentes = log_manager.buscar_logs(limite=10)
        if not logs_recentes.empty:
            st.dataframe(
                logs_recentes[['timestamp', 'usuario_nome', 'acao', 'tabela_afetada']],
                use_container_width=True
            )

def gerar_relatorio_pdf(protocolos_df, filtro_data_inicio=None, filtro_data_fim=None, 
                       filtro_status=None, filtro_tipo=None):
    """
    Gera um relatório PDF dos protocolos com os filtros aplicados
    """
    buffer = BytesIO()
    
    # Configurar documento PDF
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=1*inch,
        bottomMargin=0.5*inch
    )
    
    # Estilos
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=30,
        alignment=1,  # Centralizado
        textColor=colors.darkblue
    )
    
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=12,
        spaceAfter=20,
        alignment=1,  # Centralizado
        textColor=colors.black
    )
    
    # Conteúdo do PDF
    story = []
    
    # Título
    story.append(Paragraph("RELATÓRIO DE PROTOCOLOS", title_style))
    
    # Informações do filtro
    filtros_aplicados = []
    if filtro_data_inicio and filtro_data_fim:
        filtros_aplicados.append(f"Período: {filtro_data_inicio.strftime('%d/%m/%Y')} a {filtro_data_fim.strftime('%d/%m/%Y')}")
    elif filtro_data_inicio:
        filtros_aplicados.append(f"A partir de: {filtro_data_inicio.strftime('%d/%m/%Y')}")
    elif filtro_data_fim:
        filtros_aplicados.append(f"Até: {filtro_data_fim.strftime('%d/%m/%Y')}")
    
    if filtro_status and filtro_status != "Todos":
        filtros_aplicados.append(f"Status: {filtro_status}")
    
    if filtro_tipo and filtro_tipo != "Todos":
        filtros_aplicados.append(f"Tipo: {filtro_tipo}")
    
    if filtros_aplicados:
        filtros_texto = " | ".join(filtros_aplicados)
        story.append(Paragraph(f"Filtros aplicados: {filtros_texto}", subtitle_style))
    
    # Data de geração
    data_geracao = datetime.now().strftime("%d/%m/%Y às %H:%M")
    story.append(Paragraph(f"Gerado em: {data_geracao}", subtitle_style))
    
    # Total de registros
    total_protocolos = len(protocolos_df)
    story.append(Paragraph(f"Total de protocolos: {total_protocolos}", subtitle_style))
    
    story.append(Spacer(1, 20))
    
    if not protocolos_df.empty:
        # Preparar dados da tabela
        dados_tabela = [['Nº Protocolo', 'Título', 'Descrição', 'Solicitante', 'Criado por', 'Data']]
        
        for _, row in protocolos_df.iterrows():
            # Truncar títulos muito longos
            titulo = row['titulo'][:40] + "..." if len(row['titulo']) > 40 else row['titulo']
            descricao = row['descricao'][:40] + "..." if len(row['descricao']) > 40 else row['descricao']
            
            # Formatar datas
            data_protocolo = row['data_protocolo']
            if isinstance(data_protocolo, str):
                try:
                    data_protocolo = datetime.strptime(data_protocolo, '%Y-%m-%d').strftime('%d/%m/%Y')
                except:
                    data_protocolo = data_protocolo
            
            #data_prazo = row.get('data_prazo', '')
            #if data_prazo and isinstance(data_prazo, str):
            #    try:
            #        data_prazo = datetime.strptime(data_prazo, '%Y-%m-%d').strftime('%d/%m/%Y')
            #    except:
                    pass
            
            dados_tabela.append([
                row['numero_protocolo'],
                titulo,
                row['descricao'],
                row.get('solicitante_nome', '')[:20] + "..." if len(str(row.get('solicitante_nome', ''))) > 20 else row.get('solicitante_nome', ''),
                row.get('criado_por_nome', '')[:20] + "..." if len(str(row.get('criado_por_nome', ''))) > 20 else row.get('criado_por_nome', ''),
                data_protocolo or '-'
                # data_prazo or '-'
            ])
        
        # Criar tabela
        tabela = Table(dados_tabela, colWidths=[1.0*inch, 1.0*inch, 5.5*inch, 1.7*inch, 1.3*inch, 0.8*inch])
        
        # Estilo da tabela
        tabela.setStyle(TableStyle([
            # Cabeçalho
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            
            # Corpo da tabela
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            
            # Bordas
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            
            # Alternating row colors
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))
        
        story.append(tabela)
        
        # Resumo por status
        story.append(Spacer(1, 30))
        story.append(Paragraph("RESUMO POR TIPO DE DOCUMENTO", title_style))
        
        resumo_status = protocolos_df['tipo_documento'].value_counts()
        dados_resumo = [['Tipo', 'Quantidade', 'Percentual']]
        
        for status, quantidade in resumo_status.items():
            percentual = (quantidade / total_protocolos) * 100
            dados_resumo.append([status, str(quantidade), f"{percentual:.1f}%"])
        
        tabela_resumo = Table(dados_resumo, colWidths=[2*inch, 1*inch, 1*inch])
        tabela_resumo.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        
        story.append(tabela_resumo)
    
    else:
        story.append(Paragraph("Nenhum protocolo encontrado com os filtros aplicados.", subtitle_style))
    
    # Gerar PDF
    doc.build(story)
    buffer.seek(0)
    return buffer

# Gerenciar Protocolos
def gerenciar_protocolos():
    st.title("📋 Gerenciar Protocolos")
    
    # Inicializar contador único para chaves se não existir
    if 'key_counter' not in st.session_state:
        st.session_state.key_counter = 0
    
    def get_unique_key(base_key):
        """Gera uma chave única incrementando o contador"""
        st.session_state.key_counter += 1
        return f"{base_key}_{st.session_state.key_counter}"
    
    tabs = st.tabs(["📝 Novo Protocolo", "📋 Lista de Protocolos", "📊 Relatórios"])
    
    with tabs[0]:
        st.subheader("Criar Novo Protocolo")
        
        with st.form("novo_protocolo"):
            col1, col2 = st.columns(2)
            
            with col1:
                titulo = st.text_input("Título*", placeholder="Título do protocolo")
                tipo_documento = st.selectbox("Tipo de Documento*", [
                    "Ofício", "Memorando", "Relatório", "Solicitação", 
                    "Recurso", "Processo", "Outros"
                ])
                data_prazo = st.date_input(
                    "Data Prazo", 
                    value=date.today(),
                    min_value=date.today(),  # Não permite datas anteriores à data atual
                    help="A data prazo deve ser igual ou posterior à data do protocolo"
                )
                
            
            with col2:
                # Listar solicitantes
                data_protocolo = st.date_input("Data do Protocolo*", value=date.today(), disabled=True)
                solicitantes_df = solicitantes_crud.listar()
                if not solicitantes_df.empty:
                    solicitante_opcoes = {
                        f"{row['nome']} - {row['departamento']}": row['id'] 
                        for _, row in solicitantes_df.iterrows()
                    }
                    solicitante_selecionado = st.selectbox("Solicitante*", list(solicitante_opcoes.keys()))
                    solicitante_id = solicitante_opcoes[solicitante_selecionado]
                else:
                    st.warning("Nenhum solicitante cadastrado. Cadastre um solicitante primeiro.")
                    solicitante_id = None
                
                           
            descricao = st.text_area("Descrição", placeholder="Descrição detalhada do protocolo")
            observacoes = st.text_area("Observações", placeholder="Observações adicionais")
            
            submit = st.form_submit_button("🚀 Criar Protocolo")
            
            if submit:
                if titulo and tipo_documento and solicitante_id:
                    numero_protocolo = protocolos_crud.criar(
                        titulo=titulo,
                        descricao=descricao,
                        tipo_documento=tipo_documento,
                        data_protocolo=data_protocolo,
                        data_prazo=data_prazo,
                        solicitante_id=solicitante_id,
                        observacoes=observacoes,
                        criado_por=st.session_state.user_data[0]
                    )
                    st.success(f"✅ Protocolo criado com sucesso! Número: {numero_protocolo}")
                    st.rerun()
                else:
                    st.error("❌ Preencha todos os campos obrigatórios!")
    
    with tabs[1]:
        st.subheader("Lista de Protocolos")
        
        # Verificar se o usuário é administrador
        is_admin = st.session_state.user_data[3] == 'admin'  # Assumindo que o tipo de usuário está no índice 3
        current_user_id = st.session_state.user_data[0]  # ID do usuário atual
        
        # Verificar se está editando um protocolo
        if 'editando_protocolo' in st.session_state:
            protocolo_id = st.session_state.editando_protocolo
            protocolo_data = protocolos_crud.buscar_por_id(protocolo_id)
            
            if protocolo_data:
                # Verificar permissão para editar - assumindo que criado_por está no final da tupla
                # Vamos verificar diferentes possíveis índices para criado_por_id
                protocolo_criado_por = None
                
                # Tentar encontrar o campo criado_por_id na tupla retornada
                if len(protocolo_data) > 10:
                    protocolo_criado_por = protocolo_data[10]  # Índice 10
                elif len(protocolo_data) > 9:
                    protocolo_criado_por = protocolo_data[9]   # Índice 9 (se observacoes for antes)
                
                # Debugging: mostrar informações do protocolo para verificar estrutura
                #if st.checkbox("🔍 Debug: Mostrar estrutura do protocolo", key="debug_protocolo"):
                #    st.write("Dados do protocolo:", protocolo_data)
                #    st.write("Comprimento da tupla:", len(protocolo_data))
                #    st.write("ID do usuário atual:", current_user_id)
                #    st.write("Criado por (tentativa):", protocolo_criado_por)
                
                # Permitir edição se for admin OU se for o criador do protocolo
                pode_editar = is_admin or (protocolo_criado_por == current_user_id)
                
                if not pode_editar:
                    st.error("❌ Você não tem permissão para editar este protocolo. Apenas o criador ou administradores podem editar.")
                    st.info(f"ℹ️ Este protocolo foi criado por outro usuário (ID: {protocolo_criado_por}). Seu ID: {current_user_id}")
                    del st.session_state.editando_protocolo
                    st.rerun()
                else:
                    st.subheader(f"✏️ Editando Protocolo: {protocolo_data[1]}")  # numero_protocolo
                    
                    with st.form("editar_protocolo"):
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            titulo_edit = st.text_input("Título*", value=protocolo_data[2])  # titulo
                            tipo_documento_edit = st.selectbox("Tipo de Documento*", 
                                ["Ofício", "Memorando", "Relatório", "Solicitação", "Recurso", "Processo", "Outros"],
                                index=["Ofício", "Memorando", "Relatório", "Solicitação", "Recurso", "Processo", "Outros"].index(protocolo_data[4]) if protocolo_data[4] in ["Ofício", "Memorando", "Relatório", "Solicitação", "Recurso", "Processo", "Outros"] else 0
                            )
                            status_edit = st.selectbox("Status*", 
                                ["Pendente", "Em Andamento", "Concluído", "Cancelado"],
                                index=["Pendente", "Em Andamento", "Concluído", "Cancelado"].index(protocolo_data[5]) if protocolo_data[5] in ["Pendente", "Em Andamento", "Concluído", "Cancelado"] else 0
                            )
                            data_protocolo_edit = st.date_input("Data do Protocolo*", 
                                value=datetime.strptime(protocolo_data[6], '%Y-%m-%d').date() if protocolo_data[6] else date.today(),
                                disabled=True,
                                help="A data do protocolo não pode ser alterada após a criação"
                            )
                        
                        with col2:
                            # Listar solicitantes
                            solicitantes_df = solicitantes_crud.listar()
                            if not solicitantes_df.empty:
                                solicitante_opcoes = {
                                    f"{row['nome']} - {row['departamento']}": row['id'] 
                                    for _, row in solicitantes_df.iterrows()
                                }
                                
                                # Encontrar o solicitante atual
                                solicitante_atual = None
                                for key, value in solicitante_opcoes.items():
                                    if value == protocolo_data[8]:  # solicitante_id
                                        solicitante_atual = key
                                        break
                                
                                if solicitante_atual:
                                    index_atual = list(solicitante_opcoes.keys()).index(solicitante_atual)
                                else:
                                    index_atual = 0
                                
                                solicitante_selecionado_edit = st.selectbox("Solicitante*", 
                                    list(solicitante_opcoes.keys()), 
                                    index=index_atual
                                )
                                solicitante_id_edit = solicitante_opcoes[solicitante_selecionado_edit]
                            else:
                                st.warning("Nenhum solicitante cadastrado.")
                                solicitante_id_edit = protocolo_data[8]
                            
                            data_prazo_edit = st.date_input("Data Prazo", 
                                value=datetime.strptime(protocolo_data[7], '%Y-%m-%d').date() if protocolo_data[7] else None,
                                min_value=datetime.strptime(protocolo_data[6], '%Y-%m-%d').date() if protocolo_data[6] else date.today(),
                                help="A data prazo deve ser igual ou posterior à data do protocolo"
                            )
                        
                        descricao_edit = st.text_area("Descrição", value=protocolo_data[3] or "")  # descricao
                        observacoes_edit = st.text_area("Observações", value=protocolo_data[9] or "")  # observacoes
                        
                        col_btn1, col_btn2 = st.columns(2)
                        
                        with col_btn1:
                            submit_edit = st.form_submit_button("💾 Salvar Alterações")
                        
                        with col_btn2:
                            cancel_edit = st.form_submit_button("❌ Cancelar")
                        
                        if submit_edit:
                            if titulo_edit and tipo_documento_edit and solicitante_id_edit:
                                if protocolos_crud.atualizar(
                                    id=protocolo_id,
                                    titulo=titulo_edit,
                                    descricao=descricao_edit,
                                    tipo_documento=tipo_documento_edit,
                                    status=status_edit,
                                    data_protocolo=data_protocolo_edit,
                                    data_prazo=data_prazo_edit,
                                    solicitante_id=solicitante_id_edit,
                                    observacoes=observacoes_edit,
                                    atualizado_por_id=current_user_id
                                ):
                                    st.success("✅ Protocolo atualizado com sucesso!")
                                    del st.session_state.editando_protocolo
                                    st.rerun()
                                else:
                                    st.error("❌ Erro ao atualizar protocolo!")
                            else:
                                st.error("❌ Preencha todos os campos obrigatórios!")
                        
                        if cancel_edit:
                            del st.session_state.editando_protocolo
                            st.rerun()
            else:
                st.error("Protocolo não encontrado!")
                del st.session_state.editando_protocolo
                st.rerun()
        
        else:
            # Filtros - Inicializar variáveis antes de usar
            col1, col2, col3 = st.columns(3)
            
            with col1:
                # MODIFICAÇÃO: Filtro por Solicitantes em vez de Status
                # Buscar lista de solicitantes para o filtro
                solicitantes_df = solicitantes_crud.listar()
                if not solicitantes_df.empty:
                    solicitante_opcoes_filtro = ["Todos"] + [f"{row['nome']} - {row['departamento']}" for _, row in solicitantes_df.iterrows()]
                    filtro_solicitante = st.selectbox("Filtrar por Solicitante:", 
                                                    solicitante_opcoes_filtro,
                                                    key="filtro_solicitante_select")
                    
                    # Obter ID do solicitante selecionado para filtro
                    if filtro_solicitante != "Todos":
                        solicitante_filtro_id = solicitantes_df[
                            solicitantes_df.apply(lambda row: f"{row['nome']} - {row['departamento']}" == filtro_solicitante, axis=1)
                        ]['id'].iloc[0]
                    else:
                        solicitante_filtro_id = None
                else:
                    st.info("Nenhum solicitante cadastrado.")
                    filtro_solicitante = "Todos"
                    solicitante_filtro_id = None
            
            with col2:
                filtro_tipo = st.selectbox("Filtrar por Tipo:", 
                                         ["Todos", "Ofício", "Memorando", "Relatório", "Solicitação", "Recurso", "Processo", "Outros"],
                                         key="filtro_tipo_select")
            
            with col3:
                busca_texto = st.text_input("Buscar por título/número:", key="busca_texto_input")
            
            # Listar protocolos - ALTERAÇÃO: Administrador vê todos, usuário comum vê todos mas só edita os seus
            try:
                # Todos os usuários podem ver todos os protocolos
                protocolos_df = protocolos_crud.listar()  # Método que lista todos os protocolos
                
            except Exception as e:
                st.error(f"Erro ao carregar protocolos: {str(e)}")
                protocolos_df = pd.DataFrame()  # DataFrame vazio em caso de erro
            
            # Verificar se o DataFrame foi carregado corretamente
            if not protocolos_df.empty:
                # Aplicar filtros apenas se as variáveis estão definidas
                try:
                    # MODIFICAÇÃO: Filtro por solicitante em vez de status
                    if solicitante_filtro_id is not None:
                        protocolos_df = protocolos_df[protocolos_df['solicitante_id'] == solicitante_filtro_id]
                    
                    if filtro_tipo and filtro_tipo != "Todos":
                        protocolos_df = protocolos_df[protocolos_df['tipo_documento'] == filtro_tipo]
                    
                    if busca_texto and busca_texto.strip():
                        mask = (protocolos_df['titulo'].str.contains(busca_texto, case=False, na=False) |
                               protocolos_df['numero_protocolo'].str.contains(busca_texto, case=False, na=False))
                        protocolos_df = protocolos_df[mask]
                except Exception as e:
                    st.error(f"Erro ao aplicar filtros: {str(e)}")
                
                # Exibir informação sobre permissões
                if not is_admin:
                    st.info("ℹ️ Você pode visualizar todos os protocolos, mas só pode editar os protocolos criados por você. Apenas administradores podem excluir protocolos.")
                
                # Mostrar tabela
                if not protocolos_df.empty:
                    for index, protocolo in protocolos_df.iterrows():
                        # Usar uma combinação de ID do protocolo e timestamp para garantir unicidade
                        unique_suffix = f"{protocolo['id']}_{index}_{hash(str(protocolo))}"
                        
                        # Verificar se o usuário pode editar este protocolo
                        # Tentar diferentes campos possíveis para criado_por_id
                        protocolo_criado_por_id = None
                        
                        # Verificar possíveis nomes de colunas para criado_por_id
                        if 'criado_por_id' in protocolo:
                            protocolo_criado_por_id = protocolo['criado_por_id']
                        elif 'criado_por' in protocolo:
                            protocolo_criado_por_id = protocolo['criado_por']
                        elif 'user_id' in protocolo:
                            protocolo_criado_por_id = protocolo['user_id']
                        
                        # Debugging: adicionar informação sobre criado_por_id
                        #if st.checkbox(f"🔍 Debug protocolo {protocolo['numero_protocolo']}", key=f"debug_{unique_suffix}"):
                        #    st.write("Colunas disponíveis:", list(protocolo.keys()))
                        #    st.write("ID do protocolo:", protocolo['id'])
                        #    st.write("Criado por ID encontrado:", protocolo_criado_por_id)
                        #    st.write("ID do usuário atual:", current_user_id)
                        
                        pode_editar = is_admin or (protocolo_criado_por_id == current_user_id)

                        # Se data_protocolo for um objeto datetime
                        if isinstance(protocolo['data_protocolo'], datetime):
                            data_formatada = protocolo['data_protocolo'].strftime('%d%m%Y')
                        else:
                            # Se for string, primeiro converte para datetime e depois formata
                            # Ajuste o formato de entrada conforme necessário
                            data_obj = datetime.strptime(protocolo['data_protocolo'], '%Y-%m-%d')  # Formato de entrada exemplo
                            data_formatada = data_obj.strftime('%d/%m/%Y')

                        # Adicionar indicador visual para protocolos próprios
                        titulo_protocolo = f"📋 {protocolo['numero_protocolo']} - {protocolo['titulo']} - {data_formatada} - **Solicitante:** {protocolo['solicitante_nome']} - **Descrição:** {protocolo['descricao']}"
                        if not is_admin and protocolo_criado_por_id == current_user_id:
                            titulo_protocolo += " 👤 (Seu protocolo)"
                        
                        with st.expander(titulo_protocolo):
                            col1, col2, col3 = st.columns(3)
                            
                            with col1:
                                st.write(f"**Status:** {protocolo['status']}")
                                st.write(f"**Tipo:** {protocolo['tipo_documento']}")
                                st.write(f"**Data:** {protocolo['data_protocolo']}")
                            
                            with col2:
                                st.write(f"**Solicitante:** {protocolo['solicitante_nome']}")
                                st.write(f"**Criado por:** {protocolo['criado_por_nome']}")
                                if protocolo['data_prazo']:
                                    st.write(f"**Prazo:** {protocolo['data_prazo']}")
                            
                            with col3:
                                # Botão de editar - disponível para o criador ou admin
                                if pode_editar:
                                    if st.button(f"✏️ Editar", key=f"edit_{unique_suffix}"):
                                        st.session_state.editando_protocolo = protocolo['id']
                                        st.rerun()
                                else:
                                    st.button(f"✏️ Editar", key=f"edit_{unique_suffix}", 
                                            disabled=True, 
                                            help="Você só pode editar protocolos criados por você")
                                
                                # Botão de excluir - APENAS para administradores
                                if is_admin:
                                    if st.button(f"🗑️ Excluir", key=f"del_{unique_suffix}"):
                                        if protocolos_crud.deletar(protocolo['id'], current_user_id):
                                            st.success("Protocolo excluído com sucesso!")
                                            st.rerun()
                                        else:
                                            st.error("Erro ao excluir protocolo!")
                            
                            if protocolo['descricao']:
                                st.write(f"**Descrição:** {protocolo['descricao']}")
                            
                            if protocolo['observacoes']:
                                st.write(f"**Observações:** {protocolo['observacoes']}")
                else:
                    st.info("Nenhum protocolo encontrado com os filtros aplicados.")
            else:
                st.info("Nenhum protocolo encontrado.")
    
    # Nova aba de relatórios
    with tabs[2]:
        st.subheader("📊 Relatórios em PDF")
        
        st.markdown("""
        Gere relatórios completos dos protocolos em formato PDF com filtros personalizados.
        """)
        
        # Filtros para relatório
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**📅 Filtro por Data**")
            filtro_data_inicio = st.date_input(
                "Data de início:",
                value=None,
                key="relatorio_data_inicio",
                help="Deixe em branco para incluir todos os protocolos desde o início"
            )
            
            filtro_data_fim = st.date_input(
                "Data de fim:",
                value=None,
                key="relatorio_data_fim",
                help="Deixe em branco para incluir todos os protocolos até hoje"
            )
        
        with col2:
            st.markdown("**🔍 Filtros Adicionais**")
            filtro_status_relatorio = st.selectbox(
                "Status:",
                ["Todos", "Pendente", "Em Andamento", "Concluído", "Cancelado"],
                key="relatorio_status"
            )
            
            filtro_tipo_relatorio = st.selectbox(
                "Tipo de Documento:",
                ["Todos", "Ofício", "Memorando", "Relatório", "Solicitação", "Recurso", "Processo", "Outros"],
                key="relatorio_tipo"
            )
        
        # Validação de datas
        data_valida = True
        if filtro_data_inicio and filtro_data_fim and filtro_data_inicio > filtro_data_fim:
            st.error("❌ A data de início não pode ser posterior à data de fim!")
            data_valida = False
        
        # Botão para gerar relatório
        if st.button("📄 Gerar Relatório PDF", disabled=not data_valida):
            with st.spinner("Gerando relatório PDF..."):
                try:
                    # Carregar protocolos
                    protocolos_df = protocolos_crud.listar()
                    
                    if not protocolos_df.empty:
                        # Aplicar filtros de data
                        if filtro_data_inicio:
                            # Converter coluna de data para datetime se necessário
                            protocolos_df['data_protocolo_dt'] = pd.to_datetime(protocolos_df['data_protocolo'])
                            protocolos_df = protocolos_df[protocolos_df['data_protocolo_dt'] >= pd.to_datetime(filtro_data_inicio)]
                        
                        if filtro_data_fim:
                            if 'data_protocolo_dt' not in protocolos_df.columns:
                                protocolos_df['data_protocolo_dt'] = pd.to_datetime(protocolos_df['data_protocolo'])
                            protocolos_df = protocolos_df[protocolos_df['data_protocolo_dt'] <= pd.to_datetime(filtro_data_fim)]
                        
                        # Aplicar outros filtros
                        if filtro_status_relatorio != "Todos":
                            protocolos_df = protocolos_df[protocolos_df['status'] == filtro_status_relatorio]
                        
                        if filtro_tipo_relatorio != "Todos":
                            protocolos_df = protocolos_df[protocolos_df['tipo_documento'] == filtro_tipo_relatorio]
                        
                        # Remover coluna auxiliar se foi criada
                        if 'data_protocolo_dt' in protocolos_df.columns:
                            protocolos_df = protocolos_df.drop('data_protocolo_dt', axis=1)
                        
                        # Gerar PDF
                        pdf_buffer = gerar_relatorio_pdf(
                            protocolos_df,
                            filtro_data_inicio,
                            filtro_data_fim,
                            filtro_status_relatorio,
                            filtro_tipo_relatorio
                        )
                        
                        # Nome do arquivo
                        nome_arquivo = "relatorio_protocolos"
                        if filtro_data_inicio and filtro_data_fim:
                            nome_arquivo += f"_{filtro_data_inicio.strftime('%Y%m%d')}_a_{filtro_data_fim.strftime('%Y%m%d')}"
                        elif filtro_data_inicio:
                            nome_arquivo += f"_a_partir_{filtro_data_inicio.strftime('%Y%m%d')}"
                        elif filtro_data_fim:
                            nome_arquivo += f"_ate_{filtro_data_fim.strftime('%Y%m%d')}"
                        
                        nome_arquivo += f"_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                        
                        # Botão de download
                        st.success(f"✅ Relatório gerado com sucesso! Total de {len(protocolos_df)} protocolos encontrados.")
                        
                        st.download_button(
                            label="📥 Baixar Relatório PDF",
                            data=pdf_buffer.getvalue(),
                            file_name=nome_arquivo,
                            mime="application/pdf",
                            key="download_relatorio"
                        )
                        
                        # Mostrar prévia dos dados
                        if len(protocolos_df) > 0:
                            st.markdown("**📋 Prévia dos dados incluídos no relatório:**")
                            
                            # Resumo por status
                            col1, col2, col3 = st.columns(3)
                            
                            with col1:
                                st.metric("Total de Protocolos", len(protocolos_df))
                            
                            with col2:
                                if filtro_data_inicio and filtro_data_fim:
                                    dias_periodo = (filtro_data_fim - filtro_data_inicio).days + 1
                                    st.metric("Período (dias)", dias_periodo)
                                else:
                                    st.metric("Período", "Não definido")
                            
                            with col3:
                                tipos_unicos = protocolos_df['tipo_documento'].nunique()
                                st.metric("Tipos Diferentes", tipos_unicos)
                            
                            # Gráfico de status
                            st.markdown("**📊 Distribuição por Status:**")
                            status_counts = protocolos_df['status'].value_counts()
                            
                            col1, col2 = st.columns([2, 1])
                            
                            with col1:
                                st.bar_chart(status_counts)
                            
                            with col2:
                                for status, count in status_counts.items():
                                    percentual = (count / len(protocolos_df)) * 100
                                    st.write(f"**{status}:** {count} ({percentual:.1f}%)")
                            
                            # Tabela resumida
                            st.markdown("**📋 Primeiros 10 protocolos do relatório:**")
                            colunas_exibicao = ['numero_protocolo', 'titulo', 'tipo_documento', 'status', 'data_protocolo']
                            df_preview = protocolos_df[colunas_exibicao].head(10)
                            st.dataframe(df_preview, use_container_width=True)
                        
                    else:
                        st.warning("⚠️ Nenhum protocolo encontrado para gerar o relatório!")
                        
                except Exception as e:
                    st.error(f"❌ Erro ao gerar relatório: {str(e)}")
                    st.error("Verifique se todos os dados estão corretos e tente novamente.")
        
        # Informações sobre o relatório
        st.markdown("---")
        st.markdown("**ℹ️ Informações sobre o Relatório:**")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("""
            **📄 Conteúdo do Relatório:**
            - Lista completa de protocolos filtrados
            - Informações básicas: número, título, tipo, status
            - Dados do solicitante e datas
            - Resumo estatístico por status
            - Data e hora de geração
            """)
        
        with col2:
            st.markdown("""
            **🔧 Funcionalidades:**
            - Filtro por período (data início/fim)
            - Filtro por solicitante do protocolo
            - Filtro por tipo de documento
            - Formato PDF profissional
            - Download direto do arquivo
            """)
        
        
                                
# Gerenciar Solicitantes
def gerenciar_solicitantes():
    st.title("👥 Gerenciar Solicitantes")
    
    # Verificar se está editando um solicitante
    if 'editando_solicitante' in st.session_state and st.session_state.editando_solicitante:
        editar_solicitante()
        return
    
    tabs = st.tabs(["➕ Novo Solicitante", "📋 Lista de Solicitantes"])
    
    with tabs[0]:
        st.subheader("Cadastrar Novo Solicitante")
        
        with st.form("novo_solicitante"):
            col1, col2 = st.columns(2)
            
            with col1:
                nome = st.text_input("Nome*", placeholder="Nome completo")
                email = st.text_input("Email", placeholder="email@exemplo.com")
            
            with col2:
                telefone = st.text_input("Telefone", placeholder="(11) 99999-9999")
                departamento = st.text_input("Departamento", placeholder="Ex: Recursos Humanos")
            
            submit = st.form_submit_button("💾 Cadastrar")
            
            if submit:
                if nome:
                    if solicitantes_crud.criar(nome, email, telefone, departamento, st.session_state.user_data[0]):
                        st.success("✅ Solicitante cadastrado com sucesso!")
                        st.rerun()
                    else:
                        st.error("❌ Erro ao cadastrar solicitante!")
                else:
                    st.error("❌ O nome é obrigatório!")
    
    with tabs[1]:
        st.subheader("Lista de Solicitantes")
        
        solicitantes_df = solicitantes_crud.listar()
        
        if not solicitantes_df.empty:
            # Busca
            busca = st.text_input("🔍 Buscar solicitante:")
            
            if busca:
                mask = (solicitantes_df['nome'].str.contains(busca, case=False, na=False) |
                       solicitantes_df['departamento'].str.contains(busca, case=False, na=False))
                solicitantes_df = solicitantes_df[mask]
            
            # Mostrar em cards
            for _, solicitante in solicitantes_df.iterrows():
                with st.expander(f"👤 {solicitante['nome']} - {solicitante['departamento']}"):
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.write(f"**Nome:** {solicitante['nome']}")
                        st.write(f"**Departamento:** {solicitante['departamento']}")
                    
                    with col2:
                        st.write(f"**Email:** {solicitante['email'] or 'Não informado'}")
                        st.write(f"**Telefone:** {solicitante['telefone'] or 'Não informado'}")
                    
                    with col3:
                        if st.button(f"✏️ Editar", key=f"edit_sol_{solicitante['id']}"):
                            st.session_state.editando_solicitante = solicitante['id']
                            st.rerun()
                        
                        if st.button(f"🗑️ Excluir", key=f"del_sol_{solicitante['id']}"):
                            # Usar checkbox para confirmação
                            confirm_key = f"confirm_del_{solicitante['id']}"
                            if confirm_key not in st.session_state:
                                st.session_state[confirm_key] = False
                            
                            if not st.session_state[confirm_key]:
                                st.session_state[confirm_key] = True
                                st.warning("⚠️ Clique novamente para confirmar a exclusão!")
                                st.rerun()
                            else:
                                if solicitantes_crud.deletar(solicitante['id'], st.session_state.user_data[0]):
                                    st.success("✅ Solicitante excluído com sucesso!")
                                    # Limpar estado de confirmação
                                    del st.session_state[confirm_key]
                                    st.rerun()
                                else:
                                    st.error("❌ Erro ao excluir solicitante!")
                                    st.session_state[confirm_key] = False
        else:
            st.info("📝 Nenhum solicitante cadastrado.")


def editar_solicitante():
    """Função para editar um solicitante existente"""
    st.title("✏️ Editar Solicitante")
    
    # Botão para voltar
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("← Voltar"):
            if 'editando_solicitante' in st.session_state:
                del st.session_state.editando_solicitante
            st.rerun()
    
    # Buscar dados do solicitante
    solicitante_id = st.session_state.editando_solicitante
    
    # Buscar o solicitante na lista (alternativa se não tiver buscar_por_id)
    try:
        # Tentar usar buscar_por_id se existir
        if hasattr(solicitantes_crud, 'buscar_por_id'):
            solicitante_raw = solicitantes_crud.buscar_por_id(solicitante_id)
        else:
            # Usar a função listar() para encontrar o solicitante
            solicitantes_df = solicitantes_crud.listar()
            solicitante_filtered = solicitantes_df[solicitantes_df['id'] == solicitante_id]
            
            if not solicitante_filtered.empty:
                solicitante_raw = solicitante_filtered.iloc[0].to_dict()
            else:
                solicitante_raw = None
    except Exception as e:
        st.error(f"Erro ao buscar solicitante: {e}")
        solicitante_raw = None
    
    if not solicitante_raw:
        st.error("❌ Solicitante não encontrado!")
        if st.button("Voltar à lista"):
            if 'editando_solicitante' in st.session_state:
                del st.session_state.editando_solicitante
            st.rerun()
        return
    
    # Converter tupla para dicionário se necessário
    if isinstance(solicitante_raw, tuple):
        # Assumindo a ordem: id, nome, email, telefone, departamento, criado_por, data_criacao, data_atualizacao
        solicitante = {
            'id': solicitante_raw[0],
            'nome': solicitante_raw[1],
            'email': solicitante_raw[2],
            'telefone': solicitante_raw[3],
            'departamento': solicitante_raw[4],
            'criado_por': solicitante_raw[5] if len(solicitante_raw) > 5 else None,
            'data_criacao': solicitante_raw[6] if len(solicitante_raw) > 6 else None,
            'data_atualizacao': solicitante_raw[7] if len(solicitante_raw) > 7 else None
        }
    else:
        solicitante = solicitante_raw
    
    st.info(f"📝 Editando: **{solicitante['nome']}**")
    
    # Formulário de edição
    with st.form("editar_solicitante"):
        col1, col2 = st.columns(2)
        
        with col1:
            nome = st.text_input(
                "Nome*", 
                value=solicitante['nome'],
                placeholder="Nome completo"
            )
            email = st.text_input(
                "Email", 
                value=solicitante['email'] or '',
                placeholder="email@exemplo.com"
            )
        
        with col2:
            telefone = st.text_input(
                "Telefone", 
                value=solicitante['telefone'] or '',
                placeholder="(11) 99999-9999"
            )
            departamento = st.text_input(
                "Departamento", 
                value=solicitante['departamento'] or '',
                placeholder="Ex: Recursos Humanos"
            )
        
        # Botões do formulário
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col2:
            cancelar = st.form_submit_button("❌ Cancelar", type="secondary")
        
        with col3:
            salvar = st.form_submit_button("💾 Salvar", type="primary")
        
        # Processar ações do formulário
        if cancelar:
            if 'editando_solicitante' in st.session_state:
                del st.session_state.editando_solicitante
            st.rerun()
        
        if salvar:
            if nome.strip():
                # Verificar se houve mudanças
                dados_originais = {
                    'nome': solicitante['nome'],
                    'email': solicitante['email'] or '',
                    'telefone': solicitante['telefone'] or '',
                    'departamento': solicitante['departamento'] or ''
                }
                
                dados_novos = {
                    'nome': nome.strip(),
                    'email': email.strip(),
                    'telefone': telefone.strip(),
                    'departamento': departamento.strip()
                }
                
                if dados_originais != dados_novos:
                    # Atualizar solicitante
                    try:
                        if hasattr(solicitantes_crud, 'atualizar'):
                            success = solicitantes_crud.atualizar(
                                solicitante_id, 
                                nome.strip(), 
                                email.strip() if email.strip() else None,
                                telefone.strip() if telefone.strip() else None,
                                departamento.strip() if departamento.strip() else None,
                                st.session_state.user_data[0]
                            )
                        else:
                            # Se não tem função atualizar, pode usar a função criar com modificações
                            st.error("❌ Função de atualização não implementada no CRUD!")
                            success = False
                        
                        if success:
                            st.success("✅ Solicitante atualizado com sucesso!")
                            # Limpar estado de edição
                            if 'editando_solicitante' in st.session_state:
                                del st.session_state.editando_solicitante
                            st.rerun()
                        else:
                            st.error("❌ Erro ao atualizar solicitante!")
                    except Exception as e:
                        st.error(f"❌ Erro ao atualizar solicitante: {e}")
                else:
                    st.info("ℹ️ Nenhuma alteração foi feita.")
            else:
                st.error("❌ O nome é obrigatório!")
    
    # Seção de informações adicionais
    st.divider()
    
    with st.expander("📊 Informações do Solicitante"):
        col1, col2 = st.columns(2)
        
        with col1:
            st.write(f"**ID:** {solicitante['id']}")
            st.write(f"**Criado por:** {solicitante.get('criado_por', 'N/A')}")
        
        with col2:
            if 'data_criacao' in solicitante and solicitante['data_criacao']:
                st.write(f"**Data de Criação:** {solicitante['data_criacao']}")
            if 'data_atualizacao' in solicitante and solicitante['data_atualizacao']:
                st.write(f"**Última Atualização:** {solicitante['data_atualizacao']}")
    
    # Seção de estatísticas (se disponível)
    with st.expander("📈 Estatísticas de Solicitações"):
        # Aqui você pode adicionar estatísticas do solicitante
        # Por exemplo, número de solicitações feitas, etc.
        st.info("🚧 Funcionalidade em desenvolvimento - mostrará estatísticas das solicitações deste solicitante.")             

# Gerenciar Usuários (apenas admin)
def gerenciar_usuarios():
    st.title("👨‍💼 Gerenciar Usuários")
    
    if not verificar_permissao("admin"):
        st.error("Acesso negado!")
        return
    
    # Verificar se está editando um usuário
    if 'editando_usuario' in st.session_state and st.session_state.editando_usuario:
        editar_usuario_modal()
        return
    
    tabs = st.tabs(["➕ Novo Usuário", "📋 Lista de Usuários"])
    
    with tabs[0]:
        st.subheader("Criar Novo Usuário")
        
        with st.form("novo_usuario"):
            col1, col2 = st.columns(2)
            
            with col1:
                usuario = st.text_input("Usuário*", placeholder="nome.usuario")
                nome = st.text_input("Nome Completo*", placeholder="Nome completo")
                email = st.text_input("Email", placeholder="email@exemplo.com")
            
            with col2:
                senha = st.text_input("Senha*", type="password")
                confirmar_senha = st.text_input("Confirmar Senha*", type="password")
                nivel_acesso = st.selectbox("Nível de Acesso*", ["usuario", "admin"])
            
            submit = st.form_submit_button("👤 Criar Usuário")
            
            if submit:
                if usuario and nome and senha and confirmar_senha:
                    if senha == confirmar_senha:
                        if auth_manager.criar_usuario(usuario, senha, nome, email, nivel_acesso, st.session_state.user_data[0]):
                            st.success("✅ Usuário criado com sucesso!")
                            st.rerun()
                        else:
                            st.error("❌ Erro ao criar usuário! Usuário já existe.")
                    else:
                        st.error("❌ As senhas não coincidem!")
                else:
                    st.error("❌ Preencha todos os campos obrigatórios!")
    
    with tabs[1]:
        st.subheader("Lista de Usuários")
        
        usuarios_df = auth_manager.listar_usuarios()
        
        if not usuarios_df.empty:
            for _, usuario in usuarios_df.iterrows():
                with st.expander(f"👤 {usuario['nome']} ({usuario['usuario']})"):
                    col1, col2, col3 = st.columns(3)
                    
                    with col1:
                        st.write(f"**Nome:** {usuario['nome']}")
                        st.write(f"**Usuário:** {usuario['usuario']}")
                        st.write(f"**Email:** {usuario['email'] or 'Não informado'}")
                    
                    with col2:
                        st.write(f"**Nível:** {usuario['nivel_acesso'].title()}")
                        st.write(f"**Status:** {'Ativo' if usuario['ativo'] else 'Inativo'}")
                        st.write(f"**Cadastro:** {usuario['data_cadastro'][:10]}")
                        if usuario['ultimo_login']:
                            st.write(f"**Último Login:** {usuario['ultimo_login'][:16]}")
                    
                    with col3:
                        if st.button(f"✏️ Editar", key=f"edit_user_{usuario['id']}"):
                            st.session_state.editando_usuario = usuario['id']
                            st.rerun()
                        
                        if usuario['id'] != st.session_state.user_data[0]:  # Não pode excluir a si mesmo
                            if st.button(f"🗑️ Excluir", key=f"del_user_{usuario['id']}", 
                                       help="Atenção: Esta ação não pode ser desfeita!"):
                                if st.button(f"⚠️ Confirmar Exclusão", key=f"confirm_del_{usuario['id']}"):
                                    if auth_manager.deletar_usuario(usuario['id'], st.session_state.user_data[0]):
                                        st.success("✅ Usuário excluído com sucesso!")
                                        st.rerun()
                                    else:
                                        st.error("❌ Erro ao excluir usuário!")
                        else:
                            st.info("ℹ️ Não é possível excluir seu próprio usuário")
        else:
            st.info("Nenhum usuário cadastrado.")

def editar_usuario_modal():
    """Modal para editar dados do usuário"""
    st.title("✏️ Editar Usuário")
    
    usuario_id = st.session_state.editando_usuario
    
    # Buscar dados do usuário
    conn = db_manager.get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM usuarios WHERE id=?', (usuario_id,))
    usuario_dados = cursor.fetchone()
    conn.close()
    
    if not usuario_dados:
        st.error("Usuário não encontrado!")
        st.session_state.editando_usuario = None
        st.rerun()
        return
    
    # Botão para voltar
    if st.button("← Voltar para Lista de Usuários"):
        st.session_state.editando_usuario = None
        st.rerun()
    
    st.markdown("---")
    
    # Dados atuais do usuário
    id_usuario, usuario, senha, nome, email, nivel_acesso, ativo, data_cadastro, ultimo_login = usuario_dados
    
    # Tabs para diferentes tipos de edição
    tabs = st.tabs(["📝 Dados Básicos", "🔐 Alterar Senha", "🗑️ Gerenciar Acesso"])
    
    with tabs[0]:
        st.subheader("Editar Dados Básicos")
        
        with st.form("editar_usuario"):
            col1, col2 = st.columns(2)
            
            with col1:
                nome_novo = st.text_input("Nome Completo*", value=nome)
                email_novo = st.text_input("Email", value=email or "")
            
            with col2:
                nivel_acesso_novo = st.selectbox("Nível de Acesso*", 
                                               ["usuario", "admin"], 
                                               index=0 if nivel_acesso == "usuario" else 1)
                ativo_novo = st.checkbox("Usuário Ativo", value=bool(ativo))
            
            st.markdown("---")
            col1, col2 = st.columns(2)
            
            with col1:
                if st.form_submit_button("💾 Salvar Alterações"):
                    if nome_novo:
                        if auth_manager.atualizar_usuario(id_usuario, nome_novo, email_novo, 
                                                        nivel_acesso_novo, ativo_novo, 
                                                        st.session_state.user_data[0]):
                            st.success("✅ Dados atualizados com sucesso!")
                            st.session_state.editando_usuario = None
                            st.rerun()
                        else:
                            st.error("❌ Erro ao atualizar usuário!")
                    else:
                        st.error("❌ Nome é obrigatório!")
            
            with col2:
                if st.form_submit_button("❌ Cancelar"):
                    st.session_state.editando_usuario = None
                    st.rerun()
    
    with tabs[1]:
        st.subheader("Alterar Senha")
        
        with st.form("alterar_senha"):
            nova_senha = st.text_input("Nova Senha*", type="password")
            confirmar_nova_senha = st.text_input("Confirmar Nova Senha*", type="password")
            
            st.markdown("---")
            if st.form_submit_button("🔐 Alterar Senha"):
                if nova_senha and confirmar_nova_senha:
                    if nova_senha == confirmar_nova_senha:
                        if len(nova_senha) >= 6:
                            if auth_manager.alterar_senha(id_usuario, nova_senha, st.session_state.user_data[0]):
                                st.success("✅ Senha alterada com sucesso!")
                            else:
                                st.error("❌ Erro ao alterar senha!")
                        else:
                            st.error("❌ A senha deve ter pelo menos 6 caracteres!")
                    else:
                        st.error("❌ As senhas não coincidem!")
                else:
                    st.error("❌ Preencha todos os campos!")
    
    with tabs[2]:
        st.subheader("Informações e Gerenciamento")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.info(f"""
            **Informações do Usuário:**
            - **ID:** {id_usuario}
            - **Usuário:** {usuario}
            - **Data de Cadastro:** {data_cadastro}
            - **Último Login:** {ultimo_login or 'Nunca'}
            """)
        
        with col2:
            st.warning("**Ações Permanentes:**")
            
            # Desativar/Ativar usuário
            if ativo:
                if st.button("🔒 Desativar Usuário", key="desativar_usuario"):
                    if auth_manager.atualizar_usuario(id_usuario, nome, email, 
                                                    nivel_acesso, False, 
                                                    st.session_state.user_data[0]):
                        st.success("✅ Usuário desativado!")
                        st.rerun()
            else:
                if st.button("🔓 Ativar Usuário", key="ativar_usuario"):
                    if auth_manager.atualizar_usuario(id_usuario, nome, email, 
                                                    nivel_acesso, True, 
                                                    st.session_state.user_data[0]):
                        st.success("✅ Usuário ativado!")
                        st.rerun()
            
            # Excluir usuário (não pode excluir a si mesmo)
            if id_usuario != st.session_state.user_data[0]:
                st.markdown("---")
                if st.button("🗑️ Excluir Usuário", key="excluir_usuario_modal"):
                    st.session_state.confirmando_exclusao = True
                
                if st.session_state.get('confirmando_exclusao', False):
                    st.error("⚠️ **ATENÇÃO:** Esta ação não pode ser desfeita!")
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        if st.button("⚠️ CONFIRMAR EXCLUSÃO", key="confirmar_exclusao_final"):
                            if auth_manager.deletar_usuario(id_usuario, st.session_state.user_data[0]):
                                st.success("✅ Usuário excluído com sucesso!")
                                st.session_state.editando_usuario = None
                                st.session_state.confirmando_exclusao = False
                                st.rerun()
                            else:
                                st.error("❌ Erro ao excluir usuário!")
                    
                    with col2:
                        if st.button("❌ Cancelar", key="cancelar_exclusao"):
                            st.session_state.confirmando_exclusao = False
                            st.rerun()
            else:
                st.info("ℹ️ Não é possível excluir seu próprio usuário")

# Interface principal do sistema
def main():
    # Verificar se o usuário está autenticado
    if 'authenticated' not in st.session_state or not st.session_state.authenticated:
        pagina_login()
        return
    
    # Header com informações do usuário
    user_data = st.session_state.user_data
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        st.title("📋 Sistema de Protocolo de Documentos")
    
    with col2:
        st.markdown(f"**Usuário:** {user_data[2]} ({user_data[3].title()})")
    
    with col3:
        if st.button("🚪 Sair"):
            logout()
    
    # Sidebar para navegação
    st.sidebar.title("Navegação")
    
    opcoes_menu = ["Dashboard", "Protocolos", "Solicitantes"]
    
    # Adicionar opções de admin
    if verificar_permissao("admin"):
        opcoes_menu.extend(["Usuários", "Logs do Sistema"])
    
    opcao = st.sidebar.selectbox("Escolha uma opção:", opcoes_menu)
    
    # Roteamento das páginas
    if opcao == "Dashboard":
        dashboard()
    elif opcao == "Protocolos":
        gerenciar_protocolos()
    elif opcao == "Solicitantes":
        gerenciar_solicitantes()
    elif opcao == "Usuários" and verificar_permissao("admin"):
        gerenciar_usuarios()
    elif opcao == "Logs do Sistema" and verificar_permissao("admin"):
        pagina_logs()

if __name__ == "__main__":
    main()