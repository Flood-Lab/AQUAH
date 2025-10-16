import os
from datetime import datetime
from typing import Sequence, Union, Optional
import pandas as pd

# Global timeout setting for EF5 process (in seconds)
TIMEOUT_S = 4000

def _detect_container_runtime(preferred: Optional[str] = None) -> str:
    """Return 'container' or 'docker' based on availability, honoring preference.

    Preference can be passed explicitly or via environment variable EF5_RUNTIME.
    """
    import shutil, os
    pref = preferred or os.environ.get("EF5_RUNTIME")
    if pref in ("container", "docker") and shutil.which(pref):
        return pref
    for cmd in ("container", "docker"):
        if shutil.which(cmd):
            return cmd
    raise RuntimeError("Neither 'container' nor 'docker' command found on PATH.")


def _run_ef5_via_container(control_file: str, log_path: Optional[str] = None, image: str = "ef5image", runtime: Optional[str] = None) -> None:
    """Run EF5 via available container runtime, mounting the current working directory.

    Parameters
    ----------
    control_file : str
        Path to control.txt relative to CWD mounted into the container.
    log_path : Optional[str]
        If provided, process output is written here; otherwise goes to stdout.
    image : str
        Container image name that contains EF5 (`ef5` binary in PATH).
    runtime : Optional[str]
        Force a runtime ('container' or 'docker'); if None, auto-detect.
    """
    import subprocess
    from pathlib import Path

    runtime_cmd = _detect_container_runtime(runtime)
    workdir = Path.cwd()
    cmd = [
        runtime_cmd, "run", "--rm",
        "-v", f"{workdir}:/work",
        "-w", "/work",
        image,
        "ef5", control_file,
    ]

    if log_path:
        with open(log_path, "w", encoding="utf-8") as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, text=True)
    else:
        subprocess.run(cmd, check=True)

def generate_control_file(
    time_begin: datetime,
    time_end: datetime,
    time_step: str,
    basic_data_path: str,
    mrms_path: str,
    pet_path: str,
    gauge_id: str,
    gauge_lon: float,
    gauge_lat: float,
    gauges_list: pd.DataFrame,
    usgs_data_path: str = "",
    output_dir: str = "./output",
    # CREST params
    wm: float = 0.0,
    b: float = 0.0,
    im: float = 0.0,
    ke: float = 1.0,
    fc: float = 0.0,
    iwu: float = 0.0,
    # KW params
    under: float = 1.0,
    leaki: float = 0.0,
    th: float = 0.3,
    isu: float = 0.0,
    alpha: float = 1.0,
    beta: float = 1.0,
    alpha0: float = 0.0,
    # I/O params
    control_file_path: str = "control.txt",
    grid_on: bool = False,
) -> str:
    """Generate *control.txt* for CREST with any number of gauges.

    Parameters
    ----------
    gauges_list : pandas.DataFrame
        Must contain **STAID**, **LON**, **LAT** columns.  The *outlet* gauge_id is still
        passed separately for clarity; if present in `gauges_list` it will be de‑duplicated.
    All other parameters are as in earlier versions.
    """

    if not {"STAID", "LNG_GAGE", "LAT_GAGE"}.issubset(gauges_list.columns):
        raise ValueError("gauges_list DataFrame must include STAID, LNG_GAGE, LAT_GAGE columns")

    g_ids = gauges_list["STAID"].astype(str).str.zfill(8).tolist()
    g_lons = gauges_list["LNG_GAGE"].astype(float).tolist()
    g_lats = gauges_list["LAT_GAGE"].astype(float).tolist()
    g_basin_areas = gauges_list["DRAIN_SQKM"].astype(float).tolist()

    outlet_id = str(gauge_id).zfill(8)
    other_gauges = [
        (gid, lon, lat, basin_area)
        for gid, lon, lat, basin_area in zip(g_ids, g_lons, g_lats, g_basin_areas)
        # if gid != outlet_id
    ]

    # Paths → absolute
    basic_data_path = os.path.abspath(basic_data_path)
    mrms_path = os.path.abspath(mrms_path)
    pet_path = os.path.abspath(pet_path)
    usgs_data_path = os.path.abspath(usgs_data_path)
    output_dir = os.path.abspath(output_dir)

    # ---------- Gauge blocks ----------
    def _gauge_block(gid: str, lon: float, lat: float, basin_area: float) -> str:
        return (
            f"[Gauge {gid}]\n"
            f"LON={lon}\nLAT={lat}\n"
            f"OBS={usgs_data_path}/USGS_{gid}_UTC_m3s.csv\n"
            f"OUTPUTTS=TRUE\nWANTCO=TRUE\nBASINAREA={basin_area}\n"
        )

    # gauges_section = _gauge_block(outlet_id, gauge_lon, gauge_lat, basin_area) + "".join(
    #     _gauge_block(gid, lon, lat, basin_area) for gid, lon, lat, basin_area in other_gauges
    # )
    gauges_section = "".join(
        _gauge_block(gid, lon, lat, basin_area) for gid, lon, lat, basin_area in other_gauges
    )

    # ---------- Basin block ----------
    # basin_section = "[Basin 0]\n" + f"GAUGE={outlet_id}\n" + "".join(
    #     f"GAUGE={gid}\n" for gid, *_ in other_gauges
    # ) + "\n"

    basin_section = "[Basin 0]\n" + "".join(
        f"GAUGE={gid}\n" for gid, *_ in other_gauges
    ) + "\n"

    # ---------- Parameter blocks (replicated) ----------
    def _repeat_block(header: str, param_lines: Sequence[str]) -> str:
        lines = [f"[{header}]"]
        # for gid in [outlet_id] + [g[0] for g in other_gauges]:
        for gid in [g[0] for g in other_gauges]:
            lines.append(f"gauge={gid}")
            lines.extend(param_lines)
        return "\n".join(lines) + "\n\n"

    crest_lines = [f"wm={wm}", f"b={b}", f"im={im}", f"ke={ke}", f"fc={fc}", f"iwu={iwu}"]
    kw_lines = [f"under={under}", f"leaki={leaki}", f"th={th}", f"isu={isu}", f"alpha={alpha}", f"beta={beta}", f"alpha0={alpha0}"]

    crest_param_section = _repeat_block("CrestParamSet CrestParam", crest_lines)
    kw_param_section = _repeat_block("kwparamset KWParam", kw_lines)

    # ---------- Task Simu ----------
    task_simu = (
        "[Task Simu]\nSTYLE=SIMU\nMODEL=CREST\nROUTING=KW\nBASIN=0\n"
        f"PRECIP=MRMS\nPET=PET\nOUTPUT={output_dir}\nPARAM_SET=CrestParam\nROUTING_PARAM_Set=KWParam\n"
        f"TIMESTEP={time_step}\n"
    )
    if grid_on:
        task_simu += "OUTPUT_GRIDS=STREAMFLOW\n"
    task_simu += f"TIME_BEGIN={time_begin:%Y%m%d%H%M}\nTIME_END={time_end:%Y%m%d%H%M}\n\n"

    # ---------- Precip forcing template ----------
    fmt_cut = datetime(2020, 10, 15)
    if time_step == "1h":
        unit_precip = "mm/h"
        mrms_file_name = (
            "GaugeCorr_QPE_01H_00.00_YYYYMMDD-HH0000.tif"
            if time_begin < fmt_cut
            else "MultiSensor_QPE_01H_Pass2_00.00_YYYYMMDD-HH0000.tif"
        )
    elif time_step == "1d":
        unit_precip = "mm/d"
        mrms_file_name = "precipitation_MRMS_YYYYMMDD00.tif"
    else:
        raise ValueError("time_step must be '1h' or '1d'")

    # ---------- Combine all ----------
    control_content = (
        f"[Basic]\nDEM={basic_data_path}/dem_clip.tif\nDDM={basic_data_path}/fdir_clip.tif\nFAM={basic_data_path}/facc_clip.tif\n\n"
        "PROJ=geographic\nESRIDDM=true\nSelfFAM=true\n\n"
        f"[PrecipForcing MRMS]\nTYPE=TIF\nUNIT={unit_precip}\nFREQ={time_step}\nLOC={mrms_path}\nNAME={mrms_file_name}\n\n"
        f"[PETForcing PET]\nTYPE=TIF\nUNIT=mm/100d\nFREQ=d\nLOC={pet_path}\nNAME=etYYYYMMDD.tif\n\n"
        + gauges_section
        + basin_section
        + crest_param_section
        + kw_param_section
        + task_simu
        + "[Execute]\nTASK=Simu\n"
    )

    # ---------- Write file ----------
    with open(control_file_path, "w", encoding="utf-8") as fp:
        fp.write(control_content)

    return os.path.abspath(control_file_path)



import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
def visualize_model_results(args, default_flag=False):
    """
    Visualize hydrological model results comparing simulated vs observed discharge.
    
    Parameters:
    -----------
    ts_file : str, optional
        Path to the time series CSV file with model results
    figure_path : str, optional
        Directory to save the plot image as 'results.png' (default: None, plot is not saved)
    """
    if default_flag:
        ts_file=os.path.join(args.crest_output_path, 'default', f'ts.{args.gauge_id}.{args.water_balance_type}.csv')
    else:
        ts_file=os.path.join(args.crest_output_path, f'ts.{args.gauge_id}.{args.water_balance_type}.csv')
    figure_path=args.figure_path
    # Check if file exists
    if not os.path.exists(ts_file):
        print(f"Error: Results file not found at {ts_file}")
        return False
        
    # Read the CSV file
    try:
        df = pd.read_csv(ts_file)
    except Exception as e:
        print(f"Error reading results file: {str(e)}")
        return False
    
    print(f"Visualizing model results from: {os.path.abspath(ts_file)}")
    
    # Get performance metrics to display on the plot
    metrics = evaluate_model_performance(args, default_flag)
    cc = metrics.get('CC', 'N/A')
    nsce = metrics.get('NSCE', 'N/A')
    kge = metrics.get('KGE', 'N/A')
    bias = metrics.get('Bias', 'N/A')
    
    # Create the figure with two y-axes
    fig, ax1 = plt.subplots(figsize=(12, 4))
    ax2 = ax1.twinx()

    # Plot precipitation as bar chart on right y-axis (inverted) - at bottom layer
    ax2.bar(range(len(df)), df['Precip(mm h^-1)'], color='blue', alpha=0.6, width=1.0, label='Precipitation', zorder=1)
    ax2.set_ylabel('Precipitation (mm/h)')
    ax2.invert_yaxis()  # Invert the y-axis so 0 is at top
    # Set y-axis limit so maximum precipitation value occupies 80% of the axis (inverted)
    max_precip = df['Precip(mm h^-1)'].max()
    ax2.set_ylim(max_precip / 0.8, 0)  # Set inverted y-axis limits

    # Plot discharge on left y-axis - on top layer
    # Fill area under simulated discharge curve with light blue
    ax1.fill_between(df['Time'], 0, df['Discharge(m^3 s^-1)'], color='skyblue', alpha=0.3, zorder=2)
    # Plot simulated discharge as black line
    ax1.plot(df['Time'], df['Discharge(m^3 s^-1)'], label='Simulated', linewidth=1, color='black', zorder=3)
    ax1.scatter(df['Time'], df['Observed(m^3 s^-1)'], label='Observed', s=5, color='red', zorder=4)
    ax1.set_xlabel('Time')
    ax1.set_ylabel('Discharge (m³/s)')
    # Set y-axis limit so maximum discharge value (observed and simulated) occupies 80% of the axis
    max_observed = df['Observed(m^3 s^-1)'].max()
    max_simulated = df['Discharge(m^3 s^-1)'].max()
    max_discharge = max(max_observed, max_simulated)
    ax1.set_ylim(0, max_discharge / 0.8)

    # Add legends
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper right')

    # Add metrics to the upper left corner
    ax1.text(0.02, 0.98, f"CC: {cc:.3f}\nNSCE: {nsce:.3f}\nKGE: {kge:.3f}\nBias: {bias:.3f}", 
             transform=ax1.transAxes, fontsize=10, verticalalignment='top',
             bbox=dict(boxstyle='round', facecolor='white', alpha=0.7))

    # Set title
    plt.title(f'{args.basin_name} - Gauge {args.gauge_id}: Simulated vs Observed Discharge with Precipitation')

    # Set x-axis limits to first and last time points
    ax1.set_xlim(df['Time'].iloc[0], df['Time'].iloc[-1])

    # Reduce x-axis density and rotate labels
    step = 24  # Show every 24th tick
    ax1.set_xticks(range(0, len(df), step))
    ax1.set_xticklabels([t.split()[0] for t in df['Time'][::step]], rotation=45, ha='right')

    # Adjust layout to prevent label cutoff
    plt.tight_layout()

    # Save the plot to figure_path as 'results.png' with dpi=300
    if figure_path is not None:
        if default_flag:
            save_path = os.path.join(figure_path, 'results_default.png')
        else:
            save_path = os.path.join(figure_path, 'results.png')
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"Plot saved to {os.path.abspath(save_path)}")
    plt.show()
    
    # Create a second figure with log scale y-axis (semilogy)
    fig2, ax3 = plt.subplots(figsize=(12, 4))
    ax4 = ax3.twinx()

    # Plot precipitation as bar chart on right y-axis (inverted) - at bottom layer
    ax4.bar(range(len(df)), df['Precip(mm h^-1)'], color='blue', alpha=0.6, width=1.0, label='Precipitation', zorder=1)
    ax4.set_ylabel('Precipitation (mm/h)')
    ax4.invert_yaxis()  # Invert the y-axis so 0 is at top
    # Set y-axis limit so maximum precipitation value occupies 80% of the axis (inverted)
    ax4.set_ylim(max_precip / 0.8, 0)  # Set inverted y-axis limits

    # Plot discharge on left y-axis with log scale - on top layer
    # Plot simulated discharge as black line
    ax3.plot(df['Time'], df['Discharge(m^3 s^-1)'], label='Simulated', linewidth=1, color='black', zorder=3)
    # Apply log scale to observed data and plot
    observed_log = df['Observed(m^3 s^-1)'].apply(lambda x: max(x, 0.001) if not np.isnan(x) else np.nan)
    ax3.scatter(df['Time'], observed_log, label='Observed', s=5, color='red', zorder=4)
    ax3.set_xlabel('Time')
    ax3.set_ylabel('Discharge (m³/s) - Log Scale')
    ax3.set_yscale('log')  # Set y-axis to log scale

    # Add legends
    lines3, labels3 = ax3.get_legend_handles_labels()
    lines4, labels4 = ax4.get_legend_handles_labels()
    ax3.legend(lines3 + lines4, labels3 + labels4, loc='upper right')

    # Add metrics to the upper left corner
    ax3.text(0.02, 0.98, f"CC: {cc:.3f}\nNSCE: {nsce:.3f}\nKGE: {kge:.3f}\nBias: {bias:.3f}", 
             transform=ax3.transAxes, fontsize=10, verticalalignment='top',
             bbox=dict(boxstyle='round', facecolor='white', alpha=0.7))

    # Set title
    plt.title(f'{args.basin_name} - Gauge {args.gauge_id}: Simulated vs Observed Discharge with Precipitation (Log Scale)')

    # Set x-axis limits to first and last time points
    ax3.set_xlim(df['Time'].iloc[0], df['Time'].iloc[-1])

    # Reduce x-axis density and rotate labels
    ax3.set_xticks(range(0, len(df), step))
    ax3.set_xticklabels([t.split()[0] for t in df['Time'][::step]], rotation=45, ha='right')

    # Adjust layout to prevent label cutoff
    plt.tight_layout()

    # Save the log scale plot to figure_path as 'results_log.png' with dpi=300
    if figure_path is not None:
        if default_flag:
            save_path_log = os.path.join(figure_path, 'results_default_log.png')
        else:
            save_path_log = os.path.join(figure_path, 'results_log.png')
        plt.savefig(save_path_log, dpi=300, bbox_inches='tight')
        print(f"Log scale plot saved to {os.path.abspath(save_path_log)}")
    plt.show()

    
def evaluate_model_performance(args, default_flag=False):
    """
    Evaluate hydrological model performance by calculating statistical metrics
    between simulated and observed discharge.
    
    Parameters:
    -----------
    ts_file : str, optional
        Path to the time series CSV file with model results
        
    Returns:
    --------
    dict: Dictionary containing the calculated performance metrics
    """
    if default_flag:
        ts_file=os.path.join(args.crest_output_path, 'default', f'ts.{args.gauge_id}.{args.water_balance_type}.csv')
    else:
        ts_file=os.path.join(args.crest_output_path, f'ts.{args.gauge_id}.{args.water_balance_type}.csv')
    # Check if file exists
    if not os.path.exists(ts_file):
        print(f"Error: Results file not found at {ts_file}")
        return None
        
    # Read the CSV file
    try:
        df = pd.read_csv(ts_file)
    except Exception as e:
        print(f"Error reading results file: {str(e)}")
        return None
    
    print(f"Evaluating model performance from: {os.path.abspath(ts_file)}")
    
    # Extract simulated and observed discharge
    sim = df['Discharge(m^3 s^-1)'].values
    obs = df['Observed(m^3 s^-1)'].values
    
    # Remove any rows where either simulated or observed values are NaN
    valid_indices = ~(np.isnan(sim) | np.isnan(obs))
    sim = sim[valid_indices]
    obs = obs[valid_indices]
    
    if len(sim) == 0 or len(obs) == 0:
        print("Error: No valid data points after removing NaN values")
        return None
    
    # Calculate Root Mean Square Error (RMSE)
    rmse = np.sqrt(np.mean((sim - obs) ** 2))
    
    # Calculate Bias (as percentage)
    bias = np.mean(sim - obs)
    bias_percent = (bias / np.mean(obs)) * 100
    
    # Calculate Correlation Coefficient (CC)
    cc = np.corrcoef(sim, obs)[0, 1]
    
    # Calculate Nash-Sutcliffe Coefficient of Efficiency (NSCE)
    mean_obs = np.mean(obs)
    nsce = 1 - (np.sum((sim - obs) ** 2) / np.sum((obs - mean_obs) ** 2))
    
    # Calculate Kling-Gupta Efficiency (KGE)
    # KGE = 1 - sqrt((r-1)^2 + (alpha-1)^2 + (beta-1)^2)
    # where r = correlation coefficient, alpha = std(sim)/std(obs), beta = mean(sim)/mean(obs)
    r = cc  # correlation coefficient
    alpha = np.std(sim) / np.std(obs)  # variability ratio
    beta = np.mean(sim) / np.mean(obs)  # bias ratio
    kge = 1 - np.sqrt((r - 1)**2 + (alpha - 1)**2 + (beta - 1)**2)
    
    # Create a dictionary with the metrics
    metrics = {
        'RMSE': rmse,
        'Bias': bias,
        'Bias_percent': bias_percent,
        'CC': cc,
        'NSCE': nsce,
        'KGE': kge
    }
    
    # Print the metrics
    print("\nModel Performance Metrics:")
    print(f"RMSE: {rmse:.4f} m³/s")
    print(f"Bias: {bias:.4f} m³/s ({bias_percent:.2f}%)")
    print(f"CC: {cc:.4f}")
    print(f"NSCE: {nsce:.4f}")
    print(f"KGE: {kge:.4f}")
    
    return metrics


def crest_run(args,crest_args):
    if not os.path.exists(args.crest_output_path):
        os.makedirs(args.crest_output_path)
    generate_control_file(
        time_begin=args.time_start,
        time_end=args.time_end,
        time_step=args.time_step,
        basic_data_path=args.basic_data_clip_path,
        mrms_path=args.crest_input_mrms_path,
        pet_path=args.crest_input_pet_path,
        gauge_id=args.gauge_id,
        gauge_lon=args.longitude_gauge,
        gauge_lat=args.latitude_gauge,
        gauges_list=args.gauges_list,
        usgs_data_path=args.usgs_data_path,
        # basin_area=args.basin_area,
        output_dir=args.crest_output_path,
        wm=crest_args.wm,
        b=crest_args.b,
        im=crest_args.im,
        ke=crest_args.ke,
        fc=crest_args.fc,
        iwu=crest_args.iwu,
        under=crest_args.under,
        leaki=crest_args.leaki,
        th=crest_args.th,
        isu=crest_args.isu,
        alpha=crest_args.alpha,
        beta=crest_args.beta,
        alpha0=crest_args.alpha0,
        grid_on=crest_args.grid_on,
        control_file_path=args.control_file_path,
    )
    # import platform
    # import subprocess

    # if platform.system() == 'Windows':
    #     # Windows path
    #     ef5_exe_path = os.path.join(os.getcwd(), "ef5_64.exe")
        
    #     if not os.path.isfile(ef5_exe_path):
    #         print(f"{ef5_exe_path} not found. Please make sure ef5_64.exe is in the current folder.")
    #     else:
    #         try:
    #             result = subprocess.run([ef5_exe_path], check=True)
    #             print("ef5_64.exe ran successfully.")
    #         except subprocess.CalledProcessError as e:
    #             print(f"Error running ef5_64.exe, return code: {e.returncode}")
    #         except Exception as e:
    #             print(f"An exception occurred while running ef5_64.exe: {e}")
    from pathlib import Path
    control_path = "control.txt"
    out_dir = Path(args.crest_output_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file = out_dir / "ef5_run.log"
    try:
        runtime = getattr(args, 'container_runtime', None)
        _run_ef5_via_container(control_path, str(log_file), runtime=runtime)
    except Exception as e:
        with open(log_file, "a", encoding="utf-8") as log:
            log.write(f"\nEF5 docker run failed: {e}\n")
        raise


    visualize_model_results(args)
    args.metrics = evaluate_model_performance(args)



def generate_control_file_default(
    time_begin,
    time_end,
    time_step,
    basic_data_path,
    mrms_path,
    pet_path,
    gauge_id,
    gauge_lon,
    gauge_lat,
    usgs_data_path,
    basin_area,
    output_dir,
    grid_on=False,
    control_file_path='control.txt',
):
    """
    Generate a control.txt file for CREST model with variable parameters.
    
    Args:
        time_begin (datetime): Simulation start time
        time_end (datetime): Simulation end time
        basic_data_path (str): Path to basic data directory containing DEM, flow direction and flow accumulation
        mrms_path (str): Path to MRMS precipitation data directory
        pet_path (str): Path to PET data directory
        gauge_id (str): USGS gauge ID
        gauge_lon (float): Gauge longitude
        gauge_lat (float): Gauge latitude
        usgs_data_path (str): Path to USGS data directory
        basin_area (float): Basin area in square kilometers
        output_dir (str): Output directory path for model results
        control_file_path (str): Path to save the control file
        grid_on (bool): Whether to output grid files for streamflow
        
    Returns:
        str: Absolute path to the generated control file
    """
    # Convert all paths to absolute paths
    basic_data_path = os.path.abspath(basic_data_path)
    mrms_path = os.path.abspath(mrms_path)
    pet_path = os.path.abspath(pet_path)
    usgs_data_path = os.path.abspath(usgs_data_path)
    output_dir = os.path.abspath(output_dir)
    
    # Prepare the Task Simu section with optional output_grids parameter
    task_simu = """[Task Simu]
    STYLE=SIMU
    MODEL=CREST
    ROUTING=KW
    BASIN=0
    PRECIP=MRMS
    PET=PET
    OUTPUT={output_dir}
    PARAM_SET=CrestParam
    ROUTING_PARAM_Set=KWParam
    TIMESTEP={time_step}
    """
        
    # Add OUTPUT_GRIDS parameter if grid_on is True
    if grid_on:
        task_simu += "OUTPUT_GRIDS=STREAMFLOW\n"
    
    task_simu += """
    TIME_BEGIN={time_begin}
    TIME_END={time_end}
    """
        
    # Determine file format based on date
    format_change_date = datetime(2020, 10, 15)
    if time_step == '1h':
        unit_precip = 'mm/h'
        if time_begin < format_change_date:
            # Before October 15, 2020: GaugeCorr format
            mrms_file_name = "GaugeCorr_QPE_01H_00.00_YYYYMMDD-HH0000.tif"
        else:
            # October 15, 2020 and after: MultiSensor format
            mrms_file_name = "MultiSensor_QPE_01H_Pass2_00.00_YYYYMMDD-HH0000.tif"
    elif time_step == '1d':
        unit_precip = 'mm/d'
        mrms_file_name = "precipitation_MRMS_YYYYMMDD00.tif"

        
        control_content = f"""[Basic]
    DEM={basic_data_path}/dem_clip.tif
    DDM={basic_data_path}/fdir_clip.tif
    FAM={basic_data_path}/facc_clip.tif

    PROJ=geographic
    ESRIDDM=true
    SelfFAM=true

    [PrecipForcing MRMS]
    TYPE=TIF
    UNIT={unit_precip}
    FREQ={time_step}
    LOC={mrms_path}
    NAME={mrms_file_name}

    [PETForcing PET]
    TYPE=TIF
    UNIT=mm/100d
    FREQ=d
    LOC={pet_path}
    NAME=etYYYYMMDD.tif

    [Gauge {gauge_id}] 
    LON={gauge_lon}
    LAT={gauge_lat}
    OBS={usgs_data_path}/USGS_{gauge_id}_UTC_m3s.csv
    OUTPUTTS=TRUE
    BASINAREA={basin_area}
    WANTCO=TRUE

    [Basin 0]
    GAUGE={gauge_id}

    [CrestParamSet CrestParam]
    gauge={gauge_id}
    wm_grid=default_param/crest_params/wm_usa.tif
    im_grid=default_param/crest_params/im_usa.tif
    fc_grid=default_param/crest_params/ksat_usa.tif
    b_grid=default_param/crest_params/b_usa.tif
    wm=1.0
    b=1.0
    im=0.01 
    ke=1.0
    fc=1.00 
    iwu=75.0

    [kwparamset KWParam]
    gauge={gauge_id}
    under_grid=default_param/kw_params/ksat_usa.tif
    leaki_grid=default_param/kw_params/leaki_usa.tif
    alpha_grid=default_param/kw_params/alpha_usa.tif
    beta_grid=default_param/kw_params/beta_usa.tif
    alpha0_grid=default_param/kw_params/alpha0_usa.tif
    alpha0=1.0
    alpha=1.0
    beta=1.0
    under=0.0001
    leaki=1.0
    th=10.0
    isu=00.0

    {task_simu.format(
        output_dir=output_dir,
        time_begin=time_begin.strftime('%Y%m%d%H%M'),
        time_end=time_end.strftime('%Y%m%d%H%M'),
        time_step=time_step
    )}

    [Execute]
    TASK=Simu
    """

    # Write the content to the control file
    with open(control_file_path, 'w') as f:
        f.write(control_content)
    
    # Return the absolute path of the control file
    return os.path.abspath(control_file_path)

def crest_run_default(args):
    default_output_path = os.path.join(args.crest_output_path, 'default')
    if not os.path.exists(default_output_path):
        os.makedirs(default_output_path)
    generate_control_file_default(
        time_begin=args.time_start,
        time_end=args.time_end,
        time_step=args.time_step,
        basic_data_path=args.basic_data_clip_path,
        mrms_path=args.crest_input_mrms_path,
        pet_path=args.crest_input_pet_path,
        gauge_id=args.gauge_id,
        gauge_lon=args.longitude_gauge,
        gauge_lat=args.latitude_gauge,
        usgs_data_path=args.usgs_data_path,
        basin_area=args.basin_area,
        output_dir=default_output_path,
        grid_on=args.grid_on,
        control_file_path=args.control_file_path,
    )

    from pathlib import Path
    control_path = "control.txt"
    out_dir = Path(args.crest_output_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file = out_dir / "ef5_run.log"
    try:
        runtime = getattr(args, 'container_runtime', None)
        _run_ef5_via_container(control_path, str(log_file), runtime=runtime)
    except Exception as e:
        with open(log_file, "a", encoding="utf-8") as log:
            log.write(f"\nEF5 docker run failed: {e}\n")
        raise


    visualize_model_results(args, default_flag=True)
    args.metrics = evaluate_model_performance(args, default_flag=True)



def generate_control_file_cali(
    time_begin: datetime,
    time_end: datetime,
    time_step: str,
    basic_data_path: str,
    mrms_path: str,
    pet_path: str,
    gauges_list: pd.DataFrame,
    usgs_data_path: str = "",
    output_dir: str = "./output",
    simu_param_csv_path: str = "EF5_tools/simu_param_summary_aug.csv",
    control_file_path: str = "control.txt",
    grid_on: bool = False,
    warmup_flag: bool = False,
    warmup_time_begin: datetime = None,
    warmup_time_end: datetime = None,
    warmup_time_step: str = None,
    warmup_state_folder: str = None,
    water_balance_type: str = "crestphys",
) -> str:

    """
    Read the calibrated parameter table (simu_param_summary.csv),
    automatically fill in the [CrestParamSet] and [kwparamset] blocks for each station in *gauges_list*,
    with the rest of the file writing logic similar to generate_control_file.
    """

    # ------- Read calibration parameters -------
    param_df = pd.read_csv(simu_param_csv_path)

    # Standardize field names
    if "station" in param_df.columns:
        param_df.rename(columns={"station": "STAID"}, inplace=True)

    required_cols = {
        "STAID",
        "wm",
        "b",
        "im",
        "ke",
        "fc",
        "iwu",
        "igw",
        "hmaxaq",
        "gwc",
        "gwe",
        "under",
        "leaki",
        "th",
        "isu",
        "alpha",
        "beta",
        "alpha0",
    }
    missing = required_cols.difference(param_df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # ------- Basic information -------
    g_ids = gauges_list["STAID"].astype(str).str.zfill(8).tolist()
    g_lons = gauges_list["LNG_GAGE"].astype(float).tolist()
    g_lats = gauges_list["LAT_GAGE"].astype(float).tolist()
    g_basin_areas = gauges_list["DRAIN_SQKM"].astype(float).tolist()

    # Paths → absolute
    basic_data_path = os.path.abspath(basic_data_path)
    mrms_path = os.path.abspath(mrms_path)
    pet_path = os.path.abspath(pet_path)
    usgs_data_path = os.path.abspath(usgs_data_path)
    output_dir = os.path.abspath(output_dir)

    # ------- Gauge block -------
    def _gauge_block(gid: str, lon: float, lat: float, basin_area: float) -> str:
        return (
            f"[Gauge {gid}]\n"
            f"LON={lon}\nLAT={lat}\n"
            f"OBS={usgs_data_path}/USGS_{gid}_UTC_m3s.csv\n"
            f"OUTPUTTS=TRUE\nWANTCO=TRUE\nBASINAREA={basin_area}\n"
        )

    gauges_section = "".join(
        _gauge_block(gid, lon, lat, basin_area)
        for gid, lon, lat, basin_area in zip(g_ids, g_lons, g_lats, g_basin_areas)
    )

    # ------- Basin block -------
    basin_section = "[Basin 0]\n" + "".join(f"GAUGE={gid}\n" for gid in g_ids) + "\n"

    # ------- Crest & KW parameter blocks -------
    def _crest_param_lines(row) -> Sequence[str]:
        """List of CrestParam lines for a single Gauge"""
        lines = [
            "WM_GRID=default_param/crest_params/wm_usa.tif",
            "IM_GRID=default_param/crest_params/im_usa.tif",
            "FC_GRID=default_param/crest_params/ksat_usa.tif",
            "B_GRID=default_param/crest_params/b_usa.tif",
            f"wm={row.wm}",
            f"b={row.b}",
            f"im={row.im}",
            f"ke={row.ke}",
            f"fc={row.fc}",
            f"iwu={row.iwu}",
        ]

        if water_balance_type.lower() == 'crestphys':
            lines.extend([
                f"igw={row.igw}",
                f"hmaxaq={row.hmaxaq}",
                f"gwc={row.gwc}",
                f"gwe={row.gwe}",
                "ksoil=0.12",  # fixed value
            ])

        return lines
    def _kw_param_lines(row) -> Sequence[str]:
        """List of KWParam lines for a single Gauge"""
        return [
            "leaki_grid=default_param/kw_params/leaki_usa.tif",
            "alpha_grid=default_param/kw_params/alpha_usa.tif",
            "beta_grid=default_param/kw_params/beta_usa.tif",
            "alpha0_grid=default_param/kw_params/alpha0_usa.tif",
            f"under={row.under}",
            f"leaki={row.leaki}",
            f"th={row.th}",
            f"isu={row.isu}",
            f"alpha={row.alpha}",
            f"beta={row.beta}",
            f"alpha0={row.alpha0}"
        ]

    crest_lines = [f"[{water_balance_type}ParamSet CrestParam]"]
    kw_lines = ["[kwparamset KWParam]"]

    for gid in g_ids:
        row = param_df.loc[param_df["STAID"] == int(gid)].squeeze()
        if row.empty:
            raise ValueError(f"Station {gid} calibration parameters not found in {simu_param_csv_path}")

        crest_lines.append(f"gauge={gid}")
        crest_lines.extend(_crest_param_lines(row))
        kw_lines.append(f"gauge={gid}")
        kw_lines.extend(_kw_param_lines(row))

    crest_param_section = "\n".join(crest_lines) + "\n\n"
    kw_param_section = "\n".join(kw_lines) + "\n\n"

    # ------- Task Simu -------
    task_simu = (
        f"[Task Simu]\nSTYLE=SIMU\nMODEL={water_balance_type}\nROUTING=KW\nBASIN=0\n"
        f"PRECIP=MRMS\nPET=PET\nOUTPUT={output_dir}\nPARAM_SET=CrestParam\nROUTING_PARAM_Set=KWParam\n"
        f"TIMESTEP={time_step}\n"
    )
    if grid_on:
        task_simu += "OUTPUT_GRIDS=STREAMFLOW\n"
    if warmup_flag:
        task_simu += f"STATES={warmup_state_folder}\n"
    task_simu += f"TIME_BEGIN={time_begin:%Y%m%d%H%M}\nTIME_END={time_end:%Y%m%d%H%M}\n\n"


    # ------- Task Warmup -------
    if warmup_flag:
        
        task_warmup = (
            f"[Task Warmup]\nSTYLE=SIMU\nMODEL={water_balance_type}\nROUTING=KW\nBASIN=0\n"
            f"PRECIP=MRMS\nPET=PET\nOUTPUT={output_dir}\nPARAM_SET=CrestParam\nROUTING_PARAM_Set=KWParam\n"
            f"TIMESTEP={warmup_time_step}\nSTATES={warmup_state_folder}\nTIME_STATE={time_begin:%Y%m%d%H%M}\n"
        )
        if grid_on:
            task_warmup += "OUTPUT_GRIDS=STREAMFLOW\n"
        task_warmup += f"TIME_BEGIN={warmup_time_begin:%Y%m%d%H%M}\nTIME_END={warmup_time_end:%Y%m%d%H%M}\n\n"
    else:
        task_warmup = ""

    # ------- Precip forcing filename templates -------
    fmt_cut = datetime(2020, 10, 15)
    if time_step == "1h":
        unit_precip = "mm/h"
        mrms_file_name = (
            "GaugeCorr_QPE_01H_00.00_YYYYMMDD-HH0000.tif"
            if time_begin < fmt_cut
            else "MultiSensor_QPE_01H_Pass2_00.00_YYYYMMDD-HH0000.tif"
        )
    elif time_step == "1d":
        unit_precip = "mm/d"
        mrms_file_name = "precipitation_MRMS_YYYYMMDD00.tif"
    else:
        raise ValueError("time_step must be '1h' or '1d'")

    execute_task = "[Execute]\nTASK=Simu\n"
    if warmup_flag:
        execute_task = "[Execute]\nTASK=Warmup\nTASK=Simu\n"

    # ------- Combine all sections -------
    control_content = (
        f"[Basic]\nDEM={basic_data_path}/dem_clip.tif\nDDM={basic_data_path}/fdir_clip.tif\nFAM={basic_data_path}/facc_clip.tif\n\n"
        "PROJ=geographic\nESRIDDM=true\nSelfFAM=true\n\n"
        f"[PrecipForcing MRMS]\nTYPE=TIF\nUNIT={unit_precip}\nFREQ={time_step}\nLOC={mrms_path}\nNAME={mrms_file_name}\n\n"
        f"[PETForcing PET]\nTYPE=TIF\nUNIT=mm/100d\nFREQ=d\nLOC={pet_path}\nNAME=etYYYYMMDD.tif\n\n"
        + gauges_section
        + basin_section
        + crest_param_section
        + kw_param_section
        + task_warmup
        + task_simu
        + execute_task
    )

    # ------- Write file -------
    with open(control_file_path, "w", encoding="utf-8") as fp:
        fp.write(control_content)

    return os.path.abspath(control_file_path)



def crest_run_cali(args):
    if not os.path.exists(args.crest_output_path):
        os.makedirs(args.crest_output_path)
    control_file_path = generate_control_file_cali(
    time_begin = args.time_start,
    time_end = args.time_end,
    time_step = args.time_step,
    basic_data_path = args.basic_data_clip_path,
    mrms_path = args.crest_input_mrms_path,
    pet_path = args.crest_input_pet_path,
    gauges_list = args.gauges_list,
    usgs_data_path = args.usgs_data_path,
    output_dir = args.crest_output_path,
    warmup_flag = args.warmup_flag,
    warmup_time_begin = args.warmup_time_start,
    warmup_time_end = args.warmup_time_end,
    warmup_time_step = args.warmup_time_step,
    warmup_state_folder = args.warmup_state_folder,
    water_balance_type = args.water_balance_type
    )
    from pathlib import Path
    control_path = "control.txt"
    out_dir = Path(args.crest_output_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_file = out_dir / "ef5_run.log"
    try:
        runtime = getattr(args, 'container_runtime', None)
        _run_ef5_via_container(control_path, str(log_file), runtime=runtime)
    except Exception as e:
        with open(log_file, "a", encoding="utf-8") as log:
            log.write(f"\nEF5 docker run failed: {e}\n")
        raise


    visualize_model_results(args)
    args.metrics = evaluate_model_performance(args)