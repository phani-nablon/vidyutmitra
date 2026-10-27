"""Extraction schema 2.1 (one JSON document per drawing).

Coordinate frames
  page px      pixels of the rendered page at `layouts[].dpi` (origin top-left)
  room mm      per substation: origin = top-left corner of the room's bounding box in the equipment
               layout, x to the right, y down, millimetres. All *_mm polygons of a substation use it.
  site mm      per site-plan layout: origin top-left of the layout crop, millimetres (when scaled).

Groups follow the extraction plan: electrical, site, civil, cable, access, ventilation, plus segments,
layouts, associations, substations, quality and provenance. Busbar, fault level, floor loading,
interconnection and SLD data are deliberately out of scope.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "2.1"

Point = list[float]
Polygon = list[Point]


class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


class Provenance(_M):
    extractor: str = "substation_retrieval"
    extractor_version: str = "0.1.0"
    method: Literal["llm_v2", "v1_import", "dxf", "manual", "text_only"] = "llm_v2"
    llm_model: Optional[str] = None
    prompt_version: Optional[str] = None
    created_at: Optional[str] = None
    imported_from: Optional[str] = None
    notes: list[str] = Field(default_factory=list)


class Source(_M):
    file_name: str
    sha256: str
    kind: Literal["pdf", "dxf", "dwg", "json"] = "pdf"
    pages: int = 1
    path: Optional[str] = None


class Layout(_M):
    id: str
    page: int = 1
    type: str                                  # equipment_layout | section | site_plan | css_layout | beam_layout | cutout_layout | ...
    title: Optional[str] = None
    bbox_px: Optional[list[float]] = None
    dpi: Optional[float] = None
    floor: Optional[str] = None
    mm_per_px: Optional[float] = None
    scale_text: Optional[str] = None
    scale_source: Optional[str] = None          # dimension_fit | scale_text | v1 | none
    scale_confidence: Optional[float] = None
    crop_image: Optional[str] = None


class Segment(_M):
    id: str
    cls: str                                    # one of settings.extraction.mask_classes
    layout_id: Optional[str] = None
    substation_id: Optional[str] = None
    page: int = 1
    polygon_px: Optional[Polygon] = None
    polygon_mm: Optional[Polygon] = None        # room mm (substation segments) or site mm
    polyline_mm: Optional[list[Point]] = None
    label: Optional[str] = None
    source: str = "llm"                         # llm | legend_colour | vector | v1 | dxf
    confidence: Optional[float] = None
    mask_path: Optional[str] = None


class Equipment(_M):
    id: str
    label: Optional[str] = None                 # as written on the drawing ("DT-1", "RMU 3W")
    equipment_type: str                         # normalised: transformer | rmu | ltp | metering | ...
    subtype: Optional[str] = None
    kva: Optional[int] = None
    kva_source: Optional[str] = None            # drawing_text | catalogue_unique | none
    L_mm: Optional[float] = None
    B_mm: Optional[float] = None
    H_mm: Optional[float] = None
    weight_kg: Optional[float] = None
    centre_mm: Optional[Point] = None
    polygon_mm: Optional[Polygon] = None
    ceiling_clearance_mm: Optional[float] = None
    catalogue: Optional[dict[str, Any]] = None  # {name, candidates, unique}


class Opening(_M):
    id: str
    type: str                                   # door | rolling_shutter | fire_exit | gate | louvre | window | cutout | staircase
    label: Optional[str] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    wall: Optional[str] = None                  # N | S | E | W (room frame: N = top)
    centre_mm: Optional[Point] = None
    polygon_mm: Optional[Polygon] = None


class Structure(_M):
    id: str
    type: str                                   # beam | column | platform | plinth
    polygon_mm: Optional[Polygon] = None
    bottom_mm: Optional[float] = None           # beam bottom above finished floor
    depth_mm: Optional[float] = None
    label: Optional[str] = None


class Route(_M):
    id: str
    type: str                                   # trench | cable_tray | hdpe | access_path | wayleave
    kind: Optional[str] = None                  # HT | LT | vehicle | staff
    polygon_mm: Optional[Polygon] = None
    polyline_mm: Optional[list[Point]] = None
    width_mm: Optional[float] = None
    depth_mm: Optional[float] = None
    label: Optional[str] = None


class Civil(_M):
    polygon_mm: Optional[Polygon] = None
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    area_m2: Optional[float] = None
    shape: Optional[str] = None
    clear_height_mm: Optional[float] = None
    slab_to_soffit_mm: Optional[float] = None
    beam_bottom_mm: Optional[float] = None
    plinth_mm: Optional[float] = None
    platform: Optional[dict[str, Any]] = None
    trench_depth_mm: Optional[float] = None
    structures: list[Structure] = Field(default_factory=list)


class Substation(_M):
    id: str
    name: Optional[str] = None
    floor: Optional[str] = None
    equipment_layout_id: Optional[str] = None
    section_layout_id: Optional[str] = None
    section_reason: Optional[str] = None
    mm_per_px: Optional[float] = None
    civil: Civil = Field(default_factory=Civil)
    electrical: dict[str, Any] = Field(default_factory=lambda: {"equipment": []})
    access: dict[str, Any] = Field(default_factory=lambda: {"openings": []})
    ventilation: dict[str, Any] = Field(default_factory=lambda: {"openings": []})
    cable: dict[str, Any] = Field(default_factory=lambda: {"routes": [], "cutouts": []})
    clearances: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class SiteFeature(_M):
    id: str
    cls: str                                    # nalla | dp_road | internal_road | tree | compound_wall | structure | wayleave | access_path | trench | site_boundary
    label: Optional[str] = None
    layout_id: Optional[str] = None
    polygon_mm: Optional[Polygon] = None
    polyline_mm: Optional[list[Point]] = None
    polygon_px: Optional[Polygon] = None
    width_m: Optional[float] = None


class Site(_M):
    layout_id: Optional[str] = None
    mm_per_px: Optional[float] = None
    substation_polygons_mm: list[Polygon] = Field(default_factory=list)
    features: list[SiteFeature] = Field(default_factory=list)
    clearance_checks: list[dict[str, Any]] = Field(default_factory=list)


class Metadata(_M):
    installation: Optional[str] = None          # indoor | open_to_sky | semi_open
    arrangement: Optional[str] = None           # centralized | distributed
    context: Optional[str] = None               # in_building | standalone_building | outdoor_yard | temporary
    floor: Optional[str] = None                 # basement | stilt | podium | ground | first | ...
    location_in_building: Optional[str] = None
    voltage_kv: Optional[float] = None
    substation_count: int = 1
    multiple_substations: bool = False
    kind: Optional[str] = None                  # dt_css | metering_only | package | ht_switching
    project: Optional[str] = None
    drawing_date: Optional[str] = None
    title_block: dict[str, Any] = Field(default_factory=dict)
    legend: list[dict[str, Any]] = Field(default_factory=list)


class Quality(_M):
    status: Literal["auto", "provisional", "reviewed"] = "provisional"
    score: float = 0.0
    issues: list[str] = Field(default_factory=list)
    coverage: dict[str, bool] = Field(default_factory=dict)


class ExtractionDoc(_M):
    schema_version: str = SCHEMA_VERSION
    doc_id: str
    source: Source
    provenance: Provenance = Field(default_factory=Provenance)
    metadata: Metadata = Field(default_factory=Metadata)
    layouts: list[Layout] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    associations: list[dict[str, Any]] = Field(default_factory=list)
    substations: list[Substation] = Field(default_factory=list)
    site: Site = Field(default_factory=Site)
    quality: Quality = Field(default_factory=Quality)
    text: Optional[str] = None                  # searchable text (title block, notes, labels)


def validate_doc(d: dict) -> dict:
    """Validate and normalise a schema-2.1 dict; returns a plain dict."""
    return ExtractionDoc.model_validate(d).model_dump(mode="json", exclude_none=False)
