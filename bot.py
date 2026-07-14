import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import sqlite3
from datetime import datetime, timedelta
from collections import Counter
import logging
import time
from threading import Thread

# ========== CONFIGURACIÓN ==========
BOT_TOKEN = os.environ.get('BOT_TOKEN')
DB_PATH = 'loteria.db'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== BASE DE DATOS ==========
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS resultados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loteria TEXT NOT NULL,
            fecha TEXT NOT NULL,
            hora TEXT NOT NULL,
            numero TEXT,
            animal TEXT,
            resultado_completo TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(loteria, fecha, hora)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE NOT NULL,
            telefono TEXT UNIQUE NOT NULL,
            nombre TEXT,
            suscripcion_activa BOOLEAN DEFAULT 0,
            fecha_vencimiento DATE,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# ========== FUNCIONES DEL SCRAPER ==========
def ejecutar_scraper():
    """Ejecuta el scraper y devuelve el total de resultados guardados"""
    try:
        from scraper import ejecutar_scraper as scraper_main
        return scraper_main()
    except Exception as e:
        logger.error(f"Error en scraper: {e}")
        return 0

# ========== BOT ==========
class BotLoterias:
    def __init__(self):
        self.castigo_ayer = 0.4
        self.castigo_anteayer = 0.6
    
    def get_loterias(self):
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT loteria FROM resultados ORDER BY loteria')
        loterias = [row['loteria'] for row in cursor.fetchall()]
        conn.close()
        return loterias if loterias else ["El Guacharito Millonario", "La Granjita", "Lotto Activo", "Ruleta Royal"]
    
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
    
    # ========== COMANDOS DEL BOT ==========
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT suscripcion_activa, fecha_vencimiento FROM usuarios WHERE telegram_id = ?', (user_id,))
        usuario = cursor.fetchone()
        conn.close()
        
        if usuario and usuario['suscripcion_activa'] == 1:
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
    
    async def mostrar_loterias(self, update: Update, context: ContextTypes.DEFAULT_TYPE, estadistica: str):
        query = update.callback_query
        await query.answer()
        
        loterias = self.get_loterias()
        
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
            f"📊 *Selecciona una lotería:*",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def mostrar_frecuentes(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        _, loteria = query.data.split('|')
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT animal, COUNT(*) as total
            FROM resultados
            WHERE loteria = ? AND animal IS NOT NULL
            GROUP BY animal
            ORDER BY total DESC
            LIMIT 10
        ''', (loteria,))
        
        data = cursor.fetchall()
        conn.close()
        
        if not data:
            await query.edit_message_text(f"📊 *{loteria}*\n\nNo hay datos suficientes.")
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
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT animal, MAX(fecha) as ultima_aparicion
            FROM resultados
            WHERE loteria = ? AND animal IS NOT NULL
            GROUP BY animal
            ORDER BY ultima_aparicion ASC
            LIMIT 10
        ''', (loteria,))
        
        data = cursor.fetchall()
        conn.close()
        
        if not data:
            await query.edit_message_text(f"📊 *{loteria}*\n\nNo hay datos suficientes.")
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
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT animal, fecha
            FROM resultados
            WHERE loteria = ? AND animal IS NOT NULL
        ''', (loteria,))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            await query.edit_message_text(f"📊 *{loteria}*\n\nNo hay datos suficientes.")
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
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fecha, hora, resultado_completo
            FROM resultados
            WHERE loteria = ? AND resultado_completo IS NOT NULL
            ORDER BY fecha DESC, hora DESC
            LIMIT 15
        ''', (loteria,))
        
        data = cursor.fetchall()
        conn.close()
        
        if not data:
            await query.edit_message_text(f"📊 *{loteria}*\n\nNo hay resultados recientes.")
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
        
        # Ejecutar scraper en un hilo separado
        def run_scraper():
            try:
                from scraper import ejecutar_scraper
                return ejecutar_scraper()
            except Exception as e:
                logger.error(f"Error en scraper: {e}")
                return 0
        
        import threading
        thread = threading.Thread(target=run_scraper)
        thread.start()
        thread.join(timeout=60)
        
        await query.edit_message_text(
            "✅ *Datos actualizados correctamente!*\n\n"
            "Los nuevos resultados ya están disponibles.",
            parse_mode='Markdown'
        )
    
    async def mostrar_suscripcion(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = str(update.effective_user.id)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT telefono, suscripcion_activa, fecha_vencimiento FROM usuarios WHERE telegram_id = ?', (user_id,))
        usuario = cursor.fetchone()
        conn.close()
        
        if usuario and usuario['suscripcion_activa'] == 1:
            mensaje = (
                "✅ *Suscripción Activa*\n\n"
                f"📱 Teléfono: {usuario['telefono']}\n"
                f"📅 Vence: {usuario['fecha_vencimiento']}\n\n"
                "🔔 *Beneficios:*\n"
                "• Estadísticas en tiempo real\n"
                "• Top 10 más frecuentes\n"
                "• Top 10 más atrasados\n"
                "• Poder estadístico\n"
                "• Últimos resultados"
            )
        else:
            mensaje = (
                "❌ *Suscripción Inactiva*\n\n"
                "Tu suscripción no está activa.\n"
                "Contacta a @[TU_USUARIO] para activarla."
            )
        
        keyboard = [[InlineKeyboardButton("🔙 Volver", callback_data='menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    
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
    
    def run(self):
        init_db()
        app = Application.builder().token(BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CallbackQueryHandler(self.callback_handler))
        app.run_polling()

if __name__ == '__main__':
    bot = BotLoterias()
    bot.run()