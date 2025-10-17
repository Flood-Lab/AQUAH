# ======================================
# Basin Selection and Processing Functions
# ======================================
from __future__ import annotations
import requests
import geopandas as gpd
import folium
from shapely.geometry import Point
import matplotlib.pyplot as plt
import contextily as ctx
import os
import rasterio
import numpy as np
import pandas as pd

import shutil
from zipfile import ZipFile
from rasterio.merge import merge
import math


from tqdm import tqdm
from rasterio.windows import from_bounds
from osgeo import gdal
import glob
import sys

# Configure GDAL environment
if sys.platform.startswith('win'):
    # For Windows
    gdal_data = os.path.join(os.path.dirname(sys.executable), 'Library', 'share', 'gdal')
    if os.path.exists(gdal_data):
        os.environ['GDAL_DATA'] = gdal_data
else:
    # For Linux/Mac
    gdal_data = os.path.join(os.path.dirname(sys.executable), 'share', 'gdal')
    if os.path.exists(gdal_data):
        os.environ['GDAL_DATA'] = gdal_data

def download_watershed_shp(latitude, longitude, output_path, level=5):
    """
    Download watershed boundary data for a given latitude and longitude.

    Parameters
    ----------
    latitude : float
        Latitude of the point of interest (WGS-84).
    longitude : float
        Longitude of the point of interest (WGS-84).
    output_path : str
        Shapefile path or directory where output files will be saved.
    level : int, default=5
        WBD hierarchy level:
            1 = HUC2, 2 = HUC4, 3 = HUC6, 4 = HUC8,
            5 = HUC10, 6 = HUC12

    Returns
    -------
    Basin_Area : float
        Area of the watershed in square kilometres.
    Basin_Name : str
        Name of the watershed.
    bbox_coords : tuple
        (min_lon, min_lat, max_lon, max_lat) of the shapefile’s bounding box.
    """
    # ------------------------------------------------------------------ I/O
    dir_to_create = (
        os.path.dirname(output_path)
        if output_path.lower().endswith(".shp")
        else output_path
    )
    if dir_to_create:
        os.makedirs(dir_to_create, exist_ok=True)

    # --------------------------------------------------------------- Query
    wbd_url = f"https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/{level}/query"
    params = {
        "geometry": f"{longitude},{latitude}",  # (lon, lat)
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "f": "geojson",
    }
    response = requests.get(wbd_url, params=params)
    response.raise_for_status()  # stop if request failed
    data = response.json()

    # ----------------------------------------------------------- GeoDataFrame
    gdf = gpd.GeoDataFrame.from_features(data["features"]).set_crs(epsg=4326)

    # Column renaming (keeps <10-char limit for shapefiles)
    gdf = gdf.rename(
        columns={
            "shape_Length": "shp_length",
            "metasourceid": "metasource",
            "sourcedatadesc": "sourcedata",
            "sourceoriginator": "sourceorig",
            "sourcefeatureid": "sourcefeat",
            "referencegnis_ids": "ref_gnis",
        }
    )

    # ----------------------------------------------------------- Save to disk
    gdf.to_file(output_path)

    # ------------------------------------------------------------- Metadata
    Basin_Area = float(gdf.loc[0, "areasqkm"])
    Basin_Name = str(gdf.loc[0, "name"])

    # Bounding box coordinates (minx, miny, maxx, maxy)
    minx, miny, maxx, maxy = gdf.total_bounds
    bbox_coords = (minx, miny, maxx, maxy)

    # --------------------------------------------------------------- Print
    print(f"Basin Name        : {Basin_Name}")
    print(f"Basin Area (km²)  : {Basin_Area:.2f}")
    print(
        f"Bounding Box (lon/lat): "
        f"min_lon={minx:.6f}, min_lat={miny:.6f}, "
        f"max_lon={maxx:.6f}, max_lat={maxy:.6f}"
    )

    return Basin_Area, Basin_Name, bbox_coords

# def plot_watershed_with_gauges(basin_shp_path, gauge_meta_path, figure_path):
#     """
#     Plot watershed with USGS gauge stations and save both interactive HTML and static PNG (300 dpi) to figure_path.

#     Parameters:
#     -----------
#     basin_shp_path : str
#         Path to the watershed shapefile
#     gauge_meta_path : str
#         Path to the USGS gauge metadata CSV file
#     figure_path : str
#         Directory path where output HTML and PNG files will be saved

#     Returns:
#     --------
#     None
#     """
#     import os
#     import pandas as pd

#     # Ensure output directory exists
#     os.makedirs(figure_path, exist_ok=True)

#     # Load the watershed shapefile and reproject to Web Mercator for plotting
#     gdf_web = gpd.read_file(basin_shp_path).to_crs(epsg=3857)

#     # Calculate centroid in projected CRS, then convert to WGS84 for folium
#     centroid = gdf_web.geometry.unary_union.centroid
#     center_point = gpd.GeoDataFrame(geometry=[centroid], crs='EPSG:3857').to_crs(epsg=4326)
#     center_lat = center_point.geometry.y[0]
#     center_lng = center_point.geometry.x[0]

#     # Create interactive folium map centered on the watershed
#     m = folium.Map(location=[center_lat, center_lng], zoom_start=8)

#     # Remove datetime columns if present (folium/GeoJSON can't handle them)
#     gdf_web = gdf_web.drop(columns=gdf_web.select_dtypes(include=["datetime64[ns]"]).columns)

#     # Add watershed boundary to the folium map
#     folium.GeoJson(
#         gdf_web.to_crs(epsg=4326),
#         name='Watershed Boundary',
#         style_function=lambda x: {'fillColor': 'yellow', 'color': 'red', 'weight': 2, 'fillOpacity': 0.5}
#     ).add_to(m)

#     # Load USGS gauge information
#     gauge_info = pd.read_csv(gauge_meta_path)

#     # Convert gauge locations to GeoDataFrame
#     gauge_points = gpd.GeoDataFrame(
#         gauge_info,
#         geometry=gpd.points_from_xy(gauge_info.LNG_GAGE, gauge_info.LAT_GAGE),
#         crs='EPSG:4326'
#     )

#     # Reproject gauge points to match watershed CRS (Web Mercator)
#     gauge_points = gauge_points.to_crs(epsg=3857)

#     # Spatial join to find gauges within the watershed
#     gauges_in_basin = gpd.sjoin(gauge_points, gdf_web, how='inner', predicate='within')

#     # Create a new matplotlib figure for the static map
#     fig, ax = plt.subplots(figsize=(12, 8))

#     # Plot watershed boundary
#     gdf_web.plot(ax=ax, alpha=0.5, edgecolor='red', facecolor='yellow', linewidth=2)

#     # Add basemap
#     ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik)

#     # Plot centroid as a red star
#     point = Point(center_lng, center_lat)
#     point_gdf = gpd.GeoDataFrame(geometry=[point], crs='EPSG:4326').to_crs(epsg=3857)
#     point_gdf.plot(ax=ax, color='red', marker='*', markersize=100)

#     # Set map extent to focus on the watershed
#     ax.set_xlim(gdf_web.total_bounds[[0, 2]])
#     ax.set_ylim(gdf_web.total_bounds[[1, 3]])

#     # Remove axes for a clean map
#     ax.set_axis_off()

#     # File paths for output
#     html_path = os.path.join(figure_path, 'basin_map_with_gauges.html')
#     png_path = os.path.join(figure_path, 'basin_map_with_gauges.png')

#     if len(gauges_in_basin) > 0:
#         # Plot gauge locations on the static map
#         gauges_in_basin.plot(
#             ax=ax,
#             color='blue',
#             marker='^',
#             markersize=100,
#             label='USGS Gauges'
#         )

#         # Add station IDs as labels on the static map
#         for idx, row in gauges_in_basin.iterrows():
#             padded_staid = str(row['STAID']).zfill(8)
#             ax.annotate(
#                 padded_staid,
#                 xy=(row.geometry.x, row.geometry.y),
#                 xytext=(5, 5),
#                 textcoords='offset points',
#                 color='blue',
#                 fontsize=10,
#                 fontweight='bold'
#             )

#         # Add gauge markers to the interactive map
#         for idx, row in gauges_in_basin.iterrows():
#             padded_staid = str(row['STAID']).zfill(8)
#             folium.Marker(
#                 location=[row['LAT_GAGE'], row['LNG_GAGE']],
#                 popup=f"Station ID: {padded_staid}",
#                 icon=folium.Icon(color='blue', icon='info-sign')
#             ).add_to(m)

#         plt.title('Watershed Boundary with USGS Gauges')
#         ax.legend(loc='upper right')
#         plt.tight_layout()

#         # Save static PNG with 300 dpi
#         plt.savefig(png_path, dpi=300, bbox_inches='tight')
#         plt.close(fig)

#         # Save interactive HTML map
#         m.save(html_path)

#         # Print all station IDs and names
#         print("\nUSGS Gauge Stations in the Watershed:")
#         print("--------------------------------------")
#         for idx, row in gauges_in_basin.iterrows():
#             padded_staid = str(row['STAID']).zfill(8)
#             print(f"Station ID: {padded_staid}, Name: {row['STANAME']}, Latitude: {row['LAT_GAGE']:.2f}, Longitude: {row['LNG_GAGE']:.2f}")

#         print(f"Interactive map saved to {html_path}")
#         print(f"Static PNG map saved to {png_path}")

#     else:
#         plt.title('Watershed Boundary (No USGS Gauges Found)')
#         plt.tight_layout()
#         plt.savefig(png_path, dpi=300, bbox_inches='tight')
#         plt.close(fig)

#         # Save the map even if no gauges found
#         m.save(html_path)

#         print("No USGS gauge stations found within the watershed boundary.")
#         print(f"Interactive map saved to {html_path}")
#         print(f"Static PNG map saved to {png_path}")

from pathlib import Path
from datetime import datetime
from typing import List

def _has_usgs_data(
    site_code: str,
    start_date: datetime,
    end_date: datetime,
    time_step: str = "1d",
) -> bool:
    """
    Lightweight check if a USGS site has discharge data (00060) for the specified time period.
    Does not save or write files, only returns True/False.
    """
    try:
        import dataretrieval.nwis as nwis
    except ImportError:
        raise ImportError("Please install dataretrieval first: pip install dataretrieval")

    service = "dv" if time_step == "1d" else "iv"

    try:
        df = nwis.get_record(
            sites=site_code,
            service=service,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            parameterCd="00060",
            statCd="00003" if service == "dv" else None,
        )
    except Exception as e:
        print(f"USGS request failed for {site_code}: {e}")
        return False

    if df is None or df.empty:
        return False

    # Find actual discharge columns (excluding quality control flag columns ending with 'cd')
    discharge_cols = [c for c in df.columns if "00060" in c and not c.endswith("cd")]
    if not discharge_cols:
        return False

    return df[discharge_cols[0]].notna().any()

def filter_gauges_by_usgs_data(
    gauges_gdf: gpd.GeoDataFrame,
    start_date: datetime,
    end_date: datetime,
    time_step: str = "1d",
) -> gpd.GeoDataFrame:
    """
    Filter all gauges in the GeoDataFrame by checking for available data,
    returning only the subset with confirmed data availability.
    """
    kept_rows: List[pd.Series] = []

    for _, row in gauges_gdf.iterrows():
        sta_id = str(row.STAID).zfill(8)  # USGS station IDs are typically padded to 8 digits
        print(f"Checking station {sta_id}...")
        if _has_usgs_data(sta_id, start_date, end_date, time_step):
            kept_rows.append(row)
            print(f"Station {sta_id} has data, keeping")
        else:
            print(f"Station {sta_id} has no data, removing")

    if kept_rows:
        return (
            gpd.GeoDataFrame(kept_rows, crs=gauges_gdf.crs)
            .reset_index(drop=True)
        )
    else:
        print("No stations have valid data for the specified time period")
        return gpd.GeoDataFrame(columns=gauges_gdf.columns, crs=gauges_gdf.crs)

# def plot_watershed_with_gauges(
#     basin_shp_path: str,
#     gauge_meta_path: str,
#     figure_path: str,
#     buffer_m: int = 20_000,
#     start_date: datetime = datetime(2020, 1, 1),
#     end_date: datetime = datetime(2020, 12, 31),
#     time_step: str = "1d",
# ) -> None:
#     # ------------------------------------------------------------------
#     # 0)  File/dir setup
#     # ------------------------------------------------------------------
#     out_dir = Path(figure_path)
#     out_dir.mkdir(parents=True, exist_ok=True)

#     html_path = out_dir / "basin_map_with_gauges.html"
#     png_path = out_dir / "basin_map_with_gauges.png"

#     # ------------------------------------------------------------------
#     # 1)  Watershed polygon  ➜  Web-Mercator  ➜  buffered copy
#     # ------------------------------------------------------------------
#     gdf = gpd.read_file(basin_shp_path).to_crs(epsg=3857)

#     gdf_buffer = gdf.copy()
#     gdf_buffer["geometry"] = gdf_buffer.buffer(buffer_m)  # outward buffer

#     # Centroid (for map centring) converted to WGS-84
#     centroid_ll = (
#         gpd.GeoSeries([gdf.geometry.unary_union.centroid], crs="EPSG:3857")
#         .to_crs(epsg=4326)
#         .iloc[0]
#     )
#     center_lat, center_lon = centroid_ll.y, centroid_ll.x

#     # ------------------------------------------------------------------
#     # 2)  Gauges  ➜  GeoDataFrame  ➜  Web-Mercator
#     # ------------------------------------------------------------------
#     gauges_df = pd.read_csv(gauge_meta_path)

#     gauges_gdf = gpd.GeoDataFrame(
#         gauges_df,
#         geometry=gpd.points_from_xy(gauges_df.LNG_GAGE, gauges_df.LAT_GAGE),
#         crs="EPSG:4326",
#     ).to_crs(epsg=3857)

#     # Spatial join against the buffered watershed
#     gauges_in_basin = (
#         gpd.sjoin(
#             gauges_gdf,
#             gdf_buffer[["geometry"]],
#             how="inner",
#             predicate="within",
#         )
#         .drop(columns="index_right")
#         .reset_index(drop=True)
#     )

#     # A WGS-84 copy for Folium & console output
#     gauges_wgs = gauges_in_basin.to_crs(epsg=4326)
#     gauges_wgs_copy = gauges_wgs.copy()
#     gauges_wgs = filter_gauges_by_usgs_data(gauges_wgs_copy, start_date, end_date, time_step)
#     # ------------------------------------------------------------------
#     # 3)  Interactive map (Folium)
#     # ------------------------------------------------------------------
#     fmap = folium.Map(location=[center_lat, center_lon], zoom_start=8)

#     # Watershed boundary (original polygon, not buffer)
#     folium.GeoJson(
#         gdf.to_crs(epsg=4326),
#         name="Watershed Boundary",
#         style_function=lambda _: {
#             "fillColor": "#ffffbf",
#             "color": "red",
#             "weight": 2,
#             "fillOpacity": 0.5,
#         },
#     ).add_to(fmap)

#     # Gauges
#     for _, row in gauges_wgs.iterrows():
#         staid = f"{row.STAID:0>8}"
#         folium.Marker(
#             location=[row.geometry.y, row.geometry.x],
#             popup=f"<b>Station {staid}</b><br>{row.STANAME}",
#             icon=folium.Icon(color="blue", icon="info-sign"),
#         ).add_to(fmap)

#     # Save HTML map
#     fmap.save(html_path)

#     # ------------------------------------------------------------------
#     # 4)  Static map (Matplotlib) - with lat/lon coordinates and basemap
#     # ------------------------------------------------------------------
#     # Convert to WGS84 for displaying lat/lon coordinates
#     gdf_wgs84 = gdf.to_crs(epsg=4326)
#     gdf_buffer_wgs84 = gdf_buffer.to_crs(epsg=4326)
    
#     fig, ax = plt.subplots(figsize=(12, 8))

#     # Add basemap using contextily
#     try:
#         import contextily as ctx
        
#         # Watershed polygon
#         gdf_wgs84.plot(ax=ax, facecolor="#ffffbf80", edgecolor="red", linewidth=3)

#         # Gauges (blue triangles)
#         if not gauges_wgs.empty:
#             gauges_wgs.plot(
#                 ax=ax,
#                 marker="^",
#                 color="blue",
#                 markersize=120,
#                 linewidth=0,
#                 label="USGS Gauges",
#             )

#         # Centroid (red star) with coordinates
#         centroid_point = gdf_wgs84.geometry.unary_union.centroid
#         gpd.GeoSeries([centroid_point]).plot(
#             ax=ax,
#             marker="*",
#             color="red",
#             markersize=200,
#             zorder=3,
#         )
        
#         # Frame map to buffered extent so edge gauges are visible
#         bounds = gdf_buffer_wgs84.total_bounds
#         ax.set_xlim(bounds[0], bounds[2])
#         ax.set_ylim(bounds[1], bounds[3])
        
#         # Add basemap after setting the extent
#         ctx.add_basemap(ax, crs=gdf_wgs84.crs.to_string(), source=ctx.providers.OpenStreetMap.Mapnik)
        
#     except ImportError:
#         print("Warning: contextily not available, plotting without basemap")
#         # Watershed polygon
#         gdf_wgs84.plot(ax=ax, facecolor="#ffffbf80", edgecolor="red", linewidth=3)

#         # Gauges (blue triangles)
#         if not gauges_wgs.empty:
#             gauges_wgs.plot(
#                 ax=ax,
#                 marker="^",
#                 color="blue",
#                 markersize=120,
#                 linewidth=0,
#                 label="USGS Gauges",
#             )

#         # Centroid (red star) with coordinates
#         centroid_point = gdf_wgs84.geometry.unary_union.centroid
#         gpd.GeoSeries([centroid_point]).plot(
#             ax=ax,
#             marker="*",
#             color="red",
#             markersize=200,
#             zorder=3,
#         )
        
#         # Frame map to buffered extent so edge gauges are visible
#         bounds = gdf_buffer_wgs84.total_bounds
#         ax.set_xlim(bounds[0], bounds[2])
#         ax.set_ylim(bounds[1], bounds[3])
        
#     except Exception as e:
#         print(f"Warning: Could not add basemap: {e}")
#         # Watershed polygon
#         gdf_wgs84.plot(ax=ax, facecolor="#ffffbf80", edgecolor="red", linewidth=3)

#         # Gauges (blue triangles)
#         if not gauges_wgs.empty:
#             gauges_wgs.plot(
#                 ax=ax,
#                 marker="^",
#                 color="blue",
#                 markersize=120,
#                 linewidth=0,
#                 label="USGS Gauges",
#             )

#         # Centroid (red star) with coordinates
#         centroid_point = gdf_wgs84.geometry.unary_union.centroid
#         gpd.GeoSeries([centroid_point]).plot(
#             ax=ax,
#             marker="*",
#             color="red",
#             markersize=200,
#             zorder=3,
#         )
        
#         # Frame map to buffered extent so edge gauges are visible
#         bounds = gdf_buffer_wgs84.total_bounds
#         ax.set_xlim(bounds[0], bounds[2])
#         ax.set_ylim(bounds[1], bounds[3])

#     # Annotate station IDs only
#     if not gauges_wgs.empty:
#         for _, row in gauges_wgs.iterrows():
#             staid = f"{row.STAID:0>8}"
#             ax.annotate(
#                 staid,
#                 xy=(row.geometry.x, row.geometry.y),
#                 xytext=(5, 5),
#                 textcoords="offset points",
#                 fontsize=14,
#                 weight="bold",
#                 color="blue",
#                 ha='left',
#                 va='bottom',
#                 bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8)
#             )
    
#     # Annotate centroid coordinates
#     centroid_point = gdf_wgs84.geometry.unary_union.centroid
#     ax.annotate(
#         f"Centroid\n({centroid_point.y:.4f}°, {centroid_point.x:.4f}°)",
#         xy=(centroid_point.x, centroid_point.y),
#         xytext=(10, 10),
#         textcoords="offset points",
#         fontsize=16,
#         weight="bold",
#         color="red",
#         ha='left',
#         va='bottom',
#         bbox=dict(boxstyle="round,pad=0.5", facecolor="white", alpha=0.9)
#     )

#     # Set coordinate system and add grid
#     ax.set_xlabel('Longitude (°)', fontsize=20, fontweight='bold')
#     ax.set_ylabel('Latitude (°)', fontsize=20, fontweight='bold')
    
#     # Add grid for better coordinate reading
#     ax.grid(True, alpha=0.3)
#     ax.tick_params(axis='both', which='major', labelsize=14)
    
#     ax.set_title("Watershed Boundary with USGS Gauge Stations", fontsize=18, fontweight='bold')

#     if not gauges_wgs.empty:
#         ax.legend(loc="upper right", fontsize=14)

#     plt.tight_layout()
#     plt.savefig(png_path, dpi=300, bbox_inches="tight")
#     plt.close(fig)

#     # ------------------------------------------------------------------
#     # 5)  Console output
#     # ------------------------------------------------------------------
#     if gauges_wgs.empty:
#         print("⚠️  No USGS gauge stations found within the buffered watershed.")
#     else:
#         print("\nUSGS gauge stations inside buffered watershed")
#         print("--------------------------------------------")
#         for _, row in gauges_wgs.iterrows():
#             staid = f"{row.STAID:0>8}"
#             print(
#                 f"{staid} – {row.STANAME} "
#                 f"({row.geometry.y:.4f}°, {row.geometry.x:.4f}°)"
#             )

#     print(f"\nHTML map saved to {html_path}")
#     print(f"PNG  map saved to {png_path}")
#     return gauges_wgs

def plot_watershed_with_gauges(
    basin_shp_path: str,
    gauge_meta_path: str,
    figure_path: str,
    buffer_m: int = 20_000,
    start_date: datetime = datetime(2020, 1, 1),
    end_date: datetime = datetime(2020, 12, 31),
    time_step: str = "1d",
):
    """Interactive + static map of watershed with USGS gauges.

    Adds Lon/Lat info to pop‑ups, annotations, and console table.
    """

    # ------------------------------------------------------------------
    # 0)  File/dir setup
    # ------------------------------------------------------------------
    out_dir = Path(figure_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "basin_map_with_gauges.html"
    png_path = out_dir / "basin_map_with_gauges.png"

    # ------------------------------------------------------------------
    # 1)  Watershed polygon → Web‑Mercator → buffered copy
    # ------------------------------------------------------------------
    gdf = gpd.read_file(basin_shp_path).to_crs(epsg=3857)
    gdf_buffer = gdf.copy()
    gdf_buffer["geometry"] = gdf_buffer.buffer(buffer_m)

    # Use union_all() to avoid deprecated unary_union warning
    centroid_ll = (
        gpd.GeoSeries([gdf.geometry.union_all().centroid], crs="EPSG:3857")
        .to_crs(epsg=4326)
        .iloc[0]
    )
    center_lat, center_lon = centroid_ll.y, centroid_ll.x

    # ------------------------------------------------------------------
    # 2)  Gauges → GeoDataFrame → Web‑Mercator
    # ------------------------------------------------------------------
    gauges_df = pd.read_csv(gauge_meta_path)
    gauges_gdf = gpd.GeoDataFrame(
        gauges_df,
        geometry=gpd.points_from_xy(gauges_df.LNG_GAGE, gauges_df.LAT_GAGE),
        crs="EPSG:4326",
    ).to_crs(epsg=3857)

    gauges_in_basin = (
        gpd.sjoin(gauges_gdf, gdf_buffer[["geometry"]], how="inner", predicate="within")
        .drop(columns="index_right")
        .reset_index(drop=True)
    )

    gauges_wgs = gauges_in_basin.to_crs(epsg=4326)
    gauges_wgs = filter_gauges_by_usgs_data(gauges_wgs, start_date, end_date, time_step)

    # ------------------------------------------------------------------
    # 3)  Interactive map (Folium)
    # ------------------------------------------------------------------
    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=8)

    folium.GeoJson(
        gdf.to_crs(epsg=4326),
        name="Watershed Boundary",
        style_function=lambda _: {
            "fillColor": "#ffffbf",
            "color": "red",
            "weight": 2,
            "fillOpacity": 0.5,
        },
    ).add_to(fmap)

    for _, row in gauges_wgs.iterrows():
        staid = f"{row.STAID:0>8}"
        popup_html = (
            f"<b>Station {staid}</b><br>{row.STANAME}<br>"
            f"Lon: {row.geometry.x:.4f}&nbsp;°<br>Lat: {row.geometry.y:.4f}&nbsp;°"
        )
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            popup=popup_html,
            icon=folium.Icon(color="blue", icon="info-sign"),
        ).add_to(fmap)

    fmap.save(html_path)

    # ------------------------------------------------------------------
    # 4)  Static PNG map
    # ------------------------------------------------------------------
    gdf_wgs84 = gdf.to_crs(epsg=4326)
    gdf_buffer_wgs84 = gdf_buffer.to_crs(epsg=4326)

    fig, ax = plt.subplots(figsize=(12, 8))

    try:
        import contextily as ctx
        gdf_wgs84.plot(ax=ax, facecolor="#ffffbf80", edgecolor="red", linewidth=3)
        if not gauges_wgs.empty:
            gauges_wgs.plot(ax=ax, marker="^", color="blue", markersize=120, linewidth=0)
        centroid_point = gdf_wgs84.geometry.unary_union.centroid
        gpd.GeoSeries([centroid_point]).plot(ax=ax, marker="*", color="red", markersize=200, zorder=3)
        bounds = gdf_buffer_wgs84.total_bounds
        ax.set_xlim(bounds[0], bounds[2])
        ax.set_ylim(bounds[1], bounds[3])
        ctx.add_basemap(ax, crs=gdf_wgs84.crs.to_string(), source=ctx.providers.OpenStreetMap.Mapnik)
    except Exception:
        gdf_wgs84.plot(ax=ax, facecolor="#ffffbf80", edgecolor="red", linewidth=3)
        if not gauges_wgs.empty:
            gauges_wgs.plot(ax=ax, marker="^", color="blue", markersize=120, linewidth=0)
        centroid_point = gdf_wgs84.geometry.unary_union.centroid
        gpd.GeoSeries([centroid_point]).plot(ax=ax, marker="*", color="red", markersize=200, zorder=3)
        bounds = gdf_buffer_wgs84.total_bounds
        ax.set_xlim(bounds[0], bounds[2])
        ax.set_ylim(bounds[1], bounds[3])

    # Annotate station ID + Lon/Lat
    if not gauges_wgs.empty:
        for _, row in gauges_wgs.iterrows():
            staid = f"{row.STAID:0>8}"
            label = f"{staid}\n({row.geometry.x:.3f}, {row.geometry.y:.3f})"
            ax.annotate(
                label,
                xy=(row.geometry.x, row.geometry.y),
                xytext=(5, 5),
                textcoords="offset points",
                fontsize=12,
                weight="bold",
                color="blue",
                ha="left",
                va="bottom",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8),
            )

    # Annotate centroid
    centroid_point = gdf_wgs84.geometry.unary_union.centroid
    ax.annotate(
        f"Centroid\n({centroid_point.x:.3f}, {centroid_point.y:.3f})",
        xy=(centroid_point.x, centroid_point.y),
        xytext=(10, 10),
        textcoords="offset points",
        fontsize=14,
        weight="bold",
        color="red",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.9),
    )

    ax.set_xlabel("Longitude (°)", fontsize=14, fontweight="bold")
    ax.set_ylabel("Latitude (°)", fontsize=14, fontweight="bold")
    ax.grid(True, alpha=0.3)
    ax.set_title("Watershed Boundary with USGS Gauge Stations", fontsize=16, fontweight="bold")

    plt.tight_layout()
    plt.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    # ------------------------------------------------------------------
    # 5)  Console output
    # ------------------------------------------------------------------
    if gauges_wgs.empty:
        print("⚠️  No USGS gauge stations found within the buffered watershed.")
    else:
        print("\nUSGS gauge stations inside buffered watershed")
        print("--------------------------------------------")
        for _, row in gauges_wgs.iterrows():
            staid = f"{row.STAID:0>8}"
            print(
                f"{staid} – {row.STANAME} "
                f"(Lon: {row.geometry.x:.4f}, Lat: {row.geometry.y:.4f})"
            )

    print(f"\nHTML map saved to {html_path}")
    print(f"PNG  map saved to {png_path}")
    return gauges_wgs



def download_hydrosheds_data(
        bbox_coords,
        dest_folder="../BasicData",
        region="na",    # HydroSHEDS region code: af as au eu na sa
        res_tag="3s"    # Spatial resolution tag
    ):
    """
    Download & mosaic HydroSHEDS tiles (acc / con / dir) covering a bounding box.

    Parameters
    ----------
    bbox_coords : tuple
        (min_lon, min_lat, max_lon, max_lat) in WGS-84.
    dest_folder : str
        Folder for the mosaicked TIFFs (will be wiped / recreated).
    region : str
        Region prefix ('na' = North America, etc.).
    res_tag : str
        Resolution tag in file paths ('3s', '15s', …).

    Returns
    -------
    bool
        True if all OK, False otherwise.
    """
    # ------------------------- helpers
    def get_tile_prefix(lat, lon):
        """Return HydroSHEDS 10° tile code, e.g. 'N30W100'."""
        lat_band = int(math.floor(lat / 10.0) * 10)
        lat_prefix = f"{'N' if lat_band >= 0 else 'S'}{abs(lat_band):02d}"

        lon_band = int(math.ceil(abs(lon) / 10.0) * 10)
        lon_prefix = f"{'W' if lon < 0 else 'E'}{lon_band:03d}"
        return f"{lat_prefix}{lon_prefix}"

    def build_url(prefix, layer):
        subdir = f"hydrosheds-v1-{layer}/{region}_{layer}_{res_tag}"
        fname  = f"{(prefix if layer == 'acc' else prefix.lower())}_{layer}.zip"
        return f"https://data.hydrosheds.org/file/{subdir}/{fname}"

    # ------------------------- prepare folder
    if os.path.exists(dest_folder):
        shutil.rmtree(dest_folder)
    os.makedirs(dest_folder, exist_ok=True)

    # ------------------------- which tiles?
    min_lon, min_lat, max_lon, max_lat = bbox_coords
    corner_pts = [(min_lat, min_lon), (min_lat, max_lon),
                  (max_lat, min_lon), (max_lat, max_lon)]
    prefixes = {get_tile_prefix(lat, lon) for lat, lon in corner_pts}

    # ------------------------- download & unzip
    layer_tifs = {"acc": set(), "con": set(), "dir": set()}

    for layer in ("acc", "con", "dir"):
        for pfx in prefixes:
            url = build_url(pfx, layer)
            try:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
            except Exception as exc:
                print(f"[ERROR] {url} – {exc}")
                return False

            zip_path = os.path.join(dest_folder, os.path.basename(url))
            with open(zip_path, "wb") as f:
                f.write(r.content)

            with ZipFile(zip_path) as zf:
                zf.extractall(dest_folder)
            os.remove(zip_path)

            # Only add new tif files to set (deduplicate)
            for tif in glob.glob(os.path.join(dest_folder, f"*_{layer}.tif")):
                layer_tifs[layer].add(os.path.abspath(tif))

    # ------------------------- mosaic & clean
    mosaic_names = {"acc": "facc.tif", "con": "dem.tif", "dir": "fdir.tif"}
    for layer, tif_set in layer_tifs.items():
        if not tif_set:
            print(f"[WARN] No {layer} tiles downloaded.")
            continue

        tif_list = sorted(tif_set)        # stable order for reproducibility
        out_path = os.path.join(dest_folder, mosaic_names[layer])

        if len(tif_list) == 1:
            shutil.move(tif_list[0], out_path)
        else:
            srcs   = [rasterio.open(p) for p in tif_list]
            mosaic, out_trans = merge(srcs)
            meta = srcs[0].meta.copy()
            meta.update(height=mosaic.shape[1],
                        width=mosaic.shape[2],
                        transform=out_trans)
            with rasterio.open(out_path, "w", **meta) as dst:
                dst.write(mosaic)
            for s in srcs:
                s.close()

            # Delete fragments (only delete once)
            for p in tif_list:
                os.remove(p)

    print(f"[OK] HydroSHEDS data ready in “{dest_folder}”.")
    return True

def clip_tif_by_shapefile(tif_path, output_path, shp_path):
    """
    Clip a GeoTIFF file to the bounding box of a shapefile with expanded buffer.
    
    Parameters:
    -----------
    tif_path : str
        Path to the input GeoTIFF file
    output_path : str
        Path where the clipped GeoTIFF will be saved
    shp_path : str
        Path to the shapefile
    

    """
    try:
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Read the shapefile and get its bounding box
        gdf = gpd.read_file(shp_path)
        
        # Get the bounding box (minx, miny, maxx, maxy)
        minx, miny, maxx, maxy = gdf.total_bounds
        
        # Calculate buffer size: max(0.1°, 20% of width/height)
        width = maxx - minx
        height = maxy - miny
        buffer_x = max(0.1, width * 0.2)
        buffer_y = max(0.1, height * 0.2)
        
        # Expand the bounding box with buffer
        minx_buffered = minx - buffer_x
        miny_buffered = miny - buffer_y
        maxx_buffered = maxx + buffer_x
        maxy_buffered = maxy + buffer_y
        
        # Open the GeoTIFF file
        with rasterio.open(tif_path) as src:
            # Create a window from the buffered bounding box
            window = from_bounds(minx_buffered, miny_buffered, maxx_buffered, maxy_buffered, transform=src.transform)
            
            # Read the data within the window (all bands)
            data = src.read(window=window)
            
            # Calculate the new transform for the clipped raster
            out_transform = src.window_transform(window)
            
            # Copy the metadata and update with new dimensions and transform
            out_meta = src.meta.copy()
            out_meta.update({
                "height": data.shape[1],
                "width": data.shape[2],
                "transform": out_transform,
                "dtype": 'float32',  # Ensure output is float32
                "compress": 'deflate'
            })
            
            # Convert data to float32 if it's not already
            if data.dtype != np.float32:
                data = data.astype(np.float32)
            
            # Write the clipped data to a new GeoTIFF
            with rasterio.open(output_path, "w", **out_meta) as dst:
                dst.write(data)
                
        
    except Exception as e:
        tqdm.write(f"Error clipping {os.path.basename(tif_path)}: {str(e)[:100]}...")


def batch_clip_tifs_by_shapefile(input_dir='../BasicData', output_dir='../BasicData_Clip', shp_path='../shpFile/WBDHU12_CobbFort_sub2.shp'):
    """
    Batch process all TIF files in the input directory,
    clipping them to the bounding box of the specified shapefile.
    
    Parameters:
    -----------
    input_dir : str, optional
        Directory containing TIF files to clip
    output_dir : str, optional
        Directory to save clipped files
    shp_path : str, optional
        Path to the shapefile
    """
    # Create output directory if it doesn't exist
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    
    # Find all TIF files in the input directory
    tif_files = glob.glob(os.path.join(input_dir, '*.tif'))
    
    if not tif_files:
        print(f"No TIF files found in {input_dir}")
        return
    

    # Process each TIF file with a progress bar
    successful = 0
    
    with tqdm(total=len(tif_files), desc="Clipping TIF files") as pbar:
        for tif_file in tif_files:
            # Get the base filename
            base_name = os.path.basename(tif_file)
            if '_' in base_name:
                base_name = base_name.split('_', 1)[1]  # Split at first '_' and keep the second part
            # Add '_clip' suffix to the filename before the extension
            base_name_without_ext, ext = os.path.splitext(base_name)
            output_name = f"{base_name_without_ext}_clip{ext}"
            output_path = os.path.join(output_dir, output_name)
            
            # Clip the TIF file
            if clip_tif_by_shapefile(tif_file, output_path, shp_path):
                successful += 1
            
            pbar.update(1)
    
    print(f"Output files saved to {os.path.abspath(output_dir)}")

# Visualize the clipped data with basin boundary overlay
def visualize_clipped_data_with_basin(clip_data_folder, basin_shp_path, figure_path):
    """
    Visualize clipped raster data with basin boundary overlay and save the figure.

    Parameters:
    -----------
    clip_data_folder : str
        Path to the folder containing clipped raster files
    basin_shp_path : str
        Path to the basin shapefile
    figure_path : str
        Path to the folder where the output PNG will be saved
    """
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    from rasterio.plot import show
    import matplotlib.colors as colors
    import matplotlib.patches as mpatches
    import os

    # Read the basin shapefile
    basin_gdf = gpd.read_file(basin_shp_path)

    # Get all TIF files in the clipped data folder
    tif_files = glob.glob(os.path.join(clip_data_folder, '*.tif'))

    if not tif_files:
        print(f"No TIF files found in {clip_data_folder}")
        return False

    print(f"Found {len(tif_files)} TIF files in {clip_data_folder}")

    # Map file names to descriptive titles and custom colormaps
    file_info = {
        'facc_clip.tif': {
            'title': 'Flow Accumulation Map (FAM)',
            'cmap': 'Blues',
            'norm': colors.LogNorm(),  # Logarithmic scale for flow accumulation
            'threshold': 1e8  # Values above this will be masked
        },
        'dem_clip.tif': {
            'title': 'Digital Elevation Model (DEM)',
            'cmap': 'terrain',
            'norm': None,  # Linear scale for elevation
            'threshold': 9999  # Values above this will be masked
        },
        'fdir_clip.tif': {
            'title': 'Drainage Direction Map (DDM)',
            'cmap': None,  # Will be set to custom colormap for directions
            'norm': None,
            'threshold': None
        }
    }

    # Display up to 3 TIF files with basin boundary in a single row
    if len(tif_files) > 0:
        # Create a figure with subplots in a single row with reduced spacing
        fig, axes = plt.subplots(1, min(3, len(tif_files)), figsize=(15, 5),
                                 subplot_kw={'projection': ccrs.PlateCarree()})

        # Reduce horizontal spacing between subplots
        plt.subplots_adjust(wspace=0)

        # If only one file, axes won't be an array
        if len(tif_files) == 1:
            axes = [axes]

        # Process each file and plot in the corresponding subplot
        for i, (tif_file, ax) in enumerate(zip(tif_files[:3], axes)):
            file_name = os.path.basename(tif_file)

            with rasterio.open(tif_file) as src:
                data = src.read(1)
                transform = src.transform

                # Add geographic features
                ax.add_feature(cfeature.COASTLINE)
                ax.add_feature(cfeature.BORDERS, linestyle=':')
                ax.add_feature(cfeature.STATES, linestyle=':')

                # Get custom visualization settings for this file
                file_settings = file_info.get(file_name, {
                    'title': file_name,
                    'cmap': 'viridis',
                    'norm': None,
                    'threshold': None
                })

                # Mask values above threshold for DEM and FACC
                if file_settings['threshold'] is not None:
                    data = np.ma.masked_where(data > file_settings['threshold'], data)

                # Special handling for flow direction map to use discrete colors
                if file_name == 'fdir_clip.tif':
                    # Get unique values in the direction data
                    unique_values = np.unique(data)
                    unique_values = unique_values[~np.isnan(unique_values)]

                    # Create a custom colormap for the unique direction values
                    n_values = len(unique_values)
                    colors_list = plt.cm.tab10(np.linspace(0, 1, n_values))

                    # Create a custom discrete colormap
                    cmap = colors.ListedColormap(colors_list)
                    bounds = np.concatenate([unique_values - 0.5, [unique_values[-1] + 0.5]])
                    norm = colors.BoundaryNorm(bounds, cmap.N)

                    # Show the raster with discrete colors
                    img = show(data, ax=ax, transform=transform, cmap=cmap, norm=norm)

                    # Create a legend for direction values
                    legend_patches = []
                    for j, val in enumerate(unique_values):
                        patch = mpatches.Patch(color=colors_list[j], label=f'Direction {int(val)}')
                        legend_patches.append(patch)

                    # Add the legend
                    ax.legend(handles=legend_patches, loc='lower right', fontsize='small')
                else:
                    # Show other raster data with standard colormap
                    img = show(data, ax=ax, transform=transform,
                              cmap=file_settings['cmap'], norm=file_settings['norm'])

                    # Fix colorbar issue by directly creating it from the image
                    if hasattr(img, 'get_images') and img.get_images():
                        # For matplotlib 3.5+
                        cbar = plt.colorbar(img.get_images()[0], ax=ax, shrink=0.7)
                    elif hasattr(img, 'images') and img.images:
                        # For older matplotlib versions
                        cbar = plt.colorbar(img.images[0], ax=ax, shrink=0.7)

                    # Set appropriate colorbar label
                    if file_name == 'facc_clip.tif':
                        cbar.set_label('Flow Accumulation (log scale)')
                    elif file_name == 'dem_clip.tif':
                        cbar.set_label('Elevation (m)')

                # Add basin boundary with black color
                basin_gdf.boundary.plot(ax=ax, color='black', linewidth=2)

                # Add title using the mapping
                ax.set_title(file_settings['title'], fontsize=10)

        plt.tight_layout()
        # Save the figure as basic_data.png in the specified figure_path, dpi=300
        os.makedirs(figure_path, exist_ok=True)
        out_png = os.path.join(figure_path, "basic_data.png")
        plt.savefig(out_png, dpi=300, bbox_inches='tight')
        plt.close(fig)
        print(f"Figure saved to {out_png}")

def visualize_flow_accumulation_with_gauges(basin_shp_path, gauges_wgs, clip_data_folder, figure_path):
    """
    Visualize flow accumulation with specified USGS gauge stations overlay and save the figure.
    This visualization emphasizes high flow accumulation values with a colormap
    and shows gauge stations with their IDs as an overlay.
    
    Parameters:
    -----------
    basin_shp_path : str
        Path to the watershed shapefile
    gauges_wgs : GeoDataFrame
        GeoDataFrame containing the gauge stations to plot (in WGS84 coordinates)
    clip_data_folder : str
        Path to the folder containing clipped raster files
    figure_path : str
        Path to the folder where the output PNG will be saved
    """
    import os
    import pandas as pd
    import matplotlib.colors as colors
    import matplotlib.pyplot as plt
    import numpy as np
    
    # Ensure output directory exists
    os.makedirs(figure_path, exist_ok=True)
    
    # Find the flow accumulation file
    facc_file = os.path.join(clip_data_folder, 'facc_clip.tif')
    if not os.path.exists(facc_file):
        print(f"Flow accumulation file not found: {facc_file}")
        return
    
    # ------------------------------------------------------------------
    # 1) Load and prepare watershed polygon
    # ------------------------------------------------------------------
    basin_gdf = gpd.read_file(basin_shp_path)
    
    # Store original CRS for later use
    original_crs = basin_gdf.crs
    
    # Convert gauges to match basin CRS if needed
    gauges_plot = gauges_wgs.to_crs(original_crs)
    
    # Create the figure
    fig, ax = plt.subplots(figsize=(12, 10))
    
    # Open and read the flow accumulation data
    with rasterio.open(facc_file) as src:
        data = src.read(1)
        transform = src.transform

        # Mask values above threshold
        threshold = 1e8
        data = np.ma.masked_where(data > threshold, data)
        
        # Create a logarithmic colormap to emphasize high values
        data_mask = data > 0  # Exclude zeros and negative values
        if np.any(data_mask):
            vmin = data[data_mask].min()
            vmax = data.max()
            norm = colors.LogNorm(vmin=vmin, vmax=vmax)
            
            # Use rasterio's show function for proper coordinate handling
            from rasterio.plot import show
            img = show(data, ax=ax, transform=transform, cmap='Blues', norm=norm, alpha=0.8)
            
            # Add a colorbar
            if hasattr(img, 'get_images') and img.get_images():
                cbar = plt.colorbar(img.get_images()[0], ax=ax, shrink=0.7)
            elif hasattr(img, 'images') and img.images:
                cbar = plt.colorbar(img.images[0], ax=ax, shrink=0.7)
            else:
                im = ax.imshow(data, cmap='Blues', norm=norm, alpha=0.8, 
                             extent=[transform[2], transform[2] + transform[0] * data.shape[1],
                                    transform[5] + transform[4] * data.shape[0], transform[5]])
                cbar = plt.colorbar(im, ax=ax, shrink=0.7)
            
            cbar.set_label('Flow Accumulation (log scale)')
            
            # Plot basin boundary
            basin_gdf.boundary.plot(ax=ax, color='red', linewidth=2, alpha=0.7)
            
            # Add title
            ax.set_title('Flow Accumulation with USGS Gauge Stations', fontsize=14)
            
            if not gauges_plot.empty:
                # Plot gauge locations
                gauges_plot.plot(
                    ax=ax,
                    color='black',
                    marker='^',
                    markersize=100,
                    alpha=0.9,
                    label='USGS Gauges'
                )
                
                # Add station IDs as labels
                for idx, row in gauges_plot.iterrows():
                    staid = f"{row.STAID:0>8}"
                    ax.annotate(
                        staid,
                        xy=(row.geometry.x, row.geometry.y),
                        xytext=(7, 7),
                        textcoords='offset points',
                        color='black',
                        fontweight='bold',
                        fontsize=10,
                        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.7)
                    )
                
                # Add legend
                ax.legend(loc='upper right')
            else:
                print("No USGS gauge stations provided for visualization")
            
            # Set map extent to basin boundary with buffer
            bounds = basin_gdf.total_bounds
            width = bounds[2] - bounds[0]
            height = bounds[3] - bounds[1]
            buffer_x = max(0.1, width * 0.2)  # 20% of width or minimum 0.1 degree
            buffer_y = max(0.1, height * 0.2)  # 20% of height or minimum 0.1 degree
            
            ax.set_xlim(bounds[0] - buffer_x, bounds[2] + buffer_x)
            ax.set_ylim(bounds[1] - buffer_y, bounds[3] + buffer_y)
            ax.set_axis_off()
            
            # Save the figure
            output_path = os.path.join(figure_path, 'facc_with_gauges.png')
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close(fig)
            
            print(f"Flow accumulation visualization with gauges saved to {output_path}")
        else:
            print("No valid flow accumulation data found (all values <= 0)")
            plt.close(fig)

def visualize_dem_with_gauges(basin_shp_path, gauges_wgs, clip_data_folder, figure_path):
    """
    Visualize DEM with specified USGS gauge stations overlay and save the figure.
    
    Parameters:
    -----------
    basin_shp_path : str
        Path to the watershed shapefile
    gauges_wgs : GeoDataFrame
        GeoDataFrame containing the gauge stations to plot (in WGS84 coordinates)
    clip_data_folder : str
        Path to the folder containing clipped raster files
    figure_path : str
        Path to the folder where the output PNG will be saved
    """
    import os
    import matplotlib.pyplot as plt
    
    # Ensure output directory exists
    os.makedirs(figure_path, exist_ok=True)
    
    # Find the DEM file
    dem_file = os.path.join(clip_data_folder, 'dem_clip.tif')
    if not os.path.exists(dem_file):
        print(f"DEM file not found: {dem_file}")
        return
    
    # Load and prepare watershed polygon
    basin_gdf = gpd.read_file(basin_shp_path)
    original_crs = basin_gdf.crs
    
    # Convert gauges to match basin CRS if needed
    gauges_plot = gauges_wgs.to_crs(original_crs)
    
    # Create the figure
    fig, ax = plt.subplots(figsize=(12, 10))
    
    # Open and read the DEM data
    with rasterio.open(dem_file) as src:
        data = src.read(1)
        transform = src.transform

        # Mask values above threshold
        threshold = 9999
        data = np.ma.masked_where(data > threshold, data)
        
        # Use rasterio's show function for proper coordinate handling
        from rasterio.plot import show
        img = show(data, ax=ax, transform=transform, cmap='terrain', alpha=0.8)
        
        # Add a colorbar
        if hasattr(img, 'get_images') and img.get_images():
            cbar = plt.colorbar(img.get_images()[0], ax=ax, shrink=0.7)
        elif hasattr(img, 'images') and img.images:
            cbar = plt.colorbar(img.images[0], ax=ax, shrink=0.7)
        else:
            im = ax.imshow(data, cmap='terrain', alpha=0.8,
                         extent=[transform[2], transform[2] + transform[0] * data.shape[1],
                                transform[5] + transform[4] * data.shape[0], transform[5]])
            cbar = plt.colorbar(im, ax=ax, shrink=0.7)
        
        cbar.set_label('Elevation (m)')
        
        # Plot basin boundary
        basin_gdf.boundary.plot(ax=ax, color='red', linewidth=2, alpha=0.7)
        
        # Add title
        ax.set_title('Digital Elevation Model with USGS Gauge Stations', fontsize=14)
        
        if not gauges_plot.empty:
            # Plot gauge locations
            gauges_plot.plot(
                ax=ax,
                color='black',
                marker='^',
                markersize=100,
                alpha=0.9,
                label='USGS Gauges'
            )
            
            # Add station IDs as labels
            for idx, row in gauges_plot.iterrows():
                staid = f"{row.STAID:0>8}"
                ax.annotate(
                    staid,
                    xy=(row.geometry.x, row.geometry.y),
                    xytext=(7, 7),
                    textcoords='offset points',
                    color='black',
                    fontweight='bold',
                    fontsize=10,
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.7)
                )
            
            # Add legend
            ax.legend(loc='upper right')
        else:
            print("No USGS gauge stations provided for visualization")
        
        # Set map extent to basin boundary with buffer
        bounds = basin_gdf.total_bounds
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        buffer_x = max(0.1, width * 0.2)  # 20% of width or minimum 0.1 degree
        buffer_y = max(0.1, height * 0.2)  # 20% of height or minimum 0.1 degree
        
        ax.set_xlim(bounds[0] - buffer_x, bounds[2] + buffer_x)
        ax.set_ylim(bounds[1] - buffer_y, bounds[3] + buffer_y)
        ax.set_axis_off()
        
        # Save the figure
        output_path = os.path.join(figure_path, 'dem_with_gauges.png')
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close(fig)
        
        print(f"DEM visualization with gauges saved to {output_path}")

def visualize_figures_basin(figure_path):
    """
    Visualize all figures in the given directory and save them as PNG files.

    Parameters:
    figure_path : str
        Path to the folder where the output PNG will be saved
    """
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg
    import os
    
    # Check if the required PNG files exist
    basin_map_path = os.path.join(figure_path, 'basin_map_with_gauges.png')
    facc_map_path = os.path.join(figure_path, 'facc_with_gauges.png')
    dem_map_path = os.path.join(figure_path, 'dem_with_gauges.png')
    basic_data_path = os.path.join(figure_path, 'basic_data.png')
    
    # First, visualize basin_map_with_gauges.png, facc_with_gauges.png and dem_with_gauges.png side by side
    if os.path.exists(basin_map_path) and os.path.exists(facc_map_path) and os.path.exists(dem_map_path):
        fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(18, 5))
        
        # Load and display basin map
        basin_img = mpimg.imread(basin_map_path)
        ax1.imshow(basin_img)
        ax1.set_title('Basin Map with Gauges', fontsize=12, fontweight='bold')
        ax1.axis('off')
        
        # Load and display flow accumulation map
        facc_img = mpimg.imread(facc_map_path)
        ax2.imshow(facc_img)
        ax2.set_title('Flow Accumulation with Gauges', fontsize=12, fontweight='bold')
        ax2.axis('off')
        
        # Load and display DEM map
        dem_img = mpimg.imread(dem_map_path)
        ax3.imshow(dem_img)
        ax3.set_title('DEM with Gauges', fontsize=12, fontweight='bold')
        ax3.axis('off')
        
        plt.tight_layout()
        combined_output_path = os.path.join(figure_path, 'combined_maps.png')
        plt.savefig(combined_output_path, dpi=300, bbox_inches='tight')
        plt.show()
        plt.close(fig)
        
        print(f"Combined visualization saved to {combined_output_path}")
    else:
        print("Warning: One or more required map files not found")
    
    # Then, visualize basic_data.png separately
    if os.path.exists(basic_data_path):
        fig, ax = plt.subplots(1, 1, figsize=(8, 6))
        
        # Load and display basic data
        basic_img = mpimg.imread(basic_data_path)
        ax.imshow(basic_img)
        ax.set_title('Basic Data Visualization', fontsize=12, fontweight='bold')
        ax.axis('off')
        
        plt.tight_layout()
        plt.show()
        plt.close(fig)
        
        print(f"Basic data visualization displayed from {basic_data_path}")
    else:
        print("Warning: basic_data.png not found")

import io, requests, pandas as pd, pathlib, tempfile, json, geopandas as gpd
def describe_gauges(gauges_list):
    """
    Generate a description of gauge information including ID, drainage area and elevation.
    Downloads metadata for each gauge from USGS.
    
    Parameters:
    -----------
    gauges_list : GeoDataFrame
        GeoDataFrame containing gauge information
        
    Returns:
    --------
    str, list
        Text description of the gauges and list of drainage areas
    """
    description = "USGS Gauge Station Information:\n\n"
    drain_area_list = []
    
    for idx, gauge in gauges_list.iterrows():
        staid = f"{gauge.STAID:0>8}"
        
        # Query USGS site info for each gauge
        url = (f"https://waterservices.usgs.gov/nwis/site/"
               f"?site={staid}&format=rdb&siteOutput=expanded&siteStatus=all")
        
        try:
            response = requests.get(url, timeout=30)
            df = pd.read_csv(io.StringIO(response.text), sep="\t", comment="#", dtype=str)
            rec = df.loc[df["site_no"] == staid].squeeze()
            
            # Build description for this gauge
            description += f"Gauge ID: {staid}\n"
            
            drain_area = None
            if not pd.isna(rec.get("drain_area_va")):
                drain_area = float(rec["drain_area_va"])
                drain_area_km2 = drain_area * 2.58999  # Convert square miles to square kilometers
                description += f"Drainage Area: {drain_area_km2:.2f} sq km\n"
                
            if not pd.isna(rec.get("alt_va")):
                elevation_ft = float(rec["alt_va"])
                elevation_m = elevation_ft * 0.3048
                description += f"Elevation: {elevation_m:.1f} meters\n"
                
            description += "\n"
            drain_area_list.append(drain_area)
            
        except Exception as e:
            print(f"Error fetching data for gauge {staid}: {str(e)}")
            description += f"Gauge ID: {staid}\n"
            description += "Error fetching gauge metadata\n\n"
            drain_area_list.append(None)
            
    return description, drain_area_list


def basin_processor(args):
    # print("DEBUG inside basin_processor ->", type(args), vars(args))
    print("Downloading watershed shapefile...")
    Basin_Area, Basin_Name, bbox_coords = download_watershed_shp(args.selected_point[0], args.selected_point[1], args.basin_shp_path, args.basin_level)
    gauges_list = plot_watershed_with_gauges(args.basin_shp_path, args.gauge_meta_path, args.figure_path, 10000, args.time_start, args.time_end, args.time_step)
    gauges_description = describe_gauges(gauges_list)

    download_hydrosheds_data(bbox_coords, args.basic_data_path)
    batch_clip_tifs_by_shapefile(args.basic_data_path, args.basic_data_clip_path, args.basin_shp_path)
    visualize_clipped_data_with_basin(args.basic_data_clip_path, args.basin_shp_path, args.figure_path)
    
    # Add the new flow accumulation visualization with gauges
    visualize_flow_accumulation_with_gauges(args.basin_shp_path, gauges_list, args.basic_data_clip_path, args.figure_path)
    visualize_dem_with_gauges(args.basin_shp_path, gauges_list, args.basic_data_clip_path, args.figure_path)
    visualize_figures_basin(args.figure_path)
    return Basin_Area, Basin_Name, gauges_list, gauges_description



