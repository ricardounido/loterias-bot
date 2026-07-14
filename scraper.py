import requests
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime, timedelta
import re
import os
import time

# ========== CONFIGURACIÓN ==========
DB_PATH = 'loteria.db'
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

HORARIOS_SORTEOS = [
    "08:30 AM", "09:30 AM", "10:30 AM", "11:30 AM",
    "12:30 PM", "01:30 PM", "02:30 PM", "03:30 PM",
    "04:30 PM", "05:30 PM", "06:30 PM", "07:30 PM"
]

# ========== BASE DE DATOS ==========
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Tabla de resultados
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
    
    # Índices
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_loteria_fecha ON resultados(loteria, fecha)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_animal ON resultados(animal)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_fecha ON resultados(fecha)')
    
    # Tabla de usuarios
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE NOT NULL,
            telefono TEXT UNIQUE NOT NULL,
            nombre TEXT,
            suscripcion_activa BOOLEAN DEFAULT 0,
            fecha_vencimiento DATE,
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
            ultimo_acceso DATETIME
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Base de datos inicializada")

def guardar_resultado(loteria, fecha, hora, resultado_completo):
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
            INSERT OR IGNORE INTO resultados 
            (loteria, fecha, hora, numero, animal, resultado_completo)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (loteria, fecha, hora, numero, animal, resultado_completo))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"  ❌ Error guardando: {e}")
        return False
    finally:
        conn.close()

# ========== SCRAPING ==========
def obtener_loterias():
    """Extrae todas las loterías disponibles de la página"""
    url = "https://www.tuazar.com/loteria/animalitos/resultados/semana/"
    try:
        print(f"  🌐 Obteniendo loterías de {url}")
        res = requests.get(url, headers=HEADERS, timeout=15)
        print(f"  📡 Status: {res.status_code}")
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            tablas = soup.find_all('table')
            lista = set()
            for t in tablas:
                previo = t.find_previous(['h2', 'h3', 'div', 'strong'])
                nombre = previo.get_text().strip() if previo else ""
                if len(nombre) < 50 and "compartir" not in nombre.lower() and nombre != "":
                    lista.add(nombre)
            return sorted(list(lista))
    except Exception as e:
        print(f"  ❌ Error: {e}")
    
    return ["El Guacharito Millonario", "La Granjita", "Lotto Activo", "Ruleta Royal"]

def extraer_fecha_desde_texto(texto):
    patron = r'(\d{2}/\d{2}/\d{4})'
    match = re.search(patron, texto)
    if match:
        return datetime.strptime(match.group(1), "%d/%m/%Y")
    return None

def procesar_tabla(soup, loteria, fecha_inicio_semana):
    tablas = soup.find_all('table')
    
    tabla_obj = None
    for t in tablas:
        previo = t.find_previous(['h2', 'h3', 'div', 'strong'])
        if previo and loteria.lower() in previo.get_text().strip().lower():
            tabla_obj = t
            break
    
    if not tabla_obj:
        return 0
    
    filas_datos = []
    for fila in tabla_obj.find_all('tr')[1:]:
        celdas = [td.get_text().strip() for td in fila.find_all('td')]
        if celdas and celdas[0] and "horario" not in celdas[0].lower():
            filas_datos.append(celdas)
    
    if not filas_datos:
        return 0
    
    guardados = 0
    for d_idx in range(7):
        fecha_str = (fecha_inicio_semana + timedelta(days=d_idx)).strftime("%Y-%m-%d")
        
        for hora_idx, hora_real in enumerate(HORARIOS_SORTEOS):
            if hora_idx < len(filas_datos) and d_idx < len(filas_datos[hora_idx]):
                valor = filas_datos[hora_idx][d_idx]
                if valor and valor not in ["-", "Animalito", "Animalito A", "Animalito B", "Animalito C", ""]:
                    lineas = [l.strip() for l in valor.split("\n") if l.strip()]
                    if lineas:
                        partes = lineas[0].split(maxsplit=1)
                        if len(partes) == 2:
                            resultado_formateado = f"{partes[0].zfill(2)} - {partes[1]}"
                        else:
                            resultado_formateado = lineas[0]
                        
                        if guardar_resultado(loteria, fecha_str, hora_real, resultado_formateado):
                            guardados += 1
    
    return guardados

def sincronizar_semana_actual(loteria):
    url = "https://www.tuazar.com/loteria/animalitos/resultados/semana/"
    
    try:
        print(f"  🌐 Conectando a {url}")
        res = requests.get(url, headers=HEADERS, timeout=15)
        print(f"  📡 Status: {res.status_code}")
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            
            titulo = soup.find('h1')
            fecha_inicio = None
            if titulo:
                fecha_inicio = extraer_fecha_desde_texto(titulo.get_text())
            
            if not fecha_inicio:
                hoy = datetime.now()
                fecha_inicio = hoy - timedelta(days=hoy.weekday())
            
            print(f"  📅 Semana del: {fecha_inicio.strftime('%d/%m/%Y')}")
            guardados = procesar_tabla(soup, loteria, fecha_inicio)
            print(f"  ✅ {guardados} resultados guardados")
            return guardados
        else:
            print(f"  ❌ Error HTTP: {res.status_code}")
            return 0
    except Exception as e:
        print(f"  ❌ Error: {str(e)[:200]}")
        return 0

# ========== MAIN ==========
def ejecutar_scraper():
    print("="*60)
    print("🎲 SCRAPER DE LOTERÍAS - RENDER")
    print("="*60)
    
    init_db()
    
    # Obtener loterías
    loterias = obtener_loterias()
    print(f"\n📋 Loterías detectadas: {len(loterias)}")
    for lot in loterias:
        print(f"  • {lot}")
    
    # Sincronizar semana actual
    print("\n📥 SINCRONIZANDO SEMANA ACTUAL")
    print("-"*60)
    total_guardados = 0
    for loteria in loterias:
        print(f"\n🎯 {loteria}")
        guardados = sincronizar_semana_actual(loteria)
        total_guardados += guardados
    
    print(f"\n✅ TOTAL: {total_guardados} resultados guardados")
    print("="*60)
    return total_guardados

if __name__ == "__main__":
    ejecutar_scraper()