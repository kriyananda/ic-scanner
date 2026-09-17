"""
IC Scanner — Iron Condor Screener
Streamlit app per XSP / XND / RUTW con calendario macro Finnhub
e analisi opzionale tramite Anthropic.

REQUISITI:
    pip install -r requirements.txt

AVVIO LOCALE:
    streamlit run ic_scanner.py

STREAMLIT CLOUD:
    Secrets -> Advanced settings -> Secrets

    FINNHUB_API_KEY = "la_tua_chiave_finnhub"
    ANTHROPIC_API_KEY = "la_tua_chiave_anthropic"   # opzionale

IMPORTANTE:
- Non inserire mai le chiavi API direttamente nel codice.
- Non fare commit di .streamlit/secrets.toml su GitHub.
"""

import json
import os
import html
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

try:
    import anthropic
except ImportError:
    anthropic = None


# ============================================================
# CONFIG
# ============================================================

st.set_page_config(
    page_title="IC Scanner",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed",
)

ROME_TZ = ZoneInfo("Europe/Rome")
FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"
ANTHROPIC_MODEL = "claude-sonnet-4-6"

INDICES = {
    "XSP": "S&P 500 Mini ($XSP)",
    "XND": "Nasdaq 100 Mini ($XND)",
    "RUTW": "Russell 2000 ($RUT / RUTW)",
}

CRITICAL_KEYWORDS = [
    "fomc",
    "federal reserve",
    "interest rate",
    "rate decision",
    "cpi",
    "consumer price",
    "core cpi",
    "pce",
    "personal consumption",
    "nonfarm payroll",
    "non-farm payroll",
    "nfarm",
    "nfp",
    "employment",
    "jackson hole",
    "gdp",
    "gross domestic",
    "retail sales",
    "initial jobless",
]

MEDIUM_KEYWORDS = [
    "ppi",
    "producer price",
    "ism manufacturing",
    "ism services",
    "michigan consumer",
    "jolts",
    "job openings",
    "durable goods",
    "trade balance",
    "housing starts",
    "building permits",
]


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
<style>
.stApp {
    background-color: #0b0d10 !important;
    color: #e2e8f0 !important;
}
section[data-testid="stSidebar"] {
    background: #13161c !important;
}
.stApp * {
    color: #e2e8f0 !important;
}
label,
[data-testid="stWidgetLabel"],
[data-testid="stWidgetLabel"] p,
.stNumberInput label,
.stNumberInput p {
    color: #e2e8f0 !important;
    font-weight: 700 !important;
    font-size: 13px !important;
}
input,
input[type="number"],
.stNumberInput input {
    background: #1e2330 !important;
    border: 1px solid #3b82f6 !important;
    border-radius: 6px !important;
    color: #ffffff !important;
    font-weight: 700 !important;
}
textarea {
    background: #1e2330 !important;
    border: 1px solid #3b4252 !important;
    color: #e2e8f0 !important;
}
[data-testid="stExpander"] {
    background: #181c24 !important;
    border: 1px solid #252a35 !important;
    border-radius: 10px !important;
}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] summary p {
    color: #e2e8f0 !important;
    font-weight: 700 !important;
}
[data-testid="metric-container"] {
    background: #181c24 !important;
    border: 1px solid #252a35 !important;
    border-radius: 10px !important;
    padding: 12px !important;
}
[data-testid="metric-container"] label,
[data-testid="metric-container"] p,
[data-testid="stMetricLabel"] p,
[data-testid="stMetricValue"] div {
    color: #e2e8f0 !important;
}
.stButton > button {
    background: #3b82f6 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    min-height: 42px !important;
}
.stButton > button:hover {
    background: #2563eb !important;
}
hr {
    border-color: #252a35 !important;
}
.stCaption,
[data-testid="stCaptionContainer"] p {
    color: #94a3b8 !important;
}
h1, h2, h3, h4 {
    color: #f1f5f9 !important;
}
#MainMenu, footer {
    visibility: hidden;
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# HELPERS
# ============================================================

def get_secret(name: str) -> str:
    """Legge prima dagli Streamlit Secrets e poi dalle variabili ambiente."""
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""

    if value:
        return str(value).strip()

    return os.getenv(name, "").strip()


def is_critical(name: str) -> bool:
    n = str(name).lower()
    return any(k in n for k in CRITICAL_KEYWORDS)


def is_medium(name: str) -> bool:
    n = str(name).lower()
    return any(k in n for k in MEDIUM_KEYWORDS)


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_date(raw):
    """Converte diversi formati Finnhub in YYYY-MM-DD."""
    if raw in (None, "", "—"):
        return "—"

    # Timestamp Unix: secondi o millisecondi
    if isinstance(raw, (int, float)):
        try:
            ts = float(raw)
            if ts > 10_000_000_000:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=ROME_TZ).strftime("%Y-%m-%d")
        except Exception:
            return "—"

    text = str(raw).strip()

    # ISO / datetime già leggibile
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]

    # Altri formati comuni
    for fmt in (
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return text[:10]


def importance_for(name, impact):
    impact_text = str(impact).strip().lower()

    if is_critical(name) or impact_text in {"high", "3", "3.0"}:
        return 3, True

    if is_medium(name) or impact_text in {"medium", "2", "2.0"}:
        return 2, False

    return 1, False


def message_event(text, level="info"):
    return {
        "date": "—",
        "name": text,
        "importance": 3 if level == "error" else 2,
        "critical": False,
        "system": True,
    }


def macro_status():
    """Ritorna solo informazioni diagnostiche; MAI la chiave."""
    key = get_secret("FINNHUB_API_KEY")
    return {
        "present": bool(key),
        "length": len(key),
        "source": (
            "Streamlit Secrets / environment"
            if key
            else "nessun secret trovato"
        ),
    }


# ============================================================
# FINNHUB
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def fetch_macro_events():
    """
    Scarica gli eventi economici USA delle prossime 14 giornate.

    La cache dura 15 minuti, ma il pulsante "Aggiorna" la cancella
    esplicitamente. Gli errori HTTP non vengono più nascosti.
    """
    api_key = get_secret("FINNHUB_API_KEY")

    if not api_key:
        return [
            message_event(
                "❌ FINNHUB_API_KEY non trovata. "
                "Inseriscila nei Secrets di Streamlit con questo nome ESATTO."
            )
        ]

    today = date.today()
    end_date = today + timedelta(days=14)

    params = {
        "from": today.isoformat(),
        "to": end_date.isoformat(),
        "token": api_key,
    }

    try:
        response = requests.get(
            FINNHUB_URL,
            params=params,
            timeout=15,
            headers={"User-Agent": "IC-Scanner/2.0"},
        )
    except requests.RequestException as exc:
        return [
            message_event(
                f"❌ Errore di connessione a Finnhub: "
                f"{type(exc).__name__}: {exc}",
                "error",
            )
        ]

    if response.status_code != 200:
        body = response.text.strip().replace("\n", " ")
        if len(body) > 250:
            body = body[:250] + "…"

        return [
            message_event(
                f"❌ Finnhub HTTP {response.status_code}. "
                f"Risposta: {body or '(vuota)'}",
                "error",
            )
        ]

    try:
        data = response.json()
    except ValueError:
        return [
            message_event(
                "❌ Finnhub ha restituito una risposta che non è JSON.",
                "error",
            )
        ]

    if isinstance(data, dict):
        items = data.get("economicCalendar", [])
    elif isinstance(data, list):
        items = data
    else:
        items = []

    if not isinstance(items, list):
        return [
            message_event(
                "❌ Formato inatteso nella risposta Finnhub.",
                "error",
            )
        ]

    events = []
    seen = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        country = str(item.get("country", "")).upper().strip()
        if country and country not in {"US", "USA"}:
            continue

        name = (
            item.get("event")
            or item.get("name")
            or item.get("title")
            or ""
        )
        name = str(name).strip()

        if not name:
            continue

        raw_date = (
            item.get("time")
            or item.get("date")
            or item.get("datetime")
            or ""
        )
        event_date = format_date(raw_date)

        # Se Finnhub non ci dà una data utilizzabile, non usiamo
        # l'evento nel calendario operativo.
        if event_date == "—":
            continue

        impact = item.get("impact", "")
        importance, critical = importance_for(name, impact)

        # L'app serve per gli eventi rilevanti all'IC:
        # conserviamo medium/high e keyword critiche.
        if importance < 2:
            continue

        key = (event_date, name.lower())
        if key in seen:
            continue
        seen.add(key)

        events.append(
            {
                "date": event_date,
                "name": name,
                "importance": importance,
                "critical": critical,
                "system": False,
                "country": country or "US",
            }
        )

    events.sort(key=lambda x: (x["date"], -x["importance"], x["name"]))

    if not events:
        return [
            message_event(
                "⚠ Finnhub ha risposto correttamente, ma non risultano "
                "eventi USA medium/high nelle prossime 2 settimane."
            )
        ]

    return events


# ============================================================
# DETERMINISTIC ANALYSIS
# ============================================================

def calculate_blocks(vol_data, macro_events, tnx, tnx_var, vix):
    b1_indices = {}

    for idx, data in vol_data.items():
        iv = safe_float(data.get("iv"))
        hv = safe_float(data.get("hv"))
        ivr = safe_float(data.get("ivr"))
        ivp = safe_float(data.get("ivp"))

        passed = ivr > 50 and iv >= hv

        b1_indices[idx] = {
            "pass": passed,
            "ivr": round(ivr, 2),
            "ivp": round(ivp, 2),
            "iv": round(iv, 2),
            "hv": round(hv, 2),
        }

    candidates = [
        idx for idx, d in vol_data.items()
        if safe_float(d.get("ivr")) > 50
        and safe_float(d.get("iv")) >= safe_float(d.get("hv"))
    ]

    b1_pass = bool(candidates)

    real_events = [
        e for e in macro_events
        if not e.get("system") and e.get("date") != "—"
    ]

    critical_events = [
        e for e in real_events
        if e.get("critical", False)
    ]

    # Gli errori di Finnhub NON vengono interpretati come "nessun evento".
    macro_has_error = any(e.get("system") for e in macro_events)

    b2_pass = not critical_events and not macro_has_error
    b3_pass = tnx < 4.5 and abs(tnx_var) < 0.15
    b5_pass = vix < 20

    # B4 non può essere realmente calcolato senza catena opzioni.
    # Per evitare di inventare dati, lo lasciamo "non verificato".
    b4_pass = False

    return {
        "B1_volatility": {
            "pass": b1_pass,
            "best": max(
                candidates,
                key=lambda x: safe_float(vol_data[x].get("ivr")),
                default=None,
            ),
            "note": (
                "Almeno un indice ha IVR > 50 e IV ≥ HV."
                if b1_pass
                else "Nessun indice ha contemporaneamente IVR > 50 e IV ≥ HV."
            ),
            "indices": b1_indices,
        },
        "B2_macro": {
            "pass": b2_pass,
            "events": [
                f"{e['date']} — {e['name']}"
                for e in critical_events
            ],
            "note": (
                "Nessun evento critico e nessun errore calendario."
                if b2_pass
                else (
                    "Sono presenti eventi critici."
                    if critical_events
                    else "Calendario macro non verificabile."
                )
            ),
        },
        "B3_yields": {
            "pass": b3_pass,
            "tnx": round(tnx, 2),
            "change3d": round(tnx_var, 2),
            "note": (
                "TNX sotto 4.50% e variazione 3gg < 0.15%."
                if b3_pass
                else "Condizione TNX non soddisfatta."
            ),
        },
        "B4_structure": {
            "pass": b4_pass,
            "note": (
                "Non verificato: servono DTE e delta reali della catena "
                "opzioni."
            ),
        },
        "B5_trend": {
            "pass": b5_pass,
            "vix": round(vix, 1),
            "trending": None,
            "note": (
                "VIX sotto 20."
                if b5_pass
                else "VIX ≥ 20."
            ),
        },
    }


def deterministic_result(vol_data, macro_events, tnx, tnx_var, vix):
    """
    Analisi locale, senza AI.
    Non inventa strike, DTE o delta.
    """
    blocks = calculate_blocks(vol_data, macro_events, tnx, tnx_var, vix)

    passes = sum(
        bool(blocks[key]["pass"])
        for key in blocks
    )

    veto = (
        not blocks["B1_volatility"]["pass"]
        or not blocks["B2_macro"]["pass"]
    )

    if veto:
        verdict = "NO"
    elif passes == 5:
        verdict = "GO"
    elif passes >= 3:
        verdict = "WAIT"
    else:
        verdict = "NO"

    reasons = []

    if not blocks["B1_volatility"]["pass"]:
        reasons.append("B1 non passa")
    if not blocks["B2_macro"]["pass"]:
        reasons.append("B2 non passa")
    if not blocks["B3_yields"]["pass"]:
        reasons.append("B3 non passa")
    if not blocks["B4_structure"]["pass"]:
        reasons.append("B4 non verificabile senza chain")
    if not blocks["B5_trend"]["pass"]:
        reasons.append("B5 non passa")

    motivation = "; ".join(reasons)

    return {
        "verdict": verdict,
        "score": passes,
        "blocks": blocks,
        "setup": None,
        "motivation": motivation or "Tutti i blocchi verificati passano.",
        "assignment_risk": (
            "XSP/XND/RUTW: le opzioni sugli indici sono cash-settled "
            "e di stile europeo; verifica sempre il contratto specifico."
        ),
        "source": "analisi locale deterministica",
    }


# ============================================================
# OPTIONAL ANTHROPIC ANALYSIS
# ============================================================

def parse_json_response(raw):
    text = str(raw).strip()

    if text.startswith("```"):
        text = text.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None

    return None


def run_ai_analysis(vol_data, macro_events, tnx, tnx_var, vix, note):
    if anthropic is None:
        return None, "Pacchetto 'anthropic' non installato."

    api_key = get_secret("ANTHROPIC_API_KEY")

    if not api_key:
        return None, (
            "ANTHROPIC_API_KEY non configurata. "
            "Puoi usare comunque l'analisi locale."
        )

    local = deterministic_result(
        vol_data, macro_events, tnx, tnx_var, vix
    )

    macro_text = "\n".join(
        f"- {e['date']}: {e['name']}"
        for e in macro_events
        if not e.get("system")
    ) or "Nessun evento disponibile."

    vol_text = "\n".join(
        f"- {idx}: IV={d['iv']} HV={d['hv']} "
        f"IVR={d['ivr']} IVP={d['ivp']} prezzo={d['price']}"
        for idx, d in vol_data.items()
    )

    system = """
Sei un assistente tecnico per l'analisi di Iron Condor su indici.

IMPORTANTE:
- Non inventare strike, DTE, delta, credito o prezzi.
- Se non hai una vera option chain, lascia setup=null.
- Devi rispettare i blocchi calcolati in Python.
- Se B1 o B2 sono false, verdict deve essere NO.
- Rispondi SOLO con JSON valido.

Schema:
{
  "verdict": "GO|WAIT|NO",
  "score": 0,
  "motivation": "string",
  "blocks": {},
  "setup": null,
  "assignment_risk": "string"
}
"""

    prompt = f"""
DATI VOLATILITÀ:
{vol_text}

CALENDARIO MACRO:
{macro_text}

TNX: {tnx}%
Variazione TNX 3 giorni: {tnx_var:+.2f}%
VIX: {vix}
NOTE OPERATORE: {note or "nessuna"}

BLOCCHI CALCOLATI IN PYTHON:
{json.dumps(local["blocks"], ensure_ascii=False)}

VERDICT LOCALE:
{local["verdict"]}

Non creare dati di option chain che non sono stati forniti.
"""

    try:
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1800,
            system=system,
            messages=[
                {"role": "user", "content": prompt}
            ],
        )

        parts = []
        for block in getattr(response, "content", []):
            text = getattr(block, "text", None)
            if text:
                parts.append(text)

        raw = "\n".join(parts)
        result = parse_json_response(raw)

        if not isinstance(result, dict):
            return None, "Anthropic ha restituito un JSON non valido."

        # Veto locale: il modello non può sovrascriverlo.
        if not local["blocks"]["B1_volatility"]["pass"] or not local["blocks"]["B2_macro"]["pass"]:
            result["verdict"] = "NO"

        result["blocks"] = local["blocks"]
        result["source"] = "Anthropic + controlli locali"

        return result, None

    except Exception as exc:
        return None, f"Errore Anthropic: {type(exc).__name__}: {exc}"


# ============================================================
# UI HELPERS
# ============================================================

def verdict_box(verdict, score, total=5):
    colors = {
        "GO": "#22c55e",
        "WAIT": "#f59e0b",
        "NO": "#ef4444",
    }
    labels = {
        "GO": "✅ ENTRA",
        "WAIT": "⏳ ASPETTA",
        "NO": "🚫 NON ENTRARE",
    }

    color = colors.get(verdict, "#64748b")
    label = labels.get(verdict, verdict)
    pct = max(0, min(100, int(score / total * 100)))

    return f"""
<div style="
    background:{color}12;
    border:2px solid {color};
    border-radius:14px;
    padding:24px;
    text-align:center;
    margin-bottom:16px;">
    <div style="font-size:30px;font-weight:900;color:{color};">
        {label}
    </div>
    <div style="font-size:13px;margin-top:8px;">
        Blocchi superati: <b>{score} / {total}</b>
    </div>
    <div style="
        margin-top:12px;
        height:8px;
        background:#252a35;
        border-radius:99px;
        overflow:hidden;">
        <div style="
            width:{pct}%;
            height:100%;
            background:{color};
            border-radius:99px;">
        </div>
    </div>
</div>
"""


def kv_row(label, value):
    return f"""
<div style="
    display:flex;
    justify-content:space-between;
    gap:15px;
    padding:6px 0;
    border-bottom:1px solid #25253522;">
    <span style="font-size:12px;color:#94a3b8;">{html.escape(str(label))}</span>
    <span style="font-weight:700;font-size:12px;text-align:right;">
        {html.escape(str(value))}
    </span>
</div>
"""


# ============================================================
# HEADER
# ============================================================

st.markdown(
    """
<div style="
    background:#13161c;
    border-bottom:1px solid #252a35;
    padding:16px 4px;
    margin-bottom:20px;">
    <div style="font-size:22px;font-weight:900;">
        📊 IC Scanner
    </div>
    <div style="font-size:12px;color:#94a3b8;margin-top:2px;">
        Iron Condor · XSP · XND · RUTW · macro calendar
    </div>
</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# SECRETS DIAGNOSTICS
# ============================================================

with st.expander("⚙️ Stato collegamenti API", expanded=False):
    fh = macro_status()

    if fh["present"]:
        st.success(
            f"Finnhub: ✅ SECRET TROVATO · lunghezza chiave: {fh['length']}"
        )
    else:
        st.error(
            "Finnhub: ❌ FINNHUB_API_KEY NON TROVATA. "
            "Il nome del Secret deve essere esattamente FINNHUB_API_KEY."
        )

    anthropic_key = get_secret("ANTHROPIC_API_KEY")
    if anthropic_key:
        st.success(
            f"Anthropic: ✅ SECRET TROVATO · lunghezza chiave: "
            f"{len(anthropic_key)}"
        )
    else:
        st.info(
            "Anthropic: ⚪ non configurata. "
            "L'analisi locale continua a funzionare."
        )

    st.caption(
        "Per sicurezza la chiave non viene mai visualizzata."
    )


# ============================================================
# VOLATILITY
# ============================================================

st.markdown("### 📋 Dati di Volatilità")
st.caption(
    "Inserisci i dati che vuoi usare per la valutazione. "
    "I valori non vengono recuperati automaticamente."
)

vol_data = {}

for idx, label in INDICES.items():
    with st.expander(f"**{idx}** — {label}", expanded=True):
        c1, c2, c3, c4, c5 = st.columns(5)

        iv = c1.number_input(
            "IV (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.01,
            format="%.2f",
            key=f"{idx}_iv",
        )
        hv = c2.number_input(
            "HV (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.01,
            format="%.2f",
            key=f"{idx}_hv",
        )
        ivr = c3.number_input(
            "IVR (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.01,
            format="%.2f",
            key=f"{idx}_ivr",
        )
        ivp = c4.number_input(
            "IVP (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=0.01,
            format="%.2f",
            key=f"{idx}_ivp",
        )
        price = c5.number_input(
            "Prezzo",
            min_value=0.0,
            max_value=99999.0,
            value=0.0,
            step=0.01,
            format="%.2f",
            key=f"{idx}_price",
        )

        vol_data[idx] = {
            "iv": round(iv, 2),
            "hv": round(hv, 2),
            "ivr": round(ivr, 2),
            "ivp": round(ivp, 2),
            "price": round(price, 2),
        }

        if ivr > 0:
            if ivr > 50 and iv >= hv:
                st.success("✓ B1 PASSA — IVR > 50 e IV ≥ HV")
            else:
                problems = []
                if ivr <= 50:
                    problems.append("IVR ≤ 50")
                if iv < hv:
                    problems.append("IV < HV")
                st.warning("✗ B1 non passa — " + " · ".join(problems))


st.divider()


# ============================================================
# MARKET DATA
# ============================================================

st.markdown("### 📈 Dati di Mercato Aggiuntivi")
st.caption("Inserisci TNX e VIX manualmente.")

c1, c2, c3 = st.columns(3)

tnx = c1.number_input(
    "Treasury 10Y — TNX (%)",
    min_value=0.0,
    max_value=20.0,
    value=4.25,
    step=0.01,
    format="%.2f",
)

tnx_var = c2.number_input(
    "Variazione TNX 3 giorni (%)",
    min_value=-5.0,
    max_value=5.0,
    value=0.0,
    step=0.01,
    format="%.2f",
)

vix = c3.number_input(
    "VIX",
    min_value=0.0,
    max_value=100.0,
    value=15.0,
    step=0.1,
    format="%.1f",
)

tnx_ok = tnx < 4.5 and abs(tnx_var) < 0.15
vix_ok = vix < 20

c1b, c2b = st.columns(2)

c1b.success("B3 ✓ TNX ok") if tnx_ok else c1b.warning("B3 ✗ TNX fuori criterio")
c2b.success("B5 ✓ VIX ok") if vix_ok else c2b.warning("B5 ⚠ VIX elevato")

st.divider()


# ============================================================
# MACRO CALENDAR
# ============================================================

st.markdown("### 📅 Calendario Macro — prossime 2 settimane")

r1, r2 = st.columns([3, 1])

with r1:
    st.caption(
        "Fonte: Finnhub. Sono mostrati gli eventi USA classificati "
        "medium/high o riconosciuti come critici."
    )

with r2:
    if st.button("🔄 Aggiorna", key="refresh_macro"):
        fetch_macro_events.clear()
        st.rerun()

with st.spinner("Controllo calendario Finnhub..."):
    macro_events = fetch_macro_events()

system_events = [e for e in macro_events if e.get("system")]
real_events = [e for e in macro_events if not e.get("system")]

for event in system_events:
    if "❌" in event["name"]:
        st.error(event["name"])
    else:
        st.warning(event["name"])

critical_found = []

for event in real_events:
    critical = event.get("critical", False)
    importance = event.get("importance", 1)

    icon = "🔴" if critical else "🟡"
    color = "#ef4444" if critical else "#f59e0b"

    st.markdown(
        f"""
<div style="
    padding:7px 0;
    border-bottom:1px solid #25253533;">
    {icon}
    <span style="color:{color};font-size:12px;">
        <b>{html.escape(str(event['date']))}</b>
        — {html.escape(str(event['name']))}
    </span>
</div>
""",
        unsafe_allow_html=True,
    )

    if critical:
        critical_found.append(event["name"])

if real_events:
    if critical_found:
        st.warning(
            f"⚠ {len(critical_found)} evento/i critico/i rilevato/i. "
            "B2 non passa."
        )
    else:
        st.success("✅ Nessun evento critico rilevato. B2 passa.")

st.divider()


# ============================================================
# NOTES
# ============================================================

note = st.text_area(
    "📝 Note aggiuntive",
    placeholder=(
        "Es.: VIX in salita, mercato nervoso, evento già prezzato..."
    ),
    height=80,
)

has_data = any(
    safe_float(d.get("ivr")) > 0
    for d in vol_data.values()
)

if not has_data:
    st.info(
        "Inserisci almeno IVR per uno degli indici per attivare l'analisi."
    )


# ============================================================
# ANALYSIS BUTTONS
# ============================================================

st.markdown("### 🔍 Analisi")

local_button = st.button(
    "📊 ANALISI LOCALE — senza API Anthropic",
    disabled=not has_data,
    use_container_width=True,
)

ai_available = bool(get_secret("ANTHROPIC_API_KEY")) and anthropic is not None

ai_button = st.button(
    "🤖 ANALIZZA CON ANTHROPIC",
    disabled=not has_data or not ai_available,
    use_container_width=True,
)

if not ai_available:
    st.caption(
        "Il pulsante Anthropic si attiva quando ANTHROPIC_API_KEY è "
        "presente e il pacchetto anthropic è installato."
    )


# ============================================================
# SHOW RESULT
# ============================================================

result = None

if local_button:
    with st.spinner("Calcolo locale..."):
        result = deterministic_result(
            vol_data,
            macro_events,
            tnx,
            tnx_var,
            vix,
        )

elif ai_button:
    with st.spinner("Analisi Anthropic..."):
        result, error = run_ai_analysis(
            vol_data,
            macro_events,
            tnx,
            tnx_var,
            vix,
            note,
        )

    if error:
        st.error(error)


if result:
    st.markdown("---")
    st.markdown("## Risultato")

    verdict = result.get("verdict", "NO")
    score = int(safe_float(result.get("score", 0)))

    st.markdown(
        verdict_box(verdict, score),
        unsafe_allow_html=True,
    )

    st.markdown("### Motivazione")
    st.info(result.get("motivation", "—"))

    blocks = result.get("blocks", {})

    block_labels = {
        "B1_volatility": "B1 — Volatilità",
        "B2_macro": "B2 — Calendario Macro",
        "B3_yields": "B3 — Rendimenti",
        "B4_structure": "B4 — Struttura",
        "B5_trend": "B5 — Contesto / VIX",
    }

    for key, label in block_labels.items():
        block = blocks.get(key)

        if not block:
            continue

        passed = bool(block.get("pass"))
        status = "OK ✓" if passed else "NO ✗"

        with st.expander(f"**{label}** — {status}", expanded=False):
            st.write(block.get("note", ""))

            if key == "B1_volatility":
                rows = []

                for idx, d in block.get("indices", {}).items():
                    rows.append(
                        {
                            "Indice": idx,
                            "IVR %": d.get("ivr", 0),
                            "IVP %": d.get("ivp", 0),
                            "IV %": d.get("iv", 0),
                            "HV %": d.get("hv", 0),
                            "B1": "OK" if d.get("pass") else "NO",
                        }
                    )

                if rows:
                    st.dataframe(
                        rows,
                        use_container_width=True,
                        hide_index=True,
                    )

                if block.get("best"):
                    st.caption(
                        f"Indice migliore per IVR: **{block['best']}**"
                    )

            elif key == "B2_macro":
                events = block.get("events", [])

                if events:
                    for event in events:
                        st.warning(event)
                else:
                    st.success("Nessun evento critico.")

            elif key == "B3_yields":
                c1, c2 = st.columns(2)
                c1.metric(
                    "TNX",
                    f"{block.get('tnx', '?')}%",
                )
                c2.metric(
                    "Variazione 3gg",
                    f"{block.get('change3d', 0):+.2f}%",
                )

            elif key == "B5_trend":
                c1, c2 = st.columns(2)
                c1.metric(
                    "VIX",
                    block.get("vix", "?"),
                )

                trending = block.get("trending")
                c2.metric(
                    "Trend forte",
                    "SÌ" if trending else "NO"
                    if trending is not None
                    else "N/D",
                )

    # Setup: visualizza SOLO dati realmente forniti dal modello.
    setup = result.get("setup")

    if verdict == "GO" and setup:
        st.markdown("### 🎯 Setup Proposto")

        rows = [
            ("Sottostante", setup.get("underlying", "—")),
            ("Scadenza", setup.get("expiration", "—")),
            ("DTE", setup.get("dte", "—")),
            ("Put short", setup.get("put_short", "—")),
            ("Put short delta", setup.get("put_short_delta", "—")),
            ("Put long", setup.get("put_long", "—")),
            ("Call short", setup.get("call_short", "—")),
            ("Call short delta", setup.get("call_short_delta", "—")),
            ("Call long", setup.get("call_long", "—")),
            ("Credito", setup.get("credit", "—")),
            ("Max loss", setup.get("max_loss", "—")),
            ("Breakeven basso", setup.get("breakeven_low", "—")),
            ("Breakeven alto", setup.get("breakeven_high", "—")),
        ]

        st.markdown(
            '<div style="background:#181c24;border:1px solid #22c55e40;'
            'border-radius:12px;padding:16px 20px;">'
            + "".join(kv_row(k, v) for k, v in rows)
            + "</div>",
            unsafe_allow_html=True,
        )
    elif verdict == "GO":
        st.info(
            "B1-B5 risultano favorevoli, ma non è disponibile una option "
            "chain: nessuno strike/delta/credito viene inventato."
        )

    st.info(
        result.get(
            "assignment_risk",
            "Verifica sempre le caratteristiche del contratto specifico.",
        )
    )

    st.caption(
        "Analisi generata il "
        + datetime.now(ROME_TZ).strftime("%d/%m/%Y %H:%M")
        + " (ora di Roma)"
    )
