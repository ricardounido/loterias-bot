import os
import logging
from datetime import datetime, timedelta
from collections import Counter
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ========== CONFIGURACIÓN ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')

# ========== CONFIGURACIÓN DE ADMINISTRADORES ==========
ADMIN_IDS = ["123456789"]  # ⚠️ REEMPLAZA CON TU ID DE TELEGRAM

# ========== LOGS ==========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== BASE DE DATOS (SUPABASE) ==========
def get_db():
    """Conecta a Supabase (PostgreSQL)"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    """Crea las tablas en Supabase si no existen"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS resultados (
            id SERIAL PRIMARY KEY,
            loteria TEXT NOT NULL,
            fecha TEXT NOT NULL,
            hora TEXT NOT NULL,
            numero TEXT,
            animal TEXT,
            resultado_completo TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(loteria, fecha, hora)
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_loteria_fecha ON resultados(loteria, fecha)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_animal ON resultados(animal)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fecha ON resultados(fecha)')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            telegram_id TEXT UNIQUE NOT NULL,
            telefono TEXT NOT NULL,
            nombre TEXT,
            suscripcion_activa BOOLEAN DEFAULT FALSE,
            fecha_vencimiento DATE,
            fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Base de datos inicializada en Supabase")

def guardar_resultado(loteria, fecha, hora, resultado_completo):
    """Guarda un resultado en Supabase"""
    conn = get_db()
    cursor = conn.cursor()
    
    numero = None
    animal = None
    if ' - ' in resultado_completo:
        partes = resultado_completo.split(' - ')
        if len(partes) == 2:
            numero = partes[0].strip()
            animal = partes[1].strip()
    else:
        animal = resultado_completo
    
    try:
        cursor.execute('''
            INSERT INTO resultados (loteria, fecha, hora, numero, animal, resultado_completo)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (loteria, fecha, hora) DO NOTHING
        ''', (loteria, fecha, hora, numero, animal, resultado_completo))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"Error guardando: {e}")
        return False
    finally:
        conn.close()

def get_usuario(telegram_id):
    """Obtiene un usuario por su telegram_id"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM usuarios WHERE telegram_id = %s', (telegram_id,))
    usuario = cursor.fetchone()
    conn.close()
    return usuario

def guardar_usuario(telegram_id, telefono, nombre, suscripcion_activa=False, fecha_vencimiento=None):
    """Guarda un usuario en Supabase"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO usuarios (telegram_id, telefono, nombre, suscripcion_activa, fecha_vencimiento)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (telegram_id) DO UPDATE SET
                telefono = EXCLUDED.telefono,
                nombre = EXCLUDED.nombre,
                suscripcion_activa = EXCLUDED.suscripcion_activa,
                fecha_vencimiento = EXCLUDED.fecha_vencimiento
        ''', (telegram_id, telefono, nombre, suscripcion_activa, fecha_vencimiento))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error guardando usuario: {e}")
        return False
    finally:
        conn.close()

def actualizar_usuario(telegram_id, **kwargs):
    """Actualiza un usuario en Supabase"""
    conn = get_db()
    cursor = conn.cursor()
    
    set_clause = ", ".join([f"{key} = %s" for key in kwargs.keys()])
    values = list(kwargs.values()) + [telegram_id]
    
    cursor.execute(f'''
        UPDATE usuarios 
        SET {set_clause}
        WHERE telegram_id = %s
    ''', values)
    
    conn.commit()
    conn.close()

def listar_usuarios(limit=50):
    """Lista los últimos usuarios registrados"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, telegram_id, telefono, nombre, suscripcion_activa, fecha_vencimiento, fecha_registro
        FROM usuarios
        ORDER BY id DESC
        LIMIT %s
    ''', (limit,))
    usuarios = cursor.fetchall()
    conn.close()
    return usuarios

def contar_usuarios():
    """Cuenta el total de usuarios"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as total FROM usuarios')
    total = cursor.fetchone()['total']
    conn.close()
    return total

def contar_usuarios_activos():
    """Cuenta los usuarios con suscripción activa"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as total FROM usuarios WHERE suscripcion_activa = TRUE')
    total = cursor.fetchone()['total']
    conn.close()
    return total

def contar_resultados():
    """Cuenta el total de resultados guardados"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) as total FROM resultados')
    total = cursor.fetchone()['total']
    conn.close()
    return total

def get_loterias():
    """Obtiene todas las loterías disponibles"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT loteria FROM resultados ORDER BY loteria')
    loterias = [row['loteria'] for row in cursor.fetchall()]
    conn.close()
    return loterias

def get_frecuentes(loteria, top=10):
    """Obtiene los animales más frecuentes de una lotería"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT animal, COUNT(*) as total
        FROM resultados
        WHERE loteria = %s AND animal IS NOT NULL
        GROUP BY animal
        ORDER BY total DESC
        LIMIT %s
    ''', (loteria, top))
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def get_atrasados(loteria, top=10):
    """Obtiene los animales más atrasados de una lotería"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT animal, MAX(fecha) as ultima_aparicion
        FROM resultados
        WHERE loteria = %s AND animal IS NOT NULL
        GROUP BY animal
        ORDER BY ultima_aparicion ASC
        LIMIT %s
    ''', (loteria, top))
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def get_ultimos_resultados(loteria, limit=15):
    """Obtiene los últimos resultados de una lotería"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT fecha, hora, resultado_completo
        FROM resultados
        WHERE loteria = %s AND resultado_completo IS NOT NULL
        ORDER BY fecha DESC, hora DESC
        LIMIT %s
    ''', (loteria, limit))
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def get_todos_resultados(loteria):
    """Obtiene todos los resultados de una lotería (para cálculos de poder)"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT animal, fecha
        FROM resultados
        WHERE loteria = %s AND animal IS NOT NULL
    ''', (loteria,))
    resultados = cursor.fetchall()
    conn.close()
    return resultados

# ========== DECORADOR DE SEGURIDAD ==========
def solo_admin(func):
    """Decorador que SOLO permite ejecutar la función si el usuario es administrador"""
    @wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = str(update.effective_user.id)
        
        if user_id not in ADMIN_IDS:
            await update.message.reply_text(
                "🚫 *Acceso Denegado*\n\n"
                "No tienes permisos de administrador.",
                parse_mode='Markdown'
            )
            return None
        
        return await func(self, update, context, *args, **kwargs)
    return wrapper

# ========== BOT ==========
class BotLoterias:
    def __init__(self):
        self.castigo_ayer = 0.4
        self.castigo_anteayer = 0.6
        self.codigos_activacion = {}
    
    def calcular_poder(self, contador, retrasos, ultima_aparicion):
        resultados = []
        if not contador:
            return resultados
        
        hoy = datetime.now().date()
        max_freq = max(contador.values())
        max_retraso = max([d for d in retrasos.values() if d < 999], default=1)
        
        for animal in contador.keys():
            freq = contador[animal]
            retraso = retrasos.get(animal, 0)
            ultima = ultima_aparicion.get(animal)
            
            if ultima:
                ultima_date = datetime.strptime(ultima, '%Y-%m-%d').date()
                dias_desde_ultima = (hoy - ultima_date).days
            else:
                dias_desde_ultima = 999
            
            if dias_desde_ultima == 0:
                factor = 0.0
                estado = "🔴 HOY"
            elif dias_desde_ultima == 1:
                factor = self.castigo_ayer
                estado = f"🟠 AYER ({int(self.castigo_ayer*100)}%)"
            elif dias_desde_ultima == 2:
                factor = self.castigo_anteayer
                estado = f"🟡 ANTEAYER ({int(self.castigo_anteayer*100)}%)"
            else:
                factor = 1.0
                estado = "✅ FRESCO"
            
            freq_norm = freq / max_freq
            retraso_norm = min(retraso / max_retraso, 1.0) if retraso < 999 else 1.0
            
            poder_base = (freq_norm * 0.6) + (retraso_norm * 0.4)
            poder_ajustado = poder_base * factor
            
            resultados.append({
                'animal': animal,
                'frecuencia': freq,
                'dias_atraso': retraso if retraso < 999 else None,
                'dias_desde_ultima': dias_desde_ultima,
                'estado': estado,
                'poder': round(poder_ajustado, 4),
                'factor_enfriamiento': factor
            })
        
        return sorted(resultados, key=lambda x: x['poder'], reverse=True)
    
    # ========== COMANDOS PÚBLICOS ==========
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        usuario = get_usuario(user_id)
        
        if usuario and usuario['suscripcion_activa']:
            if usuario['fecha_vencimiento']:
                venc = datetime.strptime(usuario['fecha_vencimiento'], '%Y-%m-%d').date()
                if venc >= datetime.now().date():
                    await self.mostrar_menu(update, context)
                    return
        
        keyboard = [[InlineKeyboardButton("📱 Contactar soporte", url="https://t.me/[TU_USUARIO]")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔐 *Acceso Restringido*\n\n"
            "Para usar este bot necesitas una suscripción activa.\n\n"
            "📲 Contacta a @[TU_USUARIO] para activar tu suscripción.\n"
            "💰 Costo: $5 USD / mes\n\n"
            "⚠️ Las loterías son juegos de azar. Esta herramienta es solo estadística.",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def mostrar_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🔥 Más Frecuentes", callback_data='frecuentes')],
            [InlineKeyboardButton("❄️ Más Atrasados", callback_data='atrasados')],
            [InlineKeyboardButton("⚡ Poder Estadístico", callback_data='poder')],
            [InlineKeyboardButton("📅 Análisis por Día", callback_data='por_dia')],
            [InlineKeyboardButton("🔄 Últimos Resultados", callback_data='ultimos')],
            [InlineKeyboardButton("🔄 Actualizar Datos", callback_data='actualizar')],
            [InlineKeyboardButton("ℹ️ Mi Suscripción", callback_data='suscripcion')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        mensaje = (
            "🏠 *Menú Principal*\n\n"
            "🎯 Selecciona una opción para ver estadísticas.\n\n"
            f"⚙️ *Configuración:*\n"
            f"• Castigo AYER: {int(self.castigo_ayer*100)}%\n"
            f"• Castigo ANTEAYER: {int(self.castigo_anteayer*100)}%"
        )
        
        await update.message.reply_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def registrar_usuario(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        username = update.effective_user.username or "Sin nombre"
        
        usuario = get_usuario(user_id)
        
        if usuario:
            await update.message.reply_text(
                "ℹ️ *Ya estás registrado*\n\n"
                "Si no tienes suscripción activa, contacta al administrador.",
                parse_mode='Markdown'
            )
            return
        
        guardar_usuario(user_id, "Pendiente", username, False)
        
        await update.message.reply_text(
            "✅ *Registro completado*\n\n"
            "Ahora necesitas activar tu suscripción.\n\n"
            "Opciones:\n"
            "1. Si tienes un código de activación: `/canjear CODIGO`\n"
            "2. Contacta al administrador: @[TU_USUARIO]",
            parse_mode='Markdown'
        )
    
    async def canjear_codigo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        
        if not context.args or len(context.args) == 0:
            await update.message.reply_text(
                "❌ *Uso incorrecto*\n\n"
                "Ejemplo: `/canjear 123456`",
                parse_mode='Markdown'
            )
            return
        
        codigo = context.args[0].strip()
        
        if codigo not in self.codigos_activacion:
            await update.message.reply_text(
                "❌ *Código inválido*\n\n"
                "El código no es válido o ya expiró.",
                parse_mode='Markdown'
            )
            return
        
        info = self.codigos_activacion[codigo]
        
        if info['usado']:
            await update.message.reply_text("❌ *Código ya utilizado*", parse_mode='Markdown')
            return
        
        if (datetime.now() - info['creado']).total_seconds() > 86400:
            await update.message.reply_text("❌ *Código expirado*", parse_mode='Markdown')
            del self.codigos_activacion[codigo]
            return
        
        dias = info['dias']
        fecha_vencimiento = (datetime.now() + timedelta(days=dias)).strftime('%Y-%m-%d')
        
        usuario = get_usuario(user_id)
        if not usuario:
            username = update.effective_user.username or "Sin nombre"
            guardar_usuario(user_id, "Pendiente", username, True, fecha_vencimiento)
        else:
            actualizar_usuario(user_id, suscripcion_activa=True, fecha_vencimiento=fecha_vencimiento)
        
        self.codigos_activacion[codigo]['usado'] = True
        
        await update.message.reply_text(
            f"✅ *¡Suscripción Activada!*\n\n"
            f"📅 Válida hasta: *{fecha_vencimiento}*\n\n"
            f"Envía `/start` para comenzar.",
            parse_mode='Markdown'
        )
    
    async def mi_suscripcion(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        usuario = get_usuario(user_id)
        
        if not usuario:
            await update.message.reply_text(
                "❌ *No estás registrado*\n\n"
                "Usa `/registrar` para registrarte.",
                parse_mode='Markdown'
            )
            return
        
        estado = "✅ Activa" if usuario['suscripcion_activa'] else "❌ Inactiva"
        vence = usuario['fecha_vencimiento'] or "No definida"
        
        mensaje = (
            "ℹ️ *Mi Suscripción*\n\n"
            f"📱 Teléfono: {usuario['telefono']}\n"
            f"👤 Nombre: {usuario['nombre'] or 'Sin nombre'}\n"
            f"📅 Registro: {usuario['fecha_registro'][:10]}\n"
            f"📊 Estado: {estado}\n"
            f"📅 Vence: {vence}\n\n"
        )
        
        if not usuario['suscripcion_activa']:
            mensaje += "💡 *¿Cómo activar?*\n"
            mensaje += "1. Contacta al administrador @[TU_USUARIO]\n"
            mensaje += "2. Si tienes código: `/canjear CODIGO`"
        
        await update.message.reply_text(mensaje, parse_mode='Markdown')
    
    # ========== COMANDOS DE ADMINISTRACIÓN ==========
    
    @solo_admin
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("👥 Listar usuarios", callback_data='admin_listar')],
            [InlineKeyboardButton("➕ Activar usuario", callback_data='admin_activar')],
            [InlineKeyboardButton("❌ Desactivar usuario", callback_data='admin_desactivar')],
            [InlineKeyboardButton("📊 Estadísticas", callback_data='admin_stats')],
            [InlineKeyboardButton("🔑 Generar código", callback_data='admin_codigo')],
            [InlineKeyboardButton("📅 Extender suscripción", callback_data='admin_extender')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "🔐 *Panel de Administración*\n\n"
            "Selecciona una opción:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    @solo_admin
    async def admin_listar_usuarios(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        usuarios = listar_usuarios()
        
        if not usuarios:
            await query.edit_message_text("📭 No hay usuarios registrados.")
            return
        
        mensaje = "👥 *LISTA DE USUARIOS*\n\n"
        for u in usuarios:
            estado = "✅ Activo" if u['suscripcion_activa'] else "❌ Inactivo"
            vence = u['fecha_vencimiento'] or "N/A"
            mensaje += f"*ID:* `{u['telegram_id']}`\n"
            mensaje += f"📱 {u['telefono']} - {u['nombre'] or 'Sin nombre'}\n"
            mensaje += f"📅 Vence: {vence} - {estado}\n"
            mensaje += f"📆 Registro: {u['fecha_registro'][:10]}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='admin_volver')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(mensaje[:4000], reply_markup=reply_markup, parse_mode='Markdown')
    
    @solo_admin
    async def admin_activar_usuario(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        context.user_data['admin_accion'] = 'activar'
        await query.edit_message_text(
            "📝 *Activar Usuario*\n\n"
            "Envía el *ID de Telegram* del usuario que deseas activar.\n\n"
            "Ejemplo: `123456789`",
            parse_mode='Markdown'
        )
    
    @solo_admin
    async def admin_desactivar_usuario(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        context.user_data['admin_accion'] = 'desactivar'
        await query.edit_message_text(
            "📝 *Desactivar Usuario*\n\n"
            "Envía el *ID de Telegram* del usuario que deseas desactivar.",
            parse_mode='Markdown'
        )
    
    @solo_admin
    async def admin_extender_suscripcion(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        context.user_data['admin_accion'] = 'extender'
        await query.edit_message_text(
            "📝 *Extender Suscripción*\n\n"
            "Formato: `ID DIAS`\n"
            "Ejemplo: `123456789 30`",
            parse_mode='Markdown'
        )
    
    @solo_admin
    async def admin_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        total_usuarios = contar_usuarios()
        activos = contar_usuarios_activos()
        total_resultados = contar_resultados()
        loterias = get_loterias()
        
        mensaje = (
            "📊 *ESTADÍSTICAS DEL SISTEMA*\n\n"
            f"👥 *Usuarios:*\n"
            f"├── Total: {total_usuarios}\n"
            f"└── Activos: {activos}\n\n"
            f"🎲 *Datos:*\n"
            f"├── Resultados: {total_resultados}\n"
            f"└── Loterías: {len(loterias)}\n"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='admin_volver')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    
    @solo_admin
    async def admin_generar_codigo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        import random
        codigo = f"{random.randint(100000, 999999)}"
        dias = 30
        
        self.codigos_activacion[codigo] = {
            'dias': dias,
            'creado': datetime.now(),
            'usado': False,
            'generado_por': str(update.effective_user.id)
        }
        
        await query.edit_message_text(
            f"🔑 *Código de Activación Generado*\n\n"
            f"📋 *Código:* `{codigo}`\n"
            f"📅 *Días:* {dias}\n"
            f"⏰ *Válido por:* 24 horas\n\n"
            f"📤 *Envía este código al usuario:*\n"
            f"`{codigo}`\n\n"
            f"El usuario debe usar: `/canjear {codigo}`",
            parse_mode='Markdown'
        )
    
    @solo_admin
    async def admin_volver(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        keyboard = [
            [InlineKeyboardButton("👥 Listar usuarios", callback_data='admin_listar')],
            [InlineKeyboardButton("➕ Activar usuario", callback_data='admin_activar')],
            [InlineKeyboardButton("❌ Desactivar usuario", callback_data='admin_desactivar')],
            [InlineKeyboardButton("📊 Estadísticas", callback_data='admin_stats')],
            [InlineKeyboardButton("🔑 Generar código", callback_data='admin_codigo')],
            [InlineKeyboardButton("📅 Extender suscripción", callback_data='admin_extender')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔐 *Panel de Administración*\n\n"
            "Selecciona una opción:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    # ========== MANEJADOR DE MENSAJES DE ADMIN ==========
    
    async def handle_admin_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        
        if user_id not in ADMIN_IDS:
            return
        
        accion = context.user_data.get('admin_accion')
        if not accion:
            return
        
        texto = update.message.text.strip()
        
        if accion == 'activar':
            target_id = texto
            try:
                usuario = get_usuario(target_id)
                if not usuario:
                    await update.message.reply_text(f"❌ Usuario `{target_id}` no encontrado.", parse_mode='Markdown')
                else:
                    fecha_vencimiento = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
                    actualizar_usuario(target_id, suscripcion_activa=True, fecha_vencimiento=fecha_vencimiento)
                    await update.message.reply_text(
                        f"✅ Usuario `{target_id}` activado. Vence: *{fecha_vencimiento}*",
                        parse_mode='Markdown'
                    )
            except Exception as e:
                await update.message.reply_text(f"❌ Error: {str(e)}")
            context.user_data['admin_accion'] = None
        
        elif accion == 'desactivar':
            target_id = texto
            try:
                usuario = get_usuario(target_id)
                if not usuario:
                    await update.message.reply_text(f"❌ Usuario `{target_id}` no encontrado.", parse_mode='Markdown')
                else:
                    actualizar_usuario(target_id, suscripcion_activa=False)
                    await update.message.reply_text(f"✅ Usuario `{target_id}` desactivado.", parse_mode='Markdown')
            except Exception as e:
                await update.message.reply_text(f"❌ Error: {str(e)}")
            context.user_data['admin_accion'] = None
        
        elif accion == 'extender':
            partes = texto.split()
            if len(partes) != 2:
                await update.message.reply_text(
                    "❌ *Formato incorrecto*\n\n"
                    "Ejemplo: `123456789 30`",
                    parse_mode='Markdown'
                )
                return
            
            target_id, dias = partes
            try:
                dias = int(dias)
                if dias <= 0:
                    raise ValueError("Días debe ser positivo")
                
                usuario = get_usuario(target_id)
                if not usuario:
                    await update.message.reply_text(f"❌ Usuario `{target_id}` no encontrado.", parse_mode='Markdown')
                else:
                    if usuario['fecha_vencimiento']:
                        fecha_actual = datetime.strptime(usuario['fecha_vencimiento'], '%Y-%m-%d')
                        nueva_fecha = fecha_actual + timedelta(days=dias)
                    else:
                        nueva_fecha = datetime.now() + timedelta(days=dias)
                    
                    fecha_str = nueva_fecha.strftime('%Y-%m-%d')
                    actualizar_usuario(target_id, suscripcion_activa=True, fecha_vencimiento=fecha_str)
                    await update.message.reply_text(
                        f"✅ Suscripción extendida. Vence: *{fecha_str}*",
                        parse_mode='Markdown'
                    )
            except ValueError:
                await update.message.reply_text(
                    "❌ *Formato incorrecto*\n\n"
                    "El segundo valor debe ser un número de días válido.",
                    parse_mode='Markdown'
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Error: {str(e)}")
            context.user_data['admin_accion'] = None
    
    # ========== CALLBACKS ==========
    
    async def mostrar_loterias(self, update: Update, context: ContextTypes.DEFAULT_TYPE, estadistica: str):
        query = update.callback_query
        await query.answer()
        
        loterias = get_loterias()
        if not loterias:
            await query.edit_message_text("📭 No hay loterías disponibles.")
            return
        
        keyboard = []
        for loteria in loterias[:10]:
            nombre_corto = loteria[:30] + "..." if len(loteria) > 30 else loteria
            keyboard.append([InlineKeyboardButton(
                f"🎲 {nombre_corto}",
                callback_data=f"{estadistica}|{loteria}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Volver al menú", callback_data='menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📊 *Selecciona una lotería:*",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def mostrar_frecuentes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        _, loteria = query.data.split('|')
        data = get_frecuentes(loteria)
        
        if not data:
            await query.edit_message_text(f"📊 *{loteria}*\n\nNo hay datos suficientes.", parse_mode='Markdown')
            return
        
        mensaje = f"🔥 *TOP 10 MÁS FRECUENTES*\n🎯 *{loteria}*\n\n"
        for i, row in enumerate(data, 1):
            emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
            mensaje += f"{emoji} *{row['animal']}* → {row['total']} veces\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def mostrar_atrasados(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        _, loteria = query.data.split('|')
        data = get_atrasados(loteria)
        
        if not data:
            await query.edit_message_text(f"📊 *{loteria}*\n\nNo hay datos suficientes.", parse_mode='Markdown')
            return
        
        hoy = datetime.now().date()
        mensaje = f"⏳ *TOP 10 MÁS ATRASADOS*\n🎯 *{loteria}*\n\n"
        
        for i, row in enumerate(data, 1):
            ultima = datetime.strptime(row['ultima_aparicion'], '%Y-%m-%d').date()
            dias = (hoy - ultima).days
            emoji = "🔴" if dias > 15 else "🟠" if dias > 7 else "🟡"
            mensaje += f"{i}. *{row['animal']}* → {emoji} {dias} días\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def mostrar_poder(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        _, loteria = query.data.split('|')
        rows = get_todos_resultados(loteria)
        
        if not rows:
            await query.edit_message_text(f"📊 *{loteria}*\n\nNo hay datos suficientes.", parse_mode='Markdown')
            return
        
        contador = Counter()
        ultima_aparicion = {}
        for row in rows:
            animal = row['animal']
            fecha = row['fecha']
            contador[animal] += 1
            if animal not in ultima_aparicion:
                ultima_aparicion[animal] = fecha
        
        hoy = datetime.now().date()
        retrasos = {}
        for animal in contador:
            ultima = ultima_aparicion.get(animal)
            if ultima:
                ultima_date = datetime.strptime(ultima, '%Y-%m-%d').date()
                retrasos[animal] = (hoy - ultima_date).days
            else:
                retrasos[animal] = 999
        
        poder = self.calcular_poder(contador, retrasos, ultima_aparicion)[:10]
        
        mensaje = f"⚡ *PODER ESTADÍSTICO*\n🎯 *{loteria}*\n\n"
        
        for i, item in enumerate(poder, 1):
            poder_pct = item['poder'] * 100
            barra = "▓" * int(poder_pct / 10) + "░" * (10 - int(poder_pct / 10))
            emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            mensaje += f"{emoji} *{item['animal']}*\n"
            mensaje += f"   ├── {barra} {poder_pct:.0f}%\n"
            mensaje += f"   ├── Frecuencia: {item['frecuencia']} veces\n"
            mensaje += f"   └── Estado: {item['estado']}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def mostrar_ultimos(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        _, loteria = query.data.split('|')
        data = get_ultimos_resultados(loteria)
        
        if not data:
            await query.edit_message_text(f"📊 *{loteria}*\n\nNo hay resultados recientes.", parse_mode='Markdown')
            return
        
        mensaje = f"📈 *ÚLTIMOS 15 RESULTADOS*\n🎯 *{loteria}*\n\n"
        for row in data:
            fecha = datetime.strptime(row['fecha'], '%Y-%m-%d').strftime('%d/%m')
            mensaje += f"📅 {fecha} {row['hora']} → {row['resultado_completo']}\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def actualizar_datos(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text("⏳ *Actualizando datos...*\nEsto puede tomar unos segundos.", parse_mode='Markdown')
        
        try:
            from scraper import ejecutar_scraper
            total = ejecutar_scraper()
            await query.edit_message_text(
                f"✅ *Datos actualizados correctamente!*\n\n"
                f"📥 {total} nuevos resultados guardados.",
                parse_mode='Markdown'
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Error al actualizar: {str(e)}", parse_mode='Markdown')
    
    async def mostrar_suscripcion(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = str(update.effective_user.id)
        usuario = get_usuario(user_id)
        
        if not usuario:
            await query.edit_message_text(
                "❌ *No estás registrado*\n\n"
                "Usa `/registrar` para registrarte.",
                parse_mode='Markdown'
            )
            return
        
        estado = "✅ Activa" if usuario['suscripcion_activa'] else "❌ Inactiva"
        vence = usuario['fecha_vencimiento'] or "No definida"
        
        mensaje = (
            "ℹ️ *Mi Suscripción*\n\n"
            f"📱 Teléfono: {usuario['telefono']}\n"
            f"👤 Nombre: {usuario['nombre'] or 'Sin nombre'}\n"
            f"📅 Registro: {usuario['fecha_registro'][:10]}\n"
            f"📊 Estado: {estado}\n"
            f"📅 Vence: {vence}\n\n"
        )
        
        if not usuario['suscripcion_activa']:
            mensaje += "💡 Contacta al administrador @[TU_USUARIO] para activar."
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    
    # ========== CALLBACK HANDLER PRINCIPAL ==========
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        
        if data == 'menu':
            await self.mostrar_menu(update, context)
        elif data == 'frecuentes':
            await self.mostrar_loterias(update, context, 'frecuentes')
        elif data == 'atrasados':
            await self.mostrar_loterias(update, context, 'atrasados')
        elif data == 'poder':
            await self.mostrar_loterias(update, context, 'poder')
        elif data == 'por_dia':
            await query.answer("🛠️ Funcionalidad en desarrollo")
        elif data == 'ultimos':
            await self.mostrar_loterias(update, context, 'ultimos')
        elif data == 'actualizar':
            await self.actualizar_datos(update, context)
        elif data == 'suscripcion':
            await self.mostrar_suscripcion(update, context)
        elif data.startswith('frecuentes|'):
            await self.mostrar_frecuentes(update, context)
        elif data.startswith('atrasados|'):
            await self.mostrar_atrasados(update, context)
        elif data.startswith('poder|'):
            await self.mostrar_poder(update, context)
        elif data.startswith('ultimos|'):
            await self.mostrar_ultimos(update, context)
        elif data.startswith('admin_'):
            # Manejar callbacks de administración
            if data == 'admin_listar':
                await self.admin_listar_usuarios(update, context)
            elif data == 'admin_activar':
                await self.admin_activar_usuario(update, context)
            elif data == 'admin_desactivar':
                await self.admin_desactivar_usuario(update, context)
            elif data == 'admin_stats':
                await self.admin_stats(update, context)
            elif data == 'admin_codigo':
                await self.admin_generar_codigo(update, context)
            elif data == 'admin_extender':
                await self.admin_extender_suscripcion(update, context)
            elif data == 'admin_volver':
                await self.admin_volver(update, context)
    
    # ========== RUN ==========
    
    def run(self):
        init_db()
        app = Application.builder().token(BOT_TOKEN).build()
        
        # Comandos públicos
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("registrar", self.registrar_usuario))
        app.add_handler(CommandHandler("canjear", self.canjear_codigo))
        app.add_handler(CommandHandler("misuscripcion", self.mi_suscripcion))
        
        # Comandos de administración
        app.add_handler(CommandHandler("admin", self.admin_panel))
        
        # Callbacks
        app.add_handler(CallbackQueryHandler(self.callback_handler))
        
        # Mensajes de texto (para administración)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_admin_message))
        
        app.run_polling()

if __name__ == '__main__':
    bot = BotLoterias()
    bot.run()