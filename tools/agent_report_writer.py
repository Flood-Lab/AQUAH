from __future__ import annotations
"""
Multi‑provider `make_report_for_figures()`
-----------------------------------------
• Supports **OpenAI**, **Anthropic Claude‑3**, and **Google Gemini** vision chat models
  (DeepSeek intentionally left out per user request).
• Keeps the original three‑section memo logic but routes calls to the chosen model
  automatically, handling each provider's unique image+text format.

Minimal usage
-------------
```python
report = make_report_for_figures("./figures", model="gpt-4o", temperature=0.25)
```
"""
import base64
import io
import mimetypes
import os
from pathlib import Path
from typing import List, Tuple
import pypandoc
import requests
from PIL import Image
from crewai import Agent, Crew, Task, Process

# ─────────────────────────────────────────────────────────────────────────────
# ENV PATCH: allow google.generativeai + LiteLLM style key reuse
# ─────────────────────────────────────────────────────────────────────────────
if "GEMINI_API_KEY" not in os.environ and "GOOGLE_API_KEY" in os.environ:
    os.environ["GEMINI_API_KEY"] = os.environ["GOOGLE_API_KEY"]

# ╭──────────────────── Helper: provider detection & b64 encode ──────────────╮

_CLAUDE_B64_LIMIT = 5 * 1024 * 1024  # Anthropic counts base64 size (5 MB)
_CLAUDE_BIN_LIMIT = int(_CLAUDE_B64_LIMIT * 0.75)  # ≈3.9 MB raw bytes


def _detect_provider(model: str) -> str:
    if model.startswith(("anthropic/", "claude-")):
        return "anthropic"
    if model.startswith(("gemini", "models/", "gemini/")):
        return "gemini"
    return "openai"  # default


def _jpeg_downsize(data: bytes, *, limit: int) -> Tuple[str, bytes]:
    """Iteratively JPEG‑recompress + down‑scale until ≤ *limit* bytes."""
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        w, h = im.size
        qualities = [85, 75, 65, 55]
        while True:
            for q in qualities:
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=q, optimize=True)
                out = buf.getvalue()
                if len(out) <= limit:
                    return "image/jpeg", out
            if w < 450:
                return "image/jpeg", out  # safety net
            w, h = int(w * 0.85), int(h * 0.85)
            im = im.resize((w, h), Image.LANCZOS)


def _file_to_b64(path: str, provider: str) -> Tuple[str, str]:
    data = Path(path).read_bytes()
    mime = mimetypes.guess_type(path)[0] or "image/png"
    if provider == "anthropic" and len(data) > _CLAUDE_BIN_LIMIT:
        mime, data = _jpeg_downsize(data, limit=_CLAUDE_BIN_LIMIT)
    return mime, base64.b64encode(data).decode()

# ╰────────────────────────────────────────────────────────────────────────────╯
# ╭──────────────────── Provider‑specific thin wrappers ───────────────────────╮


def _call_openai(model: str, messages: list, *, temperature: float):
    from openai import OpenAI

    cli = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return (
        cli.chat.completions.create(model=model, messages=messages)
        .choices[0]
        .message
        .content
    )


def _call_claude(model: str, messages: list, *, temperature: float):
    import anthropic

    cli = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return cli.messages.create(model=model, messages=messages, temperature=temperature, max_tokens=2048).content[0].text

def _call_gemini(model: str, parts, *, temperature: float):
    import google.generativeai as genai

    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    return genai.GenerativeModel(model).generate_content(parts, generation_config={"temperature": temperature}).text


# ╰────────────────────────────────────────────────────────────────────────────╯
# ╭────────────────────────── Universal vision_chat() ────────────────────────╮

def vision_chat(model: str, images: List[str], user_text: str, *, temperature: float = 0.7) -> str:
    provider = _detect_provider(model)

    if provider == "openai":
        msg = [{"role": "user", "content": []}]
        for p in images:
            mime, b64 = _file_to_b64(p, provider)
            msg[0]["content"].append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        msg[0]["content"].append({"type": "text", "text": user_text})
        return _call_openai(model, msg, temperature=temperature)

    if provider == "anthropic":
        blocks = []
        for p in images:
            mime, b64 = _file_to_b64(p, provider)
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
        blocks.append({"type": "text", "text": user_text})
        return _call_claude(model, [{"role": "user", "content": blocks}], temperature=temperature)

    if provider == "gemini":
        parts = [user_text] + [Image.open(p) for p in images]
        return _call_gemini(model, parts, temperature=temperature)

    raise ValueError(f"Unsupported provider for model '{model}'")

# ╰────────────────────────────────────────────────────────────────────────────╯
# ╭───────────────── Public API: make_report_for_figures() ────────────────────╮

def make_report_for_figures(fig_dir: str, *, model: str = "gpt-4o", temperature: float = 0.3) -> str:
    """Generate the 3‑section Markdown memo from figure directory *fig_dir*.

    Parameters
    ----------
    fig_dir : str | Path
        Directory containing required figure files:
        • combined_maps.png
        • basic_data.png
        • results.png
    model : str, default "gpt-4o"
        Vision‑capable chat model name (OpenAI / Claude‑3 / Gemini).
    temperature : float, default 0.3
        Sampling temperature.
    """

    fig_dir = Path(fig_dir)
    img_paths = [
        fig_dir / "combined_maps.png",
        fig_dir / "basic_data.png",
        fig_dir / "results.png",
    ]

    # Guard: ensure files exist
    missing = [p.name for p in img_paths if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing figure(s): " + ", ".join(missing))

    system_prompt = (
        "You are a senior hydrologist writing an internal technical memo.\n"
        "You will receive three figures in order:\n"
        "① combined_maps → basin outline + all USGS gauges and flow accumulation with gauges;\n"
        "② basic_data → DEM / FAM / DDM overview of the same basin;\n"
        "③ results → hydro-simulation result plot (obs, precip, sim curves).\n\n"
        "Generate a concise **Markdown report** with **three sections**:\n"
        "1. **Basin & Gauge Map**  – main features, which gauges appear downstream, notable geography;\n"
        "2. **Fundamental Basin Data** – comment on DEM (relief), FAM (flow acc.), DDM (drain‑dens.);\n"
        "3. **Simulation vs Observation** – describe each curve, comment on fit, peaks, lags, bias.\n\n"
    )

    # The provider wrappers expect *user* content only, so blend roles manually.
    full_prompt = system_prompt + "Please draft the report as requested."

    return vision_chat(model, [str(p) for p in img_paths], full_prompt, temperature=temperature).strip()

# ╰────────────────────────────────────────────────────────────────────────────╯

def generate_simulation_summary(args, crest_args):
    """
    Generate a concise textual summary of the hydrological simulation based on args and crest_args.
    
    Args:
        args: Object containing simulation metadata and results
        crest_args: Object containing CREST model parameters
    
    Returns:
        str: A concise English summary of key simulation information
    """
    # Format dates for better readability
    start_date = args.time_start.split()[0] if isinstance(args.time_start, str) else args.time_start.strftime("%Y-%m-%d")
    end_date = args.time_end.split()[0] if isinstance(args.time_end, str) else args.time_end.strftime("%Y-%m-%d")
    
    # Extract key performance metrics
    rmse = args.metrics['RMSE']
    bias = args.metrics['Bias']
    bias_percent = args.metrics['Bias_percent']
    correlation = args.metrics['CC']
    nsce = args.metrics['NSCE']
    kge = args.metrics['KGE']

    # Create summary text
    summary = f"""
Hydrological Simulation Summary for {args.basin_name}:

The simulation was conducted for the period from {start_date} to {end_date} in the {args.basin_name} (basin area: {args.basin_area} km²). The analysis focused on USGS gauge #{args.gauge_id} located at ({args.latitude_gauge}, {args.longitude_gauge}).

Key model parameters included:

Water Balnce Parameters:
- Water capacity ratio (WM: {crest_args.wm}): Maximum soil water capacity in mm. Higher values allow soil to hold more water, reducing runoff.
- Infiltration curve exponent (B: {crest_args.b}): Controls water partitioning to runoff. Higher values reduce infiltration, increasing runoff.
- Impervious area ratio (IM: {crest_args.im}): Represents urbanized areas. Higher values increase direct runoff.
- PET adjustment factor (KE: {crest_args.ke}): Affects potential evapotranspiration. Higher values increase PET, reducing runoff.
- Soil saturated hydraulic conductivity (FC: {crest_args.fc}): Rate at which water enters soil (mm/hr). Higher values allow easier water entry, reducing runoff.
- Initial soil water value (IWU: {crest_args.iwu}): Initial soil moisture (mm). Higher values leave less space for water, increasing runoff.

Kinematic Wave (Routing) Parameters:
- Drainage threshold (TH: {crest_args.th}): Defines river cells based on flow accumulation (km²). Higher values result in fewer channels.
- Interflow speed multiplier (UNDER: {crest_args.under}): Higher values accelerate subsurface flow.
- Interflow reservoir leakage coefficient (LEAKI: {crest_args.leaki}): Higher values increase interflow drainage rate.
- Initial interflow reservoir value (ISU: {crest_args.isu}): Initial subsurface water. Higher values may cause early peak flows.
- Channel flow multiplier (ALPHA: {crest_args.alpha}): In Q = αAᵝ equation. Higher values slow wave propagation in channels.
- Channel flow exponent (BETA: {crest_args.beta}): In Q = αAᵝ equation. Higher values slow wave propagation in channels.
- Overland flow multiplier (ALPHA0: {crest_args.alpha0}): Similar to ALPHA but for non-channel cells. Higher values slow overland flow.

Performance metrics show a Nash-Sutcliffe Coefficient of Efficiency (NSCE) of {nsce:.3f}. The Kling-Gupta Efficiency (KGE) was {kge:.3f}, which provides a balanced assessment of correlation, bias, and variability. The correlation coefficient between observed and simulated streamflow was {correlation:.3f}. The model showed a bias of {bias:.2f} m³/s ({bias_percent:.1f}%), with a root mean square error (RMSE) of {rmse:.2f} m³/s.
"""
    return summary.strip()

# Census API report writer
import requests
import json
from openai import OpenAI

# Helper mapping
STATE_FIPS = {
    "Alabama": "01", "Alaska": "02", "Arizona": "04", "Arkansas": "05",
    "California": "06", "Colorado": "08", "Connecticut": "09", "Delaware": "10",
    "Florida": "12", "Georgia": "13", "Hawaii": "15", "Idaho": "16",
    "Illinois": "17", "Indiana": "18", "Iowa": "19", "Kansas": "20",
    "Kentucky": "21", "Louisiana": "22", "Maine": "23", "Maryland": "24",
    "Massachusetts": "25", "Michigan": "26", "Minnesota": "27", "Mississippi": "28",
    "Missouri": "29", "Montana": "30", "Nebraska": "31", "Nevada": "32",
    "New Hampshire": "33", "New Jersey": "34", "New Mexico": "35", "New York": "36",
    "North Carolina": "37", "North Dakota": "38", "Ohio": "39", "Oklahoma": "40",
    "Oregon": "41", "Pennsylvania": "42", "Rhode Island": "44", "South Carolina": "45",
    "South Dakota": "46", "Tennessee": "47", "Texas": "48", "Utah": "49",
    "Vermont": "50", "Virginia": "51", "Washington": "53", "West Virginia": "54",
    "Wisconsin": "55", "Wyoming": "56",
    "District of Columbia": "11"
}

def generate_census_based_summary(basin_name, time_start, time_end):
    """
    Fetch ACS data for the state corresponding to basin_name (fallback to Texas)
    using state FIPS mapping, then generate a summary via OpenAI.
    """

    print("[INFO] Starting census-based summary generation.")

    # 1. Derive year from time_start
    try:
        if hasattr(time_start, "year"):
            year = time_start.year
        else:
            year = int(str(time_start).split("-")[0])
    except Exception as e:
        print(f"[WARN] Could not parse year from time_start: {e}")
        year = 2025

    # 2. Determine state and FIPS code
    state = basin_name if basin_name in STATE_FIPS else "Texas"
    state_fips = STATE_FIPS.get(state, STATE_FIPS["Texas"])
    print(f"[INFO] Using state {state} with FIPS {state_fips}")

    # 3. Prepare Census API variables
    census_api_key = os.environ.get("CENSUS_API_KEY")
    if not census_api_key:
        print("[WARN] No Census API key found — using fallback mock data.")
        census_details = {
            "Population": "N/A",
            "Median Age": "N/A",
            "Male Population": "N/A",
            "Female Population": "N/A",
            "Median Household Income": "N/A",
            "Disability Estimate": "N/A",
            "Veterans Estimate": "N/A",
            "Average Household Size": "N/A",
            "Vehicles Available Ratio": "N/A",
        }
    else:
        try:
            # List of ACS variables we want (example codes)
            vars_list = [
                "B01003_001E",  # total population
                "B01002_001E",  # median age
                "B01001_002E",  # male pop
                "B01001_026E",  # female pop
                "B19013_001E",  # median household income
                "C18108_001E",  # estimate: disability
                "B21001_002E",  # veterans (civilian population + veterans)
                "B25010_001E",  # average household size
                "B08201_002E"   # households with no vehicle (or vehicles) — example
            ]
            get_vars = ",".join(vars_list + ["NAME"])
            url = (
                f"https://api.census.gov/data/{year}/acs/acs5"
                f"?get={get_vars}"
                f"&for=state:{state_fips}"
                f"&key={census_api_key}"
            )
            resp = requests.get(url)
            arr = resp.json()
            # arr[0] is header, arr[1] is data row
            header = arr[0]
            row = arr[1]
            census_details = {header[i]: row[i] for i in range(len(header))}
        except Exception as e:
            print(f"[ERROR] Census API fetch failed: {e}")
            census_details = {
                "Population": "N/A",
                "Median Age": "N/A",
                "Male Population": "N/A",
                "Female Population": "N/A",
                "Median Household Income": "N/A",
                "Disability Estimate": "N/A",
                "Veterans Estimate": "N/A",
                "Average Household Size": "N/A",
                "Vehicles Available Ratio": "N/A",
            }

    print("[INFO] Census details:", census_details)

    # 4. Generate prompt for OpenAI summary
    prompt = f"""
You are a hydrology & flood evacuation expert. Summarize the following demographic data
for {state} (time period: {time_start} — {time_end}). Then explain how these factors
impact hydrological modeling, flood risk, and evacuation planning:

Data:
{json.dumps(census_details, indent=2)}
"""

    openai_api_key = os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=openai_api_key)
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3
        )
        summary = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[WARN] OpenAI summary failed: {e}")
        summary = "Summary not available due to API error."

    return summary
  
from crewai import LLM

def _get_crewai_llm(model_name: str, *, temperature: float = 0.7) -> LLM:
    """Return a CrewAI‑compatible LLM wrapper while avoiding `provider` leaks
    to endpoints that reject unknown parameters (OpenAI, Anthropic)."""
    provider = _detect_provider(model_name)

    # 1) Gemini ─ ensure "gemini/" prefix, rely on LiteLLM auto‑routing
    if provider == "gemini":
        model_name = _ensure_gemini_route(model_name)
        return LLM(model=model_name, temperature=temperature)

    # 2) OpenAI – DO NOT pass provider (causes 400)
    if provider == "openai":
        return LLM(model=model_name, temperature=temperature)

    # 3) Anthropic – same, DO NOT pass provider to avoid "provider: extra inputs"
    if provider == "anthropic":
        return LLM(model=model_name, temperature=temperature)

    # 4) DeepSeek – needs explicit provider flag
    if provider == "deepseek":
        return LLM(model=model_name, provider="deepseek", temperature=temperature)

    # 5) Anything else -> error
    raise ValueError(f"Unsupported provider for model '{model_name}'")(model=model_name, temperature=temperature)

def _ensure_gemini_route(name: str) -> str:
    """Prepends the gemini/ route if missing so LiteLLM can infer provider."""
    return name if name.startswith("gemini/") else f"gemini/{name}"

def final_report_writer(args, crest_args, agents_config, tasks_config, iteration_num=0):
    
    # Create LLM instance using specified model
    temp_flag = False
    if args.llm_model_name == 'gemini-2.5-flash-preview-05-20':
        args.llm_model_name = 'gemini-2.0-flash'
        temp_flag = True
    llm = _get_crewai_llm(args.llm_model_name, temperature=0.7)
    report_for_figures_md = make_report_for_figures(args.figure_path, model=args.llm_model_name, temperature=0.7)
    summary = generate_simulation_summary(args, crest_args)
    report_writer = Agent(
        role=agents_config['report_writer_agent']['role'],
        goal=agents_config['report_writer_agent']['goal'],
        backstory=agents_config['report_writer_agent']['backstory'],
        allow_delegation=agents_config['report_writer_agent']['allow_delegation'],
        verbose=agents_config['report_writer_agent']['verbose'],
        llm=llm
    )
    report_writer_task = Task(
        description=tasks_config['write_report']['description'].format(
            summary=summary,
            report_for_figures_md=report_for_figures_md,
            basin_name=args.basin_name,
            figure_path=args.figure_path
        ),
        expected_output=tasks_config['write_report']['expected_output'],
        agent=report_writer
    )
    crew = Crew(
        agents=[report_writer],
        tasks=[report_writer_task]
    )
    final_report_md = crew.kickoff()
    
    with open("Hydro_Report.md", "w", encoding="utf-8") as f:
        report_content = str(final_report_md)
        if report_content.startswith("```markdown"):
            report_content = report_content[len("```markdown"):].strip()
        if report_content.endswith("```"):
            report_content = report_content[:-3].strip()
        f.write(report_content)
    
    # Create report directory if it doesn't exist
    if not os.path.exists(args.report_path):
        os.makedirs(args.report_path)

    extra = ['--pdf-engine=xelatex',
         '--variable', 'mainfont=Latin Modern Roman']
    if temp_flag:
        args.llm_model_name = 'gemini-2.5-flash-preview-05-20'
    output_pdf_path = os.path.join(args.report_path, f'Hydro_Report_{args.basin_name.replace(" ", "_")}_{args.llm_model_name}_{iteration_num:02d}.pdf')
    output = pypandoc.convert_file('Hydro_Report.md', 'pdf', outputfile=output_pdf_path,extra_args=extra)
    print(f"PDF report saved to {os.path.abspath(output_pdf_path)}")
    print("=== LLM used for report writing ===\n", report_writer.llm.model)


