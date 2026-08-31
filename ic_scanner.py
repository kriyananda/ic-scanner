"""
IC Scanner — Iron Condor Screener
Standalone Streamlit app con dati macro live e analisi AI.

Requisiti:
  pip install streamlit anthropic requests pandas

Avvio locale:
  streamlit run ic_scanner.py

Deploy su Streamlit Cloud:
  1. Carica su GitHub
  2. Vai su share.streamlit.io
  3. Aggiungi ANTHROPIC_API_KEY nei Secrets
"""

import streamlit as st
import anthropic
import requests
import json
import re
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

# ── CONFIGURAZIONE PAGINA ─────────────────────────────────────────
st.set_page_config(
    page_title="IC Scanner",
    page_icon="📊",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── CSS DARK TERMINAL ─────────────────────────────────────────────
st.markdown("""
<style>
  /* Sfondo generale */
  .stApp { background-color: #0b0d10; color: #e2e8f0; }
  section[data-testid="stSidebar"] { background: #13161c; }

  /* Input numerici */
  input[type="number"] {
    background: #13161c !important;
    border: 1px solid #252a35 !important;
    border-radius: 6px !important;
    color: #e2e8f0 !important;
    font-weight: 600 !important;
  }

  /* Textarea */
  textarea {
    background: #13161c !important;
    border: 1px solid #252a35 !important;
    color: #e2e8f0 !important;
  }

  /* Bottoni primari */
  .stButton > button {
    background: #3b82f6 !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 700 !important;
    font-size: 15px !important;
    padding: 12px 0 !important;
    width: 100% !important;
    transition: background 0.2s !important;
  }
  .stButton > button:hover {
    background: #2563eb !important;
  }

  /* Expander */
  .streamlit-expanderHeader {
    background: #181c24 !important;
    border: 1px solid #252a35 !important;
    border-radius: 8px !important;
    color: #e2e8f0 !important;
    font-weight: 600 !important;
  }

  /* Metriche */
  [data-testid="metric-container"] {
    background: #181c24;
    border: 1px solid #252a35;
    border-radius: 10px;
    padding: 12px;
  }

  /* Divider */
  hr { border-color: #252a35 !important; }

  /* Nasconde hamburger menu e footer */
  #MainMenu, footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ── HELPERS HTML ──────────────────────────────────────────────────
def badge(text, color):
    return (f'<span style="background:{color}22;color:{color};'
            f'border:1px solid {color}44;border-radius:6px;'
            f'padding:2px 10px;font-size:11px;font-weight:700;'
            f'letter-spacing:1px;text-transform:uppercase;">{text}</span>')

def verdict_box(verdict, score):
    col = {"GO": "#22c55e", "WAIT": "#f59e0b", "NO": "#ef4444"}.get(verdict, "#64748b")
    label = {"GO": "✅ ENTRA", "WAIT": "⏳ ASPETTA", "NO": "🚫 NON ENTRARE"}.get(verdict, "")
    pct = int((score / 5) * 100)
    return f"""
<div style="background:{col}12;border:2px solid {col};border-radius:14px;
     padding:24px;text-align:center;margin-bottom:16px;">
  <div style="font-size:30px;font-weight:900;color:{col};">{label}</div>
  <div style="font-size:13px;color:#64748b;margin-top:8px;">
    Blocchi superati: <b style="color:{col};">{score} / 5</b>
  </div>
  <div style="margin-top:12px;height:8px;background:#252a35;
       border-radius:99px;overflow:hidden;">
    <div style="width:{pct}%;height:100%;background:{col};
         border-radius:99px;"></div>
  </div>
</div>"""

def kv_row(label, value, color="#e2e8f0"):
    return (f'<div style="display:flex;justify-content:space-between;'
            f'padding:5px 0;border-bottom:1px solid #25253522;">'
            f'<span style="color:#64748b;font-size:12px;">{label}</span>'
            f'<span style="color:{color};font-weight:600;font-size:12px;">{value}</span>'
            f'</div>')


# ── CALENDARIO MACRO: FETCH LIVE ──────────────────────────────────
@st.cache_data(ttl=3600)   # cache 1 ora
def fetch_macro_events():
    """
    Recupera eventi macro dalla settimana corrente e la prossima
    usando l'API pubblica di Tradingeconomics (gratuita, no auth).
    Fallback: lista hardcoded degli eventi da monitorare sempre.
    """
    events = []
    try:
        # Tradingeconomics calendar — dati US
        today = date.today()
        end   = today + timedelta(days=14)
        url   = (f"https://api.tradingeconomics.com/calendar/country/united%20states/"
                 f"{today.isoformat()}/{end.isoformat()}")
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            data = r.json()
            critical = {
                "Interest Rate Decision", "Fed Interest Rate Decision",
                "FOMC Meeting Minutes", "FOMC Minutes",
                "CPI", "Core CPI", "PCE Price Index", "Core PCE",
                "Nonfarm Payrolls", "NFP",
                "Initial Jobless Claims",
                "GDP", "Retail Sales",
                "Jackson Hole",
            }
            for item in data:
                name = item.get("Event", "")
                if any(k.lower() in name.lower() for k in critical):
                    dt = item.get("Date", "")[:10]
                    events.append({
                        "date": dt,
                        "name": name,
                        "importance": item.get("Importance", 2),
                    })
    except Exception:
        pass

    # Fallback / integrazione manuale se fetch vuoto
    if not events:
        # Genera promemoria statici sulle date tipiche mensili
        today = date.today()
        events.append({
            "date": "—",
            "name": "⚠ Fetch automatico non disponibile — verifica manualmente su investing.com/economic-calendar",
            "importance": 3,
        })

    return events


# ── ANALISI AI ────────────────────────────────────────────────────
def parse_robust(raw):
    s = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    # Bilanciamento parentesi
    depth, start, end = 0, -1, -1
    for i, c in enumerate(s):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if start != -1 and end != -1:
        try:
            return json.loads(s[start:end + 1])
        except Exception:
            pass
    # Patch JSON troncato
    opens  = s.count("{")
    closes = s.count("}")
    patched = s
    if opens > closes:
        patched += "}" * (opens - closes)
    try:
        return json.loads(patched)
    except Exception:
        return None


def run_analysis(vol_data, macro_events, tnx, tnx_var, vix, note):
    api_key = st.secrets.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        st.error("⚠ Chiave API Anthropic non configurata. Aggiungila nei Secrets di Streamlit.")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    today = datetime.now(ZoneInfo("Europe/Rome")).strftime("%A %d %B %Y")

    vol_summary = "\n".join([
        f"  {idx}: IV={d['iv']}% HV={d['hv']}% IVR={d['ivr']}% "
        f"IVP={d['ivp']}% Prezzo={d.get('price', 'n/d')}"
        for idx, d in vol_data.items()
        if d.get("ivr")
    ])

    macro_summary = "\n".join([
        f"  {e['date']} — {e['name']}"
        for e in macro_events[:10]
    ]) or "  Nessun evento critico rilevato"

    prompt = f"""Oggi è {today}.

DATI REALI DI VOLATILITÀ (usali ESATTAMENTE nel blocco B1, non inventare):
{vol_summary}

EVENTI MACRO PROSSIME 2 SETTIMANE:
{macro_summary}

DATI AGGIUNTIVI:
  Treasury 10Y (TNX): {tnx}%
  Variazione TNX ultimi 3 giorni: {tnx_var:+.2f}%
  VIX: {vix}
{f"  Note operatore: {note}" if note else ""}

Analizza e dimmi se aprire un Iron Condor su XSP, XND o RUTW.
Verifica tutti e 5 i blocchi della checklist e rispondi SOLO con JSON."""

    system = """Sei un assistente per opzioni finanziarie. Rispondi SOLO con JSON puro, no markdown, no backtick.
I dati di volatilità B1 sono forniti dall'utente — usali ESATTAMENTE, non inventare valori.
Regole:
  B1 pass = almeno un indice ha IVR>50 E iv>=hv. best = indice con IVR più alto che passa.
  B2 pass = nessun evento FOMC/CPI/NFP/Jackson Hole nella lista macro ricevuta.
  B3 pass = tnx < 4.5 E variazione 3gg < 0.15.
  B4 sempre true se esiste un setup valido con DTE 21-30.
  B5 pass = vix < 20 E mercato non in trend forte.
Note MAX 60 caratteri. Motivation MAX 300 caratteri.
Struttura JSON esatta:
{
  "verdict": "GO"|"WAIT"|"NO",
  "score": 0-5,
  "blocks": {
    "B1_volatility": {
      "pass": false,
      "best": "XND",
      "note": "stringa max 60 char",
      "indices": {
        "XSP":  {"pass": false, "ivr": 14.02, "ivp": 10, "iv": 12.10, "hv": 12.23},
        "XND":  {"pass": false, "ivr": 60.78, "ivp": 40, "iv": 19.22, "hv": 22.27},
        "RUTW": {"pass": false, "ivr": 45,    "ivp": 35, "iv": 16.5,  "hv": 18.2}
      }
    },
    "B2_macro":     {"pass": true,  "events": [], "note": "stringa max 60 char"},
    "B3_yields":    {"pass": true,  "tnx": 4.21, "change3d": 0.06, "note": "stringa max 60 char"},
    "B4_structure": {"pass": true,  "dte_ok": true, "credit_ok": true, "note": "stringa max 60 char"},
    "B5_trend":     {"pass": true,  "vix": 15.2, "trending": false, "note": "stringa max 60 char"}
  },
  "setup": {
    "underlying": "XND",
    "expiration": "2026-09-18",
    "dte": 28,
    "put_short": 280, "put_long": 270,
    "call_short": 310, "call_long": 320,
    "credit": 380, "max_loss": 620,
    "breakeven_low": 276.2, "breakeven_high": 313.8,
    "tp_target": 190, "sl_trigger": 760
  },
  "motivation": "Spiegazione in italiano max 300 caratteri.",
  "assignment_risk": "Zero — European-style cash-settled."
}"""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text
        return parse_robust(raw)
    except Exception as e:
        st.error(f"Errore API: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# UI PRINCIPALE
# ════════════════════════════════════════════════════════════════

# ── HEADER ───────────────────────────────────────────────────────
st.markdown("""
<div style="background:#13161c;border-bottom:1px solid #252a35;
     padding:16px 4px;margin-bottom:20px;">
  <div style="font-size:22px;font-weight:900;color:#e2e8f0;">
    📊 IC Scanner
  </div>
  <div style="font-size:12px;color:#64748b;margin-top:2px;">
    Iron Condor · XSP · XND · RUTW · European-style
  </div>
</div>
""", unsafe_allow_html=True)


# ── SEZIONE 1: DATI DI VOLATILITÀ ────────────────────────────────
st.markdown("### 📋 Dati di Volatilità")
st.caption("Copia i valori esatti che vedi su Barchart.com per ciascun indice.")

vol_data = {}
INDICES = {
    "XSP":  "S&P 500 Mini ($XSP)",
    "XND":  "Nasdaq 100 Mini ($XND)",
    "RUTW": "Russell 2000 ($RUT / RUTW)",
}

for idx, label in INDICES.items():
    with st.expander(f"**{idx}** — {label}", expanded=True):
        col1, col2, col3, col4, col5 = st.columns(5)
        iv    = col1.number_input("IV (%)",    min_value=0.0, max_value=100.0,
                                  value=0.0, step=0.01, key=f"{idx}_iv",
                                  format="%.2f")
        hv    = col2.number_input("HV (%)",    min_value=0.0, max_value=100.0,
                                  value=0.0, step=0.01, key=f"{idx}_hv",
                                  format="%.2f")
        ivr   = col3.number_input("IVR (%)",   min_value=0.0, max_value=100.0,
                                  value=0.0, step=0.01, key=f"{idx}_ivr",
                                  format="%.2f")
        ivp   = col4.number_input("IVP (%)",   min_value=0.0, max_value=100.0,
                                  value=0.0, step=0.01, key=f"{idx}_ivp",
                                  format="%.2f")
        price = col5.number_input("Prezzo",    min_value=0.0, max_value=99999.0,
                                  value=0.0, step=0.01, key=f"{idx}_price",
                                  format="%.2f")

        vol_data[idx] = {
            "iv": round(iv, 2), "hv": round(hv, 2),
            "ivr": round(ivr, 2), "ivp": round(ivp, 2),
            "price": round(price, 2),
        }

        # Mini feedback immediato
        if ivr > 0:
            passes = ivr > 50 and iv >= hv
            col_ok = "#22c55e" if passes else "#ef4444"
            msg = "✓ PASSA (IVR>50 e IV≥HV)" if passes else \
                  f"✗ NON PASSA — {'IVR<50' if ivr <= 50 else ''}" \
                  f"{'e ' if ivr <= 50 and iv < hv else ''}{'IV<HV' if iv < hv else ''}"
            st.markdown(
                f'<div style="font-size:11px;color:{col_ok};'
                f'font-weight:700;margin-top:4px;">{msg}</div>',
                unsafe_allow_html=True
            )

st.divider()


# ── SEZIONE 2: DATI AGGIUNTIVI B3 e B5 ───────────────────────────
st.markdown("### 📈 Dati di Mercato Aggiuntivi")
st.caption("Cerca TNX e VIX su IBKR o su finance.yahoo.com")

c1, c2, c3 = st.columns(3)
tnx     = c1.number_input("Treasury 10Y — TNX (%)", min_value=0.0,
                           max_value=20.0, value=4.25, step=0.01, format="%.2f")
tnx_var = c2.number_input("Variazione TNX 3 giorni (%)", min_value=-5.0,
                           max_value=5.0, value=0.0, step=0.01, format="%.2f")
vix     = c3.number_input("VIX", min_value=0.0,
                           max_value=100.0, value=15.0, step=0.1, format="%.1f")

# Feedback immediato B3 e B5
c1b, c2b = st.columns(2)
tnx_ok = tnx < 4.5 and abs(tnx_var) < 0.15
vix_ok = vix < 20
c1b.markdown(
    f'<div style="font-size:11px;color:{"#22c55e" if tnx_ok else "#ef4444"};'
    f'font-weight:700;">B3: {"✓ TNX ok" if tnx_ok else "✗ TNX critico"}</div>',
    unsafe_allow_html=True
)
c2b.markdown(
    f'<div style="font-size:11px;color:{"#22c55e" if vix_ok else "#f59e0b"};'
    f'font-weight:700;">B5: {"✓ VIX ok" if vix_ok else "⚠ VIX elevato"}</div>',
    unsafe_allow_html=True
)

st.divider()


# ── SEZIONE 3: CALENDARIO MACRO ───────────────────────────────────
st.markdown("### 📅 Calendario Macro (prossime 2 settimane)")

with st.spinner("Scarico eventi macro..."):
    macro_events = fetch_macro_events()

if macro_events:
    critical_found = []
    for e in macro_events:
        name = e.get("name", "")
        dt   = e.get("date", "")
        imp  = e.get("importance", 1)
        is_critical = any(k in name.upper() for k in [
            "FOMC", "RATE", "CPI", "PCE", "PAYROLL", "NFP",
            "JACKSON", "GDP", "INTEREST"
        ])
        col_e = "#ef4444" if is_critical else "#f59e0b" if imp >= 2 else "#64748b"
        icon  = "🔴" if is_critical else "🟡" if imp >= 2 else "⚪"
        st.markdown(
            f'<div style="padding:5px 0;border-bottom:1px solid #25253533;">'
            f'{icon} <span style="color:{col_e};font-size:12px;">'
            f'<b>{dt}</b> — {name}</span></div>',
            unsafe_allow_html=True
        )
        if is_critical:
            critical_found.append(name)

    if critical_found:
        st.warning(
            f"⚠ **{len(critical_found)} evento/i critico/i rilevato/i** "
            f"— B2 probabilmente FALLISCE. Valuta se aspettare."
        )
    else:
        st.success("✅ Nessun evento critico nelle prossime 2 settimane — B2 dovrebbe passare.")
else:
    st.info("Nessun evento recuperato. Verifica manualmente su investing.com/economic-calendar")

st.divider()


# ── SEZIONE 4: NOTE E ANALISI ─────────────────────────────────────
note = st.text_area(
    "📝 Note aggiuntive (opzionale)",
    placeholder="Es: VIX in salita stamattina, mercato nervoso dopo Fed...",
    height=70,
)

# Check dati minimi
has_data = any(d.get("ivr", 0) > 0 for d in vol_data.values())
if not has_data:
    st.warning("⚠ Inserisci almeno IV, HV e IVR per uno degli indici prima di analizzare.")

if st.button("🔍 ANALIZZA CON DATI REALI", disabled=not has_data):

    with st.spinner("Analisi AI in corso..."):
        result = run_analysis(vol_data, macro_events, tnx, tnx_var, vix, note)

    if result:
        st.markdown("---")
        st.markdown("## Risultato Analisi")

        # ── VERDICT ──────────────────────────────────────────────
        st.markdown(
            verdict_box(result.get("verdict", "NO"), result.get("score", 0)),
            unsafe_allow_html=True
        )

        # ── MOTIVAZIONE ──────────────────────────────────────────
        with st.container():
            st.markdown(f"""
<div style="background:#181c24;border:1px solid #252a35;
     border-radius:12px;padding:14px 16px;margin-bottom:16px;">
  <div style="font-size:10px;color:#64748b;letter-spacing:1px;
       text-transform:uppercase;margin-bottom:8px;">Motivazione</div>
  <div style="font-size:13px;color:#e2e8f0;line-height:1.7;">
    {result.get("motivation", "—")}
  </div>
</div>""", unsafe_allow_html=True)

        # ── I 5 BLOCCHI ──────────────────────────────────────────
        blocks = result.get("blocks", {})
        block_labels = {
            "B1_volatility": "B1 — Volatilità per indice",
            "B2_macro":      "B2 — Calendario Macro",
            "B3_yields":     "B3 — Rendimenti Obbligazionari",
            "B4_structure":  "B4 — Struttura Posizione",
            "B5_trend":      "B5 — Contesto di Mercato",
        }

        for key, label in block_labels.items():
            b = blocks.get(key)
            if not b:
                continue
            col_b = "#22c55e" if b.get("pass") else "#ef4444"
            stato = "OK ✓" if b.get("pass") else "NO ✗"

            with st.expander(f"**{label}** — {stato}"):

                st.markdown(
                    f'<div style="font-size:12px;color:#e2e8f0;'
                    f'margin-bottom:10px;">{b.get("note","")}</div>',
                    unsafe_allow_html=True
                )

                # B1 — tabella indici
                if key == "B1_volatility" and "indices" in b:
                    rows_html = ""
                    for iname, d in b["indices"].items():
                        rc  = "#22c55e" if d.get("pass") else "#ef4444"
                        best = "★ BEST" if b.get("best") == iname else ""
                        rows_html += f"""
<tr style="background:{'#22c55e12' if b.get('best')==iname else '#181c24'}">
  <td style="padding:6px;color:{rc};font-weight:800;">{iname} <span style="color:{rc};font-size:9px;">{best}</span></td>
  <td style="padding:6px;text-align:center;color:{'#22c55e' if d.get('ivr',0)>50 else '#ef4444'};font-weight:700;">{d.get('ivr',0)}%</td>
  <td style="padding:6px;text-align:center;color:#e2e8f0;">{d.get('ivp',0)}%</td>
  <td style="padding:6px;text-align:center;color:#e2e8f0;">{d.get('iv',0)}%</td>
  <td style="padding:6px;text-align:center;color:#e2e8f0;">{d.get('hv',0)}%</td>
  <td style="padding:6px;text-align:center;color:{'#22c55e' if d.get('iv',0)>=d.get('hv',0) else '#ef4444'};font-size:11px;">{'IV≥HV ✓' if d.get('iv',0)>=d.get('hv',0) else 'IV<HV ✗'}</td>
  <td style="padding:6px;text-align:center;">
    <span style="background:{rc}22;color:{rc};border:1px solid {rc}44;
          border-radius:4px;padding:2px 6px;font-size:10px;font-weight:700;">
      {'OK' if d.get('pass') else 'NO'}
    </span>
  </td>
</tr>"""
                    st.markdown(f"""
<div style="overflow-x:auto;">
<table style="width:100%;border-collapse:collapse;font-size:12px;">
  <thead>
    <tr style="background:#252a35;">
      <th style="padding:6px;text-align:left;color:#64748b;font-size:10px;">INDICE</th>
      <th style="padding:6px;text-align:center;color:#64748b;font-size:10px;">IVR</th>
      <th style="padding:6px;text-align:center;color:#64748b;font-size:10px;">IVP</th>
      <th style="padding:6px;text-align:center;color:#64748b;font-size:10px;">IV</th>
      <th style="padding:6px;text-align:center;color:#64748b;font-size:10px;">HV</th>
      <th style="padding:6px;text-align:center;color:#64748b;font-size:10px;">IV vs HV</th>
      <th style="padding:6px;text-align:center;color:#64748b;font-size:10px;">STATO</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
</div>
<div style="font-size:10px;color:#64748b;margin-top:8px;">
  B1 passa se <b style="color:#e2e8f0;">almeno un indice</b> ha IVR&gt;50 e IV≥HV.
</div>""", unsafe_allow_html=True)

                # B2 — eventi
                elif key == "B2_macro":
                    evts = b.get("events", [])
                    if evts:
                        for ev in evts:
                            st.markdown(
                                f'<div style="color:#f59e0b;font-size:12px;">⚠ {ev}</div>',
                                unsafe_allow_html=True
                            )
                    else:
                        st.markdown(
                            '<div style="color:#22c55e;font-size:12px;">'
                            '✓ Nessun evento critico rilevato</div>',
                            unsafe_allow_html=True
                        )

                # B3 — yields
                elif key == "B3_yields":
                    c1t, c2t = st.columns(2)
                    c1t.metric("Treasury 10Y", f"{b.get('tnx','?')}%")
                    c2t.metric("Var. 3 giorni",
                               f"{b.get('change3d', 0):+.2f}%",
                               delta_color="inverse")

                # B5 — VIX
                elif key == "B5_trend":
                    c1t, c2t = st.columns(2)
                    c1t.metric("VIX", b.get("vix", "?"))
                    c2t.metric("Trend forte",
                               "SÌ ✗" if b.get("trending") else "NO ✓")

        # ── SETUP PROPOSTO ────────────────────────────────────────
        setup = result.get("setup")
        if result.get("verdict") == "GO" and setup:
            st.markdown("---")
            st.markdown("### 🎯 Setup Proposto")
            s = setup
            rows_s = [
                ("Sottostante",       s.get("underlying", "—")),
                ("Scadenza",          s.get("expiration", "—")),
                ("DTE",               f"{s.get('dte','?')} giorni"),
                ("Put short / long",  f"{s.get('put_short','?')} / {s.get('put_long','?')}"),
                ("Call short / long", f"{s.get('call_short','?')} / {s.get('call_long','?')}"),
                ("Credito incassato", f"${s.get('credit','?')}"),
                ("Max loss",          f"${s.get('max_loss','?')}"),
                ("Breakeven basso",   s.get("breakeven_low", "?")),
                ("Breakeven alto",    s.get("breakeven_high", "?")),
                ("Take profit 50%",   f"${s.get('tp_target','?')}"),
                ("Stop loss 200%",    f"${s.get('sl_trigger','?')}"),
            ]
            html_rows = "".join(kv_row(k, v) for k, v in rows_s)
            st.markdown(f"""
<div style="background:#181c24;border:1px solid #22c55e40;
     border-radius:12px;padding:16px 20px;">
  {html_rows}
</div>""", unsafe_allow_html=True)

        # ── ASSIGNMENT RISK ───────────────────────────────────────
        st.info(
            "ℹ **Rischio assignment:** "
            + result.get("assignment_risk",
                         "XSP, XND e RUTW sono European-style cash-settled — "
                         "nessun rischio di esercizio anticipato.")
        )

        st.caption(
            f"Analisi generata il "
            f"{datetime.now(ZoneInfo('Europe/Rome')).strftime('%d/%m/%Y %H:%M')} (ora di Roma)"
        )
