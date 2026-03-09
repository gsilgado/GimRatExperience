# ============================
# SECCIÓN A1 — Núcleo de datos
# Modelos + Utilidades + Seguimiento + Estado del día (override/intensidad)
# ============================

from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from datetime import datetime, date
from typing import List, Dict, Optional, Any
import json
import os
import random
import hashlib
import requests

# -----------------------------------------------------------------------------
# App & Templates
# -----------------------------------------------------------------------------
app = FastAPI(title="Coach – Onboarding + Dashboard")
templates = Jinja2Templates(directory="templates")

# -----------------------------------------------------------------------------
# Archivos JSON (persistencia)
# -----------------------------------------------------------------------------
USERS_DB = "usuarios.json"
MEALS_DB = "comidas.json"
WORK_DB = "entrenos.json"
SEGUIMIENTO_DB = "seguimiento.json"      # histórico de peso/medidas
ESTADO_DIA_DB = "estado_dia.json"        # override ON/OFF + intensidad por día
ENTRENO_PLAN_DB = "plan_entreno.json"    # plan persistido del día
FAVS_DB = "favoritos.json"               # favoritos por usuario
ALIMS_PERSONALES_DB = "alimentos_personales.json"

# -----------------------------------------------------------------------------
# Modelos
# -----------------------------------------------------------------------------
class Usuario(BaseModel):
    id: int
    nombre: str
    edad: int
    sexo: str
    peso: float
    estatura: int
    objetivo: str
    experiencia: str
    actividad: str = "moderado"   # nivel de actividad diario
    dias_entreno: int = 0         # nº de días de entreno/semana
    intensidad_base: str = "moderada"  # intensidad por defecto
    creado: str
    plan: Optional[Dict[str, Any]] = None  # baseline compat; plan_on/off se guardan aparte


class Comida(BaseModel):
    user_id: int
    fecha: str  # YYYY-MM-DD
    nombre: str
    kcal: float
    prot: float
    carb: float
    grasa: float


class Entreno(BaseModel):
    user_id: int
    fecha: str  # YYYY-MM-DD
    ejercicio: str
    series: int
    reps: int
    peso: float
    tonelaje: float

# -----------------------------------------------------------------------------
# Utilidades JSON
# -----------------------------------------------------------------------------
def _ts() -> str:
    """Marca temporal para logs."""
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _leer_lista(path: str) -> List[dict]:
    """Lee una lista JSON desde disco. Si no existe o está vacío, retorna []."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        print(f"[WARN] {_ts()} No se pudo parsear JSON en {path}. Se devuelve [].")
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    print(f"[WARN] {_ts()} Formato inesperado en {path}. Se devuelve [].")
    return []


def _guardar_lista(path: str, data: List[dict]) -> None:
    """Guarda una lista JSON en disco con indentación y UTF-8."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# Plan entreno persistido del día
def leer_plan_entreno() -> List[dict]:
    return _leer_lista(ENTRENO_PLAN_DB)


def guardar_plan_entreno(items: List[dict]) -> None:
    _guardar_lista(ENTRENO_PLAN_DB, items)


def get_plan_guardado(user_id: int, fecha: str) -> Optional[dict]:
    for it in leer_plan_entreno():
        if int(it.get("user_id", -1)) == int(user_id) and it.get("fecha") == fecha:
            return it.get("plan")
    return None


def set_plan_guardado(user_id: int, fecha: str, plan: dict) -> None:
    items = leer_plan_entreno()
    for it in items:
        if int(it.get("user_id", -1)) == int(user_id) and it.get("fecha") == fecha:
            it["plan"] = plan
            guardar_plan_entreno(items)
            return
    items.append({"user_id": int(user_id), "fecha": fecha, "plan": plan})
    guardar_plan_entreno(items)


def reset_plan_guardado(user_id: int, fecha: str) -> None:
    items = leer_plan_entreno()
    items = [it for it in items if not (int(it.get("user_id", -1)) == int(user_id) and it.get("fecha") == fecha)]
    guardar_plan_entreno(items)

# Fecha de hoy (ISO)
def hoy_iso() -> str:
    return date.today().isoformat()

# CRUD varias tablas
def leer_usuarios() -> List[dict]:
    return _leer_lista(USERS_DB)


def guardar_usuarios(data: List[dict]) -> None:
    _guardar_lista(USERS_DB, data)


def next_user_id() -> int:
    data = leer_usuarios()
    if not data:
        return 1
    return max(int(u.get("id", 0)) for u in data) + 1


def add_usuario(u: Usuario) -> None:
    data = leer_usuarios()
    data.append(u.model_dump())
    guardar_usuarios(data)


def get_usuario(uid: int) -> Optional[dict]:
    for u in leer_usuarios():
        if int(u.get("id", -1)) == int(uid):
            return u
    return None


def leer_comidas() -> List[dict]:
    return _leer_lista(MEALS_DB)


def add_comida(c: Comida) -> None:
    data = leer_comidas()
    data.append(c.model_dump())
    _guardar_lista(MEALS_DB, data)


def leer_entrenos() -> List[dict]:
    return _leer_lista(WORK_DB)


def add_entreno(e: Entreno) -> None:
    data = leer_entrenos()
    data.append(e.model_dump())
    _guardar_lista(WORK_DB, data)

# Seguimiento (peso/medidas)
def leer_seguimiento() -> List[dict]:
    return _leer_lista(SEGUIMIENTO_DB)


def guardar_seguimiento(data: List[dict]) -> None:
    _guardar_lista(SEGUIMIENTO_DB, data)


def add_seguimiento(
    user_id: int,
    fecha: str,
    peso: Optional[float] = None,
    pecho: Optional[float] = None,
    cintura: Optional[float] = None,
    abdomen: Optional[float] = None,
    cadera: Optional[float] = None,
    brazo: Optional[float] = None,
    pierna: Optional[float] = None,
) -> None:
    """Añade una medición de seguimiento. Modo experto: no bloquea, pero advierte rangos extremos."""
    def _to_float(x):
        try:
            if x is None or str(x).strip() == "":
                return None
            return float(x)
        except Exception:
            print(f"[WARN] {_ts()} Valor no convertible a float en seguimiento: {x!r}")
            return None

    item = {
        "user_id": int(user_id),
        "fecha": fecha,
        "peso": _to_float(peso),
        "pecho": _to_float(pecho),
        "cintura": _to_float(cintura),
        "abdomen": _to_float(abdomen),
        "cadera": _to_float(cadera),
        "brazo": _to_float(brazo),
        "pierna": _to_float(pierna),
    }

    if item["peso"] is not None and not (20 <= item["peso"] <= 400):
        print(f"[WARN] {_ts()} Peso fuera de rango razonable: {item['peso']} kg (se guarda igualmente).")

    data = leer_seguimiento()
    data.append(item)
    guardar_seguimiento(data)


def ultimo_seguimiento(user_id: int) -> Optional[dict]:
    datos = [s for s in leer_seguimiento() if int(s.get("user_id", -1)) == int(user_id)]
    if not datos:
        return None
    datos.sort(key=lambda x: x.get("fecha", ""), reverse=True)
    return datos[0]

# Estado del día (override + intensidad)
def leer_estado_dia() -> List[dict]:
    return _leer_lista(ESTADO_DIA_DB)


def guardar_estado_dia(data: List[dict]) -> None:
    _guardar_lista(ESTADO_DIA_DB, data)


def _key_estado(user_id: int, fecha: str) -> str:
    return f"{int(user_id)}::{fecha}"


def get_estado_dia(user_id: int, fecha: str) -> Dict[str, Any]:
    items = leer_estado_dia()
    for it in items:
        if int(it.get("user_id", -1)) == int(user_id) and it.get("fecha") == fecha:
            if it.get("intensidad") not in ("ligera", "moderada", "intensa"):
                it["intensidad"] = "moderada"
            return {
                "user_id": int(user_id),
                "fecha": fecha,
                "override_on": it.get("override_on", None),
                "intensidad": it.get("intensidad", "moderada"),
            }
    return {"user_id": int(user_id), "fecha": fecha, "override_on": None, "intensidad": "moderada"}


def set_override_on(user_id: int, fecha: str, value: Optional[bool]) -> None:
    items = leer_estado_dia()
    found = False
    for it in items:
        if int(it.get("user_id", -1)) == int(user_id) and it.get("fecha") == fecha:
            it["override_on"] = value
            found = True
            break
    if not found:
        items.append({"user_id": int(user_id), "fecha": fecha, "override_on": value, "intensidad": "moderada"})
    guardar_estado_dia(items)


def set_intensidad_dia(user_id: int, fecha: str, intensidad: str) -> None:
    intensidad = (intensidad or "").lower()
    if intensidad not in ("ligera", "moderada", "intensa"):
        print(f"[WARN] {_ts()} Intensidad inválida '{intensidad}'. Se fuerza 'moderada'.")
        intensidad = "moderada"
    items = leer_estado_dia()
    found = False
    for it in items:
        if int(it.get("user_id", -1)) == int(user_id) and it.get("fecha") == fecha:
            it["intensidad"] = intensidad
            found = True
            break
    if not found:
        items.append({"user_id": int(user_id), "fecha": fecha, "override_on": None, "intensidad": intensidad})
    guardar_estado_dia(items)

def leer_alimentos_personales() -> list:
    return _leer_lista(ALIMS_PERSONALES_DB)

def guardar_alimentos_personales(items: list) -> None:
    _guardar_lista(ALIMS_PERSONALES_DB, items)

def get_alims_personales_de(user_id: int) -> list:
    data = leer_alimentos_personales()
    for fila in data:
        if int(fila.get("user_id", -1)) == int(user_id):
            return fila.get("items", [])
    return []

def set_alims_personales_de(user_id: int, items: list) -> None:
    data = leer_alimentos_personales()
    found = False
    for fila in data:
        if int(fila.get("user_id", -1)) == int(user_id):
            fila["items"] = items
            found = True
            break
    if not found:
        data.append({"user_id": int(user_id), "items": items})
    guardar_alimentos_personales(data)

templates.env.globals['get_alims_personales_de'] = get_alims_personales_de
templates.env.globals['set_alims_personales_de'] = set_alims_personales_de

# ============================
# SECCIÓN A2 — Cálculos ON/OFF + Etiquetas + Recomendaciones
# ============================
def objetivo_label(code: str) -> str:
    return {
        "perder_grasa": "Perder grasa",
        "ganar_musculo": "Ganar músculo",
        "recomposicion": "Recomposición corporal",
        "salud_general": "Mejorar salud",
    }.get((code or ""), code)


def experiencia_label(code: str) -> str:
    return {
        "principiante": "Principiante",
        "intermedio": "Intermedio",
        "avanzado": "Avanzado",
    }.get((code or ""), code)


def actividad_label(code: str) -> str:
    return {
        "sedentario": "Sedentario (oficina / poco movimiento)",
        "ligero": "Ligero (algo de actividad)",
        "moderado": "Moderado (actividad física regular)",
        "activo": "Activo (entreno frecuente)",
        "muy_activo": "Muy activo (entrenos intensos + actividad alta)",
    }.get((code or "").lower(), "Moderado")


def intensidad_label(code: str):
    return {
        "ligera": "Ligera (~150 kcal)",
        "moderada": "Moderada (~250 kcal)",
        "intensa": "Intensa (~400 kcal)",
    }.get((code or "").lower(), "Moderada")


def hay_entreno_en_fecha(user_id: int, fecha: str) -> bool:
    try:
        items = leer_entrenos()
        for e in items:
            if int(e.get("user_id", -1)) == int(user_id) and e.get("fecha") == fecha:
                return True
        return False
    except Exception as ex:
        print(f"[WARN] {_ts()} hay_entreno_en_fecha fallo: {ex}")
        return False

def sugerencias_sustitucion(etiqueta: str, ejercicios_en_plan: list, max_por_ej: int = 12) -> dict:
    """
    Devuelve alternativas por cada ejercicio del plan:
      { idx: [ "Ejercicio A", "Ejercicio B", ... ] }
    Usa el pool de la etiqueta actual y excluye el propio ejercicio.
    """
    banco = _ejercicios_base()
    pool = list(banco.get(etiqueta, banco.get("FB", [])))
    out = {}
    for idx, e in enumerate(ejercicios_en_plan):
        actual = (e.get("nombre") or "").strip()
        # quitamos el que ya está seleccionado y repetidos
        opciones = [x for x in pool if x.strip().lower() != actual.lower()]
        out[idx] = opciones[:max_por_ej]
    return out

def dia_es_on(user_id: int, fecha: str) -> bool:
    estado = get_estado_dia(user_id, fecha)
    ov = estado.get("override_on", None)
    if ov is True:
        return True
    if ov is False:
        return False
    return hay_entreno_en_fecha(user_id, fecha)


def intensidad_del_dia(user_id: int, fecha: str, intensidad_base: str = "moderada") -> str:
    est = get_estado_dia(user_id, fecha)
    intensidad = est.get("intensidad") or intensidad_base or "moderada"
    intensidad = intensidad.lower()
    if intensidad not in ("ligera", "moderada", "intensa"):
        intensidad = "moderada"
    return intensidad


def _stable_seed(*parts: str) -> int:
    raw = "|".join(parts).encode("utf-8")
    h = hashlib.sha256(raw).hexdigest()
    return int(h[:16], 16)  # 64 bits


def _target_ejercicios_por_sesion(etiqueta: str) -> int:
    etiqueta = (etiqueta or "").lower()
    if etiqueta in ("fb", "upper", "lower"):
        return 6
    if etiqueta in ("push", "pull", "legs"):
        return 5
    return 6


def seleccionar_ejercicios_variedad_semana(user_id: int, etiqueta: str, fecha_iso: str) -> List[str]:
    banco = _ejercicios_base()
    pool = list(banco.get(etiqueta, banco.get("FB", [])))
    if not pool:
        return []
    try:
        d = date.fromisoformat(fecha_iso)
    except Exception:
        d = date.today()
    iso_year, iso_week, _ = d.isocalendar()
    seed = _stable_seed(str(user_id), f"{iso_year}-W{iso_week}", etiqueta)
    rnd = random.Random(seed)
    rnd.shuffle(pool)
    n = min(_target_ejercicios_por_sesion(etiqueta), len(pool))
    sel = pool[:n]
    rnd.shuffle(sel)
    return sel


def _reps_to_int(reps_val: Any, default: int = 10) -> int:
    try:
        if reps_val is None:
            return default
        if isinstance(reps_val, (int, float)):
            return int(reps_val)
        s = str(reps_val)
        if "-" in s:
            partes = [p.strip() for p in s.split("-") if p.strip()]
            nums = [int(p) for p in partes if p.isdigit()]
            if len(nums) == 2:
                return max(nums)
            if len(nums) == 1:
                return nums[0]
        return int(float(s))
    except Exception:
        return default


def _met_para_ejercicio(nombre: str) -> float:
    n = (nombre or "").lower()
    compuestos = ["sentadilla", "peso muerto", "deadlift", "press banca", "press militar", "press inclinado", "remo", "jalón", "dominadas", "pull"]
    medianos = ["prensa", "zancadas", "lunge", "hip thrust", "remo en máquina", "press en máquina"]
    menores  = ["elevaciones laterales", "elevación lateral", "curl", "extensión cuádriceps", "extension", "curl femoral", "pájaros", "face pulls", "pull-over"]
    core     = ["plancha", "hollow", "abdominal"]
    if any(k in n for k in compuestos): return 5.8
    if any(k in n for k in medianos):   return 5.0
    if any(k in n for k in menores):    return 3.8
    if any(k in n for k in core):       return 3.8
    return 4.5


def _bw_carga_equivalente(nombre: str, peso_usuario_kg: float) -> float:
    n = (nombre or "").lower()
    if "flexion" in n or "flexión" in n: return 0.65 * peso_usuario_kg
    if "fondos" in n:                    return 0.8  * peso_usuario_kg
    if "dominad" in n:                   return 1.0  * peso_usuario_kg
    return 0.0


def _kcal_por_ejercicio(
    ej: dict,
    peso_usuario_kg: float,
    tempo_seg_por_rep: float = 2.5,
    rom_m: float = 0.5,
    eficiencia: float = 0.25,
) -> float:
    try:
        series = int(ej.get("series", 3))
        reps = _reps_to_int(ej.get("reps", 10))
        nombre = ej.get("nombre", "")
        met = _met_para_ejercicio(nombre)

        minutos = (series * reps * float(tempo_seg_por_rep)) / 60.0
        kcal_min = met * 3.5 * float(peso_usuario_kg) / 200.0
        kcal_base = kcal_min * minutos

        peso_kg = ej.get("peso_kg", 0.0)
        try:
            peso_kg = float(peso_kg)
        except Exception:
            peso_kg = 0.0

        if peso_kg is None or peso_kg <= 0.0:
            peso_kg = _bw_carga_equivalente(nombre, float(peso_usuario_kg))

        tonnage = max(0.0, series * reps * max(0.0, peso_kg))
        kcal_mecanicas = (tonnage * rom_m * 9.81) / (4184.0 * max(eficiencia, 1e-6))

        return max(0.0, kcal_base + kcal_mecanicas)
    except Exception:
        return 0.0


def kcal_total_entreno(plan: dict, peso_usuario_kg: float) -> float:
    if not plan or not plan.get("ejercicios"):
        return 0.0
    total = 0.0
    for ej in plan["ejercicios"]:
        total += _kcal_por_ejercicio(ej, peso_usuario_kg)
    return total

# Cálculos base
def calcular_bmr(edad: int, sexo: str, peso: float, estatura: int) -> float:
    if (sexo or "").lower() == "hombre":
        return 10 * peso + 6.25 * estatura - 5 * edad + 5
    return 10 * peso + 6.25 * estatura - 5 * edad - 161


def actividad_factor(code: str) -> float:
    m = {"sedentario": 1.2, "ligero": 1.375, "moderado": 1.55, "activo": 1.725, "muy_activo": 1.9}
    return m.get((code or "").lower(), 1.55)


def intensidad_kcal_por_sesion(intensidad: str) -> int:
    intensidad = (intensidad or "").lower()
    if intensidad == "ligera":  return 150
    if intensidad == "intensa": return 400
    return 250


def _prote_recomendada(peso: float, objetivo: str, dias_entreno: int) -> float:
    obj = (objetivo or "").lower()
    if obj == "perder_grasa":   return 2.2 * peso if int(dias_entreno) >= 4 else 2.0 * peso
    if obj == "ganar_musculo":  return 2.2 * peso if int(dias_entreno) >= 4 else 1.8 * peso
    if obj == "recomposicion":  return 2.0 * peso
    return 1.6 * peso


def _ajuste_calorias_por_objetivo(calorias_mantenimiento: float, objetivo: str) -> float:
    obj = (objetivo or "").lower()
    if obj == "perder_grasa":  return calorias_mantenimiento - 350
    if obj == "ganar_musculo": return calorias_mantenimiento + 300
    if obj == "recomposicion": return calorias_mantenimiento - 100
    return calorias_mantenimiento


def _clamp_fat_min(fat_g: float, peso: float) -> float:
    minimo = 0.6 * float(peso)
    return max(fat_g, minimo, 0.0)


def calcular_planes_on_off(
    edad: int,
    sexo: str,
    peso: float,
    estatura: int,
    objetivo: str,
    actividad: str,
    dias_entreno: int,
    intensidad_base: str = "moderada",
) -> Dict[str, Dict[str, int]]:
    bmr = calcular_bmr(edad, sexo, peso, estatura)
    tdee_pal = bmr * actividad_factor(actividad)
    extra_kcal = (intensidad_kcal_por_sesion(intensidad_base) * int(dias_entreno)) / 7.0
    tdee = tdee_pal + extra_kcal

    cals_obj = _ajuste_calorias_por_objetivo(tdee, objetivo)

    prot_g = _prote_recomendada(peso, objetivo, dias_entreno)

    grasa_g_min = 0.8 * float(peso)
    grasa_g = max(grasa_g_min, (cals_obj * 0.25) / 9.0)

    carb_g = (cals_obj - (prot_g * 4.0 + grasa_g * 9.0)) / 4.0
    if carb_g < 0:
        grasa_g = grasa_g_min
        carb_g = max(0.0, (cals_obj - (prot_g * 4.0 + grasa_g * 9.0)) / 4.0)

    # Día ON
    on_cal  = cals_obj * 1.15
    on_prot = prot_g
    on_carb = max(0.0, carb_g * 1.25)
    on_fat  = (on_cal - (on_prot * 4.0 + on_carb * 4.0)) / 9.0
    on_fat  = _clamp_fat_min(on_fat, peso)

    # Día OFF
    off_cal  = cals_obj * 0.90
    off_prot = prot_g
    off_carb = max(0.0, carb_g * 0.75)
    off_fat  = (off_cal - (off_prot * 4.0 + off_carb * 4.0)) / 9.0
    off_fat  = _clamp_fat_min(off_fat, peso)

    plan_on = {"bmr": int(round(bmr)), "tdee": int(round(tdee)), "calorias": int(round(on_cal)),
               "proteinas_g": int(round(on_prot)), "carbohidratos_g": int(round(on_carb)), "grasas_g": int(round(on_fat))}
    plan_off = {"bmr": int(round(bmr)), "tdee": int(round(tdee)), "calorias": int(round(off_cal)),
                "proteinas_g": int(round(off_prot)), "carbohidratos_g": int(round(off_carb)), "grasas_g": int(round(off_fat))}
    plan_base = {"bmr": int(round(bmr)), "tdee": int(round(tdee)), "calorias": int(round(cals_obj)),
                 "proteinas_g": int(round(prot_g)), "carbohidratos_g": int(round(carb_g)), "grasas_g": int(round(grasa_g))}
    return {"on": plan_on, "off": plan_off, "base": plan_base}

# Recomendaciones avanzadas (peso/consumo/entreno)
def _parse_iso(d: str) -> Optional[date]:
    try:
        return date.fromisoformat(d)
    except Exception:
        return None


def _peso_semanal_estimado(user_id: int) -> Optional[Dict[str, float]]:
    hist = [s for s in leer_seguimiento() if int(s.get("user_id", -1)) == int(user_id) and s.get("peso") is not None]
    if len(hist) < 2:
        return None
    hist.sort(key=lambda x: x.get("fecha", ""))  # asc
    a, b = hist[-2], hist[-1]
    pa, pb = float(a.get("peso", 0)), float(b.get("peso", 0))
    fa, fb = _parse_iso(a.get("fecha", "")), _parse_iso(b.get("fecha", ""))
    if not fa or not fb:
        return None
    dias = (fb - fa).days
    if dias <= 0:
        return None
    delta = pb - pa
    delta_sem = (delta / dias) * 7.0
    pct_sem = (delta_sem / max(pb, 0.1)) * 100.0
    return {"delta_kg_semana": delta_sem, "pct_semana": pct_sem, "peso_actual": pb}


def coach_recomendaciones_avanzadas(
    plan_on: Dict[str, int],
    plan_off: Dict[str, int],
    consumo: Dict[str, float],
    entreno: Dict[str, Any],
    ultimo_seguim: Optional[dict],
    user_id: int,
    fecha: str,
    objetivo: str,
) -> List[str]:
    tips = []
    kcal = float(consumo.get("kcal", 0.0))
    prot = float(consumo.get("prot", 0.0))
    es_on = dia_es_on(user_id, fecha)
    plan_ref = plan_on if es_on else plan_off

    diff = kcal - plan_ref["calorias"]
    if diff > 150:
        tips.append("Calorías por encima del plan de hoy. Ajusta la siguiente comida o aumenta actividad.")
    elif diff < -150:
        tips.append("Calorías por debajo del plan de hoy. Añade una comida ligera para llegar al objetivo.")
    else:
        tips.append("Calorías dentro del margen del plan de hoy. 👍")

    if prot < plan_ref["proteinas_g"] * 0.9:
        tips.append("Proteína por debajo de lo recomendado. Prioriza una fuente proteica en la próxima comida.")
    else:
        tips.append("Proteína en buen rango.")

    if entreno.get("sesiones", 0) == 0 and es_on:
        tips.append("Es un día ON marcado. Aún no registraste entreno; no olvides hacerlo o marca OFF si fue descanso.")
    elif entreno.get("sesiones", 0) > 0 and not es_on:
        tips.append("Has entrenado pero el día está como OFF. Puedes marcarlo como ON para ajustar el plan.")
    else:
        tips.append("Entreno y estado del día coherentes.")

    try:
        dist_on = abs(kcal - plan_on["calorias"])
        dist_off = abs(kcal - plan_off["calorias"])
        if es_on and dist_off + 100 < dist_on:
            tips.append("Hoy es ON pero tu ingesta se parece a OFF. Considera aumentar carbohidratos/calorías.")
        if (not es_on) and dist_on + 100 < dist_off:
            tips.append("Hoy es OFF pero tu ingesta se parece a ON. Considera reducir carbohidratos/calorías.")
    except Exception as ex:
        print(f"[WARN] {_ts()} coherencia ON/OFF calorías: {ex}")

    ritmo = _peso_semanal_estimado(user_id)
    if ritmo:
        pct_sem = ritmo["pct_semana"]
        obj = (objetivo or "").lower()
        if obj == "perder_grasa":
            if pct_sem < -1.2:
                tips.append(f"Tu pérdida semanal (~{pct_sem:.1f}%) es rápida. Sube ligeramente calorías.")
            elif pct_sem > -0.2:
                tips.append(f"La bajada es lenta (~{pct_sem:.1f}%). Baja un poco calorías o aumenta actividad.")
            else:
                tips.append(f"Ritmo de pérdida saludable (~{pct_sem:.1f}%).")
        elif obj == "ganar_musculo":
            if pct_sem > 0.8:
                tips.append(f"Subida semanal (~{pct_sem:.1f}%) rápida. Baja ligeramente calorías para minimizar grasa.")
            elif pct_sem < 0.2:
                tips.append(f"Subida semanal (~{pct_sem:.1f}%) lenta. Sube un poco calorías.")
            else:
                tips.append(f"Ritmo de ganancia adecuado (~{pct_sem:.1f}%).")
        elif obj == "recomposicion":
            tips.append(f"Recomposición: cambio semanal estimado ~{pct_sem:.1f}%. Ajusta con pequeñas variaciones.")
        else:
            tips.append(f"Cambio semanal ~{pct_sem:.1f}%. Mantén hábitos y constancia.")
    else:
        tips.append("Añade mediciones de peso para monitorizar el ritmo semanal 📏.")

    return tips

# ============================
# SECCIÓN A3 — Helpers día (comidas/entrenos)
# ============================
def sumar_comidas(uid: int, dia: str) -> dict:
    items = [c for c in leer_comidas() if int(c.get("user_id", -1)) == uid and c.get("fecha") == dia]
    return {
        "kcal": sum(float(c.get("kcal", 0)) for c in items),
        "prot": sum(float(c.get("prot", 0)) for c in items),
        "carb": sum(float(c.get("carb", 0)) for c in items),
        "grasa": sum(float(c.get("grasa", 0)) for c in items),
        "items": items,
    }


def resumen_entrenos(uid: int, dia: str) -> dict:
    items = [e for e in leer_entrenos() if int(e.get("user_id", -1)) == uid and e.get("fecha") == dia]
    return {
        "sesiones": len(items),
        "series": sum(int(e.get("series", 0)) for e in items),
        "tonelaje": sum(float(e.get("tonelaje", 0)) for e in items),
        "items": items,
    }


def porcentaje_vs_plan(consumo: dict, plan_ref: dict) -> dict:
    def pct(actual, objetivo):
        try:
            a = float(actual or 0)
            o = float(objetivo or 0)
            if o <= 0:
                return 0.0
            return (a / o) * 100.0
        except Exception:
            return 0.0

    return {
        "kcal_pct": pct(consumo.get("kcal", 0), plan_ref.get("calorias", 0)),
        "prot_pct": pct(consumo.get("prot", 0), plan_ref.get("proteinas_g", 0)),
        "carb_pct": pct(consumo.get("carb", 0), plan_ref.get("carbohidratos_g", 0)),
        "grasa_pct": pct(consumo.get("grasa", 0), plan_ref.get("grasas_g", 0)),
    }

# ============================
# SECCIÓN A3 — Onboarding / Edición / Dashboard / Overrides
# ============================

# RUTA RAÍZ
@app.get("/", response_class=HTMLResponse)
def inicio(request: Request):
    usuarios = leer_usuarios()
    if usuarios:
        return templates.TemplateResponse("home.html", {"request": request, "usuarios": usuarios})
    return templates.TemplateResponse("inicio.html", {"request": request})

# Onboarding
@app.get("/nuevo", response_class=HTMLResponse)
def nuevo_usuario(request: Request):
    return templates.TemplateResponse("inicio.html", {"request": request})


@app.post("/paso2", response_class=HTMLResponse)
def paso2(request: Request, nombre: str = Form(...)):
    return templates.TemplateResponse("paso2.html", {"request": request, "nombre": nombre})


@app.post("/registrar", response_class=HTMLResponse)
def registrar(
    request: Request,
    nombre: str = Form(...),
    edad: int = Form(...),
    sexo: str = Form(...),
    peso: float = Form(...),
    estatura: int = Form(...),
    actividad: str = Form(...),
    dias_entreno: int = Form(...),
):
    data = {"nombre": nombre, "edad": edad, "sexo": sexo, "peso": peso, "estatura": estatura,
            "actividad": actividad, "dias_entreno": dias_entreno}
    return templates.TemplateResponse("objetivo.html", {"request": request, **data})


@app.post("/objetivo", response_class=HTMLResponse)
def objetivo_step(
    request: Request,
    nombre: str = Form(...),
    edad: int = Form(...),
    sexo: str = Form(...),
    peso: float = Form(...),
    estatura: int = Form(...),
    actividad: str = Form(...),
    dias_entreno: int = Form(...),
    objetivo: str = Form(...),
):
    data = {"nombre": nombre, "edad": edad, "sexo": sexo, "peso": peso, "estatura": estatura,
            "actividad": actividad, "dias_entreno": dias_entreno, "objetivo": objetivo}
    return templates.TemplateResponse("experiencia.html", {"request": request, "data": data})


@app.post("/experiencia", response_class=HTMLResponse)
def experiencia_step(
    request: Request,
    nombre: str = Form(...),
    edad: int = Form(...),
    sexo: str = Form(...),
    peso: float = Form(...),
    estatura: int = Form(...),
    actividad: str = Form(...),
    dias_entreno: int = Form(...),
    objetivo: str = Form(...),
    experiencia: str = Form(...),
):
    data = {"nombre": nombre, "edad": edad, "sexo": sexo, "peso": peso, "estatura": estatura,
            "actividad": actividad, "dias_entreno": dias_entreno, "objetivo": objetivo, "experiencia": experiencia}
    return templates.TemplateResponse("resumen.html", {"request": request, "data": data})


@app.post("/finalizar")
def finalizar(
    request: Request,
    nombre: str = Form(...),
    edad: int = Form(...),
    sexo: str = Form(...),
    peso: float = Form(...),
    estatura: int = Form(...),
    actividad: str = Form(...),
    dias_entreno: int = Form(...),
    objetivo: str = Form(...),
    experiencia: str = Form(...),
):
    uid = next_user_id()
    intensidad_base = "moderada"
    planes = calcular_planes_on_off(
        edad=int(edad), sexo=sexo, peso=float(peso), estatura=int(estatura),
        objetivo=objetivo, actividad=actividad, dias_entreno=int(dias_entreno),
        intensidad_base=intensidad_base,
    )
    usuario = Usuario(
        id=uid, nombre=nombre, edad=int(edad), sexo=sexo, peso=float(peso), estatura=int(estatura),
        actividad=actividad, dias_entreno=int(dias_entreno), intensidad_base=intensidad_base,
        objetivo=objetivo, experiencia=experiencia, creado=datetime.utcnow().isoformat(), plan=planes["base"],
    )
    add_usuario(usuario)
    usuarios = leer_usuarios()
    for u in usuarios:
        if int(u["id"]) == uid:
            u["plan_on"] = planes["on"]
            u["plan_off"] = planes["off"]
            break
    guardar_usuarios(usuarios)
    return RedirectResponse(url=f"/dashboard/{uid}", status_code=303)

# Editar usuario
@app.get("/usuario/editar/{user_id}", response_class=HTMLResponse)
def editar_usuario_view(request: Request, user_id: int):
    u = get_usuario(user_id)
    if not u:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("editar_usuario.html", {"request": request, "u": u})


@app.post("/usuario/editar")
def guardar_usuario_editado(
    user_id: int = Form(...),
    nombre: str = Form(...),
    edad: int = Form(...),
    sexo: str = Form(...),
    peso: float = Form(...),
    estatura: int = Form(...),
    actividad: str = Form(...),
    dias_entreno: int = Form(...),
    objetivo: str = Form(...),
    experiencia: str = Form(...),
):
    usuarios = leer_usuarios()
    for u in usuarios:
        if int(u["id"]) == int(user_id):
            edad, peso, estatura, dias_entreno = int(edad), float(peso), int(estatura), int(dias_entreno)
            sexo = (sexo or "").lower()
            actividad = (actividad or "").lower()
            objetivo = (objetivo or "").lower().replace(" ", "_")
            experiencia = (experiencia or "").lower()
            intensidad_base = u.get("intensidad_base", "moderada")

            u["nombre"] = nombre; u["edad"] = edad; u["sexo"] = sexo; u["peso"] = peso
            u["estatura"] = estatura; u["actividad"] = actividad; u["dias_entreno"] = dias_entreno
            u["objetivo"] = objetivo; u["experiencia"] = experiencia

            planes = calcular_planes_on_off(edad, sexo, peso, estatura, objetivo, actividad, dias_entreno, intensidad_base)
            u["plan"] = planes["base"]; u["plan_on"] = planes["on"]; u["plan_off"] = planes["off"]
            break
    guardar_usuarios(usuarios)
    return RedirectResponse(url="/", status_code=303)

# Cambios desde dashboard
@app.post("/usuario/cambiar_objetivo")
def cambiar_objetivo(user_id: int = Form(...), objetivo: str = Form(...)):
    usuarios = leer_usuarios()
    for u in usuarios:
        if int(u["id"]) == int(user_id):
            u["objetivo"] = (objetivo or "").lower().replace(" ", "_")
            planes = calcular_planes_on_off(
                u["edad"], u["sexo"], u["peso"], u["estatura"], u["objetivo"],
                u.get("actividad", "moderado"), u.get("dias_entreno", 0), u.get("intensidad_base", "moderada"),
            )
            u["plan"] = planes["base"]; u["plan_on"] = planes["on"]; u["plan_off"] = planes["off"]
            break
    guardar_usuarios(usuarios)
    return RedirectResponse(url=f"/dashboard/{user_id}", status_code=303)


@app.post("/usuario/cambiar_dias_entreno")
def cambiar_dias_entreno(user_id: int = Form(...), dias_entreno: int = Form(...)):
    usuarios = leer_usuarios()
    for u in usuarios:
        if int(u["id"]) == int(user_id):
            u["dias_entreno"] = int(dias_entreno)
            planes = calcular_planes_on_off(
                u["edad"], u["sexo"], u["peso"], u["estatura"], u["objetivo"],
                u.get("actividad", "moderado"), u.get("dias_entreno", 0), u.get("intensidad_base", "moderada"),
            )
            u["plan"] = planes["base"]; u["plan_on"] = planes["on"]; u["plan_off"] = planes["off"]
            break
    guardar_usuarios(usuarios)
    return RedirectResponse(url=f"/dashboard/{user_id}", status_code=303)


@app.post("/usuario/cambiar_intensidad_base")
def cambiar_intensidad_base(user_id: int = Form(...), intensidad_base: str = Form(...)):
    intensidad_base = (intensidad_base or "moderada").lower()
    if intensidad_base not in ("ligera", "moderada", "intensa"):
        intensidad_base = "moderada"
    usuarios = leer_usuarios()
    for u in usuarios:
        if int(u["id"]) == int(user_id):
            u["intensidad_base"] = intensidad_base
            planes = calcular_planes_on_off(
                u["edad"], u["sexo"], u["peso"], u["estatura"], u["objetivo"],
                u.get("actividad", "moderado"), u.get("dias_entreno", 0), intensidad_base,
            )
            u["plan"] = planes["base"]; u["plan_on"] = planes["on"]; u["plan_off"] = planes["off"]
            break
    guardar_usuarios(usuarios)
    return RedirectResponse(url=f"/dashboard/{user_id}", status_code=303)

# Overrides estado del día
@app.post("/estado-dia/override")
def override_estado_dia(user_id: int = Form(...), fecha: str = Form(...), modo: str = Form(...)):
    modo = (modo or "").lower()
    if modo == "on": set_override_on(user_id, fecha, True)
    elif modo == "off": set_override_on(user_id, fecha, False)
    else: set_override_on(user_id, fecha, None)
    reset_plan_guardado(user_id, fecha)
    return RedirectResponse(url=f"/dashboard/{user_id}?dia={fecha}", status_code=303)


@app.post("/estado-dia/intensidad")
def cambiar_intensidad_dia(user_id: int = Form(...), fecha: str = Form(...), intensidad: str = Form(...)):
    set_intensidad_dia(user_id, fecha, intensidad)
    return RedirectResponse(url=f"/dashboard/{user_id}?dia={fecha}", status_code=303)

# Dashboard
@app.get("/dashboard/{user_id}", response_class=HTMLResponse)
def dashboard(request: Request, user_id: int, dia: Optional[str] = None):
    u = get_usuario(user_id)
    if not u:
        return templates.TemplateResponse("inicio.html", {"request": request})
    if dia is None:
        dia = hoy_iso()

    actividad_code = u.get("actividad", "moderado")
    dias_entreno = u.get("dias_entreno", 0)
    intensidad_base = u.get("intensidad_base", "moderada")

    plan_on = u.get("plan_on")
    plan_off = u.get("plan_off")
    if not (plan_on and plan_off):
        planes = calcular_planes_on_off(
            u["edad"], u["sexo"], u["peso"], u["estatura"], u["objetivo"],
            actividad_code, dias_entreno, intensidad_base,
        )
        plan_on, plan_off = planes["on"], planes["off"]
        usuarios = leer_usuarios()
        for uu in usuarios:
            if int(uu["id"]) == int(user_id):
                uu["plan"] = planes["base"]; uu["plan_on"] = plan_on; uu["plan_off"] = plan_off
                break
        guardar_usuarios(usuarios)

    es_on = dia_es_on(user_id, dia)
    estado = get_estado_dia(user_id, dia)
    intensidad_dia_val = intensidad_del_dia(user_id, dia, intensidad_base)

    consumo = sumar_comidas(user_id, dia)
    entreno = resumen_entrenos(user_id, dia)
    ultimo = ultimo_seguimiento(user_id)
    plan_entreno = generar_entreno_del_dia(user_id, dia)

    historial = [m for m in leer_seguimiento() if int(m.get("user_id", -1)) == int(user_id)]
    historial.sort(key=lambda x: x.get("fecha", ""), reverse=True)

    tips = coach_recomendaciones_avanzadas(plan_on, plan_off, consumo, entreno, ultimo, user_id, dia, u.get("objetivo", ""))

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "id": u["id"],
            "nombre": u["nombre"],
            "edad": u["edad"],
            "sexo": u["sexo"],
            "peso": u["peso"],
            "estatura": u["estatura"],
            "actividad": actividad_label(actividad_code),
            "actividad_code": actividad_code,
            "objetivo": objetivo_label(u["objetivo"]),
            "objetivo_code": u["objetivo"],
            "experiencia": experiencia_label(u["experiencia"]),
            "dias_entreno": dias_entreno,
            "intensidad_base": intensidad_base,
            "plan_on": plan_on,
            "plan_off": plan_off,
            "dia": dia,
            "es_on": es_on,
            "estado_override": estado.get("override_on"),
            "intensidad_dia": intensidad_dia_val,
            "consumo": consumo,
            "entreno": entreno,
            "ultimo": ultimo,
            "plan_entreno": plan_entreno,
            "historial": historial,
            "tips": tips,
            "creado": u["creado"],
        },
    )

# -------------------------------
# VISTA COMIDAS
# -------------------------------
def ultimos_alimentos(user_id: int, limit: int = 8):
    data = [c for c in leer_comidas() if int(c.get("user_id", -1)) == int(user_id)]
    data.sort(key=lambda x: (x.get("fecha", ""), x.get("nombre", "")), reverse=True)
    out, seen = [], set()
    for c in data:
        nombre = str(c.get("nombre", "")).strip()
        if not nombre or nombre.lower() in seen:
            continue
        ref = c.get("ref_per100", {})
        out.append({
            "name": nombre,
            "kcal": float(ref.get("kcal",  c.get("kcal", 0))),
            "protein": float(ref.get("prot", c.get("prot", 0))),
            "carb": float(ref.get("carb", c.get("carb", 0))),
            "fat": float(ref.get("grasa", c.get("grasa", 0))),
        })
        seen.add(nombre.lower())
        if len(out) >= limit:
            break
    return out


def leer_favoritos(user_id: int):
    data = _leer_lista(FAVS_DB)
    for f in data:
        if int(f.get("user_id", -1)) == int(user_id):
            return f.get("items", [])
    return []


def guardar_favoritos(user_id: int, items: list):
    data = _leer_lista(FAVS_DB)
    found = False
    for f in data:
        if int(f.get("user_id", -1)) == int(user_id):
            f["items"] = items
            found = True
            break
    if not found:
        data.append({"user_id": user_id, "items": items})
    _guardar_lista(FAVS_DB, data)


@app.get("/comidas/{user_id}", response_class=HTMLResponse)
def comidas_view(request: Request, user_id: int, dia: str):
    u = get_usuario(user_id)
    if not u:
        return RedirectResponse("/", status_code=303)

    consumo = sumar_comidas(user_id, dia)
    es_on = dia_es_on(user_id, dia)

    plan_on = u.get("plan_on")
    plan_off = u.get("plan_off")
    plan_ref = plan_on if es_on else plan_off

    pct = {"kcal_pct": 0, "prot_pct": 0, "carb_pct": 0, "grasa_pct": 0}
    if plan_ref:
        pct = porcentaje_vs_plan(consumo, plan_ref)

    recientes = ultimos_alimentos(user_id, 8)
    favoritos = leer_favoritos(user_id)

    return templates.TemplateResponse(
        "comidas.html",
        {
            "request": request,
            "user": u,
            "dia": dia,
            "consumo": consumo,
            "es_on": es_on,
            "plan_ref": plan_ref,
            "pct": pct,
            "favoritos": favoritos,
            "recientes": recientes,
        },
    )

# -------------------------------
# FORMULARIO AÑADIR / EDIT / DELETE COMIDA
# -------------------------------
@app.post("/comidas/add")
def comidas_add(
    user_id: int = Form(...),
    fecha: str = Form(...),
    nombre: str = Form(...),
    kcal: float = Form(...),
    prot: float = Form(...),
    carb: float = Form(...),
    grasa: float = Form(...),
    cantidad: float = Form(1.0),
    peso_g: float = Form(100.0),
    ref_kcal_100: float = Form(0.0),
    ref_prot_100: float = Form(0.0),
    ref_carb_100: float = Form(0.0),
    ref_grasa_100: float = Form(0.0),
    guardar_personal: str = Form("off"),
):
    """
    Añade una comida al día + opcionalmente guarda un alimento personal.
    """

    # Normalizar valores
    try: cantidad = max(0.0, float(cantidad))
    except: cantidad = 1.0
    try: peso_g = max(0.0, float(peso_g))
    except: peso_g = 100.0

    # ----------------------
    # 1) CREAR EL ITEM (ESTO ES LO QUE TE FALTABA)
    # ----------------------
    item = {
        "user_id": int(user_id),
        "fecha": fecha,
        "nombre": nombre,
        "kcal": round(float(kcal), 1),
        "prot": round(float(prot), 1),
        "carb": round(float(carb), 1),
        "grasa": round(float(grasa), 1),
        "cantidad": float(cantidad),
        "peso_g": float(peso_g),
        "ref_per100": {
            "kcal": float(ref_kcal_100),
            "prot": float(ref_prot_100),
            "carb": float(ref_carb_100),
            "grasa": float(ref_grasa_100),
        },
    }

    # Guardar comida en la BD
    data = leer_comidas()
    data.append(item)
    _guardar_lista(MEALS_DB, data)

    # ----------------------
    # 2) GUARDAR COMO ALIMENTO PERSONAL (si está marcado)
    # ----------------------
    if guardar_personal.lower() in ("on", "true", "1", "yes"):
        base_kcal = ref_kcal_100
        base_prot = ref_prot_100
        base_carb = ref_carb_100
        base_fat  = ref_grasa_100

        # Si al usuario no le llegó ref_per100 → derivamos por 100g
        if (base_kcal + base_prot + base_carb + base_fat) == 0:
            try:
                factor_total = (peso_g / 100.0) * (cantidad if cantidad > 0 else 1.0)
                if factor_total > 0:
                    base_kcal = float(kcal) / factor_total
                    base_prot = float(prot) / factor_total
                    base_carb = float(carb) / factor_total
                    base_fat  = float(grasa) / factor_total
            except:
                pass

        lista = get_alims_personales_de(user_id)

        nombre_limpio = nombre.strip().lower()
        existe = any(a.get("name", "").strip().lower() == nombre_limpio for a in lista)

        if not existe:
            lista.append({
                "name": nombre.strip(),
                "kcal": float(base_kcal),
                "protein": float(base_prot),
                "carb": float(base_carb),
                "fat": float(base_fat),
            })
            set_alims_personales_de(user_id, lista)

    return RedirectResponse(f"/comidas/{user_id}?dia={fecha}", status_code=303)

@app.post("/alimentos_personales/delete")
def alimentos_personales_delete(
    user_id: int = Form(...),
    nombre: str = Form(...)
):
    items = get_alims_personales_de(user_id)
    items = [i for i in items if i.get("name","").strip().lower() != nombre.strip().lower()]
    set_alims_personales_de(user_id, items)
    return RedirectResponse(url=f"/comidas/{user_id}?dia={hoy_iso()}", status_code=303)

@app.post("/alimentos_personales/edit")
def alimentos_personales_edit(
    user_id: int = Form(...),
    nombre_original: str = Form(...),
    nombre: str = Form(...),
    kcal: float = Form(...),
    protein: float = Form(...),
    carb: float = Form(...),
    fat: float = Form(...)
):
    items = get_alims_personales_de(user_id)

    nuevos = []
    for it in items:
        if it["name"].strip().lower() == nombre_original.strip().lower():
            nuevos.append({
                "name": nombre.strip(),
                "kcal": float(kcal),
                "protein": float(protein),
                "carb": float(carb),
                "fat": float(fat),
            })
        else:
            nuevos.append(it)

    set_alims_personales_de(user_id, nuevos)

    return RedirectResponse(url=f"/comidas/{user_id}?dia={hoy_iso()}", status_code=303)

# ============================
# BUSCADOR ALIMENTOS (USDA robusto con nutrientId)
# ============================
USDA_API_KEY = os.getenv("USDA_API_KEY", "0MelPfD5DuUNHrC3o0gnSt3tHJWqpAgeithFXmeu")

NUTRID = {"kcal": 1008, "protein": 1003, "carb": 1005, "fat": 1004}

def _get_val(foodNutrients: list, nid: int, default: float = 0.0) -> float:
    try:
        for n in foodNutrients or []:
            if int(n.get("nutrientId", -1)) == nid:
                v = n.get("value", default)
                return float(v) if v is not None else default
    except Exception:
        pass
    return default

import math
import requests


# ---------------------------------------------------------------------
# Helper para redondear con seguridad
# ---------------------------------------------------------------------
def _f1(x) -> float:
    try:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            return 0.0
        return round(float(x), 1)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------
# OpenFoodFacts v2 — estable, rápido
# ---------------------------------------------------------------------
def _off_v2_search(q: str, page_size: int = 20):
    url = "https://world.openfoodfacts.org/api/v2/search"
    params = {
        "search_terms": q,
        "fields": "product_name,nutriments",
        "page_size": max(1, min(page_size, 50)),
        "lc": "es",   # idioma preferido
        "cc": "es"    # país para relevancia
    }
    headers = {
        "User-Agent": "CoachApp-Gabriel/1.0 (+https://localhost)",
        "Accept": "application/json"
    }

    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
    except Exception as ex:
        print(f"[OFF v2 ERROR]: {ex}")
        return []

    products = data.get("products", []) or []
    out = []

    for p in products:
        name = (p.get("product_name") or "").strip()
        if not name:
            continue

        nutr = p.get("nutriments", {}) or {}

        kcal = nutr.get("energy-kcal_100g")
        if kcal is None:
            kj = nutr.get("energy_100g")
            if isinstance(kj, (int, float)):
                kcal = float(kj) / 4.184

        out.append({
            "name": name,
            "kcal": _f1(kcal),
            "protein": _f1(nutr.get("proteins_100g")),
            "carb": _f1(nutr.get("carbohydrates_100g")),
            "fat": _f1(nutr.get("fat_100g")),
        })

    return out


# ---------------------------------------------------------------------
# OpenFoodFacts v1 — fallback (cuando v2 falla o devuelve vacío)
# ---------------------------------------------------------------------
def _off_v1_search(q: str, page_size: int = 20):
    url = "https://world.openfoodfacts.org/cgi/search.pl"
    params = {
        "search_terms": q,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": max(1, min(page_size, 50)),
    }
    headers = {
        "User-Agent": "CoachApp-Gabriel/1.0 (+https://localhost)",
        "Accept": "application/json"
    }

    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
    except Exception as ex:
        print(f"[OFF v1 ERROR]: {ex}")
        return []

    products = data.get("products", []) or []
    out = []

    for p in products:
        name = (p.get("product_name") or "").strip()
        if not name:
            continue

        nutr = p.get("nutriments", {}) or {}

        kcal = nutr.get("energy-kcal_100g")
        if kcal is None:
            kj = nutr.get("energy_100g")
            if isinstance(kj, (int, float)):
                kcal = float(kj) / 4.184

        out.append({
            "name": name,
            "kcal": _f1(kcal),
            "protein": _f1(nutr.get("proteins_100g")),
            "carb": _f1(nutr.get("carbohydrates_100g")),
            "fat": _f1(nutr.get("fat_100g")),
        })

    return out


# ---------------------------------------------------------------------
# USDA — fallback final (muy estable)
# ---------------------------------------------------------------------
def _usda_search(q: str, page_size: int = 20):
    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {
        "api_key": USDA_API_KEY,   # asegúrate de tener esta constante declarada
        "query": q,
        "pageSize": max(1, min(page_size, 50)),
    }

    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
    except Exception as ex:
        print(f"[USDA ERROR]: {ex}")
        return []

    foods = data.get("foods", []) or []
    out = []

    # Nutrient IDs estándar USDA
    NID_KCAL = 1008
    NID_PROT = 1003
    NID_CARB = 1005
    NID_FAT  = 1004

    def _nutr_val(nlist, nid):
        for n in nlist:
            if str(n.get("nutrientId")) == str(nid):
                return n.get("value")
        return 0.0

    for f in foods:
        name = (f.get("description") or "").strip()
        if not name:
            continue

        nlist = f.get("foodNutrients", []) or []

        kcal = _nutr_val(nlist, NID_KCAL)
        prot = _nutr_val(nlist, NID_PROT)
        carb = _nutr_val(nlist, NID_CARB)
        fat  = _nutr_val(nlist, NID_FAT)

        out.append({
            "name": name,
            "kcal": _f1(kcal),
            "protein": _f1(prot),
            "carb": _f1(carb),
            "fat": _f1(fat),
        })

    return out
def _buscar_personales(user_id: int, q: str, max_items: int = 50) -> list:
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    
    items = get_alims_personales_de(user_id)
    out = []
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        if q in name.lower():
            out.append({
                "name": name,
                "kcal": float(it.get("kcal", 0)),
                "protein": float(it.get("protein", 0)),
                "carb": float(it.get("carb", 0)),
                "fat": float(it.get("fat", 0)),
            })
            if len(out) >= max_items:
                break
    return out


# ---------------------------------------------------------------------
# ENDPOINT — /alimentos/buscar  (personales + externos con fallback)
# ---------------------------------------------------------------------
@app.get("/alimentos/buscar")
def alimentos_buscar(q: str, user_id: int = 0, page_size: int = 20):

    # 1. Personales
    personales = _buscar_personales(user_id, q)

    # 2. Externos
    externos = []
    try:
        externos = _off_v2_search(q, page_size)
        if not externos:
            externos = _off_v1_search(q, page_size)
        if not externos:
            externos = _usda_search(q, page_size)
    except Exception as ex:
        print("[WARN] búsqueda externa falló:", ex)

    # 3. Mezcla sin duplicados
    vistos = set()
    out = []
    for item in personales + externos:
        n = (item.get("name") or "").strip().lower()
        if not n or n in vistos:
            continue
        vistos.add(n)
        out.append(item)

    return out

# ---------------------------------------------------------------------
# EDIT / DELETE COMIDA (arreglado &lt; &gt; y consistencia)
# ---------------------------------------------------------------------
@app.post("/comidas/edit")
def comidas_edit(
    user_id: int = Form(...),
    fecha: str = Form(...),
    idx: int = Form(...),
    nombre: str = Form(...),
    kcal: float = Form(...),
    prot: float = Form(...),
    carb: float = Form(...),
    grasa: float = Form(...),
    cantidad: float = Form(1.0),
    peso_g: float = Form(100.0),
):
    data = leer_comidas()
    indices_del_dia = [
        i for i, c in enumerate(data)
        if int(c.get("user_id", -1)) == int(user_id) and c.get("fecha") == fecha
    ]

    if 0 <= int(idx) < len(indices_del_dia):
        real_i = indices_del_dia[int(idx)]
        ref = data[real_i].get("ref_per100", {}) or {}

        try:
            cantidad = max(0.0, float(cantidad))
        except Exception:
            cantidad = 1.0
        try:
            peso_g = max(0.0, float(peso_g))
        except Exception:
            peso_g = 100.0

        ref_kcal_100  = float(ref.get("kcal",  0.0))
        ref_prot_100  = float(ref.get("prot",  0.0))
        ref_carb_100  = float(ref.get("carb",  0.0))
        ref_grasa_100 = float(ref.get("grasa", 0.0))

        if (ref_kcal_100 + ref_prot_100 + ref_carb_100 + ref_grasa_100) > 0:
            factor_total = (peso_g / 100.0) * (cantidad if cantidad > 0 else 1.0)
            kcal = ref_kcal_100  * factor_total
            prot = ref_prot_100  * factor_total
            carb = ref_carb_100  * factor_total
            grasa= ref_grasa_100 * factor_total

        data[real_i] = {
            "user_id": int(user_id), "fecha": fecha, "nombre": nombre,
            "kcal": round(float(kcal), 1), "prot": round(float(prot), 1),
            "carb": round(float(carb), 1), "grasa": round(float(grasa), 1),
            "cantidad": float(cantidad), "peso_g": float(peso_g),
            "ref_per100": {
                "kcal": ref_kcal_100, "prot": ref_prot_100,
                "carb": ref_carb_100, "grasa": ref_grasa_100
            },
        }
        _guardar_lista(MEALS_DB, data)

    return RedirectResponse(f"/comidas/{user_id}?dia={fecha}", status_code=303)


@app.post("/comidas/delete")
def comidas_delete(user_id: int = Form(...), fecha: str = Form(...), idx: int = Form(...)):
    data = leer_comidas()
    indices_del_dia = [
        i for i, c in enumerate(data)
        if int(c.get("user_id", -1)) == int(user_id) and c.get("fecha") == fecha
    ]
    if 0 <= int(idx) < len(indices_del_dia):
        real_i = indices_del_dia[int(idx)]
        del data[real_i]
        _guardar_lista(MEALS_DB, data)
    return RedirectResponse(url=f"/comidas/{user_id}?dia={fecha}", status_code=303)


# ---------------------------------------------------------------------
# (Opcional) Borrar un alimento personal
# ---------------------------------------------------------------------
@app.post("/alimentos_personales/delete")
def alimentos_personales_delete(
    user_id: int = Form(...),
    nombre: str = Form(...),
):
    items = get_alims_personales_de(user_id)
    items = [i for i in items if (i.get("name","").strip().lower() != nombre.strip().lower())]
    set_alims_personales_de(user_id, items)
    return RedirectResponse(url=f"/comidas/{user_id}?dia={hoy_iso()}", status_code=303)

# -------------------------------
# VISTA ENTRENOS (automático)
# -------------------------------
@app.get("/entrenos/{user_id}", response_class=HTMLResponse)
def entrenos_view(request: Request, user_id: int, dia: str):
    u = get_usuario(user_id)
    if not u:
        return RedirectResponse("/", status_code=303)

    plan = generar_entreno_del_dia(user_id, dia)

    alternativas = {}
    if plan.get("es_on"):
        alternativas = sugerencias_sustitucion(
            plan.get("etiqueta", "FB"), plan.get("ejercicios", [])
        )

    # 🔥 kcal total del entreno (solo para ON)
    kcal_total = 0.0
    if plan.get("es_on"):
        kcal_total = kcal_total_entreno(plan, float(u.get("peso", 70.0)))

    # ✅ pool para "añadir" (lo tenías)
    pool_add = pool_ejercicios_para_agregar(
        plan.get("etiqueta", "FB"), incluir_todos=True
    )

    return templates.TemplateResponse(
        "entrenos.html",
        {
            "request": request,
            "user": u,
            "dia": dia,
            "plan": plan,
            "alternativas": alternativas,  # ← lo volvemos a enviar a la plantilla
            "kcal_total": kcal_total,
            "pool_add": pool_add,
        },
    )

@app.post("/entrenos/sustituir")
def entrenos_sustituir(user_id: int = Form(...), fecha: str = Form(...), idx: int = Form(...), nuevo_nombre: str = Form(...)):
    plan = get_plan_guardado(user_id, fecha) or generar_entreno_del_dia(user_id, fecha)
    ejercicios = plan.get("ejercicios", [])
    if 0 <= idx < len(ejercicios):
        ejercicios[idx]["nombre"] = nuevo_nombre
    plan["ejercicios"] = ejercicios
    set_plan_guardado(user_id, fecha, plan)
    return RedirectResponse(url=f"/entrenos/{user_id}?dia={fecha}", status_code=303)


@app.post("/entrenos/actualizar")
def entrenos_actualizar(
    user_id: int = Form(...),
    fecha: str = Form(...),
    idx: int = Form(...),
    series: int = Form(...),
    reps: int = Form(...),
    peso_kg: float = Form(...),
):
    plan = get_plan_guardado(user_id, fecha)
    if not plan:
        plan = generar_entreno_del_dia(user_id, fecha)
        set_plan_guardado(user_id, fecha, plan)

    ejercicios = plan.get("ejercicios", [])
    if 0 <= idx < len(ejercicios):
        ejercicios[idx]["series"] = int(series)
        ejercicios[idx]["reps"]   = int(reps)
        ejercicios[idx]["peso_kg"]= float(peso_kg)
    plan["ejercicios"] = ejercicios
    set_plan_guardado(user_id, fecha, plan)
    return RedirectResponse(url=f"/entrenos/{user_id}?dia={fecha}", status_code=303)


@app.post("/entrenos/agregar")
def entrenos_agregar(
    user_id: int = Form(...),
    fecha: str = Form(...),
    nombre: str = Form(...),
    series: int = Form(...),
    reps: int = Form(...),
    peso_kg: float = Form(...),
):
    plan = get_plan_guardado(user_id, fecha)
    if not plan:
        plan = generar_entreno_del_dia(user_id, fecha)
        set_plan_guardado(user_id, fecha, plan)

    ejercicios = plan.get("ejercicios", [])
    nombre = (nombre or "").strip()
    # Permitir duplicados si quieres: aquí no bloqueamos
    ejercicios.append({"nombre": nombre, "series": int(series), "reps": int(reps), "peso_kg": float(peso_kg), "notas": "Ejercicio añadido manualmente"})
    plan["ejercicios"] = ejercicios
    set_plan_guardado(user_id, fecha, plan)
    return RedirectResponse(url=f"/entrenos/{user_id}?dia={fecha}", status_code=303)


@app.post("/entrenos/reset")
def entrenos_reset(user_id: int = Form(...), fecha: str = Form(...)):
    reset_plan_guardado(user_id, fecha)
    return RedirectResponse(url=f"/entrenos/{user_id}?dia={fecha}", status_code=303)

# ============================
# ENTRENAMIENTO AUTOMÁTICO
# ============================
def _elegir_split_por_dias(objetivo: str, dias_entreno: int) -> str:
    d = int(max(0, dias_entreno))
    if d <= 2: return "fb"
    if d <= 4: return "ul"
    return "ppl"


def _orden_semana(split: str, dias_entreno: int) -> list:
    d = max(1, int(dias_entreno))
    if split == "fb":
        return ["FB"] * d
    if split == "ul":
        base = ["Upper", "Lower"]
        return [base[i % 2] for i in range(d)]
    base = ["Push", "Pull", "Legs"]
    return [base[i % 3] for i in range(d)]


def _selector_del_dia(orden: list, fecha: str) -> str:
    try:
        w = date.fromisoformat(fecha).weekday()  # 0=lunes
    except Exception:
        w = 0
    if len(orden) == 0:
        return "FB"
    return orden[w % len(orden)]


def _volumen_por_experiencia(experiencia: str) -> dict:
    exp = (experiencia or "intermedio").lower()
    if exp == "principiante":
        return {"series": 3, "reps": "8-12", "descanso": "60-90s"}
    if exp == "avanzado":
        return {"series": 4, "reps": "5-10", "descanso": "90-120s"}
    return {"series": 4, "reps": "8-12", "descanso": "60-120s"}


def _ajuste_por_objetivo(volumen: dict, objetivo: str) -> dict:
    obj = (objetivo or "").lower()
    v = volumen.copy()
    if obj == "perder_grasa":
        v["reps"] = "10-15"; v["descanso"] = "45-75s"
    elif obj == "ganar_musculo":
        v["reps"] = "6-12";  v["descanso"] = "60-120s"
    elif obj == "recomposicion":
        v["reps"] = "8-12";  v["descanso"] = "60-90s"
    else:
        v["reps"] = "8-12";  v["descanso"] = "60-90s"
    return v


def _ajuste_por_intensidad(volumen: dict, intensidad: str) -> dict:
    v = volumen.copy()
    inten = (intensidad or "moderada").lower()
    if inten == "ligera":
        v["series"] = max(2, v["series"] - 1)
    elif inten == "intensa":
        v["series"] = v["series"] + 1
    return v


def _ejercicios_base():
    return {
        "Push": [
            "Press banca con barra","Press banca con mancuernas","Press inclinado con mancuernas",
            "Press inclinado con barra","Press militar con barra","Press militar con mancuernas",
            "Flexiones (estándar)","Flexiones declinadas","Fondos en paralelas","Fondos asistidos",
            "Press en máquina (plano)","Press en máquina (inclinado)","Elevaciones laterales con mancuernas",
            "Elevaciones laterales en polea","Cruces en polea (alto/medio/bajo)","Press Arnold",
        ],
        "Pull": [
            "Remo con barra","Remo con mancuernas (1 brazo)","Remo en máquina (Hammer)","Remo en polea baja",
            "Jalón al pecho en polea","Dominadas (libres)","Dominadas asistidas","Face pulls","Pájaros en polea",
            "Pull-over con mancuerna","Curl de bíceps con barra","Curl alterno con mancuernas","Curl en banco Scott",
            "Curl en polea con cuerda",
        ],
        "Legs": [
            "Sentadilla con barra (back squat)","Sentadilla goblet","Prensa de piernas","Zancadas caminando",
            "Zancadas estáticas","Peso muerto rumano","Hip thrust (barra)","Hip thrust (máquina)",
            "Curl femoral tumbado","Curl femoral sentado","Extensión de cuádriceps",
            "Elevación de gemelos de pie","Elevación de gemelos sentado","Step-ups al cajón","Sentadilla frontal",
        ],
        "Upper": [
            "Press banca con barra","Remo con barra","Press militar con mancuernas","Jalón al pecho",
            "Elevaciones laterales","Curl bíceps con mancuernas","Extensiones tríceps en polea",
            "Remo en polea baja","Press banca con mancuernas",
        ],
        "Lower": [
            "Sentadilla con barra","Peso muerto rumano","Prensa de piernas","Zancadas","Curl femoral",
            "Extensión de cuádriceps","Elevaciones de gemelos","Plancha (core)",
        ],
        "FB": [
            "Sentadilla con barra","Press banca con barra","Remo con barra","Peso muerto rumano",
            "Jalón al pecho","Plancha (core)","Prensa de piernas","Press militar con mancuernas",
        ],
        "OFF": [
            "Movilidad de cadera y hombro (10')","Caminata ligera 20-30' (o bici suave)",
            "Core: plancha 3×30'', hollow hold 3×20''","Estiramientos suaves (5-10')",
        ],
    }


def pool_ejercicios_para_agregar(etiqueta: str, incluir_todos: bool = True) -> List[str]:
    banco = _ejercicios_base()
    if not incluir_todos:
        return list(banco.get(etiqueta, banco.get("FB", [])))
    todos: set[str] = set()
    for k, lista in banco.items():
        if k == "OFF":
            continue
        for e in lista:
            todos.add(e)
    return sorted(todos)


def _prioridad_ejercicio(nombre: str) -> int:
    n = nombre.lower()
    compuestos = ["sentadilla","back squat","front squat","prensa","zancada","lunge","remo","dominada","jalón","pull",
                  "deadlift","press banca","press militar","press inclinado","peso muerto","hip thrust"]
    if any(c in n for c in compuestos): return 1
    medianos = ["elevación lateral","elevaciones laterales","remo en polea","press en máquina"]
    if any(m in n for m in medianos):   return 2
    accesorios = ["extension","curl femoral","extensión cuádriceps"]
    if any(a in n for a in accesorios): return 3
    brazos = ["bíceps","tríceps","curl","extensión de tríceps"]
    if any(b in n for b in brazos):     return 4
    core = ["plancha","abdominal","hollow"]
    if any(c in n for c in core):       return 5
    return 3


def generar_entreno_del_dia(user_id: int, fecha: str) -> dict:
    plan_cache = get_plan_guardado(user_id, fecha)
    if plan_cache:
        if plan_cache.get("es_on") is True:
            return plan_cache

    u = get_usuario(user_id)
    if not u:
        return {
            "es_on": False, "intensidad": "moderada", "split": "fb", "etiqueta": "OFF",
            "objetivo": "salud_general", "experiencia": "principiante",
            "ejercicios": [{"nombre": e, "series": "-", "reps": "-", "descanso": "-", "notas": ""} for e in _ejercicios_base()["OFF"]],
        }

    objetivo = u["objetivo"]; experiencia = u["experiencia"]; dias_entreno = u["dias_entreno"]
    intensidad_base = u.get("intensidad_base", "moderada")
    es_on = dia_es_on(user_id, fecha)
    intensidad = intensidad_del_dia(user_id, fecha, intensidad_base)
    banco = _ejercicios_base()

    if not es_on or dias_entreno == 0:
        ejercicios = [{"nombre": e, "series": "-", "reps": "-", "descanso": "-", "notas": ""} for e in banco["OFF"]]
        return {"es_on": False, "intensidad": intensidad, "split": "off", "etiqueta": "OFF",
                "objetivo": objetivo, "experiencia": experiencia, "ejercicios": ejercicios}

    split = _elegir_split_por_dias(objetivo, dias_entreno)
    orden = _orden_semana(split, dias_entreno)
    etiqueta = _selector_del_dia(orden, fecha)

    vol = _volumen_por_experiencia(experiencia)
    vol = _ajuste_por_objetivo(vol, objetivo)
    vol = _ajuste_por_intensidad(vol, intensidad)

    nombres = seleccionar_ejercicios_variedad_semana(user_id, etiqueta, fecha)

    ejercicios = []
    for nombre in nombres:
        ejercicios.append({
            "nombre": nombre, "series": int(vol["series"]),
            "reps": _reps_to_int(vol["reps"], default=10), "peso_kg": 0.0,
            "notas": "RIR ~2" if "plancha" not in nombre.lower() else "",
        })
    ejercicios.sort(key=lambda x: _prioridad_ejercicio(x["nombre"]))
    plan = {"es_on": True, "intensidad": intensidad, "split": split, "etiqueta": etiqueta,
            "objetivo": objetivo, "experiencia": experiencia, "ejercicios": ejercicios}
    set_plan_guardado(user_id, fecha, plan)
    return plan

# -------------------------------
# VISTA SEGUIMIENTO
# -------------------------------
@app.get("/seguimiento/{user_id}", response_class=HTMLResponse)
def seguimiento_view(request: Request, user_id: int):
    u = get_usuario(user_id)
    if not u:
        return RedirectResponse("/", status_code=303)
    data = [s for s in leer_seguimiento() if int(s.get("user_id", -1)) == int(user_id)]
    data.sort(key=lambda x: x.get("fecha",""), reverse=True)
    return templates.TemplateResponse("seguimiento.html", {"request": request, "user": u, "historial": data, "dia": hoy_iso()})


@app.get("/seguimiento/borrar/{user_id}/{fecha}")
def borrar_medicion(user_id: int, fecha: str):
    data = leer_seguimiento()
    filtrado = [m for m in data if not (int(m.get("user_id", -1)) == int(user_id) and m.get("fecha") == fecha)]
    guardar_seguimiento(filtrado)
    return RedirectResponse(f"/dashboard/{user_id}", status_code=303)


@app.post("/seguimiento/add")
def seguimiento_add(
    user_id: int = Form(...),
    fecha: str = Form(...),
    peso: float = Form(None),
    pecho: float = Form(None),
    cintura: float = Form(None),
    abdomen: float = Form(None),
    cadera: float = Form(None),
    brazo: float = Form(None),
    pierna: float = Form(None),
):
    add_seguimiento(user_id, fecha, peso, pecho, cintura, abdomen, cadera, brazo, pierna)
    return RedirectResponse(f"/seguimiento/{user_id}", status_code=303)


@app.get("/seguimiento/editar/{user_id}/{fecha}", response_class=HTMLResponse)
def editar_medicion_view(request: Request, user_id: int, fecha: str):
    data = leer_seguimiento()
    medicion = None
    for m in data:
        if int(m.get("user_id", -1)) == int(user_id) and m.get("fecha") == fecha:
            medicion = m; break
    if medicion is None:
        return RedirectResponse(f"/seguimiento/{user_id}", status_code=303)
    return templates.TemplateResponse("seguimiento_editar.html", {"request": request, "user_id": user_id, "m": medicion})


@app.post("/seguimiento/editar")
def editar_medicion(
    user_id: int = Form(...),
    fecha_original: str = Form(...),
    fecha: str = Form(...),
    peso: float = Form(None),
    pecho: float = Form(None),
    cintura: float = Form(None),
    abdomen: float = Form(None),
    cadera: float = Form(None),
    brazo: float = Form(None),
    pierna: float = Form(None),
):
    data = leer_seguimiento()
    nuevas = []
    for m in data:
        if int(m.get("user_id", -1)) == int(user_id) and m.get("fecha") == fecha_original:
            nuevas.append({"user_id": user_id, "fecha": fecha, "peso": peso, "pecho": pecho, "cintura": cintura,
                           "abdomen": abdomen, "cadera": cadera, "brazo": brazo, "pierna": pierna})
        else:
            nuevas.append(m)
    guardar_seguimiento(nuevas)
    return RedirectResponse(f"/seguimiento/{user_id}", status_code=303)

# -------------------------------
# BORRAR USUARIO (con cascada)
# -------------------------------
@app.get("/borrar_usuario/{user_id}")
def borrar_usuario(user_id: int):
    user_id = int(user_id)
    usuarios = [u for u in leer_usuarios() if int(u.get("id", -1)) != user_id]
    guardar_usuarios(usuarios)

    comidas = [c for c in leer_comidas() if int(c.get("user_id", -1)) != user_id]
    _guardar_lista(MEALS_DB, comidas)

    entrenos = [e for e in leer_entrenos() if int(e.get("user_id", -1)) != user_id]
    _guardar_lista(WORK_DB, entrenos)

    seguimiento = [s for s in leer_seguimiento() if int(s.get("user_id", -1)) != user_id]
    guardar_seguimiento(seguimiento)

    estado = [st for st in leer_estado_dia() if int(st.get("user_id", -1)) != user_id]
    guardar_estado_dia(estado)

    print(f"[INFO] Usuario {user_id} y todos sus datos han sido eliminados.")
    return RedirectResponse("/", status_code=303)

if __name__ == "__main__":
    import uvicorn
    import os

    # Render define PORT, tu PC no
    port = int(os.getenv("PORT", 8000))

    # Si estamos en Render → host 0.0.0.0
    # Si estamos en tu PC → host 127.0.0.1
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"

    print(f"Arrancando en {host}:{port}")
    uvicorn.run("main:app", host=host, port=port, reload=True)