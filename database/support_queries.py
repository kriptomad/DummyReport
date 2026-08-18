"""
Support queries derived from legacy query bundles.
Each function receives a connection + user-friendly parameters and returns pd.DataFrame.
"""
import pandas as pd


# ─────────────────────────────────────────────────────────────
#  QUERY 1 — Shipment Full Details
#  Use: Always run when analysing a SHIPMENT_ID
# ─────────────────────────────────────────────────────────────
SHIPMENT_DETAILS_SQL = """
SELECT DISTINCT
    shipment.shipment_number,
    shipment.customer_code,
    shipment.status,
    shipment.equipment_type_code,
    shipment.origin_location_code,
    shipment.origin_name,
    shipment.origin_country_code,
    shipment.origin_state_code,
    shipment.origin_city_name,
    shipment.destination_location_code,
    shipment.destination_name,
    shipment.destination_country_code,
    shipment.destination_state_code,
    shipment.destination_city_name,
    shipment.preferred_carrier_code,
    shipment.preferred_service_code,
    shipment.charge_override_code,
    shipment.origin_pickup_at,
    shipment.destination_pickup_at,
    shipment.origin_delivery_at,
    shipment.destination_delivery_at,
    shipment.created_at,
    shipment.created_by_user,
    shipment.updated_at,
    shipment.updated_by_user,
    shipment.planned_weight,
    shipment.vol,
    shipment.urgent_flag,
    shipment.freight_terms,
    shipment.shipment_description,
    shipment.consolidation_class
FROM
    ACME_TMS.shipment shipment
WHERE
    shipment.shipment_number = :shipment_number
"""


def run_shipment_details(conn, shipment_number: str) -> pd.DataFrame:
    """Full shipment record from ACME_TMS.SHIPMENT by SHIPMENT_NUMBER."""
    cursor = conn.cursor()
    try:
        cursor.execute(SHIPMENT_DETAILS_SQL, {"shipment_number": shipment_number.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 2 — Shipment History
#  Use: Always run — shows status transitions and when error occurred
# ─────────────────────────────────────────────────────────────
SHIPMENT_HISTORY_SQL = """
SELECT *
FROM ACME_TMS.SHIPMENT_HISTORY
WHERE shipment_number = :shipment_number
ORDER BY shipment_number, status_step_id
"""


def run_shipment_history(conn, shipment_number: str) -> pd.DataFrame:
    """Status transition history from ACME_TMS.SHIPMENT_HISTORY."""
    cursor = conn.cursor()
    try:
        cursor.execute(SHIPMENT_HISTORY_SQL, {"shipment_number": shipment_number.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 3 — Load Info (Load Leg)
#  Use: Un-routable errors — confirms if load was generated
# ─────────────────────────────────────────────────────────────
LOAD_INFO_SQL = """
SELECT DISTINCT
    llt.load_segment_id,
    lldt.customer_code,
    lldt.origin_location_code,
    lldt.destination_location_code,
    lldt.service_code,
    llt.carrier_code,
    llt.current_status_id,
    llt.created_at,
    llt.updated_at,
    llt.total_planned_weight
FROM
    RTG_APP.DEMO_LOAD llt
    JOIN RTG_APP.DEMO_LOAD_DETAIL lldt ON llt.load_segment_id = lldt.load_segment_id
    JOIN RTG_APP.DEMO_SHIPMENT_LINK st ON lldt.shipment_key = st.shipment_key
WHERE
    st.shipment_number = :shipment_number
ORDER BY
    llt.created_at DESC
"""


def run_load_info(conn, shipment_number: str) -> pd.DataFrame:
    """Load leg details — checks if a load was created for the shipment."""
    cursor = conn.cursor()
    try:
        cursor.execute(LOAD_INFO_SQL, {"shipment_number": shipment_number.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
 #  QUERY 4 — Location Status (manual — single location code)
#  Use: Manual lookup of any location code
# ─────────────────────────────────────────────────────────────
LOCATION_STATUS_SQL = """
SELECT
    sl.location_code,
    sl.location_type,
    sl.active_flag,
    sl.customer_code,
    sl.created_at,
    sl.updated_at,
    a.location_name,
    a.street_name,
    a.city_name,
    a.state_code,
    a.country_code,
    a.postal_code,
    a.loc
FROM
    RTG_APP.DEMO_LOCATION sl
    INNER JOIN RTG_APP.DEMO_ADDRESS a ON sl.address_id = a.address_id
WHERE
    sl.location_code = :location_code
"""


def run_location_status(conn, location_code: str) -> pd.DataFrame:
    """Location details — checks if a location code exists and is active."""
    cursor = conn.cursor()
    try:
        cursor.execute(LOCATION_STATUS_SQL, {"location_code": location_code.strip().upper()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 4B — Origin & Destination Validation (automatic)
#  Use: Validates ORIGIN and DESTINATION from the audit record
#       directly — no manual input needed, runs from SHIPMENT_ID
# ─────────────────────────────────────────────────────────────
ORIGIN_DEST_VALIDATION_SQL = """
SELECT
    audit.SHIPMENT_ID,
    audit.ORIGIN                        AS audit_origin,
    audit.DESTINATION                   AS audit_destination,
    audit.ERR_MSG,
    -- ORIGIN validation
    orig_loc.location_code                AS origin_loc_cd,
    orig_loc.active_flag                   AS origin_active_flag,
    orig_loc.location_type           AS origin_type,
    orig_addr.location_name                  AS origin_loc_name,
    orig_addr.country_code                   AS origin_ctry_cd,
    orig_addr.state_code                    AS origin_sta_cd,
    orig_addr.city_name                  AS origin_city,
    -- DESTINATION validation
    dest_loc.location_code                AS dest_loc_cd,
    dest_loc.active_flag                   AS destination_active_flag,
    dest_loc.location_type           AS dest_type,
    dest_addr.location_name                  AS dest_loc_name,
    dest_addr.country_code                   AS destination_country_code,
    dest_addr.state_code                    AS dest_sta_cd,
    dest_addr.city_name                  AS dest_city,
    -- Existence flags
    CASE WHEN orig_loc.location_code IS NULL THEN 'NOT FOUND'
         WHEN UPPER(orig_loc.active_flag) IN ('0','N','NO','INACTIVE') THEN 'INACTIVE'
         ELSE 'ACTIVE' END              AS origin_status,
    CASE WHEN dest_loc.location_code IS NULL THEN 'NOT FOUND'
         WHEN UPPER(dest_loc.active_flag) IN ('0','N','NO','INACTIVE') THEN 'INACTIVE'
         ELSE 'ACTIVE' END              AS destination_status
FROM
    ACME_OMS.DEMO_AUDIT audit
    -- ORIGIN join
    LEFT JOIN RTG_APP.DEMO_LOCATION orig_loc
        ON UPPER(orig_loc.location_code) = UPPER(audit.ORIGIN)
    LEFT JOIN RTG_APP.DEMO_ADDRESS orig_addr
        ON orig_loc.address_id = orig_addr.address_id
    -- DESTINATION join
    LEFT JOIN RTG_APP.DEMO_LOCATION dest_loc
        ON UPPER(dest_loc.location_code) = UPPER(audit.DESTINATION)
    LEFT JOIN RTG_APP.DEMO_ADDRESS dest_addr
        ON dest_loc.address_id = dest_addr.address_id
WHERE
    audit.SHIPMENT_ID = :shipment_id
"""


def run_origin_dest_validation(conn, shipment_id: str) -> pd.DataFrame:
    """
    Validates ORIGIN and DESTINATION from DEMO_AUDIT against
    RTG_APP.DEMO_LOCATION. Returns existence + active status for both.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(ORIGIN_DEST_VALIDATION_SQL, {"shipment_id": shipment_id.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  QUERY 5 — Reference Numbers (SERVICE_CODE, Equipment, etc.)
#  Use: Un-routable errors — cross-reference with Rate Card Lookup SERVICE_CODE
# ─────────────────────────────────────────────────────────────
REFERENCE_NUMBERS_SQL = """
SELECT DISTINCT
    st.shipment_number,
    st.shipment_key,
    st.equipment_type_code,
    st.origin_location_code,
    st.destination_location_code,
    rn.reference_type,
    rn.reference_value
FROM
    RTG_APP.DEMO_SHIPMENT_LINK st
    LEFT JOIN RTG_APP.DEMO_REFERENCE rn ON st.shipment_key = rn.shipment_key
WHERE
    st.shipment_number = :shipment_number
    AND rn.reference_type IN (
        'Container', 'Ocean B/L', 'Vessel', 'DeliveryPlan', 'UL',
        'Transshipment Vessel', 'HAZ', 'OCEAN_CONTAINER_NUMBER'
    )
ORDER BY
    rn.reference_type
"""


def run_reference_numbers(conn, shipment_number: str) -> pd.DataFrame:
    """Reference numbers for a shipment — SERVICE_CODE, container, ocean BOL, etc."""
    cursor = conn.cursor()
    try:
        cursor.execute(REFERENCE_NUMBERS_SQL, {"shipment_number": shipment_number.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)

# ─────────────────────────────────────────────────────────────
#  QUERY 6 — Load Events / Routing Logs
#  Use: "Rate exists – routing-platform investigation required" — capture Routing logs
# ─────────────────────────────────────────────────────────────
LOAD_EVENTS_SQL = """
SELECT
    ev.root_object_id,
    ev.event_notification_id,
    ev.completed_at,
    ev.event_id,
    ev.event_type_code,
    ev.status,
    ev.message_text
FROM
    ACME_TMS.DEMO_EVENT_LOG ev
    JOIN RTG_APP.DEMO_LOAD_DETAIL lldt ON ev.root_object_id = lldt.load_segment_id
    JOIN RTG_APP.DEMO_SHIPMENT_LINK st ON lldt.shipment_key = st.shipment_key
WHERE
    st.shipment_number = :shipment_number
ORDER BY
    ev.completed_at DESC
"""


def run_load_events(conn, shipment_number: str) -> pd.DataFrame:
    """routing event log for a shipment — useful when rate exists but routing failed."""
    cursor = conn.cursor()
    try:
        cursor.execute(LOAD_EVENTS_SQL, {"shipment_number": shipment_number.strip()})
        cols = [c[0] for c in cursor.description]
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return pd.DataFrame(rows, columns=cols)


# ─────────────────────────────────────────────────────────────
#  CATALOGUE — maps query key → metadata for UI
# ─────────────────────────────────────────────────────────────
QUERY_CATALOGUE = {
    "shipment_details": {
        "label":       "📦 Shipment Full Details",
        "description": "Full shipment record from ACME_TMS.SHIPMENT — origin, destination, carrier, equipment, status.",
        "param_label": "SHIPMENT_NUMBER (Shipment Number)",
        "param_key":   "shipment_number",
        "fn":          run_shipment_details,
        "auto_on":     ["all"],
    },
    "shipment_history": {
        "label":       "🕓 Shipment History",
        "description": "Status transition log — shows when the error occurred and all state changes.",
        "param_label": "SHIPMENT_NUMBER (Shipment Number)",
        "param_key":   "shipment_number",
        "fn":          run_shipment_history,
        "auto_on":     ["all"],
    },
    "origin_dest_validation": {
        "label":       "📍 Origin & Destination Validation",
        "description": (
            "Validates ORIGIN and DESTINATION from the audit record against DEMO_LOCATION. "
            "Shows if each location EXISTS and is ACTIVE — runs automatically from SHIPMENT_ID."
        ),
        "param_label": "SHIPMENT_ID",
        "param_key":   "shipment_id",
        "fn":          run_origin_dest_validation,
        "auto_on":     ["all"],   # always useful — highlights inactive/missing locations
    },
    "load_info": {
        "label":       "🚛 Load Info (Load Leg)",
        "description": "Load leg details — confirms if a load was generated from the shipment.",
        "param_label": "SHIPMENT_NUMBER (Shipment Number)",
        "param_key":   "shipment_number",
        "fn":          run_load_info,
        "auto_on":     ["missing rate", "itinerary", "schedule"],
    },
    "location_status": {
        "label":       "📍 Location Status (manual)",
        "description": "Manual lookup — checks if a specific location code exists and is active.",
        "param_label": "LOCATION_CODE (Location Code)",
        "param_key":   "location_code",
        "fn":          run_location_status,
        "auto_on":     ["inactive", "dc not found", "master data", "logistics", "division", "equipment"],
    },
    "reference_numbers": {
        "label":       "🔢 Reference Numbers",
        "description": "Reference numbers for the shipment — container, ocean BOL, DeliveryPlan, vessel.",
        "param_label": "SHIPMENT_NUMBER (Shipment Number)",
        "param_key":   "shipment_number",
        "fn":          run_reference_numbers,
        "auto_on":     ["missing rate", "itinerary", "schedule"],
    },
    "load_events": {
        "label":       "📋 Load Events / Routing Logs",
        "description": "routing event log — use when rate exists but routing still failed.",
        "param_label": "SHIPMENT_NUMBER (Shipment Number)",
        "param_key":   "shipment_number",
        "fn":          run_load_events,
        "auto_on":     ["missing rate", "itinerary", "schedule"],
    },
}


def get_auto_queries_for_category(category: str) -> list[str]:
    """Return list of query keys that should auto-run for this error category."""
    cat_lower = (category or "").lower()
    keys = []
    for key, meta in QUERY_CATALOGUE.items():
        triggers = meta.get("auto_on", [])
        if "all" in triggers:
            keys.append(key)
            continue
        for trigger in triggers:
            if trigger in cat_lower:
                keys.append(key)
                break
    return keys

