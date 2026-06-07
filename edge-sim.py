#!/usr/bin/env python3
"""
edge-sim.py — Simulador del "edge" de audiencia para el gemelo digital.

Emula lo que haría un Nvidia Jetson (visión anónima sobre cada pantalla):
hace POST periódico a /signage/audience con cuánta gente mira, su atención y un
perfil agregado (género/edad). El worker atribuye esos "ojos" al creativo que
suena ahora y el gemelo los muestra en vivo (badge "edge ●", no SIM).

Pensado para el booth Lenovo × Nvidia: arrancas un comando y las pantallas del
gemelo cobran vida con datos realistas, sin hardware físico.

Opcional (--content, activado por defecto): además rota creativos en
/signage/now por pantalla, así el gemelo muestra contenido REAL + audiencia con
un solo proceso. Las imágenes son CORS-limpias (las exige WebGL para texturas).

Uso:
  GRID_KEY=<clave> python3 edge-sim.py screen-twin-1 screen-twin-2
  python3 edge-sim.py --key <clave> --interval 8 screen-totem-01 screen-vending-02
  python3 edge-sim.py --once screen-twin-1            # un solo tick (prueba)
  python3 edge-sim.py --no-content screen-twin-1      # solo audiencia, no toca el contenido

Sin argumentos de pantallas usa: screen-twin-1 screen-twin-2 screen-twin-3
"""
import argparse, json, math, os, random, ssl, sys, time, urllib.request

WORKER = os.environ.get("WORKER_BASE", "https://pixer-eleven.csilvasantin.workers.dev")
CTX = ssl.create_default_context()

# Creativos demo (imágenes CORS-limpias para que el gemelo las pinte como textura).
CREATIVES = [
    {"kind": "image", "name": "Lenovo ThinkVision",  "src": "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=960&q=70"},
    {"kind": "image", "name": "Nvidia RTX · IA local", "src": "https://images.unsplash.com/photo-1591488320449-011701bb6704?w=960&q=70"},
    {"kind": "image", "name": "Café de bienvenida",   "src": "https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=960&q=70"},
    {"kind": "image", "name": "Moda primavera",       "src": "https://images.unsplash.com/photo-1483985988355-763728e1935b?w=960&q=70"},
    {"kind": "image", "name": "Gaming Legion",        "src": "https://images.unsplash.com/photo-1542751371-adc38448a05e?w=960&q=70"},
]

def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(WORKER + path, data=data,
                                 headers={"Content-Type": "application/json", "User-Agent": "edge-sim/1.0"})
    with urllib.request.urlopen(req, timeout=8, context=CTX) as r:
        return json.loads(r.read().decode())

def traffic_now():
    """Curva de afluencia 0..1: baja de madrugada, picos a mediodía y tarde."""
    t = time.localtime()
    hod = t.tm_hour + t.tm_min / 60.0
    midday = math.exp(-((hod - 13.5) ** 2) / (2 * 2.2 ** 2))   # pico ~13:30
    evening = math.exp(-((hod - 19.0) ** 2) / (2 * 1.8 ** 2))  # pico ~19:00
    base = 0.12 + 0.95 * max(midday, evening * 0.95)
    return max(0.05, min(1.0, base))

def sample_screen(screen, key, push_content, state):
    seed = abs(hash(screen))
    rnd = random.Random(seed ^ int(time.time() // 7))   # cambia suave cada ~7s
    tr = traffic_now()
    cap = 4 + (seed % 7)                                 # "tamaño" típico de la pantalla
    present = max(0, int(round(cap * tr * rnd.uniform(0.5, 1.15))))
    attention = int(min(98, max(35, 60 + 30 * tr * rnd.uniform(0.6, 1.1) - (0 if present else 25))))
    dwell = int(rnd.uniform(1800, 7000))
    f_ratio = 0.40 + ((seed >> 3) % 25) / 100.0          # sesgo de género estable por pantalla
    f = int(round(present * f_ratio)); m = max(0, present - f)
    # reparto por edad (algo de aleatoriedad)
    def split(n):
        w = [rnd.uniform(0.08, 0.16), rnd.uniform(0.28, 0.40), rnd.uniform(0.32, 0.44), rnd.uniform(0.10, 0.18)]
        s = sum(w); w = [x / s for x in w]
        a = [int(n * x) for x in w]
        a[2] += n - sum(a)  # ajustar redondeo en el grupo mayoritario
        return a
    kid, young, adult, senior = split(present)

    # Rotación de creativo (opcional): cambia cada ~25s por pantalla.
    if push_content:
        slot = int(time.time() // 25)
        idx = (seed + slot) % len(CREATIVES)
        if state.get(screen) != idx:
            state[screen] = idx
            try: post("/signage/now", {"screen": screen, "item": CREATIVES[idx]})
            except Exception as e: print(f"  ! now {screen}: {e}", file=sys.stderr)

    payload = {"key": key, "screen": screen, "present": present, "attention": attention,
               "dwellMs": dwell, "demo": {"f": f, "m": m},
               "age": {"kid": kid, "young": young, "adult": adult, "senior": senior}}
    try:
        r = post("/signage/audience", payload)
        attr = r.get("attributedTo") or "—"
        print(f"  {screen:22} 👁 {present:>2} · atn {attention:>2}% · ♀{f}/♂{m} · → {attr}")
    except Exception as e:
        print(f"  ! audience {screen}: {e}", file=sys.stderr)

def main():
    ap = argparse.ArgumentParser(description="Simulador de edge de audiencia para el gemelo")
    ap.add_argument("screens", nargs="*", default=[], help="IDs de pantalla (def: screen-twin-1..3)")
    ap.add_argument("--key", default=os.environ.get("GRID_KEY", ""), help="GRID_KEY (o env GRID_KEY)")
    ap.add_argument("--interval", type=float, default=8.0, help="segundos entre ticks (def 8)")
    ap.add_argument("--once", action="store_true", help="un solo tick y salir")
    ap.add_argument("--no-content", dest="content", action="store_false", help="no tocar /signage/now")
    args = ap.parse_args()

    screens = args.screens or ["screen-twin-1", "screen-twin-2", "screen-twin-3"]
    if not args.key:
        print("Falta GRID_KEY (--key <clave> o env GRID_KEY).", file=sys.stderr); sys.exit(2)

    print(f"edge-sim → {WORKER}")
    print(f"pantallas: {', '.join(screens)} · contenido: {'sí' if args.content else 'no'} · cada {args.interval}s\n")
    state = {}
    try:
        while True:
            print(time.strftime("· %H:%M:%S") + f"  (afluencia {int(traffic_now()*100)}%)")
            for s in screens:
                sample_screen(s, args.key, args.content, state)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nedge-sim detenido.")

if __name__ == "__main__":
    main()
